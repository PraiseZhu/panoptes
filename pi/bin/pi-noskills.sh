#!/bin/bash
# pi for the Pi line's coding steps (lib.pi skills=False, via pi-task's PI_BIN): no skill discovery. ~/.pi/agent/settings.json
# loads ~/.claude/skills for interactive use; in a prfix step that was 36 KB of unrelated Claude Code skills per request (10-24).
exec "${PI_REAL_BIN:-$HOME/.local/bin/pi}" --no-skills "$@"
