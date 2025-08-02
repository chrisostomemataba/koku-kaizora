from typing import List, Dict, Any, Tuple, Optional
from datetime import date, time, datetime, timedelta
from collections import defaultdict
import random
from dataclasses import dataclass, field

from app.utils.data_helpers import TimetableDataHelper
from app.utils.validators import TimetableValidator, ValidationError

@dataclass
class SessionSlot:
    child_id: int
    child_name: str
    department_id: int
    department_name: str
    sessions_needed: int
    day_preferences: List[str]
    time_preferences: List[Tuple[time, time]]

@dataclass 
class TherapistSlot:
    therapist_id: int
    therapist_name: str
    department_id: int
    department_name: str
    availability: Dict[str, List[Tuple[time, time]]]
    current_load: int = 0
    fairness_score: float = 0.0

@dataclass
class GenerationResult:
    success: bool
    sessions_created: int
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timetable_data: Dict[str, Any] = field(default_factory=dict)

class SmartTimetableEngine:
    def __init__(self, db_helper: TimetableDataHelper):
        self.db_helper = db_helper
        self.validator = TimetableValidator()
        self.time_slots = self._generate_time_slots()
        self.days_of_week = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

    def _generate_time_slots(self) -> List[Tuple[time, time]]:
        """Generate 1-hour time slots from 8 AM to 4 PM"""
        slots = []
        current_time = time(8, 0)
        
        while current_time < time(16, 0):
            end_time = (datetime.combine(date.today(), current_time) + timedelta(hours=1)).time()
            slots.append((current_time, end_time))
            current_time = end_time
        
        return slots

    def generate_weekly_timetable(self, week_starting: date, active_children: List[int], 
                                 active_therapists: List[int], regenerate_existing: bool = False) -> GenerationResult:
        """Main orchestrator for timetable generation"""
        
        try:
            # Step 1: Validation
            validation_result = self._validate_generation_request(
                week_starting, active_children, active_therapists, regenerate_existing
            )
            if not validation_result.success:
                return validation_result

            # Step 2: Clear existing sessions if regenerating
            if regenerate_existing:
                cleared_count = self.db_helper.clear_week_sessions(week_starting)
                if cleared_count > 0:
                    validation_result.warnings.append(f"Cleared {cleared_count} existing sessions")

            # Step 3: Fetch and validate data
            children_data = self.db_helper.get_active_children_with_needs(active_children)
            therapists_data = self.db_helper.get_available_therapists_with_schedule(active_therapists)
            
            data_validation = self._validate_input_data(children_data, therapists_data)
            if not data_validation.success:
                return data_validation

            # Step 4: Calculate fairness and prepare slots
            session_slots = self._prepare_session_slots(children_data)
            therapist_slots = self._prepare_therapist_slots(therapists_data, week_starting)

            # Step 5: Smart allocation algorithm
            allocation_result = self._allocate_sessions_intelligently(
                session_slots, therapist_slots, week_starting
            )
            
            if not allocation_result.success:
                return allocation_result

            # Step 6: Create sessions in database
            sessions_data = allocation_result.timetable_data.get('sessions', [])
            created_count, creation_errors = self.db_helper.bulk_create_sessions(sessions_data)
            
            if creation_errors:
                return GenerationResult(
                    success=False, 
                    sessions_created=0,
                    errors=creation_errors
                )

            # Step 7: Generate final timetable view
            timetable_overview = self.db_helper.get_week_overview_optimized(week_starting)
            
            return GenerationResult(
                success=True,
                sessions_created=created_count,
                warnings=validation_result.warnings + allocation_result.warnings,
                timetable_data=timetable_overview
            )

        except Exception as e:
            return GenerationResult(
                success=False,
                sessions_created=0,
                errors=[f"Engine error: {str(e)}"]
            )

    def _validate_generation_request(self, week_starting: date, active_children: List[int], 
                                   active_therapists: List[int], regenerate_existing: bool) -> GenerationResult:
        """Comprehensive validation of generation request"""
        
        # Check if sessions already exist
        if not regenerate_existing and self.db_helper.check_existing_sessions(week_starting):
            return GenerationResult(
                success=False,
                sessions_created=0,
                errors=["Sessions already exist for this week. Use regenerate_existing=true to overwrite."]
            )

        # Basic input validation
        if not active_children:
            return GenerationResult(success=False, sessions_created=0, errors=["No children selected"])
        
        if not active_therapists:
            return GenerationResult(success=False, sessions_created=0, errors=["No therapists selected"])

        # Date validation
        if week_starting.weekday() != 0:
            return GenerationResult(success=False, sessions_created=0, errors=["Week must start on Monday"])

        return GenerationResult(success=True, sessions_created=0)

    def _validate_input_data(self, children_data: List[Dict], therapists_data: List[Dict]) -> GenerationResult:
        """Validate children and therapist data"""
        
        # Validate children
        children_valid, children_errors = self.validator.validate_child_data(children_data)
        if not children_valid:
            return GenerationResult(success=False, sessions_created=0, errors=children_errors)

        # Validate therapists
        therapists_valid, therapist_errors = self.validator.validate_therapist_data(therapists_data)
        if not therapists_valid:
            return GenerationResult(success=False, sessions_created=0, errors=therapist_errors)

        # Check capacity limits
        capacity_valid, capacity_messages = self.validator.validate_capacity_limits(children_data, therapists_data)
        if not capacity_valid:
            return GenerationResult(success=False, sessions_created=0, errors=capacity_messages)

        return GenerationResult(success=True, sessions_created=0, warnings=capacity_messages)

    def _prepare_session_slots(self, children_data: List[Dict]) -> List[SessionSlot]:
        """Convert children data into session slots for allocation"""
        session_slots = []
        
        for child in children_data:
            for dept in child['departments']:
                for _ in range(dept['sessions_per_week']):
                    # Extract day preferences from availability
                    day_preferences = [avail['day_of_week'] for avail in child['availability']]
                    
                    # Extract time preferences
                    time_preferences = []
                    for avail in child['availability']:
                        time_preferences.append((avail['start_time'], avail['end_time']))
                    
                    session_slot = SessionSlot(
                        child_id=child['id'],
                        child_name=child['name'],
                        department_id=dept['department_id'],
                        department_name=dept['department_name'],
                        sessions_needed=1,
                        day_preferences=day_preferences,
                        time_preferences=time_preferences
                    )
                    session_slots.append(session_slot)
        
        # Shuffle for fairness in allocation order
        random.shuffle(session_slots)
        return session_slots

    def _prepare_therapist_slots(self, therapists_data: List[Dict], week_starting: date) -> List[TherapistSlot]:
        """Convert therapist data into slots with fairness scoring"""
        
        # Get previous week loads for fairness
        previous_loads = self.db_helper.get_previous_week_loads(week_starting)
        current_loads = self.db_helper.get_current_week_loads(week_starting)
        
        therapist_slots = []
        
        for therapist in therapists_data:
            # Organize availability by day
            availability_by_day = defaultdict(list)
            for avail in therapist['availability']:
                day = avail['day_of_week']
                time_range = (avail['start_time'], avail['end_time'])
                availability_by_day[day].append(time_range)
            
            # Calculate fairness score (lower = more fair to assign)
            previous_load = previous_loads.get(therapist['id'], 0)
            current_load = current_loads.get(therapist['id'], 0)
            fairness_score = previous_load + current_load
            
            therapist_slot = TherapistSlot(
                therapist_id=therapist['id'],
                therapist_name=therapist['name'],
                department_id=therapist['department_id'],
                department_name=therapist['department_name'],
                availability=dict(availability_by_day),
                current_load=current_load,
                fairness_score=fairness_score
            )
            therapist_slots.append(therapist_slot)
        
        return therapist_slots

    def _allocate_sessions_intelligently(self, session_slots: List[SessionSlot], 
                                       therapist_slots: List[TherapistSlot], 
                                       week_starting: date) -> GenerationResult:
        """Core intelligent allocation algorithm"""
        
        allocated_sessions = []
        unallocated_sessions = []
        warnings = []
        
        # Track daily limits
        child_daily_sessions = defaultdict(lambda: defaultdict(int))
        therapist_daily_sessions = defaultdict(lambda: defaultdict(int))
        
        # Group therapists by department for faster lookup
        therapists_by_dept = defaultdict(list)
        for therapist in therapist_slots:
            therapists_by_dept[therapist.department_id].append(therapist)
        
        # Allocation loop
        for session_slot in session_slots:
            allocation_success = False
            
            # Get available therapists for this department
            available_therapists = therapists_by_dept.get(session_slot.department_id, [])
            if not available_therapists:
                unallocated_sessions.append(f"No therapists available for {session_slot.department_name}")
                continue
            
            # Sort therapists by fairness (least loaded first)
            available_therapists.sort(key=lambda t: (t.fairness_score, t.current_load))
            
            # Try to find a suitable time slot
            best_allocation = self._find_best_time_slot(
                session_slot, available_therapists, week_starting,
                child_daily_sessions, therapist_daily_sessions
            )
            
            if best_allocation:
                allocated_sessions.append(best_allocation)
                
                # Update tracking
                child_id = session_slot.child_id
                therapist_id = best_allocation['therapist_id']
                session_date = best_allocation['date']
                
                child_daily_sessions[child_id][session_date] += 1
                therapist_daily_sessions[therapist_id][session_date] += 1
                
                # Update therapist load for fairness
                for therapist in therapist_slots:
                    if therapist.therapist_id == therapist_id:
                        therapist.current_load += 1
                        therapist.fairness_score += 1
                        break
                
                allocation_success = True
            
            if not allocation_success:
                unallocated_sessions.append(
                    f"Could not allocate session for {session_slot.child_name} in {session_slot.department_name}"
                )
        
        # Generate warnings for unallocated sessions
        if unallocated_sessions:
            warnings.extend(unallocated_sessions)
        
        return GenerationResult(
            success=True,
            sessions_created=len(allocated_sessions),
            warnings=warnings,
            timetable_data={'sessions': allocated_sessions}
        )

    def _find_best_time_slot(self, session_slot: SessionSlot, available_therapists: List[TherapistSlot],
                           week_starting: date, child_daily_sessions: Dict, 
                           therapist_daily_sessions: Dict) -> Optional[Dict[str, Any]]:
        """Find the optimal time slot for a session"""
        
        best_options = []
        
        # Try each day in the child's preference order
        for day in session_slot.day_preferences:
            session_date = self._get_date_for_day(week_starting, day)
            
            # Check child's daily limit
            if child_daily_sessions[session_slot.child_id][session_date] >= 5:
                continue
            
            # Try each therapist (already sorted by fairness)
            for therapist in available_therapists:
                # Check if therapist is available on this day
                if day not in therapist.availability:
                    continue
                
                # Check therapist's daily limit
                if therapist_daily_sessions[therapist.therapist_id][session_date] >= 8:
                    continue
                
                # Find available time slots
                therapist_times = therapist.availability[day]
                child_times = session_slot.time_preferences
                
                # Find overlapping time slots
                available_slots = self._find_overlapping_slots(therapist_times, child_times)
                
                for time_slot in available_slots:
                    # Check if this specific time is free
                    if self._is_time_slot_free(therapist.therapist_id, session_date, time_slot):
                        
                        # Calculate priority score
                        priority_score = self._calculate_slot_priority(
                            session_slot, therapist, day, time_slot, session_date,
                            child_daily_sessions, therapist_daily_sessions
                        )
                        
                        best_options.append({
                            'child_id': session_slot.child_id,
                            'therapist_id': therapist.therapist_id,
                            'department_id': session_slot.department_id,
                            'date': session_date,
                            'start_time': time_slot[0],
                            'end_time': time_slot[1],
                            'week_starting': week_starting,
                            'priority_score': priority_score
                        })
        
        # Return the best option
        if best_options:
            best_options.sort(key=lambda x: x['priority_score'], reverse=True)
            return best_options[0]
        
        return None

    def _get_date_for_day(self, week_starting: date, day_name: str) -> date:
        """Convert day name to actual date"""
        day_mapping = {
            'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 
            'Thursday': 3, 'Friday': 4, 'Saturday': 5
        }
        days_offset = day_mapping.get(day_name, 0)
        return week_starting + timedelta(days=days_offset)

    def _find_overlapping_slots(self, therapist_times: List[Tuple[time, time]], 
                              child_times: List[Tuple[time, time]]) -> List[Tuple[time, time]]:
        """Find time slots where both therapist and child are available"""
        overlapping_slots = []
        
        for t_start, t_end in therapist_times:
            for c_start, c_end in child_times:
                # Find overlap
                overlap_start = max(t_start, c_start)
                overlap_end = min(t_end, c_end)
                
                if overlap_start < overlap_end:
                    # Generate 1-hour slots within overlap
                    current_time = overlap_start
                    while current_time < overlap_end:
                        slot_end = (datetime.combine(date.today(), current_time) + timedelta(hours=1)).time()
                        if slot_end <= overlap_end:
                            overlapping_slots.append((current_time, slot_end))
                        current_time = slot_end
        
        return overlapping_slots

    def _is_time_slot_free(self, therapist_id: int, session_date: date, time_slot: Tuple[time, time]) -> bool:
        """Check if therapist is free at this specific time (simplified for MVP)"""
        # In a full implementation, this would check existing sessions
        # For MVP, we assume if we're here, the slot is potentially available
        return True

    def _calculate_slot_priority(self, session_slot: SessionSlot, therapist: TherapistSlot,
                               day: str, time_slot: Tuple[time, time], session_date: date,
                               child_daily_sessions: Dict, therapist_daily_sessions: Dict) -> float:
        """Calculate priority score for slot allocation"""
        
        priority = 100.0  # Base priority
        
        # Fairness bonus (prefer less loaded therapists)
        priority += (10 - therapist.current_load) * 2
        
        # Time preference bonus (prefer morning slots)
        morning_bonus = 5 if time_slot[0] < time(12, 0) else 0
        priority += morning_bonus
        
        # Day distribution bonus (prefer spreading across days)
        child_sessions_today = child_daily_sessions[session_slot.child_id][session_date]
        day_distribution_bonus = max(0, 3 - child_sessions_today)
        priority += day_distribution_bonus
        
        # Therapist workload bonus
        therapist_sessions_today = therapist_daily_sessions[therapist.therapist_id][session_date]
        workload_bonus = max(0, 6 - therapist_sessions_today)
        priority += workload_bonus
        
        return priority

    def manual_adjust_session(self, session_id: int, updates: Dict[str, Any]) -> GenerationResult:
        """Handle manual adjustments to existing sessions"""
        
        try:
            # Get existing session data for conflict checking
            existing_sessions = []  # This would be fetched from database
            
            # Validate the update
            update_valid, errors = self.validator.validate_session_update(updates, existing_sessions)
            if not update_valid:
                return GenerationResult(success=False, sessions_created=0, errors=errors)
            
            # Apply the update (this would integrate with routes.py)
            # The actual database update would happen in the route handler
            
            return GenerationResult(
                success=True,
                sessions_created=0,
                warnings=["Session updated successfully"]
            )
            
        except Exception as e:
            return GenerationResult(
                success=False,
                sessions_created=0,
                errors=[f"Update error: {str(e)}"]
            )