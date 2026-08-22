# OpenCLAW Harness

OpenCLAW is treated as an agent harness in this repository. It owns the agent runtime, local gateway, sessions, messaging channels, tool profile, model provider wiring, and workspace used by the OpenCLAW agent.

The live machine currently uses OpenCLAW with a vLLM endpoint running on a rented Runpod A40 instance.

## Current Local Setup

Observed local installation:

```text
OpenCLAW: 2026.7.1-2
OS: Windows
Node: 24.18.1
Gateway mode: local
Gateway bind: loopback
Gateway port: 18789
Provider: vllm
Model: vllm/qwen36-27b
API compatibility: OpenAI-style /v1 completions
Context window: 131072 tokens
Max output tokens: 2048
Messaging channel: Telegram
```

The real OpenCLAW state lives outside this repository:

```text
%USERPROFILE%\.openclaw\
```

Important local paths:

```text
%USERPROFILE%\.openclaw\openclaw.json
%USERPROFILE%\.openclaw\workspace\
%USERPROFILE%\.openclaw\agents\main\sessions\
%USERPROFILE%\.openclaw\state\
%USERPROFILE%\.openclaw\credentials\
%USERPROFILE%\.openclaw\devices\
```

## Runtime Shape

OpenCLAW is configured with a single vLLM provider. The vLLM server is exposed through Runpod's HTTPS proxy and uses an OpenAI-compatible `/v1` base URL.

The active model is:

```text
vllm/qwen36-27b
```

OpenCLAW references the API key by environment variable name:

```text
VLLM_API_KEY
```

The current setup also uses a local OpenCLAW gateway token and a Telegram bot token. Those values must stay outside Git.

## Required Environment Variables

Use local environment variables or a private `.env` file, never committed:

```powershell
$env:VLLM_API_KEY = "<secret>"
$env:VLLM_BASE_URL = "https://example-8000.proxy.runpod.net/v1"
$env:TELEGRAM_BOT_TOKEN = "<secret>"
$env:OPENCLAW_GATEWAY_TOKEN = "<secret>"
```

For repository documentation, only keep placeholders in `.env.example`.

## Config Example

Use [config.example.json](./config.example.json) as a sanitized reference. It mirrors the important structure of the local setup without real tokens, Telegram identifiers, or Runpod hostnames.

Do not commit the real files below:

```text
openclaw.json
openclaw.json.bak*
openclaw.json.last-good
%USERPROFILE%\.openclaw\credentials\
%USERPROFILE%\.openclaw\devices\
%USERPROFILE%\.openclaw\state\
```

## Operational Notes

The current status check showed the gateway was not running:

```text
Gateway: ws://127.0.0.1:18789
State: unreachable / ECONNREFUSED
Scheduled Task: not installed
```

This means OpenCLAW appears to be launched manually rather than installed as a persistent Windows service or Scheduled Task.

Useful diagnostic commands:

```powershell
openclaw --version
openclaw status
openclaw status --deep
openclaw gateway probe
```

### Launch OpenCLAW

For the current local setup, run the Gateway in the foreground:

```powershell
openclaw gateway run --force
```

This uses the configured local Gateway port (`18789` in the current setup). Keep this terminal open while OpenCLAW is running.

If the Gateway is later installed as a Windows service, use:

```powershell
openclaw gateway start
```

Check the result with:

```powershell
openclaw gateway status
```

### Open the Web Control UI

Open the authenticated local Control UI with:

```powershell
openclaw dashboard
```

If the Gateway is not running yet and you want OpenCLAW to start or install it when needed, use:

```powershell
openclaw dashboard --yes
```

The local dashboard also exposes an `Ouvrir l'interface web` button in the OpenCLAW Harness card. It opens the configured Gateway URL directly; `openclaw dashboard` is preferable when the browser still needs Gateway authentication.

Only start, stop, restart, or install services intentionally. For a future restore workflow, prefer documenting the exact command sequence before automating it.

## Add an MCP Server

MCP servers are connected to the OpenCLAW Gateway, not to vLLM or the RunPod Pod. Use the Control UI at `http://127.0.0.1:18789/settings/mcp` for an inventory and editor, or use the commands below for a deterministic setup without asking an agent to modify its own configuration.

### Connect the IDA MCP Server

First confirm that the Windows machine can reach the server:

```powershell
Test-NetConnection 192.168.196.128 -Port 8745
```

For an IDA MCP server exposing Streamable HTTP at `/mcp`, add it with:

```powershell
openclaw mcp add ida --url "http://192.168.196.128:8745/mcp" --transport streamable-http --connect-timeout 10 --timeout 30
openclaw mcp doctor ida --probe
```

If the IDA server instead exposes Server-Sent Events, use its exact SSE path and transport. Do not configure both variants under the same name:

```powershell
openclaw mcp add ida --url "http://192.168.196.128:8745/sse" --transport sse --connect-timeout 10 --timeout 30
openclaw mcp doctor ida --probe
```

`doctor --probe` is the important step: it opens a real MCP connection and lists the server capabilities. If it fails, the issue is the address, path, transport, firewall, or the IDA MCP process itself, rather than the model.

Inspect or remove the saved definition with:

```powershell
openclaw mcp show ida --json
openclaw mcp status --verbose
openclaw mcp unset ida
```

When the Gateway runs as a managed Windows service, reload the MCP runtime with a graceful restart:

```powershell
openclaw gateway restart --safe
```

When it runs in a foreground terminal, stop that terminal with `Ctrl+C`, then start it again:

```powershell
openclaw gateway run --force
```

An MCP definition can be saved while disabled or excluded by the agent tool policy. Confirm the `bundle-mcp` plugin, or the specific `ida__*` tools, are allowed for the agent that should use it.

## Runpod And vLLM Notes

When the Runpod pod changes, the proxy hostname usually changes too. Update every OpenCLAW location that stores the vLLM base URL.

In the inspected machine, the main config and the vLLM plugin catalog did not point to the same Runpod hostname. That may be harmless if only the main config is used, but it is worth checking when the model endpoint appears stale.

Keep these fields aligned:

```text
models.providers.vllm.baseUrl
agents/main/agent/plugins/vllm/catalog.json -> providers.vllm.baseUrl
```

## Security Notes

The local OpenCLAW config and backups may contain:

- gateway tokens;
- Telegram bot tokens;
- paired device tokens;
- Runpod/vLLM endpoint identifiers;
- session history;
- local memory;
- SQLite runtime state.

Treat `%USERPROFILE%\.openclaw` as private machine state. This repository should only contain sanitized examples and restore notes.

## Monitoring Direction

For this Windows setup, Runpod lifecycle scripts are the preferred local control plane. Keep monitoring and machine control notes under:

```text
monitoring/runpod/
```

The first monitoring target should be client-side traces for OpenAI-compatible calls going from Windows/OpenCLAW to the Runpod vLLM endpoint.

### Runpod Control Center

From the repository root, start the local dashboard that groups pods, templates, and vLLM/ASR deployments:

```powershell
$env:RUNPOD_API_KEY = "<ta_runpod_api_key>"
streamlit run monitoring/runpod/dashboard/app.py
```

Then open `http://localhost:8501`. The key can also be entered in the sidebar and saved to the Windows user environment with `Enregistrer la clé dans Windows`.
