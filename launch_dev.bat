@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ============================================================
::  Python Agent Platform — Mode développement
::  Lance backend (hot reload) + frontend Vite dans 2 fenêtres
:: ============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "VENV=%ROOT%\.venv"
set "VENV_PYTHON=%VENV%\Scripts\python.exe"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"

title Python Agent Platform — Dev

:: ── Vérifications ─────────────────────────────────────────────
if not exist "%VENV_PYTHON%" (
    echo  [ERREUR] Venv introuvable. Lancez install.bat d'abord.
    pause
    exit /b 1
)

if not exist "%ROOT%\frontend\node_modules" (
    echo  [ERREUR] node_modules introuvable. Lancez install.bat d'abord.
    pause
    exit /b 1
)

cls
echo.
echo  ========================================================
echo   Python Agent Platform — Mode Developpement
echo  ========================================================
echo.
echo   Backend  : http://localhost:%BACKEND_PORT%/api/docs
echo   Frontend : http://localhost:%FRONTEND_PORT%
echo.
echo   Deux fenetres CMD vont s'ouvrir.
echo   Fermez-les pour arreter les serveurs.
echo.
echo  ========================================================
echo.

:: ── Lancer backend dans une nouvelle fenêtre ─────────────────
start "Agent Platform — Backend (FastAPI)" cmd /k ^
    "chcp 65001 >nul && cd /d "%ROOT%" && echo [Backend] Demarrage... && "%VENV_PYTHON%" -m uvicorn backend.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"

:: ── Lancer frontend dans une nouvelle fenêtre ────────────────
timeout /t 1 /nobreak >nul
start "Agent Platform — Frontend (Vite)" cmd /k ^
    "chcp 65001 >nul && cd /d "%ROOT%\frontend" && echo [Frontend] Demarrage... && npm run dev"

:: ── Ouvrir le navigateur ──────────────────────────────────────
timeout /t 4 /nobreak >nul
start "" "http://localhost:%FRONTEND_PORT%"

echo  Les serveurs sont lancés dans des fenêtres séparées.
echo  Fermez cette fenêtre si vous voulez.
timeout /t 3 /nobreak >nul
