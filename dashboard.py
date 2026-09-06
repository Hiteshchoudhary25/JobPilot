import sqlite3
import os
import webbrowser
import json
import csv
from datetime import datetime, date

DB_PATH = "tracker.db"
OUT_PATH = "dashboard.html"
CSV_PATH = "discovered_jobs.csv"

def fetch_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    today = date.today().isoformat()

    c.execute("SELECT COUNT(*) as cnt FROM applications WHERE DATE(applied_at) = ?", (today,))
    applied_today = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM applications")
    total_applied = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM jobs")
    total_discovered = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM emails")
    total_emails = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM emails WHERE DATE(sent_at) = ?", (today,))
    emails_today = c.fetchone()["cnt"]

    c.execute("SELECT platform, COUNT(*) as cnt FROM applications GROUP BY platform")
    by_platform = {r["platform"].upper(): r["cnt"] for r in c.fetchall()}

    c.execute("""
        SELECT DATE(applied_at) as day, COUNT(*) as cnt
        FROM applications
        WHERE applied_at >= date('now', '-14 days')
        GROUP BY day ORDER BY day
    """)
    daily_rows = c.fetchall()
    daily_labels = [r["day"] for r in daily_rows]
    daily_counts = [r["cnt"] for r in daily_rows]

    c.execute("""
        SELECT a.role_title, a.company, a.platform, a.status, a.applied_at,
               j.location, j.fit_score, j.url
        FROM applications a
        LEFT JOIN jobs j ON a.job_id = j.id
        ORDER BY a.applied_at DESC LIMIT 20
    """)
    recent = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT company, COUNT(*) as cnt FROM applications
        GROUP BY company ORDER BY cnt DESC LIMIT 8
    """)
    top_companies = [dict(r) for r in c.fetchall()]

    # Fetch ALL discovered jobs from database
    c.execute("""
        SELECT j.id, j.platform, j.title, j.company, j.location, j.url,
               j.fit_score, j.recruiter_emails, j.created_at,
               a.status as app_status
        FROM jobs j
        LEFT JOIN applications a ON j.id = a.job_id
        ORDER BY j.created_at DESC
    """)
    all_discovered = [dict(r) for r in c.fetchall()]

    conn.close()

    # Also export all jobs to CSV for Excel
    try:
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Platform", "Role Title", "Company", "Location", "Fit Score", "Status", "Recruiter Emails", "Job URL", "Discovered At"])
            for j in all_discovered:
                writer.writerow([
                    j["platform"].upper(),
                    j["title"],
                    j["company"],
                    j["location"],
                    f"{j['fit_score']}%" if j['fit_score'] else "-",
                    j["app_status"] or "DISCOVERED",
                    j["recruiter_emails"] or "-",
                    j["url"],
                    str(j["created_at"])[:19]
                ])
    except Exception as e:
        print(f"CSV export notice: {e}")

    return {
        "applied_today": applied_today,
        "total_applied": total_applied,
        "total_discovered": total_discovered,
        "total_emails": total_emails,
        "emails_today": emails_today,
        "by_platform": by_platform,
        "daily_labels": daily_labels,
        "daily_counts": daily_counts,
        "recent": recent,
        "all_discovered": all_discovered,
        "top_companies": top_companies,
        "generated_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "today_str": date.today().strftime("%d %B %Y"),
    }

def build_html(d):
    platform_labels  = json.dumps(list(d["by_platform"].keys()))
    platform_data    = json.dumps(list(d["by_platform"].values()))
    daily_labels     = json.dumps(d["daily_labels"])
    daily_data       = json.dumps(d["daily_counts"])
    top_co_labels    = json.dumps([r["company"][:22] for r in d["top_companies"]])
    top_co_data      = json.dumps([r["cnt"] for r in d["top_companies"]])

    applied_today    = d["applied_today"]
    total_applied    = d["total_applied"]
    total_discovered = d["total_discovered"]
    total_emails     = d["total_emails"]
    emails_today     = d["emails_today"]
    plat_count       = len(d["by_platform"])
    today_str        = d["today_str"]
    generated_at     = d["generated_at"]
    easy_count       = max(0, total_applied - total_emails)

    # Discovered jobs table rows
    all_jobs_rows = ""
    for r in d["all_discovered"]:
        score = r.get("fit_score")
        if isinstance(score, int):
            color = "#22c55e" if score >= 70 else "#f59e0b" if score >= 50 else "#ef4444"
            score_badge = f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:9999px;font-size:12px">{score}%</span>'
        else:
            score_badge = '<span style="color:#888">-</span>'

        status = r.get("app_status")
        if status == "APPLIED_EASY":
            status_badge = '<span style="background:#15803d;color:#fff;padding:2px 7px;border-radius:6px;font-size:11px">APPLIED</span>'
        elif status == "REQUIRES_MANUAL":
            status_badge = '<span style="background:#b45309;color:#fff;padding:2px 7px;border-radius:6px;font-size:11px">EXTERNAL</span>'
        elif status == "EMAIL_SENT":
            status_badge = '<span style="background:#7c3aed;color:#fff;padding:2px 7px;border-radius:6px;font-size:11px">EMAIL SENT</span>'
        else:
            status_badge = '<span style="background:#334155;color:#94a3b8;padding:2px 7px;border-radius:6px;font-size:11px">FOUND</span>'

        url = r.get("url") or "#"
        ts  = (str(r.get("created_at") or ""))[:16].replace("T", " ")
        emails = r.get("recruiter_emails") or ""
        email_display = f'<span style="color:#a855f7">{emails}</span>' if emails else '<span style="color:#64748b">-</span>'

        all_jobs_rows += f"""
        <tr class="job-row" data-plat="{(r['platform'] or '').lower()}" data-search="{(r['title'] or '').lower()} {(r['company'] or '').lower()} {(r.get('location') or '').lower()}">
          <td><a href="{url}" target="_blank" style="color:#60a5fa;text-decoration:none;font-weight:600">{r["title"]}</a></td>
          <td style="color:#f1f5f9">{r["company"]}</td>
          <td><span class="badge">{(r["platform"] or "").upper()}</span></td>
          <td style="color:#cbd5e1">{r.get("location") or "-"}</td>
          <td>{score_badge}</td>
          <td>{status_badge}</td>
          <td style="font-size:12px">{email_display}</td>
          <td style="color:#94a3b8;font-size:12px">{ts}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>JobPilot Dashboard - Hitesh Choudhary</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#e2e8f0;font-family:"Segoe UI",system-ui,sans-serif;padding-bottom:50px}}
