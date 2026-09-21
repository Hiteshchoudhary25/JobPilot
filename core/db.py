import sqlite3
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from .models import JobPosting, ApplicationRecord, EmailRecord, ApplicationStatus

class Database:
    def __init__(self, db_path: str = "tracker.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    url TEXT NOT NULL,
                    description TEXT,
                    easy_apply INTEGER DEFAULT 0,
                    recruiter_emails TEXT,
                    fit_score INTEGER,
                    fit_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role_title TEXT NOT NULL,
                    job_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    resume_attached INTEGER DEFAULT 1,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.commit()

    def job_exists(self, job_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
            return cursor.fetchone() is not None

    def application_exists(self, job_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM applications WHERE job_id = ?", (job_id,))
            return cursor.fetchone() is not None

    def email_already_sent(self, recipient_email: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM emails WHERE recipient_email = ? AND status IN ('SENT', 'DRY_RUN')", (recipient_email,))
            return cursor.fetchone() is not None

    def save_job(self, job: JobPosting):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO jobs 
                (id, platform, title, company, location, url, description, easy_apply, recruiter_emails, fit_score, fit_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.platform, job.title, job.company, job.location, job.url,
                job.description, 1 if job.easy_apply else 0,
                ",".join(job.recruiter_emails), job.fit_score, job.fit_reason,
                job.created_at.isoformat()
            ))
            conn.commit()

    def record_application(self, record: ApplicationRecord):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO applications 
                (job_id, platform, company, role_title, job_url, status, applied_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.job_id, record.platform, record.company, record.role_title,
                record.job_url, record.status.value if hasattr(record.status, "value") else record.status,
                record.applied_at.isoformat(), record.notes
            ))
            conn.commit()

    def record_email(self, record: EmailRecord):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO emails 
                (job_id, recipient_email, subject, body, resume_attached, sent_at, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.job_id, record.recipient_email, record.subject, record.body,
                1 if record.resume_attached else 0, record.sent_at.isoformat(),
                record.status, record.error_message
            ))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM jobs")
            total_jobs = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'APPLIED_EASY'")
            easy_applied = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'EMAIL_SENT'")
            emails_sent = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'REQUIRES_MANUAL'")
            manual_queued = cursor.fetchone()[0]
            cursor.execute("SELECT platform, COUNT(*) FROM applications GROUP BY platform")
            by_platform = {row[0]: row[1] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT role_title, company, platform, status, applied_at 
                FROM applications ORDER BY applied_at DESC LIMIT 10
            """)
            recent_apps = [dict(row) for row in cursor.fetchall()]

            return {
                "total_discovered": total_jobs,
                "easy_applied": easy_applied,
                "cold_emails_sent": emails_sent,
                "manual_queued": manual_queued,
                "by_platform": by_platform,
                "recent_applications": recent_apps
            }

    def reset_platform_applications(self, platform: str) -> int:
        """Delete all application records for a platform so jobs can be re-tried.
        Returns number of records deleted."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM applications WHERE platform = ?", (platform,))
            conn.commit()
            return cursor.rowcount

    def get_jobs_for_platform(self, platform: str) -> List[Dict[str, Any]]:
        """Fetch all stored jobs for a platform from the DB."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, platform, title, company, location, url, description,
                       easy_apply, recruiter_emails, fit_score, fit_reason
                FROM jobs WHERE platform = ?
                ORDER BY created_at DESC
            """, (platform,))
            return [dict(row) for row in cursor.fetchall()]

    def mark_job_applied(self, job_id: str, notes: str = "Manually applied by user") -> bool:
        """Marks a job as applied in the applications table."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT platform, company, title, url FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute("""
                INSERT OR REPLACE INTO applications
                (job_id, platform, company, role_title, job_url, status, applied_at, notes)
                VALUES (?, ?, ?, ?, ?, 'APPLIED_EASY', CURRENT_TIMESTAMP, ?)
            """, (job_id, row["platform"], row["company"], row["title"], row["url"], notes))
            conn.commit()
            return True

