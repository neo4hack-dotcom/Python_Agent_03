#!/bin/bash
# Start backend and frontend in development mode (hot reload)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔧 Mode développement"
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
    echo "❌ Python 3 est introuvable. Installez-le depuis https://www.python.org/downloads/"
    exit 1
fi

# ── Utiliser le venv si disponible ───────────────────────────
if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
    echo "✅ Utilisation du venv : .venv"
else
    echo "⚠️  Aucun venv trouvé, utilisation de : $PYTHON_CMD"
    echo "   → Conseil : créez un venv : python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
fi

# ── Installer les dépendances si nécessaire ───────────────────
"$PYTHON_CMD" -c "import fastapi" 2>/dev/null || {
    echo "📦 Installation des dépendances Python..."
    "$PYTHON_CMD" -m pip install -r "$ROOT_DIR/requirements.txt" -q
}

# ── Backend ───────────────────────────────────────────────────
echo ""
echo "▶ Backend FastAPI sur http://localhost:8000"
cd "$ROOT_DIR"
"$PYTHON_CMD" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# ── Frontend ──────────────────────────────────────────────────
echo "▶ Frontend Vite sur http://localhost:3000"
cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "🌐 URLs:"
echo "   → Frontend: http://localhost:3000"
echo "   → API Docs:  http://localhost:8000/api/docs"
echo ""
echo "Ctrl+C pour arrêter les deux serveurs"

trap "echo ''; echo 'Arrêt...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
