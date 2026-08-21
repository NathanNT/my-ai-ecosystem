@echo off
setlocal
pushd "%~dp0.."

where python >nul 2>&1
if errorlevel 1 (
    echo Python est introuvable dans le PATH.
    popd
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo Installation ou mise a jour des dependances...
".venv\Scripts\python.exe" -m pip install -r monitoring\runpod\dashboard\requirements.txt
if errorlevel 1 goto :error

echo.
echo Installation terminee. Lance maintenant start-runpod-dashboard.bat.
popd
exit /b 0

:error
echo.
echo L'operation a echoue.
popd
exit /b 1
