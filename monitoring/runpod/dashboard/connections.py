"""Local, secret-free catalog of vLLM connections for multi-agent harnesses."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_VLLM_CONTEXT_WINDOW = 16384
DEFAULT_VLLM_MAX_TOKENS = 4096


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connection_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "vllm"


def load_connection_store(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    connections = value.get("connections") if isinstance(value, dict) else []
    return {"version": 1, "connections": connections if isinstance(connections, list) else []}


def save_connection_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(store, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def upsert_connection(store: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    connection_id = connection_slug(str(connection.get("id") or connection.get("name") or "vllm"))
    now = utc_timestamp()
    normalized = {
        "id": connection_id,
        "name": str(connection.get("name") or connection_id).strip(),
        "podId": str(connection.get("podId") or "").strip(),
        "baseUrl": str(connection.get("baseUrl") or "").strip().rstrip("/"),
        "model": str(connection.get("model") or "").strip(),
        "apiKeyEnv": str(connection.get("apiKeyEnv") or f"VLLM_{connection_id.upper().replace('-', '_')}_API_KEY").strip(),
        "contextWindow": max(1024, int(connection.get("contextWindow") or DEFAULT_VLLM_CONTEXT_WINDOW)),
        "maxTokens": max(128, int(connection.get("maxTokens") or DEFAULT_VLLM_MAX_TOKENS)),
        "enabled": bool(connection.get("enabled", True)),
        "createdAt": str(connection.get("createdAt") or now),
        "updatedAt": now,
    }
    connections = store.setdefault("connections", [])
    for index, existing in enumerate(connections):
        if isinstance(existing, dict) and existing.get("id") == connection_id:
            normalized["createdAt"] = str(existing.get("createdAt") or normalized["createdAt"])
            connections[index] = normalized
            return normalized
    connections.append(normalized)
    return normalized


def remove_connection(store: dict[str, Any], connection_id: str) -> None:
    connections = store.get("connections")
    if not isinstance(connections, list):
        return
    store["connections"] = [
        connection for connection in connections if not isinstance(connection, dict) or connection.get("id") != connection_id
    ]


def openclaw_provider_id(connection: dict[str, Any]) -> str:
    return f"vllm-{connection_slug(str(connection.get('id') or connection.get('name') or 'vllm'))}"


def openclaw_model_ref(connection: dict[str, Any]) -> str:
    return f"{openclaw_provider_id(connection)}/{connection['model']}"


def openclaw_provider_config(connection: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseUrl": connection["baseUrl"],
        "apiKey": "${" + connection["apiKeyEnv"] + "}",
        "api": "openai-completions",
        "timeoutSeconds": 300,
        "models": [
            {
                "id": connection["model"],
                "name": connection["name"],
                "reasoning": True,
                "input": ["text"],
                "contextWindow": int(connection["contextWindow"]),
                "maxTokens": int(connection["maxTokens"]),
            }
        ],
    }
