"""Fleet handoff contracts: isolated files, no network/model/deployment."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import qa_standard as Q


def fleet_fixture(root, now=None, mode='shadow'):
    root = Path(root); now = now or dt.datetime.now(dt.timezone.utc)
    conf = root / 'conf'; conf.mkdir(parents=True, exist_ok=True)
    for name in ('qa-fleet.json', 'qa-standard.json', 'qa-standard-brief.md'):
        (conf / name).write_bytes((Q.CODE / 'conf' / name).read_bytes())
    standard = json.loads((conf / 'qa-standard.json').read_text()); standard['mode'] = mode
    Q.write_json(conf / 'qa-standard.json', standard)
    env = {'PI_QA_FLEET_FILE': str(conf / 'qa-fleet.json'), 'PI_QA_STANDARD_FILE': str(conf / 'qa-standard.json'),
           'PI_QA_STANDARD_BRIEF': str(conf / 'qa-standard-brief.md'), 'PI_QA_FLEET_MANIFEST': str(root / 'manifest.json')}
    with mock.patch.dict(os.environ, env):
        meta = Q.binding()
        for lane, job in [('qa', 'qa'), ('repair', 'prfix'), ('retest', 'opsfix_retest'), ('ops', 'opsfix'), ('outbound', 'outbound')]:
            doc = dict(meta, schema='qa.fleet.run/v1', runtime_sha=meta['source_sha'], run_id='fleet-' + lane, lane=lane,
                       job=job, started_at=now.isoformat(), finished_at=now.isoformat(), status='pass', exit_code=0, ok=True)
            Q.write_json(root / 'out/qa-fleet/runs' / ('fleet-' + lane + '.json'), doc)
            if lane == 'qa':
                report = root / 'report.json'; Q.write_json(report, {'system_health': {'all_discovered_tested': True}, 'run': {'id': 'qa-real-run'}})
                doc = Q.record_finish(root / 'out/qa-fleet/runs/fleet-qa.json', 0, report)
                Q.write_json(root / 'manifest.json', doc)
        Q.write_json(root / 'out/qa-fleet/outbound.json', dict(meta, checked_at=now.isoformat(), missing_evidence_outbound=0, rejected_without_evidence=0))
    return env


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.env = fleet_fixture(self.root, self.now)
        self.patch = mock.patch.dict(os.environ, self.env); self.patch.start(); self.addCleanup(self.patch.stop)

    def test_all_model_lanes_and_live_mode_reload(self):
        for session, lane in [('pi-line-prfix-1', 'repair'), ('opsfix-retest-1', 'retest'), ('opsreview-1', 'ops'), ('dailyretro-1', 'ops'), ('grokline', 'repair')]:
            text = Q.prompt_context(session)
            self.assertIn('lane=' + lane, text); self.assertIn('mode=shadow', text)
        policy = json.loads(Q.paths()[1].read_text()); policy['mode'] = 'strict'; Q.write_json(Q.paths()[1], policy)
        self.assertIn('mode=strict', Q.prompt_context('pi-line-prfix-1'))
        # Verify the actual shared model boundary passes the brief into the tool, without invoking it.
        import lib
        with mock.patch.object(lib.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout='', stderr='fake failure')) as run, mock.patch.object(lib, 'sandbox_prefix', return_value=[]), mock.patch.object(lib, 'BASE', str(self.root)):
            with self.assertRaises(RuntimeError): lib.pi('work', str(self.root), 'task body', 'pi-line-prfix-1', skills=False)
            self.assertIn('qa-standard', str(run.call_args.args[0]))
            self.assertIn('task body', str(run.call_args.args[0]))

    def test_missing_brief_version_and_lane_are_fail_visible(self):
        brief = Q.paths()[2]; original = brief.read_text()
        for text in (original.replace('qa-standard/v1', 'qa-standard/v0'), original.replace('## qa', '## unknown')):
            brief.write_text(text); self.assertIn('config', Q.inspect(self.root)['errors'])
            with self.assertRaises(ValueError): Q.prompt_context('prfix')
        brief.unlink(); self.assertIn('config', Q.inspect(self.root)['errors'])

    def test_missing_run_and_source_policy_mode_drift(self):
        self.assertEqual(Q.inspect(self.root, now=self.now)['errors'], {})
        p = self.root / 'out/qa-fleet/runs/fleet-repair.json'; doc = json.loads(p.read_text())
        for key in ('source_sha', 'runtime_sha', 'standard_version', 'manifest_hash', 'mode'):
            Q.write_json(p, dict(doc, **{key: 'drift'})); self.assertIn('drift:repair', Q.inspect(self.root, now=self.now)['errors'])
        p.unlink(); self.assertIn('lane:repair', Q.inspect(self.root, now=self.now)['errors'])

    def test_strict_streak_alert_even_while_next_run_is_running_and_hash_drift(self):
        policy = json.loads(Q.paths()[1].read_text()); policy['mode'] = 'strict'; Q.write_json(Q.paths()[1], policy)
        meta = Q.binding()
        for n in range(3):
            Q.write_json(self.root / f'out/qa-fleet/runs/fleet-{n}.json', dict(meta, schema='qa.fleet.run/v1', lane='repair', runtime_sha=meta['source_sha'], run_id=f'fleet-{n}', started_at=(self.now + dt.timedelta(seconds=n + 1)).isoformat(), status='fail' if n < 2 else 'running'))
        self.assertIn('fail:repair', Q.inspect(self.root, now=self.now + dt.timedelta(seconds=4))['errors'])
        (self.root / 'report.json').write_text('{}')
        result = Q.inspect(self.root, now=self.now)
        self.assertIsNone(result['all_discovered_tested']); self.assertIn('qa:report', result['errors'])
        self.assertIn('stale:qa', Q.inspect(self.root, now=self.now + dt.timedelta(days=3))['errors'])

    def test_strict_outbound_rechecks_original_artifacts_and_alerts_rejections(self):
        from test_prfix_evidence_gate import evidence_finding
        import lib
        f = evidence_finding(self.root / 'evidence')
        policy = json.loads(Q.paths()[1].read_text()); policy['mode'] = 'strict'; Q.write_json(Q.paths()[1], policy)
        report = self.root / 'PR-7.json'
        source = dict(number=7, tested_sha=f['tested_sha'], base_sha=f['base_sha'], findings=[f])
        def save(): Q.write_json(report, {'prs': [source]})
        action = {'qa': 7, 'fingerprint': f['fingerprint']}
        with mock.patch.object(lib, 'BASE', str(self.root)), mock.patch.object(lib, 'RPT', str(self.root)):
            save(); Q.require_product_evidence(action)
            for bad in [dict(f, pending=True), dict(f, decision={'status': 'pending'}), dict(f, status='environment_unavailable'), dict(f, validation={'status': 'invalid'})]:
                source['findings'] = [bad]; save()
                with self.assertRaisesRegex(ValueError, 'evidence_missing'): Q.require_product_evidence(action)
            source['findings'] = [f]; save(); (self.root / 'evidence/head-result.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'evidence_missing'): Q.require_product_evidence(action)
        self.assertIn('outbound-rejected', Q.inspect(self.root, now=dt.datetime.now(dt.timezone.utc))['errors'])
        policy['mode'] = 'rollback'; Q.write_json(Q.paths()[1], policy)
        with self.assertRaisesRegex(ValueError, 'rollback'): Q.require_product_evidence(action)
        self.assertTrue((self.root / 'evidence/manifest.json').exists())

    def test_outbound_audit_creates_directory_before_locking(self):
        state = self.root / 'fresh-state'
        Q.audit_outbound(state)
        audit = json.loads((state / 'out/qa-fleet/outbound.json').read_text())
        self.assertEqual(audit['missing_evidence_outbound'], 0)
        Q.audit_outbound(state, rejected=True)
        self.assertEqual(json.loads((state / 'out/qa-fleet/outbound.json').read_text())['rejected_without_evidence'], 1)

    def test_capture_binds_real_observer_files_and_refuses_missing_check(self):
        self.env.update(fleet_fixture(self.root, mode='canary')); os.environ.update(self.env)
        snapshot = Q.inspect(self.root)
        fleet = self.root / 'fleet-status.json'; check = self.root / 'stdcheck.json'
        Q.write_json(fleet, {'qa_standard': snapshot})
        Q.write_json(check, {'qa_standard': snapshot, 'results': {'fleet_standard': {}}, 'errors': {}})
        run = self.root / 'out/qa-fleet/runs/fleet-repair.json'
        captured = Q.capture_receipt(self.root, run, fleet, check)
        self.assertEqual(captured['scheduler']['run_id'], captured['report']['run_id'])
        self.assertTrue(captured['report']['artifact']['sha256'])
        check.unlink()
        with self.assertRaises(OSError): Q.capture_receipt(self.root, run, fleet, check)

    def test_watchdog_surfaces_drift_without_two_extra_polls(self):
        import watchdog
        Q.paths()[2].unlink(); probs = {}
        with mock.patch.object(watchdog, 'BASE', str(self.root)):
            watchdog.qa_standard_check(self.now, probs)
        self.assertIn('qa-standard:config', probs)

if __name__ == '__main__': unittest.main()
