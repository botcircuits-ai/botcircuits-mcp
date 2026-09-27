"""
Worked playbook examples served to the host AI (get_authoring_guide("playbook_examples")).

They are data, not prose, so tests/test_guides.py compiles and validates every one: an
example the model copies is guaranteed to be a working playbook.
"""

import json


def _send(text: str) -> dict:
    return {"kind": "SEND", "config": {"displayTextOptions": [{"displayText": text}]}}


def _ask(slot: str, question: str, retry: str = "", **extra) -> dict:
    config = {"slot": slot, "displayTextOptions": [{"displayText": question}], **extra}
    if retry:
        config["validationErrorDisplayTextOptions"] = [{"displayText": retry}]
    return {"kind": "ASK", "config": config}


def _when(condition: str, steps: list) -> dict:
    """Natural-language branch, as the console's Create-with-AI generator writes every condition."""
    return {"conditionType": "natural", "naturalLanguage": condition, "includeConversationHistory": False,
            "steps": steps}


EXAMPLES: dict[str, dict] = {
    "order_status": {
        "use_when": "Look something up with a function, branch on the result, retry when not found.",
        "requires": {"codehooks": ["api_get_order"]},
        "requires_notes": "api_get_order is an API tool made with create_api_tool whose slot_mapping sets "
                          "order_status and ship_date. When the order is not found it returns no slots, so "
                          "order_status stays empty.",
        "name": "Order status",
        "description": "Use when a customer asks where their order is or about its delivery status.",
        "variables": {
            "order_id": {"dataType": "regex", "content": "^ORD-\\d{5}$", "captureFromUserInput": True},
            # Outputs of api_get_order: declared so the IF and SEND references are known.
            "order_status": {"dataType": "any", "content": "", "captureFromUserInput": False},
            "ship_date": {"dataType": "any", "content": "", "captureFromUserInput": False},
        },
        "playbook": {"sections": [
            {"title": "Identify the order", "steps": [
                _ask("order_id", "What's your order number? It looks like ORD-12345.",
                     "That doesn't look like an order number. It should look like ORD-12345."),
            ]},
            {"title": "Look up and answer", "steps": [
                {"kind": "RUN", "config": {"target": "FUNCTION", "codehookId": "api_get_order"}},
                # An empty variable makes a natural condition false, so "not found" is the ELSE branch.
                {"kind": "IF", "branches": [
                    _when("{order_status} is shipped", [_send("Good news: order {order_id} shipped on {ship_date}.")]),
                    _when("{order_status} is processing",
                          [_send("Order {order_id} is being prepared and will ship soon.")]),
                    _when("{order_status} has any other value", [_send("Order {order_id} is currently: {order_status}.")]),
                    {"conditionType": "else", "steps": [
                        {"kind": "SET", "config": {"slotToAssign": "order_id", "value": ""}},
                        _ask("order_id", "I couldn't find that order. Could you check the number and send it again?",
                             "It should look like ORD-12345."),
                        {"kind": "GO_TO", "config": {"sectionTitle": "Look up and answer"}},
                    ]},
                ]},
                _send("Is there anything else I can help you with?"),
            ]},
        ]},
    },

    "return_request": {
        "use_when": "Collect details, answer from knowledge, confirm before an action with side effects, "
                    "hand off when needed.",
        "requires": {"knowledge": ["returns_policy"]},
        "requires_notes": "returns_policy is a knowledge source (add_text_knowledge) with the returns policy.",
        "name": "Return request",
        "description": "Use when a customer wants to return or exchange an item.",
        "variables": {
            "order_id": {"dataType": "custom", "content": "the order number the customer wants to return, e.g. ORD-12345",
                         "captureFromUserInput": True},
            "return_reason": {"dataType": "values", "content": "damaged,wrong_item,no_longer_needed,other",
                              "captureFromUserInput": True},
            "confirm_return": {"dataType": "values", "content": "yes,no,agent", "captureFromUserInput": False},
        },
        "playbook": {"sections": [
            {"title": "Collect details", "steps": [
                _ask("order_id", "Which order would you like to return? Please share the order number."),
                _ask("return_reason", "What's the reason for the return?", inputType="BUTTONS", data=[
                    {"title": "Arrived damaged", "payload": "damaged"},
                    {"title": "Wrong item", "payload": "wrong_item"},
                    {"title": "No longer needed", "payload": "no_longer_needed"},
                    {"title": "Something else", "payload": "other"},
                ]),
            ]},
            {"title": "Check the policy and confirm", "steps": [
                {"kind": "RUN", "config": {"target": "KNOWLEDGE", "filterKb": ["returns_policy"], "topK": 10,
                                           "inputPrompt": "Can an item be returned for this reason: {return_reason}? "
                                                          "Answer in one or two sentences.",
                                           "slotToAssign": "policy_answer"}},
                _ask("confirm_return", "{policy_answer} Shall I start the return for order {order_id}?",
                     inputType="BUTTONS", autoFillFromEntity=False,
                     data=[{"title": "Yes, start the return", "payload": "yes"},
                           {"title": "No, not now", "payload": "no"},
                           {"title": "Talk to a person", "payload": "agent"}]),
            ]},
            {"title": "Finish", "steps": [
                # The IF is the last step, so every branch ends the playbook (HANDOFF included).
                {"kind": "IF", "branches": [
                    _when("{confirm_return} is yes", [
                        {"kind": "RUN", "config": {"target": "API", "webhookConfig": {
                            "url": "https://api.example.com/returns", "method": "POST",
                            "requestBody": "{\"orderId\": \"{order_id}\", \"reason\": \"{return_reason}\"}",
                            "responseMapping": [{"key": "data.returnId", "slot": "return_id"}]}}},
                        _send("Your return {return_id} has been created. We'll email you a prepaid label."),
                    ]),
                    _when("{confirm_return} is agent", [
                        _send("Sure, I'm connecting you with a member of our team now."),
                        {"kind": "HANDOFF", "config": {}},
                    ]),
                    {"conditionType": "natural", "includeConversationHistory": True,
                     "naturalLanguage": "the customer is angry or threatening to leave",
                     "steps": [_send("I'm sorry about this. Let me connect you with a member of our team."),
                               {"kind": "HANDOFF", "config": {}}]},
                    {"conditionType": "else", "steps": [
                        _send("No problem, I haven't started a return. Let me know if you change your mind."),
                    ]},
                ]},
            ]},
        ]},
    },

    "lead_capture": {
        "use_when": "Collect several validated answers, compute a value, route by choice. No external resources.",
        "requires": {},
        "requires_notes": "",
        "name": "Demo request",
        "description": "Use when a visitor wants a demo, pricing for their team, or to talk to sales.",
        "variables": {
            "full_name": {"dataType": "custom", "content": "the person's full name", "captureFromUserInput": True},
            "work_email": {"dataType": "email", "content": "", "captureFromUserInput": True},
            "team_size": {"dataType": "values", "content": "1-10,11-50,51-200,200+", "captureFromUserInput": True},
        },
        "playbook": {"sections": [
            {"title": "Qualify", "steps": [
                _ask("full_name", "Happy to set up a demo! What's your name?"),
                _ask("work_email", "Thanks {full_name}. What's your work email?",
                     "That doesn't look like an email address. Could you check it?"),
                _ask("team_size", "How big is your team?", inputType="BUTTONS", data=[
                    {"title": "1-10", "payload": "1-10"}, {"title": "11-50", "payload": "11-50"},
                    {"title": "51-200", "payload": "51-200"}, {"title": "200+", "payload": "200+"}]),
                {"kind": "SET", "config": {"slotToAssign": "lead_source", "value": "chat-demo-request"}},
            ]},
            {"title": "Route", "steps": [
                {"kind": "IF", "branches": [
                    {"conditionType": "natural", "naturalLanguage": "{team_size} is 51-200 or 200+",
                     "includeConversationHistory": False,
                     "steps": [_send("Thanks {full_name}! An account executive will email {work_email} within one "
                                     "business day to book your demo."),
                               {"kind": "HANDOFF", "config": {}}]},
                    {"conditionType": "else", "steps": [
                        _send("Thanks {full_name}! We've sent a link to {work_email} so you can book a demo time "
                              "that suits you.")]},
                ]},
            ]},
        ]},
    },
}


def render_examples() -> str:
    parts = ["## Worked playbook examples (all compile and validate)\n",
             "Copy the structure, not the wording. Each shows the `create_playbook` arguments.\n"]
    for key, example in EXAMPLES.items():
        requires = example["requires"]
        needs = ", ".join(f"{k}: {', '.join(v)}" for k, v in requires.items()) or "nothing"
        args = {"name": example["name"], "description": example["description"],
                "variables": example["variables"], "playbook": example["playbook"]}
        parts.append(f"### {key} — {example['use_when']}\n")
        parts.append(f"Requires: {needs}. {example['requires_notes']}\n")
        parts.append("```json\n" + json.dumps(args, indent=1) + "\n```\n")
    return "\n".join(parts)
