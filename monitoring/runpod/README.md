# RunPod Machine Control (Windows)

This directory contains PowerShell scripts and a local Streamlit dashboard for managing RunPod Pods, vLLM deployments, and related local harnesses.

## Prerequisites

- A RunPod API key in `RUNPOD_API_KEY`.
- PowerShell 5.1+ or PowerShell 7+.
- Python 3.11+ for the dashboard.

```powershell
$env:RUNPOD_API_KEY = "<your-runpod-api-key>"
```

The scripts use `https://rest.runpod.io/v1` by default. Pass `-ApiBaseUrl` when using another endpoint.

## PowerShell Scripts

### List Pods

```powershell
.\powershell\monitor-runpods.ps1
.\powershell\monitor-runpods.ps1 -State running
.\powershell\monitor-runpods.ps1 -Raw
```

### Create a Pod

Prepare a payload in `powershell/create-pod-template.example.json`, then run:

```powershell
.\powershell\create-runpod.ps1 -TemplatePath .\powershell\create-pod-template.example.json
```

The payload is sent as-is, so keep it aligned with the RunPod API schema.

### Stop or Delete a Pod

```powershell
.\powershell\delete-runpod.ps1 -PodIdentifier 123abc
.\powershell\delete-runpod.ps1 -PodIdentifier "my-a40-vllm"
.\powershell\delete-runpod.ps1 -PodIdentifier 123abc -Action stop
```

Stopping releases the GPU but keeps the Volume Disk, which RunPod continues to bill. Delete the Pod when its storage is no longer needed.

## Streamlit Dashboard

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r monitoring/runpod/dashboard/requirements.txt
$env:RUNPOD_API_KEY = "<your-runpod-api-key>"
streamlit run monitoring/runpod/dashboard/app.py
```

Open `http://localhost:8501`.

On Windows, `scripts\start-runpod-dashboard.bat` creates `.venv`, installs missing dependencies, and launches the dashboard. Use `scripts\setup-runpod-dashboard.bat` when you only want to prepare the environment.

The dashboard provides:

- current RunPod credit balance, hourly spending, estimated runway, monthly Pod costs, and auto-pay status;
- live Pod inventory, GPU allocation, cost, endpoint, and vLLM health;
- Pod creation, stopping, deletion, and persisted anti-overspend policies;
- vLLM text and ASR deployment templates;
- local OpenClaw and Qwen Code process, model, and vLLM connection status.

RunPod reads are preloaded in parallel and shared across views. Pod inventory is cached for 15 seconds, account data for 60 seconds, billing data for five minutes, GPU availability for 60 seconds, vLLM health for 12 seconds, and the local process inventory for 15 seconds. Refresh, create, stop, and delete actions invalidate the affected cache entries immediately. API keys are excluded from cache keys; only their SHA-256 fingerprints distinguish cache entries.

The dashboard UI is currently in French. Names such as `Options vLLM`, `Mettre en pause`, and `Detruire` refer to its visible controls.

## Harness Monitoring

The `Harnesses` view refreshes OpenClaw and Qwen Code regularly. Each card displays the active connection, model, endpoint, vLLM health, process uptime, memory use, and latency. Start, stop, and restart controls only target the detected local harness processes.

`Options vLLM` separates saved RunPod instances from manual configuration. Selecting a different instance creates a pending selection; it does not change the active connection until it is applied. Before restarting a running harness, the dashboard checks the selected `/models` endpoint with its API key and verifies that the selected model is available.

For OpenClaw, applying an instance updates the explicit `vllm` provider and primary model in `openclaw.json`. The selected per-Pod secret stays in its dedicated Windows environment variable; the dashboard synchronizes the active value to `VLLM_API_KEY` and OpenClaw's `vllm:default` auth profile. Gateway processes launched by the dashboard receive the resolved environment variables and `NODE_USE_SYSTEM_CA=1` so Node.js uses the Windows certificate store without disabling TLS verification.

Qwen Code receives `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` in its process environment. It supports one vLLM connection per CLI process.

OpenClaw device-pairing requests are loaded only when `Verifier les demandes` is used, keeping the dashboard responsive.

## Multi-Agent vLLM Pool

Every text vLLM Pod created through `Deploy vLLM` is registered as a selectable local instance. Its endpoint, model, and the name of its dedicated Windows API-key variable are stored outside Git.

The Harnesses view compares the catalog with the live RunPod inventory. Deleted Pods are hidden from selectors and can be removed from the local catalog in one click.

For OpenClaw, select one primary instance and zero or more additional instances in `Options vLLM`:

- the primary instance becomes the default `vllm` model;
- each additional instance creates a dedicated OpenClaw provider and agent;
- every selected endpoint is validated before application;
- the Gateway is restarted when it is already running.

The catalog is stored in `monitoring/runpod/data/vllm-connections.json`, which is ignored by Git. It contains only endpoint and model metadata plus environment variable names, never API-key values.

## Model and Harness Configuration

The `Configuration` view centralizes OpenClaw and Qwen Code settings:

- primary and fallback models, generation limits, and context windows;
- thinking/reasoning, temperature, top-p, and compaction settings;
- concurrency, tool profiles, approvals, and sub-agent limits;
- detected plugins, skills, extensions, and MCP servers;
- OpenClaw schema-driven settings and Qwen Code local settings.

Boolean settings use toggles, bounded numeric values use suitable inputs, and enumerations use selection controls. OpenClaw changes go through `openclaw config patch --stdin` followed by `openclaw config validate`. Qwen Code settings are saved atomically to `%USERPROFILE%\.qwen\settings.json` with a timestamped backup.

Paths containing keys, tokens, passwords, secrets, or credentials are excluded from the advanced configuration explorer.

## Automatic Overspend Protection

In `Machines`, the `Protection contre la surfacturation` section can schedule a stop delay, a deletion delay, or both for each Pod. If both are enabled, the deletion delay must be later than the stop delay.

Policies are stored outside Git in `monitoring/runpod/data/pod-lifecycle.json` and contain no API keys. The dashboard evaluates policies every 30 seconds while it is running. After a dashboard restart, absolute due dates are restored and overdue actions run on the next check. Errors are stored with the policy and retried on a subsequent check.

Stopping a Pod retains the Volume Disk and may continue to incur storage charges. Deletion is permanent for all data outside a Network Volume. Policies cannot execute while both the PC and dashboard are stopped, but they catch up on the next dashboard launch.

## Deploy vLLM

In `Deploy vLLM`, choose a template or `Configuration manuelle`. Templates prefill the Pod name, service type, cloud, preferred GPUs, model, storage, and vLLM startup options. You can still adjust all fields before creation.

For text generation, provide:

- a Hugging Face model ID such as `Qwen/Qwen3-8B`;
- a cloud type: `SECURE` or `COMMUNITY`;
- one or more GPUs in preference order;
- a Hugging Face token only when the model is private or gated;
- a Network Volume ID when model-cache persistence is needed.

The [qwen3.8-27b-fp8-a40.example.json](./templates/qwen3.8-27b-fp8-a40.example.json) template provides a conservative 16K context on one A40. The [qwen3.8-27b-fp8-a40-64k.example.json](./templates/qwen3.8-27b-fp8-a40-64k.example.json) and [qwen3.8-27b-fp8-a40-128k.example.json](./templates/qwen3.8-27b-fp8-a40-128k.example.json) templates raise that limit to 64K and 128K for long-horizon agent work. The 128K template uses `--max-model-len 128000` and `--gpu-memory-utilization 0.90`: a single A40 could not initialize the KV cache at the binary 131,072-token limit with the former 0.88 setting. All three use FP8 to fit the model in the A40's 48 GB of memory; validate the selected template from vLLM startup logs before relying on it for sustained workloads.

The dashboard uses the official `vllm/vllm-openai` image, exposes `8000/http`, and starts an OpenAI-compatible API. vLLM downloads models into `/workspace/huggingface` at first start.

For ASR, the default model is `openai/whisper-large-v3-turbo` and the transcription endpoint is:

```text
POST https://<pod-id>-8000.proxy.runpod.net/v1/audio/transcriptions
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<pod-id>-8000.proxy.runpod.net/v1",
    api_key="<vllm-api-key>",
)

with open("message.wav", "rb") as audio_file:
    result = client.audio.transcriptions.create(
        model="openai/whisper-large-v3-turbo",
        file=audio_file,
        language="fr",
    )

print(result.text)
```

The matching raw template is [vllm-asr-whisper.example.json](./templates/vllm-asr-whisper.example.json).

If RunPod returns `There are no instances currently available`, capacity is temporarily unavailable for the chosen cloud and GPUs. Try another cloud, add GPU alternatives, or retry later.

After a text deployment, the dashboard provides copyable and downloadable credentials:

```text
VLLM_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1
VLLM_API_KEY=<generated-vllm-key>
VLLM_MODEL=<hugging-face-model>
```

The vLLM health badge is green when `/v1/models` succeeds, orange while the Pod or model is starting, and red when the endpoint is unavailable or rejects authentication. Existing Pods without a locally available vLLM key are shown as unverifiable. Use `Retester /v1/models` to check readiness manually.

## Shared Parameters

All PowerShell scripts accept:

- `-ApiBaseUrl`: API base URL, default `https://rest.runpod.io/v1`;
- `-ApiKey`: API key, otherwise read from `RUNPOD_API_KEY`;
- `-TimeoutSec`: HTTP timeout in seconds, default `30`.

## Structure

```text
monitoring/runpod/
  README.md
  dashboard/
    app.py
    requirements.txt
  powershell/
    monitor-runpods.ps1
    create-runpod.ps1
    delete-runpod.ps1
    create-pod-template.example.json
  templates/
    a40-vllm.example.json
    qwen3.8-27b-fp8-a40.example.json
    qwen3.8-27b-fp8-a40-64k.example.json
    qwen3.8-27b-fp8-a40-128k.example.json
    vllm-asr-whisper.example.json
```

See the official [RunPod Pod API](https://docs.runpod.io/api-reference/pods/POST/pods) and [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/) documentation for API details.
