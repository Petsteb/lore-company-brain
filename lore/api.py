"""HTTP front end: the tool console API, the optional agent, the MCP endpoint and the web UI.
Run:  uvicorn lore.api:app --port 8000"""
from __future__ import annotations

import contextlib
import inspect
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent
from .db import graph
from .mcp_server import mcp
from .tools import TOOLS, call

WEB = Path(__file__).resolve().parents[1] / "web"
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="0.0.0.0")


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Lore — company brain on FalkorDB", lifespan=lifespan)


class ToolCall(BaseModel):
    args: dict = {}


class Ask(BaseModel):
    question: str
    history: list = []


@app.get("/api/tools")
def tools():
    out = []
    for name, fn in TOOLS.items():
        params = [{"name": p.name, "type": getattr(p.annotation, "__name__", str(p.annotation)),
                   "default": None if p.default is inspect.Parameter.empty else p.default,
                   "required": p.default is inspect.Parameter.empty}
                  for p in inspect.signature(fn).parameters.values()]
        out.append({"name": name, "doc": inspect.getdoc(fn), "params": params})
    return out


@app.post("/api/tool/{name}")
def run_tool(name: str, body: ToolCall):
    return call(name, body.args)


@app.post("/api/ask")
def ask(body: Ask):
    if not agent.available():
        return {"answer": None, "check": "disabled",
                "error": "No ANTHROPIC_API_KEY on this deployment. Use the tool console, or connect an MCP client to /mcp."}
    return agent.ask(body.question, body.history)


@app.get("/api/stats")
def stats():
    g = graph()
    nodes = g.ro_query("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC").result_set
    rels = g.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return {"nodes": dict(nodes), "relationships": rels, "agent": agent.available(), "model": agent.MODEL}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", mcp_app)
