"""Product finding admission: local artifacts only, no model or GitHub writes."""
import copy, hashlib, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import lib


def evidence_finding(root, fp='mq1-' + 'a' * 20, **overrides):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    binding = dict(run_id='fixture-run', tested_sha='1' * 40, base_sha='2' * 40)
    refs, artifacts, steps = [], [], []
    key = hashlib.sha256(b'unit::case').hexdigest()
    for phase in ('head', 'rerun', 'base'):
        ref = phase + '-result'; refs.append(ref)
        result = dict(binding, phase=phase, revision_sha=binding['base_sha' if phase == 'base' else 'tested_sha'],
                      check_id='unit', status='pass' if phase == 'base' else 'fail', exit_code=0 if phase == 'base' else 1,
                      failure_keys=[] if phase == 'base' else [key], passed_cases=[key] if phase == 'base' else [])
        raw = json.dumps(result).encode(); (root / (ref + '.json')).write_bytes(raw)
        artifacts.append(dict(id=ref, path=ref + '.json', sha256=hashlib.sha256(raw).hexdigest(), kind='test-result'))
        steps.append(dict(binding, id='unit-' + phase, status=result['status'], phase=phase,
                          revision_sha=result['revision_sha'], artifact_refs=[ref]))
    projection = dict(binding, id='finding-1', check_id='unit-head', fingerprint=hashlib.sha256(fp.encode()).hexdigest(),
                      status='confirmed', artifact_refs=refs)
    manifest = dict(binding, schema='qa.evidence/v2', status='failed', artifact_refs=refs, artifacts=artifacts, steps=steps, findings=[projection])
    (root / 'manifest.json').write_text(json.dumps(manifest))
    finding = dict(binding, fingerprint=fp, source='check', status='introduced', check_id='unit', test='case', severity='P1',
                   decision=dict(status='confirmed'), decision_status='confirmed', fix_eligible=True, artifact_refs=refs,
                   evidence_root=str(root.resolve()), evidence_manifest='manifest.json',
                   evidence=dict(tested_sha=binding['tested_sha'], base_sha=binding['base_sha'], first_run='fail', rerun='fail', base_run='pass'))
    finding.update(overrides)
    return finding


class EvidenceGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.finding = evidence_finding(self.tmp.name)

    def test_complete_contract(self):
        self.assertEqual(lib.read_finding_evidence(self.finding)['schema'], 'qa.evidence/v2')

    def test_named_base_pass_uses_verified_cases_even_if_the_step_failed(self):
        root = Path(self.tmp.name)
        path = root / 'base-result.json'
        result = json.loads(path.read_text())
        result.update(status='fail', exit_code=1, failure_keys=[hashlib.sha256(b'unit::old').hexdigest()])
        manifest_path = root / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        def save():
            raw = json.dumps(result).encode(); path.write_bytes(raw)
            next(a for a in manifest['artifacts'] if a['id'] == 'base-result')['sha256'] = hashlib.sha256(raw).hexdigest()
            next(s for s in manifest['steps'] if s['phase'] == 'base')['status'] = 'fail'
            manifest_path.write_text(json.dumps(manifest))
        save()
        self.assertEqual(lib.read_finding_evidence(self.finding)['schema'], 'qa.evidence/v2')
        result['passed_cases'] = []; save()
        with self.assertRaisesRegex(ValueError, 'base pass evidence incomplete'):
            lib.read_finding_evidence(self.finding)

    def test_missing_and_non_product_evidence(self):
        for field in ('tested_sha', 'base_sha', 'run_id', 'check_id', 'artifact_refs'):
            with self.subTest(field=field):
                f = copy.deepcopy(self.finding); f.pop(field)
                with self.assertRaises(ValueError): lib.read_finding_evidence(f)
        for status in ('pending_verification', 'timeout', 'environment_unavailable', 'runner_error', 'flaky', 'pre-existing', 'fixed-later'):
            f = dict(self.finding, status=status)
            with self.assertRaises(ValueError): lib.read_finding_evidence(f)
        for flag in ('pending', 'invalid', 'flaky', 'pre_existing', 'low_memory'):
            with self.assertRaises(ValueError): lib.read_finding_evidence(dict(self.finding, **{flag: True}))

    def test_hash_binding_and_source_report_are_verified(self):
        with self.assertRaises(ValueError): lib.read_finding_evidence(self.finding, dict(tested_sha='3' * 40, base_sha='2' * 40))
        p = Path(self.tmp.name) / 'head-result.json'; p.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): lib.read_finding_evidence(self.finding)

    def test_cannot_traverse_or_follow_artifact_symlinks(self):
        for rel in ('../manifest.json', '/manifest.json', 'file://manifest.json'):
            with self.assertRaises(ValueError): lib.read_finding_evidence(dict(self.finding, evidence_manifest=rel))
        root = Path(self.tmp.name); (root / 'alias.json').symlink_to(root / 'manifest.json')
        with self.assertRaises(ValueError): lib.read_finding_evidence(dict(self.finding, evidence_manifest='alias.json'))

    def test_cli_missing_evidence_exits_nonzero_without_outbound(self):
        import prfix
        for field in ('tested_sha', 'base_sha', 'run_id', 'check_id', 'artifact_refs'):
            f = dict(self.finding); f.pop(field)
            row = dict(state='todo', key='1:fp', c=dict(finding=f, qa=1, fp='fp'))
            ctx = dict(links={}, gate_warn=None)
            with mock.patch.object(prfix, 'scrub_env'), mock.patch.object(prfix, 'raise_oom_score'), \
                 mock.patch.object(prfix.lib, 'gh_skip', return_value=False), \
                 mock.patch.object(prfix, 'gather', return_value=ctx), \
                 mock.patch.object(prfix, 'evaluate_all', return_value=[row]), \
                 mock.patch.object(prfix, 'evaluate', return_value=row), \
                 mock.patch.object(prfix, 'bookkeeping', return_value=ctx), \
                 mock.patch.object(prfix, 'prwatch_dispatching', return_value=False), \
                 mock.patch.object(prfix, 'write_manual_orders'), \
                 mock.patch.object(prfix, 'write_atomic'), \
                 mock.patch.object(prfix, 'prio', return_value=0), \
                 mock.patch.object(prfix, 'pi') as model, mock.patch.object(prfix, 'git') as git, \
                 mock.patch.object(prfix, 'record_actions') as actions:
                self.assertNotEqual(prfix.main(['--next']), 0)
                model.assert_not_called(); git.assert_not_called(); actions.assert_not_called()

    def test_prfix_rejects_before_any_model_git_or_outbound(self):
        import prfix
        import argparse
        for field in ('tested_sha', 'base_sha', 'run_id', 'check_id', 'artifact_refs'):
            f = dict(self.finding); f.pop(field)
            with mock.patch.object(prfix, 'pi') as model, mock.patch.object(prfix, 'git') as git, \
                 mock.patch.object(prfix, 'record_actions') as actions, mock.patch.object(prfix, 'write_atomic'), \
                 mock.patch.object(prfix, 'OUT', self.tmp.name):
                out, _ = prfix.fix_one(dict(qa=1, fp=f['fingerprint'], sev='P1', title='fixture', finding=f),
                                      argparse.Namespace(dry_run=True, base=None), '2026-11-16')
                self.assertEqual(out['status'], 'failed'); self.assertEqual(out['kind'], 'deterministic')
                model.assert_not_called(); git.assert_not_called(); actions.assert_not_called()


if __name__ == '__main__': unittest.main()
