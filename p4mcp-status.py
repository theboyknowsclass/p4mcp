#!/usr/bin/env python3
"""status.py - your pending changelists, whether they are shelved, and their Swarm review + votes.

One p4mcp-server session for all calls. Uses p4mcp.json next to this file (P4USER = author).
  python status.py [--client <alias>] [--all-reviews]
Output: one line per pending CL: change|client|shelved|review id state votes|description
A review with 2+ current up votes and no down votes is flagged READY.
"""
import argparse, importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pc", HERE / "p4mcp-call.py")
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)


def dig(o):
    if isinstance(o, list): return o
    if isinstance(o, dict):
        for k in ("reviews", "changelists", "shelves", "data", "message", "result"):
            if k in o:
                r = dig(o[k])
                if r is not None: return r
    return None


def unwrap(o):
    while isinstance(o, dict) and any(k in o for k in ("data", "message", "result")):
        o = next(o[k] for k in ("data", "message", "result") if k in o)
    return o[0] if isinstance(o, list) and o else o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client"); ap.add_argument("--config")
    a = ap.parse_args()
    cfg = pc.load_config(a.config)
    user = cfg.get("P4USER") or ""
    s = pc.Session(pc.build_command(cfg), pc.build_env(cfg, a.client)); s.open()

    def call(tool, args):
        r = s.request("tools/call", {"name": tool, "arguments": args})
        t = "".join(i.get("text", "") for i in r.get("content", []))
        try: return json.loads(t)
        except Exception: return {"_raw": t[:300]}

    try:
        pend = dig(call("query_changelists", {"action": "list", "user": user, "status": "pending", "max_results": 500})) or []
        shel = {str(c.get("change")) for c in (dig(call("query_shelves", {"action": "list", "user": user, "max_results": 1000})) or [])}
        revs = dig(call("query_reviews", {"action": "list", "keywords": user.lower(), "keywords_fields": ["author"],
               "state": ["needsReview", "needsRevision", "approved:isPending", "approved"], "fields": ["id", "state", "changes", "commits"],
               "max_results": 100})) or []
        byc = {}
        for r in revs:
            if r.get("commits"): continue  # already committed
            g = dig(call("query_reviews", {"action": "get", "review_id": r["id"], "fields": ["id", "state", "participantsData"]}))
            g = g[0] if g else {}
            votes, ups, downs = [], 0, 0
            for u, p in (g.get("participantsData") or {}).items():
                v = p.get("vote") if isinstance(p, dict) else None
                if not v or not v.get("value"): continue
                stale = v.get("isStale")
                votes.append(f"{u} {'+1' if v['value'] > 0 else '-1'}{' stale' if stale else ''}")
                if not stale:
                    ups += v["value"] > 0; downs += v["value"] < 0
            flag = " READY" if ups >= 2 and downs == 0 else ""
            for ch in r.get("changes", []):
                byc[str(ch)] = f"review {r['id']} {g.get('state', r['state'])}{flag} | votes: {', '.join(votes) or 'none'}"
        for c in pend:
            n = str(c["change"])
            d = unwrap(call("query_changelists", {"action": "get", "changelist_id": n}))
            desc = d.get("desc") or d.get("Description") or "" if isinstance(d, dict) else ""
            desc = " ".join(desc.split())[:120] or c.get("desc", "")
            print(f"{n}|{c.get('client')}|{'shelved' if n in shel else '-'}|{byc.get(n, 'no review')}|{desc}")
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