.topbar{{background:linear-gradient(135deg,#1e40af,#7c3aed);padding:20px 32px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 4px 20px rgba(0,0,0,.3)}}
.topbar h1{{font-size:22px;font-weight:700}}
.topbar .sub{{font-size:13px;opacity:.85;margin-top:4px}}
.btn-group{{display:flex;gap:10px}}
.btn{{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13.5px;text-decoration:none;display:inline-flex;align-items:center;transition:all .2s}}
.btn:hover{{background:rgba(255,255,255,.28)}}
.main{{padding:28px 32px;max-width:1400px;margin:auto}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:28px}}
.card{{background:#1e293b;border-radius:14px;padding:20px 22px;border:1px solid #334155;position:relative;overflow:hidden}}
.card::before{{content:"";position:absolute;top:0;left:0;right:0;height:3px}}
.card.blue::before{{background:linear-gradient(90deg,#3b82f6,#06b6d4)}}
.card.green::before{{background:linear-gradient(90deg,#22c55e,#10b981)}}
.card.purple::before{{background:linear-gradient(90deg,#a855f7,#6366f1)}}
.card.orange::before{{background:linear-gradient(90deg,#f59e0b,#ef4444)}}
.card.teal::before{{background:linear-gradient(90deg,#14b8a6,#06b6d4)}}
.card-label{{font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}}
.card-value{{font-size:38px;font-weight:700;line-height:1}}
.today-highlight{{color:#22c55e}}
.card-sub{{font-size:12px;color:#64748b;margin-top:6px}}
.charts{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin-bottom:32px}}
.chart-box{{background:#1e293b;border-radius:14px;padding:20px;border:1px solid #334155}}
.chart-box.wide{{grid-column:span 2}}
.chart-title{{font-size:14px;font-weight:600;color:#cbd5e1;margin-bottom:16px}}
.table-box{{background:#1e293b;border-radius:14px;padding:22px;border:1px solid #334155;margin-bottom:32px}}
.table-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px}}
.table-title{{font-size:17px;font-weight:700;color:#f1f5f9}}
.search-input{{background:#0f172a;border:1px solid #475569;color:#fff;padding:8px 14px;border-radius:8px;font-size:13.5px;min-width:280px}}
.search-input:focus{{outline:none;border-color:#60a5fa}}
.filter-btn{{background:#0f172a;border:1px solid #475569;color:#94a3b8;padding:7px 14px;border-radius:8px;cursor:pointer;font-size:13px;margin-left:6px}}
.filter-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6}}
.table-wrapper{{overflow-x:auto;max-height:550px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:#94a3b8;font-weight:600;padding:10px 12px;border-bottom:2px solid #334155;font-size:12px;text-transform:uppercase;letter-spacing:.6px;position:sticky;top:0;background:#1e293b;z-index:2}}
td{{padding:11px 12px;border-bottom:1px solid #283548;vertical-align:middle}}
tr:hover td{{background:#263348}}
.badge{{background:#334155;color:#94a3b8;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}}
.footer{{text-align:center;color:#475569;font-size:12px;padding:20px}}
@media(max-width:900px){{.charts{{grid-template-columns:1fr}}.chart-box.wide{{grid-column:span 1}}}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>JobPilot Dashboard</h1>
    <div class="sub">Hitesh Choudhary &nbsp;&middot;&nbsp; {today_str}</div>
  </div>
  <div class="btn-group">
    <a href="discovered_jobs.csv" download class="btn" title="Download Excel CSV">Download CSV</a>
    <button class="btn" onclick="window.location.reload()">Refresh</button>
  </div>
</div>
<div class="main">

  <div class="cards">
    <div class="card green">
      <div class="card-label">Applied Today</div>
      <div class="card-value today-highlight">{applied_today}</div>
      <div class="card-sub">jobs applied today</div>
    </div>
    <div class="card blue">
      <div class="card-label">Total Applications</div>
      <div class="card-value">{total_applied}</div>
      <div class="card-sub">all time</div>
    </div>
    <div class="card purple">
      <div class="card-label">Jobs Discovered</div>
      <div class="card-value">{total_discovered}</div>
      <div class="card-sub">in database</div>
    </div>
    <div class="card orange">
      <div class="card-label">Cold Emails Sent</div>
      <div class="card-value">{total_emails}</div>
      <div class="card-sub">{emails_today} today</div>
    </div>
    <div class="card teal">
      <div class="card-label">Platforms Active</div>
      <div class="card-value">{plat_count}</div>
      <div class="card-sub">LinkedIn &middot; Naukri</div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-box wide">
      <div class="chart-title">Applications Per Day (Last 14 Days)</div>
      <canvas id="dailyChart" height="110"></canvas>
    </div>
    <div class="chart-box">
      <div class="chart-title">By Platform</div>
      <canvas id="platformChart" height="180"></canvas>
    </div>
    <div class="chart-box wide">
      <div class="chart-title">Top Companies Applied To</div>
      <canvas id="companyChart" height="120"></canvas>
    </div>
    <div class="chart-box">
      <div class="chart-title">Application Status Mix</div>
      <canvas id="statusChart" height="180"></canvas>
    </div>
  </div>

  <!-- ALL DISCOVERED JOBS EXPLORER -->
  <div class="table-box">
    <div class="table-header">
      <div>
        <div class="table-title">All Discovered Jobs in Database ({total_discovered} Total)</div>
        <div style="font-size:12px;color:#94a3b8;margin-top:2px">Filter, search, or click any job title to open the original job listing.</div>
      </div>
      <div>
        <input type="text" id="jobSearch" class="search-input" placeholder="Search role, company, or city..." onkeyup="filterTable()">
        <button class="filter-btn active" onclick="setPlatFilter('all', this)">All</button>
        <button class="filter-btn" onclick="setPlatFilter('linkedin', this)">LinkedIn</button>
        <button class="filter-btn" onclick="setPlatFilter('naukri', this)">Naukri</button>
      </div>
    </div>
    <div class="table-wrapper">
      <table id="jobsTable">
        <thead>
          <tr>
            <th>Role Title</th><th>Company</th><th>Platform</th><th>Location</th>
            <th>Fit Score</th><th>Status</th><th>Recruiter Email</th><th>Discovered</th>
          </tr>
        </thead>
        <tbody>
          {all_jobs_rows}
        </tbody>
      </table>
    </div>
  </div>

</div>

<div class="footer">Generated at {generated_at} &nbsp;&middot;&nbsp; JobPilot Database: tracker.db ({total_discovered} jobs)</div>

<script>
const B="#3b82f6",G="#22c55e",P="#a855f7",O="#f59e0b",T="#14b8a6";
Chart.defaults.color="#94a3b8";
Chart.defaults.borderColor="rgba(51,65,85,0.6)";

new Chart(document.getElementById("dailyChart"),{{
  type:"bar",
  data:{{labels:{daily_labels},datasets:[{{label:"Applications",data:{daily_data},backgroundColor:B+"cc",borderRadius:6,borderSkipped:false}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}}}},y:{{beginAtZero:true,ticks:{{stepSize:1}}}}}}}}
}});

new Chart(document.getElementById("platformChart"),{{
  type:"doughnut",
  data:{{labels:{platform_labels},datasets:[{{data:{platform_data},backgroundColor:[B,G,P,O],borderWidth:0,hoverOffset:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:"bottom",labels:{{padding:14}}}}}}}}
}});

new Chart(document.getElementById("companyChart"),{{
  type:"bar",
  data:{{labels:{top_co_labels},datasets:[{{label:"Apps",data:{top_co_data},backgroundColor:P+"cc",borderRadius:5,borderSkipped:false}}]}},
  options:{{indexAxis:"y",responsive:true,plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,ticks:{{stepSize:1}}}},y:{{grid:{{display:false}}}}}}}}
}});

new Chart(document.getElementById("statusChart"),{{
  type:"pie",
  data:{{labels:["Easy Apply","Cold Email"],datasets:[{{data:[{easy_count},{total_emails}],backgroundColor:[G,O],borderWidth:0,hoverOffset:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:"bottom",labels:{{padding:14}}}}}}}}
}});

let currentPlat = 'all';

function setPlatFilter(plat, btn) {{
  currentPlat = plat;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterTable();
}}

function filterTable() {{
  const query = document.getElementById("jobSearch").value.toLowerCase().trim();
  const rows = document.querySelectorAll(".job-row");

  rows.forEach(r => {{
    const plat = r.getAttribute("data-plat");
    const searchData = r.getAttribute("data-search");

    const matchesPlat = (currentPlat === 'all') || (plat === currentPlat);
    const matchesQuery = !query || searchData.includes(query);

    r.style.display = (matchesPlat && matchesQuery) ? "" : "none";
  }});
}}
</script>
</body>
</html>"""

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}. Run: python main.py run")
        exit(1)
    data = fetch_data()
    html = build_html(data)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    abs_path = os.path.abspath(OUT_PATH)
    print(f"Dashboard updated with all {data['total_discovered']} discovered jobs!")
    print(f"CSV exported: {os.path.abspath(CSV_PATH)}")
    webbrowser.open(f"file:///{abs_path}")