from typing import List, Dict, Any, Tuple, Optional
from datetime import date, time, datetime, timedelta
from pydantic import BaseModel, field_validator
import re

class ValidationError(Exception):
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(self.message)

class TimetableGenerationRequest(BaseModel):
    week_starting: date
    active_children: List[int]
    active_therapists: List[int]
    regenerate_existing: bool = False
    
    @field_validator('week_starting')
    def week_starting_validation(cls, v):
        if v < date.today() - timedelta(days=365):
            raise ValueError('Week starting date cannot be more than 1 year in the past')
        if v > date.today() + timedelta(days=365):
            raise ValueError('Week starting date cannot be more than 1 year in the future')
        return v
    
    @field_validator('active_children')
    def children_list_not_empty(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one child must be selected')
        return v
    
    @field_validator('active_therapists')
    def therapists_list_not_empty(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one therapist must be selected')
        return v

class TimetableValidator:
    def __init__(self):
        self.working_hours_start = time(8, 0)
        self.working_hours_end = time(16, 0)
        self.max_sessions_per_child_per_day = 5
        self.max_sessions_per_therapist_per_day = 8
        self.session_duration_minutes = 60
        self.valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

    def validate_generation_request(self, request: TimetableGenerationRequest) -> Tuple[bool, List[str]]:
        errors = []
        
        if request.week_starting < date.today() - timedelta(days=365):
            errors.append("Cannot generate timetable more than 1 year in the past")
        
        if request.week_starting > date.today() + timedelta(days=365):
            errors.append("Cannot generate timetable more than 1 year in the future")
        
        if not request.active_children:
            errors.append("At least one child must be selected")
        
        if not request.active_therapists:
            errors.append("At least one therapist must be selected")
        
        return len(errors) == 0, errors

    def validate_child_data(self, children_data: List[Dict]) -> Tuple[bool, List[str]]:
        errors = []
        
        if not children_data:
            errors.append("No valid children data provided")
            return False, errors
        
        for child in children_data:
            if not child.get('id'):
                errors.append(f"Child missing required ID: {child}")
                continue
                
            if not child.get('name'):
                errors.append(f"Child {child.get('id')} missing name")
            
            if not child.get('departments'):
                errors.append(f"Child {child.get('name', child.get('id'))} has no department assignments")
                continue
            
            total_sessions = sum(dept.get('sessions_per_week', 0) for dept in child['departments'])
            if total_sessions > self.max_sessions_per_child_per_day * 6:
                errors.append(f"Child {child.get('name')} has too many sessions per week ({total_sessions})")
            
            if not child.get('availability'):
                errors.append(f"Child {child.get('name')} has no availability set")
        
        return len(errors) == 0, errors

    def validate_therapist_data(self, therapists_data: List[Dict]) -> Tuple[bool, List[str]]:
        errors = []
        
        if not therapists_data:
            errors.append("No valid therapist data provided")
            return False, errors
        
        for therapist in therapists_data:
            if not therapist.get('id'):
                errors.append(f"Therapist missing required ID: {therapist}")
                continue
                
            if not therapist.get('name'):
                errors.append(f"Therapist {therapist.get('id')} missing name")
            
            if not therapist.get('department_id'):
                errors.append(f"Therapist {therapist.get('name', therapist.get('id'))} has no department assignment")
            
            if not therapist.get('availability'):
                errors.append(f"Therapist {therapist.get('name')} has no availability set")
        
        return len(errors) == 0, errors

    def validate_capacity_limits(self, children_data: List[Dict], therapists_data: List[Dict]) -> Tuple[bool, List[str]]:
        warnings = []
        
        total_child_sessions = sum(
            sum(dept.get('sessions_per_week', 0) for dept in child.get('departments', []))
            for child in children_data
        )
        
        total_therapist_capacity = len(therapists_data) * self.max_sessions_per_therapist_per_day * 6
        
        if total_child_sessions > total_therapist_capacity:
            warnings.append(f"Demand ({total_child_sessions} sessions) exceeds therapist capacity ({total_therapist_capacity} sessions)")
        
        department_demand = {}
        for child in children_data:
            for dept in child.get('departments', []):
                dept_name = dept.get('department_name', 'Unknown')
                sessions = dept.get('sessions_per_week', 0)
                department_demand[dept_name] = department_demand.get(dept_name, 0) + sessions
        
        department_capacity = {}
        for therapist in therapists_data:
            dept_name = therapist.get('department_name', 'Unknown')
            capacity = self.max_sessions_per_therapist_per_day * 6
            department_capacity[dept_name] = department_capacity.get(dept_name, 0) + capacity
        
        for dept, demand in department_demand.items():
            capacity = department_capacity.get(dept, 0)
            if demand > capacity:
                warnings.append(f"{dept} department: demand ({demand}) exceeds capacity ({capacity})")
        
        return True, warnings

    def validate_session_time(self, start_time: time, end_time: time) -> bool:
        if start_time < self.working_hours_start or end_time > self.working_hours_end:
            return False
        if start_time >= end_time:
            return False
        return True

    def validate_session_duration(self, start_time: time, end_time: time) -> bool:
        start_minutes = start_time.hour * 60 + start_time.minute
        end_minutes = end_time.hour * 60 + end_time.minute
        duration = end_minutes - start_minutes
        return duration == self.session_duration_minutes

    def validate_day_of_week(self, day: str) -> bool:
        return day in self.valid_days

    def validate_session_conflicts(self, sessions: List[Dict], new_session: Dict) -> List[str]:
        conflicts = []
        
        new_start = datetime.strptime(f"{new_session['date']} {new_session['start_time']}", "%Y-%m-%d %H:%M:%S")
        new_end = datetime.strptime(f"{new_session['date']} {new_session['end_time']}", "%Y-%m-%d %H:%M:%S")
        
        for session in sessions:
            if session.get('id') == new_session.get('id'):
                continue
                
            start = datetime.strptime(f"{session['date']} {session['start_time']}", "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(f"{session['date']} {session['end_time']}", "%Y-%m-%d %H:%M:%S")
            
            if new_start < end and new_end > start:
                if session['child_id'] == new_session['child_id']:
                    conflicts.append(f"Child conflict with existing session at {session['start_time']}")
                if session['therapist_id'] == new_session['therapist_id']:
                    conflicts.append(f"Therapist conflict with existing session at {session['start_time']}")
        
        return conflicts

    def validate_weekly_limits(self, sessions: List[Dict], child_id: int, therapist_id: int, date_obj: date) -> List[str]:
        warnings = []
        
        week_start = date_obj - timedelta(days=date_obj.weekday())
        week_end = week_start + timedelta(days=6)
        
        child_sessions_this_week = [
            s for s in sessions 
            if s['child_id'] == child_id and week_start <= datetime.strptime(s['date'], "%Y-%m-%d").date() <= week_end
        ]
        
        therapist_sessions_this_week = [
            s for s in sessions 
            if s['therapist_id'] == therapist_id and week_start <= datetime.strptime(s['date'], "%Y-%m-%d").date() <= week_end
        ]
        
        child_daily_count = {}
        therapist_daily_count = {}
        
        for session in child_sessions_this_week:
            day = session['date']
            child_daily_count[day] = child_daily_count.get(day, 0) + 1
        
        for session in therapist_sessions_this_week:
            day = session['date']
            therapist_daily_count[day] = therapist_daily_count.get(day, 0) + 1
        
        for day, count in child_daily_count.items():
            if count >= self.max_sessions_per_child_per_day:
                warnings.append(f"Child approaching daily limit ({count}/{self.max_sessions_per_child_per_day}) on {day}")
        
        for day, count in therapist_daily_count.items():
            if count >= self.max_sessions_per_therapist_per_day:
                warnings.append(f"Therapist approaching daily limit ({count}/{self.max_sessions_per_therapist_per_day}) on {day}")
        
        return warnings