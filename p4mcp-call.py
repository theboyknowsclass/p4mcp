#!/usr/bin/env python3
"""p4mcp-call.py - command-line front end for Perforce's p4mcp-server (stdio MCP).

Lets an agent or a person call P4 / Swarm MCP tools without an MCP client.
Starts p4mcp-server, does the MCP handshake, runs one request, prints the result, exits.

Usage:
  python p4mcp-call.py --list                         tool names + one-line descriptions
  python p4mcp-call.py --schema <tool>                full input schema of one tool
  python p4mcp-call.py --call <tool> --args-file a.json
  python p4mcp-call.py --call <tool> --args '{"action":"list"}'
  options: --client <name or alias>   override P4CLIENT (aliases in p4mcp.json "clients")
           --config <path>            default: p4mcp.json next to this file
           --raw                      print the JSON-RPC result instead of the text content

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


class Session:
    def __init__(self, cmd, env):
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
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"]))
                return msg["result"]

    def open(self):
        self.request("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "p4mcp-call", "version": "1.0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


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
    a = ap.parse_args()

    cfg = load_config(a.config)
    s = Session(build_command(cfg), build_env(cfg, a.client))
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
