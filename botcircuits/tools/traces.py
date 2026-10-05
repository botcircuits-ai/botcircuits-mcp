"""
MCP tools for runtime traces: what the app's agent actually did in a conversation.

Read-only, and the starting point for every "why did the bot do that?" question. The
backend (botcircuits-backend src/tracing) records a span per step of each turn and serves
three views of them, used here from broad to narrow:

  find_problem_sessions  sessions with errors (or just the recent ones)
  get_session_trace      one digest per turn: message, reply, outcome, steps, issues
  get_turn_trace         one turn's full span tree: model calls, tools, workflow steps

get_authoring_guide("troubleshooting") explains how to read them. Mirrors
botcircuits-agent-builder-copilot-native (copilot_tools/tools/trace_tools.py).
"""

from datetime import UTC, datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

MAX_SESSIONS = 50
MAX_LOOKBACK_HOURS = 24 * 30  # traces are kept for 30 days


def _session_row(session: dict) -> dict:
    row = {
        "session_id": session.get("sessionId"),
        "ref_id": session.get("refId"),
        "last_seen": session.get("lastSeen"),
        "turns": session.get("turnCount"),
        "errors": session.get("errorCount"),
        "last_turn": session.get("lastTurn"),
        "last_error": session.get("lastError"),
        "handled_by": session.get("handleBy"),
    }
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def find_problem_sessions(app_id: str, since_hours: int = 24, errors_only: bool = True,
                                    limit: int = 10) -> dict:
        """
        Find recent conversations with the app's agent, newest first.

        The starting point for troubleshooting when the user has no session id: by default
        only sessions where a step failed. Each row has the session id, its last user message
        and outcome, and its last error. Read get_authoring_guide("troubleshooting") first.

        Args:
            app_id: The agent / app ID.
            since_hours: How far back to look, in hours (max 720).
            errors_only: Only sessions where at least one step failed. Set false to list every recent
                session (e.g. to find a conversation by its last message).
            limit: Maximum sessions to return (max 50).
        """
        hours = min(max(int(since_hours), 1), MAX_LOOKBACK_HOURS)
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        result = await client.search_trace_sessions(
            app_id, status="error" if errors_only else None, since=since,
            limit=min(max(int(limit), 1), MAX_SESSIONS))
        sessions = [_session_row(s) for s in (result.get("data") or [])]
        extra = {} if sessions else {"hint": "No traced sessions matched. Widen since_hours or set errors_only=false."}
        # The backend scans a bounded number of index rows; `complete` says when it stopped short.
        return {"sessions": sessions, "count": len(sessions), "complete": result.get("complete", True), **extra}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_session_trace(app_id: str, session_id: str, last_turns: int = 10) -> dict:
        """
        Summarize what the agent did in one conversation, turn by turn, with detected issues.

        Each turn has the user's message, the agent's replies, the outcome (answered, workflow,
        waiting_for_input, blocked_by_guardrail, iteration_limit, error, ...), its key steps
        (model calls, tools, workflow steps, branch decisions) and issues. `issues` lists
        problems across the session: errors, stuck workflow slots, repeated tool calls, slow
        turns, unfinished steps.

        Args:
            app_id: The agent / app ID.
            session_id: The conversation's session id (from the user or find_problem_sessions).
            last_turns: How many of the latest turns to include (max 50).
        """
        summary = await client.get_trace_summary(app_id, session_id, min(max(int(last_turns), 1), 50))
        return {k: v for k, v in (summary or {}).items() if k != "appId"}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_turn_trace(app_id: str, session_id: str, message_id: str, full_detail: bool = False) -> dict:
        """
        Show one turn of a conversation as its full step tree, to pin down the root cause.

        Use after get_session_trace pointed at a turn. Each node has its kind (llm, tool,
        workflow_step, decision, retrieval, ...), status, duration, structured attrs (model,
        tokens, prompt excerpt and tool calls on llm nodes; state, transition and slots on
        workflow steps; the branch and the values it was decided on for decisions) and errors
        with their origin. Payloads are trimmed unless full_detail is true.

        Args:
            app_id: The agent / app ID.
            session_id: The conversation's session id.
            message_id: The turn's messageId from get_session_trace.
            full_detail: Return untrimmed step inputs/outputs. Large; only when the trimmed view is not enough.
        """
        tree = await client.get_turn_trace(app_id, session_id, message_id, full=bool(full_detail))
        return {k: v for k, v in (tree or {}).items() if k != "appId"}
