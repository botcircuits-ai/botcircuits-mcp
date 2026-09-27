"""The stmDefinition vocabulary, as the runtime implements it (bc-tech-docs/08).

The runtime is the specification: these sets mirror `executor.invoke_state`,
`handle_prompt`, `message_factory` and `action_handler.HANDLERS`.
"""
# Shared with botcircuits-agent-builder-copilot (copilot/botcircuits/); keep the two in sync.


STATE_TYPES = {"start", "message", "prompt", "action", "choice"}

MESSAGE_TYPES = {"text", "image"}

PROMPT_TYPES = {"text", "buttons", "cards", "doc_capture", "languageSelector", "auto_capture_slots"}
# Declared in stm_consts but never handled: the prompt delivers nothing and the chat hangs.
UNIMPLEMENTED_PROMPT_TYPES = {"date", "date_time"}
# Both loader paths raise KeyError that is swallowed; the step silently does nothing (§4.6).
BROKEN_PROMPT_TYPES = {"custom"}

ACTION_TYPES = {
    "codehook", "webhook", "_webhook", "integrationWorkflow", "journey", "liveagent", "pause",
    "docSearch", "aiTask", "agentAction", "auth", "customAction", "setVariable", "end",
}

# Action types whose handler returns a user-visible message unless skipNotify is set,
# which is delivered to the customer (§5.1).
NOTIFYING_ACTION_TYPES = {"codehook", "webhook", "aiTask", "docSearch", "agentAction"}

CHOICE_OPERATORS = (
    "is", "is not", "greater than", "greater than or equal", "less than", "less than or equal",
    "contains", "not contains", "starts with", "ends with", "is empty", "is not empty",
)
NUMERIC_OPERATORS = {"greater than", "greater than or equal", "less than", "less than or equal"}
VALUELESS_OPERATORS = {"is empty", "is not empty"}

SLOT_DATA_TYPES = {
    "custom", "regex", "values", "any", "number", "boolean", "email", "age", "date", "datetime", "document",
}
SLOT_TYPES_REQUIRING_CONTENT = {"custom", "regex", "values"}

RESERVED_SLOTS = {
    "workflow_option", "sys_language_selected", "sys_ht_force_connect", "auto_capture_slots",
    "sys_agent_action_result", "capture_slot", "agent_action_result", "sys_failure_count_reset",
}
SYSTEM_PLACEHOLDERS = {
    "sys_input_text", "sys_channel", "system_app_id", "system_session_id", "system_message_id", "system_channel",
}

# Canvas node type per (stateType, type): the runtime ignores `state.type`, but the console
# uses it to pick a node component, so emitting it keeps workflows openable (§8).
NODE_TYPE = {
    ("start", None): "StartNode",
    ("message", "text"): "messagePrompt",
    ("message", "image"): "imageMessage",
    ("prompt", "text"): "questionPrompt",
    ("prompt", "buttons"): "buttonsPrompt",
    ("prompt", "cards"): "cardsPrompt",
    ("prompt", "doc_capture"): "docCapturePrompt",
    ("prompt", "languageSelector"): "languageSelectorPrompt",
    ("prompt", "auto_capture_slots"): "autoCaptureSlots",
    ("action", "codehook"): "codehookAction",
    ("action", "webhook"): "webhookAction",
    ("action", "journey"): "journeyAction",
    ("action", "integrationWorkflow"): "integrationAction",
    ("action", "liveagent"): "liveAgentAction",
    ("action", "docSearch"): "docSearchAction",
    ("action", "aiTask"): "aiTask",
    ("action", "agentAction"): "agentAction",
    ("action", "customAction"): "customAction",
    ("action", "pause"): "pauseAction",
    ("action", "auth"): "auth",
    ("action", "setVariable"): "aiTask",
    ("choice", None): "choice",
}

# Step types a playbook cannot express (bc-tech-docs/12 §11 item 8). Needing one of these
# is a legitimate reason to author a canvas workflow even for a small process.
PLAYBOOK_UNSUPPORTED_FEATURES = {
    "image_message": "Image message step",
    "language_selector": "Language selector prompt",
    "auth": "OAuth authentication step",
    "integration": "Make.com integration step",
    "custom_action": "Client-side custom action",
    "pause": "Pause step",
    "ai_task": "AI task (LLM extraction into a slot)",
    "nested_if": "IF inside an IF branch",
    "attached_conditions": "Conditional edges on a non-choice step (numeric expCondition branching)",
}
