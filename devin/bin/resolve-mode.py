#!/usr/bin/env python3
"""Resolve the Devin session mode of one task kind into legal AutomationSessionSettings (PraiseZhu/panoptes#312).

Usage: resolve-mode.py TASK_KIND (--available-modes a,b,c | --schemas FILE) [--routing FILE]
       resolve-mode.py --validate SETTINGS.json [--available-modes a,b,c | --schemas FILE]

TASK_KIND is a key of devin/conf/routing.json "tasks" or one of its aliases (A1..A4, bot1..bot3).
--schemas FILE: JSON from GET /v3/organizations/{org_id}/automations/schemas (the first "available_devin_modes" list found
anywhere in it) or a plain JSON list of modes.
Devin API contract (AutomationSessionSettings):
  preferred preview tier (swe-2-*) listed in available_devin_modes -> {"devin_mode": preferred, "fallback_devin_mode": fallback}
  preview tier not listed (not opened yet, or withdrawn)            -> {"devin_mode": fallback}  (no fallback_devin_mode key)
  a stable tier or null devin_mode carrying fallback_devin_mode is rejected (--validate, and every resolved result).
Prints one JSON object: {task_kind, platform, api_session_settings, audit{requested_mode, effective_mode, fallback_used,
fallback_reason}}. --validate prints {"ok": true}. Exit: 0 ok, 1 routing or contract error, 2 usage.
"""
import argparse
import json
import os
import sys

DEFAULT_ROUTING = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf", "routing.json")
FALLBACK_KEY = "fallback_devin_mode"


class RoutingError(Exception):
    pass


def load_routing(path=DEFAULT_ROUTING):
    with open(path, encoding="utf-8") as f:
        routing = json.load(f)
    prefix, stable = routing.get("preview_mode_prefix"), routing.get("stable_modes")
    if not isinstance(prefix, str) or not prefix or not isinstance(stable, list) or not stable:
        raise RoutingError(f"{path}: preview_mode_prefix and stable_modes are required")
    for kind, task in (routing.get("tasks") or {}).items():
        pref, fb = task.get("preferred_devin_mode"), task.get(FALLBACK_KEY)
        if not isinstance(pref, str) or not pref.startswith(prefix):
            raise RoutingError(f"{kind}: preferred_devin_mode must be a {prefix}* preview tier (got {pref!r})")
        if fb not in stable:
            raise RoutingError(f"{kind}: fallback_devin_mode must be one of {stable} (got {fb!r})")
        if task.get("platform") != "linux":
            raise RoutingError(f"{kind}: platform must be linux in this phase (got {task.get('platform')!r})")
    return routing


def select_task(routing, kind):
    tasks = routing.get("tasks") or {}
    if kind in tasks:
        return kind, tasks[kind]
    for name, task in tasks.items():
        if kind in (task.get("aliases") or []):
            return name, task
    raise RoutingError(f"unknown task kind {kind!r} (expected one of {sorted(tasks)} or their aliases)")


def is_preview(mode, routing):
    return isinstance(mode, str) and mode.startswith(routing["preview_mode_prefix"])


def validate_settings(settings, routing, available=None):
    """Raise RoutingError unless settings is a legal AutomationSessionSettings mode pair."""
    if not isinstance(settings, dict):
        raise RoutingError(f"session settings must be an object (got {type(settings).__name__})")
    mode = settings.get("devin_mode")
    has_fallback = FALLBACK_KEY in settings
    if not is_preview(mode, routing):
        if has_fallback:
            raise RoutingError(f"devin_mode {mode!r} is not a preview tier: {FALLBACK_KEY} must be omitted "
                               f"(got {settings[FALLBACK_KEY]!r})")
        if mode is not None and available is not None and mode not in available:
            raise RoutingError(f"devin_mode {mode!r} is not in available_devin_modes {sorted(available)}")
        return settings
    fb = settings.get(FALLBACK_KEY)
    if fb is None or is_preview(fb, routing):
        raise RoutingError(f"preview devin_mode {mode!r} needs a stable {FALLBACK_KEY} (got {fb!r})")
    if available is not None and (mode not in available or fb not in available):
        raise RoutingError(f"devin_mode {mode!r} / {FALLBACK_KEY} {fb!r} must both be in available_devin_modes "
                           f"{sorted(available)}")
    return settings


def resolve(kind, routing, available):
    """-> {task_kind, platform, api_session_settings, audit} for the org's current available_devin_modes."""
    name, task = select_task(routing, kind)
    available = set(available)
    pref, fb = task["preferred_devin_mode"], task[FALLBACK_KEY]
    if fb not in available:
        raise RoutingError(f"{name}: stable fallback {fb!r} is not in available_devin_modes {sorted(available)}")
    if pref in available:
        settings, reason = {"devin_mode": pref, FALLBACK_KEY: fb}, None
    else:
        settings, reason = {"devin_mode": fb}, f"{pref} not in available_devin_modes"
    validate_settings(settings, routing, available)
    return {"task_kind": name, "platform": task["platform"], "api_session_settings": settings,
            "audit": {"requested_mode": pref, "effective_mode": settings["devin_mode"],
                      "fallback_used": reason is not None, "fallback_reason": reason}}


def find_available(doc):
    if isinstance(doc, list):
        return doc if all(isinstance(m, str) for m in doc) else None
    if isinstance(doc, dict):
        if isinstance(doc.get("available_devin_modes"), list):
            return find_available(doc["available_devin_modes"])
        for value in doc.values():
            found = find_available(value) if isinstance(value, (dict, list)) else None
            if found is not None:
                return found
    return None


def read_available(args):
    if args.available_modes is not None:
        return [m for m in (s.strip() for s in args.available_modes.split(",")) if m]
    if args.schemas is None:
        return None
    with open(args.schemas, encoding="utf-8") as f:
        found = find_available(json.load(f))
    if found is None:
        raise RoutingError(f"{args.schemas}: no available_devin_modes list found")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task_kind", nargs="?")
    ap.add_argument("--routing", default=DEFAULT_ROUTING)
    ap.add_argument("--available-modes")
    ap.add_argument("--schemas")
    ap.add_argument("--validate", metavar="SETTINGS_JSON")
    args = ap.parse_args(argv)
    if (args.task_kind is None) == (args.validate is None):
        ap.error("give either TASK_KIND or --validate SETTINGS.json")
    try:
        routing = load_routing(args.routing)
        available = read_available(args)
        if args.validate:
            with open(args.validate, encoding="utf-8") as f:
                validate_settings(json.load(f), routing, None if available is None else set(available))
            print(json.dumps({"ok": True}))
            return 0
        if available is None:
            ap.error("TASK_KIND needs --available-modes or --schemas")
        print(json.dumps(resolve(args.task_kind, routing, available), ensure_ascii=False))
        return 0
    except (RoutingError, OSError, ValueError) as e:
        print(f"resolve-mode: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
