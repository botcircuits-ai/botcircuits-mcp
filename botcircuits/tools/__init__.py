from mcp.server.fastmcp import FastMCP
from . import applications, sub_agents, workflows, workflow_schema, skills


def register_all(mcp: FastMCP) -> None:
    applications.register(mcp)
    sub_agents.register(mcp)
    workflows.register(mcp)
    workflow_schema.register(mcp)
    skills.register(mcp)
