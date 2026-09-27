"""MCP tools against an in-memory BotCircuits API (tests/fakes.py)."""

import pytest

from botcircuits import client
from botcircuits.config import settings
from botcircuits.server import mcp
from botcircuits.workflow.complexity import recommend
from botcircuits.workflow.transform import transform_to_platform_format

from .fakes import API, FakeBotCircuits


@pytest.fixture
def fake(monkeypatch):
    backend = FakeBotCircuits()
    monkeypatch.setattr(client, "_transport", backend.transport())
    monkeypatch.setattr(client, "JOURNEY_POLL_SECONDS", 0)
    monkeypatch.setattr(settings, "api_base_url", API)
    monkeypatch.setattr(settings, "access_token", backend.valid_token)
    return backend


async def call(tool_name, /, **args):
    return await mcp._tool_manager.get_tool(tool_name).fn(**args)


def _send(text):
    return {"kind": "SEND", "config": {"displayTextOptions": [{"displayText": text}]}}


PLAYBOOK = {"sections": [{"title": "Order", "steps": [
    {"kind": "ASK", "config": {"slot": "order_id", "displayTextOptions": [{"displayText": "Order number?"}]}},
    {"kind": "RUN", "config": {"target": "FUNCTION", "codehookId": "api_get_order"}},
    {"kind": "IF", "branches": [
        {"conditionType": "expression", "expressionList": [{"variable": "order_status", "operator": "is",
                                                            "value": "shipped"}], "steps": [_send("Shipped!")]},
        {"conditionType": "else", "steps": [_send("Status: {order_status}")]},
    ]},
]}]}
VARIABLES = {"order_id": {"dataType": "custom", "content": "order number like ORD-1", "captureFromUserInput": True},
             "order_status": {"dataType": "any", "captureFromUserInput": False}}


def _intermediate(n_steps, payload_name="billing"):
    states = {"start": {"type": "StartNode", "next": "ask", "stateConfig": {"stateType": "start", "next": "ask"}},
              "ask": {"type": "buttonsPrompt", "next": "route", "stateConfig": {
                  "stateType": "prompt", "type": "buttons", "name": "Ask", "slot": "issue",
                  "displayTextOptions": ["What do you need?"],
                  "data": [{"title": "Billing", "payload": payload_name, "actionType": "set_value"}]}},
              "route": {"type": "choice", "next": None, "stateConfig": {
                  "stateType": "choice", "name": "Route",
                  "choices": [{"operator": "AND", "expressionList": [{"variable": "issue", "operator": "is",
                                                                      "value": "billing"}], "next": "billing"}],
                  "next": "s0"}},
              "billing": {"type": "messagePrompt", "next": None, "stateConfig": {
                  "stateType": "message", "type": "text", "name": "Billing", "displayTextOptions": ["Billing!"]}}}
    for i in range(n_steps):
        states[f"s{i}"] = {"type": "aiTask", "next": f"s{i + 1}" if i + 1 < n_steps else None, "stateConfig": {
            "stateType": "action", "type": "setVariable", "name": f"Set {i}", "slotToAssign": f"v{i}", "value": str(i)}}
    return {"stmDefinition": {"startAt": "start", "states": states},
            "slots": {"issue": {"slot": "issue", "dataType": "values", "content": "billing,technical",
                                "captureFromUserInput": True}}}


# ---------------------------------------------------------------- playbooks

