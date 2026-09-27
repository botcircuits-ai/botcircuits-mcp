from mcp.server.fastmcp import FastMCP

from . import agent_tools, applications, codehooks, knowledge, mcp_servers, playbooks, skills, sub_agents, workflows


def register_all(mcp: FastMCP) -> None:
    applications.register(mcp)
    workflows.register(mcp)      # includes recommend_implementation
    playbooks.register(mcp)
    agent_tools.register(mcp)
    codehooks.register(mcp)
    sub_agents.register(mcp)
    skills.register(mcp)
    mcp_servers.register(mcp)
    knowledge.register(mcp)
