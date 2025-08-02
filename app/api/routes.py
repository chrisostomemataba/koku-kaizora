from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional
from datetime import date, datetime, timedelta
from pydantic import BaseModel
from collections import defaultdict

from app.models.schema import (
    Child, Therapist, Department, ChildDepartment, ChildAvailability, 
    TherapistAvailability, Session as SessionModel, SessionLog
)
from app.utils.data_helpers import get_db
from app.utils.redis_helper import redis_helper
from app.core.timetable_engine import SmartTimetableEngine
from app.utils.data_helpers import TimetableDataHelper

router = APIRouter()

class ChildCreate(BaseModel):
    name: str

class ChildUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None

class TherapistCreate(BaseModel):
    name: str
    department_id: int

class TherapistUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None

class ChildDepartmentCreate(BaseModel):
    department_id: int
    sessions_per_week: int = 1

class AvailabilityCreate(BaseModel):
    day_of_week: str
    start_time: str
    end_time: str

class SessionCreate(BaseModel):
    child_id: int
    therapist_id: int
    department_id: int
    date: date
    start_time: str
    end_time: str

class SessionUpdate(BaseModel):
    therapist_id: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class TimetableGenerationRequest(BaseModel):
    week_starting: date
    active_children: List[int]
    active_therapists: List[int]
    regenerate_existing: bool = False

@router.get("/children")
def get_children(db: Session = Depends(get_db)):
    cached_data = redis_helper.get_children_list()
    if cached_data:
        return cached_data
    
    children = db.query(Child).filter(Child.is_active == True).all()
    result = []
    for child in children:
        departments = db.query(ChildDepartment).filter(ChildDepartment.child_id == child.id).all()
        dept_info = [{"department_id": cd.department_id, "sessions_per_week": cd.sessions_per_week} for cd in departments]
        result.append({
            "id": child.id,
            "name": child.name,
            "is_active": child.is_active,
            "departments": dept_info
        })
    
    redis_helper.cache_children_list(result)
    return result

@router.post("/children")
def create_child(child: ChildCreate, db: Session = Depends(get_db)):
    new_child = Child(name=child.name)
    db.add(new_child)
    db.commit()
    db.refresh(new_child)
    
    redis_helper.invalidate_child_cache()
    return {"id": new_child.id, "name": new_child.name, "message": "Child created successfully"}

@router.put("/children/{child_id}")
def update_child(child_id: int, child: ChildUpdate, db: Session = Depends(get_db)):
    db_child = db.query(Child).filter(Child.id == child_id).first()
    if not db_child:
        raise HTTPException(status_code=404, detail="Child not found")
    
    if child.name is not None:
        db_child.name = child.name
    if child.is_active is not None:
        db_child.is_active = child.is_active
    
    db.commit()
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Child updated successfully"}

@router.delete("/children/{child_id}")
def delete_child(child_id: int, db: Session = Depends(get_db)):
    db_child = db.query(Child).filter(Child.id == child_id).first()
    if not db_child:
        raise HTTPException(status_code=404, detail="Child not found")
    
    db.delete(db_child)
    db.commit()
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Child deleted successfully"}

