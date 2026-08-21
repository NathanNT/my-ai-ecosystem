# Windows Shortcuts

These batch files are convenience launchers for the commands used most often on Windows.

## RunPod dashboard

Double-click `setup-runpod-dashboard.bat` once to create the local `.venv` and install the dashboard dependencies.

Then double-click `start-runpod-dashboard.bat` to launch Streamlit at `http://localhost:8501`.

The RunPod API key is read from `RUNPOD_API_KEY`, or can be entered in the dashboard sidebar. The scripts never contain the key.

## RunPod monitoring

`runpod-monitor.bat` lists the pods using the current `RUNPOD_API_KEY`:

```bat
scripts\runpod-monitor.bat
scripts\runpod-monitor.bat -State running
scripts\runpod-monitor.bat -Raw
```

## OpenClaw

`openclaw-status.bat` runs `openclaw status`.

`openclaw-gateway.bat` starts the native local Gateway in the foreground:

```bat
scripts\openclaw-gateway.bat
```
