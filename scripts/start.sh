#!/bin/sh
# Start FalkorDB, rebuild the graph from HERB, then serve the API/UI/MCP.
set -e
DATA=${FALKORDB_DATA:-/tmp/falkordb-data}
mkdir -p "$DATA"
# shellcheck disable=SC2086
redis-server --protected-mode no --bind 127.0.0.1 --port 6379 --dir "$DATA" --save "" \
  --loadmodule /var/lib/falkordb/bin/falkordb.so ${FALKORDB_ARGS} &
until redis-cli -p 6379 ping >/dev/null 2>&1; do sleep 0.3; done
cd /app
/opt/venv/bin/python -m lore.load data-raw/herb
exec /opt/venv/bin/uvicorn lore.api:app --host 0.0.0.0 --port "${PORT:-7860}"
