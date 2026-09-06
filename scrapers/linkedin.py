import re
import time
import random
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Set
from core.models import JobPosting, ApplicationStatus, ApplicationRecord
from .base import BaseScraper
from rich.console import Console

console = Console()

# Explicitly reject these — even if a job description mentions "India"
BLOCKED_LOCATION_KEYWORDS = [
    ", in", "indianapolis", "indiana", "united states", "united kingdom",
    "england", ", uk", " uk ", "portugal", "lisbon", "london", "canada",
    "australia", ", ca", "richmond, va", "virginia", "singapore", "dubai", "uae"
]

# Only accept jobs from these Indian locations
INDIA_KEYWORDS = [
    "india", "ahmedabad", "jaipur", "noida", "bangalore", "bengaluru",
    "mumbai", "pune", "hyderabad", "chennai", "delhi", "gurugram",
    "gurgaon", "kolkata", "gujarat", "rajasthan", "maharashtra",
    "karnataka", "telangana", "remote"
]

def is_india_location(location_text: str) -> bool:
    loc_lower = location_text.lower()
    # First explicitly reject known non-India locations
    if any(kw in loc_lower for kw in BLOCKED_LOCATION_KEYWORDS):
        return False
    return any(kw in loc_lower for kw in INDIA_KEYWORDS)

class LinkedInScraper(BaseScraper):
    BASE_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

    def search_jobs(self, criteria: Dict[str, Any], limit: int = 15) -> List[JobPosting]:
        search_params = criteria.get("search_parameters", {})
        titles = search_params.get("job_titles", ["Salesforce Developer"])
        locations = search_params.get("locations", ["Ahmedabad"])
        posted_within = search_params.get("posted_within_days", 7)

        found_jobs: List[JobPosting] = []
        seen_ids: Set[str] = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        tpr_map = {1: "r86400", 3: "r259200", 7: "r604800", 14: "r1209600", 30: "r2592000"}
        f_tpr = tpr_map.get(posted_within, "r604800")

        for title in titles:
            if len(found_jobs) >= limit:
                break
            for loc in locations:
                if len(found_jobs) >= limit:
                    break

                params = {
                    "keywords": title,
                    "location": loc,
                    "f_TPR": f_tpr,
                    "start": 0
                }

                try:
                    url = f"{self.BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                    resp = requests.get(url, headers=headers, timeout=12)
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.find_all("li")

                    for card in cards:
                        if len(found_jobs) >= limit:
                            break

                        title_elem = card.find("h3", class_=re.compile("base-search-card__title"))
                        company_elem = card.find("h4", class_=re.compile("base-search-card__subtitle"))
                        location_elem = card.find("span", class_=re.compile("job-search-card__location"))
                        link_elem = card.find("a", class_=re.compile("base-card__full-link"))
                        job_id_elem = card.find("div", {"data-entity-urn": True})

                        if not title_elem or not link_elem:
                            continue

                        job_url = link_elem.get("href", "").split("?")[0]
                        job_title = title_elem.get_text(strip=True)
                        company = company_elem.get_text(strip=True) if company_elem else "Unknown"
                        job_loc = location_elem.get_text(strip=True) if location_elem else loc

                        # Filter: India-only jobs
                        if not is_india_location(job_loc):
                            continue

                        match_id = re.search(r"view/([0-9]+)", job_url) or re.search(r"jobPosting:([0-9]+)", str(job_id_elem))
                        job_id = f"li_{match_id.group(1)}" if match_id else f"li_{abs(hash(job_url))}"

                        # Skip duplicates
                        if job_id in seen_ids or self.db.job_exists(job_id):
                            continue
                        seen_ids.add(job_id)

                        description = ""
                        emails = []
                        if match_id:
                            try:
                                d_resp = requests.get(
                                    self.DETAIL_URL.format(match_id.group(1)),
                                    headers=headers, timeout=8
                                )
                                if d_resp.status_code == 200:
                                    d_soup = BeautifulSoup(d_resp.text, "html.parser")
                                    desc_div = d_soup.find("div", class_=re.compile("show-more-less-html__markup"))
                                    if desc_div:
                                        description = desc_div.get_text(separator=" ", strip=True)
                                        emails = self.email_extractor.extract_emails(description)
                            except Exception:
                                pass

                        job_posting = JobPosting(
                            id=job_id,
                            platform="linkedin",
                            title=job_title,
                            company=company,
                            location=job_loc,
                            url=job_url,
                            description=description,
                            easy_apply=True,
                            recruiter_emails=emails
                        )
                        found_jobs.append(job_posting)

                    time.sleep(random.uniform(0.8, 1.5))

                except Exception as e:
                    console.print(f"[yellow]Warning querying LinkedIn for '{title}' in '{loc}': {e}[/yellow]")

        return found_jobs

    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        console.print(f"[bold cyan]Processing Application for LinkedIn:[/bold cyan] {job.title} at {job.company}")
        record = ApplicationRecord(
            job_id=job.id,
            platform="linkedin",
            company=job.company,
            role_title=job.title,
            job_url=job.url,
            status=ApplicationStatus.APPLIED_EASY,
            notes="Submitted via LinkedIn automated runner"
        )
        self.db.record_application(record)
        return True