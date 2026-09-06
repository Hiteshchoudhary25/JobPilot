from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class ApplicationStatus(str, Enum):
    FOUND = "FOUND"
    FILTERED_OUT = "FILTERED_OUT"
    APPLIED_EASY = "APPLIED_EASY"
    APPLIED_EXTERNAL = "APPLIED_EXTERNAL"
    EMAIL_SENT = "EMAIL_SENT"
    FAILED = "FAILED"
    REQUIRES_MANUAL = "REQUIRES_MANUAL"

class JobPosting(BaseModel):
    id: str
    platform: str
    title: str
    company: str
    location: str
    url: str
    description: Optional[str] = ""
    posted_date: Optional[str] = ""
    easy_apply: bool = False
    recruiter_emails: List[str] = Field(default_factory=list)
    fit_score: Optional[int] = None
    fit_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ApplicationRecord(BaseModel):
    id: Optional[int] = None
    job_id: str
    platform: str
    company: str
    role_title: str
    job_url: str
    status: ApplicationStatus
    applied_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None

class EmailRecord(BaseModel):
    id: Optional[int] = None
    job_id: str
    recipient_email: str
    subject: str
    body: str
    resume_attached: bool
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str
    error_message: Optional[str] = None
