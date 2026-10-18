# One container: FalkorDB + the Lore API/UI/MCP server. The graph is rebuilt
# from the HERB files at startup (about 5 seconds), so the image is stateless.
FROM falkordb/falkordb:v4.22.1

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY lore ./lore
COPY web ./web
COPY data-derived ./data-derived
# HERB is downloaded at build time from Hugging Face (CC-BY-NC-4.0, Salesforce Research).
COPY scripts/fetch_herb.sh ./scripts/
RUN sh scripts/fetch_herb.sh /app/data-raw/herb

COPY scripts/start.sh ./scripts/
ENV PORT=7860 FALKORDB_HOST=127.0.0.1 FALKORDB_PORT=6379 \
    FALKORDB_ARGS="MAX_QUEUED_QUERIES 50 TIMEOUT_DEFAULT 15000 TIMEOUT_MAX 60000 RESULTSET_SIZE 50000"
RUN chmod -R a+rwX /app /var/lib/falkordb
EXPOSE 7860
ENTRYPOINT []
CMD ["sh", "/app/scripts/start.sh"]
