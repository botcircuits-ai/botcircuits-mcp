"""create_application: prompt-based only, no duplicates, clear errors for keys that can't create apps."""

from botcircuits.server import mcp

from .test_tools import call, fake  # noqa: F401  (fixture)


async def test_create_application_is_always_prompt_based(fake):  # noqa: F811
    result = await call("create_application", name="Acme Support", description="Answers order questions.")
    assert result["created"] and result["appMode"] == "prompt_based"
    sent = fake.created_apps[0]
    assert sent["appMode"] == "prompt_based" and sent["intentLess"] is True
    assert sent["name"] == "Acme Support" and sent["description"] == "Answers order questions."
    assert result["appId"] == sent["appId"]


async def test_create_application_refuses_duplicate_names_unless_asked(fake):  # noqa: F811
    fake.app["name"] = "Acme Support"
    refused = await call("create_application", name="acme support")
    assert refused["created"] is False and "already exists" in refused["error"] and fake.created_apps == []
    created = await call("create_application", name="acme support", allow_duplicate_name=True)
    assert created["created"] and len(fake.created_apps) == 1


async def test_create_application_explains_keys_that_cannot_create(fake):  # noqa: F811
    fake.can_create_apps = False
    try:
        await call("create_application", name="New app")
    except RuntimeError as exc:
        assert "account-level access key" in str(exc)
    else:
        raise AssertionError("expected an error")


def test_instructions_require_asking_new_or_existing():
    text = mcp.instructions
    assert "CHOOSING THE APPLICATION" in text
    assert "Do you want to create a new application, or use an" in text
    assert 'appMode "prompt_based"' in text


SCOPED_PROMPT = """## Scope
You are the assistant for Acme Plumbing, a residential plumbing company. You ONLY help with:
- booking plumber visits
Everything else is out of scope. For an out-of-scope request, do not answer it or start any playbook."""


async def test_overview_flags_unscoped_agent(fake):  # noqa: F811
    result = await call("get_application_overview", app_id=fake.app_id)
    assert result["scopeDefined"] is False and "Scope" in result["scopeNote"]
    fake.instructions["globalPrompt"] = SCOPED_PROMPT
    result = await call("get_application_overview", app_id=fake.app_id)
    assert result["scopeDefined"] is True and "scopeNote" not in result


async def test_instructions_without_scope_are_refused(fake):  # noqa: F811
    result = await call("update_agent_instructions", app_id=fake.app_id,
                        global_prompt="You are a friendly plumbing assistant. Use book_visit to book.")
    assert result["saved"] is False and "## Scope" in result["error"]
    assert "globalPrompt" not in fake.instructions


async def test_instructions_with_scope_are_saved_and_survive_append(fake):  # noqa: F811
    result = await call("update_agent_instructions", app_id=fake.app_id, global_prompt=SCOPED_PROMPT)
    assert result["saved"], result
    result = await call("update_agent_instructions", app_id=fake.app_id,
                        global_prompt="Use book_visit when a customer wants a plumber.", mode="append")
    assert result["saved"], result
    assert fake.instructions["globalPrompt"].startswith("## Scope")


async def test_scope_guidance_is_delivered():
    assert "## Agent scope" in mcp.instructions
    assert "## Scope" in (await call("get_authoring_guide", topic="scope"))
