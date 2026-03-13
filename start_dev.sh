#!/bin/bash
# Start backend and frontend in development mode (hot reload)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔧 Mode développement"
echo ""

# Backend in background
echo "▶ Backend FastAPI sur http://localhost:8000"
cd "$ROOT_DIR"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Frontend dev server
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

# Cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
