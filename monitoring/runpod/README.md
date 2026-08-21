# Runpod Machine Scripts (Windows)

Ce dossier contient des scripts PowerShell pour piloter les machines Runpod depuis ton poste.

## Pré-requis

- Jeton API Runpod dans l'environnement (`RUNPOD_API_KEY`)
- PowerShell 7+ ou PowerShell 5.1+
- (Optionnel) un token en variable d'environnement pour éviter de le passer en argument

```
$env:RUNPOD_API_KEY = "<ton_runpod_api_key>"
```

Par défaut, les scripts appellent `https://rest.runpod.io/v1`.
Si ton endpoint change, passe `-ApiBaseUrl`.

## Scripts

### 1) Liste des pods

```powershell
.\powershell\monitor-runpods.ps1
.\powershell\monitor-runpods.ps1 -State running
.\powershell\monitor-runpods.ps1 -Raw
```

### 2) Créer une machine

Prépare un payload Runpod dans `powershell/create-pod-template.example.json` puis exécute :

```powershell
.\powershell\create-runpod.ps1 -TemplatePath .\powershell\create-pod-template.example.json
```

Le script envoie le payload tel quel. Adapte donc le JSON au format exact attendu par l'API Runpod.

### 3) Supprimer / arrêter une machine

Supprime par ID :

```powershell
.\powershell\delete-runpod.ps1 -PodIdentifier 123abc
```

Supprime par nom (si unique) :

```powershell
.\powershell\delete-runpod.ps1 -PodIdentifier "my-a40-vllm"
```

### 4) Arrêt logique via action terminate

Selon les droits/versions d'API, le stop réel peut être un `DELETE` ou un `POST /terminate`.
Le script supporte les deux :

```powershell
.\powershell\delete-runpod.ps1 -PodIdentifier 123abc -Action terminate
```

## Dashboard Streamlit (recommandé)

Le dashboard local regroupe les machines, les templates JSON et les actions de création, terminaison et suppression dans une seule interface. Il ne stocke pas la clé API.

Depuis la racine du repository :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r monitoring/runpod/dashboard/requirements.txt
$env:RUNPOD_API_KEY = "<ta_runpod_api_key>"
streamlit run monitoring/runpod/dashboard/app.py
```

Puis ouvre `http://localhost:8501`.

Tu peux aussi saisir la clé dans la barre latérale puis cliquer sur `Enregistrer la clé dans Windows`. Elle sera enregistrée comme variable d'environnement utilisateur `RUNPOD_API_KEY` dans Windows. Les nouveaux terminaux et les prochains lancements de Streamlit la récupéreront automatiquement ; la valeur reste stockée localement en clair par Windows comme toute variable d'environnement.

Les templates éditables sont dans `monitoring/runpod/templates/`. Le bouton `terminate` est l'action à privilégier pour arrêter une machine ; `delete` est conservé comme action explicite et demande une confirmation dans l'interface.

### Déployer vLLM et récupérer les credentials

Dans l'onglet `Déployer vLLM`, choisis d'abord le service `Génération texte` ou `Transcription vocale`, puis renseigne :

- l'identifiant du modèle Hugging Face, par exemple `Qwen/Qwen3-8B` ;
- le type de cloud (`SECURE` ou `COMMUNITY`) ;
- un ou plusieurs GPU dans la liste récupérée directement depuis Runpod ;
- un token Hugging Face uniquement si le modèle est privé ou soumis à une licence ;
- un Network Volume ID si tu veux conserver le cache du modèle entre plusieurs pods.

Pour ton cas, la template [qwen3.8-27b-fp8-a40.example.json](./templates/qwen3.8-27b-fp8-a40.example.json) préconfigure `Qwen/Qwen3.8-27B-FP8` sur une A40. La variante FP8 est nécessaire pour tenir dans les 48 GB de mémoire de l'A40 ; le contexte initial est limité à 32K pour laisser de la marge au cache KV et à l'encodeur vision. Le modèle Qwen3.8 est multimodal, mais son API reste compatible OpenAI pour les requêtes texte.

Le dashboard récupère le catalogue GPU Runpod avec l'état de stock, la mémoire et le prix indicatif, puis crée le pod avec l'image officielle `vllm/vllm-openai`, expose le port `8000/http` et démarre l'API OpenAI-compatible. vLLM télécharge automatiquement le modèle au démarrage dans `/workspace/huggingface`.

Pour le service `Transcription vocale`, le modèle par défaut est `openai/whisper-large-v3-turbo`. L'endpoint à utiliser depuis ton application est :

```text
POST https://<pod-id>-8000.proxy.runpod.net/v1/audio/transcriptions
```

Exemple de connexion avec le client OpenAI :

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<pod-id>-8000.proxy.runpod.net/v1",
    api_key="<cle-vllm>",
)

with open("message.wav", "rb") as audio_file:
    result = client.audio.transcriptions.create(
        model="openai/whisper-large-v3-turbo",
        file=audio_file,
        language="fr",
    )

print(result.text)
```

Le template brut correspondant est [vllm-asr-whisper.example.json](./templates/vllm-asr-whisper.example.json). vLLM demande ses dépendances audio pour activer l'API de transcription ; l'image officielle doit donc être conservée à jour.

Si RunPod renvoie `There are no instances currently available`, cela signifie que la capacité est temporairement épuisée pour le cloud et les GPU choisis. Change le cloud, ajoute des GPU alternatifs ou réessaie plus tard. L'API REST RunPod documente `SECURE` et `COMMUNITY` comme types de cloud disponibles pour la création d'un pod.

Après la création, l'interface affiche un bloc copiable et deux boutons de téléchargement :

```text
VLLM_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1
VLLM_API_KEY=<cle-generee-pour-vllm>
VLLM_MODEL=<modele-hugging-face>
```

Le dashboard affiche aussi un badge d'état vLLM pour chaque machine : `Operationnel` en vert lorsque `/v1/models` répond, `Demarrage` en orange lorsque le pod ou le chargement du modèle est encore en cours, et `Hors ligne` en rouge lorsque l'endpoint ne répond pas ou que l'authentification échoue. Pour les pods existants dont la clé vLLM n'est pas connue par la session, le badge indique `Non verifiable` en orange.

Le bouton `Retester /v1/models` permet de vérifier manuellement que le téléchargement est terminé et que vLLM répond. La clé vLLM n'est conservée que dans la session Streamlit ; télécharge le fichier `.env` immédiatement après le déploiement.

## Paramètres partagés

Tous les scripts partagent :

- `-ApiBaseUrl` : base URL de l'API Runpod (default `https://rest.runpod.io/v1`)
- `-ApiKey` : clé API (sinon utilise `RUNPOD_API_KEY`)
- `-TimeoutSec` : timeout HTTP (default 30)

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
    vllm-asr-whisper.example.json
```

Les URLs proxy Runpod et le lancement Docker suivent les documentations officielles [Runpod](https://docs.runpod.io/api-reference/pods/POST/pods) et [vLLM](https://docs.vllm.ai/en/stable/serving/online_serving/speech_to_text/).
