import os
import re
import time
import random
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Set
from playwright.sync_api import sync_playwright
from core.models import JobPosting, ApplicationStatus, ApplicationRecord
from .base import BaseScraper
from rich.console import Console

console = Console()

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SESSION_DIR = os.path.abspath("browser_sessions/linkedin")

# Explicitly reject these foreign locations using word boundaries
BLOCKED_LOCATION_PATTERNS = [
    r'\bindiana\b', r'\bindianapolis\b', r'\bunited states\b', r'\bunited kingdom\b',
    r'\bengland\b', r'\bportugal\b', r'\blisbon\b', r'\blondon\b',
    r'\bcanada\b', r'\baustralia\b', r'\bvirginia\b', r'\bsingapore\b', r'\bdubai\b', r'\buae\b',
    r',\s*in\s*$'  # matches US state abbreviation ', IN' at end of string
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
    for pattern in BLOCKED_LOCATION_PATTERNS:
        if re.search(pattern, loc_lower):
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

        # Type guard against YAML string parsing errors
        if isinstance(titles, str):
            titles = [t.strip("- \r") for t in titles.split("\n") if t.strip("- \r")]
        if isinstance(locations, str):
            locations = [l.strip("- \r") for l in locations.split("\n") if l.strip("- \r")]

        for title in titles:
            if len(found_jobs) >= limit:
                break
            for loc in locations:
                if len(found_jobs) >= limit:
                    break

                console.print(f"[dim]  Checking LinkedIn: '{title}' in '{loc}' (found {len(found_jobs)}/{limit})...[/dim]")

                params = {
                    "keywords": title,
                    "location": loc,
                    "f_TPR": f_tpr,
                    "start": 0
                }

                try:
                    url = f"{self.BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                    resp = requests.get(url, headers=headers, timeout=8)
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
                                    headers=headers, timeout=5
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

                    time.sleep(random.uniform(0.6, 1.2))

                except Exception as e:
                    console.print(f"[yellow]Warning querying LinkedIn for '{title}' in '{loc}': {e}[/yellow]")

        return found_jobs

    def _fill_easy_apply_step(self, modal, user_profile: Dict[str, Any]):
        """Intelligently detects form questions, labels, radios, and dropdowns, then autofills them."""
        personal = user_profile.get("personal_info", {})
        comp = user_profile.get("compensation_and_notice", {})
        answers = user_profile.get("answers_to_common_questions", {})

        # 1. Autofill Text / Number / Tel inputs
        text_inputs = modal.query_selector_all("input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='file']), textarea")
        for inp in text_inputs:
            try:
                curr_val = inp.input_value()
                if curr_val and len(curr_val.strip()) > 0:
                    continue  # Already pre-filled by LinkedIn

                inp_id = inp.get_attribute("id") or ""
                label_el = modal.query_selector(f"label[for='{inp_id}']") if inp_id else None
                label_text = label_el.inner_text().lower() if label_el else ""

                if not label_text:
                    # Look up closest label or parent container
                    parent_box = inp.evaluate_handle("el => el.closest('.fb-dash-form-element') || el.closest('div')")
                    if parent_box:
                        label_text = parent_box.inner_text().lower()[:120]

                # Match question type
                if any(w in label_text for w in ["phone", "mobile", "contact"]):
                    inp.fill(str(personal.get("phone", "6375807336")))
                elif "email" in label_text:
                    inp.fill(personal.get("email", "hiteshchoudhary2508@gmail.com"))
                elif any(w in label_text for w in ["experience", "years", "how many"]):
                    inp.fill(str(answers.get("years_of_experience", 1)))
                elif "current" in label_text and any(w in label_text for w in ["ctc", "salary", "pay"]):
                    inp.fill(str(comp.get("current_ctc_lpa", 4.25)))
                elif "expected" in label_text and any(w in label_text for w in ["ctc", "salary", "pay"]):
                    inp.fill(str(comp.get("expected_ctc_lpa", 6.0)))
                elif "notice" in label_text:
                    inp.fill(str(comp.get("notice_period_days", 30)))
                elif any(w in label_text for w in ["city", "location", "address"]):
                    inp.fill(personal.get("location", "Ahmedabad"))
                elif inp.get_attribute("type") == "number":
                    inp.fill("1")
                else:
                    ans = self.llm.answer_form_question(label_text, user_profile)
                    inp.fill(ans[:50])
            except Exception:
                pass

        # 2. Autofill Radio Button Groups (Yes/No Questions)
        fieldsets = modal.query_selector_all("fieldset, div[data-test-form-builder-radio-button-form-component]")
        for fs in fieldsets:
            try:
                legend = fs.query_selector("legend, label, span")
                legend_text = legend.inner_text().lower() if legend else ""

                # Target answer logic
                if any(w in legend_text for w in ["sponsorship", "visa required", "require sponsorship"]):
                    target_choice = "no"
                elif any(w in legend_text for w in ["authorized", "legally", "commute", "relocate", "comfortable", "experience", "background"]):
                    target_choice = "yes"
                else:
                    target_choice = "yes"

                radios = fs.query_selector_all("input[type='radio']")
                for r in radios:
                    r_id = r.get_attribute("id") or ""
                    r_lbl = fs.query_selector(f"label[for='{r_id}']")
                    r_val = (r.get_attribute("value") or "").lower()
                    full_lbl = (r_lbl.inner_text().lower() if r_lbl else "") + " " + r_val

                    if target_choice in full_lbl:
                        r.check(force=True)
                        break
            except Exception:
                pass

        # 3. Autofill Dropdowns (<select> and custom menus)
        selects = modal.query_selector_all("select")
        for sel in selects:
            try:
                selected_val = sel.evaluate("el => el.value")
                if not selected_val or selected_val == "":
                    sel_id = sel.get_attribute("id") or ""
                    lbl = modal.query_selector(f"label[for='{sel_id}']")
                    lbl_text = lbl.inner_text().lower() if lbl else ""

                    target = "no" if "sponsorship" in lbl_text else "yes"

                    options = sel.query_selector_all("option")
                    matched = False
                    for opt in options:
                        if target in opt.inner_text().lower() and opt.get_attribute("value"):
                            sel.select_option(value=opt.get_attribute("value"))
                            matched = True
                            break
                    if not matched and len(options) > 1:
                        # Select first non-empty option
                        first_opt = options[1].get_attribute("value")
                        if first_opt:
                            sel.select_option(value=first_opt)
            except Exception:
                pass

        # 4. Upload Resume if required and none selected
        file_input = modal.query_selector("input[type='file']")
        if file_input:
            try:
                resume_file = os.path.abspath("resumes/HiteshChoudhary_Resume.pdf")
                if os.path.exists(resume_file):
                    file_input.set_input_files(resume_file)
            except Exception:
                pass

    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        """Navigates to live LinkedIn job, clicks Easy Apply, autofills multi-step form questions, and submits."""
        console.print(f"[bold cyan]Navigating live LinkedIn job page:[/bold cyan] {job.title} at {job.company}")

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
                    page.wait_for_timeout(2500)

                    # Check for login redirection
                    if "login" in page.url or "authwall" in page.url:
                        console.print("[yellow]LinkedIn session requires login. Run: python main.py login --platform linkedin[/yellow]")
                        context.close()
                        return False

                    # 1. Check if already applied
                    applied_badge = page.query_selector(".artdeco-inline-feedback--success, .jobs-s-apply__applied-date")
                    if applied_badge or "applied" in page.inner_text("body").lower()[:500]:
                        console.print(f"[green][OK] Already applied on LinkedIn: {job.title} at {job.company}[/green]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id,
                            platform="linkedin",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.APPLIED_EASY,
                            notes="Already submitted previously on LinkedIn"
                        ))
                        context.close()
                        return True

                    # 2. Check for Easy Apply button
                    apply_btn = page.query_selector("button.jobs-apply-button, button:has-text('Easy Apply')")
                    if not apply_btn:
                        # Check external Apply button
                        ext_btn = page.query_selector("button:has-text('Apply')")
                        if ext_btn:
                            console.print(f"[cyan]-> External company website application: {job.company} (queued in dashboard)[/cyan]")
                            self.db.record_application(ApplicationRecord(
                                job_id=job.id,
                                platform="linkedin",
                                company=job.company,
                                role_title=job.title,
                                job_url=job.url,
                                status=ApplicationStatus.REQUIRES_MANUAL,
                                notes="External application - apply on company website"
                            ))
                        else:
                            console.print(f"[dim]No active apply button for {job.company}[/dim]")
                        context.close()
                        return False

                    btn_text = apply_btn.inner_text().lower()
                    if "easy apply" not in btn_text:
                        console.print(f"[cyan]-> External application: {job.company} (queued in dashboard)[/cyan]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id,
                            platform="linkedin",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.REQUIRES_MANUAL,
                            notes="External application - apply on company website"
                        ))
                        context.close()
                        return False

                    # 3. Open Easy Apply multi-step modal
                    console.print(f"[bold green]Found Easy Apply! Opening application modal for {job.company}...[/bold green]")
                    apply_btn.click()
                    page.wait_for_timeout(2500)

                    modal = page.query_selector("div[role='dialog'], .jobs-easy-apply-modal")
                    if not modal:
                        console.print(f"[yellow]Could not detect application modal for {job.company}[/yellow]")
                        context.close()
                        return False

                    max_steps = 8
                    submitted = False

                    for step in range(max_steps):
                        # Autofill inputs on current step
                        self._fill_easy_apply_step(modal, user_profile)
                        page.wait_for_timeout(1000)

                        # Check for Submit application button
                        submit_btn = modal.query_selector("button:has-text('Submit application'), button[aria-label='Submit application']")
                        if submit_btn and submit_btn.is_visible():
                            console.print(f"[bold green]Submitting Easy Apply application to {job.company}...[/bold green]")
                            submit_btn.click()
                            page.wait_for_timeout(3500)
                            submitted = True
                            break

                        # Check for Review button
                        review_btn = modal.query_selector("button:has-text('Review'), button[aria-label='Review your application']")
                        if review_btn and review_btn.is_visible():
                            review_btn.click()
                            page.wait_for_timeout(1500)
                            continue

                        # Check for Next step button
                        next_btn = modal.query_selector("button:has-text('Next'), button[aria-label='Continue to next step']")
                        if next_btn and next_btn.is_visible():
                            next_btn.click()
                            page.wait_for_timeout(1500)

                            # Check for validation errors
                            err = modal.query_selector(".artdeco-inline-feedback--error, [data-test-form-element-error-messages]")
                            if err and err.is_visible():
                                console.print(f"[yellow]Required question noticed: {err.inner_text().strip()[:60]}[/yellow]")
                                self._fill_easy_apply_step(modal, user_profile)
                                page.wait_for_timeout(500)
                                next_btn.click()
                                page.wait_for_timeout(1500)
                            continue
                        else:
                            break

                    if submitted:
                        done_btn = page.query_selector("button:has-text('Done'), button[aria-label='Dismiss']")
                        if done_btn and done_btn.is_visible():
                            done_btn.click()

                        console.print(f"[bold green][OK] Successfully submitted LinkedIn Easy Apply: {job.title} at {job.company}[/bold green]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id,
                            platform="linkedin",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.APPLIED_EASY,
                            notes="Successfully applied via LinkedIn Easy Apply autofill"
                        ))
                        context.close()
                        return True
                    else:
                        # Modal required complex steps, dismiss gracefully and queue
                        dismiss = modal.query_selector("button[aria-label='Dismiss']")
                        if dismiss:
                            dismiss.click()
                            page.wait_for_timeout(1000)
                            discard = page.query_selector("button[data-control-name='discard_application_confirm_btn']")
                            if discard: discard.click()

                        console.print(f"[yellow]-> Easy Apply required manual inputs: queued in dashboard for {job.company}[/yellow]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id,
                            platform="linkedin",
                            company=job.company,
                            role_title=job.title,
                            job_url=job.url,
                            status=ApplicationStatus.REQUIRES_MANUAL,
                            notes="Easy Apply form required manual review - click link from dashboard to finish"
                        ))
                        context.close()
                        return False

                except Exception as e:
                    console.print(f"[yellow]Error during LinkedIn live apply for {job.company}: {e}[/yellow]")
                    context.close()
                    return False

        except Exception as e:
            console.print(f"[red]Error in LinkedIn apply browser: {e}[/red]")
            return False