async def test_create_playbook_full_flow(fake):
    fake.codehooks["api_get_order"] = {"codehookId": "api_get_order"}
    result = await call("create_playbook", app_id="app1", name="Order status",
                        description="Use when a customer asks where their order is.",
                        playbook=PLAYBOOK, variables=VARIABLES)
    assert result["ok"], result
    wid = result["workflowId"]
    assert fake.actions[wid]["authoringMode"] == "playbook"
    meta = fake.journeys[wid]["stm"]["metadata"]
    assert meta["authoringMode"] == "playbook" and meta["nodes"] == [] and meta["playbook"]["sections"]
    stm = fake.journeys[wid]["stm"]["stmDefinition"]
    start = stm["states"][stm["startAt"]]
    assert start["next"] == start["stateConfig"]["next"]
    assert fake.journeys[wid]["slots"]["order_id"]["dataType"] == "custom"
    assert result["agentTool"] == {"registered": False}  # only when the user chooses to connect it
    assert fake.collections["tools"] == {}

    read = await call("get_playbook", app_id="app1", workflow_id=wid)
    assert read["playbook"]["sections"][0]["title"] == "Order"

    updated = await call("update_playbook", app_id="app1", workflow_id=wid, playbook=read["playbook"],
                         expose_as_agent_tool=True)
    assert updated["ok"] and updated["agentTool"]["registered"]
    tool = fake.collections["tools"][f"playbook-{wid}"]
    assert tool["managedByPlaybook"] == wid and tool["toolData"] == {"workflowId": wid}


async def test_invalid_playbook_is_not_saved(fake):
    result = await call("create_playbook", app_id="app1", name="Broken", description="Use when testing.",
                        playbook=PLAYBOOK, variables={})
    assert result["ok"] is False and result["saved"] is False
    joined = " ".join(result["errors"])
    assert "codehook 'api_get_order' does not exist" in joined
    assert "ASK variable 'order_id' has no definition" in joined
    assert fake.actions == {}


# ---------------------------------------------------------------- workflows

async def test_small_new_workflow_is_redirected_to_playbook(fake):
    result = await call("upload_workflow", app_id="app1", name="Small", description="Use for a small flow.",
                        intermediate=_intermediate(25))
    assert result["ok"] is False and result["policy"] == "use_playbook"
    assert "30 steps" in result["error"] or "> 30" in result["error"]
    assert fake.actions == {}


async def test_large_workflow_saves_with_round_trippable_layout(fake):
    result = await call("upload_workflow", app_id="app1", name="Big", description="Use for the big flow.",
                        intermediate=_intermediate(30))
    assert result["ok"], result
    stm = fake.journeys[result["workflowId"]]["stm"]
    states = stm["stmDefinition"]["states"]
    choice_id, choice = next((k, s) for k, s in states.items() if s["stateConfig"]["stateType"] == "choice")
    handles = [e["sourceHandle"] for e in stm["metadata"]["edges"]]
    # The canvas parses the *state* id from these handles.
    assert f"source-condition-{choice_id}-1-right" in handles
    assert f"source-else-condition-{choice_id}-right" in handles
    assert choice["stateConfig"]["choices"][0]["id"] == 1
    assert "next" not in choice  # else branch lives on stateConfig
    # A button payload equal to a state id must not be rewritten to a numeric id.
    buttons = next(s for s in states.values() if s["stateConfig"].get("type") == "buttons")
    assert buttons["stateConfig"]["data"][0]["payload"] == "billing"
    assert fake.actions[result["workflowId"]]["authoringMode"] == "workflow"


def test_legacy_option_slot_is_rewritten():
    wf = _intermediate(1)
    wf["stmDefinition"]["states"]["ask"]["stateConfig"]["slot"] = "bc_workflow_option"
    out = transform_to_platform_format(wf)
    ask = next(s for s in out["stm"]["stmDefinition"]["states"].values() if s["stateConfig"].get("type") == "buttons")
    assert ask["stateConfig"]["slot"] == "workflow_option"


async def test_validate_workflow_reports_runtime_failures(fake):
    wf = _intermediate(1)
    wf["stmDefinition"]["states"]["ask"]["stateConfig"]["type"] = "date"
    wf["stmDefinition"]["states"]["s0"]["next"] = "missing_state"
    result = await call("validate_workflow_definition", app_id="app1", intermediate=wf)
    joined = " ".join(result["errors"])
    assert not result["valid"] and "not implemented" in joined and "missing_state" in joined


