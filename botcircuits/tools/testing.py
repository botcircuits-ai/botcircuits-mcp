"""
MCP tools for automated agent tests: application-scoped test plans (scenarios) and asynchronous
test runs, through the console's agent-eval API.

Mirrors testing_tools.py of botcircuits-agent-builder-copilot-native; keep them in sync. The
copilot's console follows generation and runs live; here the host checks back with
get_test_plan / get_test_run when the user asks (see get_authoring_guide("testing")).
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import client

SCENARIO_FIELDS = ("id", "name", "targetName", "turns", "expected", "expectations", "enabled", "source")


def plan_summary(plan: dict, offset: int = 0, limit: int = 20) -> dict:
    scenarios = [{**{k: case[k] for k in SCENARIO_FIELDS if k in case}, "category": category["id"]}
                 for category in plan.get("categories", []) for case in category.get("cases", [])]
    return {k: plan.get(k) for k in ("id", "name", "totals", "generationStatus", "generationError", "coverage",
                                     "coverageWarnings")} | {
        "scenarios": scenarios[offset:offset + limit], "totalScenarios": len(scenarios),
        "nextOffset": offset + limit if len(scenarios) > offset + limit else None,
    }


def _shown(plan: dict) -> dict:
    return {"plan": plan_summary(plan), "next_step": (
        "Scenarios are being created in the background (usually a few minutes). Tell the user, and "
        "check again with get_test_plan when they come back; then offer to run them."
        if plan.get("generationStatus") == "generating" else
        "Tell the user in one or two sentences what is covered (and any coverageWarnings), then offer to "
        "run all tests. Tests talk to the live agent and may use its connected tools.")}


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_test_plans(app_id: str) -> dict:
        """
        List saved test plans for an application. Read a plan before editing it.

        Args:
            app_id: The application ID.
        """
        return {"plans": await client.list_test_plans(app_id)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_test_plan(app_id: str, plan_id: str, offset: int = 0, limit: int = 20) -> dict:
        """
        Read a test plan: generation status, coverage, and its scenarios (customer messages and
        expected outcomes), paged.

        Args:
            app_id: The application ID.
            plan_id: ID from list_test_plans or generate_test_plan.
            offset: Scenario offset for large plans.
            limit: Scenarios to return, between 1 and 20.
        """
        plan = await client.get_test_plan(app_id, plan_id)
        return {"plan": plan_summary(plan, max(0, int(offset)), min(20, max(1, int(limit))))}

    @mcp.tool()
    async def generate_test_plan(app_id: str, plan_id: str = "") -> dict:
        """
        Create automated tests for the agent. Use whenever the user asks to test the agent,
        create/generate tests or scenarios, or accepts the testing offer. No options needed: it
        reads everything the agent is connected to (instructions, workflows and playbooks, skills,
        tools, MCP servers, sub-agents) and writes up to 10 scenarios for each, plus up to 50
        questions about the connected knowledge. Runs in the background; does not run tests.

        Args:
            app_id: The application ID.
            plan_id: Existing plan to recreate after the agent changed (keeps scenarios the user
                wrote), or empty for a new plan. Prefer recreating the latest plan from list_test_plans.
        """
        return _shown(await client.generate_test_plan(app_id, plan_id))

    @mcp.tool()
    async def save_test_plan(app_id: str, plan: dict, plan_id: str = "") -> dict:
        """
        Add or change scenarios when the user asks for a specific test. Read all pages of an
        existing plan first and keep every scenario you were not asked to change.
        Plan shape: {name, categories: [{id, cases: [{id?, name, turns: [customer messages],
        expected: "plain-language description of a correct reply", source: "user",
        expectations: {toolsCalled?: [tool names that must be used]}}]}]}.
        Category ids: instructions, knowledge_base, workflow, skill, tool_use, mcp, sub_agent.
        `expected` is judged against the conversation; describe the outcome, not exact wording.

        Args:
            app_id: The application ID.
            plan: Complete plan with categories, customer messages and expected outcomes.
            plan_id: Existing plan ID when editing; empty to create a plan.
        """
        return _shown(await client.save_test_plan(app_id, plan, plan_id))

    @mcp.tool(annotations=ToolAnnotations(openWorldHint=True))
    async def start_test_run(app_id: str, plan_id: str, case_ids: list[str] | None = None) -> dict:
        """
        Run tests once the user asks to ("run the tests"). Calls the live agent and its connected
        tools; never run while only offering or creating tests. Returns immediately with a run ID.

        Args:
            app_id: The application ID.
            plan_id: The test plan to run.
            case_ids: Optional specific scenario IDs (e.g. only the failed ones); omit to run all
                enabled scenarios.
        """
        run = await client.start_test_run(app_id, plan_id, case_ids)
        if not (run.get("runId") or run.get("id")):
            return {"started": True, "message": "Tests were queued but no run ID came back. Use list_test_runs "
                                                "to find the run; do not start a second run."}
        return {"run": run, "next_step": "Runs take a few minutes. Tell the user it started; read results with "
                                         "get_test_run when they ask. Never infer success from starting."}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_test_run(app_id: str, run_id: str, case_id: str = "") -> dict:
        """
        Read progress or explain failures for a test run. Never start a new run to check status.

        Args:
            app_id: The application ID.
            run_id: ID returned by start_test_run or list_test_runs.
            case_id: Optional scenario ID to inspect its conversation and checks in detail.
        """
        run = await client.get_test_run(app_id, run_id)
        cases = run.pop("cases", [])
        if case_id:
            matches = [case for case in cases if case.get("caseId") == case_id]
            return {"run": run, "scenarios": matches} if matches else {
                "ok": False, "error": "No completed result for that scenario yet."}
        failures = [{k: case.get(k) for k in ("caseId", "name", "category", "targetName", "status", "reason",
                                              "expected")}
                    for case in cases if case.get("status") in ("failed", "error", "cancelled")]
        return {"run": run, "failures": failures[:20], "totalFailures": len(failures)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_test_runs(app_id: str) -> dict:
        """
        Find recent test runs for an application, including IDs and progress.

        Args:
            app_id: The application ID.
        """
        return {"runs": await client.list_test_runs(app_id)}
