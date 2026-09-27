"""
Authoring guides delivered to the host AI.

CAPABILITY_GUIDE, PLAYBOOK_SCHEMA and PLAYBOOK_DESIGN go into the server `instructions` (sent
on every connection). Worked examples and WORKFLOW_SCHEMA are fetched on demand (resources +
get_authoring_guide tool): canvas workflows are only for complex processes.
"""

from .playbook_examples import render_examples
from .workflow.complexity import WORKFLOW_MIN_CONDITIONS, WORKFLOW_MIN_STEPS

CAPABILITY_GUIDE = f"""\
## Choosing what to build (follow this policy)

1. **Tools for fetch/act operations.** "Get the weather for a city", "look up an order",
   "create a ticket" are agent TOOLS: `create_api_tool` for an HTTP API (generates and deploys
   a codehook + function tool), `deploy_codehook` + `create_agent_tool(type="function")` for
   custom logic, `kb` tools for documents, `json` for fixed data, an MCP server if one exists.
   Simple behaviour (tone, scope, rules) goes in `update_agent_instructions`. Know-how the model
   applies flexibly with existing tools is a SKILL (`create_skill`).
2. **Multi-step processes start as a PLAYBOOK** (`create_playbook`): anything that must run in
   order, asks the user several things across turns, or branches. Inside it, fetch/compute steps
   call tools (RUN FUNCTION with a codehook, RUN API, RUN KNOWLEDGE); the playbook owns ordering,
   questions and decisions.
3. **Canvas WORKFLOWS only for complex use cases** (`upload_workflow`): more than
   {WORKFLOW_MIN_STEPS} steps, more than {WORKFLOW_MIN_CONDITIONS} conditional nodes, or a step type
   playbooks cannot express (image message, language selector, OAuth, Make.com integration,
   custom client action, pause, AI task). The server rejects smaller new workflows.

Call `recommend_implementation` when the choice is not obvious and tell the user which
construct you chose and why.
"""

