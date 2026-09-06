# 🚀 JobPilot — Autonomous Job Application & Recruiter Outreach Agent

JobPilot is an intelligent, autonomous agent that searches, evaluates, applies to jobs, and dispatches personalized recruiter cold emails across platforms like **LinkedIn** and **Naukri.com**.

---

## ✨ Features

- 🔍 **Multi-Platform Search**: Searches LinkedIn (Guest API) and Naukri.com (Playwright-driven Chrome automation) across customizable titles and target locations.
- 🎯 **AI-Powered Fit Scoring**: Evaluates candidate profile against job descriptions and scores relevance (0–100%) with automated seniority and tech stack filtering.
- ⚡ **Automated In-Browser Apply**: Interacts directly with Naukri 1-Click Apply buttons using persistent authenticated sessions.
- 📧 **Cold Email Outreach**: Extracts recruiter emails from job descriptions, filters non-recruiter inboxes (fraud, ADA, billing), generates personalized pitches, and sends emails with an attached resume.
- 📊 **Interactive Web Dashboard**: Beautiful HTML dashboard with charts, KPI statistics, search bar, platform filters, and CSV export for Excel.
- 🛡️ **Deduplication & Anti-Bot Protection**: SQLite tracking prevents duplicate applications and duplicate email sends; randomized human-like browsing patterns bypass bot checks.

---

## 🏗️ Project Architecture

```
job_agent/
├── config/
│   ├── search_criteria.yaml   # Target titles, locations, fit thresholds
│   ├── settings.yaml          # SMTP configs, limits, platform toggles
│   └── user_profile.yaml      # Candidate skills, experience, CTC, answers
├── core/
│   ├── browser_manager.py     # Playwright interactive session manager
│   ├── db.py                  # SQLite database CRUD operations (tracker.db)
│   ├── llm.py                 # Fit scoring, cold email generation
│   └── models.py              # Pydantic data models & status enums
├── outreach/
│   ├── email_extractor.py     # Regex email extraction with blocklists
│   └── email_sender.py        # SMTP email dispatch & resume attachment
├── scrapers/
│   ├── base.py                # Abstract Base Scraper class
│   ├── linkedin.py            # LinkedIn search & apply engine
│   └── naukri.py              # Naukri Playwright automation engine
├── dashboard.py               # Generates live HTML analytics & CSV export
├── main.py                    # CLI entrypoint for all agent operations
├── requirements.txt           # Python dependencies
└── .env.example               # Environment variables template
```

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Hiteshchoudhary25/JobPilot.git
cd JobPilot

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate       # Windows PowerShell
# source .venv/bin/activate    # Linux / macOS

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 2. Configuration

1. **Credentials**: Copy `.env.example` to `.env` and add your Gmail 16-character App Password:
   ```env
   EMAIL_APP_PASSWORD=your_gmail_app_password
   ```
2. **User Profile**: Edit `config/user_profile.yaml` with your skills, experience, and contact details.
3. **Search Criteria**: Edit `config/search_criteria.yaml` for job titles and cities.
4. **Resume**: Place your PDF resume into `resumes/` and ensure the path matches `config/settings.yaml`.

### 3. Save Browser Login Sessions (One-Time)

To enable automated applying on Naukri & LinkedIn:
```bash
python main.py login --platform naukri
python main.py login --platform linkedin
```

---

## 🛠️ CLI Usage

```bash
# Full daily autonomous pipeline (Search -> Score -> Apply -> Email)
python main.py run

# Search only (discover & evaluate jobs)
python main.py search --limit 20

# Auto-apply to matching jobs
python main.py apply

# Send cold outreach emails to recruiters
python main.py email

# View terminal application statistics
python main.py stats

# Open interactive browser dashboard
python dashboard.py

# Send a test email to verify SMTP configuration
python main.py test-email
```

---

## 📊 Live Dashboard

Run `python dashboard.py` to open the local web dashboard:
- Daily application trends & platform breakdown
- Full searchable table of all discovered jobs
- Filter by platform (LinkedIn / Naukri)
- 1-click export to CSV for Excel

---

## 🔒 Security & Privacy

- Sensitive credentials and session cookies are strictly excluded via `.gitignore`.
- Respects rate limits with randomized delays between requests.
- Always use a Gmail App Password, never your primary account password.