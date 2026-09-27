"""
Conversation-design checks on a compiled stmDefinition.

The validator answers "will this run?"; these checks answer "will this converse well?".
They encode how the runtime actually drives a journey:

  * The run loop keeps going after a message until a prompt (ASK) pauses it or the flow
    ends (agent_loop_native.workflow.run_stm_loop / agent_executor intent loop). In a
    prompt-based agent it stops after MAX_MESSAGES_PER_TURN message-producing invocations,
    leaving the flow stranded mid-way.
  * A prompt with autoFillFromEntity is skipped when its slot is already truthy, so a loop
    that only re-visits already-answered questions never waits for the customer.
  * A choice with no match and no else hands the message back to the agent.

Shared with botcircuits-agent-builder-copilot (copilot/botcircuits/workflow/); keep in sync.
"""

from .constants import NOTIFYING_ACTION_TYPES

# runtime: agent_loop_native/constants.py MAX_LOOP = 10 (message-producing invocations per turn)
MAX_MESSAGES_PER_TURN = 10
_MESSAGE_WARN_AT = 8
_END_ACTIONS = {"liveagent", "journey", "end"}


def _successors(state: dict) -> list[str]:
    cfg = state.get("stateConfig") or {}
    targets = [state.get("next"), cfg.get("next")]
    targets += [c.get("next") for c in cfg.get("choices") or []]
    targets += [c.get("next") for c in cfg.get("conditions") or []]
    seen: list[str] = []
    for t in targets:
        if t and t not in seen:
            seen.append(t)
    return seen


def _kind(state: dict) -> str:
    """'prompt' pauses, 'visible' shows something and continues, 'silent' shows nothing."""
    cfg = state.get("stateConfig") or {}
    state_type = cfg.get("stateType")
    if state_type == "prompt":
        return "prompt"
    if state_type == "message":
        return "visible"
    if state_type == "action" and cfg.get("type") in NOTIFYING_ACTION_TYPES and not cfg.get("skipNotify"):
        return "visible"
    return "silent"


def _label(state: dict, sid: str) -> str:
    return f"'{(state.get('stateConfig') or {}).get('name') or sid}'"


def lint_flow(stm_definition: dict) -> list[str]:
    states: dict = (stm_definition or {}).get("states") or {}
    start = (stm_definition or {}).get("startAt")
    if start not in states:
        return []
    succ = {sid: [t for t in _successors(s) if t in states] for sid, s in states.items()}
    warnings: list[str] = []

    # 1. Paths that end on a silent step: the customer gets no closing message.
    for sid, state in states.items():
        cfg = state.get("stateConfig") or {}
        if succ[sid] or cfg.get("stateType") in ("start", "choice"):
            continue
        if _kind(state) == "silent" and cfg.get("type") not in _END_ACTIONS:
            warnings.append(f"The flow can end at {_label(state, sid)}, which shows nothing: add a SEND that "
                            "tells the customer the outcome.")

    # 2. Steps after a human handoff still run (the handoff step returns and the flow continues).
    for sid, state in states.items():
        if (state.get("stateConfig") or {}).get("type") == "liveagent" and succ[sid]:
            warnings.append(f"Steps still run after the handoff {_label(state, sid)}: make HANDOFF the last step on "
                            "its path (put it in an IF branch at the end of the last section, or end the section).")

    # 2b. IF with no ELSE and nothing after it: unmatched cases leave the playbook.
    for sid, state in states.items():
        cfg = state.get("stateConfig") or {}
        if cfg.get("stateType") == "choice" and not cfg.get("next"):
            warnings.append(f"IF {_label(state, sid)} has no ELSE and no step after it: when no branch matches, "
                            "the playbook stops and the message goes back to the agent. Add an ELSE branch.")

    # 3. Too many messages before the next question.
    best: dict[str, int] = {}
    stack = [(start, 0)]
    flagged = False
    while stack and not flagged:
        sid, count = stack.pop()
        kind = _kind(states[sid])
        count = 0 if kind == "prompt" else count + (kind == "visible")
        if count >= _MESSAGE_WARN_AT:
            warnings.append(f"{count} messages in a row reach {_label(states[sid], sid)} without a question. A turn "
                            f"delivers at most {MAX_MESSAGES_PER_TURN}; merge SENDs or add an ASK.")
            flagged = True
            continue
        if best.get(sid, -1) >= count:
            continue
        best[sid] = count
        stack.extend((t, count) for t in succ[sid])

    # 4. Loops that never wait for the customer.
    reach: dict[str, set[str]] = {}
    for sid in states:
        seen, todo = set(), list(succ[sid])
        while todo:
            t = todo.pop()
            if t not in seen:
                seen.add(t)
                todo.extend(succ[t])
        reach[sid] = seen
    reported: set[frozenset] = set()
    for sid in states:
        if sid not in reach[sid]:
            continue
        loop = frozenset(t for t in reach[sid] if sid in reach[t])
        if loop in reported:
            continue
        reported.add(loop)
        prompts = [states[t]["stateConfig"] for t in loop if _kind(states[t]) == "prompt"]
        cleared = {states[t]["stateConfig"].get("slotToAssign") for t in loop
                   if states[t]["stateConfig"].get("type") == "setVariable"
                   and not str(states[t]["stateConfig"].get("value") or "").strip()}
        waits = any(p.get("autoFillFromEntity") is False or p.get("type") == "doc_capture"
                    or p.get("slot") in cleared for p in prompts)
        if not waits:
            names = ", ".join(sorted(_label(states[t], t) for t in loop)[:4])
            warnings.append(f"A loop through {names} never waits for the customer: its questions are skipped once "
                            "answered. SET the variable to \"\" before asking again, or set autoFillFromEntity false "
                            "on the retry ASK.")
    return warnings
