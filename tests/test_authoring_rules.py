"""Parity with the console's AI playbook generator (botcircuits-backend/src/gen-ai-utils/playbook-gen)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from botcircuits.playbook_examples import EXAMPLES
from botcircuits.workflow.playbook import compile_playbook, normalize_playbook

from .test_playbook_design import _retry_playbook
from .test_tools import call, fake  # noqa: F401 - fixture

BACKEND_COMPILER = (Path(__file__).resolve().parents[2] / "botcircuits-backend/src/gen-ai-utils/playbook-gen"
                    / "playbook-compiler.js")
_JS = """
const { compilePlaybook } = require(process.argv[1]);
let input = ''; process.stdin.on('data', d => input += d).on('end', () => process.stdout.write(JSON.stringify(
  JSON.parse(input).map(pb => { const r = compilePlaybook(pb); return { stm: r.stmDefinition, errors: r.errors }; }))));
"""


def _canonical(stm: dict) -> dict:
    """Rename generated state ids by traversal order so two compilations can be compared."""
    states, order, queue = stm["states"], [], [stm["startAt"]]
    while queue:
        sid = queue.pop(0)
        if sid in order or sid not in states:
            continue
        order.append(sid)
        cfg = states[sid]["stateConfig"]
        queue += [states[sid].get("next"), cfg.get("next")] + [c.get("next") for c in cfg.get("choices") or []]
    names = {sid: f"S{i}" for i, sid in enumerate(order)}

    def fix(value):
        if isinstance(value, dict):
            return {k: (names.get(v, v) if k in ("next", "blockId") else "ID" if k == "id" else fix(v))
                    for k, v in value.items()}
        return [fix(v) for v in value] if isinstance(value, list) else value

    return {names[sid]: fix(states[sid]) for sid in order}


@pytest.mark.skipif(not shutil.which("node") or not BACKEND_COMPILER.exists(),
                    reason="needs node and a botcircuits-backend checkout next to this repo")
def test_python_compiler_matches_the_console_compiler():
    playbooks = [normalize_playbook(e["playbook"])[0] for e in EXAMPLES.values()]
    playbooks.append(normalize_playbook({"sections": _retry_playbook(True, True)})[0])
    js = json.loads(subprocess.run(["node", "-e", _JS, str(BACKEND_COMPILER)], input=json.dumps(playbooks),
                                   capture_output=True, text=True, check=True).stdout)
    for playbook, expected in zip(playbooks, js, strict=True):
        ours = compile_playbook(playbook)
        assert ours["errors"] == [] and expected["errors"] == []
        assert _canonical(ours["stmDefinition"]) == _canonical(expected["stm"])


def _pb(*steps):
    return {"sections": [{"title": "A", "steps": list(steps)}]}


def _send(text):
    return {"kind": "SEND", "config": {"displayTextOptions": [{"displayText": text}]}}


async def _errors(playbook, variables=None):
    result = await call("validate_playbook", app_id="app1", playbook=playbook, variables=variables or {})
    return result


async def test_undeclared_references_are_errors(fake):  # noqa: F811
    result = await _errors(_pb(_send("Your total is {total}")))
    assert not result["valid"] and any("{total} is not a declared variable" in e for e in result["errors"])
    result = await _errors(_pb(_send("Your total is {total}")), {"total": {"dataType": "any"}})
    assert result["valid"]


async def test_unsupported_config_fields_are_rejected(fake):  # noqa: F811
    step = _send("Hi")
    step["config"]["responseModifyByAI"] = True
    result = await _errors(_pb(step))
    assert any("unsupported setting 'responseModifyByAI'" in e for e in result["errors"])


async def test_api_body_must_be_json_with_references_inside_strings(fake):  # noqa: F811
    run = {"kind": "RUN", "config": {"target": "API", "webhookConfig": {
        "url": "https://api.example.com/x", "method": "POST", "requestBody": "{\"id\": {order_id}}"}}}
    result = await _errors(_pb(run, _send("Done")), {"order_id": {"dataType": "any"}})
    assert any("requestBody must be valid JSON" in e for e in result["errors"])


async def test_variables_are_completed_and_checked(fake):  # noqa: F811
    ask = {"kind": "ASK", "config": {"slot": "code", "displayTextOptions": [{"displayText": "Code?"}]}}
    bad = await _errors(_pb(ask, _send("ok")), {"code": {"dataType": "regex", "content": "([a-z"}})
    assert any("invalid regex" in e for e in bad["errors"])

    saved = await call("create_playbook", app_id="app1", name="Codes", description="Use to collect a code.",
                       playbook=_pb(ask, _send("Thanks")), variables={"code": {"dataType": "regex", "content": "^[A-Z]{3}$"}})
    slot = fake.journeys[saved["workflowId"]]["slots"]["code"]
    assert slot == {"slot": "code", "displayText": "Code", "content": "^[A-Z]{3}$", "captureFromUserInput": True,
                    "dependencies": [], "dataType": "regex"}


async def test_limits(fake):  # noqa: F811
    too_many = {"sections": [{"title": f"S{i}", "steps": [_send("x")]} for i in range(21)]}
    result = await _errors(too_many)
    assert any("At most 20 sections" in e for e in result["errors"])


async def test_emptiness_in_natural_conditions_warns(fake):  # noqa: F811
    branch = {"conditionType": "natural", "naturalLanguage": "{order_status} is empty", "steps": [_send("Not found")]}
    playbook = _pb({"kind": "IF", "branches": [branch, {"conditionType": "else", "steps": [_send("Found")]}]})
    result = await _errors(playbook, {"order_status": {"dataType": "any"}})
    assert result["valid"] and any("can never match" in w for w in result["warnings"])
