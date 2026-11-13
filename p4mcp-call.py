#!/usr/bin/env python3
"""p4mcp-call.py - command-line front end for Perforce's p4mcp-server (stdio MCP).

Lets an agent or a person call P4 / Swarm MCP tools without an MCP client.
Starts p4mcp-server, does the MCP handshake, runs one request, prints the result, exits.

Usage:
  python p4mcp-call.py --list                         tool names + one-line descriptions
  python p4mcp-call.py --schema <tool>                full input schema of one tool
  python p4mcp-call.py --call <tool> --args-file a.json
  python p4mcp-call.py --call <tool> --args '{"action":"list"}'
  python p4mcp-call.py --call submit_shelf --args '{"changelist_id":"12345"}'   (local tool: p4 submit -e)
  options: --yes                      auto-accept the server's confirmation prompts (deletes etc.);
                                      same as "autoApprove": true in p4mcp.json
           --client <name or alias>   override P4CLIENT (aliases in p4mcp.json "clients")
           --config <path>            default: p4mcp.json next to this file
           --raw                      print the JSON-RPC result instead of the text content

Fixes to the installed p4mcp-server (p4mcp_patches.py) are re-applied on every start.
Everything organisation-specific (P4PORT, P4USER, clients, toolsets, TLS) lives in p4mcp.json.
Exit code: 0 ok, 1 tool returned an error, 2 usage / server failure.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = "2025-06-18"


def load_config(path):
    p = Path(path) if path else HERE / "p4mcp.json"
    if not p.exists():
        sys.exit(f"config not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_command(cfg):
    exe = cfg.get("server")
    if not exe:
        venv = HERE / ("Scripts/p4mcp-server.exe" if os.name == "nt" else "bin/p4mcp-server")
        exe = str(venv) if venv.exists() else (shutil.which("p4mcp-server") or "p4mcp-server")
    cmd = [exe]
    if cfg.get("readonly"):
        cmd.append("--readonly")
    if cfg.get("toolsets"):
        cmd += ["--toolsets", *cfg["toolsets"]]
    if cfg.get("caBundle"):
        cmd += ["--ca-bundle", cfg["caBundle"]]
    if cfg.get("sslVerify") is False:
        cmd.append("--ssl-no-verify")
    if cfg.get("maxResults") is not None:
        cmd += ["--max-results", str(cfg["maxResults"])]
    cmd += ["--log-dir", cfg.get("logDir") or str(HERE / "logs")]
    return cmd


def build_env(cfg, client):
    env = dict(os.environ)
    for key in ("P4PORT", "P4USER", "P4CHARSET"):
        if cfg.get(key):
            env[key] = cfg[key]
    aliases = cfg.get("clients", {})
    chosen = client or cfg.get("defaultClient")
    if chosen:
        env["P4CLIENT"] = aliases.get(chosen, chosen)
    env.pop("P4CONFIG", None)  # the client comes from config/--client, not the cwd
    for key, value in (cfg.get("env") or {}).items():
        env[key] = str(value)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def auto_answer(schema):
    """Fill an elicitation form with 'yes' answers."""
    content = {}
    for name, prop in (schema or {}).get("properties", {}).items():
        options = prop.get("enum") or [o.get("const") for o in prop.get("oneOf", []) if "const" in o]
        if options:
            yes = [o for o in options if str(o).lower() in ("yes", "y", "approve", "approved", "confirm", "accept", "true", "ok")]
            content[name] = yes[0] if yes else options[0]
        elif prop.get("type") == "boolean":
            content[name] = True
        elif prop.get("type") in ("integer", "number"):
            content[name] = prop.get("default", 0)
        else:
            content[name] = prop.get("default", "yes")
    return content


class Session:
    def __init__(self, cmd, env, auto_approve=False):
        self.auto_approve = auto_approve
        self.proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(HERE))
        self.stderr = []
        threading.Thread(target=self._drain, daemon=True).start()
        self.next_id = 1

    def _drain(self):
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace"))

    def send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method, params=None):
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                tail = "".join(self.stderr[-20:])
                raise RuntimeError(f"p4mcp-server exited (code {self.proc.poll()})\n{tail}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in msg and "id" in msg:
                self._answer_server(msg)
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"]))
                return msg["result"]

    def _answer_server(self, msg):
        method, mid = msg["method"], msg["id"]
        if method == "elicitation/create":
            params = msg.get("params") or {}
            prompt = (params.get("message") or "").strip()
            if self.auto_approve:
                print(f"[auto-approved] {prompt}", file=sys.stderr)
                result = {"action": "accept", "content": auto_answer(params.get("requestedSchema"))}
            else:
                print(f"[declined - pass --yes to approve] {prompt}", file=sys.stderr)
                result = {"action": "decline"}
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "roots/list":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {"roots": []}})
        else:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unsupported: {method}"}})

    def open(self):
        self.request("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {"elicitation": {}},
            "clientInfo": {"name": "p4mcp-call", "version": "1.0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


LOCAL_TOOLS = {
    "submit_shelf": "Submit a shelved changelist as-is (p4 submit -e <changelist_id>). Needs --yes / autoApprove. (local)",
}


def submit_shelf(env, args, approved):
    cl = str(args.get("changelist_id") or "").strip()
    if not cl.isdigit():
        return 2, "ERROR: submit_shelf needs changelist_id"
    if not approved:
        return 1, f"declined: submitting shelf {cl} needs --yes (or autoApprove in p4mcp.json)"
    from P4 import P4, P4Exception  # P4Python, installed with p4mcp-server
    p4 = P4()
    p4.port, p4.user = env.get("P4PORT", p4.port), env.get("P4USER", p4.user)
    if env.get("P4CLIENT"):
        p4.client = env["P4CLIENT"]
    p4.exception_level = 1
    try:
        with p4.connect():
            out = p4.run("submit", "-e", cl)
        return 0, json.dumps({"status": "success", "action": "submit_shelf", "result": out}, default=str)
    except P4Exception as e:
        return 1, json.dumps({"status": "error", "action": "submit_shelf", "errors": p4.errors or [str(e)]})


def main():
    ap = argparse.ArgumentParser(description="Call p4mcp-server tools from the command line")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--schema")
    g.add_argument("--call")
    ap.add_argument("--args")
    ap.add_argument("--args-file")
    ap.add_argument("--client")
    ap.add_argument("--config")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--yes", action="store_true", help="auto-accept confirmation prompts")
    a = ap.parse_args()

    cfg = load_config(a.config)
    approved = a.yes or bool(cfg.get("autoApprove"))
    env = build_env(cfg, a.client)
    if a.call in LOCAL_TOOLS:
        args = json.loads(Path(a.args_file).read_text(encoding="utf-8-sig")) if a.args_file else json.loads(a.args or "{}")
        code, text = submit_shelf(env, args, approved)
        print(text)
        return code
    try:
        from p4mcp_patches import ensure_patched  # re-applies fixes lost on a p4mcp-server upgrade
        ensure_patched()
    except Exception as e:  # never block a call on the patch step
        print(f"[p4mcp-patch] skipped: {e}", file=sys.stderr)
    s = Session(build_command(cfg), env, approved)
    try:
        s.open()
        if a.list or a.schema:
            tools = s.request("tools/list")["tools"]
            if a.schema:
                tool = next((t for t in tools if t["name"] == a.schema), None)
                if not tool:
                    sys.exit(f"no tool {a.schema}")
                print(json.dumps(tool, indent=2))
            else:
                for t in tools:
                    desc = (t.get("description") or "").strip().splitlines()
                    print(f"{t['name']}: {desc[0] if desc else ''}")
                for name, desc in LOCAL_TOOLS.items():
                    print(f"{name}: {desc}")
            return 0
        if a.args_file:
            args = json.loads(Path(a.args_file).read_text(encoding="utf-8-sig"))
        else:
            args = json.loads(a.args) if a.args else {}
        result = s.request("tools/call", {"name": a.call, "arguments": args})
        if a.raw:
            print(json.dumps(result, indent=2))
        else:
            for item in result.get("content", []):
                print(item.get("text", json.dumps(item)))
        return 1 if result.get("isError") else 0
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
