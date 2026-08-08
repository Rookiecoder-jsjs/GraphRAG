#!/bin/bash
# Start both frontend and backend for the Knowledge Graph System.
# Cross-platform: macOS / Linux / Git Bash (Windows).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "Starting Knowledge Graph System..."
echo ""

# ---- Locate the venv python (repo root / parent / backend; bin or Scripts) ----
PYTHON=""
for base in "$SCRIPT_DIR" "$SCRIPT_DIR/.." "$BACKEND_DIR"; do
    if   [ -x "$base/.venv/bin/python" ];         then PYTHON="$base/.venv/bin/python";         break
    elif [ -x "$base/.venv/Scripts/python.exe" ]; then PYTHON="$base/.venv/Scripts/python.exe"; break
    fi
done
if [ -z "$PYTHON" ]; then
    if   command -v python3 >/dev/null 2>&1; then PYTHON="python3"
    elif command -v python  >/dev/null 2>&1; then PYTHON="python"
    else
        echo "ERROR: No Python found. Create a venv at .venv (project root or its parent) or install python3."
        exit 1
    fi
    echo "WARN: No .venv found, using system '$PYTHON'."
fi
echo "Using Python: $PYTHON"

# ---- Cleanup both services on exit ----
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
    echo ""
    echo "Stopping services..."
    [ -n "$BACKEND_PID" ]  && kill "$BACKEND_PID"  2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# ---- Backend ----
echo "[1/2] Starting Backend (port 8001)..."
cd "$BACKEND_DIR" || exit 1
"$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

sleep 2

# ---- Frontend ----
echo "[2/2] Starting Frontend (port 5173)..."
cd "$FRONTEND_DIR" || exit 1
npm run dev &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "Services started:"
echo "  - Backend:  http://localhost:8001"
echo "  - Frontend: http://localhost:5173"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop all services."

wait
