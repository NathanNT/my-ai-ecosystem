"""Persistent, local lifecycle policies for RunPod Pods.

The policy store contains no credentials. It is deliberately separate from the
Streamlit UI so overdue actions can be reconciled after an application restart.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


INACTIVE_POD_STATES = {"EXITED", "STOPPED", "TERMINATED", "DELETED"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_policy_store(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    policies = value.get("policies") if isinstance(value, dict) else {}
    return {"version": 1, "policies": policies if isinstance(policies, dict) else {}}


def save_policy_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(store, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def schedule_policy(
    store: dict[str, Any],
    pod_id: str,
    pod_name: str,
    pause_after_minutes: int | None,
    delete_after_minutes: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    policy = {
        "podId": pod_id,
        "podName": pod_name,
        "createdAt": timestamp(now),
        "updatedAt": timestamp(now),
        "pauseAt": timestamp(now + timedelta(minutes=pause_after_minutes))
        if pause_after_minutes
        else None,
        "deleteAt": timestamp(now + timedelta(minutes=delete_after_minutes))
        if delete_after_minutes
        else None,
        "pauseCompletedAt": None,
        "deleteCompletedAt": None,
        "lastError": "",
    }
    store.setdefault("policies", {})[pod_id] = policy
    return policy


def clear_policy(store: dict[str, Any], pod_id: str) -> None:
    policies = store.get("policies")
    if isinstance(policies, dict):
        policies.pop(pod_id, None)


def pod_policy_action(policy: dict[str, Any], pod_state: str, now: datetime | None = None) -> str | None:
    now = now or utc_now()
    state = pod_state.upper()
    delete_at = parse_timestamp(policy.get("deleteAt"))
    if delete_at and now >= delete_at and not policy.get("deleteCompletedAt"):
        return "delete"
    pause_at = parse_timestamp(policy.get("pauseAt"))
    if pause_at and now >= pause_at and not policy.get("pauseCompletedAt"):
        return "pause_complete" if state in INACTIVE_POD_STATES else "stop"
    return None


def mark_completed(policy: dict[str, Any], action: str, now: datetime | None = None) -> None:
    now = now or utc_now()
    if action in {"stop", "pause_complete"}:
        policy["pauseCompletedAt"] = timestamp(now)
    elif action == "delete":
        policy["deleteCompletedAt"] = timestamp(now)
    policy["lastError"] = ""
    policy["updatedAt"] = timestamp(now)


def mark_error(policy: dict[str, Any], message: str, now: datetime | None = None) -> None:
    now = now or utc_now()
    policy["lastError"] = message[:500]
    policy["updatedAt"] = timestamp(now)


@contextmanager
def policy_lock(path: Path) -> Iterator[bool]:
    """Avoid duplicate destructive actions from concurrent Streamlit sessions."""

    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if utc_now().timestamp() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
                    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                pass
        yield descriptor is not None
    finally:
        if descriptor is not None:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except OSError:
                pass
