"""The playbook design guidance must produce working playbooks.

Every published example is validated and saved through the real tools, and each design
check is shown to fire on a broken playbook and stay quiet on a sound one.
"""

import copy

import pytest

from botcircuits.guides import GUIDES
from botcircuits.playbook_examples import EXAMPLES
from botcircuits.server import mcp
from botcircuits.workflow.lint import lint_flow
from botcircuits.workflow.playbook import compile_playbook, normalize_playbook

from .test_tools import call, fake  # noqa: F401 - fixture


def _send(text):
    return {"kind": "SEND", "config": {"displayTextOptions": [{"displayText": text}]}}


def _ask(slot, text, **extra):
    return {"kind": "ASK", "config": {"slot": slot, "displayTextOptions": [{"displayText": text}], **extra}}


def _lint(sections):
    normalized, errors = normalize_playbook({"sections": sections})
    assert errors == []
    compiled = compile_playbook(normalized)
    assert compiled["errors"] == []
    return lint_flow(compiled["stmDefinition"])


def _provide(fake, example):  # noqa: F811
    for hook in example["requires"].get("codehooks", []):
        fake.codehooks[hook] = {"codehookId": hook}
    for source in example["requires"].get("knowledge", []):
        fake.sources[source] = {"dataSourceId": source, "id": f"int-{source}", "modelStatus": "ready"}


@pytest.mark.parametrize("key", sorted(EXAMPLES))
async def test_every_example_validates_cleanly_and_saves(fake, key):  # noqa: F811
    example = EXAMPLES[key]
    _provide(fake, example)
    checked = await call("validate_playbook", app_id="app1", playbook=example["playbook"],
                         variables=example["variables"])
    assert checked["valid"], checked["errors"]
    assert checked["warnings"] == []
    assert any("⏸" in line for line in checked["outline"])

    saved = await call("create_playbook", app_id="app1", name=example["name"],
                       description=example["description"], playbook=example["playbook"],
                       variables=example["variables"])
    assert saved["ok"], saved
    stm = fake.journeys[saved["workflowId"]]["stm"]
    assert stm["metadata"]["authoringMode"] == "playbook"
    start = stm["stmDefinition"]["states"][stm["stmDefinition"]["startAt"]]
    assert start["next"] == start["stateConfig"]["next"]


async def test_examples_need_their_resources(fake):  # noqa: F811
    example = EXAMPLES["order_status"]
    checked = await call("validate_playbook", app_id="app1", playbook=example["playbook"],
                         variables=example["variables"])
    assert not checked["valid"]
    assert any("api_get_order" in e for e in checked["errors"])


async def test_button_payloads_must_be_in_values_list(fake):  # noqa: F811
    playbook = {"sections": [{"title": "A", "steps": [
        _ask("choice", "Pick one", inputType="BUTTONS",
             data=[{"title": "Yes", "payload": "yes"}, {"title": "Maybe", "payload": "maybe"}]),
        _send("You picked {choice}"),
    ]}]}
    checked = await call("validate_playbook", app_id="app1", playbook=playbook,
                         variables={"choice": {"dataType": "values", "content": "yes,no"}})
    assert not checked["valid"]
    assert any("['maybe']" in e for e in checked["errors"])


def test_lint_flags_steps_after_handoff():
    warnings = _lint([{"title": "A", "steps": [_send("Connecting you"), {"kind": "HANDOFF", "config": {}},
                                               _send("Anything else?")]}])
    assert any("after the handoff" in w for w in warnings)


def test_lint_flags_silent_ending():
    warnings = _lint([{"title": "A", "steps": [_send("Hi"), {"kind": "SET", "config": {"slotToAssign": "x",
                                                                                       "value": "1"}}]}])
    assert any("shows nothing" in w for w in warnings)


def test_lint_flags_if_without_else_at_the_end():
    warnings = _lint([{"title": "A", "steps": [_ask("x", "X?"), {"kind": "IF", "branches": [
        {"conditionType": "natural", "naturalLanguage": "the customer said yes", "steps": [_send("Great")]}]}]}])
    assert any("no ELSE" in w for w in warnings)


def _retry_playbook(clear: bool, auto_fill: bool):
    retry = [_ask("code", "Try again?", autoFillFromEntity=auto_fill),
             {"kind": "GO_TO", "config": {"sectionTitle": "Check"}}]
    if clear:
        retry.insert(0, {"kind": "SET", "config": {"slotToAssign": "code", "value": ""}})
    return [
        {"title": "Ask", "steps": [_ask("code", "Code?")]},
        {"title": "Check", "steps": [
            {"kind": "RUN", "config": {"target": "FUNCTION", "codehookId": "check"}},
            {"kind": "IF", "branches": [
                {"conditionType": "expression", "expressionList": [{"variable": "valid", "operator": "is",
                                                                    "value": "yes"}], "steps": [_send("OK")]},
                {"conditionType": "else", "steps": retry},
            ]},
        ]},
    ]


@pytest.mark.parametrize(("clear", "auto_fill", "loops_forever"), [
    (False, True, True), (True, True, False), (False, False, False)])
def test_lint_flags_retry_loops_that_never_wait(clear, auto_fill, loops_forever):
    warnings = _lint(_retry_playbook(clear, auto_fill))
    assert any("never waits" in w for w in warnings) is loops_forever


def test_lint_flags_too_many_messages_per_turn():
    warnings = _lint([{"title": "A", "steps": [_send(f"Line {n}") for n in range(9)]}])
    assert any("messages in a row" in w for w in warnings)
    assert not any("messages in a row" in w for w in _lint(
        [{"title": "A", "steps": [_send("a"), _send("b"), _ask("x", "X?"), _send("c")]}]))


async def test_instructions_teach_design_with_the_correct_turn_model():
    text = mcp.instructions
    assert "Designing a playbook that works" in text and "Checklist before saving" in text
    assert "END THE TURN" not in text and "ends the turn" not in text.lower()
    assert "pauses" in text.lower()
    for topic in ("capabilities", "playbook", "playbook_design", "playbook_examples", "workflow"):
        assert await call("get_authoring_guide", topic=topic) == GUIDES[topic]
    prompt = await mcp.get_prompt("design_playbook", {"app_id": "app1", "requirements": "Book a table"})
    assert "validate_playbook" in prompt.messages[0].content.text


def test_examples_are_not_mutated_by_rendering():
    before = copy.deepcopy(EXAMPLES)
    GUIDES["playbook_examples"]
    assert before == EXAMPLES
