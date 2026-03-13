#!/bin/bash
# Start the Python Agent Platform (backend + frontend)

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Python Agent Platform"
echo "========================"

# Backend setup
echo "📦 Installation des dépendances Python..."
cd "$ROOT_DIR"
pip install -r requirements.txt -q

# Frontend setup
echo "📦 Installation des dépendances Node.js..."
cd "$ROOT_DIR/frontend"
if [ ! -d node_modules ]; then
  npm install --silent
fi

# Build frontend
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
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
