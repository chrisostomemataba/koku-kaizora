from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_, func, text
from typing import List, Dict, Any, Tuple
from datetime import date, timedelta, time
from collections import defaultdict

from app.models.schema import (
    Child, Therapist, Department, ChildDepartment, ChildAvailability,
    TherapistAvailability, Session as SessionModel, SessionLog
)

def get_db():
    from app.core.config import get_database_session
    db = get_database_session()
    try:
        yield db
    finally:
        db.close()

class TimetableDataHelper:
    def __init__(self, db: Session):
        self.db = db

    def get_active_children_with_needs(self, active_child_ids: List[int]) -> List[Dict[str, Any]]:
        """Get children with their department needs and availability - Single query with joins"""
        children = (
            self.db.query(Child)
            .options(
                selectinload(Child.departments).joinedload(ChildDepartment.department),
                selectinload(Child.availability)
            )
            .filter(Child.id.in_(active_child_ids), Child.is_active == True)
            .all()
        )
        
        result = []
        for child in children:
            dept_needs = []
            for cd in child.departments:
                dept_needs.append({
                    "department_id": cd.department_id,
                    "department_name": cd.department.name,
                    "sessions_per_week": cd.sessions_per_week
                })
            
            availability_slots = []
            for avail in child.availability:
                availability_slots.append({
                    "day_of_week": avail.day_of_week,
                    "start_time": avail.start_time,
                    "end_time": avail.end_time
                })
            
            result.append({
                "id": child.id,
                "name": child.name,
                "departments": dept_needs,
                "availability": availability_slots
            })
        
        return result

    def get_available_therapists_with_schedule(self, active_therapist_ids: List[int]) -> List[Dict[str, Any]]:
        """Get therapists with their availability and department - Single query with joins"""
        therapists = (
            self.db.query(Therapist)
            .options(
                joinedload(Therapist.department),
                selectinload(Therapist.availability)
            )
            .filter(Therapist.id.in_(active_therapist_ids), Therapist.is_active == True)
            .all()
        )
        
        result = []
        for therapist in therapists:
            availability_slots = []
            for avail in therapist.availability:
                if avail.is_available:
                    availability_slots.append({
                        "day_of_week": avail.day_of_week,
                        "start_time": avail.start_time,
                        "end_time": avail.end_time
                    })
            
            result.append({
                "id": therapist.id,
                "name": therapist.name,
                "department_id": therapist.department_id,
                "department_name": therapist.department.name,
                "availability": availability_slots
            })
        
        return result

    def get_previous_week_loads(self, week_starting: date) -> Dict[int, int]:
        """Get therapist session counts from previous week for fairness"""
        previous_week = week_starting - timedelta(days=7)
        
        loads = (
            self.db.query(SessionModel.therapist_id, func.count(SessionModel.id))
            .filter(SessionModel.week_starting == previous_week)
            .group_by(SessionModel.therapist_id)
            .all()
        )
        
        return {therapist_id: count for therapist_id, count in loads}

    def get_current_week_loads(self, week_starting: date) -> Dict[int, int]:
        """Get current week therapist loads for real-time balancing"""
        loads = (
            self.db.query(SessionModel.therapist_id, func.count(SessionModel.id))
            .filter(SessionModel.week_starting == week_starting)
            .group_by(SessionModel.therapist_id)
            .all()
        )
        
        return {therapist_id: count for therapist_id, count in loads}

    def check_existing_sessions(self, week_starting: date) -> bool:
        """Check if sessions already exist for this week"""
        existing = (
            self.db.query(SessionModel)
            .filter(SessionModel.week_starting == week_starting)
            .first()
        )
        return existing is not None

    def clear_week_sessions(self, week_starting: date) -> int:
        """Clear existing sessions for regeneration"""
        deleted_count = (
            self.db.query(SessionModel)
            .filter(SessionModel.week_starting == week_starting)
            .delete()
        )
        self.db.commit()
        return deleted_count

    def bulk_create_sessions(self, sessions_data: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
        """Efficiently create multiple sessions with conflict detection"""
        errors = []
        created_count = 0
        
        try:
            # Check for time conflicts before creating
            conflict_check = self._check_time_conflicts(sessions_data)
            if conflict_check:
                errors.extend(conflict_check)
                return 0, errors
            
            session_objects = []
            for session_data in sessions_data:
                session_obj = SessionModel(
                    child_id=session_data["child_id"],
                    therapist_id=session_data["therapist_id"],
                    department_id=session_data["department_id"],
                    date=session_data["date"],
                    start_time=session_data["start_time"],
                    end_time=session_data["end_time"],
                    week_starting=session_data["week_starting"]
                )
                session_objects.append(session_obj)
            
            self.db.bulk_save_objects(session_objects)
            self.db.commit()
            created_count = len(session_objects)
            
        except Exception as e:
            self.db.rollback()
            errors.append(f"Database error during bulk creation: {str(e)}")
        
        return created_count, errors

    def _check_time_conflicts(self, sessions_data: List[Dict[str, Any]]) -> List[str]:
        """Check for therapist double-booking conflicts"""
        conflicts = []
        therapist_schedule = defaultdict(list)
        
        # Group sessions by therapist
        for session in sessions_data:
            key = (session["therapist_id"], session["date"])
            therapist_schedule[key].append({
                "start": session["start_time"],
                "end": session["end_time"],
                "child_id": session["child_id"]
            })
        
        # Check for overlapping times
        for (therapist_id, date), time_slots in therapist_schedule.items():
            time_slots.sort(key=lambda x: x["start"])
            
            for i in range(len(time_slots) - 1):
                current_end = time_slots[i]["end"]
                next_start = time_slots[i + 1]["start"]
                
                if current_end > next_start:
                    conflicts.append(
                        f"Therapist {therapist_id} has overlapping sessions on {date}: "
                        f"{time_slots[i]['start']}-{current_end} and {next_start}-{time_slots[i+1]['end']}"
                    )
        
        return conflicts

    def get_week_overview_optimized(self, week_starting: date) -> Dict[str, Any]:
        """Get complete week data with minimal queries"""
        # Single query with all joins
        sessions = (
            self.db.query(SessionModel)
            .options(
                joinedload(SessionModel.child),
                joinedload(SessionModel.therapist).joinedload(Therapist.department),
                joinedload(SessionModel.department)
            )
            .filter(SessionModel.week_starting == week_starting)
            .order_by(SessionModel.date, SessionModel.start_time)
            .all()
        )
        
        # Organize data by day and time
        timetable_grid = defaultdict(lambda: defaultdict(list))
        therapist_loads = defaultdict(int)
        
        for session in sessions:
            day_key = session.date.strftime("%A")
            time_key = f"{session.start_time}-{session.end_time}"
            
            session_info = {
                "session_id": session.id,
                "child_name": session.child.name,
                "therapist_name": session.therapist.name,
                "department_name": session.department.name
            }
            
            timetable_grid[day_key][time_key].append(session_info)
            therapist_loads[session.therapist_id] += 1
        
        return {
            "week_starting": week_starting,
            "timetable_grid": dict(timetable_grid),
            "therapist_loads": dict(therapist_loads),
            "total_sessions": len(sessions)
        }

    def get_child_daily_limits(self, child_id: int, date: date) -> int:
        """Check how many sessions child already has on a specific day"""
        count = (
            self.db.query(func.count(SessionModel.id))
            .filter(and_(SessionModel.child_id == child_id, SessionModel.date == date))
            .scalar()
        )
        return count or 0

    def get_departments_list(self) -> List[Dict[str, Any]]:
        """Get all departments for dropdowns/validation"""
        departments = self.db.query(Department).all()
        return [{"id": d.id, "name": d.name} for d in departments]

    def log_session_change(self, session_id: int, user_id: int, change_type: str, old_value: str, new_value: str):
        """Log session modifications for audit trail"""
        log_entry = SessionLog(
            session_id=session_id,
            changed_by=user_id,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value
        )
        self.db.add(log_entry)
        self.db.commit()