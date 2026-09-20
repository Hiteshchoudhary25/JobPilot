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

    def _handle_naukri_popup(self, page, user_profile: Dict[str, Any]) -> bool:
        """Detects and fills the Naukri recruiter questions popup, then clicks Save.
        Returns True if popup was found and handled."""
        personal = user_profile.get("personal_info", {})
        comp = user_profile.get("compensation_and_notice", {})
        answers = user_profile.get("answers_to_common_questions", {})

        # Detect popup — Naukri uses a chat-style widget or a dialog
        popup = page.query_selector(
            "div.chatbot_DrawerContentWrapper, "
            "div[class*='chatbot'], "
            "div[class*='applypopup'], "
            "div[class*='apply-popup'], "
            "div[class*='recruiter-question'], "
            "div.naukri-apply-modal, "
            "div[role='dialog']"
        )
        if not popup:
            return False

        console.print(f"[bold yellow]  Recruiter popup detected — autofilling questions...[/bold yellow]")

        max_rounds = 10  # max question rounds to prevent infinite loops
        for _ in range(max_rounds):
            page.wait_for_timeout(800)

            # --- Handle radio button questions (e.g. "Less than 5 years" / "More than 5 years") ---
            radio_groups = popup.query_selector_all("div[class*='singleSelect'], div[class*='radio'], fieldset")
            for grp in radio_groups:
                try:
                    # Get question text
                    q_el = grp.query_selector("label, p, span, legend")
                    q_text = q_el.inner_text().lower() if q_el else ""

                    radios = grp.query_selector_all("input[type='radio']")
                    if not radios:
                        # Naukri sometimes uses styled divs as radio options
                        radios = grp.query_selector_all("li, div[class*='option'], div[class*='btn']")

                    if not radios:
                        continue

                    # Decide which option to pick
                    target_text = None
                    if any(w in q_text for w in ["years of experience", "experience", "how many years"]):
                        yoe = int(answers.get("years_of_experience", 1))
                        target_text = "less than 5" if yoe < 5 else "more than 5"
                    elif any(w in q_text for w in ["notice", "joining"]):
                        target_text = str(comp.get("notice_period_days", 30))
                    elif any(w in q_text for w in ["current", "ctc", "salary", "compensation"]):
                        target_text = str(comp.get("current_ctc_lpa", 4.25))
                    elif any(w in q_text for w in ["expected", "expect"]):
                        target_text = str(comp.get("expected_ctc_lpa", 6.0))
                    elif any(w in q_text for w in ["location", "city", "relocat"]):
                        target_text = "yes"
                    elif any(w in q_text for w in ["work from home", "remote", "wfh"]):
                        target_text = "yes"
                    else:
                        target_text = "yes"  # safe default

                    # Try clicking the best matching radio/option
                    clicked = False
                    for r in radios:
                        try:
                            r_text = r.inner_text().lower() if hasattr(r, "inner_text") else ""
                            if not r_text:
                                lbl_id = r.get_attribute("id")
                                lbl = popup.query_selector(f"label[for='{lbl_id}']")
                                r_text = lbl.inner_text().lower() if lbl else ""
                            r_val = (r.get_attribute("value") or "").lower()
                            combined = r_text + " " + r_val

                            if target_text and target_text in combined:
                                r.click()
                                clicked = True
                                break
                        except Exception:
                            continue

                    if not clicked and radios:
                        # Fall back: click first option
                        try:
                            radios[0].click()
                        except Exception:
                            pass
                except Exception:
                    continue

            # --- Handle text / number inputs ---
            text_inputs = popup.query_selector_all("input[type='text'], input[type='number'], textarea")
            for inp in text_inputs:
                try:
                    curr = inp.input_value()
                    if curr and curr.strip():
                        continue  # already filled
                    inp_id = inp.get_attribute("id") or inp.get_attribute("name") or ""
                    lbl = popup.query_selector(f"label[for='{inp_id}']")
                    lbl_text = lbl.inner_text().lower() if lbl else inp_id.lower()

                    if any(w in lbl_text for w in ["phone", "mobile"]):
                        inp.fill(str(personal.get("phone", "6375807336")))
                    elif "email" in lbl_text:
                        inp.fill(personal.get("email", "hiteshchoudhary2508@gmail.com"))
                    elif any(w in lbl_text for w in ["experience", "years"]):
                        inp.fill(str(answers.get("years_of_experience", 1)))
                    elif any(w in lbl_text for w in ["current", "ctc", "salary"]):
                        inp.fill(str(comp.get("current_ctc_lpa", 4.25)))
                    elif any(w in lbl_text for w in ["expected", "expect"]):
                        inp.fill(str(comp.get("expected_ctc_lpa", 6.0)))
                    elif "notice" in lbl_text:
                        inp.fill(str(comp.get("notice_period_days", 30)))
                    else:
                        inp.fill("1")
                except Exception:
                    continue

            # --- Handle dropdowns ---
            selects = popup.query_selector_all("select")
            for sel in selects:
                try:
                    val = sel.evaluate("el => el.value")
                    if not val:
                        options = sel.query_selector_all("option")
                        if len(options) > 1:
                            sel.select_option(index=1)
                except Exception:
                    continue

            # --- Click Save / Next / Submit in the popup ---
            save_btn = None
            for btn_sel in [
                "button:has-text('Save')",
                "button:has-text('Submit')",
                "button:has-text('Next')",
                "button:has-text('Apply')",
                "button[type='submit']",
                "div[class*='saveBtn']",
                "div[class*='submitBtn']",
            ]:
                try:
                    candidate = popup.query_selector(btn_sel)
                    if candidate and candidate.is_visible():
                        save_btn = candidate
                        break
                except Exception:
                    continue

            if save_btn:
                console.print(f"[dim]    Clicking popup button: {save_btn.inner_text().strip()[:30]}[/dim]")
                save_btn.click()
                page.wait_for_timeout(1500)
            else:
                break  # No more buttons, popup is done

            # Check if popup is gone (application submitted)
            still_open = page.query_selector(
                "div.chatbot_DrawerContentWrapper, "
                "div[class*='chatbot'], "
                "div[class*='applypopup'], "
                "div[role='dialog']"
            )
            if not still_open:
                console.print(f"[green]  Popup closed — application submitted successfully.[/green]")
                return True

        # If loop ends, popup is still there — check for success text in popup
        try:
            popup_text = popup.inner_text().lower()
            if any(w in popup_text for w in ["thank you", "applied", "successfully", "received"]):
                console.print(f"[green]  Popup shows success message.[/green]")
                return True
        except Exception:
            pass

        console.print(f"[yellow]  Popup not fully resolved — may need manual completion.[/yellow]")
        return False

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
                    direct_apply = page.query_selector(
                        "#apply-button, "
                        "button.apply-button, "
                        "button:has-text('Apply'), "
                        "a.apply-button"
                    )
                    if direct_apply and direct_apply.is_visible():
                        console.print(f"[bold green]  Clicking Apply button for {job.company}...[/bold green]")
                        direct_apply.click()
                        page.wait_for_timeout(2500)

                        # Check if a recruiter popup appeared after clicking Apply
                        popup_handled = self._handle_naukri_popup(page, user_profile)

                        # Now check final page state
                        page_text = page.inner_text("body").lower()
                        success_words = ["successfully applied", "application submitted", "thank you for applying", "already applied"]
                        if popup_handled or any(w in page_text for w in success_words):
                            console.print(f"[bold green][OK] Successfully applied on Naukri: {job.title} at {job.company}[/bold green]")
                            record = ApplicationRecord(
                                job_id=job.id,
                                platform="naukri",
                                company=job.company,
                                role_title=job.title,
                                job_url=job.url,
                                status=ApplicationStatus.APPLIED_EASY,
                                notes="Successfully submitted via Naukri Apply (popup autofilled)" if popup_handled else "Applied via Naukri 1-Click"
                            )
                            self.db.record_application(record)
                            context.close()
                            return True
                        else:
                            # Popup appeared but wasn't fully resolved — queue for manual
                            console.print(f"[yellow]  Apply clicked but not confirmed for {job.company} — queued for manual review[/yellow]")
                            record = ApplicationRecord(
                                job_id=job.id,
                                platform="naukri",
                                company=job.company,
                                role_title=job.title,
                                job_url=job.url,
                                status=ApplicationStatus.REQUIRES_MANUAL,
                                notes="Apply clicked but popup required manual input — open job URL to complete"
                            )
                            self.db.record_application(record)
                            context.close()
                            return False

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