import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Optional, Dict, Any
from core.models import EmailRecord
from core.db import Database
from rich.console import Console

console = Console()

class EmailSender:
    def __init__(self, config: Dict[str, Any], db: Database):
        self.config = config.get('email_outreach', {})
        self.db = db
        self.enabled = self.config.get('enabled', True)
        self.dry_run = self.config.get('dry_run', True)
        self.smtp_host = self.config.get('smtp_host', 'smtp.gmail.com')
        self.smtp_port = self.config.get('smtp_port', 587)
        self.use_tls = self.config.get('smtp_use_tls', True)
        self.sender_email = self.config.get('sender_email', '')
        self.password_env = self.config.get('sender_password_env_var', 'EMAIL_APP_PASSWORD')
        self.default_resume_path = self.config.get('default_resume_path', './resumes/resume.pdf')

    def send_outreach_email(
        self,
        job_id: str,
        recipient_email: str,
        subject: str,
        body: str,
        resume_path: Optional[str] = None
    ) -> EmailRecord:
        target_resume = resume_path or self.default_resume_path
        has_resume = os.path.exists(target_resume)

        if self.db.email_already_sent(recipient_email):
            console.print(f'[yellow]i Skipped emailing {recipient_email}: Already emailed previously.[/yellow]')
            return EmailRecord(
                job_id=job_id,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                resume_attached=has_resume,
                status='SKIPPED_DUPLICATE'
            )

        if self.dry_run:
            console.print(f'[bold cyan][DRY RUN] Would send email to:[/bold cyan] {recipient_email}')
            console.print(f'[bold]Subject:[/bold] {subject}')
            console.print(f'[bold]Resume Attached:[/bold] {has_resume} ({target_resume})')
            console.print(f'[dim]--- Email Body Preview ---\n{body[:300]}...\n--------------------------[/dim]')
            
            record = EmailRecord(
                job_id=job_id,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                resume_attached=has_resume,
                status='DRY_RUN'
            )
            self.db.record_email(record)
            return record

        sender_pwd = os.environ.get(self.password_env)
        if not sender_pwd or not self.sender_email:
            err_msg = f'Missing credentials: set {self.password_env} in environment and sender_email in settings.yaml'
            console.print(f'[red]X {err_msg}[/red]')
            record = EmailRecord(
                job_id=job_id,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                resume_attached=has_resume,
                status='FAILED',
                error_message=err_msg
            )
            self.db.record_email(record)
            return record

        try:
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'plain'))

            if has_resume:
                with open(target_resume, 'rb') as f:
                    attach = MIMEApplication(f.read(), _subtype='pdf')
                    filename = os.path.basename(target_resume)
                    attach.add_header('Content-Disposition', 'attachment', filename=filename)
                    msg.attach(attach)

            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            if self.use_tls:
                server.starttls()
            server.login(self.sender_email, sender_pwd)
            server.send_message(msg)
            server.quit()

            console.print(f'[bold green]Successfully sent cold email to {recipient_email}[/bold green]')
            record = EmailRecord(
                job_id=job_id,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                resume_attached=has_resume,
                status='SENT'
            )
            self.db.record_email(record)
            return record

        except Exception as e:
            console.print(f'[bold red]Failed sending email to {recipient_email}: {str(e)}[/bold red]')
            record = EmailRecord(
                job_id=job_id,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
                resume_attached=has_resume,
                status='FAILED',
                error_message=str(e)
            )
            self.db.record_email(record)
            return record
