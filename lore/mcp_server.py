"""The tool catalogue as an MCP server. Any MCP client (Claude Code, Claude
Desktop, ...) becomes the agent: it picks tools and fills arguments; the
Cypher stays here.  stdio:  python -m lore.mcp_server   HTTP: mounted at /mcp by lore.api"""
from __future__ import annotations

import functools
import inspect

from mcp.server import MCPServer

from .tools import TOOLS, call

mcp = MCPServer(
    name="lore",
    instructions="Company brain over a FalkorDB graph. Resolve names with `find` when unsure. "
                 "ambiguous_entity means ask the user; not_in_graph means the graph does not record it. "
                 "plan_handoff only drafts; apply_action needs explicit human approval.",
)


def _wrap(name, fn):
    @functools.wraps(fn)
    def tool(**kwargs):
        return call(name, kwargs)

    tool.__signature__ = inspect.signature(fn).replace(return_annotation=dict)
    return tool


for _name, _fn in TOOLS.items():
    mcp.add_tool(_wrap(_name, _fn), name=_name, description=inspect.getdoc(_fn))


if __name__ == "__main__":
    mcp.run("stdio")