PLAYBOOK_SCHEMA = """\
## Playbook format (create_playbook / update_playbook / validate_playbook)

A playbook is sections of steps run top to bottom; sections only group steps (the last step of
one flows into the next section). Ids are optional on create (generated for you). When EDITING,
start from get_playbook and keep every existing id, step and variable you are not changing.
Limits: 20 sections, 100 steps, 100 variables, 10 branches per IF. Use only the config fields
shown here — the Playbooks editor drops anything else (validation rejects it).

    {"sections": [{"title": "Collect details", "steps": [ <step>, ... ]}]}
    step = {"kind": "...", "config": {...}}   (+ "branches" for IF)

Turn rules: the playbook runs step after step in the same turn — SENDs are delivered as they are
reached — until an ASK pauses it to wait for the customer's reply (or the playbook ends). The next
customer message answers that ASK and the playbook continues from the step after it. RUN, SET and
IF show nothing. A turn delivers at most 10 messages, so put an ASK between long runs of SENDs.

- SEND:  {"kind": "SEND", "config": {"displayTextOptions": [{"displayText": "Thanks {first_name}!"}]}}
- ASK:   {"kind": "ASK", "config": {"slot": "order_id", "inputType": "TEXT",
            "displayTextOptions": [{"displayText": "What's your order number?"}],
            "validationErrorDisplayTextOptions": [{"displayText": "Order numbers look like ORD-12345."}],
            "autoFillFromEntity": true}}
    inputType TEXT | BUTTONS ("data": [{"title": "Refund", "payload": "refund"}], "optionsTitle")
    | CARDS ("data": [{"title", "description", "imageUrl", "payload", "buttons": [...]}])
    | DOCUMENT ("allowedFileTypes": ["pdf"], "extractionSchema": "{\\"total\\": \\"grand total\\"}"; variable dataType document;
      read fields as {invoice.data.total}). autoFillFromEntity skips the question if already answered.
- RUN:   config.target =
    PROMPT   {"action": "Summarise the issue in one sentence", "slotToAssign": "summary"} (sub-agent with the app's tools)
    FUNCTION {"codehookId": "api_get_order"}  — receives all variables as `slots`, returns {"slots": {...}}
    API      {"webhookConfig": {"url": "https://api.x.com/orders/{order_id}", "method": "GET", "headers": [],
              "requestBody": "{}", "responseMapping": [{"key": "data.status", "slot": "order_status"}]}}
             (requestBody is a JSON STRING; prefer FUNCTION + create_api_tool when credentials are involved)
    KNOWLEDGE {"inputPrompt": "Return window for {product}?", "slotToAssign": "answer", "filterKb": ["returns_policy"]}
             (filterKb [] searches all knowledge; only use ids from list_knowledge_sources)
  Never invent ids or URLs: codehookId, filterKb, journeyId must exist in the app, and API URLs must
  come from the user. Use RUN PROMPT for reasoning/composition; use FUNCTION/API for data and actions.
  RUN steps are silent (skipNotify defaults true); follow with a SEND to show a result. A failed
  FUNCTION/API call sets none of its variables: check with IF `<variable> is empty`.
- SET:   {"kind": "SET", "config": {"slotToAssign": "status", "value": "escalated"}}  (no LLM, supports {slot})
- IF:    {"kind": "IF", "branches": [
            {"conditionType": "natural", "naturalLanguage": "{order_status} is shipped",
             "includeConversationHistory": false, "steps": [...]},
            {"conditionType": "natural", "naturalLanguage": "the customer sounds upset or asks for a person",
             "includeConversationHistory": true, "steps": [...]},
            {"conditionType": "else", "steps": [...]}]}
    Write every condition as conditionType "natural" — the same rule the console's Create-with-AI
    generator follows — including exact comparisons ("{plan} is enterprise", "{score} is greater than
    700"; numbers compare correctly). Name variables in braces so the evaluator sees their values.
    An EMPTY variable makes a condition that uses it FALSE: handle "not found / not answered" in the
    ELSE branch, never with "{x} is empty". includeConversationHistory: true only when the condition
    is about what the customer said. Each branch is one AI judgement at runtime (a few cheap calls).
    One ELSE, placed last, no condition. No IF inside a branch: GO_TO a section that starts with the
    inner IF. Branches rejoin after the IF unless they end in GO_TO. (Expression mode with
    expressionList exists in console-built playbooks; keep those as-is when editing, but don't add new ones.)
- GO_TO: {"kind": "GO_TO", "config": {"sectionTitle": "Look up order"}}  (or "sectionId"). Jumps, bounded
    retries and nested decisions. It transfers control — the steps after it are not run. SECTIONS FALL
    THROUGH: if two sections are alternatives, end each path with GO_TO to a final section (or put them in
    IF branches) so the second does not also run. A retry must SET the variable to "" before asking again.
- HANDOFF: {"kind": "HANDOFF", "config": {}}  — must be the LAST step on its path (steps after it still run)          RUN_PLAYBOOK: {"kind": "RUN_PLAYBOOK", "config": {"journeyId": "<id>"}}

Variables: declare every variable an ASK collects AND every variable a text or condition reads —
including what a RUN FUNCTION returns. SET / RUN slotToAssign / API responseMapping outputs are
added automatically. Names: letters, digits, _ (not starting with a digit). Regex is Python `re`.
slot, displayText, captureFromUserInput and dependencies are filled in if omitted.
    {"order_id": {"dataType": "regex", "content": "^ORD-\\\\d{5}$", "captureFromUserInput": true},
     "reason":   {"dataType": "custom", "content": "why the customer wants to return the item", "captureFromUserInput": true},
     "plan":     {"dataType": "values", "content": "basic,pro,enterprise"}}
dataType: custom (content = extraction description) · regex · values (comma list) · number · boolean ·
email · age · date · datetime · any · document.
"""

