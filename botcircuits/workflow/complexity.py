"""Capability-selection policy: which BotCircuits construct fits a requirement.

Policy for the MCP server:
  * Instructions (LLM prompt) and tools (codehook- or API-backed functions, KB, MCP) are
    the default for fetch-style actions and free-form behaviour.
  * A multi-step process that must run in order starts as a **playbook**.
  * Only a genuinely complex process becomes a canvas **workflow** — more than
    WORKFLOW_MIN_STEPS steps or more than WORKFLOW_MIN_CONDITIONS conditional nodes, or
    when it needs a step type playbooks cannot express.

The thresholds are enforced in code at save time, not only described in the prompt.
Shared logic with botcircuits-agent-builder-copilot, which uses its own thresholds.
"""

from dataclasses import dataclass, field

from .constants import PLAYBOOK_UNSUPPORTED_FEATURES

WORKFLOW_MIN_STEPS = 30       # a workflow is justified when steps > 30
WORKFLOW_MIN_CONDITIONS = 20  # ... or conditional nodes > 20


@dataclass
class Complexity:
    steps: int
    conditional_nodes: int
    unsupported_playbook_features: list[str] = field(default_factory=list)

    @property
    def exceeds_playbook(self) -> bool:
        return (self.steps > WORKFLOW_MIN_STEPS or self.conditional_nodes > WORKFLOW_MIN_CONDITIONS
                or bool(self.unsupported_playbook_features))

    def as_dict(self) -> dict:
        return {
            "steps": self.steps,
            "conditionalNodes": self.conditional_nodes,
            "unsupportedPlaybookFeatures": self.unsupported_playbook_features,
            "thresholds": {"steps": WORKFLOW_MIN_STEPS, "conditionalNodes": WORKFLOW_MIN_CONDITIONS},
            "workflowJustified": self.exceeds_playbook,
        }


def workflow_complexity(stm_definition: dict) -> Complexity:
    """Measure a canvas workflow. The start state is not a step."""
    states = (stm_definition or {}).get("states") or {}
    steps = conditional = 0
    features: set[str] = set()
    for state in states.values():
        cfg = (state or {}).get("stateConfig") or {}
        state_type, sub_type = cfg.get("stateType"), cfg.get("type")
        if state_type == "start":
            continue
        steps += 1
        if state_type == "choice":
            conditional += 1
        elif cfg.get("conditions"):
            conditional += 1
            features.add("attached_conditions")
        if state_type == "message" and sub_type == "image":
            features.add("image_message")
        if state_type == "prompt" and sub_type == "languageSelector":
            features.add("language_selector")
        if state_type == "action":
            features.update({"auth": ["auth"], "integrationWorkflow": ["integration"],
                             "customAction": ["custom_action"], "pause": ["pause"],
                             "aiTask": ["ai_task"]}.get(sub_type, []))
    return Complexity(steps, conditional, sorted(features))


def playbook_complexity(playbook: dict) -> Complexity:
    steps = conditional = 0

    def walk(items: list) -> None:
        nonlocal steps, conditional
        for step in items or []:
            if step.get("kind") != "GO_TO":
                steps += 1
            if step.get("kind") == "IF":
                conditional += 1
                for branch in step.get("branches") or []:
                    walk(branch.get("steps"))

    for section in (playbook or {}).get("sections") or []:
        walk(section.get("steps"))
    return Complexity(steps, conditional)


def recommend(
    *,
    summary: str,
    estimated_steps: int,
    decision_points: int,
    multi_turn_input: bool,
    must_follow_fixed_order: bool,
    single_action: bool,
    guidance_only: bool,
    needs_features: list[str] | None = None,
) -> dict:
    """Recommend the construct for one capability. Pure; used by the design tool."""
    needs = [f for f in (needs_features or []) if f in PLAYBOOK_UNSUPPORTED_FEATURES]
    unknown = [f for f in (needs_features or []) if f not in PLAYBOOK_UNSUPPORTED_FEATURES]
    reasons: list[str] = []

    if single_action and not multi_turn_input and estimated_steps <= 2 and decision_points == 0:
        choice = "tool"
        reasons.append("A single fetch/act operation: expose it as a tool the agent calls with arguments "
                       "(type=function backed by a codehook for APIs/custom logic, kb for documents, "
                       "json for fixed data, or an MCP server's tools).")
    elif guidance_only and not must_follow_fixed_order:
        choice = "skill"
        reasons.append("Procedural know-how the model should follow flexibly, loaded on demand: a skill "
                       "that orchestrates existing tools. No enforced state machine is needed.")
    elif estimated_steps > WORKFLOW_MIN_STEPS or decision_points > WORKFLOW_MIN_CONDITIONS or needs:
        choice = "workflow"
        if estimated_steps > WORKFLOW_MIN_STEPS:
            reasons.append(f"{estimated_steps} steps exceeds the playbook limit of {WORKFLOW_MIN_STEPS}.")
        if decision_points > WORKFLOW_MIN_CONDITIONS:
            reasons.append(f"{decision_points} conditional nodes exceeds the playbook limit of "
                           f"{WORKFLOW_MIN_CONDITIONS}.")
        if needs:
            reasons.append("Needs step types playbooks cannot express: "
                           + ", ".join(PLAYBOOK_UNSUPPORTED_FEATURES[f] for f in needs) + ".")
    elif multi_turn_input or must_follow_fixed_order or estimated_steps > 2 or decision_points > 0:
        choice = "playbook"
        reasons.append("A multi-step process with an enforced order and/or questions across turns, within "
                       f"playbook limits (≤{WORKFLOW_MIN_STEPS} steps, ≤{WORKFLOW_MIN_CONDITIONS} conditions). "
                       "Start with a playbook; promote to a workflow only if it outgrows them.")
    else:
        choice = "instructions"
        reasons.append("Simple conversational behaviour: put it in the agent's global instructions.")

    result = {"summary": summary, "recommendation": choice, "reasons": reasons}
    if choice in {"playbook", "workflow"}:
        result["exposeAs"] = ("After saving, it is registered as a type=workflow tool so the prompt-based "
                              "agent can start it; give that tool a precise 'when to use' description.")
        result["pieces"] = ("Steps that fetch or compute should call tools/codehooks (RUN FUNCTION / API); "
                            "keep the playbook for ordering, questions and branching.")
    if unknown:
        result["ignoredFeatures"] = unknown
        result["knownFeatures"] = sorted(PLAYBOOK_UNSUPPORTED_FEATURES)
    return result
