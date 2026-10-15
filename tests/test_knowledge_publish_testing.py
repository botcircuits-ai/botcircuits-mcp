"""Knowledge verification, file upload, publishing and agent tests (synced from copilot-native)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from botcircuits import client
from botcircuits.config import settings
from botcircuits.guides import GUIDES
from botcircuits.server import _INSTRUCTIONS, mcp
from botcircuits.tools.knowledge import assess_preview

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


def _ago(minutes: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------- knowledge
@pytest.mark.parametrize(("preview", "verdict"), [
    ({"modelStatus": "ready", "empty": False}, "ok"),
    ({"modelStatus": "ready", "empty": True, "dataSourceType": "web"}, "empty"),
    ({"modelStatus": "failed"}, "failed"),
    ({"modelStatus": "preparing", "modifiedAt": _ago(2)}, "processing"),
    ({"modelStatus": "preparing", "modifiedAt": _ago(30), "dataSourceType": "web"}, "stalled"),
])
def test_assess_preview(preview, verdict):
    result = assess_preview(preview)
    assert result["verdict"] == verdict
    assert ("next_step" in result) == (verdict != "ok")
    if preview.get("dataSourceType") == "web" and verdict != "ok":
        assert "add_file_knowledge" in result["next_step"]


async def test_preview_pages_text_and_flags_empty_web_source(fake):
    fake.previews["faq"] = {"dataSourceType": "web", "modelStatus": "ready", "content": "x" * 50}
    first = await call("preview_knowledge_source", app_id="app1", source_id="faq", max_chars=20)
    assert first["verdict"] == "ok" and first["content"] == "x" * 20 and first["truncated"] is True
    assert fake.last_query["output"] == "text"

    fake.previews["blocked"] = {"dataSourceType": "web", "modelStatus": "ready", "content": "  "}
    empty = await call("preview_knowledge_source", app_id="app1", source_id="blocked")
    assert empty["verdict"] == "empty" and "add_file_knowledge" in empty["next_step"]

    with pytest.raises(ValueError):
        await call("preview_knowledge_source", app_id="app1", source_id="faq", max_chars=0)


async def test_add_web_knowledge_failure_suggests_upload(fake):
    fake.fail_register_types.add("web")
    result = await call("add_web_knowledge", app_id="app1", source_id="site_pages", description="Site",
                        urls=["https://example.com"])
    assert result["ok"] is False and "add_file_knowledge" in result["next_step"]

    fake.fail_register_types.clear()
    started = await call("add_web_knowledge", app_id="app1", source_id="site_pages", description="Site",
                         urls=["https://example.com"])
    assert started["status"] == "preparing" and "preview_knowledge_source" in started["next_step"]


async def test_add_file_knowledge_uploads_and_registers(fake, tmp_path):
    doc = tmp_path / "Pricing.PDF"
    doc.write_bytes(b"%PDF-1.4 prices")
    result = await call("add_file_knowledge", app_id="app1", source_id="pricing_2026",
                        description="2026 price list", file_path=str(doc))
    assert result["status"] == "preparing" and result["fileName"] == "Pricing.PDF"
    assert fake.uploads["/kb/pricing_2026"] == b"%PDF-1.4 prices"
    source = fake.sources["pricing_2026"]
    assert source["dataSourceType"] == "file" and source["dataType"] == "pdf"
    assert source["ocr"] == {"enable": False, "provider": "default"}


async def test_add_file_knowledge_rejects_images_without_ocr(fake, tmp_path):
    scan = tmp_path / "menu.png"
    scan.write_bytes(b"png")
    with pytest.raises(ValueError, match="ocr=true"):
        await call("add_file_knowledge", app_id="app1", source_id="menu_scan", description="Menu",
                   file_path=str(scan))
    result = await call("add_file_knowledge", app_id="app1", source_id="menu_scan", description="Menu",
                        file_path=str(scan), ocr=True)
    assert result["status"] == "preparing" and fake.sources["menu_scan"]["ocr"]["enable"] is True
    with pytest.raises(ValueError, match="not found"):
        await call("add_file_knowledge", app_id="app1", source_id="other_doc", description="x",
                   file_path=str(tmp_path / "missing.pdf"))


async def test_delete_knowledge_source_reports_referencing_tools(fake):
    await call("add_text_knowledge", app_id="app1", source_id="returns_policy", description="Returns",
               content="30 days.")
    fake.collections["tools"]["t1"] = {"id": "t1", "name": "search_returns", "type": "kb",
                                       "toolData": {"filterKb": ["returns_policy"]}}
    result = await call("delete_knowledge_source", app_id="app1", source_id="returns_policy")
    assert result == {"deleted": "returns_policy", "toolsStillReferencing": ["search_returns"]}
    assert "returns_policy" not in fake.sources


# ---------------------------------------------------------------- publishing
class ElicitingContext:
    """Stands in for FastMCP's Context on a client that can ask the user (elicitation)."""

    def __init__(self, answer: bool | None, supported: bool = True):
        self.answer, self.questions = answer, []
        self.session = SimpleNamespace(check_client_capability=lambda caps: supported)

    async def elicit(self, message, schema):
        self.questions.append(message)
        if self.answer is None:
            return SimpleNamespace(action="cancel", data=None)
        return SimpleNamespace(action="accept", data=schema(publish=self.answer))


