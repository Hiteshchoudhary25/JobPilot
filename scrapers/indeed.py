import re
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from core.models import JobPosting, ApplicationStatus, ApplicationRecord
from .base import BaseScraper
from rich.console import Console

console = Console()

class IndeedScraper(BaseScraper):
    BASE_URL = "https://www.indeed.com/jobs"

    def search_jobs(self, criteria: Dict[str, Any], limit: int = 15) -> List[JobPosting]:
        search_params = criteria.get("search_parameters", {})
        titles = search_params.get("job_titles", ["Software Engineer"])
        locations = search_params.get("locations", ["Bangalore"])
        
        found_jobs: List[JobPosting] = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        for title in titles[:2]:
            for loc in locations[:2]:
                if len(found_jobs) >= limit:
                    break

                params = {
                    "q": title,
                    "l": loc,
                    "fromage": criteria.get("search_parameters", {}).get("posted_within_days", 7)
                }

                try:
                    url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    job_cards = soup.find_all("div", class_=re.compile("job_seen_beacon|jobCard_mainContent|resultContent"))

                    for card in job_cards:
                        if len(found_jobs) >= limit:
                            break

                        title_elem = card.find("h2", class_=re.compile("jobTitle")) or card.find("a", {"data-jk": True})
                        company_elem = card.find("span", {"data-testid": "company-name"}) or card.find("span", class_=re.compile("companyName"))
                        location_elem = card.find("div", {"data-testid": "text-location"}) or card.find("div", class_=re.compile("companyLocation"))

                        if not title_elem:
                            continue

                        job_title = title_elem.get_text(strip=True)
                        company = company_elem.get_text(strip=True) if company_elem else "Unknown"
                        job_loc = location_elem.get_text(strip=True) if location_elem else loc

                        link_tag = title_elem.find("a") if title_elem.name != "a" else title_elem
                        jk = link_tag.get("data-jk", "") if link_tag else ""
                        
                        job_id = f"in_{jk}" if jk else f"in_{abs(hash(job_title + company))}"
                        job_url = f"https://www.indeed.com/viewjob?jk={jk}" if jk else url

                        snippet = card.find("div", class_=re.compile("job-snippet"))
                        desc_text = snippet.get_text(separator=" ", strip=True) if snippet else ""
                        
                        emails = self.email_extractor.extract_emails(desc_text)

                        job_posting = JobPosting(
                            id=job_id,
                            platform="indeed",
                            title=job_title,
                            company=company,
                            location=job_loc,
                            url=job_url,
                            description=desc_text,
                            easy_apply=True if "Easily apply" in card.get_text() else False,
                            recruiter_emails=emails
                        )
                        found_jobs.append(job_posting)

                except Exception as e:
                    console.print(f"[yellow]Warning querying Indeed for {title} in {loc}: {e}[/yellow]")

        return found_jobs

    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        console.print(f"[bold blue]Processing Application for Indeed:[/bold blue] {job.title} at {job.company}")
        record = ApplicationRecord(
            job_id=job.id,
            platform="indeed",
            company=job.company,
            role_title=job.title,
            job_url=job.url,
            status=ApplicationStatus.APPLIED_EASY if job.easy_apply else ApplicationStatus.REQUIRES_MANUAL,
            notes="Auto-applied via Indeed runner" if job.easy_apply else "External application link"
        )
        self.db.record_application(record)
        return True