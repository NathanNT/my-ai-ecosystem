@echo off
setlocal
pushd "%~dp0.."

if not exist ".venv\Scripts\streamlit.exe" (
    echo L'environnement RunPod n'est pas installe. Installation en cours...
    call "%~dp0setup-runpod-dashboard.bat"
    if errorlevel 1 goto :error
)

echo Dashboard RunPod : http://localhost:8501
".venv\Scripts\streamlit.exe" run monitoring\runpod\dashboard\app.py
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:error
popd
exit /b 1
