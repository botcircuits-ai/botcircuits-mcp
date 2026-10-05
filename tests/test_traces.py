"""Trace tools: finding problem sessions and reading their traces (backend src/tracing)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from botcircuits import client
from botcircuits.config import settings
from botcircuits.server import mcp

from .fakes import API, FakeBotCircuits


@pytest.fixture
def fake(monkeypatch):
    backend = FakeBotCircuits()
    monkeypatch.setattr(client, "_transport", backend.transport())
    monkeypatch.setattr(settings, "api_base_url", API)
    monkeypatch.setattr(settings, "access_token", backend.valid_token)
    return backend


async def call(tool_name, /, **args):
    return await mcp._tool_manager.get_tool(tool_name).fn(**args)


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def test_find_problem_sessions_filters_and_projects(fake):
    fake.trace_sessions = [
        {"sessionId": "s1", "lastSeen": _ago(1), "errorCount": 2, "turnCount": 3, "refId": "R-1",
         "lastError": {"span": "Using a tool", "error": "boom"}, "spanCount": 40},
        {"sessionId": "s2", "lastSeen": _ago(2), "errorCount": 0, "turnCount": 1},
        {"sessionId": "s3", "lastSeen": _ago(100), "errorCount": 5, "turnCount": 1},
    ]

    result = await call("find_problem_sessions", app_id="app1")
    assert [s["session_id"] for s in result["sessions"]] == ["s1"]
    assert result["sessions"][0] == {"session_id": "s1", "ref_id": "R-1", "last_seen": fake.trace_sessions[0]["lastSeen"],
                                     "turns": 3, "errors": 2, "last_error": {"span": "Using a tool", "error": "boom"}}
    assert fake.last_query["status"] == "error" and result["complete"] is True

    every = await call("find_problem_sessions", app_id="app1", errors_only=False, since_hours=200)
    assert [s["session_id"] for s in every["sessions"]] == ["s1", "s2", "s3"]
    assert "status" not in fake.last_query


async def test_find_problem_sessions_clamps_and_hints_when_empty(fake):
    result = await call("find_problem_sessions", app_id="app1", since_hours=1, limit=500)
    assert result["count"] == 0 and "Widen" in result["hint"]
    assert fake.last_query["limit"] == "50"


async def test_session_trace_passes_through_the_summary(fake):
    fake.trace_summaries["s1"] = {"turnCount": 1, "issues": [{"type": "tool_error", "messageId": "m1"}],
                                  "turns": [{"messageId": "m1", "outcome": "answered"}]}
    result = await call("get_session_trace", app_id="app1", session_id="s1", last_turns=5)
    assert result["issues"][0]["type"] == "tool_error" and "appId" not in result
    assert fake.last_query == {"lastTurns": "5"}
    with pytest.raises(client.NotFoundError):
        await call("get_session_trace", app_id="app1", session_id="missing")


async def test_turn_trace_trimmed_unless_full_detail(fake):
    fake.trace_turns[("s1", "m1")] = {"messageId": "m1", "tree": [{"kind": "llm", "status": "ok"}]}
    trimmed = await call("get_turn_trace", app_id="app1", session_id="s1", message_id="m1")
    assert trimmed["tree"][0]["kind"] == "llm" and trimmed["detail"] == "trimmed" and "appId" not in trimmed
    full = await call("get_turn_trace", app_id="app1", session_id="s1", message_id="m1", full_detail=True)
    assert full["detail"] == "full"


async def test_trace_ids_cannot_escape_the_app_path(fake):
    with pytest.raises(ValueError, match="session_id"):
        await call("get_session_trace", app_id="app1", session_id="../other")
    with pytest.raises(ValueError, match="message_id"):
        await call("get_turn_trace", app_id="app1", session_id="s1", message_id="a/b")
