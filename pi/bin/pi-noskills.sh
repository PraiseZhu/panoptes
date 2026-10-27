#!/bin/bash
# pi for the Pi line's coding steps (lib.pi skills=False, via pi-task's PI_BIN): no ambient skill discovery, pstack only.
# ~/.pi/agent/settings.json loads ~/.claude/skills for interactive use; in a prfix step that was 36 KB of unrelated Claude Code skills
# per request (10-24). pstack (poteto's skill pack, github.com/cursor/plugins pstack/skills, linked at ~/.pi/agent/skills/pstack;
# Praise 10-27: every Pi run loads it) is passed explicitly: --skill still loads under --no-skills. Missing dir -> run without it.
PSTACK="${PI_PSTACK_DIR:-$HOME/.pi/agent/skills/pstack}"
extra=(); [ -d "$PSTACK" ] && extra=(--skill "$PSTACK")
exec "${PI_REAL_BIN:-$HOME/.local/bin/pi}" --no-skills "${extra[@]}" "$@"
