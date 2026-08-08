#!/usr/bin/env bash
# Backup SQLite + Neo4j + ChromaDB + uploads to a timestamped tarball.
#
# Usage:
#   ./scripts/backup.sh                       # uses ./data, outputs to ./data/backups
#   DATA_DIR=/var/lib/kg OUT_DIR=/tmp bk ./scripts/backup.sh
#
# Runs `sqlite3 .backup` first so the DB is copied consistently while the
# server is still running (no need to stop it). Neo4j/Chroma/uploads are
# tarred directly - they're append-mostly and tolerate a live tar, but for
# a guaranteed-consistent snapshot stop the services first.
set -euo pipefail

DATA_DIR="${DATA_DIR:-./data}"
OUT_DIR="${OUT_DIR:-./data/backups}"
SQLITE_PATH="${SQLITE_PATH:-$DATA_DIR/sqlite/app.db}"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$OUT_DIR/kg-backup-$TS.tar.gz"

mkdir -p "$OUT_DIR"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "ERROR: sqlite3 CLI not found. Install it or run on the host that has it." >&2
  exit 1
fi

# Online backup (safe while the server is running).
sqlite3 "$SQLITE_PATH" ".backup '$SQLITE_PATH.bak'"

# tar the snapshot + the other stores. Ignore missing dirs (e.g. fresh
# install with no uploads yet) instead of failing.
tar -czf "$OUT" \
  -C "$DATA_DIR" \
  sqlite/app.db.bak \
  neo4j/data chromadb uploads 2>/dev/null || true

rm -f "$SQLITE_PATH.bak"
echo "Backup written to $OUT"
echo "Restore: tar -xzf $OUT -C \$DATA_DIR"
