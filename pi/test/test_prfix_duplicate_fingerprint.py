"""Fingerprint claim races use real processes/flocks, never a product repository."""
import json, os, socket, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import lib

class FingerprintClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
    def claim(self, owner='pr-1', **kwargs):
        return lib.claim_fingerprint(self.tmp.name, 'owner/repo', 'mq1-' + 'a' * 20, owner, **kwargs)
    def test_one_active_claim_across_source_prs(self):
        lease = self.claim()
        try:
            with self.assertRaises(lib.FingerprintBusy): self.claim('pr-2')
        finally: lib.release_fingerprint(lease)
        lease = self.claim('pr-2'); lib.release_fingerprint(lease)
    def test_process_race_has_one_winner(self):
        start_r, start_w = os.pipe(); result_r, result_w = os.pipe(); release_r, release_w = os.pipe(); kids = []
        for i in range(2):
            pid = os.fork()
            if pid == 0:
                os.close(start_w); os.close(result_r); os.close(release_w); os.read(start_r, 1)
                try:
                    lease = self.claim('pr-' + str(i)); os.write(result_w, b'Y'); os.read(release_r, 1); lib.release_fingerprint(lease)
                except lib.FingerprintBusy: os.write(result_w, b'N')
                finally: os._exit(0)
            kids.append(pid)
        os.close(start_r); os.close(result_w); os.close(release_r); os.write(start_w, b'xx'); os.close(start_w)
        got = os.read(result_r, 1) + os.read(result_r, 1); os.close(result_r); os.write(release_w, b'xx'); os.close(release_w)
        for pid in kids: self.assertEqual(os.waitpid(pid, 0)[1], 0)
        self.assertEqual(sorted(got), sorted(b'YN'))
    def abandoned(self, lease, **kwargs):
        path = lease['path']; value = dict(lease['lease'], **kwargs)
        lib.release_fingerprint(lease); Path(path + '.json').write_text(json.dumps(value))
    def test_stale_owner_requires_dead_pid_and_age_and_same_host(self):
        lease = self.claim(now=100)
        self.abandoned(lease, pid=999999999)
        with self.assertRaises(lib.FingerprintBusy): self.claim('new', now=200)
        current = self.claim('new', now=4000); lib.release_fingerprint(current)
        lease = self.claim(now=100); self.abandoned(lease, pid=os.getpid())
        with self.assertRaises(lib.FingerprintBusy): self.claim('new', now=4000)
    def test_outbound_and_unknown_host_never_expire_automatically(self):
        lease = self.claim(now=100); lib.release_fingerprint(lease, reserve=True)
        with self.assertRaises(lib.FingerprintBusy): self.claim('new', now=999999)
    def test_dispatch_handoff_only_the_matching_owner_and_nonce_can_adopt(self):
        lease = self.claim(); nonce = lease['lease']['nonce']
        lib.release_fingerprint(lease, reserve='dispatch')
        for owner, token in [('pr-2', nonce), ('pr-1', 'wrong')]:
            with self.assertRaises(lib.FingerprintBusy): self.claim(owner, handoff_nonce=token)
        current = self.claim(handoff_nonce=nonce)
        with self.assertRaises(lib.FingerprintBusy): self.claim('pr-2')
        lib.release_fingerprint(current)

    def test_unadopted_dispatch_expires_even_when_dispatcher_is_alive(self):
        lease = self.claim(now=100); lib.release_fingerprint(lease, reserve='dispatch')
        with self.assertRaises(lib.FingerprintBusy): self.claim('new', now=200)
        current = self.claim('new', now=4000); lib.release_fingerprint(current)

    def test_failed_outbound_can_only_be_replaced_by_its_owner_and_action(self):
        lease = self.claim(); lib.release_fingerprint(lease, reserve='action-1')
        with self.assertRaises(lib.FingerprintBusy): self.claim('pr-2', replaces='action-1')
        with self.assertRaises(lib.FingerprintBusy): self.claim(replaces='other')
        current = self.claim(replaces='action-1'); lib.release_fingerprint(current)

    def test_prwatch_reserves_before_launch_and_releases_failed_spawn(self):
        import prwatch as W
        from types import SimpleNamespace
        flag = Path(self.tmp.name) / 'enabled'; flag.touch()
        root = self.tmp.name + '/out/praise-pr/fingerprints'
        fp = 'mq1-' + 'a' * 20
        adopted = []
        def popen(*args, **kwargs):
            env = kwargs['env']
            self.assertEqual(env['PRFIX_CLAIM_TICKET'], '10:' + fp)
            with self.assertRaises(lib.FingerprintBusy): lib.claim_fingerprint(root, lib.REPO, fp, 'another')
            held = lib.claim_fingerprint(root, lib.REPO, fp, '10:' + fp, handoff_nonce=env['PRFIX_CLAIM_NONCE'])
            adopted.append(held)
        with mock.patch.multiple(W, BASE=self.tmp.name, FIX_FLAG=str(flag), DRY=False), \
             mock.patch.object(W, 'kick_finished'), mock.patch.object(W, 'sample_peaks'), \
             mock.patch.object(W, 'proc_table', return_value={}), mock.patch.object(W, 'prfix_runs', return_value=([], [])), \
             mock.patch.object(W, 'inputs_sig', return_value='sig'), mock.patch.object(W, 'admit_block', return_value=(None, 8000)), \
             mock.patch.object(W, 'jload', return_value={}), \
             mock.patch.object(W.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps([dict(qa=10, fp=fp, sev='P1')]))), \
             mock.patch.object(W.subprocess, 'Popen', side_effect=popen):
            W.dispatch_fix({})
            self.assertEqual(len(adopted), 1); lib.release_fingerprint(adopted[0])
            with mock.patch.object(W.subprocess, 'Popen', side_effect=OSError('spawn failed')):
                with self.assertRaises(OSError): W.dispatch_fix({})
            held = lib.claim_fingerprint(root, lib.REPO, fp, 'another')
            lib.release_fingerprint(held)

    def test_wrong_owner_cannot_release(self):
        lease = self.claim(); original = dict(lease['lease']); lease['lease'] = dict(original, nonce='wrong')
        with self.assertRaises(ValueError): lib.release_fingerprint(lease)
        self.assertEqual(json.loads(Path(lease['path'] + '.json').read_text())['nonce'], original['nonce'])

if __name__ == '__main__': unittest.main()
