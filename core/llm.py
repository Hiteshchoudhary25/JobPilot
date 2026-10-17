import os
import re
import warnings
from typing import Dict, Any, Tuple, Optional, List

warnings.filterwarnings("ignore", category=FutureWarning)

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

class LLMAssistant:
    def __init__(self, provider: str = "gemini", model: str = "gemini-1.5-flash"):
        self.provider = provider
        self.model_name = model
        self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.genai_model = None
        self._init_genai()

    def _init_genai(self):
        """Initialize Google Generative AI if key is present and package is installed."""
        if HAS_GENAI and self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                # Try requested model with fallback
                self.genai_model = genai.GenerativeModel(self.model_name)
            except Exception:
                try:
                    self.genai_model = genai.GenerativeModel("gemini-1.5-flash")
                except Exception:
                    self.genai_model = None

    def evaluate_fit(self, job_title: str, job_description: str, profile: Dict[str, Any]) -> Tuple[int, str]:
        """Calculates a match percentage score (0-100) and rationale."""
        user_skills = [s.lower() for s in profile.get("skills", {}).get("primary", [])]
        user_skills += [s.lower() for s in profile.get("skills", {}).get("secondary", [])]

        text_to_check = f"{job_title} {job_description}".lower()
        title_lower = job_title.lower()
        matched_skills = [skill for skill in user_skills if skill in text_to_check]

        # --- Salesforce core signal ---
        is_salesforce_role = any(w in title_lower for w in [
            "salesforce", "crm developer", "apex", "lwc", "lightning", "agentforce", "vlocity"
        ])

        # --- Irrelevant tech penalty: if title is clearly NOT Salesforce, cap score ---
        IRRELEVANT_TECH = [
            "react", "reactjs", "angular", "vue", "laravel", "shopify", "ui/ux",
            "android", "ios", "flutter", "django", "rails", "ruby", "php",
            "wordpress", "magento", "front-end", "frontend", "graphic design",
            "game developer", "blockchain", "solidity", "devops only", "sre",
        ]
        is_irrelevant = any(tech in title_lower for tech in IRRELEVANT_TECH)
        if is_irrelevant:
            return 30, f"Irrelevant tech stack for Salesforce-focused search: {job_title}"

        base_score = 50
        if len(user_skills) > 0:
            match_ratio = len(matched_skills) / max(5, len(user_skills[:8]))
            score = int(base_score + min(45, match_ratio * 50))
        else:
            score = 65

        # Salesforce/CRM/Java alignment bonus
        if is_salesforce_role and any(s in ["salesforce", "java"] for s in user_skills):
            score = min(98, score + 12)
        elif not is_salesforce_role:
            score = min(score, 68)

        matched_str = ", ".join(matched_skills[:5]) if matched_skills else "General engineering alignment"
        reason = f"Matched skills: {matched_str}. Experience aligned."
        return max(30, min(99, score)), reason

    def generate_cold_email(self, job_title: str, company: str, job_description: str, profile: Dict[str, Any]) -> Dict[str, str]:
        """Generates a personalized recruiter cold email pitch."""
        personal = profile.get("personal_info", {})
        candidate_name = personal.get("full_name", "Candidate")
        phone = personal.get("phone", "")
        linkedin = personal.get("linkedin_url", "")
        portfolio = personal.get("portfolio_url", "")
        github = personal.get("github_url", "")
        skills_summary = ", ".join(profile.get("skills", {}).get("primary", ["Java", "Salesforce", "Software Engineering"])[:4])
        years_exp = profile.get("answers_to_common_questions", {}).get("years_of_experience", 1)

        subject = f"Application: {job_title} - {candidate_name} ({years_exp}+ Years Exp | {skills_summary})"

        links_section = []
        if linkedin:
            links_section.append(f"- LinkedIn: {linkedin}")
        if github:
            links_section.append(f"- GitHub: {github}")
        if portfolio:
            links_section.append(f"- Portfolio: {portfolio}")
        links_text = "\n".join(links_section)

        body = f"""Hi Hiring Team at {company},

I came across your opening for the {job_title} role and wanted to reach out directly to express my strong interest in joining your team.

With practical software engineering and enterprise integration experience specializing in {skills_summary}, I have built and validated enterprise platform components, API integrations, and robust automated validation workflows.

Given {company}'s requirements for this role, I believe my background in {skills_summary} and solid foundation in OOPs and data validation would allow me to make an immediate, positive contribution to your engineering team.

I have attached my resume for your review. You can also review my profiles here:
{links_text}

I would welcome the opportunity to connect for a brief 10-15 minute conversation. Thank you for your time and consideration!

Best regards,

{candidate_name}
{personal.get("email", "")}
{phone}
"""
        return {"subject": subject, "body": body}

    def answer_form_question(self, question: str, profile: Dict[str, Any], input_type: str = "text", max_length: Optional[int] = None) -> str:
        """Generates intelligent, highly accurate answers to custom application screening questions."""
        # 1. Try AI generation via Gemini if available
        if self.genai_model:
            try:
                candidate_summary = (
                    f"Name: {profile.get('personal_info', {}).get('full_name', 'Hitesh Choudhary')}\n"
                    f"Experience: Graduate Engineer at Coforge Ltd, working with Java, Salesforce platforms, REST API validation, SQL.\n"
                    f"LeetCode: 700+ solved in Java. Education: B.Tech in Computer Science from JECRC University (GPA 8.4).\n"
                    f"Notice Period: 30 days. Current CTC: 4.25 LPA. Expected CTC: 6.00 LPA. Location: Noida, India. Willing to relocate: Yes. Sponsorship needed: No."
                )
                type_constraint = "Respond with ONLY an integer or decimal number, no letters or units." if input_type == "number" else "Respond concisely and professionally."
                length_constraint = f"Maximum length {max_length} characters." if max_length else "Under 150 characters."

                prompt = (
                    f"You are autofilling a job application form question for this candidate:\n{candidate_summary}\n\n"
                    f"Question: \"{question}\"\n"
                    f"Instructions: {type_constraint} {length_constraint} Do not add fluff, markdown, or commentary. Output the direct answer only."
                )
                resp = self.genai_model.generate_content(prompt)
                if resp and resp.text:
                    cleaned = resp.text.strip().replace('"', '').replace("'", "")
                    if input_type == "number":
                        # Extract digits/decimal
                        num_match = re.search(r"[-+]?(?:\d*\.\d+|\d+)", cleaned)
                        if num_match:
                            return num_match.group(0)
                    elif cleaned:
                        return cleaned[:max_length] if max_length else cleaned
            except Exception:
                pass

        # 2. Comprehensive semantic inference engine (offline fallback)
        return self._semantic_fallback_answer(question, profile, input_type=input_type, max_length=max_length)

    def choose_best_option(self, question: str, options: List[str], profile: Dict[str, Any]) -> str:
        """Selects the best matching option from a dropdown or radio list based on the candidate's profile."""
        if not options:
            return ""

        # Filter out empty or placeholder options
        valid_opts = [
            opt for opt in options 
            if opt and opt.strip().lower() not in ["", "select", "select an option", "choose", "please select", "--", "none", "select..."]
        ]
        if not valid_opts:
            return options[0]

        # 1. Try Gemini option selection if available
        if self.genai_model:
            try:
                prompt = (
                    f"Candidate Profile: Software Engineer with 1+ year experience in Java and Salesforce at Coforge. "
                    f"B.Tech in Computer Science (GPA 8.4). Notice: 30 days. Authorized in India, no visa sponsorship needed. Location: Noida, willing to relocate.\n\n"
                    f"Form Question: \"{question}\"\n"
                    f"Available Options: {valid_opts}\n\n"
                    f"Which ONE exact option from the list should the candidate select? Return ONLY the exact text of that option."
                )
                resp = self.genai_model.generate_content(prompt)
                if resp and resp.text:
                    cleaned = resp.text.strip().replace('"', '').replace("'", "")
                    for opt in valid_opts:
                        if opt.lower() == cleaned.lower() or opt.lower() in cleaned.lower():
                            return opt
            except Exception:
                pass

        # 2. Semantic option matcher
        return self._semantic_fallback_choose(question, valid_opts, profile)

    def _semantic_fallback_answer(self, question: str, profile: Dict[str, Any], input_type: str = "text", max_length: Optional[int] = None) -> str:
        q_lower = question.lower()
        answers = profile.get("answers_to_common_questions", {})
        comp = profile.get("compensation_and_notice", {})
        personal = profile.get("personal_info", {})
        education = profile.get("education", [{}])[0]
        work_auth = profile.get("work_authorization", {})

        # 1. Numeric inputs
        if input_type == "number":
            # Notice period in days
            if any(w in q_lower for w in ["notice", "serving"]):
                return str(comp.get("notice_period_days", 30))
            # CTC in hundreds
            if any(w in q_lower for w in ["100", "hundred", "/ 100", "/100"]):
                return str(comp.get("expected_ctc_hundreds", 6000) if any(w in q_lower for w in ["expected", "desired"]) else comp.get("current_ctc_hundreds", 4250))
            # CTC in LPA
            if any(w in q_lower for w in ["lpa", "lakh"]):
                return str(comp.get("expected_ctc_lpa", 6.0) if any(w in q_lower for w in ["expected", "desired"]) else comp.get("current_ctc_lpa", 4.25))
            # Full INR CTC
            if any(w in q_lower for w in ["ctc", "salary", "inr", "rupee"]):
                return str(comp.get("expected_ctc_inr", 600000) if any(w in q_lower for w in ["expected", "desired"]) else comp.get("current_ctc_inr", 425000))
            # Ratings on scale
            if any(w in q_lower for w in ["scale of 1-5", "1 to 5", "out of 5", "rating 1-5"]):
                return "4"
            if any(w in q_lower for w in ["scale of 1-10", "1 to 10", "out of 10", "rating 1-10"]):
                return "8"
            # Percentage / CGPA
            if any(w in q_lower for w in ["cgpa", "gpa", "percentage", "marks"]):
                return "8.4" if "gpa" in q_lower or "cgpa" in q_lower else "84"
            # Graduation year
            if any(w in q_lower for w in ["graduation year", "passing year", "year of completion"]):
                return str(education.get("graduation_year", 2026))
            # Skill years:
            if any(w in q_lower for w in ["java", "dsa", "oops"]):
                return "2"
            if any(w in q_lower for w in ["salesforce", "apex", "lwc", "lightning", "admin", "crm"]):
                return "1"
            if any(w in q_lower for w in ["selenium", "test", "testing", "qa", "automation"]):
                return "1"
            if any(w in q_lower for w in ["sql", "database", "snowflake"]):
                return "1"
            if any(w in q_lower for w in ["python", "ml"]):
                return "1"
            return str(answers.get("years_of_experience", 1))

        # 2. Text / Textarea inputs
        # Education details
        if any(w in q_lower for w in ["highest qualification", "highest degree", "highest level of education", "degree"]):
            return education.get("degree", "Bachelor of Technology in Computer Science")
        if any(w in q_lower for w in ["university", "college", "institution", "school"]):
            return education.get("institution", "JECRC University")
        if any(w in q_lower for w in ["major", "specialization", "branch", "field of study"]):
            return "Computer Science and Engineering"
        if any(w in q_lower for w in ["graduation year", "year of pass", "year of graduation"]):
            return str(education.get("graduation_year", 2026))

        # Notice Period
        if any(w in q_lower for w in ["notice period", "notice days", "how soon can you join", "joining time"]):
            return f"{comp.get('notice_period_days', 30)} days (negotiable)"

        # CTC
        if any(w in q_lower for w in ["current ctc", "current salary", "present ctc"]):
            return f"{comp.get('current_ctc_lpa', 4.25)} LPA"
        if any(w in q_lower for w in ["expected ctc", "expected salary"]):
            return f"{comp.get('expected_ctc_lpa', 6.0)} LPA"

        # Work authorization / Sponsorship
        if any(w in q_lower for w in ["sponsorship", "visa", "require sponsorship"]):
            return "No"
        if any(w in q_lower for w in ["authorized", "legally authorized", "work permit", "eligible to work in india"]):
            return "Yes"

        # Relocation / Shifts / Commute / Travel
        if any(w in q_lower for w in ["relocate", "relocation", "willing to move"]):
            return "Yes, willing to relocate"
        if any(w in q_lower for w in ["remote", "hybrid", "work from office", "wfo", "commute"]):
            return "Yes, comfortable working from office, hybrid, or remote"
        if any(w in q_lower for w in ["shift", "rotational", "night shift"]):
            return "Yes, flexible and comfortable with assigned shifts"

        # Experience & Skill checks
        if any(w in q_lower for w in ["years of experience", "how many years"]):
            if any(w in q_lower for w in ["java", "dsa"]):
                return "2"
            return str(answers.get("years_of_experience", 1))

        # Boolean inquiry ("Do you have experience...", "Have you worked with...")
        if any(q_lower.startswith(prefix) for prefix in ["do you", "have you", "are you", "can you", "will you"]):
            # Negative triggers
            if any(w in q_lower for w in ["criminal", "felony", "misdemeanor", "non-compete", "sponsorship", "disciplinary"]):
                return "No"
            return "Yes"

        # Open-ended motivation / summary questions
        if any(w in q_lower for w in ["why should we hire", "why are you interested", "tell us about yourself", "cover letter", "summary", "briefly describe"]):
            desc = (
                "Software Engineer with 1+ year of hands-on experience developing and validating enterprise Java components "
                "integrated with Salesforce, Snowflake, and core backend databases at Coforge. Strong foundation in OOPs and DSA "
                "with 700+ LeetCode problems solved in Java, REST API validation, and automated testing."
            )
            return desc[:max_length] if max_length else desc

        # General affirmative, professional answer
        return "Yes, proficient and experienced with relevant production implementations."

    def _semantic_fallback_choose(self, question: str, options: List[str], profile: Dict[str, Any]) -> str:
        q_lower = question.lower()

        # 1. Sponsorship / Visa
        if any(w in q_lower for w in ["sponsorship", "visa", "require sponsorship"]):
            for opt in options:
                if any(w in opt.lower() for w in ["no", "not require", "neither", "don't require", "do not require"]):
                    return opt

        # 2. Work Authorization / Citizenship
        if any(w in q_lower for w in ["authorized", "citizen", "work permit", "authorization"]):
            for opt in options:
                if any(w in opt.lower() for w in ["citizen", "authorized", "yes", "permanent resident"]):
                    return opt

        # 3. Education Degree
        if any(w in q_lower for w in ["education", "degree", "qualification"]):
            for opt in options:
                if any(w in opt.lower() for w in ["bachelor", "b.tech", "undergraduate", "b.e", "graduate", "college degree"]):
                    return opt

        # 4. Notice Period
        if any(w in q_lower for w in ["notice", "joining", "how soon"]):
            for opt in options:
                if any(w in opt.lower() for w in ["30 days", "1 month", "immediate", "15 days", "< 1 month", "less than 30", "1 to 30"]):
                    return opt

        # 5. Gender
        if any(w in q_lower for w in ["gender", "sex"]):
            for opt in options:
                if opt.strip().lower() == "male":
                    return opt
                if "prefer not to say" in opt.lower() or "decline" in opt.lower():
                    return opt

        # 6. Experience brackets
        if any(w in q_lower for w in ["experience", "years"]):
            for opt in options:
                if any(w in opt.lower() for w in ["1-2", "1 to 2", "0-2", "1-3", "1 year", "1+ year"]):
                    return opt

        # 7. English / Communication Proficiency
        if any(w in q_lower for w in ["english", "communication", "language"]):
            for opt in options:
                if any(w in opt.lower() for w in ["fluent", "professional", "advanced", "native"]):
                    return opt

        # 8. Proficiency levels in tech
        if any(w in q_lower for w in ["proficiency", "level", "skill"]):
            for opt in options:
                if any(w in opt.lower() for w in ["intermediate", "advanced", "proficient", "expert"]):
                    return opt

        # 9. Shifts / Relocation / Remote
        if any(w in q_lower for w in ["relocate", "relocation", "shift", "remote", "hybrid"]):
            for opt in options:
                if any(w in opt.lower() for w in ["yes", "flexible", "hybrid", "willing", "open"]):
                    return opt

        # 10. General affirmative choice
        for opt in options:
            if opt.strip().lower() in ["yes", "true", "i agree", "agree", "acknowledged"]:
                return opt

        # Default: first non-empty option
        return options[0]