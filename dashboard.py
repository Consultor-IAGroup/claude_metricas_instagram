"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
import os
import sqlite3
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".claude" / "usage.db"


def get_dashboard_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python cli.py scan"}

    # ── Stats-cache: authoritative source for all token/session totals ────────
    sc_by_model = []   # [{model, input, output, cache_read, cache_creation}]
    sc_daily    = []   # [{day, model, tokens}]  tokens = input+output combined
    sc_sessions = 0
    sc_messages = 0
    if STATS_CACHE_PATH.exists():
        with open(STATS_CACHE_PATH, encoding="utf-8") as f:
            sc_raw = json.load(f)
        mu = sc_raw.get("modelUsage", {})
        sc_by_model = sorted([
            {
                "model":          m,
                "input":          v.get("inputTokens", 0),
                "output":         v.get("outputTokens", 0),
                "cache_read":     v.get("cacheReadInputTokens", 0),
                "cache_creation": v.get("cacheCreationInputTokens", 0),
            }
            for m, v in mu.items()
        ], key=lambda x: x["output"], reverse=True)
        sc_daily = [
            {"day": day["date"], "model": model, "tokens": tokens}
            for day in sc_raw.get("dailyModelTokens", [])
            for model, tokens in day.get("tokensByModel", {}).items()
        ]
        sc_sessions = sc_raw.get("totalSessions", 0)
        sc_messages = sc_raw.get("totalMessages", 0)

    # ── All models for filter UI (union of sc + DB) ───────────────────────────
    all_models = [m["model"] for m in sc_by_model] if sc_by_model else []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── All sessions from DB (used only for the detail session table) ─────────
    session_rows = conn.execute("""
        SELECT
            session_id, project_name, first_timestamp, last_timestamp,
            total_input_tokens, total_output_tokens,
            total_cache_read, total_cache_creation, model, turn_count
        FROM sessions
        ORDER BY last_timestamp DESC
    """).fetchall()

    sessions_all = []
    for r in session_rows:
        try:
            t1 = datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(r["last_timestamp"].replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0
        sessions_all.append({
            "session_id":    r["session_id"][:8],
            "project":       r["project_name"] or "unknown",
            "last":          (r["last_timestamp"] or "")[:16].replace("T", " "),
            "last_date":     (r["last_timestamp"] or "")[:10],
            "duration_min":  duration_min,
            "model":         r["model"] or "unknown",
            "turns":         r["turn_count"] or 0,
            "input":         r["total_input_tokens"] or 0,
            "output":        r["total_output_tokens"] or 0,
            "cache_read":    r["total_cache_read"] or 0,
            "cache_creation": r["total_cache_creation"] or 0,
        })

    conn.close()

    return {
        "all_models":   all_models,
        "sc_by_model":  sc_by_model,
        "sc_daily":     sc_daily,
        "sc_sessions":  sc_sessions,
        "sc_messages":  sc_messages,
        "sessions_all": sessions_all,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


STATS_CACHE_PATH = Path.home() / ".claude" / "stats-cache.json"


def _find_git_repos(since_date: str) -> list[str]:
    """Find git repos used in Claude Code sessions (from DB cwd field)."""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT DISTINCT cwd FROM turns WHERE cwd IS NOT NULL AND cwd != ''"
        ).fetchall()
        conn.close()
    except Exception:
        return []

    repos = set()
    for (cwd,) in rows:
        p = Path(cwd)
        while p != p.parent:
            if (p / ".git").exists():
                repos.add(str(p))
                break
            p = p.parent
    return sorted(repos)


def get_git_stats() -> dict:
    """Aggregate git commits and lines added/deleted across known repos."""
    if not STATS_CACHE_PATH.exists():
        return {"commits": 0, "lines_added": 0, "lines_deleted": 0, "repos": []}

    with open(STATS_CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    since = (cache.get("firstSessionDate") or "2025-01-01")[:10]

    repos = _find_git_repos(since)
    total_commits = 0
    total_added = 0
    total_deleted = 0

    for repo in repos:
        try:
            # Count commits by any author since first session
            out = subprocess.check_output(
                ["git", "log", "--oneline", f"--since={since}"],
                cwd=repo, stderr=subprocess.DEVNULL, text=True
            )
            total_commits += len([l for l in out.splitlines() if l.strip()])

            # Count lines added/deleted
            out2 = subprocess.check_output(
                ["git", "log", "--numstat", f"--since={since}", "--format="],
                cwd=repo, stderr=subprocess.DEVNULL, text=True
            )
            for line in out2.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                    total_added   += int(parts[0])
                    total_deleted += int(parts[1])
        except Exception:
            continue

    return {
        "commits":       total_commits,
        "lines_added":   total_added,
        "lines_deleted": total_deleted,
        "repos":         repos,
    }

# Harry Potter e a Pedra Filosofal — ~103k tokens (edição original UK, ~77k palavras)
HP1_TOKENS = 103_000


def _fun_phrase(total_tokens):
    ratio = round(total_tokens / HP1_TOKENS)
    return f"Você usou ~{ratio:,}× mais tokens do que Harry Potter e a Pedra Filosofal."


def _streak(date_set):
    from datetime import date, timedelta
    today = date.today()
    cur = 0
    d = today
    while str(d) in date_set:
        cur += 1
        d -= timedelta(days=1)
    if cur == 0:
        d = today - timedelta(days=1)
        while str(d) in date_set:
            cur += 1
            d -= timedelta(days=1)
    return cur


def _longest_streak(date_set):
    from datetime import date, timedelta
    if not date_set:
        return 0
    mx = cur = 1
    prev = None
    for dt_str in sorted(date_set):
        dt = date.fromisoformat(dt_str)
        if prev and (dt - prev).days == 1:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 1
        prev = dt
    return mx


def get_stats_cache_data():
    if not STATS_CACHE_PATH.exists():
        return {"error": "stats-cache.json not found"}

    with open(STATS_CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    daily_activity  = cache.get("dailyActivity", [])
    daily_tokens    = cache.get("dailyModelTokens", [])
    model_usage     = cache.get("modelUsage", {})
    hour_counts     = cache.get("hourCounts", {})

    date_set = set(d["date"] for d in daily_activity)

    # Token totals (input+output per day, per model from dailyModelTokens)
    total_tokens = sum(
        t for day in daily_tokens
        for t in day.get("tokensByModel", {}).values()
    )

    # Favorite model by output tokens
    fav_model = ""
    if model_usage:
        fav_model = max(model_usage, key=lambda m: model_usage[m].get("outputTokens", 0))

    # Peak hour
    peak_hour = ""
    if hour_counts:
        peak_hour = max(hour_counts, key=lambda h: hour_counts[h])

    # Daily heatmap data: {date: total_tokens}
    heatmap = {}
    for day in daily_tokens:
        d = day["date"]
        heatmap[d] = sum(day.get("tokensByModel", {}).values())

    git = get_git_stats()
    active_days = len(date_set)

    # Total session hours from DB (sum of all session durations, counting paralelas)
    total_hours = 0
    if DB_PATH.exists():
        try:
            conn2 = sqlite3.connect(DB_PATH)
            session_rows = conn2.execute(
                "SELECT first_timestamp, last_timestamp FROM sessions "
                "WHERE first_timestamp IS NOT NULL AND last_timestamp IS NOT NULL"
            ).fetchall()
            conn2.close()
            for (t1s, t2s) in session_rows:
                try:
                    from datetime import datetime as _dt
                    t1 = _dt.fromisoformat(t1s.replace("Z", "+00:00"))
                    t2 = _dt.fromisoformat(t2s.replace("Z", "+00:00"))
                    total_hours += max(0, (t2 - t1).total_seconds()) / 3600
                except Exception:
                    pass
        except Exception:
            pass

    commits     = git["commits"]
    lines_added = git["lines_added"]
    commits_per_day = round(commits / active_days, 1) if active_days else 0
    lines_per_day   = int(lines_added / active_days) if active_days else 0

    return {
        "sessions":         cache.get("totalSessions", 0),
        "messages":         cache.get("totalMessages", 0),
        "total_tokens":     total_tokens,
        "active_days":      active_days,
        "streak":           _streak(date_set),
        "max_streak":       _longest_streak(date_set),
        "peak_hour":        peak_hour,
        "fav_model":        fav_model,
        "heatmap":          heatmap,
        "fun_phrase":       _fun_phrase(total_tokens),
        "first_date":       cache.get("firstSessionDate", "")[:10],
        "commits":          commits,
        "lines_added":      lines_added,
        "lines_deleted":    git["lines_deleted"],
        "total_hours":      round(total_hours),
        "commits_per_day":  commits_per_day,
        "lines_per_day":    lines_per_day,
        "hp_ratio":         round(total_tokens / HP1_TOKENS),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e8f0;
    --muted: #8892a4;
    --accent: #d97757;
    --blue: #4f8ef7;
    --green: #4ade80;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

  header { background: var(--card); border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; font-weight: 600; color: var(--accent); }
  header .meta { color: var(--muted); font-size: 12px; }
  #rescan-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; margin-top: 4px; }
  #rescan-btn:hover { color: var(--text); border-color: var(--accent); }
  #rescan-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  #filter-bar { background: var(--card); border-bottom: 1px solid var(--border); padding: 10px 24px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .filter-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); white-space: nowrap; }
  .filter-sep { width: 1px; height: 22px; background: var(--border); flex-shrink: 0; }
  #model-checkboxes { display: flex; flex-wrap: wrap; gap: 6px; }
  .model-cb-label { display: flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; border: 1px solid var(--border); cursor: pointer; font-size: 12px; color: var(--muted); transition: border-color 0.15s, color 0.15s, background 0.15s; user-select: none; }
  .model-cb-label:hover { border-color: var(--accent); color: var(--text); }
  .model-cb-label.checked { background: rgba(217,119,87,0.12); border-color: var(--accent); color: var(--text); }
  .model-cb-label input { display: none; }
  .filter-btn { padding: 3px 10px; border-radius: 4px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; white-space: nowrap; }
  .filter-btn:hover { border-color: var(--accent); color: var(--text); }
  .range-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; flex-shrink: 0; }
  .range-btn { padding: 4px 13px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 12px; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .range-btn:last-child { border-right: none; }
  .range-btn:hover { background: rgba(255,255,255,0.04); color: var(--text); }
  .range-btn.active { background: rgba(217,119,87,0.15); color: var(--accent); font-weight: 600; }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .stat-card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .stat-card .value { font-size: 22px; font-weight: 700; }
  .stat-card .sub { color: var(--muted); font-size: 11px; margin-top: 4px; }

  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 240px; }
  .chart-wrap.tall { height: 300px; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--text); }
  .sort-icon { font-size: 9px; opacity: 0.8; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .model-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; background: rgba(79,142,247,0.15); color: var(--blue); }
  .cost { color: var(--green); font-family: monospace; }
  .cost-na { color: var(--muted); font-family: monospace; font-size: 11px; }
  .num { font-family: monospace; }
  .muted { color: var(--muted); }
  .section-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-header .section-title { margin-bottom: 0; }
  .export-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 3px 10px; border-radius: 5px; cursor: pointer; font-size: 11px; }
  .export-btn:hover { color: var(--text); border-color: var(--accent); }
  .table-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 24px; overflow-x: auto; }

  footer { border-top: 1px solid var(--border); padding: 16px 24px; margin-top: 8px; }
  .footer-content { max-width: 1400px; margin: 0 auto; display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .footer-brand { display:flex; align-items:center; gap:9px; flex-shrink:0; }
  .footer-logo { height:26px; width:auto; }
  .footer-brand-text { font-size:11px; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; color:rgba(196,181,253,0.55); }
  .footer-meta { color: var(--muted); font-size: 11px; line-height: 1.6; }
  .footer-meta a { color: var(--muted); text-decoration: none; }
  .footer-meta a:hover { color: var(--text); }

  @media (max-width: 768px) { .charts-grid { grid-template-columns: 1fr; } .chart-card.wide { grid-column: 1; } }

  /* ── Overview panel ───────────────────────────────────────────────────── */
  #overview-panel { background: var(--card); border-bottom: 1px solid var(--border); }
  .ov-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 24px; border-bottom: 1px solid var(--border); }
  .tab-bar { display: flex; gap: 4px; }
  .tab-btn { background: transparent; border: none; padding: 6px 14px; border-radius: 6px; color: var(--muted); font-size: 13px; font-weight: 500; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .tab-btn:hover { background: rgba(255,255,255,0.05); color: var(--text); }
  .tab-btn.active { background: rgba(255,255,255,0.08); color: var(--text); font-weight: 600; }
  .ov-range-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .ov-range-btn { padding: 4px 14px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 12px; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .ov-range-btn:last-child { border-right: none; }
  .ov-range-btn:hover { background: rgba(255,255,255,0.04); color: var(--text); }
  .ov-range-btn.active { background: rgba(217,119,87,0.15); color: var(--accent); font-weight: 600; }

  .ov-body { max-width: 1400px; margin: 0 auto; padding: 20px 24px 16px; }
  .ov-stats-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 20px; }
  @media (max-width: 1100px) { .ov-stats-row { grid-template-columns: repeat(4, 1fr); } }
  @media (max-width: 800px)  { .ov-stats-row { grid-template-columns: repeat(2, 1fr); } }
  .ov-stat { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .ov-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .ov-value { font-size: 20px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* heatmap */
  #heatmap-container { overflow-x: auto; padding-bottom: 4px; }
  .heatmap-outer { display: flex; gap: 0; min-width: max-content; }
  .heatmap-day-labels { display: flex; flex-direction: column; gap: 0; margin-right: 6px; padding-top: 22px; }
  .heatmap-day-label { height: 14px; font-size: 10px; color: var(--muted); line-height: 14px; text-align: right; }
  .heatmap-day-label.hidden { visibility: hidden; }
  .heatmap-cols-wrap { display: flex; flex-direction: column; gap: 0; }
  .heatmap-month-row { display: flex; gap: 3px; margin-bottom: 4px; min-height: 16px; }
  .heatmap-month-label { font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; }
  .heatmap-cols { display: flex; gap: 3px; }
  .heatmap-col { display: flex; flex-direction: column; gap: 3px; }
  .heatmap-cell { width: 13px; height: 13px; border-radius: 2px; cursor: default; flex-shrink: 0; }
  .heatmap-cell:hover { outline: 1px solid rgba(255,255,255,0.35); }
  .hm-0 { background: #1a1d27; }
  .hm-1 { background: #2d1f5e; }
  .hm-2 { background: #3d2a7a; }
  .hm-3 { background: #5338a0; }
  .hm-4 { background: #7c5cc4; }
  .hm-5 { background: #a78bfa; }

  #fun-phrase { color: var(--muted); font-size: 12px; margin-top: 10px; font-style: italic; }

  /* ── Instagram cards ──────────────────────────────────────────────────── */
  .ig-card-wrap { position: relative; }
  .ig-dl-btn { position:absolute; top:10px; right:10px; background:rgba(255,255,255,0.12);
    border:1px solid rgba(255,255,255,0.18); color:#fff; border-radius:8px;
    padding:5px 10px; font-size:12px; cursor:pointer; z-index:10;
    backdrop-filter:blur(4px); transition:background 0.15s; }
  .ig-dl-btn:hover { background:rgba(255,255,255,0.22); }

  .ig-card { width:100%; aspect-ratio:1/1; border-radius:16px; overflow:hidden;
    position:relative; display:flex; flex-direction:column; align-items:center;
    justify-content:center; text-align:center; padding:40px 36px 68px;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }

  .ig-bg-dark    { background: linear-gradient(155deg,#07030f 0%,#0f0a1e 60%,#1a1133 100%); }
  .ig-bg-lavender{ background: linear-gradient(155deg,#06030e 0%,#0e0920 55%,#160d2e 100%); }
  .ig-bg-black   { background: #07030f; }
  .ig-bg-cover   { background: linear-gradient(155deg,#06030e 0%,#100c22 50%,#180f30 100%); }
  .ig-bg-amber   { background: linear-gradient(155deg,#0d0800 0%,#1a1000 55%,#261800 100%); }

  .ig-orb { position:absolute; border-radius:50%; filter:blur(60px); pointer-events:none; }

  .ig-num { font-size: clamp(64px,12vw,100px); font-weight:900; line-height:1;
    color:#c4b5fd; letter-spacing:-2px; }
  .ig-num-sm { font-size: clamp(48px,9vw,76px); font-weight:900; line-height:1;
    color:#c4b5fd; letter-spacing:-1px; }
  .ig-label { font-size: clamp(16px,2.8vw,22px); font-weight:700; color:#fff;
    margin-top:10px; letter-spacing:0.01em; }
  .ig-desc { font-size: clamp(12px,2vw,15px); color:rgba(255,255,255,0.55);
    margin-top:14px; line-height:1.55; max-width:340px; }
  .ig-tag { font-size:11px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase;
    color:rgba(255,255,255,0.3); margin-bottom:18px; }
  .ig-tag-bottom { font-size:11px; letter-spacing:0.1em; text-transform:uppercase;
    color:rgba(255,255,255,0.25); margin-top:22px; }

  .ig-pair { display:flex; gap:40px; align-items:flex-end; justify-content:center; }
  .ig-pair-item { display:flex; flex-direction:column; align-items:center; }
  .ig-divider { width:1px; height:80px; background:rgba(255,255,255,0.12); align-self:center; }

  .ig-insight-box { border:1px solid rgba(245,158,11,0.35); border-radius:16px;
    padding:28px 32px; background:rgba(245,158,11,0.06); max-width:440px; }
  .ig-insight-title { font-size:clamp(14px,2.4vw,18px); font-weight:700; color:#fbbf24;
    line-height:1.4; margin-bottom:14px; }
  .ig-insight-body { font-size:clamp(11px,1.7vw,13px); color:rgba(255,255,255,0.55);
    line-height:1.6; }

  .ig-cover-stats { display:flex; flex-direction:column; gap:4px; margin:20px 0; }
  .ig-cover-line { font-size:clamp(20px,4vw,32px); font-weight:900; color:#fff;
    letter-spacing:-0.5px; line-height:1.15; }
  .ig-cover-sub { font-size:clamp(12px,2vw,15px); color:rgba(255,255,255,0.5);
    margin-top:16px; font-style:italic; }

  .ig-footer { position:absolute; bottom:18px; left:22px; right:22px;
    display:flex; align-items:center; gap:9px; }
  .ig-logo { height:28px; width:auto; mix-blend-mode:screen; }
  .ig-brand { font-size:11px; font-weight:700; letter-spacing:0.1em;
    text-transform:uppercase; color:rgba(255,255,255,0.38); }
</style>
</head>
<body>
<header>
  <h1>Claude Code Usage Dashboard</h1>
  <div class="meta" id="meta">Loading...</div>
  <button id="rescan-btn" onclick="triggerRescan()" title="Rebuild the database from scratch by re-scanning all JSONL files. Use if data looks stale or costs seem wrong.">&#x21bb; Rescan</button>
</header>

<div id="overview-panel">
  <div class="ov-header">
    <div class="tab-bar">
      <button class="tab-btn active" id="tab-overview" onclick="switchTab('overview')">Visão Geral</button>
      <button class="tab-btn" id="tab-cards"   onclick="switchTab('cards')">Cards Instagram</button>
      <button class="tab-btn" id="tab-detail"  onclick="switchTab('detail')">Modelos</button>
    </div>
    <div class="ov-range-group">
      <button class="ov-range-btn active" data-ovrange="all" onclick="setOvRange('all')">Todos</button>
      <button class="ov-range-btn" data-ovrange="30d" onclick="setOvRange('30d')">30d</button>
      <button class="ov-range-btn" data-ovrange="7d" onclick="setOvRange('7d')">7d</button>
    </div>
  </div>
  <div id="ov-body" class="ov-body">
    <div class="ov-stats-row">
      <div class="ov-stat"><div class="ov-label">Sessões</div><div class="ov-value" id="ov-sessions">—</div></div>
      <div class="ov-stat"><div class="ov-label">Mensagens</div><div class="ov-value" id="ov-messages">—</div></div>
      <div class="ov-stat"><div class="ov-label">Total de tokens</div><div class="ov-value" id="ov-tokens">—</div></div>
      <div class="ov-stat"><div class="ov-label">Dias ativos</div><div class="ov-value" id="ov-days">—</div></div>
      <div class="ov-stat"><div class="ov-label">Sequência atual</div><div class="ov-value" id="ov-streak">—</div></div>
      <div class="ov-stat"><div class="ov-label">Maior sequência</div><div class="ov-value" id="ov-max-streak">—</div></div>
      <div class="ov-stat"><div class="ov-label">Horário de pico</div><div class="ov-value" id="ov-peak-hour">—</div></div>
      <div class="ov-stat"><div class="ov-label">Modelo favorito</div><div class="ov-value" id="ov-fav-model">—</div></div>
      <div class="ov-stat"><div class="ov-label">Commits</div><div class="ov-value" id="ov-commits">—</div></div>
      <div class="ov-stat"><div class="ov-label">Linhas de código</div><div class="ov-value" id="ov-lines">—</div></div>
    </div>
    <div id="heatmap-container"></div>
    <div id="fun-phrase"></div>
  </div>
</div>

<div id="cards-panel" style="display:none; max-width:1400px; margin:0 auto; padding:24px;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:20px;">
    <div>
      <div style="font-size:15px; font-weight:700; color:#e2e8f0;">Cards Instagram</div>
      <div style="font-size:12px; color:#8892a4; margin-top:3px;">7 cards prontos para postar. Clique em ⬇ para baixar cada um como PNG.</div>
    </div>
    <button onclick="downloadAllCards()" style="background:#4f8ef7;border:none;color:#fff;padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">⬇ Baixar todos</button>
  </div>
  <div id="ig-grid" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:20px;"></div>
</div>

<div id="detail-panel">
<div id="filter-bar">
  <div class="filter-label">Models</div>
  <div id="model-checkboxes"></div>
  <button class="filter-btn" onclick="selectAllModels()">All</button>
  <button class="filter-btn" onclick="clearAllModels()">None</button>
  <div class="filter-sep"></div>
  <div class="filter-label">Range</div>
  <div class="range-group">
    <button class="range-btn" data-range="7d"  onclick="setRange('7d')">7d</button>
    <button class="range-btn" data-range="30d" onclick="setRange('30d')">30d</button>
    <button class="range-btn" data-range="90d" onclick="setRange('90d')">90d</button>
    <button class="range-btn" data-range="all" onclick="setRange('all')">All</button>
  </div>
</div>

<div class="container">
  <div class="stats-row" id="stats-row"></div>
  <div class="charts-grid">
    <div class="chart-card wide">
      <h2 id="daily-chart-title">Daily Token Usage</h2>
      <div class="chart-wrap tall"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="chart-model"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Top Projects by Tokens</h2>
      <div class="chart-wrap"><canvas id="chart-project"></canvas></div>
    </div>
  </div>
  <div class="table-card">
    <div class="section-title">Cost by Model</div>
    <table>
      <thead><tr>
        <th>Model</th>
        <th class="sortable" onclick="setModelSort('turns')">Turns <span class="sort-icon" id="msort-turns"></span></th>
        <th class="sortable" onclick="setModelSort('input')">Input <span class="sort-icon" id="msort-input"></span></th>
        <th class="sortable" onclick="setModelSort('output')">Output <span class="sort-icon" id="msort-output"></span></th>
        <th class="sortable" onclick="setModelSort('cache_read')">Cache Read <span class="sort-icon" id="msort-cache_read"></span></th>
        <th class="sortable" onclick="setModelSort('cache_creation')">Cache Creation <span class="sort-icon" id="msort-cache_creation"></span></th>
        <th class="sortable" onclick="setModelSort('cost')">Est. Cost <span class="sort-icon" id="msort-cost"></span></th>
      </tr></thead>
      <tbody id="model-cost-body"></tbody>
    </table>
  </div>
  <div class="table-card">
    <div class="section-header"><div class="section-title">Recent Sessions</div><button class="export-btn" onclick="exportSessionsCSV()" title="Export all filtered sessions to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Session</th>
        <th>Project</th>
        <th class="sortable" onclick="setSessionSort('last')">Last Active <span class="sort-icon" id="sort-icon-last"></span></th>
        <th class="sortable" onclick="setSessionSort('duration_min')">Duration <span class="sort-icon" id="sort-icon-duration_min"></span></th>
        <th>Model</th>
        <th class="sortable" onclick="setSessionSort('turns')">Turns <span class="sort-icon" id="sort-icon-turns"></span></th>
        <th class="sortable" onclick="setSessionSort('input')">Input <span class="sort-icon" id="sort-icon-input"></span></th>
        <th class="sortable" onclick="setSessionSort('output')">Output <span class="sort-icon" id="sort-icon-output"></span></th>
        <th class="sortable" onclick="setSessionSort('cost')">Est. Cost <span class="sort-icon" id="sort-icon-cost"></span></th>
      </tr></thead>
      <tbody id="sessions-body"></tbody>
    </table>
  </div>
  <div class="table-card">
    <div class="section-header"><div class="section-title">Cost by Project</div><button class="export-btn" onclick="exportProjectsCSV()" title="Export all projects to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Project</th>
        <th class="sortable" onclick="setProjectSort('sessions')">Sessions <span class="sort-icon" id="psort-sessions"></span></th>
        <th class="sortable" onclick="setProjectSort('turns')">Turns <span class="sort-icon" id="psort-turns"></span></th>
        <th class="sortable" onclick="setProjectSort('input')">Input <span class="sort-icon" id="psort-input"></span></th>
        <th class="sortable" onclick="setProjectSort('output')">Output <span class="sort-icon" id="psort-output"></span></th>
        <th class="sortable" onclick="setProjectSort('cost')">Est. Cost <span class="sort-icon" id="psort-cost"></span></th>
      </tr></thead>
      <tbody id="project-cost-body"></tbody>
    </table>
  </div>
</div>
</div><!-- /detail-panel -->

<footer>
  <div class="footer-content">
    <div class="footer-brand">
      <img src="/logo" class="footer-logo" alt="Consultor-IA">
      <span class="footer-brand-text">Feito por Consultor-IA</span>
    </div>
    <div class="footer-meta">
      Estimativas de custo baseadas em <a href="https://claude.com/pricing#api" target="_blank">API pricing Anthropic</a> (abr 2026) &middot;
      <a href="https://github.com/phuryn/claude-usage" target="_blank">claude-usage</a> &middot; MIT
    </div>
  </div>
</footer>

<script>
// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ── State ──────────────────────────────────────────────────────────────────
let rawData = null;
let selectedModels = new Set();
let selectedRange = '30d';
let charts = {};
let sessionSortCol = 'last';
let modelSortCol = 'cost';
let modelSortDir = 'desc';
let projectSortCol = 'cost';
let projectSortDir = 'desc';
let lastFilteredSessions = [];
let lastByProject = [];
let sessionSortDir = 'desc';

// ── Pricing (Anthropic API, April 2026) ────────────────────────────────────
const PRICING = {
  'claude-opus-4-6':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-5':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-sonnet-4-6': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-sonnet-4-5': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-haiku-4-5':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
  'claude-haiku-4-6':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
};

function isBillable(model) {
  if (!model) return false;
  const m = model.toLowerCase();
  return m.includes('opus') || m.includes('sonnet') || m.includes('haiku');
}

function getPricing(model) {
  if (!model) return null;
  if (PRICING[model]) return PRICING[model];
  for (const key of Object.keys(PRICING)) {
    if (model.startsWith(key)) return PRICING[key];
  }
  const m = model.toLowerCase();
  if (m.includes('opus'))   return PRICING['claude-opus-4-6'];
  if (m.includes('sonnet')) return PRICING['claude-sonnet-4-6'];
  if (m.includes('haiku'))  return PRICING['claude-haiku-4-5'];
  return null;
}

function calcCost(model, inp, out, cacheRead, cacheCreation) {
  if (!isBillable(model)) return 0;
  const p = getPricing(model);
  if (!p) return 0;
  return (
    inp           * p.input       / 1e6 +
    out           * p.output      / 1e6 +
    cacheRead     * p.cache_read  / 1e6 +
    cacheCreation * p.cache_write / 1e6
  );
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
function fmtCost(c)    { return '$' + c.toFixed(4); }
function fmtCostBig(c) { return '$' + c.toFixed(2); }

// ── Chart colors ───────────────────────────────────────────────────────────
const TOKEN_COLORS = {
  input:          'rgba(79,142,247,0.8)',
  output:         'rgba(167,139,250,0.8)',
  cache_read:     'rgba(74,222,128,0.6)',
  cache_creation: 'rgba(251,191,36,0.6)',
};
const MODEL_COLORS = ['#d97757','#4f8ef7','#4ade80','#a78bfa','#fbbf24','#f472b6','#34d399','#60a5fa'];

// ── Time range ─────────────────────────────────────────────────────────────
const RANGE_LABELS = { '7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', 'all': 'All Time' };
const RANGE_TICKS  = { '7d': 7, '30d': 15, '90d': 13, 'all': 12 };

function getRangeCutoff(range) {
  if (range === 'all') return null;
  const days = range === '7d' ? 7 : range === '30d' ? 30 : 90;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function readURLRange() {
  const p = new URLSearchParams(window.location.search).get('range');
  return ['7d', '30d', '90d', 'all'].includes(p) ? p : '30d';
}

function setRange(range) {
  selectedRange = range;
  document.querySelectorAll('.range-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range)
  );
  updateURL();
  applyFilter();
}

// ── Model filter ───────────────────────────────────────────────────────────
function modelPriority(m) {
  const ml = m.toLowerCase();
  if (ml.includes('opus'))   return 0;
  if (ml.includes('sonnet')) return 1;
  if (ml.includes('haiku'))  return 2;
  return 3;
}

function readURLModels(allModels) {
  const param = new URLSearchParams(window.location.search).get('models');
  if (!param) return new Set(allModels.filter(m => isBillable(m)));
  const fromURL = new Set(param.split(',').map(s => s.trim()).filter(Boolean));
  return new Set(allModels.filter(m => fromURL.has(m)));
}

function isDefaultModelSelection(allModels) {
  const billable = allModels.filter(m => isBillable(m));
  if (selectedModels.size !== billable.length) return false;
  return billable.every(m => selectedModels.has(m));
}

function buildFilterUI(allModels) {
  const sorted = [...allModels].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  selectedModels = readURLModels(allModels);
  const container = document.getElementById('model-checkboxes');
  container.innerHTML = sorted.map(m => {
    const checked = selectedModels.has(m);
    return `<label class="model-cb-label ${checked ? 'checked' : ''}" data-model="${esc(m)}">
      <input type="checkbox" value="${esc(m)}" ${checked ? 'checked' : ''} onchange="onModelToggle(this)">
      ${esc(m)}
    </label>`;
  }).join('');
}

function onModelToggle(cb) {
  const label = cb.closest('label');
  if (cb.checked) { selectedModels.add(cb.value);    label.classList.add('checked'); }
  else            { selectedModels.delete(cb.value); label.classList.remove('checked'); }
  updateURL();
  applyFilter();
}

function selectAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = true; selectedModels.add(cb.value); cb.closest('label').classList.add('checked');
  });
  updateURL(); applyFilter();
}

function clearAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = false; selectedModels.delete(cb.value); cb.closest('label').classList.remove('checked');
  });
  updateURL(); applyFilter();
}

// ── URL persistence ────────────────────────────────────────────────────────
function updateURL() {
  const allModels = Array.from(document.querySelectorAll('#model-checkboxes input')).map(cb => cb.value);
  const params = new URLSearchParams();
  if (selectedRange !== '30d') params.set('range', selectedRange);
  if (!isDefaultModelSelection(allModels)) params.set('models', Array.from(selectedModels).join(','));
  const search = params.toString() ? '?' + params.toString() : '';
  history.replaceState(null, '', window.location.pathname + search);
}

// ── Session sort ───────────────────────────────────────────────────────────
function setSessionSort(col) {
  if (sessionSortCol === col) {
    sessionSortDir = sessionSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sessionSortCol = col;
    sessionSortDir = 'desc';
  }
  updateSortIcons();
  applyFilter();
}

function updateSortIcons() {
  document.querySelectorAll('.sort-icon').forEach(el => el.textContent = '');
  const icon = document.getElementById('sort-icon-' + sessionSortCol);
  if (icon) icon.textContent = sessionSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortSessions(sessions) {
  return [...sessions].sort((a, b) => {
    let av, bv;
    if (sessionSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else if (sessionSortCol === 'duration_min') {
      av = parseFloat(a.duration_min) || 0;
      bv = parseFloat(b.duration_min) || 0;
    } else {
      av = a[sessionSortCol] ?? 0;
      bv = b[sessionSortCol] ?? 0;
    }
    if (av < bv) return sessionSortDir === 'desc' ? 1 : -1;
    if (av > bv) return sessionSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

// ── Aggregation & filtering ────────────────────────────────────────────────
function applyFilter() {
  if (!rawData) return;

  const cutoff = getRangeCutoff(selectedRange);

  // DAILY CHART: sc_daily is authoritative (input+output per model per day)
  const scDailyFiltered = (rawData.sc_daily || []).filter(r =>
    selectedModels.has(r.model) && (!cutoff || r.day >= cutoff)
  );
  const dailyMap = {};
  for (const r of scDailyFiltered) {
    if (!dailyMap[r.day]) dailyMap[r.day] = {day: r.day, tokens: 0};
    dailyMap[r.day].tokens += r.tokens;
  }
  const daily = Object.values(dailyMap).sort((a, b) => a.day.localeCompare(b.day));

  // MODEL TABLE + PIE: sc_by_model is authoritative for all-time
  // For date-filtered views, scale proportionally using sc_daily period totals
  let byModel;
  if (selectedRange === 'all') {
    byModel = (rawData.sc_by_model || [])
      .filter(m => selectedModels.has(m.model))
      .map(m => ({...m, turns: 0, sessions: 0}));
  } else {
    const periodByModel = {};
    for (const r of scDailyFiltered) {
      periodByModel[r.model] = (periodByModel[r.model] || 0) + r.tokens;
    }
    byModel = Object.entries(periodByModel).map(([model, periodTokens]) => {
      const full = (rawData.sc_by_model || []).find(m => m.model === model) || {};
      const allTime = (full.input || 0) + (full.output || 0);
      const ratio = allTime > 0 ? periodTokens / allTime : 1;
      return {
        model,
        input:          Math.round((full.input || 0) * ratio),
        output:         Math.round((full.output || 0) * ratio),
        cache_read:     Math.round((full.cache_read || 0) * ratio),
        cache_creation: Math.round((full.cache_creation || 0) * ratio),
        turns: 0, sessions: 0,
      };
    }).sort((a, b) => (b.input + b.output) - (a.input + a.output));
  }

  // Filter sessions from DB (for session detail table and project breakdown)
  const filteredSessions = rawData.sessions_all.filter(s =>
    selectedModels.has(s.model) && (!cutoff || s.last_date >= cutoff)
  );

  // By project: aggregate from filtered sessions
  const projMap = {};
  for (const s of filteredSessions) {
    if (!projMap[s.project]) projMap[s.project] = { project: s.project, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const p = projMap[s.project];
    p.input          += s.input;
    p.output         += s.output;
    p.cache_read     += s.cache_read;
    p.cache_creation += s.cache_creation;
    p.turns          += s.turns;
    p.sessions++;
    p.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
  }
  const byProject = Object.values(projMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // Totals — sessions/messages from stats-cache (authoritative); tokens from sc_by_model
  const allModelCount = (rawData.sc_by_model || []).length;
  const isAllModels   = selectedModels.size >= allModelCount;
  const isAllTime     = selectedRange === 'all';
  const totals = {
    sessions:       isAllTime && isAllModels ? rawData.sc_sessions : filteredSessions.length,
    messages:       isAllTime && isAllModels ? rawData.sc_messages : filteredSessions.reduce((s, sess) => s + sess.turns, 0),
    input:          byModel.reduce((s, m) => s + m.input, 0),
    output:         byModel.reduce((s, m) => s + m.output, 0),
    cache_read:     byModel.reduce((s, m) => s + m.cache_read, 0),
    cache_creation: byModel.reduce((s, m) => s + m.cache_creation, 0),
    cost:           byModel.reduce((s, m) => s + calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation), 0),
  };

  // Update daily chart title
  document.getElementById('daily-chart-title').textContent = 'Daily Token Usage \u2014 ' + RANGE_LABELS[selectedRange];

  renderStats(totals);
  renderDailyChart(daily);
  renderModelChart(byModel);
  renderProjectChart(byProject);
  lastFilteredSessions = sortSessions(filteredSessions);
  lastByProject = sortProjects(byProject);
  renderSessionsTable(lastFilteredSessions.slice(0, 20));
  renderModelCostTable(byModel);
  renderProjectCostTable(lastByProject.slice(0, 20));
}

// ── Renderers ──────────────────────────────────────────────────────────────
function renderStats(t) {
  const rangeLabel = RANGE_LABELS[selectedRange].toLowerCase();
  const stats = [
    { label: 'Sessões',         value: t.sessions.toLocaleString(), sub: rangeLabel },
    { label: 'Mensagens',       value: fmt(t.messages),             sub: rangeLabel },
    { label: 'Input Tokens',    value: fmt(t.input),                sub: rangeLabel },
    { label: 'Output Tokens',   value: fmt(t.output),               sub: rangeLabel },
    { label: 'Cache Read',      value: fmt(t.cache_read),           sub: 'from prompt cache' },
    { label: 'Cache Creation',  value: fmt(t.cache_creation),       sub: 'writes to prompt cache' },
    { label: 'Est. Cost',       value: fmtCostBig(t.cost),          sub: 'API pricing, Apr 2026', color: '#4ade80' },
  ];
  document.getElementById('stats-row').innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="label">${s.label}</div>
      <div class="value" style="${s.color ? 'color:' + s.color : ''}">${esc(s.value)}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}
    </div>
  `).join('');
}

function renderDailyChart(daily) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.day),
      datasets: [
        { label: 'Tokens (input+output)', data: daily.map(d => d.tokens), backgroundColor: TOKEN_COLORS.output, borderRadius: 2 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#8892a4', maxTicksLimit: RANGE_TICKS[selectedRange] }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', callback: v => fmt(v) }, grid: { color: '#2a2d3a' } },
      }
    }
  });
}

function renderModelChart(byModel) {
  const ctx = document.getElementById('chart-model').getContext('2d');
  if (charts.model) charts.model.destroy();
  if (!byModel.length) { charts.model = null; return; }
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: byModel.map(m => m.model),
      datasets: [{ data: byModel.map(m => m.input + m.output), backgroundColor: MODEL_COLORS, borderWidth: 2, borderColor: '#1a1d27' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#8892a4', boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} tokens` } }
      }
    }
  });
}

function renderProjectChart(byProject) {
  const top = byProject.slice(0, 10);
  const ctx = document.getElementById('chart-project').getContext('2d');
  if (charts.project) charts.project.destroy();
  if (!top.length) { charts.project = null; return; }
  charts.project = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(p => p.project.length > 22 ? '\u2026' + p.project.slice(-20) : p.project),
      datasets: [
        { label: 'Input',  data: top.map(p => p.input),  backgroundColor: TOKEN_COLORS.input },
        { label: 'Output', data: top.map(p => p.output), backgroundColor: TOKEN_COLORS.output },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#8892a4', callback: v => fmt(v) }, grid: { color: '#2a2d3a' } },
        y: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { color: '#2a2d3a' } },
      }
    }
  });
}

function renderSessionsTable(sessions) {
  document.getElementById('sessions-body').innerHTML = sessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    const costCell = isBillable(s.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td class="muted" style="font-family:monospace">${esc(s.session_id)}&hellip;</td>
      <td>${esc(s.project)}</td>
      <td class="muted">${esc(s.last)}</td>
      <td class="muted">${esc(s.duration_min)}m</td>
      <td><span class="model-tag">${esc(s.model)}</span></td>
      <td class="num">${s.turns}</td>
      <td class="num">${fmt(s.input)}</td>
      <td class="num">${fmt(s.output)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

function setModelSort(col) {
  if (modelSortCol === col) {
    modelSortDir = modelSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    modelSortCol = col;
    modelSortDir = 'desc';
  }
  updateModelSortIcons();
  applyFilter();
}

function updateModelSortIcons() {
  document.querySelectorAll('[id^="msort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('msort-' + modelSortCol);
  if (icon) icon.textContent = modelSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortModels(byModel) {
  return [...byModel].sort((a, b) => {
    let av, bv;
    if (modelSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else {
      av = a[modelSortCol] ?? 0;
      bv = b[modelSortCol] ?? 0;
    }
    if (av < bv) return modelSortDir === 'desc' ? 1 : -1;
    if (av > bv) return modelSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderModelCostTable(byModel) {
  document.getElementById('model-cost-body').innerHTML = sortModels(byModel).map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation);
    const costCell = isBillable(m.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td><span class="model-tag">${esc(m.model)}</span></td>
      <td class="num">${fmt(m.turns)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_creation)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

// ── Project cost table sorting ────────────────────────────────────────────
function setProjectSort(col) {
  if (projectSortCol === col) {
    projectSortDir = projectSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    projectSortCol = col;
    projectSortDir = 'desc';
  }
  updateProjectSortIcons();
  applyFilter();
}

function updateProjectSortIcons() {
  document.querySelectorAll('[id^="psort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('psort-' + projectSortCol);
  if (icon) icon.textContent = projectSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjects(byProject) {
  return [...byProject].sort((a, b) => {
    const av = a[projectSortCol] ?? 0;
    const bv = b[projectSortCol] ?? 0;
    if (av < bv) return projectSortDir === 'desc' ? 1 : -1;
    if (av > bv) return projectSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectCostTable(byProject) {
  document.getElementById('project-cost-body').innerHTML = sortProjects(byProject).map(p => {
    return `<tr>
      <td>${esc(p.project)}</td>
      <td class="num">${p.sessions}</td>
      <td class="num">${fmt(p.turns)}</td>
      <td class="num">${fmt(p.input)}</td>
      <td class="num">${fmt(p.output)}</td>
      <td class="cost">${fmtCost(p.cost)}</td>
    </tr>`;
  }).join('');
}

// ── CSV Export ────────────────────────────────────────────────────────────
function csvField(val) {
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function csvTimestamp() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
    + '_' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0');
}

function downloadCSV(reportType, header, rows) {
  const lines = [header.map(csvField).join(',')];
  for (const row of rows) {
    lines.push(row.map(csvField).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = reportType + '_' + csvTimestamp() + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportSessionsCSV() {
  const header = ['Session', 'Project', 'Last Active', 'Duration (min)', 'Model', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastFilteredSessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    return [s.session_id, s.project, s.last, s.duration_min, s.model, s.turns, s.input, s.output, s.cache_read, s.cache_creation, cost.toFixed(4)];
  });
  downloadCSV('sessions', header, rows);
}

function exportProjectsCSV() {
  const header = ['Project', 'Sessions', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProject.map(p => {
    return [p.project, p.sessions, p.turns, p.input, p.output, p.cache_read, p.cache_creation, p.cost.toFixed(4)];
  });
  downloadCSV('projects', header, rows);
}

// ── Overview (stats-cache) ─────────────────────────────────────────────────
let statsCache = null;
let ovRange = 'all';
let activeTab = 'overview';

function switchTab(tab) {
  activeTab = tab;
  document.getElementById('tab-overview').classList.toggle('active', tab === 'overview');
  document.getElementById('tab-cards').classList.toggle('active',    tab === 'cards');
  document.getElementById('tab-detail').classList.toggle('active',   tab === 'detail');
  document.getElementById('ov-body').style.display       = tab === 'overview' ? '' : 'none';
  document.getElementById('cards-panel').style.display   = tab === 'cards'    ? '' : 'none';
  document.getElementById('detail-panel').style.display  = tab === 'detail'   ? '' : 'none';
  if (tab === 'cards' && statsCache) renderIgCards(statsCache);
}

function setOvRange(range) {
  ovRange = range;
  document.querySelectorAll('.ov-range-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.ovrange === range)
  );
  if (statsCache) renderOverview(statsCache);
}

function fmtBig(n) {
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

function shortModel(m) {
  if (!m) return '—';
  return m.replace('claude-', '').replace(/-\d{8}$/, '');
}

function getRangeCutoffDate(range) {
  if (range === 'all') return null;
  const days = range === '7d' ? 7 : 30;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function filterHeatmap(heatmap, cutoff) {
  if (!cutoff) return heatmap;
  const out = {};
  for (const [d, v] of Object.entries(heatmap)) {
    if (d >= cutoff) out[d] = v;
  }
  return out;
}

function buildHeatmap(heatmap) {
  const container = document.getElementById('heatmap-container');
  if (!container) return;
  while (container.firstChild) container.removeChild(container.firstChild);

  const dates = Object.keys(heatmap).sort();
  if (!dates.length) return;

  const vals = Object.values(heatmap);
  const maxVal = Math.max(...vals) || 1;

  function intensity(v) {
    if (!v) return 0;
    const p = v / maxVal;
    if (p >= 0.9) return 5;
    if (p >= 0.75) return 4;
    if (p >= 0.55) return 3;
    if (p >= 0.35) return 2;
    return 1;
  }

  const MONTHS_PT = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  const DAYS_PT   = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];

  const first = new Date(dates[0] + 'T00:00:00');
  const last  = new Date(dates[dates.length - 1] + 'T00:00:00');
  const startSun = new Date(first);
  startSun.setDate(first.getDate() - first.getDay());

  const dateMap = {};
  for (const d of dates) dateMap[d] = heatmap[d];

  // Build columns (one per week)
  const cols = [];
  const cur = new Date(startSun);
  while (cur <= last) {
    const col = { month: null, cells: [] };
    for (let dow = 0; dow < 7; dow++) {
      const ds = cur.toISOString().slice(0, 10);
      const inRange = cur >= first && cur <= last;
      // Mark month label on first day of month (day 1) that falls on this column
      if (inRange && cur.getDate() === 1 && col.month === null) {
        col.month = MONTHS_PT[cur.getMonth()];
      }
      col.cells.push({ ds, inRange, val: dateMap[ds] || 0, dow });
      cur.setDate(cur.getDate() + 1);
    }
    cols.push(col);
  }

  // Outer wrapper
  const outer = document.createElement('div');
  outer.className = 'heatmap-outer';

  // Day labels column (Dom hidden, Seg shown, Ter hidden, Qua shown, Qui hidden, Sex shown, Sáb hidden)
  const dayLabelCol = document.createElement('div');
  dayLabelCol.className = 'heatmap-day-labels';
  [0,1,2,3,4,5,6].forEach(dow => {
    const lbl = document.createElement('div');
    lbl.className = 'heatmap-day-label' + (dow % 2 === 0 ? ' hidden' : '');
    lbl.textContent = DAYS_PT[dow];
    dayLabelCol.appendChild(lbl);
  });
  outer.appendChild(dayLabelCol);

  // Right side: month labels row + cells grid
  const colsWrap = document.createElement('div');
  colsWrap.className = 'heatmap-cols-wrap';

  // Month labels row
  const monthRow = document.createElement('div');
  monthRow.className = 'heatmap-month-row';
  cols.forEach(col => {
    const lbl = document.createElement('div');
    lbl.className = 'heatmap-month-label';
    lbl.style.width = '16px'; // cell(13) + gap(3)
    lbl.style.flexShrink = '0';
    lbl.textContent = col.month || '';
    monthRow.appendChild(lbl);
  });
  colsWrap.appendChild(monthRow);

  // Cell columns
  const colsEl = document.createElement('div');
  colsEl.className = 'heatmap-cols';
  cols.forEach(col => {
    const colEl = document.createElement('div');
    colEl.className = 'heatmap-col';
    col.cells.forEach(c => {
      const cell = document.createElement('div');
      cell.className = 'heatmap-cell hm-' + (c.inRange ? intensity(c.val) : 0);
      cell.title = c.inRange && c.val
        ? (c.ds + ': ' + fmtBig(c.val) + ' tokens')
        : c.ds;
      colEl.appendChild(cell);
    });
    colsEl.appendChild(colEl);
  });
  colsWrap.appendChild(colsEl);
  outer.appendChild(colsWrap);
  container.appendChild(outer);
}

function renderOverview(data) {
  const cutoff = getRangeCutoffDate(ovRange);
  const filteredHeatmap = filterHeatmap(data.heatmap || {}, cutoff);

  let sessions    = data.sessions;
  let messages    = data.messages;
  let totalTokens = data.total_tokens;
  let activeDays  = data.active_days;

  if (cutoff) {
    const allDays      = Object.keys(data.heatmap || {});
    const filteredDays = Object.keys(filteredHeatmap);
    activeDays  = filteredDays.length;
    totalTokens = Object.values(filteredHeatmap).reduce((a, b) => a + b, 0);
    const ratio = allDays.length > 0 ? filteredDays.length / allDays.length : 1;
    sessions = Math.round(data.sessions * ratio);
    messages = Math.round(data.messages * ratio);
  }

  document.getElementById('ov-sessions').textContent   = sessions.toLocaleString('pt-BR');
  document.getElementById('ov-messages').textContent   = messages.toLocaleString('pt-BR');
  document.getElementById('ov-tokens').textContent     = fmtBig(totalTokens);
  document.getElementById('ov-days').textContent       = activeDays;
  document.getElementById('ov-streak').textContent     = data.streak + 'd';
  document.getElementById('ov-max-streak').textContent = data.max_streak + 'd';
  document.getElementById('ov-peak-hour').textContent  = data.peak_hour ? (data.peak_hour + 'h') : '—';
  document.getElementById('ov-fav-model').textContent  = shortModel(data.fav_model);
  if (data.commits != null) {
    document.getElementById('ov-commits').textContent = data.commits.toLocaleString('pt-BR');
    document.getElementById('ov-lines').textContent   = fmtBig(data.lines_added);
  }

  buildHeatmap(filteredHeatmap);

  document.getElementById('fun-phrase').textContent = data.fun_phrase || '';
}

async function loadStatsCache() {
  try {
    const resp = await fetch('/api/stats');
    const d = await resp.json();
    if (d.error) return;
    statsCache = d;
    renderOverview(d);
  } catch(e) {}
}

// ── Instagram cards ───────────────────────────────────────────────────────
function igFmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(1).replace('.',',') + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1).replace('.',',') + 'M';
  if (n >= 1e3) return Math.round(n/1e3).toLocaleString('pt-BR') + 'K';
  return n.toLocaleString('pt-BR');
}

function igCard(id, bgClass, html) {
  return `<div class="ig-card-wrap">
    <button class="ig-dl-btn" onclick="downloadCard('${id}')">⬇ PNG</button>
    <div class="ig-card ${bgClass}" id="${id}">${html}</div>
  </div>`;
}

function buildIgCards(d) {
  const sessions  = (d.sessions||0).toLocaleString('pt-BR');
  const msgs      = igFmt(d.messages||0);
  const tokens    = igFmt(d.total_tokens||0);
  const commits   = (d.commits||0).toLocaleString('pt-BR');
  const lines     = igFmt(d.lines_added||0);
  const hours     = (d.total_hours||0).toLocaleString('pt-BR');
  const days      = d.active_days||0;
  const hpRatio   = (d.hp_ratio||0).toLocaleString('pt-BR');
  const cpd       = d.commits_per_day||0;
  const lpd       = igFmt(d.lines_per_day||0);
  const hpd       = Math.round((d.total_hours||0)/(d.active_days||1));

  const lavOrb = (w,pos,extra) =>
    `<div class="ig-orb" style="width:${w}px;height:${w}px;${pos};background:radial-gradient(circle,rgba(167,139,250,.22),transparent 70%);${extra||''}"></div>`;

  const igFooter = `<div class="ig-footer"><img src="/logo" class="ig-logo" alt="Consultor-IA"><span class="ig-brand">Feito por Consultor-IA</span></div>`;

  const c1 = igCard('ig-c1','ig-bg-cover', `
    ${lavOrb(280,'top:-70px;left:-70px','')}
    ${lavOrb(200,'bottom:-50px;right:-50px','opacity:.6')}
    <div class="ig-tag">Consultoria com IA · ${days} dias</div>
    <div class="ig-cover-stats">
      <div class="ig-cover-line">${sessions} sessões de agente.</div>
      <div class="ig-cover-line">${msgs} instruções trocadas.</div>
      <div class="ig-cover-line">${tokens} tokens de raciocínio.</div>
    </div>
    <div class="ig-cover-sub">Não é hype.<br>É como entrego consultoria com IA hoje.</div>
    <div class="ig-tag-bottom">#ConsultorIA #ClaudeCode #IA</div>
    ${igFooter}
  `);

  const c2 = igCard('ig-c2','ig-bg-lavender', `
    ${lavOrb(300,'top:-80px;right:-80px','')}
    <div class="ig-num">${sessions}</div>
    <div class="ig-label">sessões de agente</div>
    <div class="ig-desc">Cada uma resolveu um problema real —<br>análise, código, estratégia, automação.<br>Eu defini o problema. A IA executou.</div>
    ${igFooter}
  `);

  const c3 = igCard('ig-c3','ig-bg-black', `
    ${lavOrb(280,'bottom:-60px;left:-60px','')}
    <div class="ig-num">${msgs}</div>
    <div class="ig-label">instruções ao agente</div>
    <div class="ig-desc">Cada mensagem foi uma decisão minha:<br>o que priorizar, o que corrigir, onde ir.<br>A IA executa. Eu conduzo a consultoria.</div>
    ${igFooter}
  `);

  const c4 = igCard('ig-c4','ig-bg-lavender', `
    ${lavOrb(320,'top:-100px;left:-80px','')}
    <div class="ig-num">${tokens}</div>
    <div class="ig-label">tokens de raciocínio</div>
    <div class="ig-desc">~${hpRatio}× o conteúdo de Harry Potter<br>e a Pedra Filosofal — aplicados<br>direto nos projetos dos meus clientes.</div>
    ${igFooter}
  `);

  const c5 = igCard('ig-c5','ig-bg-black', `
    ${lavOrb(240,'top:-40px;right:-40px','')}
    <div class="ig-pair">
      <div class="ig-pair-item">
        <div class="ig-num-sm">${commits}</div>
        <div class="ig-label" style="font-size:16px">commits</div>
      </div>
      <div class="ig-divider"></div>
      <div class="ig-pair-item">
        <div class="ig-num-sm">${lines}</div>
        <div class="ig-label" style="font-size:16px">linhas de código</div>
      </div>
    </div>
    <div class="ig-desc" style="margin-top:20px">Não terceirizei. Aprendi fazendo.<br>${cpd} commits por dia. ${lpd} linhas por dia.<br>Tudo enquanto atendia clientes.</div>
    ${igFooter}
  `);

  const c6 = igCard('ig-c6','ig-bg-amber', `
    <div class="ig-insight-box">
      <div class="ig-insight-title">Em ${days} dias construí a infraestrutura que hoje entrega IA para meus clientes de consultoria.</div>
      <div class="ig-insight-body">${commits} versões entregues. ${lines} linhas de código. ${sessions} agentes orquestrados. ${tokens} tokens de análise. Enquanto isso, dei mentorias, atendi clientes e aprendi o que funciona de verdade na prática.</div>
    </div>
    ${igFooter}
  `);

  const c7 = igCard('ig-c7','ig-bg-dark', `
    ${lavOrb(260,'bottom:-60px;right:-60px','')}
    <div class="ig-num">${hours}h</div>
    <div class="ig-label">de agente rodando por mim</div>
    <div class="ig-desc">Em ${days} dias ativos.<br>~${hpd}h por dia — sessões paralelas.<br>Enquanto eu pensava, ensinava, entregava.</div>
    ${igFooter}
  `);

  return [c1, c2, c3, c4, c5, c6, c7].join('');
}

function renderIgCards(d) {
  const grid = document.getElementById('ig-grid');
  if (!grid) return;
  grid.innerHTML = buildIgCards(d);
}

async function downloadCard(id) {
  if (!window.html2canvas) {
    await loadHtml2Canvas();
  }
  const el = document.getElementById(id);
  if (!el) return;
  const canvas = await window.html2canvas(el, {
    scale: 3, useCORS: true, backgroundColor: null,
    width: el.offsetWidth, height: el.offsetHeight
  });
  const a = document.createElement('a');
  a.href = canvas.toDataURL('image/png');
  a.download = id + '.png';
  a.click();
}

async function downloadAllCards() {
  if (!window.html2canvas) await loadHtml2Canvas();
  const ids = ['ig-c1','ig-c2','ig-c3','ig-c4','ig-c5','ig-c6','ig-c7'];
  for (const id of ids) {
    await downloadCard(id);
    await new Promise(r => setTimeout(r, 300));
  }
}

function loadHtml2Canvas() {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js';
    s.onload = resolve; s.onerror = reject;
    document.head.appendChild(s);
  });
}

// ── Rescan ────────────────────────────────────────────────────────────────
async function triggerRescan() {
  const btn = document.getElementById('rescan-btn');
  btn.disabled = true;
  btn.textContent = '\u21bb Scanning...';
  try {
    const resp = await fetch('/api/rescan', { method: 'POST' });
    const d = await resp.json();
    btn.textContent = '\u21bb Rescan (' + d.new + ' new, ' + d.updated + ' updated)';
    await loadData();
  } catch(e) {
    btn.textContent = '\u21bb Rescan (error)';
    console.error(e);
  }
  setTimeout(() => { btn.textContent = '\u21bb Rescan'; btn.disabled = false; }, 3000);
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch('/api/data');
    const d = await resp.json();
    if (d.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171">' + esc(d.error) + '</div>';
      return;
    }
    document.getElementById('meta').textContent = 'Updated: ' + d.generated_at + ' \u00b7 Auto-refresh in 30s';

    const isFirstLoad = rawData === null;
    rawData = d;

    if (isFirstLoad) {
      // Restore range from URL, mark active button
      selectedRange = readURLRange();
      document.querySelectorAll('.range-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.range === selectedRange)
      );
      // Build model filter (reads URL for model selection too)
      buildFilterUI(d.all_models);
      updateSortIcons();
      updateModelSortIcons();
      updateProjectSortIcons();
    }

    applyFilter();
  } catch(e) {
    console.error(e);
  }
}

// Hide detail panel on load; show overview by default
document.getElementById('detail-panel').style.display = 'none';

loadData();
loadStatsCache();
setInterval(loadData, 30000);
setInterval(loadStatsCache, 30000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

        elif self.path == "/api/stats":
            data = get_stats_cache_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/data":
            data = get_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/logo":
            # Look for logo next to this script (drop your own logo.png or logo-dark.png here)
            script_dir = Path(__file__).parent
            candidates = [
                script_dir / "logo.png",
                script_dir / "logo-dark.png",
            ]
            found = next((p for p in candidates if p.exists()), None)
            if found:
                img_bytes = found.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img_bytes)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(img_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/rescan":
            # Full rebuild: delete DB and rescan from scratch
            if DB_PATH.exists():
                DB_PATH.unlink()
            from scanner import scan
            result = scan(verbose=False)
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def serve(host=None, port=None):
    host = host or os.environ.get("HOST", "localhost")
    port = port or int(os.environ.get("PORT", "8080"))
    server = HTTPServer((host, port), DashboardHandler)
    print(f"Dashboard running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
