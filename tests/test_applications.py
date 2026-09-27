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
