import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import git_identity, outbound, prfix, infra_outbound, infrafix  # noqa: E402

class T(unittest.TestCase):
    def test_noreply(self):
        e = git_identity.NOREPLY_EMAIL
        self.assertTrue(e.endswith("@users.noreply.github.com"))
        self.assertEqual(outbound.GIT_EMAIL, e); self.assertEqual(infra_outbound.GIT_EMAIL, e)
        self.assertIn(f"user.email={e}", prfix.GIT_ID)
        if not os.environ.get("INFRA_GIT_EMAIL"): self.assertIn(f"user.email={e}", infrafix.GIT_ID)

if __name__ == "__main__": unittest.main()