async def test_delete_workflow_removes_action_and_tools(fake):
    fake.codehooks["api_get_order"] = {"codehookId": "api_get_order"}
    created = await call("create_playbook", app_id="app1", name="Order", description="Use for order status.",
                         playbook=PLAYBOOK, variables=VARIABLES, expose_as_agent_tool=True)
    wid = created["workflowId"]
    result = await call("delete_workflow", app_id="app1", workflow_id=wid)
    assert wid not in fake.actions and wid not in fake.journeys
    assert result["removedAgentTools"] and fake.collections["tools"] == {}


# ---------------------------------------------------------------- other resources

async def test_core_settings_update_preserves_stored_fields(fake):
    fake.settings = {"authConfig": {"provider": "oauth"}, "kbTopResults": 7, "botLanguage": "english"}
    await call("update_application_core_settings", app_id="app1", default_error_message="Sorry!")
    assert fake.settings["authConfig"] == {"provider": "oauth"} and fake.settings["kbTopResults"] == 7
    assert fake.settings["defaultErrorMessage"] == "Sorry!"


async def test_agent_tools_skills_and_mcp_servers(fake):
    with pytest.raises(ValueError, match="existing codehook"):
        await call("create_agent_tool", app_id="app1", name="get_order", description="Look up an order.",
                   tool_type="function", tool_data={"functionId": "nope"})
    skill = await call("create_skill", app_id="app1", name="process-refund",
                       description="Use when a customer asks for a refund.",
                       body="1. Ask for the order. 2. Call refund_order. 3. Confirm.")
    assert skill["runtimeName"] == "skill_process-refund" and fake.collections["skills"]
    with pytest.raises(ValueError, match="slug"):
        await call("create_skill", app_id="app1", name="Bad Name", description="Use for bad names.",
                   body="A body that is long enough.")
    server = await call("create_mcp_server", app_id="app1", name="crm", url="https://crm.test/mcp",
                        authorization_token="secret-123")
    await call("update_mcp_server", app_id="app1", server_id=server["id"], changes={"allowedTools": ["a"]})
    assert fake.collections["mcp-servers"][server["id"]]["authorizationToken"] == "secret-123"


async def test_create_api_tool(fake):
    result = await call("create_api_tool", app_id="app1", name="get_weather",
                        description="Get the current weather for a city.", method="GET",
                        url="https://weather.test/v1/current", query={"q": "{city}"},
                        headers={"X-Api-Key": "{env.WEATHER_KEY}"},
                        parameters={"city": {"type": "string", "description": "City", "required": True}},
                        environment={"WEATHER_KEY": "abc"})
    assert fake.codehooks["api_get_weather"]["status"] == "deployed"
    tool = next(t for t in fake.collections["tools"].values() if t["name"] == "get_weather")
    assert tool["toolData"] == {"functionId": "api_get_weather"} and result["toolId"]
    with pytest.raises(ValueError, match="NEWS_TOKEN"):
        await call("create_api_tool", app_id="app1", name="get_news", description="Get the latest news.",
                   method="GET", url="https://news.test", headers={"Authorization": "Bearer {env.NEWS_TOKEN}"},
                   parameters={})


def test_policy_thresholds_are_30_steps_and_20_conditions():
    base = dict(summary="x", multi_turn_input=True, must_follow_fixed_order=True, single_action=False,
                guidance_only=False)
    assert recommend(**base, estimated_steps=30, decision_points=20)["recommendation"] == "playbook"
    assert recommend(**base, estimated_steps=31, decision_points=0)["recommendation"] == "workflow"
    assert recommend(**base, estimated_steps=10, decision_points=21)["recommendation"] == "workflow"
    single = {**base, "multi_turn_input": False, "must_follow_fixed_order": False, "single_action": True}
    assert recommend(**single, estimated_steps=1, decision_points=0)["recommendation"] == "tool"


async def test_guides_are_served():
    guide = await call("get_authoring_guide", topic="workflow")
    assert "workflow_option" in guide and "bc_workflow_option" not in guide
    assert "more than\n   30 steps" in mcp.instructions or "30 steps" in mcp.instructions
