"""Local Streamlit dashboard for managing Runpod pods and vLLM deployments."""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st


RUNPOD_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = RUNPOD_DIR / "templates"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEFAULT_API_BASE_URL = "https://rest.runpod.io/v1"
VLLM_PORT = 8000
ACTIVE_POD_STATES = {"RUNNING", "READY", "STARTING", "CREATING"}


def api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def request_runpod(
    method: str,
    base_url: str,
    api_key: str,
    path: str,
    **kwargs: Any,
) -> Any:
    response = requests.request(
        method,
        api_url(base_url, path),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=30,
        **kwargs,
    )
    if not response.ok:
        detail = response.text.strip() or response.headers.get("WWW-Authenticate", "aucun détail retourné")
        if response.status_code == 401:
            detail = "clé API Runpod refusée ou expirée"
        raise RuntimeError(f"Runpod API ({response.status_code}) : {detail[:500]}")
    if not response.content:
        return None
    return response.json()


def request_account_summary(api_key: str) -> dict[str, Any]:
    query = """
    query DashboardAccount {
      myself {
        clientBalance
        currentSpendPerHr
        spendLimit
        underBalance
        minBalance
        isAutoPayEnabled
        stripeAutoPaymentThreshold
        stripeAutoReloadAmount
      }
    }
    """
    response = requests.post(
        "https://api.runpod.io/graphql",
        params={"api_key": api_key},
        json={"query": query},
        timeout=20,
    )
    if response.status_code == 401:
        raise RuntimeError("Runpod API (401) : clé API refusée ou sans accès au compte")
    if not response.ok:
        raise RuntimeError(f"Runpod Account API ({response.status_code}) : {response.text[:500]}")
    body = response.json()
    if body.get("errors"):
        details = "; ".join(str(error.get("message", error)) for error in body["errors"])
        raise RuntimeError(f"Runpod Account API : {details[:500]}")
    account = body.get("data", {}).get("myself")
    if not isinstance(account, dict):
        raise RuntimeError("Réponse de compte Runpod inattendue")
    return account


