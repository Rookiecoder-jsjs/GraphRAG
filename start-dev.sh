#!/bin/bash
# Start both frontend and backend for NC Knowledge Graph System

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "Starting NC Knowledge Graph System..."
echo ""

# Function to handle cleanup on exit
cleanup() {
    echo ""
    echo "Stopping services..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start backend
echo "[1/2] Starting Backend (port 8001)..."
cd "$BACKEND_DIR"
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

sleep 2

# Start frontend
echo "[2/2] Starting Frontend (port 5173)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "Services started:"
echo "  - Backend: http://localhost:8001"
echo "  - Frontend: http://localhost:5173"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for both processes
wait
