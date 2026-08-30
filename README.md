# Foral — the runner

**Charters for software without public APIs.** Foral turns the systems your team already
logs into into typed, governed capabilities for your AI agents — served over
[MCP](https://modelcontextprotocol.io), with no public API required, no browser
puppeteering, and no generated code that rots.

This package is the **runner**: the commodity client that runs on *your* infrastructure. It
loads a contract, serves its read capabilities to your agent as MCP tools, and keeps writes
human-approved. The discovery engine that proposes a contract is **not** in here — the runner
is small and auditable on purpose.

- Website: **https://foral.dev**
- Docs: **https://foral.dev/docs**
- Contact: **founders@foral.dev**

---

## Install

```bash
# base — serve / verify / update (no browser)
pipx install foral            # or: npm i -g @foral/cli

# to also sign in locally (foral login), add the browser extra, once:
pipx install --include-deps 'foral[login]'
playwright install chromium
```

## Quickstart

```bash
# 1) the three things the runner needs — it is fail-closed and never guesses
export FORAL_TENANT=your-org                       # isolates your sessions & contracts
export SESSION_ENCRYPTION_KEY="$(foral keygen)"    # encrypts the saved session — keep it safe
export CONTRATOS_DIR=~/foral/contracts             # where your contract YAML lives

# put the contract you downloaded from the sandbox here, named after the system:
#   ~/foral/contracts/acme.yaml   →   foral serve acme   (lowercase)

# 2) sign in once (opens your system's login in a browser; the session stays on this
#    machine, ~30 days) and serve it as an MCP server
foral login acme
foral serve acme
```

## Point your agent at it

```bash
# Claude Code
claude mcp add foral -- foral serve acme
```

```jsonc
// Cursor — ~/.cursor/mcp.json
{ "mcpServers": { "foral": { "command": "foral", "args": ["serve", "acme"] } } }
```

```toml
# Codex — ~/.codex/config.toml
[mcp_servers.foral]
command = "foral"
args = ["serve", "acme"]
```

Any MCP client works — your own app connects to the same server and calls the system's
capabilities as typed tools.

## Commands

| Command | What it does |
|---|---|
| `foral serve <system>` | Serve the system's contract as an MCP server (stdio). |
| `foral login <system>` | Sign in once; save the session locally, encrypted (~30 days). Needs `foral[login]`. |
| `foral update <system> --from <url>` | Adopt a new contract version — validated before it is applied (fail-closed). |
| `foral verify <system>` | Validate the contract and exit. |
| `foral keygen` | Print a fresh `SESSION_ENCRYPTION_KEY`. |

## How it behaves

- **Reads run with no browser.** Read capabilities call the system's own internal API using
  your saved session and return typed rows — in milliseconds, no Chromium.
- **Writes stay human-approved.** Write capabilities are never served as MCP tools and never
  execute here: the runner returns the declared confirmation phrase instead. Approval happens
  in your app's harness, never silently.
- **The session never leaves your machine.** `foral login` saves an encrypted session locally;
  it is never sent to Foral. Your password is typed into your own system's form.
- **Contracts stay alive.** Every read carries a fingerprint of the system's shape. When the
  system changes, the next read raises an alarm before wrong data can flow — your cue to run
  `foral update`.
- **Fail-closed by default.** An invalid contract does not load; a missing tenant is refused;
  a missing key is refused. Errors are explicit, never a silent, wrong success.

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `FORAL_TENANT` | serve, login | Isolates your sessions and contracts. No default (fail-closed). |
| `SESSION_ENCRYPTION_KEY` | login, serve | Encrypts the saved session. Generate with `foral keygen`. |
| `CONTRATOS_DIR` | serve, update | Directory holding `<system>.yaml`. |
| `SESSION_DATA_DIR` | optional | Where sessions are stored (default `./sessions`). |

## Development

```bash
pip install -e '.[test]'
pytest
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
