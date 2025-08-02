import redis
import json
import pickle
from typing import Any, Optional, List, Dict
from datetime import date, timedelta
import logging

logger = logging.getLogger(__name__)

class RedisHelper:
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, decode_responses: bool = True):
        try:
            self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=decode_responses)
            self.redis_client.ping()
            self.available = True
        except (redis.ConnectionError, redis.TimeoutError):
            self.redis_client = None
            self.available = False
            logger.warning("Redis not available, falling back to database")

    def get(self, key: str) -> Optional[Any]:
        if not self.available:
            return None
        try:
            value = self.redis_client.get(key)
            return json.loads(value) if value else None
        except (redis.RedisError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        if not self.available:
            return False
        try:
            serialized = json.dumps(value, default=str)
            return self.redis_client.setex(key, ttl, serialized)
        except (redis.RedisError, TypeError):
            return False

    def delete(self, key: str) -> bool:
        if not self.available:
            return False
        try:
            return bool(self.redis_client.delete(key))
        except redis.RedisError:
            return False

    def get_list(self, pattern: str) -> List[str]:
        if not self.available:
            return []
        try:
            return self.redis_client.keys(pattern)
        except redis.RedisError:
            return []

    def invalidate_pattern(self, pattern: str) -> int:
        if not self.available:
            return 0
        try:
            keys = self.redis_client.keys(pattern)
            return self.redis_client.delete(*keys) if keys else 0
        except redis.RedisError:
            return 0

    def cache_children_list(self, children_data: List[Dict]) -> bool:
        return self.set("children:active", children_data, ttl=1800)

    def get_children_list(self) -> Optional[List[Dict]]:
        return self.get("children:active")

    def cache_therapists_list(self, therapists_data: List[Dict]) -> bool:
        return self.set("therapists:active", therapists_data, ttl=1800)

    def get_therapists_list(self) -> Optional[List[Dict]]:
        return self.get("therapists:active")

    def cache_departments_list(self, departments_data: List[Dict]) -> bool:
        return self.set("departments:all", departments_data, ttl=7200)

    def get_departments_list(self) -> Optional[List[Dict]]:
        return self.get("departments:all")

    def cache_child_availability(self, child_id: int, availability_data: List[Dict]) -> bool:
        return self.set(f"child:availability:{child_id}", availability_data, ttl=3600)

    def get_child_availability(self, child_id: int) -> Optional[List[Dict]]:
        return self.get(f"child:availability:{child_id}")

    def cache_therapist_availability(self, therapist_id: int, availability_data: List[Dict]) -> bool:
        return self.set(f"therapist:availability:{therapist_id}", availability_data, ttl=3600)

    def get_therapist_availability(self, therapist_id: int) -> Optional[List[Dict]]:
        return self.get(f"therapist:availability:{therapist_id}")

    def cache_weekly_timetable(self, week_starting: date, timetable_data: Dict) -> bool:
        key = f"timetable:week:{week_starting}"
        return self.set(key, timetable_data, ttl=86400)

    def get_weekly_timetable(self, week_starting: date) -> Optional[Dict]:
        key = f"timetable:week:{week_starting}"
        return self.get(key)

    def cache_weekly_report(self, week_starting: date, report_data: Dict) -> bool:
        key = f"report:week:{week_starting}"
        return self.set(key, report_data, ttl=21600)

    def get_weekly_report(self, week_starting: date) -> Optional[Dict]:
        key = f"report:week:{week_starting}"
        return self.get(key)

    def cache_daily_report(self, report_date: date, report_data: Dict) -> bool:
        key = f"report:daily:{report_date}"
        return self.set(key, report_data, ttl=10800)

    def get_daily_report(self, report_date: date) -> Optional[Dict]:
        key = f"report:daily:{report_date}"
        return self.get(key)

    def invalidate_child_cache(self, child_id: int = None) -> int:
        if child_id:
            patterns = [f"child:availability:{child_id}"]
        else:
            patterns = ["children:active", "child:availability:*"]
        
        total_deleted = 0
        for pattern in patterns:
            total_deleted += self.invalidate_pattern(pattern)
        return total_deleted

    def invalidate_therapist_cache(self, therapist_id: int = None) -> int:
        if therapist_id:
            patterns = [f"therapist:availability:{therapist_id}"]
        else:
            patterns = ["therapists:active", "therapist:availability:*"]
        
        total_deleted = 0
        for pattern in patterns:
            total_deleted += self.invalidate_pattern(pattern)
        return total_deleted

    def invalidate_timetable_cache(self, week_starting: date = None) -> int:
        if week_starting:
            patterns = [f"timetable:week:{week_starting}", f"report:week:{week_starting}"]
        else:
            patterns = ["timetable:week:*", "report:week:*", "report:daily:*"]
        
        total_deleted = 0
        for pattern in patterns:
            total_deleted += self.invalidate_pattern(pattern)
        return total_deleted

redis_helper = RedisHelper()