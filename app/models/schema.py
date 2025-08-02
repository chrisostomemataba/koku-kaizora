from datetime import datetime
from sqlalchemy import Column, String, Integer, ForeignKey, Date, Time, Boolean, DateTime, Enum
from sqlalchemy.orm import relationship, declarative_base
import enum

Base = declarative_base()

class UserRole(enum.Enum):
   MANAGER = "manager"
   THERAPIST = "therapist"

class User(Base):
   __tablename__ = "users"
   
   id = Column(Integer, primary_key=True, index=True)
   name = Column(String, nullable=False, index=True)
   email = Column(String, unique=True, nullable=False, index=True)
   role = Column(Enum(UserRole), nullable=False)
   created_at = Column(DateTime, default=datetime.utcnow)

class Department(Base):
   __tablename__ = "departments"
   
   id = Column(Integer, primary_key=True, index=True)
   name = Column(String, unique=True, nullable=False, index=True)

class Child(Base):
   __tablename__ = "children"
   
   id = Column(Integer, primary_key=True, index=True)
   name = Column(String, nullable=False, index=True)
   is_active = Column(Boolean, default=True, index=True)
   created_at = Column(DateTime, default=datetime.utcnow)
   
   departments = relationship("ChildDepartment", back_populates="child")
   availability = relationship("ChildAvailability", back_populates="child")
   sessions = relationship("Session", back_populates="child")

class Therapist(Base):
   __tablename__ = "therapists"
   
   id = Column(Integer, primary_key=True, index=True)
   name = Column(String, nullable=False, index=True)
   department_id = Column(Integer, ForeignKey("departments.id"), nullable=False, index=True)
   is_active = Column(Boolean, default=True, index=True)
   created_at = Column(DateTime, default=datetime.utcnow)
   
   department = relationship("Department")
   availability = relationship("TherapistAvailability", back_populates="therapist")
   sessions = relationship("Session", back_populates="therapist")

class ChildDepartment(Base):
   __tablename__ = "child_departments"
   
   id = Column(Integer, primary_key=True, index=True)
   child_id = Column(Integer, ForeignKey("children.id"), nullable=False, index=True)
   department_id = Column(Integer, ForeignKey("departments.id"), nullable=False, index=True)
   sessions_per_week = Column(Integer, default=1, nullable=False)
   
   child = relationship("Child", back_populates="departments")
   department = relationship("Department")

class ChildAvailability(Base):
   __tablename__ = "child_availability"
   
   id = Column(Integer, primary_key=True, index=True)
   child_id = Column(Integer, ForeignKey("children.id"), nullable=False, index=True)
   day_of_week = Column(String, nullable=False, index=True)
   start_time = Column(Time, nullable=False)
   end_time = Column(Time, nullable=False)
   
   child = relationship("Child", back_populates="availability")

class TherapistAvailability(Base):
   __tablename__ = "therapist_availability"
   
   id = Column(Integer, primary_key=True, index=True)
   therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
   day_of_week = Column(String, nullable=False, index=True)
   start_time = Column(Time, nullable=False)
   end_time = Column(Time, nullable=False)
   is_available = Column(Boolean, default=True, index=True)
   
   therapist = relationship("Therapist", back_populates="availability")

class Session(Base):
   __tablename__ = "sessions"
   
   id = Column(Integer, primary_key=True, index=True)
   child_id = Column(Integer, ForeignKey("children.id"), nullable=False, index=True)
   therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
   department_id = Column(Integer, ForeignKey("departments.id"), nullable=False, index=True)
   date = Column(Date, nullable=False, index=True)
   start_time = Column(Time, nullable=False)
   end_time = Column(Time, nullable=False)
   week_starting = Column(Date, nullable=False, index=True)
   created_at = Column(DateTime, default=datetime.utcnow)
   
   child = relationship("Child", back_populates="sessions")
   therapist = relationship("Therapist", back_populates="sessions")
   department = relationship("Department")
   logs = relationship("SessionLog", back_populates="session")

class SessionLog(Base):
   __tablename__ = "session_logs"
   
   id = Column(Integer, primary_key=True, index=True)
   session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
   changed_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
   change_type = Column(String, nullable=False)
   old_value = Column(String)
   new_value = Column(String)
   changed_at = Column(DateTime, default=datetime.utcnow, index=True)
   
   session = relationship("Session", back_populates="logs")
   user = relationship("User")