@router.post("/children/{child_id}/departments")
def add_child_department(child_id: int, dept: ChildDepartmentCreate, db: Session = Depends(get_db)):
    existing = db.query(ChildDepartment).filter(
        and_(ChildDepartment.child_id == child_id, ChildDepartment.department_id == dept.department_id)
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Child already assigned to this department")
    
    new_dept = ChildDepartment(child_id=child_id, department_id=dept.department_id, sessions_per_week=dept.sessions_per_week)
    db.add(new_dept)
    db.commit()
    
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Department added to child successfully"}

@router.put("/children/{child_id}/departments/{dept_id}")
def update_child_department(child_id: int, dept_id: int, sessions_per_week: int, db: Session = Depends(get_db)):
    child_dept = db.query(ChildDepartment).filter(
        and_(ChildDepartment.child_id == child_id, ChildDepartment.department_id == dept_id)
    ).first()
    
    if not child_dept:
        raise HTTPException(status_code=404, detail="Child department assignment not found")
    
    child_dept.sessions_per_week = sessions_per_week
    db.commit()
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Sessions per week updated successfully"}

@router.delete("/children/{child_id}/departments/{dept_id}")
def remove_child_department(child_id: int, dept_id: int, db: Session = Depends(get_db)):
    child_dept = db.query(ChildDepartment).filter(
        and_(ChildDepartment.child_id == child_id, ChildDepartment.department_id == dept_id)
    ).first()
    
    if not child_dept:
        raise HTTPException(status_code=404, detail="Child department assignment not found")
    
    db.delete(child_dept)
    db.commit()
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Department removed from child successfully"}

@router.post("/children/{child_id}/availability")
def add_child_availability(child_id: int, availability: AvailabilityCreate, db: Session = Depends(get_db)):
    new_availability = ChildAvailability(
        child_id=child_id,
        day_of_week=availability.day_of_week,
        start_time=availability.start_time,
        end_time=availability.end_time
    )
    db.add(new_availability)
    db.commit()
    
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Availability added successfully"}

@router.get("/children/{child_id}/availability")
def get_child_availability(child_id: int, db: Session = Depends(get_db)):
    cached_data = redis_helper.get_child_availability(child_id)
    if cached_data:
        return cached_data
    
    availability = db.query(ChildAvailability).filter(ChildAvailability.child_id == child_id).all()
    result = [{"id": a.id, "day_of_week": a.day_of_week, "start_time": str(a.start_time), "end_time": str(a.end_time)} for a in availability]
    
    redis_helper.cache_child_availability(child_id, result)
    return result

@router.delete("/availability/{availability_id}")
def delete_availability(availability_id: int, db: Session = Depends(get_db)):
    availability = db.query(ChildAvailability).filter(ChildAvailability.id == availability_id).first()
    if not availability:
        raise HTTPException(status_code=404, detail="Availability not found")
    
    child_id = availability.child_id
    db.delete(availability)
    db.commit()
    redis_helper.invalidate_child_cache(child_id)
    return {"message": "Availability deleted successfully"}

@router.get("/therapists")
def get_therapists(db: Session = Depends(get_db)):
    cached_data = redis_helper.get_therapists_list()
    if cached_data:
        return cached_data
    
    therapists = db.query(Therapist).filter(Therapist.is_active == True).all()
    result = []
    for therapist in therapists:
        department = db.query(Department).filter(Department.id == therapist.department_id).first()
        result.append({
            "id": therapist.id,
            "name": therapist.name,
            "department_id": therapist.department_id,
            "department_name": department.name if department else None,
            "is_active": therapist.is_active
        })
    
    redis_helper.cache_therapists_list(result)
    return result

@router.post("/therapists")
def create_therapist(therapist: TherapistCreate, db: Session = Depends(get_db)):
    new_therapist = Therapist(name=therapist.name, department_id=therapist.department_id)
    db.add(new_therapist)
    db.commit()
    db.refresh(new_therapist)
    
    redis_helper.invalidate_therapist_cache()
    return {"id": new_therapist.id, "name": new_therapist.name, "message": "Therapist created successfully"}

@router.put("/therapists/{therapist_id}")
def update_therapist(therapist_id: int, therapist: TherapistUpdate, db: Session = Depends(get_db)):
    db_therapist = db.query(Therapist).filter(Therapist.id == therapist_id).first()
    if not db_therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")
    
    if therapist.name is not None:
        db_therapist.name = therapist.name
    if therapist.is_active is not None:
        db_therapist.is_active = therapist.is_active
    
    db.commit()
    redis_helper.invalidate_therapist_cache(therapist_id)
    return {"message": "Therapist updated successfully"}

@router.put("/therapists/{therapist_id}/toggle")
def toggle_therapist_availability(therapist_id: int, db: Session = Depends(get_db)):
    therapist = db.query(Therapist).filter(Therapist.id == therapist_id).first()
    if not therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")
    
    therapist.is_active = not therapist.is_active
    db.commit()
    redis_helper.invalidate_therapist_cache(therapist_id)
    return {"message": f"Therapist availability toggled to {'active' if therapist.is_active else 'inactive'}"}

@router.post("/therapists/{therapist_id}/availability")
def add_therapist_availability(therapist_id: int, availability: AvailabilityCreate, db: Session = Depends(get_db)):
    new_availability = TherapistAvailability(
        therapist_id=therapist_id,
        day_of_week=availability.day_of_week,
        start_time=availability.start_time,
        end_time=availability.end_time
    )
    db.add(new_availability)
    db.commit()
    
    redis_helper.invalidate_therapist_cache(therapist_id)
    return {"message": "Therapist availability added successfully"}

@router.get("/therapists/{therapist_id}/availability")
def get_therapist_availability(therapist_id: int, db: Session = Depends(get_db)):
    cached_data = redis_helper.get_therapist_availability(therapist_id)
    if cached_data:
        return cached_data
    
    availability = db.query(TherapistAvailability).filter(TherapistAvailability.therapist_id == therapist_id).all()
    result = [{"id": a.id, "day_of_week": a.day_of_week, "start_time": str(a.start_time), "end_time": str(a.end_time), "is_available": a.is_available} for a in availability]
    
    redis_helper.cache_therapist_availability(therapist_id, result)
    return result

@router.put("/therapist-availability/{availability_id}/toggle")
def toggle_therapist_day_availability(availability_id: int, db: Session = Depends(get_db)):
    availability = db.query(TherapistAvailability).filter(TherapistAvailability.id == availability_id).first()
    if not availability:
        raise HTTPException(status_code=404, detail="Availability not found")
    
    availability.is_available = not availability.is_available
    db.commit()
    redis_helper.invalidate_therapist_cache(availability.therapist_id)
    return {"message": f"Availability toggled to {'available' if availability.is_available else 'unavailable'}"}

@router.post("/generate-timetable")
def generate_weekly_timetable(request: TimetableGenerationRequest, db: Session = Depends(get_db)):
    try:
        data_helper = TimetableDataHelper(db)
        engine = SmartTimetableEngine(data_helper)
        
        result = engine.generate_weekly_timetable(
            week_starting=request.week_starting,
            active_children=request.active_children,
            active_therapists=request.active_therapists,
            regenerate_existing=request.regenerate_existing
        )
        
        if result.success:
            redis_helper.invalidate_timetable_cache(request.week_starting)
            
            response = {
                "success": True,
                "message": f"Timetable generated successfully",
                "sessions_created": result.sessions_created,
                "week_starting": request.week_starting,
                "timetable_data": result.timetable_data,
                "warnings": result.warnings
            }
            
            if result.warnings:
                response["has_warnings"] = True
                
            return response
        else:
            raise HTTPException(
                status_code=400, 
                detail={
                    "success": False,
                    "message": "Timetable generation failed",
                    "errors": result.errors,
                    "sessions_created": result.sessions_created
                }
            )
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Internal server error during timetable generation",
                "error": str(e)
            }
        )