async def test_publish_never_happens_without_confirmation(fake):
    result = await call("publish_application", app_id="app1", version_name="Order support v1")
    assert result["status"] == "confirmation_required" and result["published"] is False
    assert result["ask_user"].startswith("Do you want to publish")
    # Nothing was created or published.
    assert fake.access_keys == [] and fake.versions == []
    # A client without elicitation is treated the same way.
    unsupported = await call("publish_application", app_id="app1", ctx=ElicitingContext(True, supported=False))
    assert unsupported["status"] == "confirmation_required" and fake.versions == []


async def test_publish_asks_user_through_elicitation(fake):
    declined = ElicitingContext(answer=False)
    result = await call("publish_application", app_id="app1", user_confirmed=True, ctx=declined)
    # The server asks even when the host claims confirmation, and a "no" wins.
    assert declined.questions and result["status"] == "cancelled" and fake.versions == []

    dismissed = await call("publish_application", app_id="app1", ctx=ElicitingContext(answer=None))
    assert dismissed["status"] == "cancelled" and fake.versions == []

    accepted = ElicitingContext(answer=True)
    result = await call("publish_application", app_id="app1", version_name="Order support v1", ctx=accepted)
    assert '"Order support v1"' in accepted.questions[0]
    assert result["status"] == "published" and fake.versions[0]["versionName"] == "Order support v1"


async def test_publish_creates_live_runtime_key_then_version(fake):
    assert (await call("get_publish_status", app_id="app1"))["published"] is False

    result = await call("publish_application", app_id="app1", version_name="Order support v1", user_confirmed=True)
    assert result["status"] == "published" and result["firstPublish"] is True
    key = fake.access_keys[0]
    assert key["environment"] == "live" and key["scope"] == "runtime" and key["appId"] == "app1"
    assert "botcircuits_init('app1', 'live-key-1')" in result["snippet"]
    assert result["channelSetup"]["website"].endswith("/app/app1/channels/web/install")
    assert fake.versions[0]["versionName"] == "Order support v1"

    status = await call("get_publish_status", app_id="app1")
    assert status["published"] is True and status["versions"][0]["versionName"] == "Order support v1"


async def test_publish_review_requested_and_default_name(fake):
    fake.versions.append({"versionId": "v1", "versionName": "Version 1"})
    fake.publish_review = True
    result = await call("publish_application", app_id="app1", user_confirmed=True)
    assert result["status"] == "review_requested" and result["versionName"] == "Version 2"
    assert result["firstPublish"] is False


# ---------------------------------------------------------------- testing
async def test_generate_and_run_tests(fake):
    generated = await call("generate_test_plan", app_id="app1")
    assert generated["plan"]["generationStatus"] == "generating"
    assert "get_test_plan" in generated["next_step"]
    plan_id = generated["plan"]["id"]

    fake.test_plans[plan_id] = {"id": plan_id, "generationStatus": "ready", "categories": [
        {"id": "instructions", "cases": [{"id": f"c{i}", "name": f"case {i}", "turns": ["hi"]} for i in range(25)]}]}
    page = await call("get_test_plan", app_id="app1", plan_id=plan_id, offset=20)
    assert page["plan"]["totalScenarios"] == 25 and len(page["plan"]["scenarios"]) == 5
    assert page["plan"]["nextOffset"] is None

    started = await call("start_test_run", app_id="app1", plan_id=plan_id, case_ids=["c1"])
    run_id = started["run"]["runId"]
    assert fake.test_runs[run_id]["caseIds"] == ["c1"]

    fake.test_runs[run_id]["cases"] = [
        {"caseId": "c1", "status": "failed", "reason": "did not decline pizza"},
        {"caseId": "c2", "status": "passed"},
    ]
    result = await call("get_test_run", app_id="app1", run_id=run_id)
    assert result["totalFailures"] == 1 and result["failures"][0]["reason"] == "did not decline pizza"
    detail = await call("get_test_run", app_id="app1", run_id=run_id, case_id="c2")
    assert detail["scenarios"][0]["status"] == "passed"

    regenerated = await call("generate_test_plan", app_id="app1", plan_id=plan_id)
    assert regenerated["plan"]["id"] == plan_id


async def test_save_test_plan_creates_and_updates(fake):
    plan = {"name": "Custom", "categories": [{"id": "tool_use", "cases": [
        {"name": "lookup", "turns": ["where is order 1?"], "expected": "gives the status", "source": "user"}]}]}
    saved = await call("save_test_plan", app_id="app1", plan=plan)
    plan_id = saved["plan"]["id"]
    assert saved["plan"]["totalScenarios"] == 1
    updated = await call("save_test_plan", app_id="app1", plan={**plan, "name": "Renamed"}, plan_id=plan_id)
    assert updated["plan"]["name"] == "Renamed"
    assert len((await call("list_test_plans", app_id="app1"))["plans"]) == 1


# ---------------------------------------------------------------- guidance
def test_instructions_carry_clarifying_and_testing_offer():
    assert "## Clarifying (before building)" in _INSTRUCTIONS
    assert "## Report and offer tests" in _INSTRUCTIONS
    assert "NEVER publish automatically" in _INSTRUCTIONS
    assert "Do you want to publish your agent?" in GUIDES["publishing"]
    for topic in ("clarifying", "knowledge", "testing", "publishing"):
        assert GUIDES[topic].strip()
