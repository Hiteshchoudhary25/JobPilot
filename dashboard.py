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

    c.execute("SELECT COUNT(*) as cnt FROM applications WHERE DATE(applied_at) = ? AND status = 'APPLIED_EASY'", (today,))
    applied_today = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM applications WHERE status = 'APPLIED_EASY'")
    total_easy_applied = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM applications WHERE status = 'REQUIRES_MANUAL'")
    total_queued_external = c.fetchone()["cnt"]

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
        SELECT company, COUNT(*) as cnt FROM applications
        GROUP BY company ORDER BY cnt DESC LIMIT 8
    """)
    top_companies = [dict(r) for r in c.fetchall()]

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

    # Export to CSV
    try:
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Platform", "Role Title", "Company", "Location", "Fit Score", "Application Status", "Recruiter Emails", "Job URL", "Discovered At"])
            for j in all_discovered:
                writer.writerow([
                    j["platform"].upper(),
                    j["title"],
                    j["company"],
                    j["location"],
                    f"{j['fit_score']}%" if j['fit_score'] else "-",
                    j["app_status"] or "FOUND",
                    j["recruiter_emails"] or "-",
                    j["url"],
                    str(j["created_at"])[:19]
                ])
    except Exception as e:
        print(f"CSV export notice: {e}")

    return {
        "applied_today": applied_today,
        "total_easy_applied": total_easy_applied,
        "total_queued_external": total_queued_external,
        "total_discovered": total_discovered,
        "total_emails": total_emails,
        "emails_today": emails_today,
        "by_platform": by_platform,
        "daily_labels": daily_labels,
        "daily_counts": daily_counts,
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

    applied_today         = d["applied_today"]
    total_easy_applied    = d["total_easy_applied"]
    total_queued_external = d["total_queued_external"]
    total_discovered      = d["total_discovered"]
    total_emails          = d["total_emails"]
    emails_today          = d["emails_today"]
    today_str             = d["today_str"]
    generated_at          = d["generated_at"]

    all_jobs_rows = ""
    for idx, r in enumerate(d["all_discovered"], 1):
        score = r.get("fit_score")
        if isinstance(score, int):
            color = "#22c55e" if score >= 70 else "#f59e0b" if score >= 50 else "#ef4444"
            score_badge = f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:9999px;font-size:12px">{score}%</span>'
        else:
            score_badge = '<span style="color:#888">-</span>'

        raw_status = r.get("app_status") or "FOUND"
        safe_company = (r.get("company") or "").replace("'", "\\'").replace('"', '&quot;')
        jid = str(r.get("id") or "")
        if raw_status == "APPLIED_EASY":
            status_key = "applied"
            status_badge = '<span style="background:#15803d;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600">APPLIED</span>'
            action_btn = '<span style="color:#22c55e;font-size:12px;font-weight:600">✓ Applied</span>'
        elif raw_status == "REQUIRES_MANUAL":
            status_key = "external"
            status_badge = '<span style="background:#b45309;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600" title="Click link to apply on company site">QUEUED (EXTERNAL)</span>'
            action_btn = f'<button class="mark-btn" onclick="markJobApplied(\'{jid}\', \'{idx}\', \'{safe_company}\', this)">✓ Mark Applied</button>'
        elif raw_status == "EMAIL_SENT":
            status_key = "email"
            status_badge = '<span style="background:#7c3aed;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600">EMAIL SENT</span>'
            action_btn = f'<button class="mark-btn" onclick="markJobApplied(\'{jid}\', \'{idx}\', \'{safe_company}\', this)">✓ Mark Applied</button>'
        else:
            status_key = "found"
            status_badge = '<span style="background:#334155;color:#94a3b8;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600">FOUND</span>'
            action_btn = f'<button class="mark-btn" onclick="markJobApplied(\'{jid}\', \'{idx}\', \'{safe_company}\', this)">✓ Mark Applied</button>'

        url = r.get("url") or "#"
        ts  = (str(r.get("created_at") or ""))[:16].replace("T", " ")
        emails = r.get("recruiter_emails") or ""
        email_display = f'<span style="color:#c084fc;font-weight:500">{emails}</span>' if emails else '<span style="color:#64748b">-</span>'

        all_jobs_rows += f"""
        <tr class="job-row" id="row_{r['id']}" data-jobid="{r['id']}" data-plat="{(r['platform'] or '').lower()}" data-status="{status_key}" data-search="{(r['title'] or '').lower()} {(r['company'] or '').lower()} {(r.get('location') or '').lower()}">
          <td style="color:#94a3b8;font-weight:700;text-align:center;font-size:12px">{idx}</td>
          <td><a href="{url}" target="_blank" style="color:#60a5fa;text-decoration:none;font-weight:600">{r["title"]}</a></td>
          <td style="color:#f1f5f9;font-weight:500">{r["company"]}</td>
          <td><span class="badge">{(r["platform"] or "").upper()}</span></td>
          <td style="color:#cbd5e1">{r.get("location") or "-"}</td>
          <td>{score_badge}</td>
          <td class="status-cell">{status_badge}</td>
          <td style="font-size:12px">{email_display}</td>
          <td style="color:#94a3b8;font-size:12px">{ts}</td>
          <td class="action-cell" style="text-align:center">{action_btn}</td>
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
.mark-btn{{background:#1e293b;border:1px solid #10b981;color:#34d399;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;transition:all .2s;white-space:nowrap}}
.mark-btn:hover{{background:#10b981;color:#fff;box-shadow:0 2px 8px rgba(16,185,129,.4)}}
.main{{padding:28px 32px;max-width:1400px;margin:auto}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:28px}}
.card{{background:#1e293b;border-radius:14px;padding:20px 22px;border:1px solid #334155;position:relative;overflow:hidden}}
.card::before{{content:"";position:absolute;top:0;left:0;right:0;height:3px}}
.card.green::before{{background:linear-gradient(90deg,#22c55e,#10b981)}}
.card.blue::before{{background:linear-gradient(90deg,#3b82f6,#06b6d4)}}
.card.amber::before{{background:linear-gradient(90deg,#f59e0b,#ea580c)}}
.card.purple::before{{background:linear-gradient(90deg,#a855f7,#6366f1)}}
.card.orange::before{{background:linear-gradient(90deg,#f97316,#ef4444)}}
.card-label{{font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}}
.card-value{{font-size:38px;font-weight:700;line-height:1}}
.card-sub{{font-size:12px;color:#64748b;margin-top:6px}}
.today-val{{color:#22c55e}}
.applied-val{{color:#38bdf8}}
.queued-val{{color:#fbbf24}}
.discovered-val{{color:#c084fc}}
.email-val{{color:#fb923c}}
.charts{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin-bottom:32px}}
.chart-box{{background:#1e293b;border-radius:14px;padding:20px;border:1px solid #334155}}
.chart-box.wide{{grid-column:span 2}}
.chart-title{{font-size:14px;font-weight:600;color:#cbd5e1;margin-bottom:16px}}
.table-box{{background:#1e293b;border-radius:14px;padding:22px;border:1px solid #334155;margin-bottom:32px}}
.table-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px}}
.table-title{{font-size:17px;font-weight:700;color:#f1f5f9}}
.search-input{{background:#0f172a;border:1px solid #475569;color:#fff;padding:8px 14px;border-radius:8px;font-size:13.5px;min-width:260px}}
.search-input:focus{{outline:none;border-color:#60a5fa}}
.filter-row{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}}
.filter-btn{{background:#0f172a;border:1px solid #475569;color:#94a3b8;padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12.5px}}
.filter-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6}}
.table-wrapper{{overflow-x:auto;max-height:580px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:#94a3b8;font-weight:600;padding:10px 12px;border-bottom:2px solid #334155;font-size:12px;text-transform:uppercase;letter-spacing:.6px;position:sticky;top:0;background:#1e293b;z-index:2}}
td{{padding:11px 12px;border-bottom:1px solid #283548;vertical-align:middle}}
tr:hover td{{background:#263348}}
.badge{{background:#334155;color:#94a3b8;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}}
.footer{{text-align:center;color:#475569;font-size:12px;padding:20px}}
#toast{{position:fixed;bottom:24px;right:24px;background:#10b981;color:#fff;padding:12px 20px;border-radius:10px;font-weight:600;font-size:13.5px;box-shadow:0 4px 16px rgba(0,0,0,0.4);z-index:9999;transition:opacity 0.3s ease;opacity:0;pointer-events:none}}
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
    <a href="discovered_jobs.csv" download class="btn">Download CSV</a>
    <button class="btn" onclick="window.location.reload()">Refresh</button>
  </div>
</div>
<div class="main">

  <div class="cards">
    <div class="card green">
      <div class="card-label">Applied Today</div>
      <div class="card-value today-val">{applied_today}</div>
      <div class="card-sub">1-Click applied today</div>
    </div>
    <div class="card blue">
      <div class="card-label">Direct Applied</div>
      <div class="card-value applied-val">{total_easy_applied}</div>
      <div class="card-sub">submitted via 1-click apply</div>
    </div>
    <div class="card amber">
      <div class="card-label">Queued (External)</div>
      <div class="card-value queued-val">{total_queued_external}</div>
      <div class="card-sub">needs company site form</div>
    </div>
    <div class="card purple">
      <div class="card-label">Total Discovered</div>
      <div class="card-value discovered-val">{total_discovered}</div>
      <div class="card-sub">jobs in database</div>
    </div>
    <div class="card orange">
      <div class="card-label">Cold Emails Sent</div>
      <div class="card-value email-val">{total_emails}</div>
      <div class="card-sub">{emails_today} today</div>
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
        <div class="table-title">All Tracked Jobs in Database ({total_discovered} Total)</div>
        <div style="font-size:12px;color:#94a3b8;margin-top:2px">Click any role to open the live listing. Use tabs below to see Queued/External vs 1-Click Applied.</div>
      </div>
      <div>
        <input type="text" id="jobSearch" class="search-input" placeholder="Search title, company, or city..." onkeyup="filterTable()">
      </div>
    </div>

    <!-- Filter Buttons -->
    <div class="filter-row">
      <span style="font-size:12px;color:#94a3b8;display:flex;align-items:center;margin-right:4px">Status:</span>
      <button class="filter-btn active status-btn" onclick="setStatusFilter('all', this)">All ({total_discovered})</button>
      <button class="filter-btn status-btn" onclick="setStatusFilter('applied', this)">Applied ({total_easy_applied})</button>
      <button class="filter-btn status-btn" onclick="setStatusFilter('external', this)" style="border-color:#b45309;color:#f59e0b">Queued External ({total_queued_external})</button>
      <button class="filter-btn status-btn" onclick="setStatusFilter('email', this)">Email Sent ({total_emails})</button>
      <button class="filter-btn status-btn" onclick="setStatusFilter('found', this)">Found</button>
      
      <span style="font-size:12px;color:#94a3b8;display:flex;align-items:center;margin-left:14px;margin-right:4px">Platform:</span>
      <button class="filter-btn active plat-btn" onclick="setPlatFilter('all', this)">All</button>
      <button class="filter-btn plat-btn" onclick="setPlatFilter('linkedin', this)">LinkedIn</button>
      <button class="filter-btn plat-btn" onclick="setPlatFilter('naukri', this)">Naukri</button>
    </div>

    <div class="table-wrapper" style="margin-top:14px">
      <table id="jobsTable">
        <thead>
          <tr>
            <th style="width:40px;text-align:center">#</th>
            <th>Role Title</th><th>Company</th><th>Platform</th><th>Location</th>
            <th>Fit Score</th><th>Status</th><th>Recruiter Email</th><th>Discovered</th>
            <th style="width:130px;text-align:center">Action</th>
          </tr>
        </thead>
        <tbody>
          {all_jobs_rows}
        </tbody>
      </table>
    </div>
  </div>

</div>

<div id="toast"></div>

<div class="footer">Generated at {generated_at} &nbsp;&middot;&nbsp; JobPilot Database: tracker.db ({total_discovered} jobs)</div>

<script>
const B="#38bdf8",G="#22c55e",P="#c084fc",O="#fb923c",Y="#fbbf24";
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
  data:{{labels:["1-Click Applied","Queued External","Email Sent"],datasets:[{{data:[{total_easy_applied},{total_queued_external},{total_emails}],backgroundColor:[G,Y,O],borderWidth:0,hoverOffset:6}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:"bottom",labels:{{padding:14}}}}}}}}
}});

let currentPlat = 'all';
let currentStatus = 'all';

function setPlatFilter(plat, btn) {{
  currentPlat = plat;
  document.querySelectorAll('.plat-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterTable();
}}

function setStatusFilter(status, btn) {{
  currentStatus = status;
  document.querySelectorAll('.status-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterTable();
}}

function filterTable() {{
  const query = document.getElementById("jobSearch").value.toLowerCase().trim();
  const rows = document.querySelectorAll(".job-row");

  rows.forEach(r => {{
    const plat = r.getAttribute("data-plat");
    const status = r.getAttribute("data-status");
    const searchData = r.getAttribute("data-search");

    const matchesPlat = (currentPlat === 'all') || (plat === currentPlat);
    const matchesStatus = (currentStatus === 'all') || (status === currentStatus);
    const matchesQuery = !query || searchData.includes(query);

    r.style.display = (matchesPlat && matchesStatus && matchesQuery) ? "" : "none";
  }});
}}

function markJobApplied(jobId, rowNum, company, btn) {{
  const row = document.getElementById('row_' + jobId) || btn.closest('.job-row');
  if (!row) return;

  const prevStatus = row.getAttribute('data-status');
  if (prevStatus === 'applied') return;

  // 1. Update UI Status Badge
  const statusCell = row.querySelector('.status-cell');
  if (statusCell) {{
    statusCell.innerHTML = '<span style="background:#15803d;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600">APPLIED</span>';
  }}

  // 2. Update Action Cell
  const actionCell = row.querySelector('.action-cell');
  if (actionCell) {{
    actionCell.innerHTML = '<span style="color:#22c55e;font-size:12px;font-weight:600">✓ Applied</span>';
  }}

  // 3. Update data-status
  row.setAttribute('data-status', 'applied');

  // 4. Update KPI numbers dynamically
  const appliedEl = document.querySelector('.applied-val');
  if (appliedEl) appliedEl.textContent = parseInt(appliedEl.textContent || '0') + 1;
  const todayEl = document.querySelector('.today-val');
  if (todayEl) todayEl.textContent = parseInt(todayEl.textContent || '0') + 1;
  if (prevStatus === 'external') {{
    const queuedEl = document.querySelector('.queued-val');
    if (queuedEl) queuedEl.textContent = Math.max(0, parseInt(queuedEl.textContent || '0') - 1);
  }}

  // 5. Store in localStorage so it persists in browser
  localStorage.setItem('job_applied_' + jobId, 'true');

  // 6. Send sync request to local API (updates tracker.db)
  const payload = JSON.stringify({{ job_id: jobId, status: 'APPLIED_EASY' }});
  fetch('/api/update-status', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: payload
  }}).catch(() => {{
    fetch('http://127.0.0.1:8000/api/update-status', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: payload
    }}).catch(() => {{}});
  }});

  // 7. Show Toast
  showToast('Job #' + rowNum + ' (' + company + ') marked as Applied!');
}}

function showToast(msg) {{
  let t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.style.opacity = '1';
  setTimeout(() => {{ t.style.opacity = '0'; }}, 3200);
}}

// Reconcile localStorage overrides on page load
window.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('.job-row').forEach(row => {{
    const jobId = row.getAttribute('data-jobid');
    if (localStorage.getItem('job_applied_' + jobId) === 'true') {{
      const statusCell = row.querySelector('.status-cell');
      if (statusCell) {{
        statusCell.innerHTML = '<span style="background:#15803d;color:#fff;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600">APPLIED</span>';
      }}
      const actionCell = row.querySelector('.action-cell');
      if (actionCell) {{
        actionCell.innerHTML = '<span style="color:#22c55e;font-size:12px;font-weight:600">✓ Applied</span>';
      }}
      row.setAttribute('data-status', 'applied');
    }}
  }});
}});
</script>
</body>
</html>"""

def generate_dashboard(auto_open=True):
    if not os.path.exists(DB_PATH):
        return
    data = fetch_data()
    html = build_html(data)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    abs_path = os.path.abspath(OUT_PATH)
    if auto_open:
        print(f"Dashboard updated: {abs_path}")
        print(f"CSV exported: {os.path.abspath(CSV_PATH)}")
        webbrowser.open(f"file:///{abs_path}")
    else:
        print(f"[Dashboard automatically refreshed: {abs_path}]")

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

class DashboardHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/api/update-status":
            try:
                length = int(self.headers.get("content-length", 0))
                raw_body = self.rfile.read(length).decode("utf-8")
                body = json.loads(raw_body)
                job_id = body.get("job_id")
                from core.db import Database
                db = Database(DB_PATH)
                success = db.mark_job_applied(job_id)
                generate_dashboard(auto_open=False)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": success}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def serve_dashboard(port=8000):
    generate_dashboard(auto_open=False)
    server_address = ("127.0.0.1", port)
    server = ThreadingHTTPServer(server_address, DashboardHandler)
    url = f"http://127.0.0.1:{port}/dashboard.html"
    print(f"\n=======================================================")
    print(f"  JobPilot Live Dashboard Server Running!")
    print(f"  URL: {url}")
    print(f"  Status changes made in the dashboard sync directly with tracker.db")
    print(f"  Press Ctrl+C to stop the server.")
    print(f"=======================================================\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        server.server_close()

if __name__ == "__main__":
    import sys
    if "--serve" in sys.argv:
        serve_dashboard()
    else:
        generate_dashboard(auto_open=True)