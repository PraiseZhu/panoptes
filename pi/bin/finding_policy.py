#!/usr/bin/env python3
"""Who is an author problem (@ / DM / handoff issue / overdue) — unified severity (Praise 2026-10-23).
Standard: the reviewer's unified severity, PraiseZhu/Review-PR mivo/config/severity.md (panoptes pins a copy, reports carry
`severity_standard`). New-format reports put only findings that stand under that standard in prs[].findings; everything
unproven is in prs[].pending_verification[] (待核实, QA 自查) and is never an author problem. For older reports this
module is the fallback that drops: coverage-only P2 (coverage-gate; a PR's own stated coverage target that is missed is
`stated-acceptance` and counts), unverified AI-test leads, own-tests, qa-error, unverified / setup-failed / timeout / flaky
checks, pre-existing / fixed-later, PLAUSIBLE review findings, unconfirmed failed handoff cases, anything ruled 误报.
new_notice=True (daily.py DMs / @ / new handoff issues): an AI-only code-review P0/P1 also needs
verification.status == "confirmed" (panoptes' repo-wide verification); old reports have none, so such a P1 is 待核实 and
is not sent. new_notice=False (follow-ups of items already sent, ledger rows): the old CONFIRMED review verdict stands.
The unified rule only covers notices sent from UNIFIED_SINCE (2026-10-24 00:00 Asia/Shanghai, Praise 2026-10-23 23:18):
problems already sent to an author before that keep the old flow -- reminders as before, the old overdue rule; the author's
own bot re-checks them and replies on the PR. A DM record from before then stands for what the rule of the time put in it
(legacy_notified). Same rule, JS: panoptes report/finding-policy.mjs notifiable() / UNIFIED_SINCE; ledger:
mivo-qa/ledger/build-ledger.mjs notifiable() / legacyNotified()."""
import os, re, datetime as _dt

AUTHOR_SEV = ("P0", "P1", "P2")
SEV_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# Same instant as panoptes report/finding-policy.mjs UNIFIED_SINCE; MIVO_QA_UNIFIED_SINCE is for tests only.
UNIFIED_SINCE = os.environ.get("MIVO_QA_UNIFIED_SINCE") or "2026-10-23T16:00:00Z"

def instant(s):
    """ISO time ('Z', offset, or naive = this box's local time, like JS Date.parse) -> aware datetime, or None."""
    try: t = _dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError): return None
    return t if t.tzinfo else t.astimezone()

def sent_under_unified(at):
    """True when a notice sent at `at` falls under the unified rule (sent from UNIFIED_SINCE); unknown time -> False (old flow)."""
    t, since = instant(at), instant(UNIFIED_SINCE)
    return bool(t and since and t >= since)

LEGACY_BAD_STATUS = re.compile(r"flaky|qa-error|false_positive|misreport|pre-existing|unconfirmed")
def legacy_notified(f):
    """The problems a DM sent before UNIFIED_SINCE stands for: the rule dm_compat.py and build-ledger.mjs used before the
    unified severity (unchanged on purpose). P0–P2; not an AI-test lead / unconfirmed failed author case / pre-existing /
    flaky / qa-error / 误报; review and coverage findings count, other checks only when test-backed and not unverified."""
    if not f or f.get("severity") not in AUTHOR_SEV: return False
    if f.get("source") == "ai-test" or (f.get("qa_status") == "failed" and f.get("confirmed") is False) or f.get("pre_existing"): return False
    if (f.get("calibration") or {}).get("to") == "误报" or re.search(r"false_positive|misreport|REJECTED", str(f.get("verdict") or ""), re.I): return False
    if LEGACY_BAD_STATUS.search(f"{f.get('status') or ''} {f.get('attribution') or ''}"): return False
    return f.get("source") in ("code-review", "coverage-gate") or (f.get("test_backed") is True and f.get("status") != "unverified")

def notifiable(f, new_notice=False):
    if not f or f.get("severity") not in AUTHOR_SEV or f.get("pending_kind"): return False
    if f.get("qa_status") == "failed" and f.get("confirmed") is False: return False
    if f.get("pre_existing") or f.get("not_a_pr_finding"): return False
    if (f.get("calibration") or {}).get("to") == "误报" or re.search(r"false_positive|misreport|REJECTED", str(f.get("verdict") or ""), re.I): return False
    src = f.get("source") or "check"
    if src == "code-review":
        if str(f.get("verdict") or "").upper() == "PLAUSIBLE": return False
        v = f.get("verification")
        if v is not None: return isinstance(v, dict) and v.get("status") == "confirmed"   # same as JS: present but unconfirmed -> no
        return not (new_notice and f.get("severity") in ("P0", "P1"))
    if src == "stated-acceptance": return True
    if src == "ai-test": return f.get("verified") is True
    if src in ("coverage-gate", "own-tests", "qa-error"): return False
    # a failed check: only one this PR introduced; very old reports have no status -> their test_backed
    if f.get("status") is None: return f.get("test_backed") is not False and f.get("flaky") is not True
    return f.get("status") == "introduced"

