"""Local Streamlit dashboard for managing Runpod pods and vLLM deployments."""

from __future__ import annotations

import base64
import copy
import html
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st

# Streamlit and its test runner do not always add the script directory to sys.path.
_DASHBOARD_DIR = str(Path(__file__).resolve().parent)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from lifecycle import (
    clear_policy,
    load_policy_store,
    mark_completed,
    mark_error,
    parse_timestamp,
    pod_policy_action,
    policy_lock,
    save_policy_store,
    schedule_policy,
    utc_now,
)
from connections import (
    connection_slug,
    load_connection_store,
    openclaw_model_ref,
    openclaw_provider_config,
    openclaw_provider_id,
    remove_connection,
    save_connection_store,
    upsert_connection,
)


RUNPOD_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = RUNPOD_DIR / "templates"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LIFECYCLE_POLICIES_PATH = RUNPOD_DIR / "data" / "pod-lifecycle.json"
VLLM_CONNECTIONS_PATH = RUNPOD_DIR / "data" / "vllm-connections.json"
DEFAULT_API_BASE_URL = "https://rest.runpod.io/v1"
VLLM_PORT = 8000
ACTIVE_POD_STATES = {"RUNNING", "READY", "STARTING", "CREATING"}
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
QWEN_CONFIG_PATH = Path.home() / ".qwen" / "settings.json"
SENSITIVE_CONFIG_WORDS = {
    "apikey",
    "api_key",
    "auth",
    "credential",
    "header",
    "mcpservers",
    "password",
    "secret",
    "token",
}

QWEN_SETTING_SPECS = [
    {"path": "model.name", "type": "string", "description": "Modele principal."},
    {
        "path": "model.reasoningEffort",
        "type": "enum",
        "choices": ["low", "medium", "high", "xhigh", "max"],
        "description": "Niveau d'effort de raisonnement, si le modele le supporte.",
    },
    {"path": "model.fastModel", "type": "string", "description": "Petit modele auxiliaire pour les suggestions et la speculation."},
    {"path": "model.advisorModel", "type": "string", "description": "Modele utilise par l'advisor."},
    {"path": "model.visionModel", "type": "string", "description": "Modele de vision auxiliaire."},
    {"path": "model.compactionModel", "type": "string", "description": "Modele utilise pour compacter le contexte."},
    {"path": "model.imageModel", "type": "string", "description": "Modele de generation d'images."},
    {"path": "model.voiceModel", "type": "string", "description": "Modele vocal auxiliaire."},
    {"path": "model.modelFallbacks", "type": "array", "description": "Modeles de repli, dans l'ordre."},
    {"path": "model.generationConfig.timeout", "type": "integer", "description": "Timeout des requetes modele."},
    {"path": "model.generationConfig.maxRetries", "type": "integer", "description": "Nombre maximal de nouvelles tentatives."},
    {"path": "model.generationConfig.contextWindowSize", "type": "integer", "description": "Taille de fenetre de contexte declaree."},
    {"path": "model.generationConfig.enableCacheControl", "type": "boolean", "description": "Active les controles de cache fournisseur."},
    {"path": "model.generationConfig.splitToolMedia", "type": "boolean", "description": "Separe les medias produits par les outils."},
    {"path": "model.generationConfig.samplingParams.temperature", "type": "number", "description": "Temperature d'echantillonnage."},
    {"path": "model.generationConfig.samplingParams.top_p", "type": "number", "description": "Nucleus sampling."},
    {"path": "model.generationConfig.samplingParams.max_tokens", "type": "integer", "description": "Nombre maximal de tokens generes."},
    {"path": "model.generationConfig.extra_body.enable_thinking", "type": "boolean", "description": "Active le mode thinking pour les modeles Qwen compatibles."},
    {"path": "model.maxSessionTurns", "type": "integer", "description": "Nombre maximal de tours dans une session."},
    {"path": "model.sessionTokenLimit", "type": "integer", "description": "Budget de tokens maximal de la session."},
    {"path": "model.maxWallTimeSeconds", "type": "integer", "description": "Duree maximale de la session."},
    {"path": "model.maxToolCalls", "type": "integer", "description": "Nombre maximal d'appels d'outils par session."},
    {"path": "model.maxToolCallsPerTurn", "type": "integer", "description": "Nombre maximal d'appels d'outils par tour."},
    {"path": "model.maxSubagentDepth", "type": "integer", "description": "Profondeur maximale des sous-agents."},
    {"path": "model.skipLoopDetection", "type": "boolean", "description": "Desactive la detection de boucle."},
    {"path": "model.skipStartupContext", "type": "boolean", "description": "Ignore le contexte de demarrage."},
    {"path": "context.autoCompactThreshold", "type": "number", "description": "Seuil de compaction automatique du contexte."},
    {"path": "context.includeDirectories", "type": "array", "description": "Dossiers supplementaires inclus dans le contexte."},
    {"path": "tools.approvalMode", "type": "enum", "choices": ["plan", "default", "auto-edit", "auto", "yolo"], "description": "Politique d'approbation des outils."},
    {"path": "tools.useRipgrep", "type": "boolean", "description": "Utilise ripgrep pour les recherches."},
    {"path": "tools.toolSearch.enabled", "type": "boolean", "description": "Active la recherche dynamique d'outils."},
    {"path": "tools.computerUse.enabled", "type": "boolean", "description": "Active les outils de controle de l'ordinateur."},
    {"path": "security.folderTrust.enabled", "type": "boolean", "description": "Active la confiance explicite des dossiers."},
    {"path": "general.vimMode", "type": "boolean", "description": "Active les raccourcis Vim."},
    {"path": "general.enableAutoUpdate", "type": "boolean", "description": "Active les mises a jour automatiques."},
    {"path": "general.enableRecap", "type": "boolean", "description": "Genere un recapitulatif de session."},
    {"path": "general.enableGitCoauthor", "type": "boolean", "description": "Ajoute Qwen comme co-auteur Git."},
    {"path": "ui.showLineNumbers", "type": "boolean", "description": "Affiche les numeros de ligne."},
    {"path": "ui.showCitations", "type": "boolean", "description": "Affiche les citations."},
    {"path": "ui.showFollowupSuggestions", "type": "boolean", "description": "Affiche les suggestions de suivi."},
    {"path": "ui.showTokenUsage", "type": "boolean", "description": "Affiche l'utilisation des tokens."},
    {"path": "ui.showTokensPerSecond", "type": "boolean", "description": "Affiche le debit de tokens."},
    {"path": "ui.showStatusInTitle", "type": "boolean", "description": "Affiche l'etat dans le titre du terminal."},
    {"path": "telemetry.enabled", "type": "boolean", "description": "Active la telemetrie Qwen Code."},
    {"path": "skills.disabled", "type": "array", "description": "Skills desactives explicitement."},
    {"path": "skills.disabledLevels", "type": "array", "description": "Niveaux de skills desactives."},
    {"path": "mcpServers", "type": "object", "description": "Serveurs MCP declares dans Qwen Code."},
]


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


def local_time_text(value: Any) -> str:
    parsed = parse_timestamp(value)
    return parsed.astimezone().strftime("%d/%m/%Y %H:%M") if parsed else "—"


