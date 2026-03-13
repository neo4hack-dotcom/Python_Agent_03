@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ============================================================
::  Python Agent Platform — Installation Windows
::  Double-cliquez ou lancez depuis CMD : install.bat
:: ============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "VENV=%ROOT%\.venv"
set "PYTHON_EXE="
set "NODE_OK=0"

title Python Agent Platform — Installation

echo.
echo  ========================================================
echo   Python Agent Platform — Installation
echo  ========================================================
echo.

:: ── 1. Détecter Python ───────────────────────────────────────
echo  [1/6] Vérification de Python...

for %%C in (python py python3) do (
    if "!PYTHON_EXE!"=="" (
        %%C --version >nul 2>&1
        if !errorlevel! == 0 (
            set "PYTHON_EXE=%%C"
        )
    )
)

if "!PYTHON_EXE!"=="" (
    echo.
    echo  [ERREUR] Python n'est pas installe ou pas dans le PATH.
    echo  Telechargez Python 3.11+ sur https://www.python.org/downloads/
    echo  Cochez bien "Add python.exe to PATH" lors de l'installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('!PYTHON_EXE! --version 2^>^&1') do set PY_VERSION=%%V
echo  OK — !PY_VERSION!

:: ── 2. Créer l'environnement virtuel ─────────────────────────
echo.
echo  [2/6] Création de l'environnement virtuel (.venv)...

if exist "%VENV%\Scripts\python.exe" (
    echo  Déjà existant — réutilisation.
) else (
    !PYTHON_EXE! -m venv "%VENV%"
    if !errorlevel! neq 0 (
        echo  [ERREUR] Impossible de créer le venv.
        pause
        exit /b 1
    )
    echo  OK — .venv créé.
)

set "VENV_PYTHON=%VENV%\Scripts\python.exe"
set "VENV_PIP=%VENV%\Scripts\pip.exe"

:: ── 3. Installer les dépendances Python ──────────────────────
echo.
echo  [3/6] Installation des dépendances Python (requirements.txt)...

"%VENV_PIP%" install --upgrade pip --quiet
"%VENV_PIP%" install -r "%ROOT%\requirements.txt"
if !errorlevel! neq 0 (
    echo  [ERREUR] pip install a échoué. Vérifiez votre connexion internet.
    pause
    exit /b 1
)
echo  OK — dépendances Python installées.

:: ── 4. Détecter Node.js ───────────────────────────────────────
echo.
echo  [4/6] Vérification de Node.js et npm...

node --version >nul 2>&1
if !errorlevel! neq 0 (
    echo  [ERREUR] Node.js n'est pas installé ou pas dans le PATH.
    echo  Téléchargez Node.js LTS sur https://nodejs.org/
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('node --version 2^>^&1') do set NODE_VERSION=%%V
for /f "tokens=*" %%V in ('npm --version 2^>^&1') do set NPM_VERSION=%%V
echo  OK — Node.js !NODE_VERSION! / npm !NPM_VERSION!

:: ── 5. Installer et builder le frontend ──────────────────────
echo.
echo  [5/6] Installation des dépendances Node.js (frontend)...

cd /d "%ROOT%\frontend"

if not exist "node_modules" (
    npm install
    if !errorlevel! neq 0 (
        echo  [ERREUR] npm install a échoué.
        pause
        exit /b 1
    )
) else (
    echo  node_modules déjà présent — mise à jour...
    npm install --silent
)

echo.
echo  [5/6] Build du frontend React...
npm run build
if !errorlevel! neq 0 (
    echo  [ERREUR] npm run build a échoué.
    pause
    exit /b 1
)
echo  OK — frontend compilé dans frontend\dist\

:: ── 6. Créer le raccourci bureau ──────────────────────────────
echo.
echo  [6/6] Création du raccourci sur le bureau...

cd /d "%ROOT%"
cscript //nologo "%ROOT%\create_shortcut.vbs"
if !errorlevel! == 0 (
    echo  OK — raccourci "Agent Platform" créé sur le bureau.
) else (
    echo  (Optionnel ignoré — créez manuellement si besoin)
)

:: ── Résumé ────────────────────────────────────────────────────
echo.
echo  ========================================================
echo   Installation terminée avec succès!
echo  ========================================================
echo.
echo   Pour lancer l'application :
echo   - Double-cliquez sur le raccourci "Agent Platform" du bureau
echo   - OU lancez depuis CMD :  launch.bat
echo   - OU mode dev :           launch_dev.bat
echo.
echo   URL après lancement :   http://localhost:8000
echo   Documentation API :     http://localhost:8000/api/docs
echo.
pause
