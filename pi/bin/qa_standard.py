"""Fleet policy metadata and model briefs. No credentials, network or deployment writes."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

CODE = Path(__file__).resolve().parents[1]
MODES = ('legacy', 'shadow', 'canary', 'strict', 'rollback')
LANES = ('qa', 'repair', 'retest', 'ops', 'outbound')


def paths():
    return (Path(os.environ.get('PI_QA_FLEET_FILE') or CODE / 'conf/qa-fleet.json'),
            Path(os.environ.get('PI_QA_STANDARD_FILE') or CODE / 'conf/qa-standard.json'),
            Path(os.environ.get('PI_QA_STANDARD_BRIEF') or CODE / 'conf/qa-standard-brief.md'))


def configs():
    fp, sp, bp = paths()
    fleet, standard = json.loads(fp.read_text()), json.loads(sp.read_text())
    if fleet.get('schema') != 'qa.fleet/v1' or standard.get('schema') != 'qa.standard/v1': raise ValueError('standard_schema')
    if not standard.get('version') or fleet.get('standard_version') != standard['version']: raise ValueError('policy_drift')
    if standard.get('mode') not in MODES: raise ValueError('mode_invalid')
    if set(fleet.get('lanes', {})) != set(LANES): raise ValueError('lanes_missing')
    raw = bp.read_text()
    if not re.search(r'^Version: `' + re.escape(standard['version']) + r'`\.', raw, re.M): raise ValueError('brief_version_drift')
    if len(raw.splitlines()) > 60: raise ValueError('brief_too_long')
    sections = dict(re.findall(r'^## (\w+)\n(.*?)(?=^## |\Z)', raw, re.M | re.S))
    if any(not sections.get(lane, '').strip() for lane in LANES): raise ValueError('brief_lane_missing')
    return fleet, standard, sections


def lane_for(job):
    fleet, _, _ = configs()
    for lane, spec in fleet['lanes'].items():
        if job in spec['jobs']: return lane
    return 'ops'  # free dispatch tasks inherit the operational rules


def prompt_context(session, lane=None):
    # Read per call: a long-lived process must see a mode/version change.
    fleet, standard, sections = configs()
    if lane is None:
        rules = [('opsfix-retest-', 'retest'), ('pi-line-prfix-', 'repair'), ('pi-line-infrafix-', 'ops')]
        lane = next((v for prefix, v in rules if session.startswith(prefix)), None)
        if lane is None: lane = lane_for(os.environ.get('PI_QA_JOB') or session.split('-')[0])
    if lane not in LANES: raise ValueError('lane_unknown')
    return (f"[qa-standard version={standard['version']} mode={standard['mode']} lane={lane}]\n"
            f"{sections[lane].strip()}\n"
            "Mode rules: shadow compares decisions without enabling new outbound; canary uses confirmed evidence only; "
            "strict requires complete confirmed evidence; rollback preserves all evidence and disables new product actions.")


def source_sha():
    sha = subprocess.check_output(['git', '-C', str(CODE.parent), 'rev-parse', 'HEAD'], text=True, timeout=10).strip()
    if not re.fullmatch(r'[a-f0-9]{40}', sha): raise ValueError('source_sha_invalid')
    return sha


def binding():
    fleet, standard, _ = configs()
    fp, sp, _ = paths()
    return {'source_sha': source_sha(), 'standard_version': standard['version'], 'mode': standard['mode'],
            'manifest_hash': hashlib.sha256(fp.read_bytes() + b'\0' + sp.read_bytes()).hexdigest()}


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n'); tmp.replace(path)


def record_start(state, job, trigger):
    meta = binding(); run_id = 'fleet-' + uuid.uuid4().hex
    doc = {'schema': 'qa.fleet.run/v1', **meta, 'runtime_sha': meta['source_sha'], 'run_id': run_id,
           'lane': lane_for(job), 'job': job, 'trigger': trigger, 'status': 'running',
           'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    path = Path(state) / 'out/qa-fleet/runs' / (run_id + '.json')
    write_json(path, doc)
    return path


def record_finish(file, rc, report=None):
    path = Path(file); doc = json.loads(path.read_text()); doc.update(exit_code=rc, ok=rc == 0, status='pass' if rc == 0 else 'fail',
                 finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
    # A successful scheduler exit is not a complete QA report. Missing evidence stays unknown.
    if report and Path(report).is_file():
        raw = Path(report).read_bytes(); rpt = json.loads(raw)
        doc['report'] = {'path': str(Path(report).resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}
        doc['all_discovered_tested'] = (rpt.get('system_health') or {}).get('all_discovered_tested')
        doc['report_run_id'] = (rpt.get('run') or {}).get('id')
    write_json(path, doc)
    return doc


def manifest_path(state=None, qa=None):
    return Path(os.environ.get('PI_QA_FLEET_MANIFEST') or Path(qa or os.environ.get('QA_HOME', '/workspace/mivo-qa')) / 'argus-root/qa-fleet-run-manifest.json')


def inspect(state, qa=None, now=None):
    """Fail-visible status; neither an absent run nor a missing count is treated as success."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = {'mode': None, 'standard_version': None, 'lanes': {}, 'errors': {}, 'all_discovered_tested': None,
              'missing_evidence_outbound': None}
    try:
        fleet, standard, _ = configs(); result.update(binding())
    except Exception as e:
        result['errors']['config'] = str(e) if isinstance(e, ValueError) else 'config_unavailable'; return result
    for lane in LANES: result['lanes'][lane] = {'status': 'missing', 'latest_run': None, 'consecutive_failures': 0}
    runs = []
    for p in (Path(state) / 'out/qa-fleet/runs').glob('fleet-*.json'):
        try:
            doc = json.loads(p.read_text())
            if not isinstance(doc, dict) or doc.get('schema') != 'qa.fleet.run/v1' or doc.get('lane') not in LANES: raise ValueError('receipt_invalid')
            runs.append(doc)
        except (OSError, ValueError): result['errors']['receipt'] = 'receipt_unreadable'
    for lane in LANES:
        rows = sorted((r for r in runs if r['lane'] == lane), key=lambda r: r.get('started_at', ''))
        if not rows:
            result['errors']['lane:' + lane] = 'missing_run'; continue
        # An in-progress run must not hide the last two completed failures.
        completed = [r for r in rows if r.get('status') in ('pass', 'fail')]
        r = rows[-1]; tail = 0
        for old in reversed(completed):
            if old.get('status') != 'fail': break
            tail += 1
        result['lanes'][lane] = {'status': r.get('status'), 'latest_run': r.get('run_id'), 'consecutive_failures': tail,
                                'source_sha': r.get('source_sha'), 'runtime_sha': r.get('runtime_sha'), 'standard_version': r.get('standard_version')}
        if any(r.get(k) != result.get(k) for k in ('source_sha', 'standard_version', 'manifest_hash', 'mode')) or r.get('runtime_sha') != r.get('source_sha'):
            result['errors']['drift:' + lane] = 'source_runtime_policy_drift'
        try:
            started = dt.datetime.fromisoformat(r['started_at'])
            if not started.tzinfo or not 0 <= (now - started).total_seconds() <= 48 * 3600: raise ValueError()
            if r.get('status') not in ('pass', 'fail', 'running'): raise ValueError()
        except (KeyError, ValueError, TypeError): result['errors']['stale:' + lane] = 'run_stale_or_invalid'
        if result['mode'] == 'strict' and tail >= 2: result['errors']['fail:' + lane] = 'strict_lane_failed_twice'
    try:
        manifest = json.loads(manifest_path(state, qa).read_text())
        if not isinstance(manifest, dict) or manifest.get('schema') != 'qa.fleet.run/v1': raise ValueError('manifest_invalid')
        result['qa_run'] = manifest.get('run_id')
        for key in ('source_sha', 'standard_version', 'manifest_hash', 'mode'):
            if manifest.get(key) != result.get(key): result['errors']['qa:' + key] = 'qa_run_drift'
        if manifest.get('runtime_sha') != manifest.get('source_sha'): result['errors']['qa:runtime'] = 'runtime_sha_drift'
        result['all_discovered_tested'] = manifest.get('all_discovered_tested')
        report = manifest.get('report') or {}
        try:
            raw = Path(report['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != report.get('sha256'): raise ValueError()
            rpt = json.loads(raw)
            if (rpt.get('system_health') or {}).get('all_discovered_tested') != result['all_discovered_tested']: raise ValueError()
        except (OSError, KeyError, ValueError, TypeError):
            result['all_discovered_tested'] = None
            result['errors']['qa:report'] = 'report_missing_or_hash_drift'
    except (OSError, ValueError): result['errors']['qa:manifest'] = 'missing_run_manifest'
    # Outbound emits an audit summary; without it zero cannot be asserted.
    audit = Path(state) / 'out/qa-fleet/outbound.json'
    try:
        a = json.loads(audit.read_text())
        if not isinstance(a, dict): raise ValueError('audit_invalid')
        if any(a.get(k) != result.get(k) for k in ('source_sha', 'standard_version', 'manifest_hash', 'mode')): raise ValueError('outbound_drift')
        count = a.get('missing_evidence_outbound')
        if type(count) is not int or count < 0: raise ValueError('outbound_count_missing')
        result['missing_evidence_outbound'] = count
        checked = dt.datetime.fromisoformat(a['checked_at'])
        if not checked.tzinfo or not 0 <= (now - checked).total_seconds() <= 48 * 3600: raise ValueError('outbound_stale')
        if count > 0: result['errors']['outbound'] = 'missing_evidence_outbound'
        rejected = a.get('rejected_without_evidence')
        if type(rejected) is not int or rejected < 0: raise ValueError('outbound_rejected_count_missing')
        if rejected: result['errors']['outbound-rejected'] = 'product_outbound_rejected_without_evidence'
    except (OSError, ValueError, KeyError, TypeError):
        if result['mode'] in ('canary', 'strict'): result['errors']['outbound'] = 'outbound_audit_missing_or_drift'
    if result['mode'] == 'strict' and result['all_discovered_tested'] is not True: result['errors']['strict-tested'] = 'all_discovered_tested_missing_or_false'
    return result


def capture_receipt(state, run_file, fleet_status_file, stdcheck_file, qa=None):
    """Bind a completed scheduler run to real observer files; never synthesize a pass."""
    run = json.loads(Path(run_file).read_text())
    status = inspect(state, qa)
    fleet = json.loads(Path(fleet_status_file).read_text())
    check = json.loads(Path(stdcheck_file).read_text())
    if status['errors'] or run.get('status') != 'pass' or run.get('exit_code') != 0 or not run.get('finished_at'):
        raise ValueError('canary_run_incomplete')
    if status.get('mode') != 'canary' or status['lanes'].get(run.get('lane'), {}).get('latest_run') != run.get('run_id'):
        raise ValueError('canary_scheduler_drift')
    keys = ('source_sha', 'standard_version', 'manifest_hash', 'mode')
    if any(run.get(k) != status.get(k) for k in keys) or run.get('runtime_sha') != status.get('source_sha'):
        raise ValueError('canary_binding_drift')
    for observed in (fleet.get('qa_standard'), check.get('qa_standard')):
        if not isinstance(observed, dict) or observed.get('errors') or any(observed.get(k) != run.get(k) for k in keys):
            raise ValueError('canary_observer_drift')
        if observed.get('lanes', {}).get(run['lane'], {}).get('latest_run') != run['run_id']:
            raise ValueError('canary_observer_run_missing')
    if (check.get('results') or {}).get('fleet_standard') != {} or check.get('errors'):
        raise ValueError('canary_stdcheck_incomplete')
    manifest_file = manifest_path(state, qa)
    manifest = json.loads(manifest_file.read_text())
    if status['all_discovered_tested'] is not True or status['missing_evidence_outbound'] != 0:
        raise ValueError('canary_report_or_outbound_incomplete')
    meta = {k: run[k] for k in ('run_id', 'source_sha', 'runtime_sha', 'standard_version', 'manifest_hash', 'mode')}
    def observed(file):
        raw = Path(file).read_bytes()
        return dict(meta, ok=True, artifact={'path': str(Path(file).resolve()), 'sha256': hashlib.sha256(raw).hexdigest()})
    return {'scheduler': dict(run, ok=True),
            'report': dict(observed(manifest['report']['path']), all_discovered_tested=True, report_run_id=manifest.get('report_run_id')),
            'fleet_status': observed(fleet_status_file), 'stdcheck': observed(stdcheck_file),
            'outbound': dict(observed(Path(state) / 'out/qa-fleet/outbound.json'), missing_evidence_outbound=0)}


def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('action', choices=['start', 'finish', 'inspect', 'capture']); ap.add_argument('--state', required=True)
    ap.add_argument('--job', default='qa'); ap.add_argument('--trigger', default='scheduler'); ap.add_argument('--file'); ap.add_argument('--rc', type=int, default=0); ap.add_argument('--report'); ap.add_argument('--qa'); ap.add_argument('--result'); ap.add_argument('--manifest')
    ap.add_argument('--fleet-status'); ap.add_argument('--stdcheck')
    a = ap.parse_args()
    if a.action == 'start': print(record_start(a.state, a.job, a.trigger))
    elif a.action == 'finish':
        report = a.report
        if a.result:
            text = Path(a.result).read_text(); decoder = json.JSONDecoder()
            for i, ch in enumerate(text):
                if ch != '{': continue
                try:
                    value, _ = decoder.raw_decode(text[i:])
                    if isinstance(value, dict) and isinstance(value.get('report'), str): report = value['report']
                except ValueError: pass
        doc = record_finish(a.file, a.rc, report)
        if a.manifest: write_json(a.manifest, doc)
    elif a.action == 'capture': print(json.dumps(capture_receipt(a.state, a.file, a.fleet_status, a.stdcheck, a.qa)))
    else: print(json.dumps(inspect(a.state, a.qa)))


def audit_outbound(state, rejected=False):
    """The local executor is the sole writer; record a denial, never its payload."""
    import jsonrec
    meta = binding(); path = str(Path(state) / 'out/qa-fleet/outbound.json')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    def update(doc):
        if any(doc.get(k) != meta[k] for k in ('source_sha', 'standard_version', 'manifest_hash', 'mode')):
            doc.clear(); doc.update(meta, missing_evidence_outbound=0, rejected_without_evidence=0)
        if rejected: doc['rejected_without_evidence'] += 1
        doc['checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        return True
    jsonrec.update(path, update, default={})


def ready_fingerprint(body):
    """Read existing Pi/Grok origin fields; never infer a fingerprint from prose."""
    fp = r'mq1-[0-9a-f]{20}'
    patterns = (
        rf'^[ \t]*Fingerprint[:：][ \t]*({fp})[ \t]*$',  # Grok body / commit trailer
        rf'^- 关联 Issue / 需求：QA 报告 PR-\d+ P[012]，指纹 ({fp})(?=；|[ \t]*$)',  # prfix.pr_body
        rf'^[ \t]*({fp})(?: r[1-9]\d*)?[ \t]*$',  # h_fixpr's body_must_contain line
    )
    found = {value for pattern in patterns for value in re.findall(pattern, body or '', re.M)}
    return next(iter(found)) if len(found) == 1 else None  # conflicting origins stay unknown


def require_product_evidence(action):
    """Enforce at the outbound boundary, including actions queued under an older policy."""
    _, policy, _ = configs()
    if policy['mode'] in ('legacy', 'shadow'): return
    if policy['mode'] == 'rollback': raise ValueError('rollback_product_outbound_disabled')
    import lib
    try:
        if action.get('type') == 'gh.pr.ready' and (not action.get('qa') or not action.get('fingerprint')):
            raise ValueError('ready_origin_missing')
        n = int(action.get('qa') or action.get('pr') or 0)
        report = json.loads((Path(lib.RPT) / f'PR-{n}.json').read_text())
        source = next(p for p in report.get('prs', []) if p.get('number') == n)
        fp = action.get('fingerprint') or action.get('key', '').split(':')[-1]
        finding = next(f for f in source.get('findings', []) if f.get('fingerprint') == fp)
        lib.read_finding_evidence(finding, source)
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        audit_outbound(lib.BASE, rejected=True)
        raise ValueError('product_outbound_evidence_missing') from None


if __name__ == '__main__': main()
