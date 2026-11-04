"""Shared commit identity. GitHub noreply address so pushes pass "Block command line pushes that expose my email".
11-05 (PM): every commit the Pi line pushes -- with the Praise QA App token or the PAT -- is authored AND committed as Praise with the
noreply address <id>+PraiseZhu@users.noreply.github.com (id from `gh api users/PraiseZhu`: 290989140), never as the App or a box default."""
GH_LOGIN = "PraiseZhu"
GH_USER_ID = 290989140
NAME = "Praise"
NOREPLY_EMAIL = f"{GH_USER_ID}+{GH_LOGIN}@users.noreply.github.com"
GIT_ID = ["-c", f"user.name={NAME}", "-c", f"user.email={NOREPLY_EMAIL}"]   # git -c args (config level)
GIT_COMMIT_VERBS = frozenset({"commit", "am", "merge", "cherry-pick", "revert", "rebase", "pull"})


def env(base=None):
    """base env (default os.environ) + author AND committer = Praise / noreply: the env vars beat any repo-local or global config"""
    import os
    e = dict(os.environ if base is None else base)
    e.update(GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=NOREPLY_EMAIL, GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=NOREPLY_EMAIL)
    return e


def ident(): return f"{NAME} <{NOREPLY_EMAIL}>"
