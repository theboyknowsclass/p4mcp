#!/usr/bin/env python3
"""p4mcp_patches.py - small fixes to the installed p4mcp-server package.

pip install/upgrade of p4mcp-server overwrites the package, so these patches are
re-applied automatically: p4mcp-call.py runs ensure_patched() on every start, and
since Cline uses the same venv, one CLI call after an upgrade fixes Cline too.

Each patch replaces an exact line. If the line is not there (already patched, or
upstream changed it), the patch is skipped. A backup <file>.orig is kept once.

Run by hand:  python p4mcp_patches.py          apply and report
              python p4mcp_patches.py --check  report only (exit 1 if a patch is missing)
"""
import importlib.util
import sys
from pathlib import Path

PATCHES = [
    {
        "name": "case-insensitive changelist owner check",
        # Owner "gbhatnagar" vs p4 info userName "GBhatnagar" on a case-insensitive
        # server: every modify_changelists update/delete/move_files failed with
        # "not owned by current user".
        "file": "services/changelist_services.py",
        "old": "if changelist['user'] != p4.run(\"info\")[0][\"userName\"]:",
        "new": "if changelist['user'].lower() != p4.run(\"info\")[0][\"userName\"].lower():",
    },
]


def package_dir():
    spec = importlib.util.find_spec("p4mcp")
    if not spec or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def ensure_patched(apply=True, log=None):
    """Apply missing patches. Returns list of (name, state); state is
    'ok' (already in place), 'applied', 'missing' (check only) or 'skipped'."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    root = package_dir()
    if root is None:
        return []
    results = []
    for p in PATCHES:
        path = root / p["file"]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            results.append((p["name"], "skipped"))
            continue
        if p["new"] in text:
            results.append((p["name"], "ok"))
        elif p["old"] in text:
            if not apply:
                results.append((p["name"], "missing"))
                continue
            backup = path.with_name(path.name + ".orig")
            if not backup.exists():
                backup.write_text(text, encoding="utf-8")
            path.write_text(text.replace(p["old"], p["new"]), encoding="utf-8")
            log(f"[p4mcp-patch] applied: {p['name']} ({path})")
            results.append((p["name"], "applied"))
        else:
            results.append((p["name"], "skipped"))
    return results


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    res = ensure_patched(apply=not check, log=lambda m: None)
    if not res:
        print("p4mcp package not found in this Python")
        sys.exit(2)
    for name, state in res:
        print(f"{state:8} {name}")
    sys.exit(1 if any(s == "missing" for _, s in res) else 0)
