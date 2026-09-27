"""A playbook/workflow is started by at most ONE main-agent tool record."""

import pytest

from botcircuits.tools import journeys

from .test_tools import PLAYBOOK, VARIABLES, call, fake  # noqa: F401  (fixture)

DESCRIPTION = "Use when a customer asks where their order is."


def _linked(fake, workflow_id):  # noqa: F811
    return [t for t in fake.collections["tools"].values() if (t.get("toolData") or {}).get("workflowId") == workflow_id]


async def _playbook(fake, expose=False):  # noqa: F811
    fake.codehooks["api_get_order"] = {"codehookId": "api_get_order"}
    result = await call("create_playbook", app_id="app1", name="Order status", description=DESCRIPTION,
                        playbook=PLAYBOOK, variables=VARIABLES, expose_as_agent_tool=expose)
    assert result["ok"], result
    return result["workflowId"]


async def test_expose_flag_then_create_agent_tool_keeps_one_record(fake):  # noqa: F811
    wid = await _playbook(fake, expose=True)
    again = await call("create_agent_tool", app_id="app1", name="order_status", description=DESCRIPTION,
                       tool_type="workflow", tool_data={"workflowId": wid})
    tools = _linked(fake, wid)
    assert len(tools) == 1 and tools[0]["id"] == f"playbook-{wid}" == again["toolId"]


async def test_create_agent_tool_for_a_playbook_uses_the_console_managed_record(fake):  # noqa: F811
    # The console recreates playbook-<id> when the playbook is opened; any other id would become a duplicate.
    wid = await _playbook(fake)
    result = await call("create_agent_tool", app_id="app1", name="order_status", description=DESCRIPTION,
                        tool_type="workflow", tool_data={"workflowId": wid})
    tools = _linked(fake, wid)
    assert len(tools) == 1 and tools[0]["id"] == f"playbook-{wid}" and tools[0]["managedByPlaybook"] == wid
    assert "naming convention" in result["note"]
    await call("create_agent_tool", app_id="app1", name="order_status", description=DESCRIPTION,
               tool_type="workflow", tool_data={"workflowId": wid})
    assert len(_linked(fake, wid)) == 1


async def test_existing_duplicates_are_consolidated(fake):  # noqa: F811
    wid = await _playbook(fake, expose=True)
    fake.collections["tools"]["dup1"] = {"id": "dup1", "name": "order_status", "type": "workflow",
                                         "description": DESCRIPTION, "toolData": {"workflowId": wid}}
    result = await call("update_playbook", app_id="app1", workflow_id=wid, playbook=PLAYBOOK, expose_as_agent_tool=True)
    assert [t["id"] for t in _linked(fake, wid)] == [f"playbook-{wid}"]
    assert result["agentTool"]["removedDuplicates"] == [{"id": "dup1", "name": "order_status"}]


async def test_canvas_workflow_tool_is_reused(fake):  # noqa: F811
    fake.actions["wf1"] = {"id": "wf1", "name": "Loan", "actionType": "workflow"}
    fake.journeys["wf1"] = {"journeyId": "wf1", "slots": {}, "stm": {"metadata": {"nodes": [{}, {}]}}}
    for _ in range(2):
        await call("create_agent_tool", app_id="app1", name="loan_application",
                   description="Use when a customer wants to apply for a loan.",
                   tool_type="workflow", tool_data={"workflowId": "wf1"})
    tools = _linked(fake, "wf1")
    assert len(tools) == 1 and tools[0]["name"] == "loan_application"


async def test_update_cannot_point_a_second_tool_at_a_connected_playbook(fake):  # noqa: F811
    wid = await _playbook(fake, expose=True)
    fake.collections["tools"]["other"] = {"id": "other", "name": "faq", "type": "json",
                                          "description": "Returns store hours.", "toolData": {"json": "{}"}}
    with pytest.raises(ValueError, match="already connected"):
        await call("update_agent_tool", app_id="app1", tool_id="other",
                   changes={"type": "workflow", "toolData": {"workflowId": wid}})


def test_tools_for_matches_by_workflow_id():
    tools = [{"id": "a", "toolData": {"workflowId": "x"}}, {"id": "b", "toolData": {"functionId": "x"}}]
    assert [t["id"] for t in journeys.tools_for(tools, "x")] == ["a"]
