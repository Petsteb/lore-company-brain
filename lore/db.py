import os

from falkordb import FalkorDB

_db = None


def client() -> FalkorDB:
    global _db
    if _db is None:
        _db = FalkorDB(
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", "6379")),
            password=os.getenv("FALKORDB_PASSWORD") or None,
        )
    return _db


def graph(name: str | None = None):
    return client().select_graph(name or os.getenv("LORE_GRAPH", "company"))
