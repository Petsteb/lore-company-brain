"""Optional LLM front end. Claude picks tools from the fixed catalogue and writes
the final sentence; it never sees or writes Cypher. Every id the reply names must
have come back from a tool, otherwise the reply is replaced by the tools' own
summaries. Without ANTHROPIC_API_KEY the app still works through the tool
console and the MCP server (where the MCP client is the agent)."""
from __future__ import annotations

import inspect
import json
import os
import re

from .tools import TOOLS, call

SYSTEM = """You are Lore, a company brain over a FalkorDB knowledge graph of one company's people, products,
components, issues, customers, documents, meetings and decisions. Answer only from tool results.
Rules:
- Use the tools; never guess a name or id. If a tool returns ambiguous_entity, ask the user which candidate
  they mean (list them with id and role) instead of picking one.
- If a tool returns not_in_graph or unknown_entity, say plainly that the graph does not record it.
- Cite people as "Name (eid_...)" and customers by CUST id, exactly as tools returned them.
- Keep the answer under 120 words. The UI already shows paths and sources, so do not repeat them in full.
- Never call apply_action unless the user explicitly says they approve a specific action id."""

ID = re.compile(r"\beid_[0-9a-f]{8}\b|\bCUST-\d{4}\b")
MODEL = os.getenv("LORE_MODEL", "claude-opus-5-5")


def tool_schemas() -> list[dict]:
    out = []
    for name, fn in TOOLS.items():
        props, required = {}, []
        for p in inspect.signature(fn).parameters.values():
            props[p.name] = {"type": "integer" if p.annotation in (int, "int") else "string"}
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        out.append({"name": name, "description": inspect.getdoc(fn) or name,
                    "input_schema": {"type": "object", "properties": props, "required": required}})
    return out


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def ask(question: str, history: list | None = None, max_steps: int = 6) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    messages = list(history or []) + [{"role": "user", "content": question}]
    steps = []
    response = None
    for _ in range(max_steps):
        response = client.messages.create(model=MODEL, max_tokens=16000, system=SYSTEM,
                                          tools=tool_schemas(), messages=messages)
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            res = call(block.name, dict(block.input))
            steps.append(res)
            slim = {k: res[k] for k in ("outcome", "summary", "rows", "candidates")}
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(slim, default=str)[:12000]})
        messages.append({"role": "user", "content": results})
    text = "".join(b.text for b in (response.content if response else []) if b.type == "text").strip()
    known = set(ID.findall(json.dumps(steps, default=str)))
    invented = sorted(set(ID.findall(text)) - known)
    checked = "ok"
    if invented:
        checked = f"rejected: reply named ids no tool returned {invented}"
        text = " ".join(s["summary"] for s in steps) or "No answer."
    return {"answer": text, "check": checked, "steps": steps, "model": MODEL,
            "stop_reason": response.stop_reason if response else None}
