#!/bin/bash
# Start the Python Agent Platform (production mode: build frontend + launch server)

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Python Agent Platform"
echo "========================"
echo ""

# ── Détecter Python 3 ─────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python py; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$version" = "3" ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Python 3 introuvable. Installez-le depuis https://www.python.org/downloads/"
    exit 1
fi

# ── Utiliser le venv si disponible ───────────────────────────
if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
    echo "✅ Utilisation du venv : .venv"
fi

# ── Installer les dépendances Python ─────────────────────────
echo "📦 Installation des dépendances Python..."
"$PYTHON_CMD" -m pip install -r "$ROOT_DIR/requirements.txt" -q

# ── Frontend ──────────────────────────────────────────────────
echo "📦 Installation des dépendances Node.js..."
cd "$ROOT_DIR/frontend"
if [ ! -d node_modules ]; then
    npm install --silent
fi

echo "🔨 Build du frontend React..."
npm run build

echo ""
echo "✅ Build terminé!"
echo ""
echo "🌐 Démarrage du serveur FastAPI..."
echo "   → API:      http://localhost:8000/api/docs"
echo "   → Frontend: http://localhost:8000"
echo ""

cd "$ROOT_DIR"
"$PYTHON_CMD" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
