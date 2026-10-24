#!/usr/bin/env python3
"""Read-only credential inventory for the Pi line. Prints set/unset + a read-only probe result; never values."""
import urllib.request
from lib import *
import outbound
NEED = {  # name: (who uses it, minimal scope, read-only probe)
 "DEEPSEEK_API_KEY": ("all model jobs (pi-auto)", "tap llm-proxy key", "models"),
 "MIVO_SLACK_BOT_TOKEN": ("outbound slack.post/dm (日报, alerts)", "chat:write (+channels:read for dry-run channel check, users:read)", "slack"),
 "MIVO_FEISHU_APP_ID": ("outbound feishu", "app id", None),
 "MIVO_FEISHU_APP_SECRET": ("outbound feishu.dm / group", "im:message:send_as_bot, contact:user.id:readonly, im:chat:readonly", "feishu"),
 "SLACK_USER_TOKEN": ("Jasper Slack scan", "search:read, channels:history, groups:history, im:history, users:read", None),
 "GOOGLE_OAUTH_CLIENT_ID": ("Jasper Gmail/Calendar", "OAuth client (installed app)", None),
 "GOOGLE_OAUTH_CLIENT_SECRET": ("Jasper Gmail/Calendar", "OAuth client secret", None),
 "GMAIL_REFRESH_TOKEN": ("Jasper mail scan", "https://www.googleapis.com/auth/gmail.readonly", None),
 "GCAL_REFRESH_TOKEN": ("Jasper calendar", "https://www.googleapis.com/auth/calendar.readonly", None),
}
def probe(kind):
    try:
        if kind == "slack": j = outbound.slack("auth.test"); return f"ok team={j.get('team')} bot={j.get('user')}"
        if kind == "feishu": outbound.feishu_token(); return "ok tenant token"
        if kind == "models":
            req = urllib.request.Request("https://llm-proxy.tapsvc.com/v1/models", headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"})
            return f"ok HTTP {urllib.request.urlopen(req, timeout=30).status}"
    except Exception as e: return f"FAIL {str(e)[:100]}"
def main():
    secret_env(); rows = []
    for k, (who, scope, p) in NEED.items():
        s = bool(os.environ.get(k)); rows.append((k, "set" if s else "MISSING", (probe(p) if s and p else "-"), who, scope))
    r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True)
    rows.append(("gh (hosts.yml)", "set" if r.returncode == 0 else "MISSING", f"ok login={r.stdout.strip()}" if r.returncode == 0 else r.stderr[:80], "gh writes as PraiseZhu", "repo"))
    md = "| credential | state | read-only probe | used by | minimal scope |\n|---|---|---|---|---|\n" + "\n".join("| " + " | ".join(x) + " |" for x in rows)
    print(md); write_atomic(f"{BASE}/out/creds/{today()}.md", f"# credential inventory {now_cst().isoformat(timespec='seconds')}\nSource: env, ~/.pi-fallback-secret/env, ~/.config/mivo-qa/secrets.env\n\n{md}\n")
if __name__ == "__main__": main()