def reconcile_lifecycle_policies(
    base_url: str,
    api_key: str,
    pods: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Apply overdue stop/delete policies once and persist the result."""
    if not api_key:
        return []
    with policy_lock(LIFECYCLE_POLICIES_PATH) as acquired:
        if not acquired:
            return []
        store = load_policy_store(LIFECYCLE_POLICIES_PATH)
        policies = store.get("policies", {})
        if not isinstance(policies, dict) or not policies:
            return []
        try:
            current_pods = pods if pods is not None else response_items(request_runpod("GET", base_url, api_key, "/pods"))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            for policy in policies.values():
                if isinstance(policy, dict):
                    mark_error(policy, f"Lecture RunPod impossible : {exc}")
            save_policy_store(LIFECYCLE_POLICIES_PATH, store)
            return [{"kind": "error", "message": f"Cycle de vie : lecture RunPod impossible ({exc})."}]

        pods_by_id = {str(pod.get("id")): pod for pod in current_pods if pod.get("id")}
        events: list[dict[str, str]] = []
        changed = False
        now = utc_now()
        for pod_id, policy in policies.items():
            if not isinstance(policy, dict):
                continue
            pod = pods_by_id.get(str(pod_id))
            if not pod:
                continue
            action = pod_policy_action(policy, pod_state(pod), now)
            if not action:
                continue
            pod_name = str(policy.get("podName") or pod.get("name") or pod_id)
            if action == "pause_complete":
                mark_completed(policy, action, now)
                events.append({"kind": "success", "message": f"{pod_name} est déjà en pause."})
                changed = True
                continue
            try:
                if action == "stop":
                    request_runpod("POST", base_url, api_key, f"/pods/{pod_id}/stop")
                    message = f"Pause automatique demandée pour {pod_name}."
                else:
                    request_runpod("DELETE", base_url, api_key, f"/pods/{pod_id}")
                    message = f"Destruction automatique demandée pour {pod_name}."
                mark_completed(policy, action, now)
                events.append({"kind": "success", "message": message})
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                mark_error(policy, str(exc), now)
                events.append({"kind": "error", "message": f"{pod_name} : {exc}"})
            changed = True
        if changed:
            save_policy_store(LIFECYCLE_POLICIES_PATH, store)
        return events


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


def save_user_environment_variable(name: str, value: str) -> None:
    if os.name != "nt":
        raise RuntimeError("L'enregistrement des variables utilisateur est actuellement disponible sous Windows.")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as environment_key:
        winreg.SetValueEx(environment_key, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value


def executable_path(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"La commande '{name}' est introuvable dans le PATH.")
    return executable


def run_cli(
    name: str,
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 45,
) -> str:
    executable = executable_path(name)
    command = [executable, *arguments]
    if Path(executable).suffix.lower() in {".bat", ".cmd"}:
        command = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(command),
        ]
    result = subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"code {result.returncode}"
        raise RuntimeError(detail[:800])
    return result.stdout.strip()


def run_cli_json(name: str, arguments: list[str], timeout: int = 45) -> Any:
    output = run_cli(name, arguments, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"La commande {name} n'a pas retourne de JSON valide.") from exc


@st.cache_data(ttl=120, show_spinner=False)
def load_openclaw_schema() -> tuple[dict[str, Any], str]:
    try:
        value = run_cli_json("openclaw", ["config", "schema"], timeout=60)
        return (value if isinstance(value, dict) else {}), ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {}, str(exc)


@st.cache_data(ttl=60, show_spinner=False)
def load_openclaw_models() -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    try:
        listed = run_cli_json("openclaw", ["models", "list", "--json"], timeout=60)
        status = run_cli_json("openclaw", ["models", "status", "--json"], timeout=60)
        items = listed if isinstance(listed, list) else listed.get("models", []) if isinstance(listed, dict) else []
        return [item for item in items if isinstance(item, dict)], status if isinstance(status, dict) else {}, ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return [], {}, str(exc)


@st.cache_data(ttl=300, show_spinner=False)
def load_openclaw_core() -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, Any], str]:
    """Load independent OpenClaw CLI inventories concurrently and cache the result."""

    def load_schema() -> tuple[dict[str, Any], str]:
        try:
            value = run_cli_json("openclaw", ["config", "schema"], timeout=60)
            return (value if isinstance(value, dict) else {}), ""
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return {}, str(exc)

    def load_model_list() -> tuple[list[dict[str, Any]], str]:
        try:
            value = run_cli_json("openclaw", ["models", "list", "--json"], timeout=60)
            items = value if isinstance(value, list) else value.get("models", []) if isinstance(value, dict) else []
            return [item for item in items if isinstance(item, dict)], ""
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return [], str(exc)

    def load_model_status() -> tuple[dict[str, Any], str]:
        try:
            value = run_cli_json("openclaw", ["models", "status", "--json"], timeout=60)
            return (value if isinstance(value, dict) else {}), ""
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return {}, str(exc)

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="openclaw-core") as executor:
        schema_future = executor.submit(load_schema)
        models_future = executor.submit(load_model_list)
        status_future = executor.submit(load_model_status)
        schema, schema_error = schema_future.result()
        models, models_error = models_future.result()
        status, status_error = status_future.result()
    model_error = "; ".join(error for error in (models_error, status_error) if error)
    return schema, schema_error, models, status, model_error


@st.cache_data(ttl=60, show_spinner=False)
def load_openclaw_plugins() -> tuple[list[dict[str, Any]], str]:
    try:
        value = run_cli_json("openclaw", ["plugins", "list", "--json"], timeout=90)
        items = value if isinstance(value, list) else value.get("plugins", []) if isinstance(value, dict) else []
        return [item for item in items if isinstance(item, dict)], ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return [], str(exc)


@st.cache_data(ttl=60, show_spinner=False)
def load_openclaw_skills() -> tuple[list[dict[str, Any]], str]:
    try:
        value = run_cli_json("openclaw", ["skills", "list", "--json"], timeout=90)
        items = value if isinstance(value, list) else value.get("skills", []) if isinstance(value, dict) else []
        return [item for item in items if isinstance(item, dict)], ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return [], str(exc)


@st.cache_data(ttl=120, show_spinner=False)
def load_openclaw_extensions() -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], str]:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="openclaw-extensions") as executor:
        plugins_future = executor.submit(load_openclaw_plugins)
        skills_future = executor.submit(load_openclaw_skills)
        plugins, plugin_error = plugins_future.result()
        skills, skill_error = skills_future.result()
    return plugins, plugin_error, skills, skill_error


def patch_openclaw_config(patch: dict[str, Any]) -> None:
    run_cli(
        "openclaw",
        ["config", "patch", "--stdin"],
        input_text=json.dumps(patch, ensure_ascii=True),
        timeout=60,
    )
    run_cli("openclaw", ["config", "validate"], timeout=60)
    load_openclaw_core.clear()
    load_openclaw_models.clear()
    load_openclaw_plugins.clear()
    load_openclaw_skills.clear()
    load_openclaw_extensions.clear()


def save_json_config(path: Path, value: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = path.with_name(f"{path.name}.bak-{timestamp}")
        shutil.copy2(path, backup_path)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temporary_path, path)
    return backup_path


def nested_value(value: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def set_nested_value(value: dict[str, Any], path: str, new_value: Any) -> None:
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = new_value


def delete_nested_value(value: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = value
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            return
        parents.append((current, part))
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)
    for parent, key in reversed(parents):
        if isinstance(parent.get(key), dict) and not parent[key]:
            parent.pop(key, None)


def nested_patch(path: str, new_value: Any) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    set_nested_value(patch, path, new_value)
    return patch


def deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def is_sensitive_config_path(path: str) -> bool:
    normalized = path.lower().replace("-", "_")
    parts = normalized.replace("[", ".").replace("]", "").split(".")
    return any(word in part for part in parts for word in SENSITIVE_CONFIG_WORDS)


def schema_pointer(root: dict[str, Any], pointer: str) -> dict[str, Any]:
    if not pointer.startswith("#/"):
        return {}
    current: Any = root
    for part in pointer[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return {}
        current = current[key]
    return current if isinstance(current, dict) else {}


def resolved_schema_node(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(node)
    reference = resolved.get("$ref")
    if isinstance(reference, str):
        base = schema_pointer(root, reference)
        resolved = {**base, **{key: value for key, value in resolved.items() if key != "$ref"}}
    return resolved


def schema_type_and_choices(root: dict[str, Any], node: dict[str, Any]) -> tuple[str, list[Any]]:
    node = resolved_schema_node(root, node)
    choices = list(node.get("enum", [])) if isinstance(node.get("enum"), list) else []
    value_type = node.get("type") if isinstance(node.get("type"), str) else ""
    variants = node.get("anyOf") if isinstance(node.get("anyOf"), list) else []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        variant = resolved_schema_node(root, variant)
        if "const" in variant and variant["const"] is not None:
            choices.append(variant["const"])
        if not value_type and isinstance(variant.get("type"), str) and variant.get("type") != "null":
            value_type = variant["type"]
    if choices:
        value_type = "enum"
    return value_type or "object", list(dict.fromkeys(choices))


def flatten_schema(root: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], path: str, seen: set[str]) -> None:
        reference = node.get("$ref")
        if isinstance(reference, str):
            if reference in seen:
                return
            seen = {*seen, reference}
        node = resolved_schema_node(root, node)
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if properties:
            for name, child in properties.items():
                if isinstance(child, dict):
                    visit(child, f"{path}.{name}" if path else name, seen)
            return
        if not path or is_sensitive_config_path(path):
            return
        value_type, choices = schema_type_and_choices(root, node)
        rows.append(
            {
                "Chemin": path,
                "Type": value_type,
                "Valeurs": ", ".join(str(choice) for choice in choices[:20]),
                "Defaut": node.get("default", ""),
                "Description": str(node.get("description") or node.get("title") or ""),
            }
        )

    visit(root, "", set())
    return rows


def plugin_id(plugin: dict[str, Any]) -> str:
    return str(plugin.get("id") or plugin.get("name") or plugin.get("pluginId") or "")


def plugin_enabled(plugin: dict[str, Any]) -> bool:
    if isinstance(plugin.get("enabled"), bool):
        return bool(plugin["enabled"])
    return str(plugin.get("status") or plugin.get("state") or "").lower() in {"enabled", "loaded", "active"}


def scan_qwen_extensions() -> list[dict[str, Any]]:
    extension_dir = Path.home() / ".qwen" / "extensions"
    rows: list[dict[str, Any]] = []
    for manifest in sorted(extension_dir.glob("*/qwen-extension.json")) if extension_dir.exists() else []:
        value = load_json_object(manifest)
        rows.append(
            {
                "Extension": value.get("name") or manifest.parent.name,
                "Version": value.get("version", ""),
                "Chemin": str(manifest.parent),
            }
        )
    return rows


def scan_qwen_skills() -> list[dict[str, Any]]:
    roots = [Path.home() / ".qwen" / "skills", Path.cwd() / ".qwen" / "skills"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for skill_path in sorted(root.glob("*/SKILL.md")) if root.exists() else []:
            resolved = str(skill_path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append({"Skill": skill_path.parent.name, "Chemin": str(skill_path)})
    return rows


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


def effective_harness_connection(config: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    connection = dict(config)
    if override:
        for key in ("base_url", "api_key", "model"):
            if key in override:
                connection[key] = override[key]
    return connection


def pool_connection_override(
    harness_id: str,
    config: dict[str, Any],
    connection: dict[str, Any],
) -> dict[str, Any]:
    """Build a session-only harness override from a saved vLLM pool entry."""
    api_key_env = str(connection.get("apiKeyEnv") or "")
    override = {
        "base_url": str(connection.get("baseUrl") or "").strip().rstrip("/"),
        "api_key": os.getenv(api_key_env, ""),
        "model": str(connection.get("model") or "").strip(),
    }
    # OpenClaw keeps its selected model in openclaw.json. The pool only swaps
    # the endpoint and credentials for the locally launched Gateway.
    if harness_id == "openclaw":
        override["model"] = str(config.get("model") or "").strip()
    return override


def start_harness(harness_id: str, config: dict[str, Any]) -> None:
    if os.name != "nt":
        raise RuntimeError("Le pilotage des harnesses est actuellement disponible uniquement sous Windows.")

    executable_name = "openclaw" if harness_id == "openclaw" else "qwen"
    executable = shutil.which(executable_name)
    if not executable:
        raise RuntimeError(f"La commande '{executable_name}' est introuvable dans le PATH.")

    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    api_key = str(config.get("api_key") or "").strip()
    configured_model = str(config.get("model") or "").strip()
    model = configured_model.split("/", 1)[-1]
    if not base_url or not model:
        raise RuntimeError("Renseigne au minimum l'endpoint vLLM et le modèle dans Options vLLM.")

    environment = os.environ.copy()
    environment.update(
        {
            "VLLM_BASE_URL": base_url,
            "VLLM_API_KEY": api_key,
            "VLLM_MODEL": model,
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": api_key,
            "OPENAI_MODEL": model,
            "QWEN_MODEL": model,
        }
    )
    command = (
        [executable, "gateway", "run", "--force"]
        if harness_id == "openclaw"
        else [executable, "--auth-type", "openai"]
    )
    if Path(executable).suffix.lower() in {".bat", ".cmd"}:
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/k", subprocess.list2cmdline(command)]

    subprocess.Popen(
        command,
        cwd=str(Path.cwd()),
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def stop_harness(processes: list[dict[str, Any]]) -> int:
    if os.name != "nt":
        raise RuntimeError("Le pilotage des harnesses est actuellement disponible uniquement sous Windows.")
    process_ids = sorted({int(process.get("pid") or 0) for process in processes if int(process.get("pid") or 0) > 0})
    if not process_ids:
        return 0

    stopped = 0
    errors: list[str] = []
    for process_id in process_ids:
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            stopped += 1
        elif result.stderr.strip():
            errors.append(result.stderr.strip())
    if stopped == 0 and errors:
        raise RuntimeError(errors[0][:300])
    return stopped


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
        'padding:0.38rem 0.7rem;border-radius:999px;color:white;'
        f'background:{status["color"]};font-weight:650;font-size:0.92rem;">'
        '<span style="width:0.5rem;height:0.5rem;border-radius:50%;background:white;"></span>'
        f'{html.escape(status["label"])}</span>'
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
        .stApp {
            background: #0b0f13;
            font-size: 1.02rem;
        }
        [data-testid="stSidebar"] {
            background: #10151b;
            border-right: 1px solid #26313d;
        }
        [data-testid="stHeader"] { background: rgba(11, 15, 19, 0.88); }
        [data-testid="stCaptionContainer"] p {
            color: #a8b3be;
            font-size: 0.9rem;
            line-height: 1.45;
        }
        [data-testid="stMetric"] {
            background: #141a21;
            border: 1px solid #2a3541;
            border-radius: 6px;
            padding: 1rem 1.1rem;
            min-height: 118px;
        }
        [data-testid="stMetricLabel"] {
            color: #aab5c0;
            font-size: 0.9rem;
        }
        [data-testid="stMetricValue"] {
            color: #f3f6f8;
            font-size: 2rem;
        }
        [data-testid="stDataFrame"] {
            border: 1px solid #2a3541;
            border-radius: 6px;
            overflow: hidden;
        }
        .stButton > button, .stDownloadButton > button, .stLinkButton > a {
            border-radius: 6px;
            min-height: 2.75rem;
            font-size: 0.95rem;
            font-weight: 600;
        }
        [data-testid="stTextInput"] input {
            min-height: 2.75rem;
            font-size: 0.96rem;
        }
        [data-testid="stButtonGroup"] {
            margin: 0.2rem 0 1.25rem;
        }
        [data-testid="stButtonGroup"] button[data-variant="segmented_control"] {
            min-height: 2.9rem;
            font-size: 0.94rem;
            font-weight: 600;
            padding-inline: 0.85rem;
        }
        [data-testid="stButtonGroup"] button[data-variant="segmented_control"] p {
            font-size: 0.94rem;
        }
        .dashboard-kicker {
            color: #55d6be;
            font-size: 0.78rem;
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
            height: 112px;
            margin-bottom: 1rem;
            background: #f3f6f8;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0.75rem;
        }
        .harness-logo img {
            max-width: 100%;
            max-height: 82px;
            object-fit: contain;
        }
        .harness-config-path {
            color: #98a5b2;
            font-size: 0.9rem;
            line-height: 1.4;
            overflow-wrap: anywhere;
        }
        .harness-stat-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0;
            margin: 1rem 0 0.9rem;
            border-top: 1px solid #26313d;
            border-bottom: 1px solid #26313d;
        }
        .harness-stat {
            padding: 0.9rem 1rem;
            border-right: 1px solid #26313d;
        }
        .harness-stat:last-child { border-right: 0; }
        .harness-stat span, .harness-connection span {
            display: block;
            color: #98a5b2;
            font-size: 0.82rem;
            margin-bottom: 0.25rem;
        }
        .harness-stat strong {
            color: #f1f5f7;
            font-size: 1.18rem;
            font-weight: 650;
        }
        .harness-connection-grid {
            display: grid;
            grid-template-columns: minmax(180px, 0.8fr) minmax(280px, 2fr) minmax(160px, 0.7fr);
            gap: 1rem;
            margin-bottom: 0.85rem;
        }
        .harness-connection {
            min-width: 0;
        }
        .harness-connection strong {
            display: block;
            color: #f1f5f7;
            font-size: 0.98rem;
            line-height: 1.45;
            overflow-wrap: anywhere;
        }
        @media (max-width: 900px) {
            .harness-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .harness-stat:nth-child(2) { border-right: 0; }
            .harness-stat:nth-child(-n+2) { border-bottom: 1px solid #26313d; }
            .harness-connection-grid { grid-template-columns: 1fr; }
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
    pool_connections: list[dict[str, Any]],
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
        logo_col, summary_col = st.columns([1, 4.5])
        with logo_col:
            logo_uri = logo_data_uri(logo_filename)
            if logo_uri:
                st.markdown(
                    f'<div class="harness-logo"><img src="{logo_uri}" alt="{name}"></div>',
                    unsafe_allow_html=True,
                )
        with summary_col:
            title_col, status_col = st.columns([3.5, 1])
            with title_col:
                st.subheader(name)
                config_label = "Configuration détectée" if config.get("configured") else "Configuration absente"
                st.markdown(
                    f'<div class="harness-config-path">{config_label} : '
                    f'{html.escape(str(config.get("config_path") or ""))}</div>',
                    unsafe_allow_html=True,
                )
            with status_col:
                st.markdown(status_badge(overall), unsafe_allow_html=True)

        feedback = st.session_state.setdefault("harness_feedback", {}).pop(harness_id, None)
        if feedback:
            if feedback[0] == "success":
                st.success(feedback[1])
            else:
                st.error(feedback[1])

        action_col, options_col, spacer_col = st.columns([1, 1.2, 2.8])
        with action_col:
            if st.button(
                "Arrêter" if running else "Démarrer",
                icon=":material/stop:" if running else ":material/play_arrow:",
                key=f"harness_action_{harness_id}",
                type="primary" if not running else "secondary",
                width="stretch",
            ):
                try:
                    if running:
                        stopped = stop_harness(config.get("processes") or [])
                        message = f"Arrêt demandé pour {name} ({stopped} arbre(s) de processus)."
                    else:
                        start_harness(harness_id, config)
                        message = f"{name} a été lancé dans une nouvelle console Windows."
                    st.session_state["harness_feedback"][harness_id] = ("success", message)
                    time.sleep(0.7)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    st.session_state["harness_feedback"][harness_id] = ("error", str(exc))
                st.rerun()
        with options_col:
            options_key = f"show_harness_options_{harness_id}"
            options_open = bool(st.session_state.get(options_key))
            if st.button(
                "Masquer" if options_open else "Options vLLM",
                icon=":material/close:" if options_open else ":material/settings:",
                key=f"toggle_harness_options_{harness_id}",
                width="stretch",
            ):
                st.session_state[options_key] = not options_open
                st.rerun()
        with spacer_col:
            st.caption("La connexion temporaire est injectée au prochain lancement du harness.")

        stats = [
            ("Processus", str(process_metrics.get("count", 0))),
            ("En ligne depuis", duration_text(number_value(process_metrics.get("started_at")))),
            ("Mémoire locale", memory_text(int(process_metrics.get("memory_bytes") or 0))),
            ("Latence vLLM", f"{vllm_probe['latency_ms']} ms" if vllm_probe.get("latency_ms") is not None else "—"),
        ]
        stat_markup = "".join(
            '<div class="harness-stat">'
            f'<span>{html.escape(label)}</span><strong>{html.escape(value)}</strong>'
            "</div>"
            for label, value in stats
        )
        st.markdown(f'<div class="harness-stat-grid">{stat_markup}</div>', unsafe_allow_html=True)

        alignment_label = "Modèle aligné" if alignment is True else "Modèle différent" if alignment is False else "Non vérifiable"
        connection_markup = (
            '<div class="harness-connection-grid">'
            '<div class="harness-connection"><span>Modèle configuré</span>'
            f'<strong>{html.escape(str(config.get("model") or "Non renseigné"))}</strong></div>'
            '<div class="harness-connection"><span>Endpoint vLLM</span>'
            f'<strong>{html.escape(str(config.get("base_url") or "Non renseigné"))}</strong></div>'
            '<div class="harness-connection"><span>État vLLM</span>'
            f'<strong>{html.escape(str(vllm_probe.get("label") or "Inconnu"))} · {html.escape(alignment_label)}</strong></div>'
            "</div>"
        )
        st.markdown(connection_markup, unsafe_allow_html=True)

        options_key = f"show_harness_options_{harness_id}"
        if st.session_state.get(options_key):
            st.markdown("#### Connexion vLLM temporaire")
            st.caption("Ces valeurs restent dans la session Streamlit et ne sont pas écrites dans les fichiers locaux.")
            active_connections = [
                connection
                for connection in pool_connections
                if connection.get("enabled", True) and connection.get("id") and connection.get("baseUrl") and connection.get("model")
            ]
            if active_connections:
                connection_by_id = {str(connection["id"]): connection for connection in active_connections}
                st.markdown("##### Depuis le pool vLLM")
                selected_pool_id = st.selectbox(
                    "Connexion enregistrée",
                    list(connection_by_id),
                    format_func=lambda value: (
                        f"{connection_by_id[value].get('name') or value} "
                        f"- {connection_by_id[value].get('model') or 'modèle non renseigné'}"
                    ),
                    key=f"pool_connection_{harness_id}",
                    help="Utilise un endpoint et une clé déjà enregistrés dans le pool local.",
                )
                selected_connection = connection_by_id[selected_pool_id]
                key_env = str(selected_connection.get("apiKeyEnv") or "")
                pool_action_col, pool_restart_col = st.columns([1.45, 1])
                with pool_restart_col:
                    pool_restart_now = st.toggle(
                        "Redémarrer maintenant",
                        value=running,
                        key=f"restart_pool_harness_{harness_id}",
                    )
                with pool_action_col:
                    use_pool_connection = st.button(
                        "Connecter ce vLLM",
                        icon=":material/link:",
                        key=f"use_pool_connection_{harness_id}",
                        type="primary",
                        width="stretch",
                    )
                if use_pool_connection:
                    pool_override = pool_connection_override(harness_id, config, selected_connection)
                    if not pool_override["api_key"]:
                        st.error(f"La variable Windows {key_env or 'de clé'} est absente ou vide.")
                    elif not pool_override["base_url"] or not pool_override["model"]:
                        st.error("Cette connexion du pool doit contenir un endpoint et un modèle.")
                    else:
                        st.session_state.setdefault("harness_runtime_connections", {})[harness_id] = pool_override
                        st.session_state.setdefault("harness_runtime_connection_sources", {})[harness_id] = str(
                            selected_connection.get("name") or selected_pool_id
                        )
                        try:
                            if pool_restart_now:
                                if running:
                                    stop_harness(config.get("processes") or [])
                                    time.sleep(0.7)
                                start_harness(harness_id, effective_harness_connection(config, pool_override))
                            message = f"Connexion temporaire appliquée depuis le pool : {selected_connection.get('name') or selected_pool_id}"
                            if pool_restart_now:
                                message += ". Harness relancé"
                            st.session_state["harness_feedback"][harness_id] = ("success", message + ".")
                        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                            st.session_state["harness_feedback"][harness_id] = ("error", str(exc))
                        st.rerun()
                st.caption(f"Clé attendue : `{key_env}`. La valeur n'est jamais affichée.")
                st.divider()
                st.markdown("##### Saisie manuelle")
            else:
                st.info("Aucune connexion active dans le pool. Tu peux en ajouter plus bas, ou utiliser la saisie manuelle.")
            with st.form(f"harness_connection_form_{harness_id}", border=False):
                endpoint_value = st.text_input(
                    "Endpoint OpenAI-compatible",
                    value=str(config.get("base_url") or ""),
                    placeholder="https://<pod-id>-8000.proxy.runpod.net/v1",
                )
                key_value = st.text_input(
                    "Clé API vLLM",
                    type="password",
                    placeholder="Laisser vide pour conserver la clé locale actuelle",
                    help="La clé détectée n'est jamais préremplie dans le navigateur. Saisis-en une uniquement pour la remplacer temporairement.",
                )
                model_value = st.text_input(
                    "Modèle",
                    value=str(config.get("model") or ""),
                    disabled=harness_id == "openclaw",
                    help=(
                        "Le modèle OpenClaw reste défini dans openclaw.json ; l'endpoint et la clé sont injectés à la volée."
                        if harness_id == "openclaw"
                        else "Identifiant du modèle exposé par vLLM."
                    ),
                )
                restart_now = st.toggle(
                    "Redémarrer maintenant avec cette connexion",
                    value=running,
                    key=f"restart_harness_{harness_id}",
                )
                save_col, reset_col = st.columns(2)
                with save_col:
                    save_connection = st.form_submit_button(
                        "Appliquer", icon=":material/link:", type="primary", width="stretch"
                    )
                with reset_col:
                    reset_connection = st.form_submit_button(
                        "Réinitialiser", icon=":material/restart_alt:", width="stretch"
                    )

            if save_connection:
                endpoint_value = endpoint_value.strip().rstrip("/")
                model_value = model_value.strip()
                if not endpoint_value.startswith(("http://", "https://")):
                    st.error("L'endpoint doit commencer par http:// ou https://.")
                elif not model_value:
                    st.error("Le modèle ne peut pas être vide.")
                else:
                    override = {
                        "base_url": endpoint_value,
                        "api_key": key_value.strip() or str(config.get("api_key") or ""),
                        "model": model_value,
                    }
                    st.session_state.setdefault("harness_runtime_connections", {})[harness_id] = override
                    st.session_state.setdefault("harness_runtime_connection_sources", {}).pop(harness_id, None)
                    try:
                        if restart_now:
                            if running:
                                stop_harness(config.get("processes") or [])
                                time.sleep(0.7)
                            start_harness(harness_id, effective_harness_connection(config, override))
                        message = "Connexion temporaire enregistrée"
                        if restart_now:
                            message += " et harness relancé"
                        st.session_state["harness_feedback"][harness_id] = ("success", message + ".")
                    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                        st.session_state["harness_feedback"][harness_id] = ("error", str(exc))
                    st.rerun()
            elif reset_connection:
                st.session_state.setdefault("harness_runtime_connections", {}).pop(harness_id, None)
                st.session_state.setdefault("harness_runtime_connection_sources", {}).pop(harness_id, None)
                st.session_state["harness_feedback"][harness_id] = (
                    "success",
                    "Connexion temporaire supprimée. La configuration locale redevient la référence.",
                )
                st.rerun()

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
def render_live_harnesses(pool_connections: list[dict[str, Any]]) -> None:
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
    runtime_rows: list[tuple[str, str, str, dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    for harness_id, name, logo, config in harnesses:
        override = st.session_state.get("harness_runtime_connections", {}).get(harness_id)
        config = effective_harness_connection(config, override)
        process_metrics = harness_process_metrics(config.get("processes") or [])
        probe_key = (str(config.get("base_url") or ""), str(config.get("api_key") or ""))
        if probe_key not in probe_cache:
            probe_cache[probe_key] = probe_vllm(*probe_key)
        runtime_rows.append((harness_id, name, logo, config, process_metrics, probe_cache[probe_key], pool_connections))

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


def render_harnesses(base_url: str, api_key: str) -> None:
    st.subheader("Harnesses")
    st.caption("État local, durée d'exécution et connectivité réelle vers vLLM.")
    pool_store = load_connection_store(VLLM_CONNECTIONS_PATH)
    pool_connections = [connection for connection in pool_store.get("connections", []) if isinstance(connection, dict)]
    render_live_harnesses(pool_connections)
    render_vllm_connection_pool(base_url, api_key)


def sync_openclaw_agent_pool(connections: list[dict[str, Any]]) -> None:
    current_config = load_json_object(OPENCLAW_CONFIG_PATH)
    current_agents = nested_value(current_config, "agents.list", [])
    current_agents = current_agents if isinstance(current_agents, list) else []
    managed_agent_ids = {f"agent-{connection_slug(str(connection['id']))}" for connection in connections}
    preserved_agents = [
        agent
        for agent in current_agents
        if not isinstance(agent, dict) or str(agent.get("id") or "") not in managed_agent_ids
    ]
    providers: dict[str, Any] = {}
    visible_models: dict[str, Any] = {}
    generated_agents: list[dict[str, Any]] = []
    for connection in connections:
        provider_id = openclaw_provider_id(connection)
        model_ref = openclaw_model_ref(connection)
        providers[provider_id] = openclaw_provider_config(connection)
        visible_models[model_ref] = {}
        generated_agents.append(
            {
                "id": f"agent-{connection_slug(str(connection['id']))}",
                "name": connection["name"],
                "model": model_ref,
            }
        )
    patch_openclaw_config(
        {
            "models": {"providers": providers},
            "agents": {
                "defaults": {"models": visible_models},
                "list": [*preserved_agents, *generated_agents],
            },
        }
    )


def render_vllm_connection_pool(base_url: str, api_key: str) -> None:
    st.divider()
    st.subheader("Pool vLLM multi-agent")
    st.caption("Chaque connexion représente un pod/GPU. Les clés restent dans des variables d'environnement Windows, jamais dans le catalogue local.")
    store = load_connection_store(VLLM_CONNECTIONS_PATH)
    connections = [connection for connection in store.get("connections", []) if isinstance(connection, dict)]
    connection_by_id = {str(connection.get("id")): connection for connection in connections if connection.get("id")}

    if connections:
        rows = []
        for connection in connections:
            api_key_present = bool(os.getenv(str(connection.get("apiKeyEnv") or "")))
            probe = probe_vllm(str(connection.get("baseUrl") or ""), os.getenv(str(connection.get("apiKeyEnv") or ""), ""))
            rows.append(
                {
                    "Connexion": connection.get("name") or connection.get("id"),
                    "GPU / Pod": connection.get("podId") or "Endpoint externe",
                    "Modèle": connection.get("model") or "",
                    "Agent OpenClaw": f"agent-{connection_slug(str(connection.get('id') or 'vllm'))}",
                    "Clé env": connection.get("apiKeyEnv") or "",
                    "Clé présente": "Oui" if api_key_present else "Non",
                    "État": probe.get("label") or "Inconnu",
                    "Actif": bool(connection.get("enabled", True)),
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
        selected_connection_id = st.selectbox(
            "Connexion vLLM",
            list(connection_by_id),
            format_func=lambda value: str(connection_by_id[value].get("name") or value),
        )
    else:
        selected_connection_id = ""
        st.info("Aucune connexion vLLM persistante. Ajoute un pod ou un endpoint pour former le pool.")

    add_col, remove_col, sync_col = st.columns([1, 1, 2.5])
    with add_col:
        if st.button("Ajouter", icon=":material/add:", key="add_vllm_connection", help="Ajouter un endpoint vLLM au pool"):
            st.session_state["show_vllm_connection_editor"] = True
    with remove_col:
        if st.button(
            "Retirer",
            icon=":material/remove:",
            key="remove_vllm_connection",
            help="Retirer la connexion du catalogue local",
            disabled=not selected_connection_id,
        ):
            remove_connection(store, selected_connection_id)
            save_connection_store(VLLM_CONNECTIONS_PATH, store)
            st.success("Connexion retirée du catalogue local. Les profils OpenClaw déjà générés sont conservés jusqu'à leur prochaine synchronisation.")
            st.rerun()
    with sync_col:
        enabled_connections = [connection for connection in connections if connection.get("enabled", True)]
        if st.button(
            "Générer les profils OpenClaw",
            icon=":material/account_tree:",
            key="sync_openclaw_agent_pool",
            type="primary",
            disabled=not enabled_connections,
            help="Crée ou met à jour un agent OpenClaw par connexion active",
        ):
            missing_keys = [
                str(connection.get("apiKeyEnv") or "")
                for connection in enabled_connections
                if not os.getenv(str(connection.get("apiKeyEnv") or ""))
            ]
            if missing_keys:
                st.error("Variables de clé manquantes : " + ", ".join(missing_keys))
            elif not shutil.which("openclaw"):
                st.error("OpenClaw n'est pas disponible dans le PATH.")
            else:
                try:
                    sync_openclaw_agent_pool(enabled_connections)
                    st.success("Profils OpenClaw générés. Redémarre le Gateway si ses nouveaux agents n'apparaissent pas immédiatement.")
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    st.error(f"Synchronisation impossible : {exc}")

    if not st.session_state.get("show_vllm_connection_editor"):
        return

    st.markdown("#### Ajouter une connexion")
    source = st.segmented_control(
        "Source de la connexion",
        ["Pod RunPod", "Endpoint manuel"],
        default="Pod RunPod",
        selection_mode="single",
        key="vllm_connection_source",
    ) or "Pod RunPod"
    available_pods: dict[str, dict[str, Any]] = {}
    if source == "Pod RunPod" and api_key:
        try:
            available_pods = {
                f"{pod.get('name', 'sans nom')} · {pod.get('id', '')}": pod
                for pod in response_items(request_runpod("GET", base_url, api_key, "/pods"))
                if pod.get("id")
            }
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            st.warning(f"Pods RunPod indisponibles : {exc}")
    if source == "Pod RunPod" and available_pods:
        pod_label = st.selectbox("Pod source", list(available_pods), key="vllm_connection_pod")
        selected_pod = available_pods[pod_label]
        default_name = str(selected_pod.get("name") or "vllm")
        default_pod_id = str(selected_pod.get("id") or "")
        default_url = f"{proxy_url(default_pod_id)}/v1"
        session_credentials = st.session_state.get("vllm_credentials", {}).get(default_pod_id, {})
        default_model = str(session_credentials.get("VLLM_MODEL") or "")
    else:
        default_name = ""
        default_pod_id = ""
        default_url = ""
        default_model = ""
        if source == "Pod RunPod":
            st.caption("Saisis une clé RunPod dans la barre latérale ou passe à Endpoint manuel.")

    default_env_name = f"VLLM_{connection_slug(default_name or 'connection').upper().replace('-', '_')}_API_KEY"
    with st.form("vllm_connection_form", border=False):
        connection_name = st.text_input("Nom de la connexion", value=default_name, placeholder="qwen-27b-a40")
        endpoint = st.text_input("Endpoint OpenAI-compatible", value=default_url, placeholder="https://<pod-id>-8000.proxy.runpod.net/v1")
        model = st.text_input("Modèle exposé par vLLM", value=default_model, placeholder="Qwen/Qwen3-27B")
        pod_id = st.text_input("ID Pod RunPod", value=default_pod_id)
        env_name = st.text_input("Variable d'environnement de la clé", value=default_env_name)
        secret_value = st.text_input(
            "Clé vLLM à enregistrer",
            type="password",
            help="Optionnelle si cette variable existe déjà. Elle est enregistrée dans les variables utilisateur Windows, jamais dans le catalogue.",
        )
        context_col, token_col = st.columns(2)
        with context_col:
            context_window = st.number_input("Fenêtre de contexte", min_value=1024, value=131072, step=1024)
        with token_col:
            max_tokens = st.number_input("Tokens générés maximum", min_value=128, value=4096, step=128)
        enabled = st.toggle("Connexion active", value=True)
        save_connection = st.form_submit_button("Enregistrer la connexion", type="primary")
    if save_connection:
        if not connection_name.strip() or not endpoint.startswith(("http://", "https://")) or not model.strip():
            st.error("Le nom, un endpoint HTTP(S) et le modèle sont obligatoires.")
        elif not env_name.replace("_", "").isalnum() or not env_name.upper() == env_name:
            st.error("La variable de clé doit contenir uniquement des majuscules, chiffres et underscores.")
        else:
            try:
                if secret_value.strip():
                    save_user_environment_variable(env_name, secret_value.strip())
                connection = upsert_connection(
                    store,
                    {
                        "id": connection_name,
                        "name": connection_name,
                        "podId": pod_id,
                        "baseUrl": endpoint,
                        "model": model,
                        "apiKeyEnv": env_name,
                        "contextWindow": int(context_window),
                        "maxTokens": int(max_tokens),
                        "enabled": enabled,
                    },
                )
                save_connection_store(VLLM_CONNECTIONS_PATH, store)
                st.session_state["show_vllm_connection_editor"] = False
                st.success(f"Connexion {connection['name']} enregistrée.")
                st.rerun()
            except OSError as exc:
                st.error(f"Enregistrement impossible : {exc}")


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


def render_lifecycle_policies(pods: list[dict[str, Any]]) -> None:
    st.subheader("Protection contre la surfacturation")
    st.caption(
        "Les échéances sont enregistrées localement. Une échéance dépassée est rejouée au prochain démarrage du dashboard."
    )
    store = load_policy_store(LIFECYCLE_POLICIES_PATH)
    policies = store.get("policies", {}) if isinstance(store.get("policies"), dict) else {}
    pods_by_id = {str(pod.get("id")): pod for pod in pods if pod.get("id")}
    if policies:
        rows = []
        for pod_id, policy in policies.items():
            if not isinstance(policy, dict):
                continue
            pod = pods_by_id.get(str(pod_id), {})
            rows.append(
                {
                    "Pod": policy.get("podName") or pod.get("name") or pod_id,
                    "ID": pod_id,
                    "État RunPod": pod_state(pod).upper() if pod else "Non trouvé",
                    "Pause prévue": local_time_text(policy.get("pauseAt")),
                    "Destruction prévue": local_time_text(policy.get("deleteAt")),
                    "Pause exécutée": local_time_text(policy.get("pauseCompletedAt")),
                    "Destruction exécutée": local_time_text(policy.get("deleteCompletedAt")),
                    "Dernière erreur": policy.get("lastError") or "",
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)

    pod_options = {
        f"{pod.get('name', 'sans nom')} · {pod.get('id', '')}": pod
        for pod in pods
        if pod.get("id") and pod_state(pod).upper() not in {"TERMINATED", "DELETED"}
    }
    active_policies = [
        pod_id
        for pod_id, policy in policies.items()
        if isinstance(policy, dict) and not policy.get("deleteCompletedAt")
    ]
    if not pod_options:
        st.info("Aucun pod disponible pour programmer une protection.")
        if active_policies:
            cancel_pod = st.selectbox("Protection à annuler", active_policies, key="cancel_lifecycle_policy")
            if st.button("Annuler la protection", icon=":material/event_busy:"):
                clear_policy(store, cancel_pod)
                save_policy_store(LIFECYCLE_POLICIES_PATH, store)
                st.success("Protection annulée.")
                st.rerun()
        return

    with st.form("pod_lifecycle_form", border=False):
        selected_label = st.selectbox("Machine à protéger", list(pod_options))
        pause_enabled = st.toggle("Mettre en pause après un délai", value=True)
        pause_minutes = st.number_input(
            "Délai avant mise en pause (minutes)",
            min_value=1,
            value=120,
            step=5,
            disabled=not pause_enabled,
        )
        delete_enabled = st.toggle("Détruire après un délai", value=False)
        delete_minutes = st.number_input(
            "Délai avant destruction (minutes)",
            min_value=1,
            value=240,
            step=5,
            disabled=not delete_enabled,
        )
        delete_confirmed = st.checkbox(
            "Je confirme que la destruction supprime les données hors Network Volume.",
            disabled=not delete_enabled,
        )
        save_policy = st.form_submit_button(
            "Programmer la protection", icon=":material/schedule:", type="primary"
        )
    if save_policy:
        if not pause_enabled and not delete_enabled:
            st.error("Choisis au moins une action automatique.")
            return
        if delete_enabled and not delete_confirmed:
            st.error("La confirmation est obligatoire avant de programmer une destruction.")
            return
        if pause_enabled and delete_enabled and delete_minutes <= pause_minutes:
            st.error("Le délai de destruction doit être supérieur au délai de pause.")
            return
        selected_pod = pod_options[selected_label]
        policy = schedule_policy(
            store,
            str(selected_pod["id"]),
            str(selected_pod.get("name") or selected_pod["id"]),
            int(pause_minutes) if pause_enabled else None,
            int(delete_minutes) if delete_enabled else None,
        )
        save_policy_store(LIFECYCLE_POLICIES_PATH, store)
        summary = []
        if policy.get("pauseAt"):
            summary.append(f"pause {local_time_text(policy['pauseAt'])}")
        if policy.get("deleteAt"):
            summary.append(f"destruction {local_time_text(policy['deleteAt'])}")
        st.success("Protection programmée : " + " · ".join(summary) + ".")
        st.rerun()

    if active_policies:
        cancel_pod = st.selectbox("Protection à annuler", active_policies, key="cancel_lifecycle_policy")
        if st.button("Annuler la protection", icon=":material/event_busy:"):
            clear_policy(store, cancel_pod)
            save_policy_store(LIFECYCLE_POLICIES_PATH, store)
            st.success("Protection annulée.")
            st.rerun()


@st.fragment(run_every=30)
def run_lifecycle_guard(base_url: str, api_key: str) -> None:
    for event in reconcile_lifecycle_policies(base_url, api_key):
        if event["kind"] == "success":
            st.toast(event["message"], icon=":material/schedule:")
        else:
            st.warning(event["message"])


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
        all_pods = response_items(request_runpod("GET", base_url, api_key, "/pods"))
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.error(f"Impossible de charger les machines : {exc}")
        all_pods = []

    lifecycle_events = reconcile_lifecycle_policies(base_url, api_key, all_pods)
    for event in lifecycle_events:
        if event["kind"] == "success":
            st.success(event["message"])
        else:
            st.error(event["message"])

    pods = list(all_pods)

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
            action_labels = {"Mettre en pause": "stop", "Détruire": "delete"}
            action_label = st.selectbox("Action", list(action_labels), label_visibility="collapsed")
        with confirm_col:
            confirmed = st.checkbox("Je confirme l'action sur cette machine")
        if st.button("Exécuter l'action", type="secondary", disabled=not confirmed):
            try:
                action = action_labels[action_label]
                if action == "delete":
                    request_runpod("DELETE", base_url, api_key, f"/pods/{selected_id}")
                else:
                    request_runpod("POST", base_url, api_key, f"/pods/{selected_id}/stop")
                st.success(f"Action {action_label.lower()} envoyée pour {selected_id}.")
                st.rerun()
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                st.error(f"Action impossible : {exc}")
    else:
        st.info("Aucune machine ne correspond au filtre sélectionné.")

    st.divider()
    render_lifecycle_policies(all_pods)

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


def typed_setting_widget(spec: dict[str, Any], current: Any, key: str) -> Any:
    value_type = str(spec.get("type") or "string")
    choices = list(spec.get("choices") or [])
    label = str(spec.get("path") or spec.get("Chemin") or "Valeur")
    description = str(spec.get("description") or spec.get("Description") or "")
    if value_type == "boolean":
        return st.toggle(label, value=bool(current) if isinstance(current, bool) else False, help=description, key=key)
    if value_type == "enum" and choices:
        options = list(choices)
        if current not in options and current is not None:
            options.insert(0, current)
        index = options.index(current) if current in options else 0
        return st.selectbox(label, options, index=index, help=description, key=key)
    if value_type == "integer":
        return int(
            st.number_input(
                label,
                value=int(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else 0,
                step=1,
                help=description,
                key=key,
            )
        )
    if value_type == "number":
        return float(
            st.number_input(
                label,
                value=float(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else 0.0,
                step=0.05,
                format="%.3f",
                help=description,
                key=key,
            )
        )
    if value_type in {"array", "object"}:
        initial = current if isinstance(current, (list, dict)) else ([] if value_type == "array" else {})
        return st.text_area(
            label,
            value=json.dumps(initial, indent=2, ensure_ascii=True),
            height=180,
            help=description,
            key=key,
        )
    return st.text_input(label, value="" if current is None else str(current), help=description, key=key)


def parsed_setting_value(raw_value: Any, value_type: str) -> Any:
    if value_type not in {"array", "object"}:
        return raw_value
    value = json.loads(str(raw_value))
    expected = list if value_type == "array" else dict
    if not isinstance(value, expected):
        raise ValueError(f"La valeur doit etre de type {value_type}.")
    return value


def render_advanced_editor(
    title: str,
    rows: list[dict[str, Any]],
    current_config: dict[str, Any],
    save_value: Any,
    delete_value: Any,
    key_prefix: str,
) -> None:
    st.subheader(title)
    search = st.text_input(
        "Rechercher un réglage",
        placeholder="model, thinking, plugin, tools...",
        key=f"{key_prefix}_search",
    ).strip().lower()
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        path = str(row.get("Chemin") or row.get("path") or "")
        if not path or "*" in path or is_sensitive_config_path(path):
            continue
        normalized = {
            "Chemin": path,
            "Type": str(row.get("Type") or row.get("type") or "string"),
            "Valeurs": str(row.get("Valeurs") or ", ".join(str(value) for value in row.get("choices", []))),
            "Defaut": row.get("Defaut", ""),
            "Description": str(row.get("Description") or row.get("description") or ""),
        }
        haystack = " ".join(str(value).lower() for value in normalized.values())
        if not search or search in haystack:
            normalized_rows.append(normalized)

    st.caption(f"{len(normalized_rows)} réglage(s) affiché(s). Les chemins sensibles sont masqués.")
    if normalized_rows:
        st.dataframe(normalized_rows[:500], width="stretch", hide_index=True, height=280)
    else:
        st.info("Aucun réglage ne correspond à cette recherche.")
        return

    selectable = normalized_rows[:500]
    selected_path = st.selectbox(
        "Réglage à modifier",
        [row["Chemin"] for row in selectable],
        key=f"{key_prefix}_path",
    )
    selected = next(row for row in selectable if row["Chemin"] == selected_path)
    choices = [value.strip() for value in selected["Valeurs"].split(",") if value.strip()]
    spec = {
        "path": selected_path,
        "type": selected["Type"],
        "choices": choices,
        "description": selected["Description"],
    }
    current = nested_value(current_config, selected_path)
    with st.form(f"{key_prefix}_editor_form", border=False):
        edited = typed_setting_widget(spec, current, f"{key_prefix}_value_{selected_path}")
        save_col, inherit_col = st.columns(2)
        with save_col:
            save_clicked = st.form_submit_button(
                "Enregistrer", icon=":material/save:", type="primary", width="stretch"
            )
        with inherit_col:
            delete_clicked = st.form_submit_button(
                "Retirer la surcharge", icon=":material/undo:", width="stretch"
            )
    try:
        if save_clicked:
            save_value(selected_path, parsed_setting_value(edited, selected["Type"]))
            st.success(f"{selected_path} enregistré.")
            st.rerun()
        if delete_clicked:
            delete_value(selected_path)
            st.success(f"{selected_path} revient à sa valeur héritée.")
            st.rerun()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        st.error(f"Modification impossible : {exc}")


def render_openclaw_model_settings(
    config: dict[str, Any],
    models: list[dict[str, Any]],
    model_status: dict[str, Any],
) -> None:
    defaults = nested_value(config, "agents.defaults", {})
    current_model = str(nested_value(config, "agents.defaults.model.primary", ""))
    model_keys = [str(item.get("key") or item.get("id") or "") for item in models]
    model_keys = [key for key in dict.fromkeys([current_model, *model_keys]) if key]
    selected_row = next((item for item in models if str(item.get("key") or item.get("id")) == current_model), {})
    metric_cols = st.columns(4)
    metric_cols[0].metric("Modèle actif", current_model or "Non défini")
    metric_cols[1].metric("Contexte", f"{int(number_value(selected_row.get('contextWindow') or selected_row.get('contextTokens'))):,}" if selected_row else "Inconnu")
    metric_cols[2].metric("Disponible", "Oui" if selected_row.get("available") else "Non vérifié")
    metric_cols[3].metric("Fallbacks", len(model_status.get("fallbacks") or []))

    current_model_params = nested_value(defaults, f"models.{current_model}.params", {})
    if not isinstance(current_model_params, dict):
        current_model_params = {}
    global_params = defaults.get("params", {}) if isinstance(defaults, dict) else {}
    max_tokens = int(number_value(current_model_params.get("maxTokens") or global_params.get("maxTokens") or 2048))
    current_thinking = str(defaults.get("thinkingDefault") or "inherit") if isinstance(defaults, dict) else "inherit"
    fast_supported = current_model.startswith(("openai/", "anthropic/"))

    st.subheader("Modèle et génération")
    with st.form("openclaw_model_form", border=False):
        selected_model = st.selectbox(
            "Modèle principal",
            model_keys or [current_model or "vllm/model"],
            index=model_keys.index(current_model) if current_model in model_keys else 0,
        )
        thinking = st.selectbox(
            "Mode thinking",
            ["inherit", "off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max", "ultra", "on"],
            index=["inherit", "off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max", "ultra", "on"].index(current_thinking)
            if current_thinking in ["inherit", "off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max", "ultra", "on"]
            else 0,
            help="Avec les modèles Qwen servis par vLLM, le comportement réellement supporté est généralement binaire : off ou on.",
        )
        max_tokens_value = st.number_input(
            "Tokens générés maximum",
            min_value=128,
            max_value=1_000_000,
            value=max(128, max_tokens),
            step=128,
        )
        temperature_override = st.toggle(
            "Surcharger la température",
            value=isinstance(current_model_params.get("temperature"), (int, float)),
        )
        temperature = st.slider(
            "Température",
            min_value=0.0,
            max_value=2.0,
            value=float(current_model_params.get("temperature", 0.7)),
            step=0.05,
            disabled=not temperature_override,
        )
        if fast_supported:
            fast_mode = st.selectbox(
                "Fast mode",
                ["inherit", "off", "on", "auto"],
                index=["inherit", "off", "on", "auto"].index(str(current_model_params.get("fastMode") or "inherit"))
                if str(current_model_params.get("fastMode") or "inherit") in ["inherit", "off", "on", "auto"]
                else 0,
            )
            fast_auto_seconds = st.number_input(
                "Activation automatique après (secondes)",
                min_value=1,
                value=max(1, int(number_value(current_model_params.get("fastAutoOnSeconds") or 30))),
                disabled=fast_mode != "auto",
            )
        else:
            st.info("Fast mode n'est pas exposé pour ce modèle vLLM. Il dépend du fournisseur et du modèle OpenClaw sélectionné.")
            fast_mode = "inherit"
            fast_auto_seconds = 30
        save_model = st.form_submit_button(
            "Enregistrer le modèle", icon=":material/save:", type="primary"
        )

    if save_model:
        params_patch: dict[str, Any] = {
            "maxTokens": int(max_tokens_value),
            "temperature": float(temperature) if temperature_override else None,
            "fastMode": None if fast_mode == "inherit" else fast_mode,
            "fastAutoOnSeconds": int(fast_auto_seconds) if fast_mode == "auto" else None,
        }
        patch = {
            "agents": {
                "defaults": {
                    "model": {"primary": selected_model},
                    "thinkingDefault": None if thinking == "inherit" else thinking,
                    "models": {selected_model: {"params": params_patch}},
                }
            }
        }
        try:
            patch_openclaw_config(patch)
            st.success("Configuration du modèle validée par OpenClaw.")
            st.rerun()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            st.error(f"Enregistrement impossible : {exc}")


def render_openclaw_runtime_settings(config: dict[str, Any]) -> None:
    defaults = nested_value(config, "agents.defaults", {})
    tools = config.get("tools", {}) if isinstance(config.get("tools"), dict) else {}
    compaction = defaults.get("compaction", {}) if isinstance(defaults, dict) and isinstance(defaults.get("compaction"), dict) else {}
    st.subheader("Exécution et contexte")
    with st.form("openclaw_runtime_form", border=False):
        first_col, second_col = st.columns(2)
        with first_col:
            tools_profile = st.selectbox(
                "Profil d'outils",
                ["minimal", "coding", "messaging", "full"],
                index=["minimal", "coding", "messaging", "full"].index(str(tools.get("profile") or "minimal"))
                if str(tools.get("profile") or "minimal") in ["minimal", "coding", "messaging", "full"]
                else 0,
            )
            max_concurrent = st.number_input(
                "Agents simultanés maximum",
                min_value=1,
                max_value=128,
                value=max(1, int(number_value(defaults.get("maxConcurrent") or 1))),
            )
            elevated = st.selectbox(
                "Mode elevated par défaut",
                ["off", "on", "ask", "full"],
                index=["off", "on", "ask", "full"].index(str(defaults.get("elevatedDefault") or "off"))
                if str(defaults.get("elevatedDefault") or "off") in ["off", "on", "ask", "full"]
                else 0,
            )
            context_injection = st.selectbox(
                "Injection du contexte",
                ["always", "continuation-skip", "never"],
                index=["always", "continuation-skip", "never"].index(str(defaults.get("contextInjection") or "always"))
                if str(defaults.get("contextInjection") or "always") in ["always", "continuation-skip", "never"]
                else 0,
            )
        with second_col:
            bootstrap_max = st.number_input(
                "Caractères maximum par fichier bootstrap",
                min_value=0,
                value=max(0, int(number_value(defaults.get("bootstrapMaxChars") or 20_000))),
                step=1000,
            )
            bootstrap_total = st.number_input(
                "Caractères bootstrap maximum au total",
                min_value=0,
                value=max(0, int(number_value(defaults.get("bootstrapTotalMaxChars") or 60_000))),
                step=1000,
            )
            reserve_tokens = st.number_input(
                "Tokens réservés avant compaction",
                min_value=0,
                value=max(0, int(number_value(compaction.get("reserveTokens") or 2048))),
                step=256,
            )
            keep_recent = st.number_input(
                "Tokens récents à conserver",
                min_value=0,
                value=max(0, int(number_value(compaction.get("keepRecentTokens") or 0))),
                step=256,
            )
            truncate_after = st.toggle(
                "Tronquer après compaction",
                value=bool(compaction.get("truncateAfterCompaction", False)),
            )
        save_runtime = st.form_submit_button(
            "Enregistrer le harness", icon=":material/save:", type="primary"
        )
    if save_runtime:
        patch = {
            "tools": {"profile": tools_profile},
            "agents": {
                "defaults": {
                    "maxConcurrent": int(max_concurrent),
                    "elevatedDefault": elevated,
                    "contextInjection": context_injection,
                    "bootstrapMaxChars": int(bootstrap_max),
                    "bootstrapTotalMaxChars": int(bootstrap_total),
                    "compaction": {
                        "reserveTokens": int(reserve_tokens),
                        "keepRecentTokens": int(keep_recent),
                        "truncateAfterCompaction": bool(truncate_after),
                    },
                }
            },
        }
        try:
            patch_openclaw_config(patch)
            st.success("Configuration d'exécution validée par OpenClaw.")
            st.rerun()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            st.error(f"Enregistrement impossible : {exc}")


def render_openclaw_extensions(
    config: dict[str, Any],
    plugins: list[dict[str, Any]],
    plugin_error: str,
    skills: list[dict[str, Any]],
    skill_error: str,
) -> None:
    st.subheader("Plugins")
    if plugin_error:
        st.warning(f"Inventaire des plugins indisponible : {plugin_error}")
    elif plugins:
        plugin_rows = [
            {
                "Plugin": plugin_id(plugin),
                "Actif": plugin_enabled(plugin),
                "État": plugin.get("status") or plugin.get("state") or "",
                "Source": plugin.get("source") or plugin.get("origin") or "",
            }
            for plugin in plugins
            if plugin_id(plugin)
        ]
        st.dataframe(plugin_rows, width="stretch", hide_index=True, height=260)
        selected_plugin = st.selectbox("Plugin à administrer", [row["Plugin"] for row in plugin_rows])
        selected_plugin_data = next(plugin for plugin in plugins if plugin_id(plugin) == selected_plugin)
        desired_plugin_state = st.toggle(
            "Plugin activé",
            value=plugin_enabled(selected_plugin_data),
            key=f"plugin_enabled_{selected_plugin}",
        )
        if st.button("Appliquer au plugin", icon=":material/power_settings_new:"):
            try:
                command = "enable" if desired_plugin_state else "disable"
                run_cli("openclaw", ["plugins", command, selected_plugin], timeout=60)
                load_openclaw_plugins.clear()
                st.success(f"Plugin {selected_plugin} {'activé' if desired_plugin_state else 'désactivé'}.")
                st.rerun()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                st.error(f"Modification impossible : {exc}")
    else:
        st.info("Aucun plugin OpenClaw détecté.")

    st.divider()
    st.subheader("Skills visibles par le modèle")
    if skill_error:
        st.warning(f"Inventaire des skills indisponible : {skill_error}")
    else:
        if skills:
            st.dataframe(
                [
                    {
                        "Skill": skill.get("name") or skill.get("id") or "",
                        "Éligible": bool(skill.get("eligible")),
                        "Visible du modèle": bool(skill.get("modelVisible")),
                        "Désactivé": bool(skill.get("disabled")),
                        "Source": skill.get("source") or "",
                    }
                    for skill in skills
                ],
                width="stretch",
                hide_index=True,
                height=260,
            )
        eligible_skill_names = [
            str(skill.get("name") or skill.get("id") or "")
            for skill in skills
            if skill.get("eligible")
        ]
        eligible_skill_names = [name for name in dict.fromkeys(eligible_skill_names) if name]
        configured_skills = nested_value(config, "agents.defaults.skills", [])
        configured_skills = configured_skills if isinstance(configured_skills, list) else []
        skill_options = list(dict.fromkeys([*eligible_skill_names, *configured_skills]))
        selected_skills = st.multiselect(
            "Skills autorisés",
            skill_options,
            default=[name for name in configured_skills if name in skill_options],
            help="Seuls les skills éligibles et ceux déjà configurés sont proposés. Le tableau conserve l'inventaire complet.",
        )
        prompt_limit = st.number_input(
            "Budget maximal des descriptions de skills",
            min_value=0,
            value=max(0, int(number_value(nested_value(config, "skills.limits.maxSkillsPromptChars", 0)))),
            step=1000,
        )
        if st.button("Enregistrer les skills", icon=":material/save:"):
            try:
                patch_openclaw_config(
                    {
                        "agents": {"defaults": {"skills": selected_skills}},
                        "skills": {"limits": {"maxSkillsPromptChars": int(prompt_limit)}},
                    }
                )
                st.success("Visibilité des skills enregistrée.")
                st.rerun()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                st.error(f"Modification impossible : {exc}")

    st.divider()
    st.subheader("Serveurs MCP")
    servers = nested_value(config, "mcp.servers", {})
    servers = servers if isinstance(servers, dict) else {}
    if servers:
        st.dataframe(
            [
                {
                    "Serveur": server_id,
                    "Type": "HTTP" if isinstance(server, dict) and server.get("url") else "stdio",
                    "Actif": bool(server.get("enabled", True)) if isinstance(server, dict) else False,
                    "Cible": server.get("url") or server.get("command") or "" if isinstance(server, dict) else "",
                }
                for server_id, server in servers.items()
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("Aucun serveur MCP géré par OpenClaw.")
    with st.form("openclaw_mcp_form", border=False):
        server_id = st.text_input("Identifiant du serveur MCP", placeholder="ida, filesystem, github...")
        transport = st.segmented_control("Transport", ["stdio", "HTTP"], default="stdio") or "stdio"
        enabled = st.toggle("Serveur activé", value=True)
        if transport == "stdio":
            command = st.text_input("Commande", placeholder="npx")
            arguments = st.text_input("Arguments", placeholder="-y package-name")
            url = ""
        else:
            url = st.text_input("URL", placeholder="http://127.0.0.1:3000/mcp")
            command = ""
            arguments = ""
        save_mcp = st.form_submit_button("Ajouter ou mettre à jour", type="primary")
    if save_mcp:
        if not server_id.strip() or (transport == "stdio" and not command.strip()) or (transport == "HTTP" and not url.strip()):
            st.error("L'identifiant et la cible du serveur sont obligatoires.")
        else:
            server_value = (
                {"command": command.strip(), "args": arguments.split(), "enabled": enabled}
                if transport == "stdio"
                else {"url": url.strip(), "enabled": enabled}
            )
            try:
                patch_openclaw_config({"mcp": {"servers": {server_id.strip(): server_value}}})
                st.success(f"Serveur MCP {server_id.strip()} enregistré.")
                st.rerun()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                st.error(f"Modification impossible : {exc}")
    if servers:
        remove_server = st.selectbox("Serveur à retirer", list(servers), key="mcp_remove_server")
        if st.button("Retirer le serveur", icon=":material/delete:"):
            try:
                patch_openclaw_config({"mcp": {"servers": {remove_server: None}}})
                st.success(f"Serveur MCP {remove_server} retiré.")
                st.rerun()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                st.error(f"Suppression impossible : {exc}")


def render_qwen_settings(config: dict[str, Any]) -> None:
    installed = bool(shutil.which("qwen"))
    status_cols = st.columns(3)
    status_cols[0].metric("CLI", "Installée" if installed else "Absente")
    status_cols[1].metric("Configuration", "Présente" if QWEN_CONFIG_PATH.exists() else "À créer")
    status_cols[2].metric("Extensions", len(scan_qwen_extensions()))
    if not installed:
        st.info("Qwen Code n'est pas dans le PATH. Les réglages peuvent être préparés maintenant et seront lus après son installation.")

    st.subheader("Modèles Qwen Code")
    with st.form("qwen_model_form", border=False):
        model_name = st.text_input("Modèle principal", value=str(nested_value(config, "model.name", "")))
        reasoning_options = ["inherit", "low", "medium", "high", "xhigh", "max"]
        current_reasoning = str(nested_value(config, "model.reasoningEffort", "inherit"))
        reasoning = st.selectbox(
            "Effort de raisonnement",
            reasoning_options,
            index=reasoning_options.index(current_reasoning) if current_reasoning in reasoning_options else 0,
        )
        thinking = st.toggle(
            "Thinking Qwen",
            value=bool(nested_value(config, "model.generationConfig.extra_body.enable_thinking", False)),
            help="Transmet enable_thinking au serveur compatible OpenAI/vLLM.",
        )
        fast_model = st.text_input(
            "Fast model auxiliaire",
            value=str(nested_value(config, "model.fastModel", "")),
            help="Modèle léger pour les suggestions et opérations spéculatives ; ce n'est pas un mode turbo du modèle principal.",
        )
        sampling_col, limits_col = st.columns(2)
        with sampling_col:
            temperature = st.slider(
                "Température",
                0.0,
                2.0,
                float(nested_value(config, "model.generationConfig.samplingParams.temperature", 0.7)),
                0.05,
            )
            top_p = st.slider(
                "Top p",
                0.0,
                1.0,
                float(nested_value(config, "model.generationConfig.samplingParams.top_p", 0.95)),
                0.05,
            )
        with limits_col:
            max_tokens = st.number_input(
                "Tokens générés maximum",
                min_value=128,
                value=max(128, int(number_value(nested_value(config, "model.generationConfig.samplingParams.max_tokens", 2048)))),
                step=128,
            )
            context_size = st.number_input(
                "Fenêtre de contexte",
                min_value=1024,
                value=max(1024, int(number_value(nested_value(config, "model.generationConfig.contextWindowSize", 131072)))),
                step=1024,
            )
        save_qwen_model = st.form_submit_button("Enregistrer les modèles", type="primary")
    if save_qwen_model:
        updated = copy.deepcopy(config)
        changes = {
            "model.name": model_name.strip(),
            "model.generationConfig.extra_body.enable_thinking": thinking,
            "model.fastModel": fast_model.strip(),
            "model.generationConfig.samplingParams.temperature": temperature,
            "model.generationConfig.samplingParams.top_p": top_p,
            "model.generationConfig.samplingParams.max_tokens": int(max_tokens),
            "model.generationConfig.contextWindowSize": int(context_size),
        }
        for path, value in changes.items():
            set_nested_value(updated, path, value)
        if reasoning == "inherit":
            delete_nested_value(updated, "model.reasoningEffort")
        else:
            set_nested_value(updated, "model.reasoningEffort", reasoning)
        try:
            backup = save_json_config(QWEN_CONFIG_PATH, updated)
            st.success("Configuration modèle Qwen Code enregistrée." + (f" Sauvegarde : {backup.name}." if backup else ""))
            st.rerun()
        except OSError as exc:
            st.error(f"Enregistrement impossible : {exc}")

    st.divider()
    st.subheader("Exécution Qwen Code")
    with st.form("qwen_runtime_form", border=False):
        approval_modes = ["plan", "default", "auto-edit", "auto", "yolo"]
        current_approval = str(nested_value(config, "tools.approvalMode", "default"))
        approval_mode = st.selectbox(
            "Mode d'approbation",
            approval_modes,
            index=approval_modes.index(current_approval) if current_approval in approval_modes else 1,
        )
        limit_cols = st.columns(3)
        with limit_cols[0]:
            max_turns = st.number_input("Tours maximum", min_value=0, value=max(0, int(number_value(nested_value(config, "model.maxSessionTurns", 0)))))
            max_seconds = st.number_input("Durée maximum (s)", min_value=0, value=max(0, int(number_value(nested_value(config, "model.maxWallTimeSeconds", 0)))))
        with limit_cols[1]:
            max_tools = st.number_input("Appels d'outils maximum", min_value=0, value=max(0, int(number_value(nested_value(config, "model.maxToolCalls", 0)))))
            max_tools_turn = st.number_input("Outils maximum par tour", min_value=0, value=max(0, int(number_value(nested_value(config, "model.maxToolCallsPerTurn", 0)))))
        with limit_cols[2]:
            max_depth = st.number_input("Profondeur des sous-agents", min_value=0, value=max(0, int(number_value(nested_value(config, "model.maxSubagentDepth", 0)))))
            compact_threshold = st.number_input("Seuil de compaction", min_value=0.0, max_value=1.0, value=float(nested_value(config, "context.autoCompactThreshold", 0.8)), step=0.05)
        toggle_cols = st.columns(3)
        with toggle_cols[0]:
            use_rg = st.toggle("Utiliser ripgrep", value=bool(nested_value(config, "tools.useRipgrep", True)))
            tool_search = st.toggle("Recherche d'outils", value=bool(nested_value(config, "tools.toolSearch.enabled", True)))
        with toggle_cols[1]:
            folder_trust = st.toggle("Confiance des dossiers", value=bool(nested_value(config, "security.folderTrust.enabled", True)))
            loop_detection = st.toggle("Détection de boucle", value=not bool(nested_value(config, "model.skipLoopDetection", False)))
        with toggle_cols[2]:
            telemetry = st.toggle("Télémétrie", value=bool(nested_value(config, "telemetry.enabled", False)))
            tokens_per_second = st.toggle("Afficher tokens/s", value=bool(nested_value(config, "ui.showTokensPerSecond", True)))
        save_qwen_runtime = st.form_submit_button("Enregistrer le harness", type="primary")
    if save_qwen_runtime:
        updated = copy.deepcopy(config)
        changes = {
            "tools.approvalMode": approval_mode,
            "model.maxSessionTurns": int(max_turns),
            "model.maxWallTimeSeconds": int(max_seconds),
            "model.maxToolCalls": int(max_tools),
            "model.maxToolCallsPerTurn": int(max_tools_turn),
            "model.maxSubagentDepth": int(max_depth),
            "context.autoCompactThreshold": float(compact_threshold),
            "tools.useRipgrep": use_rg,
            "tools.toolSearch.enabled": tool_search,
            "security.folderTrust.enabled": folder_trust,
            "model.skipLoopDetection": not loop_detection,
            "telemetry.enabled": telemetry,
            "ui.showTokensPerSecond": tokens_per_second,
        }
        for path, value in changes.items():
            set_nested_value(updated, path, value)
        try:
            backup = save_json_config(QWEN_CONFIG_PATH, updated)
            st.success("Configuration du harness Qwen Code enregistrée." + (f" Sauvegarde : {backup.name}." if backup else ""))
            st.rerun()
        except OSError as exc:
            st.error(f"Enregistrement impossible : {exc}")

    st.divider()
    st.subheader("Extensions, skills et MCP")
    extensions = scan_qwen_extensions()
    qwen_skills = scan_qwen_skills()
    inventory_cols = st.columns(3)
    inventory_cols[0].metric("Extensions", len(extensions))
    inventory_cols[1].metric("Skills locaux", len(qwen_skills))
    mcp_servers = config.get("mcpServers", {}) if isinstance(config.get("mcpServers"), dict) else {}
    inventory_cols[2].metric("Serveurs MCP", len(mcp_servers))
    if extensions:
        st.dataframe(extensions, width="stretch", hide_index=True)
    if qwen_skills:
        st.dataframe(qwen_skills, width="stretch", hide_index=True)
    if mcp_servers:
        st.dataframe(
            [{"Serveur": name, "Type": "HTTP" if isinstance(value, dict) and value.get("url") else "stdio"} for name, value in mcp_servers.items()],
            width="stretch",
            hide_index=True,
        )


def render_configuration(base_url: str, env_api_key: str) -> None:
    st.subheader("Configuration des modèles et harnesses")
    st.caption("Inventaire local, paramètres de génération, outils, extensions et schémas configurables.")
    refresh_col, security_col = st.columns([1, 4])
    with refresh_col:
        if st.button("Actualiser", icon=":material/refresh:", key="refresh_configuration"):
            load_openclaw_schema.clear()
            load_openclaw_models.clear()
            load_openclaw_core.clear()
            load_openclaw_plugins.clear()
            load_openclaw_skills.clear()
            load_openclaw_extensions.clear()
            st.rerun()
    with security_col:
        st.caption("Les clés, tokens, mots de passe et credentials sont exclus des tableaux de configuration.")

    requested_openclaw_section = st.session_state.get("openclaw_configuration_section", "Modèle")
    load_extensions = requested_openclaw_section == "Plugins, skills et MCP"
    with st.spinner("Lecture parallèle du schéma et des modèles OpenClaw..."):
        schema, schema_error, models, model_status, model_error = load_openclaw_core()
    if load_extensions:
        with st.spinner("Lecture parallèle des plugins et skills OpenClaw..."):
            plugins, plugin_error, skills, skill_error = load_openclaw_extensions()
    else:
        plugins, plugin_error = [], ""
        skills, skill_error = [], ""
    openclaw_config = load_json_object(OPENCLAW_CONFIG_PATH)
    qwen_config = load_json_object(QWEN_CONFIG_PATH)
    schema_rows = flatten_schema(schema) if schema else []
    configured_plugins = nested_value(openclaw_config, "plugins.entries", {})
    configured_plugins = configured_plugins if isinstance(configured_plugins, dict) else {}
    configured_skills = nested_value(openclaw_config, "agents.defaults.skills", [])
    configured_skills = configured_skills if isinstance(configured_skills, list) else []

    top_metrics = st.columns(5)
    top_metrics[0].metric("Options OpenClaw", len(schema_rows) if schema_rows else "Indisponible")
    top_metrics[1].metric("Modèles", len(models))
    top_metrics[2].metric(
        "Plugins actifs",
        sum(1 for plugin in plugins if plugin_enabled(plugin))
        if load_extensions
        else sum(1 for value in configured_plugins.values() if isinstance(value, dict) and value.get("enabled")),
    )
    top_metrics[3].metric("Skills visibles", len(configured_skills))
    top_metrics[4].metric("Options Qwen", len(QWEN_SETTING_SPECS))

    openclaw_tab, qwen_tab, local_tab = st.tabs(["OpenClaw", "Qwen Code", "Local / RunPod"])
    with openclaw_tab:
        if not shutil.which("openclaw"):
            st.error("OpenClaw n'est pas disponible dans le PATH.")
        elif not openclaw_config:
            st.warning(f"Configuration OpenClaw absente ou invalide : {OPENCLAW_CONFIG_PATH}")
        if model_error:
            st.warning(f"Inventaire des modèles indisponible : {model_error}")
        openclaw_section = st.segmented_control(
            "Section OpenClaw",
            ["Modèle", "Harness", "Plugins, skills et MCP", "Tous les réglages"],
            default="Modèle",
            selection_mode="single",
            key="openclaw_configuration_section",
            width="stretch",
            label_visibility="collapsed",
        ) or "Modèle"
        if openclaw_section == "Modèle":
            render_openclaw_model_settings(openclaw_config, models, model_status)
        elif openclaw_section == "Harness":
            render_openclaw_runtime_settings(openclaw_config)
        elif openclaw_section == "Plugins, skills et MCP":
            render_openclaw_extensions(openclaw_config, plugins, plugin_error, skills, skill_error)
        else:
            if schema_error:
                st.error(f"Schéma OpenClaw indisponible : {schema_error}")
            elif schema_rows:
                render_advanced_editor(
                    "Schéma OpenClaw complet",
                    schema_rows,
                    openclaw_config,
                    lambda path, value: patch_openclaw_config(nested_patch(path, value)),
                    lambda path: patch_openclaw_config(nested_patch(path, None)),
                    "openclaw_advanced",
                )
            else:
                st.info("Aucune option de schéma chargée.")

    with qwen_tab:
        qwen_section = st.segmented_control(
            "Section Qwen Code",
            ["Réglages principaux", "Tous les réglages"],
            default="Réglages principaux",
            selection_mode="single",
            key="qwen_configuration_section",
            width="stretch",
            label_visibility="collapsed",
        ) or "Réglages principaux"
        if qwen_section == "Réglages principaux":
            render_qwen_settings(qwen_config)
        else:
            qwen_rows = [
                {
                    "Chemin": spec["path"],
                    "Type": spec["type"],
                    "Valeurs": ", ".join(spec.get("choices", [])),
                    "Description": spec.get("description", ""),
                }
                for spec in QWEN_SETTING_SPECS
            ]

            def save_qwen_value(path: str, value: Any) -> None:
                updated = load_json_object(QWEN_CONFIG_PATH)
                set_nested_value(updated, path, value)
                save_json_config(QWEN_CONFIG_PATH, updated)

            def delete_qwen_value(path: str) -> None:
                updated = load_json_object(QWEN_CONFIG_PATH)
                delete_nested_value(updated, path)
                save_json_config(QWEN_CONFIG_PATH, updated)

            render_advanced_editor(
                "Catalogue Qwen Code",
                qwen_rows,
                qwen_config,
                save_qwen_value,
                delete_qwen_value,
                "qwen_advanced",
            )

    with local_tab:
        st.subheader("Configuration locale et RunPod")
        st.write(f"**API utilisée :** `{base_url}`")
        st.write(
            "**Clé API :** "
            + ("préremplie depuis RUNPOD_API_KEY" if env_api_key else "saisie pour cette session")
        )
        st.warning(
            "Le dashboard ne place aucune clé dans le repository. Les credentials vLLM temporaires restent dans la session Streamlit."
        )
        st.code(
            '$env:RUNPOD_API_KEY = "<ta-cle-runpod>"\nstreamlit run monitoring/runpod/dashboard/app.py',
            language="powershell",
        )


def main() -> None:
    st.set_page_config(
        page_title="RunPod Control Center",
        page_icon="R",
        layout="wide",
        initial_sidebar_state="auto",
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

    run_lifecycle_guard(base_url, api_key)

    pages = ["Vue d'ensemble", "Machines", "Harnesses", "Déployer vLLM", "Templates", "Configuration"]
    page = st.segmented_control(
        "Navigation principale",
        pages,
        default="Vue d'ensemble",
        selection_mode="single",
        key="main_navigation",
        label_visibility="collapsed",
        width="stretch",
    ) or "Vue d'ensemble"

    if page == "Vue d'ensemble":
        render_overview(base_url, api_key, account, account_error)
    elif page == "Machines":
        render_machines(base_url, api_key)
    elif page == "Harnesses":
        render_harnesses(base_url, api_key)
    elif page == "Déployer vLLM":
        render_vllm_deploy(base_url, api_key)
    elif page == "Templates":
        render_templates()
    else:
        render_configuration(base_url, env_api_key)


if __name__ == "__main__":
    main()