PLAYBOOK_DESIGN = """\
## Designing a playbook that works

### How a playbook runs (design around this)
- The main agent starts it through its agent tool (type workflow). Values the customer already
  mentioned (and any tool arguments named like variables) pre-fill variables.
- Steps run top to bottom in ONE turn. SENDs are delivered as they are reached. An ASK shows its
  question and PAUSES; the customer's next message is validated against the variable's dataType
  (the ASK re-asks with its validation text on failure), then the playbook continues after it.
- An ASK is SKIPPED when its variable already has a value (autoFillFromEntity, default true). That
  saves questions, and it also means a retry must clear the variable first.
- RUN, SET and IF are silent. A failed FUNCTION/API call leaves its output variables empty.
- IF branches are natural-language conditions judged by AI with the current variable values; a
  condition that uses an empty variable is false. Checked in order; the first match runs, then the
  flow continues after the IF.
  With no match and no ELSE the flow also continues after the IF — and if nothing follows, the
  playbook stops and the customer's message goes back to the agent.
- When the playbook ends, its last message is returned to the main agent as the tool result.

### Procedure
1. **Outcome and inputs.** Write one sentence: "When <trigger>, collect <inputs>, do <action>,
   and tell the customer <outcome>." List every piece of information needed and where it comes
   from (customer, API/codehook, knowledge, computed). Ask the user for anything unknown — API
   details, policies, wording of important messages. Never invent business rules.
2. **Check what exists.** get_application_overview. Every fetch or action the playbook performs
   must be an existing codehook, API or knowledge source. Create missing ones FIRST
   (create_api_tool / deploy_codehook / add_text_knowledge) and note their exact ids and the
   variable names they return.
3. **Variables.** Name them snake_case and define each one the customer answers, choosing the
   strictest dataType that fits: values (fixed choices; must include every button payload),
   regex (codes/ids), email / number / date, custom with a precise description otherwise.
   Also declare what a RUN FUNCTION returns ({"dataType": "any", "captureFromUserInput": false}):
   the tools can't see inside a codehook, and undeclared outputs show up as warnings.
4. **Sections = stages.** Usually: Identify/collect → Look up / act → Decide → Resolve/close.
   One purpose per section; titles are GO TO targets.
5. **Happy path first**, one step per action: ASK each missing input (skipped automatically if
   known), RUN each call, IF on its results, SEND the outcome.
6. **Then every unhappy path**: invalid answer (validation text on the ASK), not found / API
   failure (the ELSE after the checks on the output), customer declines, customer wants a person or
   is upset (natural IF with conversation history → SEND + HANDOFF), and a bounded retry where it helps.
7. **Close every path** with a SEND (or a HANDOFF) so the customer always knows the outcome.
8. **validate_playbook** → fix every error, read the warnings and the `outline`; the outline is
   what the customer experiences, step by step.
   **Review it like an independent reviewer** (the console generator rejects a playbook unless all
   hold): every requested behaviour is present; ordering, branch rejoining and termination are
   right; no invented integrations or placeholder steps stand in for required behaviour; no
   unconditional loops; when editing, unrelated steps, ids and variables are unchanged.
   Show the outline to the user for anything non-trivial.
9. **create_playbook** (same arguments), then ask whether to connect it to the main agent or a
   sub-agent, and make sure the agent's instructions say when to use it.

### Patterns
- **Ask + validate:** ASK with a regex/values/email variable and a validationErrorDisplayTextOptions
  message that shows the expected format.
- **Look up then branch:** RUN FUNCTION → IF "{status} is a" / "{status} is b" / ELSE = not found or failed.
- **Confirm before side effects:** before a RUN that creates, charges, cancels or sends, ASK a yes/no
  BUTTONS question (autoFillFromEntity false) and IF on the answer.
- **Retry:** in the failure branch, SET the variable to "", ASK again, GO TO the section that uses it.
- **Handoff:** SEND "connecting you…" then HANDOFF, as the last steps of their branch.
- **Numbers and ranges:** natural conditions handle them ("{amount} is more than 500"). For money or
  eligibility decisions that must be exact, let the codehook compute a flag and test "{is_eligible} is yes".
- **Alternative sections:** when sections are alternatives (refund vs exchange), end each with GO_TO
  a shared final section; otherwise sections fall through and both run.
- **Nested decisions:** GO TO a section that starts with the inner IF (no IF inside a branch).
- **Answer from documents:** RUN KNOWLEDGE into a variable, then SEND or ASK using it.

### Mistakes the tools catch (fix and re-validate)
- Undeclared {variable} references · config fields the editor doesn't support · limits exceeded ·
  API body that isn't valid JSON · invalid regex · ASK variable without a definition · button
  payload missing from a values list · unknown
  codehook/knowledge/playbook id · empty SEND/ASK text · IF branch without steps, ELSE not last,
  IF inside a branch · GO TO a missing or empty section.
- Warnings: a path that ends silently, steps after a HANDOFF, an IF without ELSE at the end, a
  loop that never waits for the customer, too many messages before a question.

### Checklist before saving
- [ ] Every call uses an existing resource id; its output variables are the ones you test.
- [ ] Every ASK has a defined variable, clear question and validation text for strict types.
- [ ] Every RUN that can fail is followed by an IF whose ELSE handles the empty result.
- [ ] Conditions are natural language naming variables in braces; alternatives don't fall through.
- [ ] No invented ids or URLs; edits keep unrelated steps, ids and variables.
- [ ] Side-effect actions are confirmed first; HANDOFF ends its path.
- [ ] Every path ends with a SEND or HANDOFF; every IF has an ELSE unless steps follow it.
- [ ] validate_playbook: no errors, warnings understood, outline reads like the intended conversation.
"""

