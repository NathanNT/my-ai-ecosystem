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

### 4) Mettre une machine en pause

La pause libère le GPU et conserve le Volume Disk. RunPod facture toujours le stockage du volume tant que le Pod existe : utilise `delete` lorsque tu ne veux plus conserver la machine.

```powershell
.\powershell\delete-runpod.ps1 -PodIdentifier 123abc -Action stop
```

## Dashboard Streamlit (recommandé)

The `Machines` view refreshes automatically every 15 seconds. While a Pod is starting, it shows its RunPod state, requested state, GPU, image, saved model metadata, hourly cost, endpoint, and the latest lifecycle event. vLLM health is checked as soon as its locally stored API-key variable is available.

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

Depuis l'Explorateur Windows, tu peux aussi double-cliquer sur `scripts\start-runpod-dashboard.bat`. Au premier lancement, le script crée `.venv` et installe automatiquement les dépendances. Pour préparer uniquement l'environnement, utilise `scripts\setup-runpod-dashboard.bat`.

Le dashboard utilise un thème sombre local et ouvre sur une vue d'ensemble qui affiche :

- les crédits RunPod restants et la dépense actuelle par heure ;
- l'autonomie estimée au rythme de dépense actuel ;
- le coût projeté par jour, les pods actifs et le nombre de GPU alloués ;
- le coût des Pods depuis le début du mois, ventilé par jour et par machine ;
- l'état de l'auto-paiement, ses seuils et la limite de dépense du compte.

La lecture du solde utilise l'API GraphQL RunPod. Avec une clé à permissions restreintes, autorise la lecture du compte et de la facturation ; le pilotage des Pods reste disponible même si les informations financières sont refusées.

### Harness monitoring

The `Harnesses` page refreshes OpenClaw and Qwen Code every ten seconds. Each card shows the active connection, model, endpoint, vLLM health, process uptime, memory usage, and latency. Start, stop, and restart controls operate only on the detected harness processes.

`Options vLLM` separates saved RunPod instances from manual configuration. Selecting a different instance only creates a pending selection; the active connection does not change until `Apply and restart` is used. Before replacing a running harness, the dashboard calls the selected endpoint's `/models` route with the selected API key and verifies that the configured model is served. A failed endpoint, rejected key, or mismatched model prevents the restart.

RunPod reads are preloaded concurrently and shared across pages. Pod inventory is cached for 15 seconds, account data for 60 seconds, billing for five minutes, GPU availability for 60 seconds, vLLM health for 12 seconds, and the local process inventory for 15 seconds. Manual refreshes and every create, stop, or delete action invalidate the relevant caches immediately. API keys are excluded from Streamlit cache keys; only their SHA-256 fingerprints identify separate cache entries. OpenClaw device-pairing requests are loaded only when their refresh button is used.

For OpenClaw, applying an instance updates the explicit `vllm` provider and primary model in `openclaw.json`. The selected per-Pod secret remains in its dedicated Windows variable, while the dashboard synchronizes the active value to both `VLLM_API_KEY` and OpenClaw's higher-priority `vllm:default` auth profile. The key is then injected explicitly into every Gateway process launched by the dashboard. On Windows, harness processes also receive `NODE_USE_SYSTEM_CA=1` so Node.js trusts the Windows certificate store without disabling TLS verification. Qwen Code receives `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` in its process environment.

### Pool vLLM multi-agent

Every text vLLM Pod created from `Déployer vLLM` is automatically registered as a selectable instance. Its endpoint, model, and a dedicated Windows API-key variable are saved locally, outside Git. The `Harnesses` page compares this catalog with the live RunPod inventory: deleted Pods are hidden from selectors and can be removed from the local catalog in one click.

In OpenClaw's `Options vLLM`, select one primary instance and zero or more additional instances. The primary instance becomes the default `vllm` model. Each additional instance creates a dedicated provider and agent in `openclaw.json`, with its own environment-backed API key. Applying the selection validates every endpoint and restarts the Gateway when it is already running. Qwen Code supports one vLLM connection per CLI process, so its selector remains single-choice.

The local instance catalog is stored in `monitoring/runpod/data/vllm-connections.json` and is ignored by Git. It contains endpoint and model metadata plus the name of the Windows variable that holds the API key; the key itself is never written to the catalog.

### Configurer les modèles et les harnesses

