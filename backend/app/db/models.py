from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.db.database import Base


class Execution(Base):
    __tablename__ = "executions"

    job_id = Column(String, primary_key=True)
    language = Column(String, nullable=False)
    status = Column(String, nullable=False)
    policy_profile = Column(String, default="basic")
    stdout = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)