@router.get("/timetable/{week_starting}")
def get_weekly_timetable(week_starting: date, db: Session = Depends(get_db)):
    cached_data = redis_helper.get_weekly_timetable(week_starting)
    if cached_data:
        return cached_data
    
    sessions = db.query(SessionModel).filter(SessionModel.week_starting == week_starting).all()
    
    timetable_data = []
    for session in sessions:
        child = db.query(Child).filter(Child.id == session.child_id).first()
        therapist = db.query(Therapist).filter(Therapist.id == session.therapist_id).first()
        department = db.query(Department).filter(Department.id == session.department_id).first()
        
        timetable_data.append({
            "session_id": session.id,
            "child_name": child.name if child else "Unknown",
            "therapist_name": therapist.name if therapist else "Unknown",
            "department_name": department.name if department else "Unknown",
            "date": session.date,
            "start_time": str(session.start_time),
            "end_time": str(session.end_time)
        })
    
    result = {"week_starting": week_starting, "sessions": timetable_data}
    redis_helper.cache_weekly_timetable(week_starting, result)
    return result

@router.get("/sessions/week/{week_starting}")
def get_week_sessions(week_starting: date, db: Session = Depends(get_db)):
    sessions = db.query(SessionModel).filter(SessionModel.week_starting == week_starting).all()
    return [{"id": s.id, "child_id": s.child_id, "therapist_id": s.therapist_id, "department_id": s.department_id, 
             "date": s.date, "start_time": str(s.start_time), "end_time": str(s.end_time)} for s in sessions]

