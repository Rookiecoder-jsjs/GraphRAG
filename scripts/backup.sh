#!/usr/bin/env bash
# Backup SQLite + Neo4j + ChromaDB + uploads to a timestamped tarball.
#
# Usage:
#   ./scripts/backup.sh                       # uses backend/data, outputs to backend/data/backups
#   DATA_DIR=/var/lib/kg OUT_DIR=/tmp ./scripts/backup.sh
#
# Runs `sqlite3 .backup` first so the DB is copied consistently while the
# server is still running (no need to stop it). Neo4j/Chroma/uploads are
# tarred directly - they're append-mostly and tolerate a live tar, but for
# a guaranteed-consistent snapshot stop the services first.
#
# The app resolves its relative data paths against the backend/ working
# directory (run-backend.mjs / start-dev.* all cd into backend), so the REAL
# data root is backend/data - NOT the repo-root ./data (which only ever held
# a 0-byte stub). DATA_DIR is anchored to this script's location so the
# documented default invocation backs up real data, and the SQLite check
# below refuses to "succeed" on a missing/empty database.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/backend/data}"
OUT_DIR="${OUT_DIR:-$DATA_DIR/backups}"
SQLITE_PATH="${SQLITE_PATH:-$DATA_DIR/sqlite/app.db}"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$OUT_DIR/kg-backup-$TS.tar.gz"

mkdir -p "$OUT_DIR"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "ERROR: sqlite3 CLI not found. Install it or run on the host that has it." >&2
  exit 1
fi

# Refuse to back up a missing or empty database: the old default pointed at
# the repo-root stub (0 bytes) and "succeeded" while capturing nothing.
if [ ! -f "$SQLITE_PATH" ]; then
  echo "ERROR: SQLite database not found at $SQLITE_PATH" >&2
  echo "  Set DATA_DIR (or SQLITE_PATH) if your data lives elsewhere." >&2
  exit 1
fi
if [ ! -s "$SQLITE_PATH" ]; then
  echo "ERROR: SQLite database at $SQLITE_PATH is empty (0 bytes)." >&2
  echo "  Refusing to back up an empty database. Is DATA_DIR pointing at the right data root?" >&2
  exit 1
fi

# Online backup (safe while the server is running).
sqlite3 "$SQLITE_PATH" ".backup '$SQLITE_PATH.bak'"
# sqlite3 .backup can exit 0 while writing nothing; verify the snapshot.
if [ ! -s "$SQLITE_PATH.bak" ]; then
  echo "ERROR: sqlite3 .backup produced an empty snapshot of $SQLITE_PATH" >&2
  rm -f "$SQLITE_PATH.bak"
  exit 1
fi

# tar the snapshot + the other stores. Missing dirs (e.g. fresh install with
# no uploads yet) are tolerated per-directory, but a failure to create the
# archive itself must not be swallowed.
TAR_ITEMS=()
for d in sqlite neo4j/data chromadb uploads; do
  if [ -e "$DATA_DIR/$d" ]; then
    TAR_ITEMS+=("$d")
  else
    echo "WARNING: $DATA_DIR/$d missing - skipping" >&2
  fi
done
tar -czf "$OUT" -C "$DATA_DIR" "${TAR_ITEMS[@]}"

rm -f "$SQLITE_PATH.bak"

if [ ! -s "$OUT" ]; then
  echo "ERROR: backup archive $OUT is empty" >&2
  exit 1
fi

echo "Backup written to $OUT"
echo "Restore: tar -xzf $OUT -C \$DATA_DIR"
