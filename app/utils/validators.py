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
    def week_must_be_monday(cls, v):
        if v.weekday() != 0:
            raise ValueError('Week starting date must be a Monday')
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
        self.working_hours_start = time(8, 0)  # 8 AM
        self.working_hours_end = time(16, 0)   # 4 PM
        self.max_sessions_per_child_per_day = 5
        self.max_sessions_per_therapist_per_day = 8
        self.session_duration_minutes = 60
        self.valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

    def validate_generation_request(self, request: TimetableGenerationRequest) -> Tuple[bool, List[str]]:
        """Comprehensive validation of timetable generation request"""
        errors = []
        
        # Date validations
        if request.week_starting < date.today():
            errors.append("Cannot generate timetable for past weeks")
        
        if request.week_starting > date.today() + timedelta(days=365):
            errors.append("Cannot generate timetable more than 1 year in advance")
        
        # List validations
        if len(request.active_children) > 50:
            errors.append("Too many children selected (max 50 per week)")
        
        if len(request.active_therapists) > 20:
            errors.append("Too many therapists selected (max 20 per week)")
        
        # Duplicate checks
        if len(set(request.active_children)) != len(request.active_children):
            errors.append("Duplicate children IDs found in selection")
        
        if len(set(request.active_therapists)) != len(request.active_therapists):
            errors.append("Duplicate therapist IDs found in selection")
        
        return len(errors) == 0, errors

    def validate_child_data(self, children_data: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Validate child availability and department requirements"""
        errors = []
        
        for child in children_data:
            child_name = child.get('name', f"Child ID {child.get('id')}")
            
            # Check if child has departments
            if not child.get('departments') or len(child['departments']) == 0:
                errors.append(f"{child_name}: No departments assigned")
                continue
            
            # Check if child has availability
            if not child.get('availability') or len(child['availability']) == 0:
                errors.append(f"{child_name}: No availability schedule set")
                continue
            
            # Validate department session requirements
            total_weekly_sessions = sum(dept['sessions_per_week'] for dept in child['departments'])
            if total_weekly_sessions > 25:  # 5 days * 5 max sessions
                errors.append(f"{child_name}: Too many weekly sessions required ({total_weekly_sessions})")
            
            # Validate availability time slots
            for avail in child['availability']:
                if not self._is_valid_day(avail['day_of_week']):
                    errors.append(f"{child_name}: Invalid day '{avail['day_of_week']}'")
                
                if not self._is_valid_time_range(avail['start_time'], avail['end_time']):
                    errors.append(f"{child_name}: Invalid time range {avail['start_time']}-{avail['end_time']}")
        
        return len(errors) == 0, errors

    def validate_therapist_data(self, therapists_data: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Validate therapist availability and department coverage"""
        errors = []
        department_coverage = {}
        
        for therapist in therapists_data:
            therapist_name = therapist.get('name', f"Therapist ID {therapist.get('id')}")
            dept_id = therapist.get('department_id')
            
            # Track department coverage
            if dept_id not in department_coverage:
                department_coverage[dept_id] = []
            department_coverage[dept_id].append(therapist_name)
            
            # Check if therapist has availability
            if not therapist.get('availability') or len(therapist['availability']) == 0:
                errors.append(f"{therapist_name}: No availability schedule set")
                continue
            
            # Validate availability time slots
            for avail in therapist['availability']:
                if not self._is_valid_day(avail['day_of_week']):
                    errors.append(f"{therapist_name}: Invalid day '{avail['day_of_week']}'")
                
                if not self._is_valid_time_range(avail['start_time'], avail['end_time']):
                    errors.append(f"{therapist_name}: Invalid time range {avail['start_time']}-{avail['end_time']}")
        
        # Check if all departments have at least one therapist
        if not department_coverage:
            errors.append("No therapists available for any department")
        
        for dept_id, therapist_list in department_coverage.items():
            if len(therapist_list) == 0:
                errors.append(f"Department {dept_id}: No available therapists")
        
        return len(errors) == 0, errors

    def validate_capacity_limits(self, children_data: List[Dict[str, Any]], therapists_data: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Check if therapist capacity can handle child requirements"""
        errors = []
        warnings = []
        
        # Calculate total session demand
        total_sessions_needed = 0
        sessions_by_department = {}
        
        for child in children_data:
            for dept in child.get('departments', []):
                dept_id = dept['department_id']
                sessions_needed = dept['sessions_per_week']
                
                total_sessions_needed += sessions_needed
                if dept_id not in sessions_by_department:
                    sessions_by_department[dept_id] = 0
                sessions_by_department[dept_id] += sessions_needed
        
        # Calculate available therapist capacity
        therapist_capacity_by_dept = {}
        total_therapist_capacity = 0
        
        for therapist in therapists_data:
            dept_id = therapist['department_id']
            
            # Calculate weekly capacity based on availability
            weekly_capacity = self._calculate_therapist_weekly_capacity(therapist['availability'])
            total_therapist_capacity += weekly_capacity
            
            if dept_id not in therapist_capacity_by_dept:
                therapist_capacity_by_dept[dept_id] = 0
            therapist_capacity_by_dept[dept_id] += weekly_capacity
        
        # Check overall capacity
        if total_sessions_needed > total_therapist_capacity:
            errors.append(f"Insufficient therapist capacity: {total_sessions_needed} sessions needed, {total_therapist_capacity} available")
        
        # Check department-specific capacity
        for dept_id, sessions_needed in sessions_by_department.items():
            available_capacity = therapist_capacity_by_dept.get(dept_id, 0)
            
            if sessions_needed > available_capacity:
                errors.append(f"Department {dept_id}: {sessions_needed} sessions needed, {available_capacity} available")
            elif sessions_needed > available_capacity * 0.8:  # 80% capacity warning
                warnings.append(f"Department {dept_id}: High utilization ({sessions_needed}/{available_capacity})")
        
        return len(errors) == 0, errors + warnings

    def validate_session_update(self, session_data: Dict[str, Any], existing_sessions: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Validate manual session updates for conflicts"""
        errors = []
        
        # Time validation
        if 'start_time' in session_data and 'end_time' in session_data:
            if not self._is_valid_time_range(session_data['start_time'], session_data['end_time']):
                errors.append("Invalid time range")
        
        # Check for therapist conflicts
        if 'therapist_id' in session_data and 'date' in session_data:
            for existing in existing_sessions:
                if (existing['therapist_id'] == session_data['therapist_id'] and 
                    existing['date'] == session_data['date']):
                    
                    if self._times_overlap(
                        session_data.get('start_time'), session_data.get('end_time'),
                        existing['start_time'], existing['end_time']
                    ):
                        errors.append(f"Therapist conflict: overlapping session at {existing['start_time']}-{existing['end_time']}")
        
        return len(errors) == 0, errors

    def _is_valid_day(self, day: str) -> bool:
        """Check if day is valid working day"""
        return day in self.valid_days

    def _is_valid_time_range(self, start_time: time, end_time: time) -> bool:
        """Validate time range within working hours"""
        if isinstance(start_time, str):
            start_time = datetime.strptime(start_time, "%H:%M").time()
        if isinstance(end_time, str):
            end_time = datetime.strptime(end_time, "%H:%M").time()
        
        if start_time >= end_time:
            return False
        
        if start_time < self.working_hours_start or end_time > self.working_hours_end:
            return False
        
        # Check if duration is reasonable (30 minutes to 2 hours)
        duration = datetime.combine(date.today(), end_time) - datetime.combine(date.today(), start_time)
        duration_minutes = duration.total_seconds() / 60
        
        return 30 <= duration_minutes <= 120

    def _calculate_therapist_weekly_capacity(self, availability: List[Dict[str, Any]]) -> int:
        """Calculate maximum sessions per week for a therapist"""
        total_hours = 0
        
        for avail in availability:
            start_time = avail['start_time']
            end_time = avail['end_time']
            
            if isinstance(start_time, str):
                start_time = datetime.strptime(start_time, "%H:%M").time()
            if isinstance(end_time, str):
                end_time = datetime.strptime(end_time, "%H:%M").time()
            
            duration = datetime.combine(date.today(), end_time) - datetime.combine(date.today(), start_time)
            total_hours += duration.total_seconds() / 3600
        
        # Assume 1-hour sessions, with some buffer time between sessions
        return int(total_hours * 0.8)  # 80% efficiency factor

    def _times_overlap(self, start1: time, end1: time, start2: time, end2: time) -> bool:
        """Check if two time ranges overlap"""
        if isinstance(start1, str):
            start1 = datetime.strptime(start1, "%H:%M").time()
        if isinstance(end1, str):
            end1 = datetime.strptime(end1, "%H:%M").time()
        if isinstance(start2, str):
            start2 = datetime.strptime(start2, "%H:%M").time()
        if isinstance(end2, str):
            end2 = datetime.strptime(end2, "%H:%M").time()
        
        return start1 < end2 and start2 < end1