@router.get("/sessions/child/{child_id}/week/{week_starting}")
def get_child_weekly_sessions(child_id: int, week_starting: date, db: Session = Depends(get_db)):
    sessions = db.query(SessionModel).filter(
        and_(SessionModel.child_id == child_id, SessionModel.week_starting == week_starting)
    ).all()
    
    result = []
    for session in sessions:
        therapist = db.query(Therapist).filter(Therapist.id == session.therapist_id).first()
        department = db.query(Department).filter(Department.id == session.department_id).first()
        
        result.append({
            "session_id": session.id,
            "therapist_name": therapist.name if therapist else "Unknown",
            "department_name": department.name if department else "Unknown",
            "date": session.date,
            "start_time": str(session.start_time),
            "end_time": str(session.end_time)
        })
    
    return {"child_id": child_id, "week_starting": week_starting, "sessions": result}

@router.post("/sessions")
def create_session(session: SessionCreate, db: Session = Depends(get_db)):
    week_start = session.date - timedelta(days=session.date.weekday())
    
    new_session = SessionModel(
        child_id=session.child_id,
        therapist_id=session.therapist_id,
        department_id=session.department_id,
        date=session.date,
        start_time=session.start_time,
        end_time=session.end_time,
        week_starting=week_start
    )
    db.add(new_session)
    db.commit()
    
    redis_helper.invalidate_timetable_cache(week_start)
    return {"message": "Session created successfully", "session_id": new_session.id}

@router.put("/sessions/{session_id}")
def update_session(session_id: int, session: SessionUpdate, db: Session = Depends(get_db)):
    db_session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    old_values = {"therapist_id": db_session.therapist_id, "start_time": str(db_session.start_time), "end_time": str(db_session.end_time)}
    week_starting = db_session.week_starting
    
    if session.therapist_id is not None:
        db_session.therapist_id = session.therapist_id
    if session.start_time is not None:
        db_session.start_time = session.start_time
    if session.end_time is not None:
        db_session.end_time = session.end_time
    
    db.commit()
    
    log_entry = SessionLog(
        session_id=session_id,
        changed_by=1,
        change_type="UPDATE",
        old_value=str(old_values),
        new_value=str({"therapist_id": db_session.therapist_id, "start_time": str(db_session.start_time), "end_time": str(db_session.end_time)})
    )
    db.add(log_entry)
    db.commit()
    
    redis_helper.invalidate_timetable_cache(week_starting)
    return {"message": "Session updated successfully"}

@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    week_starting = session.week_starting
    db.delete(session)
    db.commit()
    
    redis_helper.invalidate_timetable_cache(week_starting)
    return {"message": "Session deleted successfully"}

