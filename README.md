# p4mcp-cli

A small command-line front end for Perforce's [P4 MCP Server](https://github.com/perforce/p4mcp-server)
(`p4mcp-server`). It starts the server over stdio, does the MCP handshake, runs one request, prints
the result and exits. Useful when the agent can only reach the machine with Perforce access through a
shell (remote exec, serial link, SSH) rather than as an MCP client.

Covers everything the server exposes: files, changelists, shelves, workspaces, jobs, streams and
P4 Code Review (Swarm) reviews. The Swarm URL is discovered from the server property `P4.Swarm.URL`
and authenticated with your p4 ticket.

## Install

```powershell
python -m venv C:\tools\p4mcp
C:\tools\p4mcp\Scripts\python.exe -m pip install p4mcp-server
copy p4mcp-call.py, p4mcp_patches.py, p4mcp.example.json C:\tools\p4mcp\
cd C:\tools\p4mcp; copy p4mcp.example.json p4mcp.json   # then edit
p4 login -a
```

Python 3.11+. No other dependencies. Placed in the venv folder it finds `Scripts\p4mcp-server.exe`
(or `bin/p4mcp-server`); otherwise it uses `p4mcp-server` from PATH, or set `"server"` in the config.

## Config (`p4mcp.json`, next to the script)

| Key | Meaning |
| --- | --- |
| `P4PORT`, `P4USER`, `P4CHARSET` | Passed to the server as env vars |
| `defaultClient`, `clients` | Workspace used when `--client` is not given; `clients` maps short aliases to workspace names |
| `toolsets` | `--toolsets` for the server (files, changelists, shelves, workspaces, jobs, reviews, streams, p4dam) |
| `readonly` | `true` adds `--readonly` (disables every modify_* tool) |
| `caBundle`, `sslVerify` | TLS for the Swarm API (`--ca-bundle`, `--ssl-no-verify`) |
| `maxResults` | `--max-results` |
| `logDir` | Server log folder (default `logs` next to the script) |
| `env` | Extra env vars for the server (e.g. `P4MCP_TLS_CA_MODE`) |

`P4CONFIG` is removed from the server's environment so the workspace always comes from the config or `--client`.

## Usage

```powershell
python p4mcp-call.py --list                               # tools
python p4mcp-call.py --schema query_reviews               # input schema
python p4mcp-call.py --call query_server --args '{"action":"server_info"}'
python p4mcp-call.py --call query_reviews --args-file my-reviews.json
python p4mcp-call.py --client other --call query_changelists --args-file pending.json
```

`my-reviews.json`:

```json
{"action":"list","keywords":"your.user","keywords_fields":["author"],
 "state":["needsReview","needsRevision","approved:isPending"],
 "fields":["id","state","changes","description"],"max_results":10}
```

Prefer `--args-file` when calling through PowerShell or another shell layer: inline JSON quoting is easily mangled.

Exit codes: 0 ok, 1 the tool returned an error, 2 usage or server failure. `--raw` prints the JSON-RPC result.

## Patches to p4mcp-server

`p4mcp_patches.py` holds small fixes to the installed p4mcp-server package. `p4mcp-call.py` re-applies
any missing ones on every start, so they survive `pip install --upgrade` (the first CLI call after an
upgrade also fixes the server that Cline runs from the same venv). Check by hand:
`python p4mcp_patches.py --check`.

- Case-insensitive changelist owner check: on a case-insensitive server the CL owner (`gbhatnagar`)
  and `p4 info` userName (`GBhatnagar`) can differ in case, so `modify_changelists`
  update/delete/move_files failed with "not owned by current user".

## Using the server from Cline

Cline runs `p4mcp-server` directly; the CLI script above is not needed for this.

Where Cline keeps its MCP settings:

- Easiest: in Cline, click **MCP Servers** (stack icon) → **Installed** → **Configure MCP Servers**. This opens `cline_mcp_settings.json`.
- VS Code extension: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
- Cline CLI / standalone: `%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`

Steps:

1. Do the **Install** step above (venv + `pip install p4mcp-server`).
2. Log in once so the server has a ticket: `p4 -p ssl:perforce.example.com:1666 -u your.user login -a`.
3. Add a `p4` entry under `mcpServers` (merge it with any servers already there):

```json
{
  "mcpServers": {
    "p4": {
      "command": "C:\\tools\\p4mcp\\Scripts\\p4mcp-server.exe",
      "args": [
        "--toolsets", "files", "changelists", "shelves", "workspaces", "jobs", "reviews", "streams",
        "--log-dir", "C:\\tools\\p4mcp\\logs"
      ],
      "env": {
        "P4PORT": "ssl:perforce.example.com:1666",
        "P4USER": "your.user",
        "P4CLIENT": "your.user_main_ws"
      },
      "disabled": false,
      "timeout": 120
    }
  }
}
```

4. Save. Cline restarts the server; `p4` should show a green dot in **MCP Servers**.
5. Test: ask Cline "list my pending changelists".

Notes:

- Backslashes in Windows paths must be doubled in JSON.
- Add `"--readonly"` to `args` to disable every modify_* tool.
- `P4CLIENT` is the workspace Cline works in. Use one entry per workspace if you need several (e.g. `p4-main`, `p4-other`).
- Server not starting: check the log in `--log-dir`, and run `p4 login -s` to see if the ticket expired.

## License

MIT