def request_monthly_pod_billing(base_url: str, api_key: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    response = request_runpod(
        "GET",
        base_url,
        api_key,
        "/billing/pods",
        params={
            "bucketSize": "day",
            "grouping": "podId",
            "startTime": month_start.isoformat().replace("+00:00", "Z"),
            "endTime": now.isoformat().replace("+00:00", "Z"),
        },
    )
    return response_items(response)


def response_items(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("pods", "data", "items"):
            value = response.get(key)
            if isinstance(value, list):
                return value
    return []


def pod_state(pod: dict[str, Any]) -> str:
    return str(
        pod.get("state")
        or pod.get("status")
        or pod.get("currentStatus")
        or pod.get("desiredStatus")
        or "unknown"
    )


def number_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def pod_hourly_cost(pod: dict[str, Any]) -> float:
    return number_value(pod.get("adjustedCostPerHr") or pod.get("costPerHr"))


def pod_gpu_name(pod: dict[str, Any]) -> str:
    gpu = pod.get("gpu")
    if isinstance(gpu, dict):
        return str(gpu.get("displayName") or gpu.get("id") or "GPU")
    return str(pod.get("gpuTypeId") or pod.get("gpuType") or "GPU inconnu")


def pod_gpu_count(pod: dict[str, Any]) -> int:
    gpu = pod.get("gpu")
    value = gpu.get("count") if isinstance(gpu, dict) else pod.get("gpuCount")
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def is_active_pod(pod: dict[str, Any]) -> bool:
    return pod_state(pod).upper() in ACTIVE_POD_STATES


def usd(value: Any, suffix: str = "") -> str:
    return f"${number_value(value):,.2f}{suffix}"


def runway_text(balance: float, hourly_spend: float) -> str:
    if hourly_spend <= 0:
        return "Aucune dépense"
    hours = max(0.0, balance / hourly_spend)
    if hours < 24:
        return f"{hours:.1f} h"
    days = hours / 24
    return f"{days:.1f} j" if days < 30 else f"{days / 30:.1f} mois"


def billing_total(records: list[dict[str, Any]]) -> float:
    return sum(number_value(record.get("amount")) for record in records)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_config_value(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("<"):
        return ""
    expanded = os.path.expandvars(value)
    if expanded != value:
        return expanded
    environment_value = os.getenv(value)
    if environment_value:
        return environment_value
    if value.startswith("${") or (value.startswith("%") and value.endswith("%")):
        return ""
    if value.isupper() and "_" in value:
        return ""
    return value


def scan_harness_processes() -> dict[str, list[dict[str, Any]]]:
    processes: dict[str, list[dict[str, Any]]] = {"openclaw": [], "qwen-code": []}
    if os.name != "nt":
        return processes
    command = """
    $items = Get-CimInstance Win32_Process | ForEach-Object {
      $created = 0
      if ($_.CreationDate) {
        $created = ([DateTimeOffset]$_.CreationDate).ToUnixTimeSeconds()
      }
      [PSCustomObject]@{
        pid = $_.ProcessId
        name = $_.Name
        commandLine = $_.CommandLine
        createTime = $created
        memoryBytes = $_.WorkingSetSize
      }
    }
    @($items) | ConvertTo-Json -Compress
    """
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=creation_flags,
        )
        values = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return processes
    if isinstance(values, dict):
        values = [values]
    for info in values if isinstance(values, list) else []:
        if not isinstance(info, dict):
            continue
        name = str(info.get("name") or "")
        executable = name.lower()
        command_line = str(info.get("commandLine") or "")
        command_text = command_line.lower()
        harness_id = ""
        if executable.startswith("openclaw") or (
            executable in {"node", "node.exe"} and "openclaw" in command_text
        ):
            harness_id = "openclaw"
        elif executable.startswith("qwen") or (
            executable in {"node", "node.exe"} and "qwen-code" in command_text
        ):
            harness_id = "qwen-code"
        if not harness_id:
            continue
        processes[harness_id].append(
            {
                "pid": int(info.get("pid") or 0),
                "name": name,
                "cmdline": command_line.split(),
                "create_time": number_value(info.get("createTime")),
                "memory_bytes": int(info.get("memoryBytes") or 0),
            }
        )
    return processes


def process_argument(processes: list[dict[str, Any]], flag: str) -> str:
    for process in processes:
        command = process.get("cmdline") or []
        for index, argument in enumerate(command[:-1]):
            if argument == flag:
                return str(command[index + 1])
    return ""


def harness_process_metrics(processes: list[dict[str, Any]]) -> dict[str, Any]:
    if not processes:
        return {"running": False, "count": 0, "memory_bytes": 0, "started_at": 0.0}
    start_times = [number_value(process.get("create_time")) for process in processes]
    return {
        "running": True,
        "count": len(processes),
        "memory_bytes": sum(int(process.get("memory_bytes") or 0) for process in processes),
        "started_at": min(value for value in start_times if value > 0) if any(start_times) else 0.0,
    }


def openclaw_harness_config(processes: list[dict[str, Any]]) -> dict[str, Any]:
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    config = load_json_object(config_path)
    providers = config.get("models", {}).get("providers", {})
    provider = providers.get("vllm", {}) if isinstance(providers, dict) else {}
    if not provider and isinstance(providers, dict):
        provider = next((value for value in providers.values() if isinstance(value, dict)), {})
    defaults = config.get("agents", {}).get("defaults", {})
    primary_model = defaults.get("model", {}).get("primary", "") if isinstance(defaults, dict) else ""
    gateway = config.get("gateway", {}) if isinstance(config.get("gateway"), dict) else {}
    raw_api_key = provider.get("apiKey", "") if isinstance(provider, dict) else ""
    return {
        "config_path": str(config_path),
        "configured": bool(config),
        "base_url": resolve_config_value(provider.get("baseUrl", "")) or os.getenv("VLLM_BASE_URL", ""),
        "api_key": resolve_config_value(raw_api_key) or os.getenv("VLLM_API_KEY", ""),
        "model": str(primary_model or os.getenv("VLLM_MODEL", "")),
        "gateway_port": int(gateway.get("port") or 18789),
        "processes": processes,
    }


def qwen_harness_config(processes: list[dict[str, Any]]) -> dict[str, Any]:
    config_path = Path.home() / ".qwen" / "settings.json"
    config = load_json_object(config_path)
    model_providers = config.get("modelProviders", {})
    provider: dict[str, Any] = {}
    if isinstance(model_providers, dict):
        for entries in model_providers.values():
            if isinstance(entries, list):
                provider = next((entry for entry in entries if isinstance(entry, dict)), {})
                if provider:
                    break
    env_key = str(provider.get("envKey") or "VLLM_API_KEY")
    return {
        "config_path": str(config_path),
        "configured": bool(config),
        "base_url": process_argument(processes, "--openai-base-url")
        or resolve_config_value(provider.get("baseUrl", ""))
        or os.getenv("VLLM_BASE_URL", ""),
        "api_key": process_argument(processes, "--openai-api-key")
        or os.getenv(env_key, "")
        or os.getenv("VLLM_API_KEY", ""),
        "model": process_argument(processes, "--model")
        or str(provider.get("id") or os.getenv("VLLM_MODEL", "")),
        "processes": processes,
    }


def tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def probe_vllm(base_url: str, api_key: str) -> dict[str, Any]:
    if not base_url:
        return {
            "label": "Non configuré",
            "color": "#64748b",
            "detail": "Aucun endpoint vLLM trouvé.",
            "latency_ms": None,
            "models": [],
        }
    started = datetime.now(timezone.utc)
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5)
        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        if response.ok:
            body = response.json()
            models = [str(item.get("id")) for item in body.get("data", []) if isinstance(item, dict) and item.get("id")]
            return {
                "label": "Connecté",
                "color": "#16a34a",
                "detail": "L'API vLLM répond sur /v1/models.",
                "latency_ms": latency_ms,
                "models": models,
            }
        if response.status_code in {401, 403}:
            return {
                "label": "Erreur auth",
                "color": "#dc2626",
                "detail": f"vLLM répond avec HTTP {response.status_code}.",
                "latency_ms": latency_ms,
                "models": [],
            }
        return {
            "label": "Dégradé",
            "color": "#d97706",
            "detail": f"vLLM répond avec HTTP {response.status_code}.",
            "latency_ms": latency_ms,
            "models": [],
        }
    except (requests.RequestException, ValueError):
        return {
            "label": "Injoignable",
            "color": "#dc2626",
            "detail": "L'endpoint vLLM ne répond pas.",
            "latency_ms": None,
            "models": [],
        }


def duration_text(started_at: float) -> str:
    if started_at <= 0:
        return "—"
    seconds = max(0, int(datetime.now().timestamp() - started_at))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} j {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def memory_text(memory_bytes: int) -> str:
    return f"{memory_bytes / (1024 * 1024):.0f} MB"


def logo_data_uri(filename: str) -> str:
    path = ASSETS_DIR / filename
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{encoded}"


def model_alignment(configured_model: str, served_models: list[str]) -> bool | None:
    if not configured_model or not served_models:
        return None
    configured = configured_model.lower().split("/", 1)[-1]
    return any(
        configured == served.lower()
        or configured == served.lower().split("/", 1)[-1]
        for served in served_models
    )


def pod_identifier(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("id", "podId", "pod_id"):
            if response.get(key):
                return str(response[key])
    return ""


def proxy_url(pod_id: str, port: int = VLLM_PORT) -> str:
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def load_templates() -> dict[str, dict[str, Any]]:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates: dict[str, dict[str, Any]] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                templates[path.name] = value
        except (OSError, json.JSONDecodeError):
            continue
    return templates


def template_text(template: dict[str, Any]) -> str:
    return json.dumps(template, indent=2, ensure_ascii=True)


def request_gpu_catalog(api_key: str, cloud_type: str) -> list[dict[str, Any]]:
    secure_cloud = "true" if cloud_type == "SECURE" else "false"
    query = f"""
    query {{
      gpuTypes {{
        id
        displayName
        memoryInGb
        secureCloud
        communityCloud
        lowestPrice(input: {{ gpuCount: 1, secureCloud: {secure_cloud} }}) {{
          stockStatus
          uninterruptablePrice
          availableGpuCounts
        }}
      }}
    }}
    """
    response = requests.post(
        "https://api.runpod.io/graphql",
        params={"api_key": api_key},
        json={"query": query},
        timeout=20,
    )
    if response.status_code == 401:
        raise RuntimeError("Runpod API (401) : clé API Runpod refusée ou expirée")
    if not response.ok:
        raise RuntimeError(f"Runpod GPU API ({response.status_code}) : {response.text[:500]}")
    body = response.json()
    if body.get("errors"):
        details = "; ".join(str(error.get("message", error)) for error in body["errors"])
        raise RuntimeError(f"Runpod GPU API : {details[:500]}")
    gpu_types = body.get("data", {}).get("gpuTypes", [])
    if not isinstance(gpu_types, list):
        raise RuntimeError("Réponse GPU Runpod inattendue")
    return gpu_types


def gpu_option_label(gpu: dict[str, Any], cloud_type: str) -> str:
    gpu_id = str(gpu.get("id", ""))
    display_name = gpu.get("displayName") or gpu_id
    memory = gpu.get("memoryInGb")
    lowest_price = gpu.get("lowestPrice") or {}
    stock = lowest_price.get("stockStatus", "inconnu")
    price = lowest_price.get("uninterruptablePrice")
    price_text = f" · ${price}/h" if price is not None else ""
    memory_text = f" · {memory} GB" if memory else ""
    return f"{display_name} ({gpu_id}){memory_text} · {cloud_type}: {stock}{price_text}"


def build_vllm_payload(
    name: str,
    model: str,
    service_type: str,
    gpu_types: list[str],
    cloud_type: str,
    vllm_api_key: str,
    hf_token: str,
    network_volume_id: str,
    volume_gb: int,
    container_disk_gb: int,
    image_name: str = "vllm/vllm-openai:latest",
    extra_start_args: list[str] | None = None,
    template_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = {
        "MODEL_ID": model,
        "HF_HOME": "/workspace/huggingface",
        "VLLM_API_KEY": vllm_api_key,
    }
    if hf_token:
        env["HF_TOKEN"] = hf_token
    if service_type == "asr":
        env["VLLM_MAX_AUDIO_CLIP_FILESIZE_MB"] = "25"
    for key, value in (template_env or {}).items():
        if key in {"MODEL_ID", "VLLM_API_KEY", "HF_TOKEN"}:
            continue
        if isinstance(value, str) and not value.startswith("<"):
            env[key] = value

    payload: dict[str, Any] = {
        "name": name,
        "cloudType": cloud_type,
        "gpuCount": 1,
        "gpuTypeIds": gpu_types,
        "gpuTypePriority": "availability",
        "imageName": image_name,
        "containerDiskInGb": container_disk_gb,
        "volumeInGb": volume_gb,
        "volumeMountPath": "/workspace",
        "ports": ["8000/http"],
        "env": env,
        "dockerStartCmd": [
            "--model",
            model,
            "--host",
            "0.0.0.0",
            "--port",
            str(VLLM_PORT),
            "--api-key",
            vllm_api_key,
        ] + list(extra_start_args or []),
    }
    if cloud_type == "COMMUNITY":
        payload["supportPublicIp"] = True
    if network_volume_id.strip():
        payload["networkVolumeId"] = network_volume_id.strip()
    return payload


def template_start_args(template: dict[str, Any] | None) -> list[str]:
    """Keep template-specific vLLM flags while regenerating connection arguments."""
    if not template or not isinstance(template.get("dockerStartCmd"), list):
        return []
    ignored_flags = {"--model", "--host", "--port", "--api-key"}
    args: list[str] = []
    skip_value = False
    for raw_arg in template["dockerStartCmd"]:
        arg = str(raw_arg)
        if skip_value:
            skip_value = False
            continue
        if arg in ignored_flags:
            skip_value = True
            continue
        args.append(arg)
    return args


def template_gpu_ids(template: dict[str, Any] | None) -> list[str]:
    if not template:
        return []
    values = template.get("gpuTypeIds")
    if isinstance(values, list):
        return [str(value) for value in values if value]
    value = template.get("gpuTypeId")
    return [str(value)] if value else []


def template_default_value(template: dict[str, Any] | None, key: str, fallback: Any) -> Any:
    value = template.get(key) if template else None
    return fallback if value is None or (isinstance(value, str) and value.startswith("<")) else value


def credentials_for(pod_id: str, model: str, vllm_api_key: str, service_type: str = "text") -> dict[str, str]:
    base_url = f"{proxy_url(pod_id)}/v1"
    credentials = {
        "VLLM_BASE_URL": base_url,
        "VLLM_API_KEY": vllm_api_key,
        "VLLM_MODEL": model,
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": vllm_api_key,
    }
    if service_type == "asr":
        credentials["VLLM_TRANSCRIPTIONS_URL"] = f"{base_url}/audio/transcriptions"
        credentials["VLLM_SERVICE"] = "speech-to-text"
    return credentials


def credentials_text(credentials: dict[str, str]) -> str:
    return "\n".join(f'{key}="{value}"' for key, value in credentials.items())


def vllm_health(pod: dict[str, Any], credentials: dict[str, str] | None) -> dict[str, str]:
    state = pod_state(pod).upper()
    if state in {"ERROR", "TERMINATED"}:
        return {"label": "Hors ligne", "color": "#dc2626", "detail": f"Etat Runpod : {state}"}
    if state not in {"RUNNING", "READY"}:
        return {"label": "Demarrage", "color": "#d97706", "detail": f"Le pod est dans l'etat {state}."}
    if not credentials:
        return {
            "label": "Non verifiable",
            "color": "#d97706",
            "detail": "La cle vLLM de ce pod n'est pas disponible dans cette session.",
        }

    try:
        response = requests.get(
            f"{credentials['VLLM_BASE_URL'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {credentials['VLLM_API_KEY']}"},
            timeout=5,
        )
        if response.ok:
            return {
                "label": "Operationnel",
                "color": "#16a34a",
                "detail": "vLLM repond correctement sur /v1/models.",
            }
        if response.status_code in {401, 403}:
            return {
                "label": "Erreur auth",
                "color": "#dc2626",
                "detail": f"vLLM repond avec HTTP {response.status_code}.",
            }
        return {
            "label": "Demarrage",
            "color": "#d97706",
            "detail": f"L'endpoint repond encore avec HTTP {response.status_code}.",
        }
    except (requests.RequestException, KeyError):
        return {
            "label": "Hors ligne",
            "color": "#dc2626",
            "detail": "L'endpoint vLLM n'est pas joignable pour le moment.",
        }


def status_badge(status: dict[str, str]) -> str:
    return (
        '<span style="display:inline-flex;align-items:center;gap:0.4rem;'
        'padding:0.2rem 0.6rem;border-radius:999px;color:white;'
        f'background:{status["color"]};font-weight:600;font-size:0.85rem;">'
        '<span style="width:0.5rem;height:0.5rem;border-radius:50%;background:white;"></span>'
        f'{status["label"]}</span>'
    )


def show_vllm_status(pod: dict[str, Any], credentials: dict[str, str] | None) -> None:
    status = vllm_health(pod, credentials)
    st.markdown(status_badge(status), unsafe_allow_html=True)
    st.caption(status["detail"])


def show_credentials(pod_id: str, credentials: dict[str, str], key_suffix: str, pod: dict[str, Any] | None = None) -> None:
    st.success(f"Identifiants vLLM générés pour le pod {pod_id}.")
    st.info("Le modèle sera téléchargé au premier démarrage de vLLM. Copie ou télécharge ces valeurs maintenant : la clé vLLM reste uniquement dans cette session.")
    show_vllm_status(pod or {"state": "RUNNING"}, credentials)
    st.code(credentials_text(credentials), language="bash")
    st.download_button(
        "Télécharger les identifiants (.env)",
        data=credentials_text(credentials) + "\n",
        file_name=f"vllm-{pod_id}.env",
        mime="text/plain",
        key=f"download_env_{key_suffix}",
    )
    st.download_button(
        "Télécharger les identifiants (JSON)",
        data=json.dumps(credentials, indent=2) + "\n",
        file_name=f"vllm-{pod_id}.json",
        mime="application/json",
        key=f"download_json_{key_suffix}",
    )
    if st.button("Tester /v1/models", key=f"probe_{key_suffix}"):
        try:
            response = requests.get(
                f"{credentials['VLLM_BASE_URL']}/models",
                headers={"Authorization": f"Bearer {credentials['VLLM_API_KEY']}"},
                timeout=10,
            )
            if response.ok:
                st.success("vLLM répond. Le pod est prêt.")
                st.json(response.json())
            else:
                st.warning(f"Le pod répond encore avec HTTP {response.status_code}. Le chargement du modèle est peut-être en cours.")
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"vLLM n'est pas encore joignable : {exc}")


def inject_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #0b0f13; }
        [data-testid="stSidebar"] {
            background: #10151b;
            border-right: 1px solid #26313d;
        }
        [data-testid="stHeader"] { background: rgba(11, 15, 19, 0.88); }
        [data-testid="stMetric"] {
            background: #141a21;
            border: 1px solid #2a3541;
            border-radius: 6px;
            padding: 0.85rem 1rem;
            min-height: 108px;
        }
        [data-testid="stMetricLabel"] { color: #9ba8b5; }
        [data-testid="stMetricValue"] { color: #f3f6f8; }
        [data-testid="stDataFrame"] {
            border: 1px solid #2a3541;
            border-radius: 6px;
            overflow: hidden;
        }
        .stButton > button, .stDownloadButton > button, .stLinkButton > a {
            border-radius: 6px;
            min-height: 2.45rem;
        }
        .dashboard-kicker {
            color: #55d6be;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0;
            margin-bottom: 0.15rem;
        }
        .dashboard-rule {
            height: 1px;
            background: #26313d;
            margin: 0.4rem 0 1.2rem;
        }
        .harness-logo {
            height: 92px;
            background: #f3f6f8;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0.75rem;
        }
        .harness-logo img {
            max-width: 100%;
            max-height: 68px;
            object-fit: contain;
        }
        .harness-stat-label {
            color: #8f9dab;
            font-size: 0.76rem;
            margin-bottom: 0.1rem;
        }
        .harness-stat-value {
            color: #eef2f5;
            font-size: 1rem;
            font-weight: 650;
            overflow-wrap: anywhere;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_overview(
    base_url: str,
    api_key: str,
    account: dict[str, Any] | None,
    account_error: str,
) -> None:
    st.subheader("Vue d'ensemble")
    refresh_col, timestamp_col = st.columns([1, 4])
    with refresh_col:
        if st.button("Actualiser", key="refresh_overview"):
            st.rerun()
    with timestamp_col:
        st.caption(f"Dernière lecture : {datetime.now().astimezone().strftime('%H:%M:%S')}")

    if not api_key:
        st.info("Ajoute ta clé RunPod dans la barre latérale pour charger le compte et les machines.")
        return

    pods: list[dict[str, Any]] = []
    billing: list[dict[str, Any]] = []
    pods_error = ""
    billing_error = ""
    try:
        pods = response_items(request_runpod("GET", base_url, api_key, "/pods"))
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        pods_error = str(exc)
    try:
        billing = request_monthly_pod_billing(base_url, api_key)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        billing_error = str(exc)

    active_pods = [pod for pod in pods if is_active_pod(pod)]
    running_pods = [pod for pod in pods if pod_state(pod).upper() in {"RUNNING", "READY"}]
    active_gpu_count = sum(pod_gpu_count(pod) for pod in active_pods)
    pod_cost_per_hour = sum(pod_hourly_cost(pod) for pod in active_pods)
    account_available = isinstance(account, dict)
    balance = number_value((account or {}).get("clientBalance"))
    account_spend = number_value((account or {}).get("currentSpendPerHr"))
    hourly_spend = account_spend if account_available else pod_cost_per_hour

    metric_cols = st.columns(4)
    metric_cols[0].metric("Crédits disponibles", usd(balance) if account_available else "Indisponible")
    metric_cols[1].metric("Dépense actuelle", usd(hourly_spend, "/h"))
    metric_cols[2].metric(
        "Autonomie estimée",
        runway_text(balance, hourly_spend) if account_available else "Indisponible",
    )
    metric_cols[3].metric("Pods actifs", len(active_pods), delta=f"{len(running_pods)} opérationnel(s)")

    detail_cols = st.columns(4)
    detail_cols[0].metric("Coût projeté / jour", usd(hourly_spend * 24))
    detail_cols[1].metric("Pods ce mois", usd(billing_total(billing)) if not billing_error else "Indisponible")
    detail_cols[2].metric("GPU alloués", active_gpu_count)
    auto_pay = (account or {}).get("isAutoPayEnabled")
    detail_cols[3].metric("Auto-paiement", "Activé" if auto_pay else "Désactivé" if account_available else "Indisponible")

    if account_error:
        st.warning(f"Crédits indisponibles : {account_error}")
    elif account_available:
        under_balance = bool(account.get("underBalance"))
        runway_hours = balance / hourly_spend if hourly_spend > 0 else float("inf")
        if under_balance or runway_hours < 2:
            st.error("Solde critique : l'autonomie estimée est inférieure à deux heures.")
        elif runway_hours < 24:
            st.warning("Solde à surveiller : moins de 24 heures d'autonomie au rythme actuel.")

    if pods_error:
        st.error(f"Machines indisponibles : {pods_error}")
    elif active_pods:
        st.subheader("Charge active")
        active_rows = [
            {
                "Pod": pod.get("name") or pod.get("id", ""),
                "État": pod_state(pod).upper(),
                "GPU": f"{pod_gpu_count(pod)} × {pod_gpu_name(pod)}",
                "Coût / h": usd(pod_hourly_cost(pod)),
                "Projection / jour": usd(pod_hourly_cost(pod) * 24),
            }
            for pod in active_pods
        ]
        st.dataframe(active_rows, width="stretch", hide_index=True)
    else:
        st.info("Aucun pod actif. Les éventuels volumes persistants peuvent continuer à être facturés.")

    if billing_error:
        st.caption(f"Historique de facturation indisponible : {billing_error}")
    elif billing:
        daily_costs: dict[str, float] = {}
        pod_costs: dict[str, float] = {}
        pod_names = {str(pod.get("id")): str(pod.get("name") or pod.get("id")) for pod in pods}
        for record in billing:
            day = str(record.get("time", ""))[:10] or "Sans date"
            pod_id = str(record.get("podId") or "Inconnu")
            amount = number_value(record.get("amount"))
            daily_costs[day] = daily_costs.get(day, 0.0) + amount
            pod_costs[pod_id] = pod_costs.get(pod_id, 0.0) + amount

        chart_rows = [{"Date": day, "Coût ($)": amount} for day, amount in sorted(daily_costs.items())]
        st.subheader("Dépenses Pods du mois")
        st.bar_chart(chart_rows, x="Date", y="Coût ($)", width="stretch")
        cost_rows = [
            {"Pod": pod_names.get(pod_id, pod_id), "Coût du mois": usd(amount)}
            for pod_id, amount in sorted(pod_costs.items(), key=lambda item: item[1], reverse=True)
        ]
        st.dataframe(cost_rows, width="stretch", hide_index=True)

    if account_available:
        with st.expander("Paramètres de facturation"):
            billing_settings = [
                {
                    "Paramètre": "Limite de dépense",
                    "Valeur": usd(account.get("spendLimit"), "/h"),
                },
                {
                    "Paramètre": "Seuil d'auto-paiement",
                    "Valeur": usd(account.get("stripeAutoPaymentThreshold")),
                },
                {
                    "Paramètre": "Recharge automatique",
                    "Valeur": usd(account.get("stripeAutoReloadAmount")),
                },
                {
                    "Paramètre": "Solde minimum",
                    "Valeur": usd(account.get("minBalance")),
                },
            ]
            st.dataframe(billing_settings, width="stretch", hide_index=True)


def render_harness_card(
    harness_id: str,
    name: str,
    logo_filename: str,
    config: dict[str, Any],
    process_metrics: dict[str, Any],
    vllm_probe: dict[str, Any],
) -> None:
    running = bool(process_metrics.get("running"))
    gateway_ready = True
    if harness_id == "openclaw":
        gateway_ready = tcp_port_open("127.0.0.1", int(config.get("gateway_port") or 18789))
    alignment = model_alignment(str(config.get("model") or ""), vllm_probe.get("models") or [])

    if not running:
        overall = {"label": "Arrêté", "color": "#dc2626"}
    elif not gateway_ready or vllm_probe.get("label") != "Connecté" or alignment is False:
        overall = {"label": "Dégradé", "color": "#d97706"}
    else:
        overall = {"label": "Opérationnel", "color": "#16a34a"}

    with st.container(border=True):
        logo_col, summary_col = st.columns([1, 4])
        with logo_col:
            logo_uri = logo_data_uri(logo_filename)
            if logo_uri:
                st.markdown(
                    f'<div class="harness-logo"><img src="{logo_uri}" alt="{name}"></div>',
                    unsafe_allow_html=True,
                )
        with summary_col:
            title_col, status_col = st.columns([3, 1])
            with title_col:
                st.subheader(name)
                st.caption(
                    f"Configuration détectée : {config.get('config_path')}"
                    if config.get("configured")
                    else f"Configuration absente : {config.get('config_path')}"
                )
            with status_col:
                st.markdown(status_badge(overall), unsafe_allow_html=True)

        stat_cols = st.columns(5)
        stats = [
            ("Processus", str(process_metrics.get("count", 0))),
            ("En ligne depuis", duration_text(number_value(process_metrics.get("started_at")))),
            ("Mémoire locale", memory_text(int(process_metrics.get("memory_bytes") or 0))),
            (
                "vLLM",
                str(vllm_probe.get("label") or "Inconnu"),
            ),
            (
                "Latence",
                f"{vllm_probe['latency_ms']} ms" if vllm_probe.get("latency_ms") is not None else "—",
            ),
        ]
        for column, (label, value) in zip(stat_cols, stats):
            with column:
                st.caption(label)
                st.write(f"**{value}**")

        connection_cols = st.columns([2, 2, 1])
        with connection_cols[0]:
            st.caption("Modèle configuré")
            st.code(str(config.get("model") or "Non renseigné"), language=None)
        with connection_cols[1]:
            st.caption("Endpoint vLLM")
            st.code(str(config.get("base_url") or "Non renseigné"), language=None)
        with connection_cols[2]:
            st.caption("Alignement")
            if alignment is True:
                st.success("Modèle aligné")
            elif alignment is False:
                st.warning("Modèle différent")
            else:
                st.info("Non vérifiable")

        if harness_id == "openclaw":
            gateway_port = int(config.get("gateway_port") or 18789)
            gateway_status = (
                {"label": "Gateway connecté", "color": "#16a34a"}
                if gateway_ready
                else {"label": "Gateway hors ligne", "color": "#dc2626"}
            )
            st.markdown(status_badge(gateway_status), unsafe_allow_html=True)
            st.caption(f"Gateway local : ws://127.0.0.1:{gateway_port}")

        st.caption(str(vllm_probe.get("detail") or ""))
        if vllm_probe.get("models"):
            st.caption("Modèles servis : " + ", ".join(vllm_probe["models"]))

        processes = config.get("processes") or []
        if processes:
            with st.expander("Détails des processus"):
                process_rows = [
                    {
                        "PID": process.get("pid"),
                        "Processus": process.get("name"),
                        "Démarré à": datetime.fromtimestamp(
                            number_value(process.get("create_time"))
                        ).astimezone().strftime("%d/%m/%Y %H:%M:%S"),
                        "Mémoire": memory_text(int(process.get("memory_bytes") or 0)),
                    }
                    for process in processes
                ]
                st.dataframe(process_rows, width="stretch", hide_index=True)


@st.fragment(run_every=10)
def render_live_harnesses() -> None:
    process_groups = scan_harness_processes()
    harnesses = [
        (
            "openclaw",
            "OpenClaw",
            "openclaw.png",
            openclaw_harness_config(process_groups["openclaw"]),
        ),
        (
            "qwen-code",
            "Qwen Code",
            "qwen.png",
            qwen_harness_config(process_groups["qwen-code"]),
        ),
    ]
    probe_cache: dict[tuple[str, str], dict[str, Any]] = {}
    runtime_rows: list[tuple[str, str, str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for harness_id, name, logo, config in harnesses:
        process_metrics = harness_process_metrics(config.get("processes") or [])
        probe_key = (str(config.get("base_url") or ""), str(config.get("api_key") or ""))
        if probe_key not in probe_cache:
            probe_cache[probe_key] = probe_vllm(*probe_key)
        runtime_rows.append((harness_id, name, logo, config, process_metrics, probe_cache[probe_key]))

    running_count = sum(1 for row in runtime_rows if row[4].get("running"))
    connected_count = sum(1 for row in runtime_rows if row[5].get("label") == "Connecté")
    local_memory = sum(int(row[4].get("memory_bytes") or 0) for row in runtime_rows)
    overview_cols = st.columns(4)
    overview_cols[0].metric("Harness actifs", f"{running_count}/{len(runtime_rows)}")
    overview_cols[1].metric("Connexions vLLM", f"{connected_count}/{len(runtime_rows)}")
    overview_cols[2].metric("Mémoire locale", memory_text(local_memory))
    overview_cols[3].metric("Rafraîchissement", "10 s")

    for row in runtime_rows:
        render_harness_card(*row)
    st.caption(f"Dernière vérification : {datetime.now().astimezone().strftime('%H:%M:%S')}")


def render_harnesses() -> None:
    st.subheader("Harnesses")
    st.caption("État local, durée d'exécution et connectivité réelle vers vLLM.")
    render_live_harnesses()


def render_vllm_deploy(base_url: str, api_key: str) -> None:
    st.subheader("Déployer vLLM")
    templates = load_templates()
    template_choices = {"Configuration manuelle": None}
    template_choices.update({name: name for name in templates})
    selected_template_name = st.selectbox("Template de déploiement", list(template_choices))
    selected_template = templates.get(template_choices[selected_template_name])
    template_key = selected_template_name.lower().replace(" ", "_")
    if selected_template:
        st.caption("Les champs ci-dessous sont préremplis depuis cette template et restent modifiables.")

    template_service_type = str((selected_template or {}).get("serviceType", "")).lower()
    default_service_index = 1 if template_service_type == "asr" else 0
    service_label = st.selectbox(
        "Service à déployer",
        ["Génération texte", "Transcription vocale"],
        index=default_service_index,
        key=f"deploy_service_{template_key}",
    )
    service_type = "asr" if service_label == "Transcription vocale" else "text"
    st.caption(
        "Le modèle audio sera exposé sur /v1/audio/transcriptions."
        if service_type == "asr"
        else "Le modèle texte sera exposé sur l'API OpenAI-compatible /v1."
    )
    if not api_key:
        st.info("Définis d'abord RUNPOD_API_KEY ou saisis ta clé Runpod dans la barre latérale.")
        return

    template_cloud_type = str((selected_template or {}).get("cloudType", "SECURE"))
    cloud_options = ["SECURE", "COMMUNITY"]
    cloud_index = cloud_options.index(template_cloud_type) if template_cloud_type in cloud_options else 0
    cloud_type = st.selectbox(
        "Type de cloud",
        cloud_options,
        index=cloud_index,
        key=f"deploy_cloud_{template_key}",
        help="COMMUNITY offre souvent davantage de capacité, mais repose sur des fournisseurs communautaires.",
    )
    with st.spinner("Récupération des GPU et de leur disponibilité Runpod..."):
        try:
            gpu_catalog = request_gpu_catalog(api_key, cloud_type)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            st.error(f"Impossible de récupérer le catalogue GPU Runpod : {exc}")
            st.info("Vérifie que RUNPOD_API_KEY est valide. La liste des GPU utilise l'API GraphQL Runpod.")
            return

    gpu_options = {
        gpu_option_label(gpu, cloud_type): str(gpu.get("id"))
        for gpu in gpu_catalog
        if gpu.get("id")
    }
    if not gpu_options:
        st.warning("Runpod n'a retourné aucun GPU pour ce cloud.")
        return
    preferred_gpu_ids = template_gpu_ids(selected_template)
    template_gpu_labels = [
        label for preferred_id in preferred_gpu_ids for label, gpu_id in gpu_options.items() if gpu_id == preferred_id
    ]
    default_gpu_labels = template_gpu_labels or [
        next((label for label in gpu_options if "A40" in label), next(iter(gpu_options)))
    ]
    selected_gpu_labels = st.multiselect(
        "GPU(s), dans l'ordre de préférence",
        options=list(gpu_options),
        default=default_gpu_labels,
        key=f"deploy_gpu_{template_key}",
        help="Les GPU sélectionnés sont envoyés à Runpod dans cet ordre. Leur disponibilité est récupérée en direct.",
    )
    selected_gpu_ids = [gpu_options[label] for label in selected_gpu_labels]
    st.caption("Les prix et états affichés proviennent du catalogue Runpod au moment du chargement.")

    with st.form("vllm_deploy_form"):
        name = st.text_input(
            "Nom du pod",
            value=str(template_default_value(selected_template, "name", "vllm-a40")),
            key=f"deploy_name_{template_key}",
        )
        template_env = (selected_template or {}).get("env", {})
        if not isinstance(template_env, dict):
            template_env = {}
        default_model = template_env.get("MODEL_ID")
        if not isinstance(default_model, str) or default_model.startswith("<"):
            default_model = "openai/whisper-large-v3-turbo" if service_type == "asr" else "Qwen/Qwen3-8B"
        model = st.text_input("Modèle Hugging Face", value=default_model, key=f"deploy_model_{template_key}")
        default_hf_token = template_env.get("HF_TOKEN", "")
        if not isinstance(default_hf_token, str) or default_hf_token.startswith("<"):
            default_hf_token = ""
        hf_token = st.text_input(
            "Token Hugging Face (optionnel)",
            value=default_hf_token,
            type="password",
            key=f"deploy_hf_token_{template_key}",
            help="Nécessaire uniquement pour un modèle privé ou soumis à une licence Hugging Face.",
        )
        network_volume_id = st.text_input(
            "Network volume ID (optionnel)",
            value=str(template_default_value(selected_template, "networkVolumeId", "")),
            key=f"deploy_network_volume_{template_key}",
            help="Recommandé pour conserver le cache du modèle entre plusieurs pods.",
        )
        default_container_disk = int(template_default_value(selected_template, "containerDiskInGb", 50))
        default_volume = int(template_default_value(selected_template, "volumeInGb", 100))
        disk_col, volume_col = st.columns(2)
        with disk_col:
            container_disk_gb = st.number_input(
                "Disque conteneur (GB)",
                min_value=20,
                value=max(20, default_container_disk),
                step=10,
                key=f"deploy_container_disk_{template_key}",
            )
        with volume_col:
            volume_gb = st.number_input(
                "Volume de travail (GB)",
                min_value=20,
                value=max(20, default_volume),
                step=10,
                key=f"deploy_volume_{template_key}",
            )
        submitted = st.form_submit_button("Créer le pod vLLM", type="primary", disabled=not selected_gpu_ids)

    if not submitted:
        return
    if not name.strip() or not model.strip() or not selected_gpu_ids:
        st.error("Le nom, le modèle et le GPU sont obligatoires.")
        return

    vllm_api_key = secrets.token_urlsafe(32)
    payload = build_vllm_payload(
        name=name.strip(),
        model=model.strip(),
        service_type=service_type,
        gpu_types=selected_gpu_ids,
        cloud_type=cloud_type,
        vllm_api_key=vllm_api_key,
        hf_token=hf_token,
        network_volume_id=network_volume_id,
        volume_gb=int(volume_gb),
        container_disk_gb=int(container_disk_gb),
        image_name=str(template_default_value(selected_template, "imageName", "vllm/vllm-openai:latest")),
        extra_start_args=template_start_args(selected_template),
        template_env=template_env,
    )
    try:
        created = request_runpod("POST", base_url, api_key, "/pods", json=payload)
        pod_id = pod_identifier(created)
        if not pod_id:
            st.warning("Runpod a accepté la demande, mais aucun ID de pod n'a été retourné.")
            st.json(created)
            return
        credentials = credentials_for(pod_id, model.strip(), vllm_api_key, service_type)
        st.session_state.setdefault("vllm_credentials", {})[pod_id] = credentials
        st.success(f"Déploiement envoyé. Pod : {pod_id}")
        st.caption(f"État initial Runpod : {pod_state(created) if isinstance(created, dict) else 'CREATING'}")
        show_credentials(pod_id, credentials, "created", created if isinstance(created, dict) else None)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        message = str(exc)
        if "no instances currently available" in message.lower():
            st.error("Aucune instance n'est actuellement disponible pour cette combinaison de cloud et de GPU.")
            st.info("Essaie COMMUNITY, un autre GPU, ou retente plus tard. Plusieurs GPU peuvent être indiqués dans le champ, séparés par des virgules.")
        else:
            st.error(f"Déploiement impossible : {exc}")


def render_machines(base_url: str, api_key: str) -> None:
    if not api_key:
        st.info("Définis RUNPOD_API_KEY ou saisis une clé dans la barre latérale pour charger les machines.")
        return

    st.subheader("Machines")
    refresh_col, filter_col = st.columns([1, 3])
    with refresh_col:
        if st.button("Actualiser"):
            st.rerun()
    with filter_col:
        state_filter = st.selectbox(
            "Filtrer par état",
            ["Tous", "RUNNING", "CREATING", "STARTING", "STOPPED", "TERMINATED", "ERROR"],
            label_visibility="collapsed",
        )

    try:
        pods = response_items(request_runpod("GET", base_url, api_key, "/pods"))
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.error(f"Impossible de charger les machines : {exc}")
        pods = []

    if state_filter != "Tous":
        pods = [pod for pod in pods if pod_state(pod).upper() == state_filter]

    machine_metrics = st.columns(3)
    machine_metrics[0].metric("Machines visibles", len(pods))
    machine_metrics[1].metric("GPU actifs", sum(pod_gpu_count(pod) for pod in pods if is_active_pod(pod)))
    machine_metrics[2].metric(
        "Coût actif",
        usd(sum(pod_hourly_cost(pod) for pod in pods if is_active_pod(pod)), "/h"),
    )
    if pods:
        rows = [
            {
                "Nom": pod.get("name", ""),
                "ID": pod.get("id", ""),
                "État": pod_state(pod).upper(),
                "GPU": f"{pod_gpu_count(pod)} × {pod_gpu_name(pod)}",
                "Coût / h": usd(pod_hourly_cost(pod)),
                "Coût / jour": usd(pod_hourly_cost(pod) * 24),
                "Volume": f"{pod.get('volumeInGb', 0)} GB",
                "Région": pod.get("region") or pod.get("dataCenterId", ""),
                "IP publique": pod.get("publicIp", ""),
            }
            for pod in pods
        ]
        st.dataframe(rows, width="stretch", hide_index=True)

        st.subheader("État des modèles vLLM")
        credentials_by_pod = st.session_state.get("vllm_credentials", {})
        for pod in pods:
            pod_id = str(pod.get("id", ""))
            status = vllm_health(pod, credentials_by_pod.get(pod_id))
            status_col, detail_col = st.columns([1, 3])
            with status_col:
                st.markdown(
                    f"**{pod.get('name', pod_id)}**<br>{status_badge(status)}",
                    unsafe_allow_html=True,
                )
            with detail_col:
                st.caption(status["detail"])

        pod_options = {
            f"{pod.get('name', 'sans nom')} · {pod.get('id', '')}": pod.get("id", "")
            for pod in pods
            if pod.get("id")
        }
        selected_label = st.selectbox("Machine à piloter", list(pod_options))
        selected_id = pod_options[selected_label]
        selected_pod = next((pod for pod in pods if str(pod.get("id")) == str(selected_id)), {})
        endpoint_col, cost_col = st.columns([3, 1])
        with endpoint_col:
            st.text_input(
                "Endpoint OpenAI-compatible",
                value=f"{proxy_url(str(selected_id))}/v1",
                disabled=True,
                key=f"endpoint_{selected_id}",
            )
        with cost_col:
            st.metric("Coût de la machine", usd(pod_hourly_cost(selected_pod), "/h"))
        action_col, confirm_col = st.columns([1, 3])
        with action_col:
            action = st.selectbox("Action", ["terminate", "delete"], label_visibility="collapsed")
        with confirm_col:
            confirmed = st.checkbox("Je confirme l'action sur cette machine")
        if st.button("Exécuter l'action", type="secondary", disabled=not confirmed):
            try:
                if action == "delete":
                    request_runpod("DELETE", base_url, api_key, f"/pods/{selected_id}")
                else:
                    request_runpod(
                        "POST",
                        base_url,
                        api_key,
                        f"/pods/{selected_id}/terminate",
                        json={"reason": "requested_via_dashboard"},
                    )
                st.success(f"Action {action} envoyée pour {selected_id}.")
                st.rerun()
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                st.error(f"Action impossible : {exc}")
    else:
        st.info("Aucune machine ne correspond au filtre sélectionné.")

    credentials = st.session_state.get("vllm_credentials", {})
    if credentials:
        st.divider()
        st.subheader("Identifiants vLLM de cette session")
        pods_by_id = {str(pod.get("id")): pod for pod in pods if pod.get("id")}
        for pod_id, pod_credentials in credentials.items():
            with st.expander(pod_id, expanded=True):
                show_credentials(
                    pod_id,
                    pod_credentials,
                    f"machine_{pod_id}",
                    pods_by_id.get(pod_id),
                )


def render_templates() -> None:
    st.subheader("Templates de machines")
    st.caption("Les templates sont des fichiers JSON locaux et peuvent être versionnés dans le repository.")
    templates = load_templates()
    if templates:
        selected_template = st.selectbox("Template à consulter", list(templates), key="template_editor")
        edited_text = st.text_area(
            "Contenu JSON",
            value=template_text(templates[selected_template]),
            height=420,
            key="template_editor_text",
        )
        if st.button("Enregistrer le template"):
            try:
                parsed = json.loads(edited_text)
                if not isinstance(parsed, dict):
                    raise ValueError("Le template doit être un objet JSON.")
                (TEMPLATES_DIR / selected_template).write_text(
                    template_text(parsed) + "\n", encoding="utf-8"
                )
                st.success(f"Template {selected_template} enregistré.")
            except json.JSONDecodeError as exc:
                st.error(f"JSON invalide : {exc}")
            except (OSError, ValueError) as exc:
                st.error(f"Impossible d'enregistrer le template : {exc}")
    else:
        st.info("Aucun template disponible.")


def main() -> None:
    st.set_page_config(
        page_title="RunPod Control Center",
        page_icon="R",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_dashboard_styles()
    st.markdown('<div class="dashboard-kicker">LOCAL GPU CONTROL</div>', unsafe_allow_html=True)
    st.title("RunPod Control Center")
    st.caption("Infrastructure GPU · coûts · modèles")
    st.markdown('<div class="dashboard-rule"></div>', unsafe_allow_html=True)

    with st.sidebar:
        st.header("Connexion")
        default_base_url = os.getenv("RUNPOD_API_BASE_URL", DEFAULT_API_BASE_URL)
        base_url = st.text_input("API base URL", value=default_base_url)
        env_api_key = os.getenv("RUNPOD_API_KEY", "")
        api_key = st.text_input(
            "Clé API Runpod",
            value=env_api_key,
            type="password",
            help="La clé saisie reste dans la session Streamlit et n'est pas enregistrée par l'application.",
        )
        if env_api_key:
            st.caption("Clé préremplie depuis RUNPOD_API_KEY.")
        else:
            st.caption("Aucune clé d'environnement détectée.")
        if api_key and st.button("Enregistrer la clé dans Windows"):
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    "Environment",
                    0,
                    winreg.KEY_SET_VALUE,
                ) as environment_key:
                    winreg.SetValueEx(environment_key, "RUNPOD_API_KEY", 0, winreg.REG_SZ, api_key)
                os.environ["RUNPOD_API_KEY"] = api_key
                st.success("Clé enregistrée dans les variables utilisateur Windows.")
                st.caption("Les nouveaux terminaux la verront automatiquement. Cette session l'utilise déjà.")
            except (ImportError, OSError) as exc:
                st.error(f"Impossible d'enregistrer la variable Windows : {exc}")

        account: dict[str, Any] | None = None
        account_error = ""
        if api_key:
            try:
                account = request_account_summary(api_key)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                account_error = str(exc)

        st.divider()
        st.subheader("Compte")
        if account:
            st.metric("Crédits RunPod", usd(account.get("clientBalance")))
            st.caption(f"Dépense actuelle : {usd(account.get('currentSpendPerHr'), '/h')}")
        elif api_key:
            st.caption("Informations financières indisponibles pour cette clé.")
        else:
            st.caption("En attente d'une clé API.")

        st.divider()
        page = st.radio(
            "Navigation",
            ["Vue d'ensemble", "Machines", "Harnesses", "Déployer vLLM", "Templates", "Configuration"],
            label_visibility="collapsed",
        )

    if page == "Vue d'ensemble":
        render_overview(base_url, api_key, account, account_error)
    elif page == "Machines":
        render_machines(base_url, api_key)
    elif page == "Harnesses":
        render_harnesses()
    elif page == "Déployer vLLM":
        render_vllm_deploy(base_url, api_key)
    elif page == "Templates":
        render_templates()
    else:
        st.subheader("Configuration locale")
        st.write(f"**API utilisée :** `{base_url}`")
        st.write(
            "**Clé API :** "
            + ("préremplie depuis la variable RUNPOD_API_KEY" if env_api_key else "saisie manuellement pour cette session")
        )
        st.warning(
            "La clé Runpod n'est pas écrite dans les templates, le repository ou un fichier de configuration par ce dashboard. "
            "Les clés vLLM générées sont conservées uniquement dans la session Streamlit : télécharge-les après le déploiement."
        )
        st.code("$env:RUNPOD_API_KEY = \"<ta-cle-runpod>\"\nstreamlit run monitoring/runpod/dashboard/app.py", language="powershell")


if __name__ == "__main__":
    main()
