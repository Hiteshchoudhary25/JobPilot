import pytest
import os
import yaml
from core.models import JobPosting, ApplicationRecord, EmailRecord, ApplicationStatus
from core.db import Database
from core.llm import LLMAssistant
from outreach.email_extractor import EmailExtractor
from outreach.email_sender import EmailSender

def test_email_extractor():
    text = """
    We are hiring Salesforce Developers!
    Please send your CV and portfolio to careers@techinnovate.io or hr.recruitment@globalsoft.com.
    For privacy policy visit https://example.com/privacy or contact support@linkedin.com.
    """
    emails = EmailExtractor.extract_emails(text)
    assert "careers@techinnovate.io" in emails
    assert "hr.recruitment@globalsoft.com" in emails
    assert "support@linkedin.com" not in emails
    assert not any("example.com" in e for e in emails)

def test_llm_fit_and_email_generation():
    with open("config/user_profile.yaml", "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    llm = LLMAssistant()
    score, reason = llm.evaluate_fit(
        "Salesforce Developer",
        "Looking for an engineer with Java, Salesforce Admin, and API integration experience.",
        profile
    )
    assert score >= 70
    assert "salesforce" in reason.lower() or "java" in reason.lower()

    email_data = llm.generate_cold_email(
        "Salesforce Developer",
        "Acme Corp",
        "Job description here",
        profile
    )
    assert "Acme Corp" in email_data["subject"] or "Acme Corp" in email_data["body"]
    assert profile["personal_info"]["full_name"] in email_data["body"]

def test_database_operations(tmp_path):
    db_file = str(tmp_path / "test_tracker.db")
    db = Database(db_file)

    job = JobPosting(
        id="test_job_1",
        platform="linkedin",
        title="Salesforce Engineer",
        company="CloudCorp",
        location="Remote",
        url="https://linkedin.com/jobs/view/12345",
        recruiter_emails=["hr@cloudcorp.com"],
        fit_score=85
    )
    db.save_job(job)
    assert db.job_exists("test_job_1")

    app_rec = ApplicationRecord(
        job_id="test_job_1",
        platform="linkedin",
        company="CloudCorp",
        role_title="Salesforce Engineer",
        job_url="https://linkedin.com/jobs/view/12345",
        status=ApplicationStatus.APPLIED_EASY
    )
    db.record_application(app_rec)
    assert db.application_exists("test_job_1")

    stats = db.get_stats()
    assert stats["total_discovered"] == 1
    assert stats["easy_applied"] == 1