@router.get("/reports/daily/{report_date}")
def get_daily_report(report_date: date, db: Session = Depends(get_db)):
    cached_data = redis_helper.get_daily_report(report_date)
    if cached_data:
        return cached_data
    
    sessions = db.query(SessionModel).filter(SessionModel.date == report_date).all()
    
    report_data = []
    for session in sessions:
        child = db.query(Child).filter(Child.id == session.child_id).first()
        therapist = db.query(Therapist).filter(Therapist.id == session.therapist_id).first()
        department = db.query(Department).filter(Department.id == session.department_id).first()
        
        report_data.append({
            "child_name": child.name if child else "Unknown",
            "therapist_name": therapist.name if therapist else "Unknown",
            "department": department.name if department else "Unknown",
            "start_time": str(session.start_time),
            "end_time": str(session.end_time)
        })
    
    result = {"date": report_date, "sessions": report_data, "total_sessions": len(report_data)}
    redis_helper.cache_daily_report(report_date, result)
    return result

@router.get("/reports/weekly/{week_starting}")
def get_weekly_report(week_starting: date, db: Session = Depends(get_db)):
    cached_data = redis_helper.get_weekly_report(week_starting)
    if cached_data:
        return cached_data
    
    sessions = db.query(SessionModel).filter(SessionModel.week_starting == week_starting).all()
    
    report_data = []
    for session in sessions:
        child = db.query(Child).filter(Child.id == session.child_id).first()
        therapist = db.query(Therapist).filter(Therapist.id == session.therapist_id).first()
        department = db.query(Department).filter(Department.id == session.department_id).first()
        
        report_data.append({
            "child_name": child.name if child else "Unknown",
            "therapist_name": therapist.name if therapist else "Unknown", 
            "department": department.name if department else "Unknown",
            "date": session.date,
            "day_of_week": session.date.strftime("%A"),
            "start_time": str(session.start_time),
            "end_time": str(session.end_time)
        })
    
    children_sessions = {}
    for session in report_data:
        child_name = session["child_name"]
        if child_name not in children_sessions:
            children_sessions[child_name] = []
        children_sessions[child_name].append(session)
    
    result = {"week_starting": week_starting, "sessions_by_child": children_sessions, "all_sessions": report_data}
    redis_helper.cache_weekly_report(week_starting, result)
    return result

@router.get("/reports/child/{child_id}/week/{week_starting}")
def get_child_weekly_report(child_id: int, week_starting: date, db: Session = Depends(get_db)):
    child = db.query(Child).filter(Child.id == child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child not found")
    
    sessions = db.query(SessionModel).filter(
        and_(SessionModel.child_id == child_id, SessionModel.week_starting == week_starting)
    ).all()
    
    report_data = []
    for session in sessions:
        therapist = db.query(Therapist).filter(Therapist.id == session.therapist_id).first()
        department = db.query(Department).filter(Department.id == session.department_id).first()
        
        report_data.append({
            "date": session.date,
            "day_of_week": session.date.strftime("%A"),
            "therapist_name": therapist.name if therapist else "Unknown",
            "department": department.name if department else "Unknown",
            "start_time": str(session.start_time),
            "end_time": str(session.end_time)
        })
    
    return {
        "child_name": child.name,
        "week_starting": week_starting,
        "sessions": report_data,
        "total_sessions": len(report_data)
    }

@router.get("/departments")
def get_departments(db: Session = Depends(get_db)):
    cached_data = redis_helper.get_departments_list()
    if cached_data:
        return cached_data
    
    departments = db.query(Department).all()
    result = [{"id": d.id, "name": d.name} for d in departments]
    
    redis_helper.cache_departments_list(result)
    return result

@router.post("/departments")
def create_department(name: str, db: Session = Depends(get_db)):
    new_department = Department(name=name)
    db.add(new_department)
    db.commit()
    
    redis_helper.invalidate_pattern("departments:*")
    return {"id": new_department.id, "name": new_department.name, "message": "Department created successfully"}

@router.get("/timetable/{week_starting}/analytics")
def get_timetable_analytics(week_starting: date, db: Session = Depends(get_db)):
    try:
        data_helper = TimetableDataHelper(db)
        
        current_loads = data_helper.get_current_week_loads(week_starting)
        previous_loads = data_helper.get_previous_week_loads(week_starting)
        
        therapists = db.query(Therapist).filter(Therapist.is_active == True).all()
        
        therapist_analytics = []
        for therapist in therapists:
            current_load = current_loads.get(therapist.id, 0)
            previous_load = previous_loads.get(therapist.id, 0)
            
            therapist_analytics.append({
                "therapist_id": therapist.id,
                "therapist_name": therapist.name,
                "department_id": therapist.department_id,
                "current_week_sessions": current_load,
                "previous_week_sessions": previous_load,
                "load_change": current_load - previous_load,
                "utilization_status": "high" if current_load > 20 else "normal" if current_load > 10 else "low"
            })
        
        sessions = db.query(SessionModel).filter(SessionModel.week_starting == week_starting).all()
        daily_distribution = defaultdict(int)
        
        for session in sessions:
            day_name = session.date.strftime("%A")
            daily_distribution[day_name] += 1
        
        return {
            "week_starting": week_starting,
            "total_sessions": len(sessions),
            "therapist_analytics": therapist_analytics,
            "daily_distribution": dict(daily_distribution),
            "average_sessions_per_therapist": len(sessions) / len(therapists) if therapists else 0
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Failed to generate analytics",
                "error": str(e)
            }
        )

