import os
import re
from typing import Dict, Any, Tuple, Optional

class LLMAssistant:
    def __init__(self, provider: str = "gemini", model: str = "gemini-1.5-flash"):
        self.provider = provider
        self.model = model
        self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")

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
            # Non-Salesforce but matched some skills — reduce score
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

    def answer_form_question(self, question: str, profile: Dict[str, Any]) -> str:
        """Generates appropriate answers to custom application questions."""
        q_lower = question.lower()
        answers = profile.get("answers_to_common_questions", {})
        
        if "years of experience" in q_lower or "how many years" in q_lower:
            return str(answers.get("years_of_experience", 1))
        elif "notice period" in q_lower or "notice" in q_lower:
            return f"{profile.get('compensation_and_notice', {}).get('notice_period_days', 30)} days"
        elif "current ctc" in q_lower or "current salary" in q_lower:
            return f"{profile.get('compensation_and_notice', {}).get('current_ctc_lpa', 4.25)} LPA"
        elif "expected ctc" in q_lower or "expected salary" in q_lower:
            return f"{profile.get('compensation_and_notice', {}).get('expected_ctc_lpa', 6.0)} LPA"
        elif "relocate" in q_lower:
            return "Yes" if answers.get("willing_to_relocate", True) else "No"
        elif "sponsorship" in q_lower:
            return "No" if not profile.get("work_authorization", {}).get("requires_sponsorship", False) else "Yes"
        elif "authorized" in q_lower or "legally" in q_lower:
            return "Yes"
        else:
            return "Yes, proficient and experienced with relevant production implementations."