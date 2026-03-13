@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ============================================================
::  Python Agent Platform — Lancement (mode production)
::  Lance le serveur FastAPI + ouvre le navigateur
:: ============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "VENV=%ROOT%\.venv"
set "VENV_PYTHON=%VENV%\Scripts\python.exe"
set "PORT=8000"
set "URL=http://localhost:%PORT%"

title Python Agent Platform

:: ── Vérifier l'installation ───────────────────────────────────
if not exist "%VENV_PYTHON%" (
    echo.
    echo  [ERREUR] L'environnement virtuel est introuvable.
    echo  Lancez d'abord :  install.bat
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%\frontend\dist\index.html" (
    echo.
    echo  [ATTENTION] Le frontend n'est pas compilé.
    echo  Lancement de la compilation...
    echo.
    cd /d "%ROOT%\frontend"
    call npm run build
    if !errorlevel! neq 0 (
        echo  [ERREUR] Build frontend échoué. Lancez install.bat.
        pause
        exit /b 1
    )
    cd /d "%ROOT%"
)

:: ── Vérifier si le port est déjà occupé ─────────────────────
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel! == 0 (
    echo.
    echo  [INFO] Le port %PORT% est déjà utilisé.
    echo  Ouverture du navigateur sur %URL%...
    timeout /t 1 /nobreak >nul
    start "" "%URL%"
    exit /b 0
)

:: ── Démarrer le serveur ───────────────────────────────────────
cls
echo.
echo  ========================================================
echo   Python Agent Platform
echo  ========================================================
echo.
echo   URL : %URL%
echo   API : %URL%/api/docs
echo.
echo   Demarrage du serveur...
echo   (Fermez cette fenetre pour arreter l'application)
echo.
echo  ========================================================
echo.

:: Ouvrir le navigateur après 3 secondes
start "" cmd /c "timeout /t 3 /nobreak >nul && start "" %URL%"

:: Lancer uvicorn (bloque dans cette fenetre — logs visibles)
cd /d "%ROOT%"
"%VENV_PYTHON%" -m uvicorn backend.main:app ^
    --host 0.0.0.0 ^
    --port %PORT% ^
    --log-level info

echo.
echo  Le serveur s'est arrêté.
pause