La page `Configuration` centralise les réglages OpenClaw et Qwen Code :

- modèle principal, fallbacks, limite de génération et fenêtre de contexte ;
- thinking/reasoning, température, top-p et paramètres de compaction ;
- concurrence, profil d'outils, élévation, approbations et limites des sous-agents ;
- plugins, skills, extensions et serveurs MCP détectés localement ;
- catalogue avancé de tous les chemins exposés par le schéma vivant OpenClaw ;
- catalogue des réglages Qwen Code, y compris les modèles auxiliaires, la télémétrie et les options d'interface.

Les booléens utilisent des interrupteurs, les valeurs bornées des champs numériques et les enums des menus. Le `Fast mode` OpenClaw n'est proposé que pour les fournisseurs/modèles compatibles. Dans Qwen Code, `fastModel` désigne un modèle auxiliaire léger et ne rend pas automatiquement le modèle principal plus rapide.

Les changements OpenClaw passent par `openclaw config patch --stdin`, puis `openclaw config validate`. Qwen Code n'expose pas de commande équivalente : le dashboard écrit `%USERPROFILE%\.qwen\settings.json` de façon atomique et crée une sauvegarde horodatée avant chaque modification d'un fichier existant. Qwen peut ainsi être préconfiguré avant même l'installation du CLI.

Les chemins contenant une clé, un token, un mot de passe, un secret ou des credentials sont exclus de l'explorateur avancé. Le bouton `Actualiser` vide le cache d'inventaire et relit le schéma, les modèles, les plugins et les skills installés.

Pour garder l'interface réactive, le schéma, la liste des modèles et leur statut sont lus en parallèle puis conservés cinq minutes en cache. Les plugins et les skills ne sont demandés que lorsque leur section est ouverte, et sont eux aussi lus en parallèle.

Références : [configuration OpenClaw](https://docs.openclaw.ai/gateway/configuration), [référence complète OpenClaw](https://docs.openclaw.ai/gateway/configuration-reference) et [settings Qwen Code](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md).

Les logos locaux du dashboard proviennent des sites officiels [OpenClaw](https://openclaw.ai/) et [Qwen](https://qwenlm.github.io/).

Tu peux aussi saisir la clé dans la barre latérale puis cliquer sur `Enregistrer la clé dans Windows`. Elle sera enregistrée comme variable d'environnement utilisateur `RUNPOD_API_KEY` dans Windows. Les nouveaux terminaux et les prochains lancements de Streamlit la récupéreront automatiquement ; la valeur reste stockée localement en clair par Windows comme toute variable d'environnement.

Les templates éditables sont dans `monitoring/runpod/templates/`. Le bouton `Mettre en pause` appelle `POST /pods/{id}/stop` et le bouton `Détruire` appelle `DELETE /pods/{id}`. La destruction demande une confirmation dans l'interface.

### Protection automatique contre la surfacturation

Dans `Machines`, la section `Protection contre la surfacturation` permet de programmer, pour chaque Pod, un délai de pause, un délai de destruction, ou les deux. Les deux délais sont calculés à partir de l'enregistrement de la règle ; si les deux sont utilisés, la destruction doit être postérieure à la pause.

Les règles sont enregistrées hors Git dans `monitoring/runpod/data/pod-lifecycle.json` et ne contiennent aucune clé API. Le dashboard les contrôle toutes les 30 secondes tant qu'il est ouvert. Après un redémarrage, les règles et leurs dates absolues sont relues ; une action échue est demandée immédiatement. Les erreurs RunPod sont mémorisées dans la règle et réessayées au contrôle suivant.

Une pause conserve le Volume Disk mais pas le Container Disk, et peut encore produire des frais de stockage. Une destruction est définitive pour toutes les données hors Network Volume. La protection ne peut évidemment pas déclencher une action pendant que le PC et le dashboard sont complètement arrêtés ; au prochain lancement, elle rattrape l'échéance sans la perdre.

### Déployer vLLM et récupérer les credentials

Dans l'onglet `Déployer vLLM`, choisis une template ou `Configuration manuelle`. Une template préremplit le nom, le service, le cloud, les GPU disponibles, le modèle, le stockage et les options de démarrage vLLM ; tu peux encore modifier ces valeurs avant de créer le pod. Pour une configuration manuelle, choisis ensuite le service `Génération texte` ou `Transcription vocale`, puis renseigne :

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