@router.post("/children/bulk-toggle")
def bulk_toggle_children(child_ids: List[int], active_status: bool, db: Session = Depends(get_db)):
    try:
        updated_count = (
            db.query(Child)
            .filter(Child.id.in_(child_ids))
            .update({Child.is_active: active_status})
        )
        db.commit()
        
        redis_helper.invalidate_child_cache()
        return {
            "success": True,
            "message": f"Updated {updated_count} children to {'active' if active_status else 'inactive'}",
            "updated_count": updated_count
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Failed to update children status",
                "error": str(e)
            }
        )

@router.post("/therapists/bulk-toggle")
def bulk_toggle_therapists(therapist_ids: List[int], active_status: bool, db: Session = Depends(get_db)):
    try:
        updated_count = (
            db.query(Therapist)
            .filter(Therapist.id.in_(therapist_ids))
            .update({Therapist.is_active: active_status})
        )
        db.commit()
        
        redis_helper.invalidate_therapist_cache()
        return {
            "success": True,
            "message": f"Updated {updated_count} therapists to {'active' if active_status else 'inactive'}",
            "updated_count": updated_count
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Failed to update therapists status", 
                "error": str(e)
            }
        )

@router.post("/timetable/quick-setup")
def quick_timetable_setup(week_starting: date, copy_from_week: Optional[date] = None, db: Session = Depends(get_db)):
    try:
        if copy_from_week:
            reference_sessions = db.query(SessionModel).filter(SessionModel.week_starting == copy_from_week).all()
            
            active_children = list(set(session.child_id for session in reference_sessions))
            active_therapists = list(set(session.therapist_id for session in reference_sessions))
            
            return {
                "success": True,
                "message": f"Quick setup ready for week {week_starting}",
                "suggested_active_children": active_children,
                "suggested_active_therapists": active_therapists,
                "reference_week": copy_from_week
            }
        else:
            active_children = [child.id for child in db.query(Child).filter(Child.is_active == True).all()]
            active_therapists = [therapist.id for therapist in db.query(Therapist).filter(Therapist.is_active == True).all()]
            
            return {
                "success": True,
                "message": f"Quick setup ready for week {week_starting}",
                "suggested_active_children": active_children,
                "suggested_active_therapists": active_therapists
            }
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "message": "Failed to prepare quick setup",
                "error": str(e)
            }
        )