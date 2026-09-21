import os
import re
import time
import random
import subprocess
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
CHROME_DEBUG_PORT = 9222

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
                    "f_AL": "true",  # Prioritize Easy Apply jobs
                    "start": 0
                }

                try:
                    url = f"{self.BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                    resp = requests.get(url, headers=headers, timeout=8)
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.find_all("li")

                    # If not enough Easy Apply cards, fallback to general search for this title/loc
                    if len(cards) < 3:
                        del params["f_AL"]
                        fallback_url = f"{self.BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                        f_resp = requests.get(fallback_url, headers=headers, timeout=8)
                        if f_resp.status_code == 200:
                            f_soup = BeautifulSoup(f_resp.text, "html.parser")
                            cards.extend(f_soup.find_all("li"))

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

    def _fill_easy_apply_step(self, modal, user_profile: Dict[str, Any], page=None):
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
                elif any(w in label_text for w in ["ctc", "salary", "compensation", "package", "pay"]):
                    is_expected = any(w in label_text for w in ["expected", "desired", "target", "look"])
                    min_attr = inp.get_attribute("min") or ""
                    step_attr = inp.get_attribute("step") or ""
                    is_hundreds = any(w in label_text for w in ["100", "hundred", "hundreds"])
                    is_thousands = any(w in label_text for w in ["thousand", "thousands", "'000", "in k"])
                    is_lakhs = any(w in label_text for w in ["lpa", "lakh", "lakhs", "lac", "lacs"])
                    is_full_inr = (
                        any(w in label_text for w in ["inr", "rupee", "rupees", "per annum", "annual", "full", "whole", "exact", "0000"]) or
                        (min_attr.isdigit() and int(min_attr) >= 10000) or
                        (step_attr == "1" and not is_lakhs)
                    )

                    if is_expected:
                        if is_hundreds:
                            val = str(comp.get("expected_ctc_hundreds", 6000))
                        elif is_thousands:
                            val = "600"
                        elif is_full_inr:
                            val = str(comp.get("expected_ctc_inr", 600000))
                        else:
                            val = str(comp.get("expected_ctc_lpa", 6.0))
                    else:
                        if is_hundreds:
                            val = str(comp.get("current_ctc_hundreds", 4250))
                        elif is_thousands:
                            val = "425"
                        elif is_full_inr:
                            val = str(comp.get("current_ctc_inr", 425000))
                        else:
                            val = str(comp.get("current_ctc_lpa", 4.25))
                    inp.fill(val)
                elif "notice" in label_text:
                    inp.fill(str(comp.get("notice_period_days", 30)))
                elif any(w in label_text for w in ["city", "location", "address"]):
                    target_loc = personal.get("location", "Noida, Uttar Pradesh, India")
                    try:
                        inp.click()
                        inp.fill("")
                        # Type city sequentially to trigger LinkedIn typeahead suggestions
                        inp.press_sequentially("Noida", delay=90)
                        if page:
                            page.wait_for_timeout(600)
                            suggestion = page.query_selector(
                                ".basic-typeahead__selectable-result, "
                                "[role='listbox'] [role='option'], "
                                ".artdeco-typeahead__result, "
                                "div.typeahead-result, "
                                ".artdeco-typeahead__results li"
                            )
                            if suggestion and suggestion.is_visible():
                                suggestion.click()
                            else:
                                inp.press("ArrowDown")
                                page.wait_for_timeout(200)
                                inp.press("Enter")
                    except Exception:
                        pass
                    # Ensure value is filled if typeahead was bypassed
                    try:
                        if not inp.input_value() or len(inp.input_value().strip()) < 3:
                            inp.fill(target_loc)
                    except Exception:
                        pass
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

    def _get_playwright_context(self, p):
        """Launch Chrome with saved session. Returns (owner, context, page)."""
        ctx = p.chromium.launch_persistent_context(
            SESSION_DIR,
            headless=False,
            executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        return ctx, ctx, pg

    def _find_apply_button(self, page):
        """Find the Easy Apply or Apply button/link using multiple strategies.
        Returns (element_handle, is_easy_apply, direct_apply_url) or (None, False, None)."""
        # Strategy 1: Evaluate DOM for a, button, [role='button']
        try:
            res = page.evaluate("""() => {
                const elements = Array.from(document.querySelectorAll("a, button, [role='button']"));
                // 1. Search for Easy Apply first
                for (const el of elements) {
                    const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const href = el.getAttribute('href') || '';
                    if (text.includes('easy apply') || aria.includes('easy apply') || href.includes('openSDUIApplyFlow') || href.includes('/apply/')) {
                        return { found: true, is_easy: true, href: href || null, text: text || aria };
                    }
                }
                // 2. Search for External Apply
                for (const el of elements) {
                    const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const cls = (el.className || '').toString().toLowerCase();
                    if ((text === 'apply' || text.startsWith('apply ') || aria.includes('apply') || cls.includes('jobs-apply-button')) && !text.includes('easy')) {
                        return { found: true, is_easy: false, href: el.getAttribute('href') || null, text: text || aria };
                    }
                }
                return { found: false };
            }""")
            if res and res.get("found"):
                is_easy = res.get("is_easy", False)
                href = res.get("href")
                btn_text = res.get("text", "")
                console.print(f"[dim]  Found apply element ({'Easy Apply' if is_easy else 'External'}): '{btn_text}'[/dim]")
                if is_easy:
                    handle = page.evaluate_handle("""() => {
                        const elements = Array.from(document.querySelectorAll("a, button, [role='button']"));
                        return elements.find(el => {
                            const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                            const h = el.getAttribute('href') || '';
                            return text.includes('easy apply') || aria.includes('easy apply') || h.includes('openSDUIApplyFlow') || h.includes('/apply/');
                        }) || null;
                    }""")
                else:
                    handle = page.evaluate_handle("""() => {
                        const elements = Array.from(document.querySelectorAll("a, button, [role='button']"));
                        return elements.find(el => {
                            const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                            const cls = (el.className || '').toString().toLowerCase();
                            return (text === 'apply' || text.startsWith('apply ') || aria.includes('apply') || cls.includes('jobs-apply-button')) && !text.includes('easy');
                        }) || null;
                    }""")
                elem = handle.as_element()
                return elem, is_easy, href
        except Exception as e:
            console.print(f"[dim]  _find_apply_button note: {e}[/dim]")

        # Strategy 2: Fallback Playwright locators
        for sel in ["a:has-text('Easy Apply')", "button:has-text('Easy Apply')", "[aria-label*='Easy Apply']"]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return loc.element_handle(), True, loc.get_attribute("href")
            except Exception:
                continue

        for sel in ["a:has-text('Apply')", "button:has-text('Apply')", ".jobs-apply-button"]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return loc.element_handle(), False, loc.get_attribute("href")
            except Exception:
                continue

        return None, False, None

    def _click_modal_button(self, page, modal, texts: list) -> bool:
        """Click the first visible button in modal matching any of the given texts."""
        for text in texts:
            try:
                loc = modal.locator(f"button:has-text('{text}')").first
                if loc.is_visible():
                    loc.click()
                    return True
            except Exception:
                pass
            try:
                btn = modal.query_selector(f"button[aria-label*='{text}']")
                if btn and btn.is_visible():
                    btn.click()
                    return True
            except Exception:
                pass
        return False

    def apply_job(self, job: JobPosting, user_profile: Dict[str, Any]) -> bool:
        """Navigate to LinkedIn job page, click Easy Apply, autofill form, and submit."""
        console.print(f"[bold cyan]Navigating live LinkedIn job page:[/bold cyan] {job.title} at {job.company}")

        try:
            with sync_playwright() as p:
                owner, context, page = self._get_playwright_context(p)

                try:
                    page.goto(job.url, wait_until="domcontentloaded", timeout=30000)

                    # Wait for network to settle (LinkedIn is a heavy SPA)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        page.wait_for_timeout(3000)

                    current_url = page.url
                    if "login" in current_url or "authwall" in current_url or "uas/login" in current_url:
                        console.print("[yellow]LinkedIn session requires login. Run: python main.py login --platform linkedin[/yellow]")
                        owner.close()
                        return False

                    # 1. Already applied check
                    applied_badge = page.query_selector(
                        ".jobs-s-apply__application-link, "
                        ".artdeco-inline-feedback--success, "
                        ".jobs-s-apply__applied-date, "
                        "[data-test-job-apply-status='applied']"
                    )
                    if applied_badge:
                        console.print(f"[green][OK] Already applied on LinkedIn: {job.title} at {job.company}[/green]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id, platform="linkedin", company=job.company,
                            role_title=job.title, job_url=job.url,
                            status=ApplicationStatus.APPLIED_EASY,
                            notes="Already submitted previously on LinkedIn"
                        ))
                        owner.close()
                        return True

                    # 2. Find apply button / link
                    apply_btn, is_easy_apply, direct_apply_url = self._find_apply_button(page)

                    if not apply_btn and not direct_apply_url:
                        console.print(f"[bold red]No apply button found for {job.company}[/bold red]")
                        owner.close()
                        return False

                    if not is_easy_apply:
                        console.print(f"[cyan]-> External company website application: {job.company} (queued in dashboard)[/cyan]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id, platform="linkedin", company=job.company,
                            role_title=job.title, job_url=job.url,
                            status=ApplicationStatus.REQUIRES_MANUAL,
                            notes="External application - apply on company website"
                        ))
                        owner.close()
                        return False

                    # 3. Open Easy Apply flow
                    console.print(f"[bold green]Found Easy Apply! Launching application flow for {job.company}...[/bold green]")
                    if direct_apply_url and direct_apply_url.startswith("http"):
                        page.goto(direct_apply_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(3000)
                    elif apply_btn:
                        try:
                            apply_btn.scroll_into_view_if_needed()
                            apply_btn.click()
                        except Exception:
                            page.evaluate("arguments => arguments[0].click()", [apply_btn])
                        page.wait_for_timeout(3000)

                    # Wait for modal
                    modal = None
                    for modal_sel in ["div[role='dialog']", ".jobs-easy-apply-modal", ".artdeco-modal"]:
                        try:
                            page.wait_for_selector(modal_sel, timeout=5000, state="visible")
                            modal = page.query_selector(modal_sel)
                            if modal:
                                break
                        except Exception:
                            continue

                    if not modal:
                        console.print(f"[yellow]Modal did not open for {job.company}[/yellow]")
                        owner.close()
                        return False

                    max_steps = 10
                    submitted = False

                    for step in range(max_steps):
                        page.wait_for_timeout(800)
                        self._fill_easy_apply_step(modal, user_profile, page=page)
                        page.wait_for_timeout(600)

                        # Submit?
                        if self._click_modal_button(page, modal, ["Submit application", "Submit"]):
                            console.print(f"[bold green]Submitting Easy Apply for {job.company}...[/bold green]")
                            page.wait_for_timeout(4000)
                            submitted = True
                            break

                        # Review?
                        if self._click_modal_button(page, modal, ["Review", "Review your application"]):
                            page.wait_for_timeout(1500)
                            continue

                        # Next?
                        if self._click_modal_button(page, modal, ["Next", "Continue to next step"]):
                            page.wait_for_timeout(1500)
                            # Check for validation errors and retry fill
                            err = modal.query_selector(".artdeco-inline-feedback--error, [data-test-form-element-error-messages]")
                            if err:
                                try:
                                    if err.is_visible():
                                        console.print(f"[yellow]  Validation notice: {err.inner_text().strip()[:80]}[/yellow]")
                                        self._fill_easy_apply_step(modal, user_profile, page=page)
                                        page.wait_for_timeout(400)
                                        self._click_modal_button(page, modal, ["Next", "Continue to next step"])
                                        page.wait_for_timeout(1500)
                                except Exception:
                                    pass
                            continue

                        # No recognizable button — modal may be finished or stuck
                        break

                    if submitted:
                        self._click_modal_button(page, page, ["Done", "Dismiss"])
                        console.print(f"[bold green][OK] LinkedIn Easy Apply submitted: {job.title} at {job.company}[/bold green]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id, platform="linkedin", company=job.company,
                            role_title=job.title, job_url=job.url,
                            status=ApplicationStatus.APPLIED_EASY,
                            notes="Successfully applied via LinkedIn Easy Apply autofill"
                        ))
                        owner.close()
                        return True
                    else:
                        # Dismiss modal gracefully
                        try:
                            dismiss = modal.query_selector("button[aria-label='Dismiss']")
                            if dismiss:
                                dismiss.click()
                                page.wait_for_timeout(1000)
                                discard = page.query_selector("button:has-text('Discard')")
                                if discard:
                                    discard.click()
                        except Exception:
                            pass

                        console.print(f"[yellow]-> Easy Apply needed manual input: queued for {job.company}[/yellow]")
                        self.db.record_application(ApplicationRecord(
                            job_id=job.id, platform="linkedin", company=job.company,
                            role_title=job.title, job_url=job.url,
                            status=ApplicationStatus.REQUIRES_MANUAL,
                            notes="Easy Apply required manual review - open job URL from dashboard to finish"
                        ))
                        owner.close()
                        return False

                except Exception as e:
                    console.print(f"[yellow]Error during LinkedIn apply for {job.company}: {e}[/yellow]")
                    try:
                        owner.close()
                    except Exception:
                        pass
                    return False

        except Exception as e:
            console.print(f"[red]Error in LinkedIn apply browser: {e}[/red]")
            return False

