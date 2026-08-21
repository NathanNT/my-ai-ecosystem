"""Local Streamlit dashboard for managing Runpod pods and vLLM deployments."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import requests
import streamlit as st


RUNPOD_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = RUNPOD_DIR / "templates"
DEFAULT_API_BASE_URL = "https://rest.runpod.io/v1"
VLLM_PORT = 8000


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
    return str(pod.get("state") or pod.get("status") or pod.get("currentStatus") or "unknown")


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

    payload: dict[str, Any] = {
        "name": name,
        "cloudType": cloud_type,
        "gpuCount": 1,
        "gpuTypeIds": gpu_types,
        "gpuTypePriority": "availability",
        "imageName": "vllm/vllm-openai:latest",
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
        ],
    }
    if cloud_type == "COMMUNITY":
        payload["supportPublicIp"] = True
    if network_volume_id.strip():
        payload["networkVolumeId"] = network_volume_id.strip()
    return payload


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


def render_vllm_deploy(base_url: str, api_key: str) -> None:
    st.subheader("Déployer vLLM")
    service_label = st.selectbox("Service à déployer", ["Génération texte", "Transcription vocale"])
    service_type = "asr" if service_label == "Transcription vocale" else "text"
    st.caption(
        "Le modèle audio sera exposé sur /v1/audio/transcriptions."
        if service_type == "asr"
        else "Le modèle texte sera exposé sur l'API OpenAI-compatible /v1."
    )
    if not api_key:
        st.info("Définis d'abord RUNPOD_API_KEY ou saisis ta clé Runpod dans la barre latérale.")
        return

    cloud_type = st.selectbox(
        "Type de cloud",
        ["SECURE", "COMMUNITY"],
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
    default_gpu = next((label for label in gpu_options if "A40" in label), next(iter(gpu_options)))
    selected_gpu_labels = st.multiselect(
        "GPU(s), dans l'ordre de préférence",
        options=list(gpu_options),
        default=[default_gpu],
        help="Les GPU sélectionnés sont envoyés à Runpod dans cet ordre. Leur disponibilité est récupérée en direct.",
    )
    selected_gpu_ids = [gpu_options[label] for label in selected_gpu_labels]
    st.caption("Les prix et états affichés proviennent du catalogue Runpod au moment du chargement.")

    with st.form("vllm_deploy_form"):
        name = st.text_input("Nom du pod", value="vllm-a40")
        default_model = "openai/whisper-large-v3-turbo" if service_type == "asr" else "Qwen/Qwen3-8B"
        model = st.text_input("Modèle Hugging Face", value=default_model)
        hf_token = st.text_input(
            "Token Hugging Face (optionnel)",
            type="password",
            help="Nécessaire uniquement pour un modèle privé ou soumis à une licence Hugging Face.",
        )
        network_volume_id = st.text_input(
            "Network volume ID (optionnel)",
            help="Recommandé pour conserver le cache du modèle entre plusieurs pods.",
        )
        disk_col, volume_col = st.columns(2)
        with disk_col:
            container_disk_gb = st.number_input("Disque conteneur (GB)", min_value=20, value=50, step=10)
        with volume_col:
            volume_gb = st.number_input("Volume de travail (GB)", min_value=20, value=100, step=10)
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

    st.metric("Machines visibles", len(pods))
    if pods:
        rows = [
            {
                "Nom": pod.get("name", ""),
                "ID": pod.get("id", ""),
                "État": pod_state(pod),
                "GPU": pod.get("gpuTypeId") or pod.get("gpuType", ""),
                "Région": pod.get("region", ""),
                "IP publique": pod.get("publicIp", ""),
            }
            for pod in pods
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("Etat des modeles vLLM")
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
    st.set_page_config(page_title="Runpod Control Center", page_icon="⚙️", layout="wide")
    st.title("Runpod Control Center")
    st.caption("Dashboard local pour suivre et piloter tes machines Runpod.")

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

    machines_tab, vllm_tab, templates_tab, config_tab = st.tabs(
        ["Machines", "Déployer vLLM", "Templates", "Configuration"]
    )
    with machines_tab:
        render_machines(base_url, api_key)
    with vllm_tab:
        render_vllm_deploy(base_url, api_key)
    with templates_tab:
        render_templates()
    with config_tab:
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
