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
                curr_val = (inp.input_value() or "").strip()
                inp_id = inp.get_attribute("id") or ""
                label_el = modal.query_selector(f"label[for='{inp_id}']") if inp_id else None
                label_text = label_el.inner_text().lower() if label_el else ""

                if not label_text:
                    # Look up closest label or parent container
                    parent_box = inp.evaluate_handle("el => el.closest('.fb-dash-form-element') || el.closest('.artdeco-form-element') || el.closest('div')")
                    if parent_box:
                        label_text = parent_box.inner_text().lower()[:150]

                placeholder = (inp.get_attribute("placeholder") or "").lower()
                aria_label = (inp.get_attribute("aria-label") or "").lower()
                combined_label = f"{label_text} {placeholder} {aria_label}"

                # Has validation error?
                has_error = False
                try:
                    has_error = inp.evaluate("el => !!el.closest('.artdeco-inline-feedback--error, .fb-dash-form-element--error, [data-test-form-element-error-messages]') || el.getAttribute('aria-invalid') === 'true'")
                except Exception:
                    pass

                # 1. Location / City / Address handling
                if any(w in combined_label for w in ["city", "location", "address"]):
                    target_loc = personal.get("location", "Noida, Uttar Pradesh, India")
                    # If already filled with full target location and no validation error, skip
                    if curr_val.lower() == target_loc.lower() and not has_error:
                        continue

                    try:
                        inp.click()
                        inp.fill("")
                        if page:
                            page.wait_for_timeout(200)
                        # Type 'Noida' to trigger LinkedIn's typeahead API
                        inp.press_sequentially("Noida", delay=100)
                        if page:
                            page.wait_for_timeout(800)
                            # Locate suggestion matching Noida, Uttar Pradesh, India
                            selected = page.evaluate("""() => {
                                const items = Array.from(document.querySelectorAll(
                                    ".basic-typeahead__selectable-result, [role='listbox'] [role='option'], .artdeco-typeahead__result, div.typeahead-result, .artdeco-typeahead__results li, div[role='option'], li[role='option']"
                                ));
                                const match = items.find(el => {
                                    const t = (el.textContent || '').toLowerCase();
                                    return t.includes('noida, uttar pradesh') || t.includes('noida, up');
                                }) || items.find(el => (el.textContent || '').toLowerCase().includes('noida'))
                                   || items[0];
                                if (match) {
                                    match.click();
                                    return true;
                                }
                                return false;
                            }""")
                            if not selected:
                                inp.press("ArrowDown")
                                page.wait_for_timeout(200)
                                inp.press("Enter")
                            page.wait_for_timeout(400)
                    except Exception:
                        pass

                    # Ensure input has the target location text if typeahead didn't close
                    try:
                        val_now = (inp.input_value() or "").strip()
                        if not val_now or "uttar pradesh" not in val_now.lower():
                            inp.fill(target_loc)
                            inp.dispatch_event("input")
                            inp.dispatch_event("change")
                            inp.dispatch_event("blur")
                    except Exception:
                        pass
                    continue

                # 2. Phone / Mobile
                if any(w in combined_label for w in ["phone", "mobile", "contact"]):
                    if curr_val and len(curr_val) >= 10 and not has_error:
                        continue
                    inp.fill(str(personal.get("phone", "6375807336")))
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    continue

                # 3. Email
                if "email" in combined_label:
                    if curr_val and "@" in curr_val and not has_error:
                        continue
                    inp.fill(personal.get("email", "hiteshchoudhary2508@gmail.com"))
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    continue

                # 4. CTC / Compensation / Salary
                if any(w in combined_label for w in ["ctc", "salary", "compensation", "package", "pay"]):
                    if curr_val and not has_error:
                        continue
                    is_expected = any(w in combined_label for w in ["expected", "desired", "target", "look"])
                    min_attr = inp.get_attribute("min") or ""
                    step_attr = inp.get_attribute("step") or ""
                    is_hundreds = any(w in combined_label for w in ["100", "hundred", "hundreds", "/ 100", "/100", "in 100", "in hundreds"])
                    is_thousands = any(w in combined_label for w in ["thousand", "thousands", "'000", "in k", "in 'k", "/ 1000"])
                    is_lakhs = any(w in combined_label for w in ["lpa", "lakh", "lakhs", "lac", "lacs", "in lakhs", "in lpa"])
                    is_full_inr = (
                        any(w in combined_label for w in ["inr", "rupee", "rupees", "per annum", "annual", "full", "whole", "exact", "0000"]) or
                        (min_attr.isdigit() and int(min_attr) >= 10000) or
                        (step_attr == "1" and not is_lakhs and not is_hundreds)
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
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    continue

                # 5. Notice Period
                if any(w in combined_label for w in ["notice", "serving notice"]):
                    if curr_val and not has_error:
                        continue
                    inp.fill(str(comp.get("notice_period_days", 30)))
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    continue

                # 6. Experience / Years
                if any(w in combined_label for w in ["experience", "years", "how many"]):
                    if curr_val and not has_error:
                        continue
                    inp.fill(str(answers.get("years_of_experience", 1)))
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    continue

                # For other fields: if already filled and valid, leave it
                if curr_val and not has_error:
                    continue

                # Generic number, text, or textarea questions answered via AI model
                inp_type = inp.get_attribute("type") or "text"
                tag_name = inp.evaluate("el => el.tagName.toLowerCase()")
                max_len = None
                max_attr = inp.get_attribute("maxlength")
                if max_attr and max_attr.isdigit():
                    max_len = int(max_attr)

                ans = self.llm.answer_form_question(
                    combined_label,
                    user_profile,
                    input_type="number" if inp_type == "number" else "text",
                    max_length=max_len or (300 if tag_name == "textarea" else 100)
                )
                if ans:
                    inp.fill(ans)
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
            except Exception:
                pass

        # 2. Autofill Radio Button Groups via AI option selection
        radios = modal.query_selector_all("input[type='radio']")
        radio_groups = {}
        for r in radios:
            r_name = r.get_attribute("name") or r.get_attribute("aria-label") or r.evaluate("""el => {
                const p = el.closest('fieldset, [role="radiogroup"], .fb-dash-form-element, [data-test-form-element]');
                return p ? (p.id || p.innerText.slice(0, 40)) : Math.random().toString();
            }""")
            if r_name not in radio_groups:
                radio_groups[r_name] = []
            radio_groups[r_name].append(r)

        for group_name, group_radios in radio_groups.items():
            try:
                # Check if already answered
                already_checked = any(r.is_checked() for r in group_radios)
                # Check if group has a validation notice
                first_r = group_radios[0]
                has_error = False
                try:
                    has_error = first_r.evaluate("""el => {
                        const fs = el.closest('fieldset, [role=\"radiogroup\"], div');
                        if (!fs) return false;
                        const err = fs.querySelector('.artdeco-inline-feedback--error, [data-test-form-element-error-messages], p[class*=\"error\"], span[class*=\"error\"]');
                        return !!(err && err.innerText.trim().length > 0);
                    }""")
                except Exception:
                    pass

                if already_checked and not has_error:
                    continue

                # 1. Extract Question text (in modern LinkedIn, question is in aria-label of the input)
                q_text = first_r.get_attribute("aria-label") or ""
                if not q_text:
                    q_text = first_r.evaluate("""el => {
                        const fs = el.closest('fieldset, [role=\"radiogroup\"], div');
                        if (!fs) return '';
                        const legend = fs.querySelector('legend, h3, h4, span.fb-dash-form-element__label, p');
                        if (legend && legend.innerText) return legend.innerText.trim();
                        const prev = fs.previousElementSibling;
                        if (prev && prev.innerText) return prev.innerText.trim();
                        return '';
                    }""")

                # 2. Extract option text for each radio button in this group
                opt_map = []
                for r in group_radios:
                    opt_text = r.evaluate("""el => {
                        let p = el.parentElement ? el.parentElement.nextElementSibling : null;
                        if (p && p.innerText) return p.innerText.trim();
                        let parent = el.closest('div.i0lmvo, .i0ligt') || el.parentElement.parentElement;
                        if (parent) {
                            let textEl = parent.querySelector('p, span:not([class*=\"icon\"])');
                            if (textEl && textEl.innerText) return textEl.innerText.trim();
                            return parent.innerText.trim();
                        }
                        const r_id = el.id;
                        const lbl = r_id ? document.querySelector(`label[for='${r_id}']`) : null;
                        if (lbl && lbl.innerText) return lbl.innerText.trim();
                        return el.value || '';
                    }""")
                    if opt_text:
                        opt_map.append((r, opt_text))

                if opt_map:
                    opt_labels = [o[1] for o in opt_map]
                    chosen_label = self.llm.choose_best_option(q_text, opt_labels, user_profile)
                    matched_r = None
                    for r_elem, txt in opt_map:
                        if txt.lower() == chosen_label.lower() or chosen_label.lower() in txt.lower():
                            matched_r = r_elem
                            break
                    if not matched_r:
                        matched_r = opt_map[0][0]

                    try:
                        matched_r.scroll_into_view_if_needed()
                        matched_r.check(force=True)
                        matched_r.dispatch_event("input")
                        matched_r.dispatch_event("change")
                    except Exception:
                        pass
                    # Click container/label to trigger React state updates
                    try:
                        matched_r.evaluate("""el => {
                            let parent = el.closest('div.i0lmvo, .i0ligt') || el.parentElement;
                            if (parent) parent.click();
                        }""")
                    except Exception:
                        pass
            except Exception:
                pass

        # 3. Autofill Native Dropdowns (<select>) via AI option selection
        selects = modal.query_selector_all("select")
        for sel in selects:
            try:
                selected_val = sel.evaluate("el => el.value")
                if not selected_val or selected_val == "" or selected_val == "Select an option":
                    sel_id = sel.get_attribute("id") or ""
                    lbl = modal.query_selector(f"label[for='{sel_id}']")
                    lbl_text = (lbl.inner_text().strip() if lbl else "")
                    if not lbl_text:
                        parent_box = sel.evaluate_handle("el => el.closest('.fb-dash-form-element') || el.closest('div')")
                        lbl_text = parent_box.inner_text().strip()[:120] if parent_box else ""

                    options = sel.query_selector_all("option")
                    opt_data = []
                    for opt in options:
                        val = opt.get_attribute("value") or ""
                        txt = (opt.inner_text() or "").strip()
                        if txt and txt.lower() not in ["select", "select an option", "choose", "please select", "--"]:
                            opt_data.append((val, txt))

                    if opt_data:
                        opt_texts = [o[1] for o in opt_data]
                        best_txt = self.llm.choose_best_option(lbl_text, opt_texts, user_profile)
                        for val, txt in opt_data:
                            if txt.lower() == best_txt.lower() or best_txt.lower() in txt.lower():
                                sel.select_option(value=val)
                                sel.dispatch_event("change")
                                break
            except Exception:
                pass

        # 4. Autofill Custom Dropdowns / Comboboxes via AI option selection
        custom_dropdowns = modal.query_selector_all("div[data-test-form-builder-dropdown-form-component], div.fb-dash-form-element div[role='combobox']")
        for cdd in custom_dropdowns:
            try:
                selected_item = cdd.query_selector(".artdeco-dropdown__item[aria-selected='true'], [aria-selected='true']")
                if selected_item:
                    continue

                trigger = cdd.query_selector("button, [role='combobox']")
                if trigger and trigger.is_visible():
                    lbl = cdd.query_selector("label, span.fb-dash-form-element__label")
                    lbl_text = (lbl.inner_text().strip() if lbl else "")
                    trigger.click()
                    if page:
                        page.wait_for_timeout(300)
                        opts = page.query_selector_all("[role='listbox'] [role='option'], .artdeco-dropdown__item")
                        opt_map = []
                        for o in opts:
                            txt = (o.inner_text() or "").strip()
                            if txt:
                                opt_map.append((o, txt))
                        if opt_map:
                            best_choice = self.llm.choose_best_option(lbl_text, [o[1] for o in opt_map], user_profile)
                            for o_el, txt in opt_map:
                                if txt.lower() == best_choice.lower() or best_choice.lower() in txt.lower():
                                    o_el.click()
                                    break
            except Exception:
                pass

        # 5. Autofill Checkboxes (terms, agreements, multi-select skill checkboxes)
        checkboxes = modal.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            try:
                cb_info = cb.evaluate("""el => {
                    const cb_id = el.id;
                    const lbl = cb_id ? document.querySelector(`label[for='${cb_id}']`) : el.closest('label');
                    const lblText = lbl ? lbl.innerText.trim() : '';
                    const parent = el.closest('fieldset, div') || el.parentElement;
                    const pText = parent ? parent.innerText.trim() : '';
                    const aria = el.getAttribute('aria-label') || '';
                    const required = el.required || el.getAttribute('aria-required') === 'true';
                    return {
                        checked: el.checked,
                        labelText: lblText,
                        contextText: (lblText + ' ' + pText + ' ' + aria).toLowerCase(),
                        required: required
                    };
                }""")

                if cb_info.get("checked"):
                    continue

                full_cb_text = cb_info.get("contextText", "")
                is_required = cb_info.get("required", False)

                # Agreement / acknowledgment / consent
                is_agreement = any(w in full_cb_text for w in [
                    "agree", "acknowledge", "consent", "certify", "confirm", "terms", "understand",
                    "authorized", "legally", "privacy", "policy"
                ])

                # Multi-select skill checkboxes matching candidate profile
                user_skills = [s.lower() for s in user_profile.get("skills", {}).get("primary", [])]
                user_skills += [s.lower() for s in user_profile.get("skills", {}).get("secondary", [])]
                is_skill_match = any(s in full_cb_text for s in user_skills)

                # Avoid "follow company" optional checkbox if not desired
                is_follow = "follow" in full_cb_text and "company" in full_cb_text

                if (is_required or is_agreement or is_skill_match) and not is_follow:
                    try:
                        cb.scroll_into_view_if_needed()
                        cb.check(force=True)
                        cb.dispatch_event("change")
                        cb.dispatch_event("input")
                    except Exception:
                        cb.evaluate("el => el.click()")
            except Exception:
                pass

        # 5. Upload Resume if required and none selected
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
        """Find the Easy Apply or Apply button/link using scoped top-card strategies.
        Returns (element_handle, is_easy_apply, direct_apply_url) or (None, False, None)."""
        try:
            res = page.evaluate("""() => {
                const containers = [
                    document.querySelector('.jobs-apply-button--top-card'),
                    document.querySelector('.jobs-unified-top-card'),
                    document.querySelector("[data-view-name='job-details']"),
                    document.querySelector('.job-details-jobs-unified-top-card__container--two-pane'),
                    document.querySelector('.jobs-details__main-content'),
                    document.querySelector('.jobs-search__job-details--container'),
                    document
                ];

                for (const root of containers) {
                    if (!root) continue;
                    const elements = Array.from(root.querySelectorAll("button, a, [role='button']"));

                    // 1. Easy Apply search
                    for (const el of elements) {
                        if (!el.offsetParent && el.offsetWidth === 0 && el.offsetHeight === 0) continue;
                        const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const href = el.getAttribute('href') || '';
                        const cls = (el.className || '').toString().toLowerCase();

                        // Exclude similar jobs, search result links, and recommendation cards
                        if (href.includes('search-results') || href.includes('/jobs/search') || href.includes('origin=JobSearchOrigin')) {
                            continue;
                        }
                        if (text.includes('promoted') || text.includes('repost') || text.includes('applicant') || text.includes('viewed') || text.includes('similar')) {
                            continue;
                        }

                        const isEasy = (
                            text === 'easy apply' ||
                            text.startsWith('easy apply') ||
                            aria.includes('easy apply') ||
                            (cls.includes('jobs-apply-button') && (text.includes('easy') || aria.includes('easy')))
                        );
                        if (isEasy && text.length < 35) {
                            return { found: true, is_easy: true, text: text || aria };
                        }
                    }

                    // 2. External Apply search
                    for (const el of elements) {
                        if (!el.offsetParent && el.offsetWidth === 0 && el.offsetHeight === 0) continue;
                        const text = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const href = el.getAttribute('href') || '';
                        const cls = (el.className || '').toString().toLowerCase();

                        if (href.includes('search-results') || href.includes('/jobs/search') || href.includes('origin=JobSearchOrigin')) {
                            continue;
                        }
                        if (text.includes('promoted') || text.includes('repost') || text.includes('similar')) {
                            continue;
                        }

                        const isApply = (
                            text === 'apply' ||
                            text.startsWith('apply on') ||
                            text.startsWith('apply to') ||
                            aria.startsWith('apply to') ||
                            (cls.includes('jobs-apply-button') && !text.includes('easy'))
                        );
                        if (isApply && text.length < 40) {
                            return { found: true, is_easy: false, text: text || aria, href: href || null };
                        }
                    }
                }
                return { found: false };
            }""")

            if res and res.get("found"):
                is_easy = res.get("is_easy", False)
                btn_text = res.get("text", "")
                console.print(f"[dim]  Found apply element ({'Easy Apply' if is_easy else 'External'}): '{btn_text}'[/dim]")

                if is_easy:
                    for sel in [
                        ".jobs-apply-button--top-card button",
                        ".jobs-unified-top-card button.jobs-apply-button",
                        "[data-view-name='job-details'] button:has-text('Easy Apply')",
                        "button:has-text('Easy Apply')",
                        "[aria-label*='Easy Apply to']"
                    ]:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible():
                                return loc.element_handle(), True, None
                        except Exception:
                            continue
                else:
                    for sel in [
                        ".jobs-apply-button--top-card a",
                        ".jobs-unified-top-card a.jobs-apply-button",
                        "[data-view-name='job-details'] a:has-text('Apply')",
                        "a:has-text('Apply')",
                        "button:has-text('Apply')"
                    ]:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible():
                                return loc.element_handle(), False, loc.get_attribute("href")
                        except Exception:
                            continue
        except Exception as e:
            console.print(f"[dim]  _find_apply_button note: {e}[/dim]")

        # Strategy 2: Fallback Playwright locators scoped to top-card
        for sel in [
            ".jobs-apply-button--top-card button",
            "button:has-text('Easy Apply')",
            "[aria-label*='Easy Apply to']"
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return loc.element_handle(), True, None
            except Exception:
                continue

        for sel in [
            ".jobs-apply-button--top-card a",
            "a:has-text('Apply')",
            "button:has-text('Apply')"
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return loc.element_handle(), False, loc.get_attribute("href")
            except Exception:
                continue

        return None, False, None

    def _click_modal_button(self, page, modal, texts: list) -> bool:
        """Click the first visible, enabled button in modal matching any of the given texts."""
        # 1. Try page-level locators scoped to modal/dialog
        for text in texts:
            for sel in [
                f"dialog[open] button:has-text('{text}')",
                f"div[role='dialog'] button:has-text('{text}')",
                f".jobs-easy-apply-modal button:has-text('{text}')",
                f"button:has-text('{text}')",
                f"dialog[open] button[aria-label*='{text}']",
                f"div[role='dialog'] button[aria-label*='{text}']",
                f"button[aria-label*='{text}']"
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible() and loc.is_enabled():
                        loc.click()
                        return True
                except Exception:
                    pass

        # 2. Try modal ElementHandle query_selector_all
        if modal:
            try:
                buttons = modal.query_selector_all("button")
                for btn in buttons:
                    try:
                        b_text = (btn.inner_text() or "").strip().lower()
                        b_aria = (btn.get_attribute("aria-label") or "").strip().lower()
                        for text in texts:
                            t_lower = text.lower()
                            if (b_text == t_lower or b_text.startswith(t_lower) or t_lower in b_aria) and btn.is_visible() and btn.is_enabled():
                                btn.click()
                                return True
                    except Exception:
                        continue
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

                    # Wait for modal - Supports native HTML5 <dialog> and classic div modals
                    modal = None
                    modal_selectors = [
                        "dialog[open]",
                        "dialog[data-testid='dialog']",
                        "dialog",
                        "div[role='dialog']",
                        "[data-testid='dialog']",
                        ".jobs-easy-apply-modal",
                        ".artdeco-modal"
                    ]
                    for modal_sel in modal_selectors:
                        try:
                            page.wait_for_selector(modal_sel, timeout=4000, state="visible")
                            modal = page.query_selector(modal_sel)
                            if modal and modal.is_visible():
                                break
                        except Exception:
                            continue

                    if not modal:
                        try:
                            modal = page.query_selector("dialog, div[role='dialog']")
                        except Exception:
                            pass

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
                                        page.wait_for_timeout(600)
                                        self._click_modal_button(page, modal, ["Next", "Continue to next step"])
                                        page.wait_for_timeout(1500)
                                except Exception:
                                    pass
                            continue

                        # Primary footer button fallback (e.g. Next / Review / Submit with dynamic text)
                        primary_btn = page.locator("dialog[open] footer button.artdeco-button--primary, div[role='dialog'] footer button.artdeco-button--primary, footer button.artdeco-button--primary, dialog[open] button.artdeco-button--primary").first
                        try:
                            if primary_btn.is_visible() and primary_btn.is_enabled():
                                btn_text = (primary_btn.inner_text() or "").lower()
                                console.print(f"[cyan]  Clicking primary modal action: '{btn_text}'[/cyan]")
                                primary_btn.click()
                                page.wait_for_timeout(2000)
                                if "submit" in btn_text:
                                    submitted = True
                                    break
                                continue
                        except Exception:
                            pass

                        # Check if primary button is disabled due to unfulfilled questions
                        try:
                            disabled_btn = page.locator("dialog[open] footer button[disabled], div[role='dialog'] footer button[disabled], footer button[disabled]").first
                            if disabled_btn.is_visible():
                                console.print("[dim]  Action button currently disabled — AI re-evaluating form to answer remaining questions...[/dim]")
                                self._fill_easy_apply_step(modal, user_profile, page=page)
                                page.wait_for_timeout(800)
                                if self._click_modal_button(page, modal, ["Submit application", "Submit"]):
                                    page.wait_for_timeout(4000)
                                    submitted = True
                                    break
                                if self._click_modal_button(page, modal, ["Review", "Review your application", "Next", "Continue to next step"]):
                                    page.wait_for_timeout(1500)
                                    continue
                        except Exception:
                            pass

                        # No recognizable or actionable button — break to avoid infinite loop
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
                            dismiss = page.locator("dialog[open] button[aria-label='Dismiss'], div[role='dialog'] button[aria-label='Dismiss'], button[data-testid='dialog-close-button'], button[aria-label='Dismiss']").first
                            if dismiss.is_visible():
                                dismiss.click()
                                page.wait_for_timeout(1000)
                                discard = page.locator("button:has-text('Discard')").first
                                if discard.is_visible():
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