WORKFLOW_SCHEMA = f"""\
## BotCircuits canvas workflow schema (upload_workflow / validate_workflow_definition)

Only for complex processes (> {WORKFLOW_MIN_STEPS} steps, > {WORKFLOW_MIN_CONDITIONS} conditional nodes, or
playbook-unsupported steps). Otherwise write a playbook.

### Intermediate format — {{"stmDefinition": {{"startAt", "states"}}, "slots": {{...}}}}
- State ids: descriptive snake_case (e.g. "ask_email"); the server renumbers them.
- First state: stateType "start" with `next` on the state AND on stateConfig.
- Linear `next` lives on the state. A choice keeps its else branch on stateConfig.next (state-level next null).
- No `next` = the workflow ends there.
- displayTextOptions: array of plain strings (one is picked at random per turn; {{slot}} interpolation).
- The run continues through messages in the same turn until a prompt pauses it for the reply
  (at most 10 messages per turn). Set "skipNotify": true on actions whose raw result should not be shown.

### Node types
START      {{"type": "StartNode", "next": "<id>", "stateConfig": {{"stateType": "start", "name": "Start", "next": "<id>"}}}}
TEXT       {{"type": "messagePrompt", "next": "<id>", "stateConfig": {{"stateType": "message", "type": "text", "name": "...", "displayTextOptions": ["Hello!"]}}}}
IMAGE      {{"type": "imageMessage", "next": "<id>", "stateConfig": {{"stateType": "message", "type": "image", "name": "...", "imageUrl": "https://...", "caption": "..."}}}}
QUESTION   {{"type": "questionPrompt", "next": "<id>", "stateConfig": {{"stateType": "prompt", "type": "text", "name": "...", "slot": "<slot>", "displayTextOptions": ["..."], "validationErrorDisplayTextOptions": ["..."], "autoFillFromEntity": true}}}}
BUTTONS    {{"type": "buttonsPrompt", "next": "<id>", "stateConfig": {{"stateType": "prompt", "type": "buttons", "name": "...", "slot": "<slot>", "displayTextOptions": ["..."], "data": [{{"title": "...", "payload": "...", "actionType": "set_value"}}]}}}}
CARDS      {{"type": "cardsPrompt", "next": "<id>", "stateConfig": {{"stateType": "prompt", "type": "cards", "name": "...", "slot": "workflow_option", "displayTextOptions": ["..."], "data": [{{"title": "...", "description": "...", "buttons": [{{"title": "...", "payload": "..."}}]}}]}}}}
DOCUMENT   {{"type": "docCapturePrompt", "next": "<id>", "stateConfig": {{"stateType": "prompt", "type": "doc_capture", "name": "...", "slot": "<document slot>", "displayTextOptions": ["Upload your invoice"], "allowedFileTypes": ["pdf"], "extractionSchema": "{{\\"total\\": \\"grand total\\"}}"}}}}
LANGUAGE   {{"type": "languageSelectorPrompt", "next": "<id>", "stateConfig": {{"stateType": "prompt", "type": "languageSelector", "name": "...", "slot": "sys_language_selected", "languages": ["english", "sinhala"], "displayTextOptions": ["Choose a language"]}}}}
WEBHOOK    {{"type": "webhookAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "webhook", "name": "...", "skipNotify": true,
             "webhookConfig": {{"url": "https://api.example.com/{{slot}}", "method": "POST", "headers": [], "parameters": [], "requestBody": "{{}}", "requestType": "raw",
                               "responseMapping": [{{"key": "data.field", "slot": "result_slot"}}]}},
             "requestMapper": {{"type": "inline_script", "inlineScript": ""}}, "responseMapper": {{"type": "inline_script", "inlineScript": ""}}}}}}
CODEHOOK   {{"type": "codehookAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "codehook", "name": "...", "codehookId": "<existing id>", "defaultInput": "{{}}", "skipNotify": true}}}}
SET        {{"type": "aiTask", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "setVariable", "name": "...", "slotToAssign": "status", "value": "{{other_slot}}"}}}}
AI TASK    {{"type": "aiTask", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "aiTask", "name": "...", "inputPrompt": "Summarise: {{slot}}", "slotToAssign": "summary", "skipNotify": true}}}}
AGENT      {{"type": "agentAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "agentAction", "name": "...", "action": "Look up order {{order_id}} and summarise delays", "slotToAssign": "result", "skipNotify": true}}}}
DOC SEARCH {{"type": "docSearchAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "docSearch", "name": "...", "inputPrompt": "query with {{slot}}", "slotToAssign": "answer", "filterKb": ["<dataSourceId>"], "skipNotify": false}}}}
JOURNEY    {{"type": "journeyAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "journey", "name": "...", "journeyId": "<workflow id>"}}}}
CUSTOM     {{"type": "customAction", "next": "<id>", "stateConfig": {{"stateType": "action", "type": "customAction", "id": "<client action id>", "name": "...", "slotToFill": "<slot>"}}}}
LIVE AGENT {{"type": "liveAgentAction", "next": null, "stateConfig": {{"stateType": "action", "type": "liveagent", "name": "Transfer to Agent"}}}}
PAUSE      {{"type": "pauseAction", "next": "<resume id>", "stateConfig": {{"stateType": "action", "type": "pause", "name": "..."}}}}
END        {{"type": "codehookAction", "next": null, "stateConfig": {{"stateType": "action", "type": "end", "name": "End"}}}}
CHOICE     {{"type": "choice", "next": null, "stateConfig": {{"stateType": "choice", "name": "...",
             "choices": [{{"id": 1, "operator": "AND", "conditionType": "expression", "expressionList": [{{"variable": "issue_type", "operator": "is", "value": "billing"}}], "next": "billing"}},
                         {{"id": 2, "operator": "AND", "conditionType": "natural", "naturalLanguage": "the customer sounds frustrated", "includeConversationHistory": true, "expressionList": [], "next": "escalate"}}],
             "next": "<else id>"}}}}
Operators: is · is not · greater than · greater than or equal · less than · less than or equal · contains ·
not contains · starts with · ends with · is empty · is not empty. Numbers compare AS TEXT in choices —
have a codehook emit a boolean/enum slot. Special variables: {{sys_input_text}} · {{sys_channel}}.
Never use prompt types date / date_time / custom (they hang or silently do nothing).

### Slot definitions — every slot a prompt collects needs one (action outputs are auto-defined)
{{"slot_name": {{"slot": "slot_name", "displayText": "Label", "captureFromUserInput": true,
                "dataType": "<type>", "content": "<type-dependent>", "dependencies": []}}}}
dataType: custom (content = AI extraction description) · regex (pattern) · values (comma list) ·
number · boolean · email · age · date · datetime · any · document. content REQUIRED for custom/regex/values.
Reserved slot `workflow_option` (cards default) needs no definition.

### Custom action steps
Use a customAction step when the user asks for file operations, code generation or command
execution inside a workflow, unless they explicitly ask for another step type.
"""

GUIDES = {
    "capabilities": CAPABILITY_GUIDE,
    "playbook": PLAYBOOK_SCHEMA,
    "playbook_design": PLAYBOOK_DESIGN,
    "playbook_examples": render_examples(),
    "workflow": WORKFLOW_SCHEMA,
}
