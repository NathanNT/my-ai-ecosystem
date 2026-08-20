# Qwen Code Harness

Qwen Code is used here as a terminal coding agent connected to the OpenAI-compatible vLLM server running on the rented RunPod GPU.

Qwen Code and OpenCLAW are separate harnesses. They can use the same vLLM endpoint, but Qwen Code does not require the OpenCLAW Gateway to be running.

## Infrastructure Shape

```text
Windows workstation
  ├─ Qwen Code CLI
  ├─ OpenCLAW Gateway (optional, separate process)
  └─ Runpod Control Center (optional)
          │
          └── HTTPS proxy: https://<pod-id>-8000.proxy.runpod.net/v1
                    │
                    └── vLLM on the RunPod GPU
```

The current text model is exposed through the vLLM OpenAI-compatible API:

```text
VLLM_MODEL=qwen36-27b
VLLM_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1
VLLM_API_KEY=<vllm-api-key>
```

## Prerequisites

- Windows PowerShell.
- Node.js 22 or newer for the npm installation path. The current machine already has Node.js 24.
- A running vLLM RunPod pod with an OpenAI-compatible `/v1` endpoint.
- The vLLM credentials displayed by the Runpod Control Center after deployment.

## Install Qwen Code

Recommended Windows installer:

```powershell
irm https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.ps1 | iex
```

Restart PowerShell after the installation, then verify:

```powershell
qwen --version
```

Alternative npm installation:

```powershell
npm install -g @qwen-code/qwen-code@latest
```

## Configure the RunPod Endpoint

For a session-only configuration, set the credentials in PowerShell:

```powershell
$env:VLLM_BASE_URL = "https://<pod-id>-8000.proxy.runpod.net/v1"
$env:VLLM_API_KEY = "<vllm-api-key>"
$env:VLLM_MODEL = "qwen36-27b"
```

The API key can also be stored as a Windows user environment variable through the Runpod Control Center. The base URL and model are intentionally session variables because the proxy hostname changes when the pod is redeployed.

Check that the endpoint is reachable before starting Qwen Code:

```powershell
$headers = @{ Authorization = "Bearer $env:VLLM_API_KEY" }
Invoke-RestMethod -Uri "$env:VLLM_BASE_URL/models" -Headers $headers
```

## Launch Qwen Code

Start Qwen Code from the repository or project directory:

```powershell
qwen --auth-type openai `
  --model $env:VLLM_MODEL `
  --openai-api-key $env:VLLM_API_KEY `
  --openai-base-url $env:VLLM_BASE_URL
```

The same configuration can be used headlessly for scripts or CI tasks:

```powershell
qwen -p "Review the repository and summarize the highest-risk issues." `
  --auth-type openai `
  --model $env:VLLM_MODEL `
  --openai-api-key $env:VLLM_API_KEY `
  --openai-base-url $env:VLLM_BASE_URL
```

Qwen Code uses the OpenAI-compatible provider path for vLLM. The credentials are passed through environment variables or CLI arguments and should not be committed to this repository.

## Persistent Model Picker Configuration

For a reusable `/model` entry, copy [settings.example.json](./settings.example.json) to the Qwen Code user configuration directory:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.qwen" | Out-Null
Copy-Item .\harnesses\qwen-code\settings.example.json "$env:USERPROFILE\.qwen\settings.json"
```

Replace the placeholder `baseUrl` with the current RunPod proxy URL. Keep the API key out of `settings.json`; the example references `VLLM_API_KEY` through `envKey`.

After changing the settings, launch:

```powershell
qwen
```

Use `/model` inside Qwen Code to select `qwen36-27b`.

## Pod Redeployment Workflow

RunPod proxy hostnames are pod-specific. When a pod is terminated and recreated:

1. Deploy or start the new vLLM pod from the Runpod Control Center.
2. Download or copy the new `VLLM_BASE_URL` and `VLLM_API_KEY` values.
3. Set the variables in the new PowerShell session.
4. Update `settings.json` if you use the persistent model picker.
5. Run the `/models` check, then start Qwen Code.

Do not reuse an old proxy hostname after redeployment.

## Relationship With OpenCLAW

OpenCLAW remains the messaging and agent harness using the same vLLM infrastructure. Qwen Code is a separate interactive coding harness:

```text
OpenCLAW  -> Telegram / Gateway -> vLLM RunPod endpoint
Qwen Code -> Windows terminal  -> vLLM RunPod endpoint
```

Starting Qwen Code does not start OpenCLAW. Starting OpenCLAW does not configure Qwen Code.

## Troubleshooting

### `qwen` is not recognized

Restart PowerShell after installation, then check the npm global binary path:

```powershell
qwen --version
npm prefix -g
```

### HTTP 401 from vLLM

The vLLM API key is missing or does not match the key used when the pod was deployed. Retrieve the credentials again from the Runpod Control Center and set `VLLM_API_KEY` in a new session.

### HTTP 404 or connection failure

Check that `VLLM_BASE_URL` ends with `/v1`, that the pod is running, and that port `8000/http` is exposed. Test `/models` before launching Qwen Code.

### Tool calls or agent actions fail

The model must support the tool-calling behavior expected by Qwen Code, and the vLLM server must expose the model through an OpenAI-compatible chat endpoint. Start with a simple repository summary before enabling more complex workflows.

## References

- [Qwen Code repository and installation](https://github.com/QwenLM/qwen-code)
- [Qwen Code model providers](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/model-providers.md)
- [RunPod Pod API](https://docs.runpod.io/api-reference/pods/POST/pods)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
