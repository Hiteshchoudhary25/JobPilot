import os
import re
import time
import random
from typing import List, Dict, Any, Set
from playwright.sync_api import sync_playwright
from core.models import JobPosting, ApplicationStatus, ApplicationRecord
from .base import BaseScraper
from rich.console import Console

console = Console()

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SESSION_DIR = os.path.abspath("browser_sessions/naukri")

class NaukriScraper(BaseScraper):
    def search_jobs(self, criteria: Dict[str, Any], limit: int = 15) -> List[JobPosting]:
        search_params = criteria.get("search_parameters", {})
        titles = search_params.get("job_titles", ["Salesforce Developer"])
        locations = search_params.get("locations", ["Ahmedabad", "Jaipur"])

        found_jobs: List[JobPosting] = []
        seen_ids: Set[str] = set()

        valid_locs = [l for l in locations if l.lower() not in ["india", "remote"]]
        if not valid_locs:
            valid_locs = ["Ahmedabad", "Jaipur"]

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    SESSION_DIR,
                    headless=True,
                    executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--start-maximized"
                    ],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.pages[0] if context.pages else context.new_page()

                for title in titles:
                    if len(found_jobs) >= limit:
                        break
                    
                    title_slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")

                    for loc in valid_locs:
                        if len(found_jobs) >= limit:
                            break

                        loc_slug = re.sub(r"[^a-zA-Z0-9]+", "-", loc.strip().lower()).strip("-")
                        url = f"https://www.naukri.com/{title_slug}-jobs-in-{loc_slug}"

                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=25000)
                            page.wait_for_timeout(random.randint(1800, 2400))

                            cards = page.query_selector_all("div.cust-job-tuple, article.jobTuple, div.srp-jobtuple-wrapper")
                            if not cards:
                                continue

                            for card in cards:
                                if len(found_jobs) >= limit:
                                    break

                                t_el = card.query_selector("a.title")
                                comp_el = card.query_selector("a.comp-name")
                                loc_el = card.query_selector(".locWdth, .loc-wrap, .location")
                                desc_el = card.query_selector(".job-desc, .ni-job-tuple-icon-srp-description, .ellipsis")

                                if not t_el:
                                    continue

                                job_title = t_el.inner_text().strip()
                                job_url = t_el.get_attribute("href") or ""
                                company = comp_el.inner_text().strip() if comp_el else "Confidential"
                                job_loc = loc_el.inner_text().strip() if loc_el else loc
                                desc_snippet = desc_el.inner_text().strip() if desc_el else ""

                                raw_id = card.get_attribute("data-job-id")
                                if not raw_id:
                                    id_match = re.search(r"(\d{10,})", job_url)
                                    raw_id = id_match.group(1) if id_match else str(abs(hash(job_url)))

                                job_id = f"nk_{raw_id}"

                                if job_id in seen_ids or self.db.job_exists(job_id):
                                    continue
                                seen_ids.add(job_id)

                                emails = self.email_extractor.extract_emails(desc_snippet)

                                posting = JobPosting(
                                    id=job_id,
                                    platform="naukri",
                                    title=job_title,
                                    company=company,
                                    location=job_loc,
                                    url=job_url,
                                    description=desc_snippet,
                                    easy_apply=True,
                                    recruiter_emails=emails
                                )
                                found_jobs.append(posting)

                        except Exception as e:
                            console.print(f"[dim]Naukri search notice for {title} in {loc}: {e}[/dim]")

                context.close()

        except Exception as e:
            console.print(f"[yellow]Error during Naukri search: {e}[/yellow]")

        return found_jobs

    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        """Navigates to the live Naukri job page and clicks Apply using the authenticated session."""
        console.print(f"[bold magenta]Navigating live Naukri job page:[/bold magenta] {job.title} at {job.company}")

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    SESSION_DIR,
                    headless=True,
                    executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars"
                    ],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.pages[0] if context.pages else context.new_page()

                try:
                    page.goto(job.url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)

                    # 1. Check if already applied
                    already_applied = page.query_selector(".already-applied, span:has-text('Already Applied'), .applied-snippet")
                    if already_applied or "already applied" in page.inner_text("body").lower()[:600]:
                        console.print(f"[green][OK] Already applied on Naukri previously: {job.title} at {job.company}[/green]")
                        record = ApplicationRecord(
                            job_id=job.id,
                            platform="naukri",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.APPLIED_EASY,
                            notes="Already submitted previously on Naukri"
                        )
                        self.db.record_application(record)
                        context.close()
                        return True

                    # 2. Check for 1-Click Direct Apply button
                    direct_apply = page.query_selector("#apply-button, button.apply-button")
                    if direct_apply and direct_apply.is_visible():
                        console.print(f"[bold green]Found 1-Click Apply button! Submitting application to {job.company}...[/bold green]")
                        direct_apply.click()
                        page.wait_for_timeout(3500)

                        page_text = page.inner_text("body")
                        if any(w in page_text.lower() for w in ["applied", "successfully applied", "application submitted"]):
                            console.print(f"[bold green][OK] Successfully applied on Naukri: {job.title} at {job.company}[/bold green]")
                            record = ApplicationRecord(
                                job_id=job.id,
                                platform="naukri",
                                company=job.company,
                                role_title=job.title,
                                job_url=job.url,
                                status=ApplicationStatus.APPLIED_EASY,
                                notes="Successfully submitted via live Naukri 1-Click Apply"
                            )
                            self.db.record_application(record)
                            context.close()
                            return True
                        else:
                            console.print(f"[yellow]Clicked apply, awaiting confirmation: {job.company}[/yellow]")
                            record = ApplicationRecord(
                                job_id=job.id,
                                platform="naukri",
                                company=job.company,
                                role_title=job.title,
                                job_url=job.url,
                                status=ApplicationStatus.APPLIED_EASY,
                                notes="Clicked live apply button on Naukri"
                            )
                            self.db.record_application(record)
                            context.close()
                            return True

                    # 3. Check for external "Apply on company site"
                    company_site = page.query_selector("#company-site-button, button.company-site-button")
                    if company_site:
                        console.print(f"[cyan]-> External company website application: {job.company} (queued in dashboard)[/cyan]")
                        record = ApplicationRecord(
                            job_id=job.id,
                            platform="naukri",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.REQUIRES_MANUAL,
                            notes="External application - click link from dashboard to apply on company website"
                        )
                        self.db.record_application(record)
                        context.close()
                        return False

                    console.print(f"[dim]No standard apply button found on page for {job.company}[/dim]")
                    context.close()
                    return False

                except Exception as e:
                    console.print(f"[yellow]Error loading job page {job.url}: {e}[/yellow]")
                    context.close()
                    return False

        except Exception as e:
            console.print(f"[red]Error in Naukri live apply: {e}[/red]")
            return False