def author_findings(pr, new_notice=False):
    """Author problems of one prs[] entry, main problem first (severity, then report order)."""
    fs = [(i, f) for i, f in enumerate((pr or {}).get("findings") or []) if notifiable(f, new_notice)]
    fs.sort(key=lambda t: (SEV_RANK[t[1]["severity"]], t[0]))
    return [f for _, f in fs]

def pending_items(pr, new_notice=False):
    """待核实: the report's pending_verification plus, for old reports, P0–P2 findings this policy does not count."""
    out = list((pr or {}).get("pending_verification") or [])
    out += [f for f in (pr or {}).get("findings") or [] if f.get("severity") in AUTHOR_SEV and not notifiable(f, new_notice)
            and not (f.get("calibration") or {}).get("to") == "误报"]
    return out

def effective_severity(pr, new_notice=False):
    """PR level from its author problems (old reports may carry a level raised by findings that no longer count)."""
    fs = author_findings(pr, new_notice)
    if fs: return fs[0]["severity"]
    if (pr or {}).get("verdict") in ("qa-error", "not-tested"): return "untested"
    sev = (pr or {}).get("severity")
    return sev if sev in ("P3", "untested") else ("P3" if sev in AUTHOR_SEV else sev)

# ---- our own PRs (Praise 2026-10-23: 只修自己的 PR，测出问题立刻修) ------------------------------------------------
DISMISSED_RE = re.compile(r"false_positive|misreport|REJECTED", re.I)
def own_pr_ticket(x, n, fix2, unified=False, nofix=frozenset(), pending=False):
    """Praise 2026-10-23: 只修自己的 PR，测出问题立刻修。On an own PR every P0/P1 the tests raised is a ticket -- including
    待核实 items (pending_verification, unverified / PLAUSIBLE review, AI-test leads): prfix's first step writes a failing
    test, which IS the verification; no repro -> NOREPRO note, no PR. P2: a confirmed P2 (in findings, not pending, review
    verdict not PLAUSIBLE) is a ticket unless the PR is in nofix (p2-rulings.md 不修 / 产品代码不修 / 误报 / P3 / 已修 —
    p2-rulings.md 10-23 header: confirmed P2 must be fixed, rulings only decide valid / which level); a pending or unconfirmed P2
    is no ticket (the ledger keeps the row). Coverage-only (coverage-gate / own-tests) only as a P2 ruled 修 (coverage is a hint
    under the unified severity). Excluded always: dismissed (误报 / REJECTED / pre-existing / flaky), qa-error (the test
    system's own failure -> infrafix lane), checks other than introduced. `unified` is kept for callers but no longer used.
    Returns the severity to work at, or None. Shared by prfix.py (own_sev) and mivo-qa/ledger/build-ledger.mjs (ownTicket, JS copy)."""
    sev = x.get("severity") or x.get("severity_if_confirmed")
    if sev not in AUTHOR_SEV: return None
    if x.get("pre_existing") or x.get("not_a_pr_finding") or x.get("flaky") is True: return None
    if (x.get("calibration") or {}).get("to") == "误报" or DISMISSED_RE.search(str(x.get("verdict") or "")): return None
    src = x.get("source") or "check"
    if src == "qa-error": return None
    if src in ("coverage-gate", "own-tests"): return sev if sev == "P2" and n in fix2 else None
    if src == "check" and x.get("status") not in (None, "introduced"): return None
    if sev == "P2":
        unconfirmed = pending or bool(x.get("pending_kind")) or str(x.get("verdict") or "").upper() == "PLAUSIBLE"
        if unconfirmed or n in nofix: return None
    return sev

def fix_rulings(text):
    """PRs whose P2 ruling in fleet-standards/p2-rulings.md is exactly 修 (已修 = fixed elsewhere; 不修 / 产品代码不修 = no PR)."""
    s = set()
    for line in (text or "").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 4 and re.fullmatch(r"#?\d+", c[1] or "") and c[3] == "修": s.add(int(c[1].lstrip("#")))
    return s

NOFIX_RULINGS = ("不修", "产品代码不修", "误报", "P3", "已修")
def no_fix_rulings(text):
    """PRs whose P2 ruling in fleet-standards/p2-rulings.md says no PR is needed: 不修 / 产品代码不修 / 误报 / P3 / 已修 (fixed elsewhere)."""
    s = set()
    for line in (text or "").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 4 and re.fullmatch(r"#?\d+", c[1] or "") and c[3] in NOFIX_RULINGS: s.add(int(c[1].lstrip("#")))
    return s

