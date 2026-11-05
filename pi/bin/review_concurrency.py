"""review_concurrency.py — review-pr peak concurrency for the daily review (11-05, retest-1104 ANALYSIS.md §4).
Replaces the calculation in /workspace/review-ops/bin/daily_review.py:207-222, which
  - compared the peak with the stale doc default max_parallel_doc=3 (Mini's configured maxParallel is 10, verified 10-16),
  - closed each interval at updated_at (re-runs / later comment write-backs stretch it) instead of completed_at,
  - read only the first page (per_page=100) and just noted the truncation.
Pure parts: fetch_all_runs (gh injected), interval, peak, concurrency_line."""
import datetime as dt, json

CST = dt.timezone(dt.timedelta(hours=8))

def ts(s):
    if not s: return None
    try: return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError: return None

def fetch_all_runs(gh, repo, workflow, created, per_page=100, max_pages=50):
    """Every run (all pages). gh(path) -> parsed JSON or None. Returns (runs, total_count, complete)."""
    runs, total = [], None
    for page in range(1, max_pages + 1):
        d = gh(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page={per_page}&page={page}&created={created}")
        if not isinstance(d, dict) or "workflow_runs" not in d: return (runs or None), total, False
        total = d.get("total_count", total); rs = d["workflow_runs"]; runs += rs
        if len(rs) < per_page or (total is not None and len(runs) >= total): return runs, total, True
    return runs, total, False

def interval(run):
    """run_started_at -> completed_at; updated_at only when completed_at is missing (still running / old API)."""
    return ts(run.get("run_started_at")), ts(run.get("completed_at")) or ts(run.get("updated_at"))

def peak(runs):
    ev = []
    for x in runs:
        s, e = interval(x)
        if s and e and e >= s: ev += [(s, 1), (e, -1)]
    cur = best = 0; at = None
    for t, k in sorted(ev):   # -1 sorts before +1 at the same instant: back-to-back runs don't overlap
        cur += k
        if cur > best: best, at = cur, t
    return best, at

def max_parallel(rm):
    """Configured maxParallel (sources.json max_parallel_configured) first; the doc default only as a fallback."""
    if rm.get("max_parallel_configured") is not None: return int(rm["max_parallel_configured"]), "配置"
    return int(rm.get("max_parallel_doc", 3)), "文档默认（未配置 max_parallel_configured）"

def concurrency_line(runs, total, complete, rm, con=None):
    p, at = peak(runs); lim, src = max_parallel(rm)
    line = f"review-pr 运行 {len(runs)} 次" + (f"（{dict(con)}）" if con else "") + f"，峰值并发 {p}"
    if at: line += f"（{at.astimezone(CST):%m-%d %H:%M} 北京时间）"
    line += f"，{'超过' if p > lim else '未超过'}{src} maxParallel={lim}"
    if rm.get("max_parallel_doc") is not None and src == "配置" and rm["max_parallel_doc"] != lim: line += f"（文档默认 {rm['max_parallel_doc']}，仅展示）"
    if not complete: line += f"（翻页没取全：取到 {len(runs)}/{total if total is not None else '?'} 次）"
    return line, p, lim
