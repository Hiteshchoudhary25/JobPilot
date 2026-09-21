import os
import sys
import yaml
import argparse
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from core.models import JobPosting, ApplicationStatus, ApplicationRecord
from core.db import Database
from core.llm import LLMAssistant
from core.browser_manager import BrowserSessionManager
from outreach.email_sender import EmailSender
from scrapers.linkedin import LinkedInScraper
from scrapers.naukri import NaukriScraper

load_dotenv()
console = Console()

# Roles too senior or irrelevant — skip applying to these
SENIORITY_BLACKLIST = [
    "technical lead", "tech lead", "solutions architect", "staff software",
    "senior salesforce", "lead software", "sr solutions", "data cloud sme",
    "vlocity", "dynamics crm", "marketing cloud", "real estate"
]

def load_yaml_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        console.print(f"[red]Error: Config file not found at {path}[/red]")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class JobPilotAgent:
    def __init__(self):
        self.profile = load_yaml_config("config/user_profile.yaml")
        self.criteria = load_yaml_config("config/search_criteria.yaml")
        self.settings = load_yaml_config("config/settings.yaml")

        self.db = Database("tracker.db")
        self.llm = LLMAssistant(
            provider=self.settings.get("llm", {}).get("provider", "gemini"),
            model=self.settings.get("llm", {}).get("model", "gemini-1.5-flash")
        )
        self.email_sender = EmailSender(self.settings, self.db)
        self.browser_manager = BrowserSessionManager()

        self.scrapers = {
            "linkedin": LinkedInScraper(self.settings, self.db, self.llm),
            "naukri": NaukriScraper(self.settings, self.db, self.llm),
        }

    def _is_too_senior(self, job_title: str) -> bool:
        title_lower = job_title.lower()
        return any(s in title_lower for s in SENIORITY_BLACKLIST)

    def _is_allowed_location(self, job: JobPosting) -> Tuple[bool, str]:
        """Enforces that non-local cities (Pune, Hyderabad, Bangalore) must be strictly Remote or Hybrid."""
        restricted_cities = self.criteria.get("filtering_rules", {}).get("remote_or_hybrid_only_cities", [])
        combined_text = f"{job.location} {job.title} {job.description}".lower()

        for city in restricted_cities:
            if city.lower() in job.location.lower():
                is_remote_or_hybrid = any(k in combined_text for k in ["remote", "hybrid", "work from home", "wfh", "telecommute"])
                if not is_remote_or_hybrid:
                    return False, f"On-site in {city} (only Remote/Hybrid accepted for {city})"
        return True, ""

    def search_all_platforms(self, limit_per_platform: int = 20) -> List[JobPosting]:
        console.print(Panel("[bold blue]Starting Multi-Platform Job Search & Ingestion[/bold blue]"))
        all_jobs: List[JobPosting] = []
        platforms = self.criteria.get("platforms_enabled", {})
        min_fit = self.criteria.get("filtering_rules", {}).get("min_fit_score", 50)

        for platform_name, enabled in platforms.items():
            if not enabled or platform_name not in self.scrapers:
                continue

            console.print(f"[cyan]Querying {platform_name.upper()}...[/cyan]")
            scraper = self.scrapers[platform_name]
            jobs = scraper.search_jobs(self.criteria, limit=limit_per_platform)
            console.print(f"  -> Found {len(jobs)} jobs on {platform_name.upper()}")

            for job in jobs:
                score, reason = self.llm.evaluate_fit(job.title, job.description or "", self.profile)
                job.fit_score = score
                job.fit_reason = reason

                blacklisted = self.criteria.get("filtering_rules", {}).get("blacklisted_keywords", [])
                if any(b.lower() in job.title.lower() for b in blacklisted):
                    job.fit_score = 0
                    job.fit_reason = "Contains blacklisted keyword"

                # Check city-specific remote/hybrid rule (Pune, Hyderabad, Bangalore must be Remote/Hybrid)
                allowed_loc, loc_reason = self._is_allowed_location(job)
                if not allowed_loc:
                    job.fit_score = 0
                    job.fit_reason = loc_reason

                self.db.save_job(job)
                all_jobs.append(job)

        table = Table(title="Job Postings Discovered & Evaluated", show_lines=True)
        table.add_column("Platform", style="cyan", width=10)
        table.add_column("Role", style="bold white")
        table.add_column("Company", style="green")
        table.add_column("Location", style="yellow")
        table.add_column("Score", justify="center")
        table.add_column("Recruiter Email(s)", style="magenta")

        for job in all_jobs:
            score_color = "green" if (job.fit_score or 0) >= min_fit else "red"
            score_text = f"[{score_color}]{job.fit_score}%[/{score_color}]"
            emails_text = ", ".join(job.recruiter_emails) if job.recruiter_emails else "-"
            table.add_row(job.platform.upper(), job.title, job.company, job.location, score_text, emails_text)

        console.print(table)
        return all_jobs

    def run_cold_email_outreach(self, jobs: Optional[List[JobPosting]] = None):
        console.print(Panel("[bold magenta]Running Cold Email Recruiter Outreach Engine[/bold magenta]"))

        if not jobs:
            jobs = self.search_all_platforms()

        emails_sent = 0
        max_emails = self.settings.get("email_outreach", {}).get("max_emails_per_day", 20)
        # In-memory dedup for THIS run — prevents the same email being tried multiple times
        sent_this_run: set = set()

        # Also scan DB for previously discovered jobs with recruiter emails not yet emailed
        try:
            import sqlite3
            conn = sqlite3.connect("tracker.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT j.id, j.platform, j.title, j.company, j.location, j.url, j.description, j.recruiter_emails
                FROM jobs j
                WHERE j.recruiter_emails IS NOT NULL AND j.recruiter_emails != ''
            """)
            db_jobs_with_email = cursor.fetchall()
            conn.close()

            existing_ids = {j.id for j in jobs}
            for row in db_jobs_with_email:
                if row["id"] in existing_ids:
                    continue  # Already in jobs list — don't double-add
                emails_in_row = [e.strip() for e in (row["recruiter_emails"] or "").split(",") if e.strip()]
                if not emails_in_row:
                    continue
                has_unsent = any(not self.db.email_already_sent(e) for e in emails_in_row)
                if has_unsent:
                    dummy_job = JobPosting(
                        id=row["id"],
                        platform=row["platform"],
                        title=row["title"],
                        company=row["company"],
                        location=row["location"],
                        url=row["url"],
                        description=row["description"] or "",
                        recruiter_emails=emails_in_row,
                        fit_score=80
                    )
                    jobs.append(dummy_job)
                    existing_ids.add(row["id"])
        except Exception as e:
            console.print(f"[dim]DB scan note: {e}[/dim]")

        for job in jobs:
            if emails_sent >= max_emails:
                console.print("[yellow]Reached maximum email limit for today.[/yellow]")
                break

            if not job.recruiter_emails:
                continue

            for email in job.recruiter_emails:
                # Skip if already sent (DB check) or already attempted this run (in-memory check)
                if email in sent_this_run or self.db.email_already_sent(email):
                    continue

                sent_this_run.add(email)  # Mark immediately to prevent duplicates
                console.print(f"[bold green]Found recruiter email {email} for {job.company} ({job.title})[/bold green]")
                email_content = self.llm.generate_cold_email(job.title, job.company, job.description or "", self.profile)

                record = self.email_sender.send_outreach_email(
                    job_id=job.id,
                    recipient_email=email,
                    subject=email_content["subject"],
                    body=email_content["body"]
                )

                if record.status in ("SENT", "DRY_RUN"):
                    self.db.record_application(ApplicationRecord(
                        job_id=job.id,
                        platform=job.platform,
                        company=job.company,
                        role_title=job.title,
                        job_url=job.url,
                        status=ApplicationStatus.EMAIL_SENT,
                        notes=f"Cold email sent to {email}"
                    ))
                    emails_sent += 1

        console.print(f"[bold]Outreach finished: Sent {emails_sent} recruiter email(s).[/bold]")


    def run_auto_applications(self, jobs: Optional[List[JobPosting]] = None, include_db_jobs: bool = False):
        console.print(Panel("[bold green]Running Auto-Apply Engine[/bold green]"))
        if not jobs:
            jobs = self.search_all_platforms()

        # If include_db_jobs: also load previously found LinkedIn/Naukri jobs from DB
        if include_db_jobs:
            console.print("[cyan]Loading previously discovered jobs from DB for retry...[/cyan]")
            existing_ids = {j.id for j in jobs}
            for platform in ["linkedin", "naukri"]:
                db_rows = self.db.get_jobs_for_platform(platform)
                for row in db_rows:
                    if row["id"] in existing_ids:
                        continue
                    emails = [e.strip() for e in (row.get("recruiter_emails") or "").split(",") if e.strip()]
                    job = JobPosting(
                        id=row["id"],
                        platform=row["platform"],
                        title=row["title"],
                        company=row["company"],
                        location=row.get("location") or "",
                        url=row["url"],
                        description=row.get("description") or "",
                        easy_apply=bool(row.get("easy_apply", 1)),
                        recruiter_emails=emails,
                        fit_score=row.get("fit_score") or 70,
                    )
                    jobs.append(job)
                    existing_ids.add(row["id"])
            console.print(f"[cyan]Total jobs to attempt (new + DB): {len(jobs)}[/cyan]")

        min_fit = self.criteria.get("filtering_rules", {}).get("min_fit_score", 50)
        applied_count = 0
        skipped_senior = 0
        max_apps = self.settings.get("automation", {}).get("max_applications_per_day", 30)

        for job in jobs:
            if applied_count >= max_apps:
                console.print("[yellow]Reached daily application limit.[/yellow]")
                break

            if (job.fit_score or 0) < min_fit:
                continue

            if self.db.application_exists(job.id):
                continue

            if self._is_too_senior(job.title):
                console.print(f"[dim]Skipping senior/irrelevant role: {job.title} at {job.company}[/dim]")
                skipped_senior += 1
                continue

            allowed_loc, loc_reason = self._is_allowed_location(job)
            if not allowed_loc:
                console.print(f"[dim]Skipping on-site non-local role: {job.title} at {job.company} ({loc_reason})[/dim]")
                continue

            scraper = self.scrapers.get(job.platform)
            if scraper:
                success = scraper.apply_job(job, self.profile)
                if success:
                    applied_count += 1

        console.print(f"[bold green]Application run finished: {applied_count} applied, {skipped_senior} senior roles skipped.[/bold green]")

    def show_statistics(self):
        stats = self.db.get_stats()
        console.print(Panel("[bold cyan]Job Application Agent Analytics[/bold cyan]"))

        stat_table = Table(title="Summary Statistics")
        stat_table.add_column("Metric", style="bold white")
        stat_table.add_column("Value", style="bold green", justify="right")

        stat_table.add_row("Total Jobs Discovered", str(stats["total_discovered"]))
        stat_table.add_row("Applications Submitted", str(stats["easy_applied"]))
        stat_table.add_row("Cold Emails Sent", str(stats["cold_emails_sent"]))
        stat_table.add_row("External Links for Manual Review", str(stats["manual_queued"]))
        console.print(stat_table)

        if stats["recent_applications"]:
            recent_table = Table(title="Recent Applications Log", show_lines=True)
            recent_table.add_column("Role", style="white")
            recent_table.add_column("Company", style="green")
            recent_table.add_column("Platform", style="cyan")
            recent_table.add_column("Status", style="magenta")
            recent_table.add_column("Applied At", style="dim")

            for app in stats["recent_applications"]:
                recent_table.add_row(
                    app["role_title"], app["company"],
                    app["platform"].upper(), app["status"], str(app["applied_at"])
                )
            console.print(recent_table)

    def login_platform(self, platform: str):
        if platform == "all":
            for p in ["linkedin", "naukri"]:
                self.browser_manager.interactive_login(p)
        else:
            self.browser_manager.interactive_login(platform)

    def reset_platform(self, platform: str):
        """Clear application records for a platform so jobs can be re-tried."""
        if platform == "all":
            for p in ["linkedin", "naukri"]:
                count = self.db.reset_platform_applications(p)
                console.print(f"[yellow]Reset {count} application records for {p.upper()}[/yellow]")
        else:
            count = self.db.reset_platform_applications(platform)
            console.print(f"[bold yellow]Reset {count} application records for {platform.upper()}.[/bold yellow]")
            console.print(f"[cyan]Jobs from {platform.upper()} can now be re-applied. Run:[/cyan]")
            console.print(f"[bold]  python main.py apply --platform {platform} --include-db[/bold]")

def main():
    parser = argparse.ArgumentParser(description="JobPilot - Autonomous Job Search & Application Agent")
    parser.add_argument("command", choices=["search", "apply", "email", "run", "stats", "test-email", "login", "reset", "dashboard", "mark-applied"],
                        help="Action to perform")
    parser.add_argument("job_id", nargs="?", default="", help="Job ID (for mark-applied)")
    parser.add_argument("--limit", type=int, default=20, help="Limit number of jobs per platform")
    parser.add_argument("--platform", choices=["linkedin", "naukri", "all"], default="linkedin",
                        help="Platform to target")
    parser.add_argument("--include-db", action="store_true",
                        help="Also retry previously discovered jobs stored in DB (use with apply/run)")

    args = parser.parse_args()
    agent = JobPilotAgent()

    if args.command == "search":
        agent.search_all_platforms(limit_per_platform=args.limit)
        try:
            from dashboard import generate_dashboard
            generate_dashboard(auto_open=False)
        except Exception:
            pass
    elif args.command == "email":
        agent.run_cold_email_outreach()
        try:
            from dashboard import generate_dashboard
            generate_dashboard(auto_open=False)
        except Exception:
            pass
    elif args.command == "apply":
        agent.run_auto_applications(include_db_jobs=args.include_db)
        try:
            from dashboard import generate_dashboard
            generate_dashboard(auto_open=False)
        except Exception:
            pass
    elif args.command == "run":
        jobs = agent.search_all_platforms(limit_per_platform=args.limit)
        agent.run_auto_applications(jobs, include_db_jobs=args.include_db)
        agent.run_cold_email_outreach(jobs)
        try:
            from dashboard import generate_dashboard
            generate_dashboard(auto_open=False)
        except Exception:
            pass
    elif args.command == "stats":
        agent.show_statistics()
    elif args.command == "login":
        agent.login_platform(args.platform)
    elif args.command == "reset":
        agent.reset_platform(args.platform)
    elif args.command == "dashboard":
        from dashboard import serve_dashboard
        serve_dashboard()
    elif args.command == "mark-applied":
        if not args.job_id:
            console.print("[red]Error: Please specify the job_id (e.g. python main.py mark-applied <job_id>)[/red]")
        else:
            success = agent.db.mark_job_applied(args.job_id)
            if success:
                console.print(f"[bold green]Successfully marked job '{args.job_id}' as APPLIED in tracker.db[/bold green]")
                try:
                    from dashboard import generate_dashboard
                    generate_dashboard(auto_open=False)
                except Exception:
                    pass
            else:
                console.print(f"[red]Job ID '{args.job_id}' not found in database.[/red]")
    elif args.command == "test-email":
        console.print("[cyan]Sending test email...[/cyan]")
        agent.email_sender.send_outreach_email(
            job_id="test_001",
            recipient_email=agent.profile.get("personal_info", {}).get("email", "test@example.com"),
            subject="JobPilot Agent: Test Cold Email",
            body="Hello! This is a test email verifying that your JobPilot outreach agent is configured properly."
        )

if __name__ == "__main__":
    main()