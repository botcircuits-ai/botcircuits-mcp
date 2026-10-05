"""In-memory stand-in for the BotCircuits design-time API, routed through httpx.MockTransport.

Implements only the routes the copilot calls, with the platform's quirks that matter:
journeys appear asynchronously after an action is created, list endpoints return arrays,
and MCP tokens are masked on read.
"""

import json
import re
import uuid
from collections import defaultdict

import httpx

API = "https://api.test"
UPLOAD_HOST = "uploads.test"


class FakeBotCircuits:
    def __init__(self, app_id: str = "app1", app_mode: str = "prompt_based", journey_delay_polls: int = 1):
        self.app_id = app_id
        self.app = {"appId": app_id, "name": "Demo", "appMode": app_mode}
        self.instructions: dict = {}
        self.settings: dict = {"authConfig": {}, "kbTopResults": 20, "botLanguage": "english"}
        self.collections: dict[str, dict[str, dict]] = defaultdict(dict)  # tools / skills / mcp-servers
        self.actions: dict[str, dict] = {}
        self.journeys: dict[str, dict] = {}
        self.pending_journeys: dict[str, int] = {}
        self.codehooks: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.uploads: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.journey_delay_polls = journey_delay_polls
        self.valid_token = "key_valid_token"
        self.created_apps: list[dict] = []
        self.can_create_apps = True
        self.codehook_runtimes: set[str] | None = None  # None = accept any
        self.deploy_conflicts = 0
        # Runtime traces (backend src/tracing): index rows, and the summary / turn views by session.
        self.trace_sessions: list[dict] = []
        self.trace_summaries: dict[str, dict] = {}
        self.trace_turns: dict[tuple[str, str], dict] = {}
        self.last_query: dict = {}

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == UPLOAD_HOST:
            self.uploads[request.url.path] = request.content
            # The platform's S3 trigger records the archive location on the codehook.
            hook = self.codehooks.get(request.url.path.removeprefix("/codehooks/").removesuffix(".zip"))
            if hook is not None:
                hook["functionInfo"] = {"code": {"bucket": "codehooks", "key": request.url.path}}
            return httpx.Response(200)
        if request.headers.get("Authorization") != self.valid_token:
            return httpx.Response(403, json={"message": "Unauthorized"})
        path = request.url.path
        self.calls.append((request.method, path))
        self.last_query = dict(request.url.params)
        body = json.loads(request.content) if request.content else {}
        if path == "/apps":
            return self.route_apps(request.method, body)
        prefix = f"/apps/{self.app_id}"
        if not path.startswith(prefix):
            return httpx.Response(403, json={"message": "no access to app"})
        return self.route(request.method, path[len(prefix):], body)

    def route(self, method: str, path: str, body: dict) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        ok = lambda data=None, status=200: httpx.Response(status, json=data if data is not None else {"message": "ok"})  # noqa: E731
        missing = httpx.Response(404, json={"message": "not found"})

        if path == "" and method == "GET":
            return ok(self.app)
        if path.startswith("/traces/sessions") and method == "GET":
            return self.trace_route(path.removeprefix("/traces/sessions"))
        if path == "/prompt-config/instructions":
            if method == "GET":
                return ok(self.instructions)
            self.instructions.update(body)
            return ok()
        if path == "/agent/core-settings":
            if method == "GET":
                return ok(self.settings)
            self.settings = body
            return ok()

        match = re.fullmatch(r"/prompt-config/(tools|skills|mcp-servers)(?:/([^/]+))?", path)
        if match:
            kind, item_id = match.groups()
            items = self.collections[kind]
            if item_id is None and method == "GET":
                return ok([self._masked(kind, v) for v in items.values()])
            if item_id is None and method == "POST":
                new_id = body.get("id") or uuid.uuid4().hex[:12]
                items[new_id] = {**body, "id": new_id}
                return ok({"id": new_id}, 201)
            if item_id not in items:
                return missing
            if method == "GET":
                return ok(self._masked(kind, items[item_id]))
            if method == "PUT":
                token = body.get("authorizationToken")
                stored = items[item_id].get("authorizationToken")
                items[item_id] = {**body, "id": item_id}
                if token in (None, "__stored__") and stored:
                    items[item_id]["authorizationToken"] = stored
                return ok({"id": item_id})
            if method == "DELETE":
                del items[item_id]
                return ok()

        if path == "/agent/actions":
            if method == "GET":
                return ok(list(self.actions.values()))
            action_id = body.get("id") or uuid.uuid4().hex[:12]
            is_new = action_id not in self.actions
            self.actions[action_id] = {**body, "id": action_id}
            if is_new and body.get("actionType") == "workflow":
                self.pending_journeys[action_id] = self.journey_delay_polls
            return ok({"id": action_id}, 201)
        match = re.fullmatch(r"/agent/actions/([^/]+)", path)
        if match and method == "DELETE":
            self.actions.pop(match.group(1), None)
            self.journeys.pop(match.group(1), None)
            return ok()

        if path == "/model/journey" and method == "POST":
            journey_id = body["journeyId"]
            if journey_id in self.journeys:
                return httpx.Response(400, json={"message": "journey already exists"})
            self.journeys[journey_id] = {"journeyId": journey_id, "name": body["name"], "slots": {}, "stm": {}}
            self.pending_journeys.pop(journey_id, None)
            return ok({"journeyId": journey_id}, 201)
        match = re.fullmatch(r"/model/journey/([^/]+)(/slots|/definition)?", path)
        if match:
            journey_id, suffix = match.groups()
            if journey_id in self.pending_journeys:
                self.pending_journeys[journey_id] -= 1
                if self.pending_journeys[journey_id] < 0:
                    del self.pending_journeys[journey_id]
                    self.journeys[journey_id] = {"journeyId": journey_id, "name": journey_id, "slots": {}, "stm": {}}
            journey = self.journeys.get(journey_id)
            if journey is None:
                return missing if method == "GET" else httpx.Response(500, json={"message": f"journey not found {journey_id}"})
            if suffix is None and method == "GET":
                return ok(journey)
            if suffix == "/slots":
                journey["slots"] = body["slots"]
                return ok()
            if suffix == "/definition":
                journey["stm"] = {"stmDefinition": body["stmDefinition"], "metadata": body["metadata"]}
                return ok()

        if path == "/model/codehooks":
            if method == "GET":
                return ok(list(self.codehooks.values()))
            if self.codehook_runtimes is not None and body.get("runtime") not in self.codehook_runtimes:
                return httpx.Response(400, json={"message": "invalid runtime type"})
            self.codehooks[body["codehookId"]] = {**body, "status": "saved"}
            return ok()
        match = re.fullmatch(r"/model/codehooks/([^/]+)(/upload-url|/deploy)?", path)
        if match:
            hook_id, suffix = match.groups()
            if hook_id not in self.codehooks:
                return missing
            if suffix == "/upload-url":
                return ok({"url": f"https://{UPLOAD_HOST}/codehooks/{hook_id}.zip?sig=x"})
            if suffix == "/deploy":
                if self.deploy_conflicts:  # Lambda still Pending: the backend answers 500
                    self.deploy_conflicts -= 1
                    return httpx.Response(500, json={"message": "ResourceConflictException"})
                self.codehooks[hook_id]["status"] = "deployed"
                return ok()
            if method == "GET":
                return ok(self.codehooks[hook_id])
            if method == "DELETE":
                del self.codehooks[hook_id]
                return ok()

        if path == "/knowledge/data-sources":
            if method == "GET":
                return ok(list(self.sources.values()))
            internal = uuid.uuid4().hex[:8]
            status = "ready" if body.get("dataSourceType") == "text" else "preparing"
            self.sources[body["dataSourceId"]] = {**body, "id": internal, "modelStatus": status}
            return ok({"id": internal})
        if path == "/knowledge/data-sources/text":
            return ok()
        if path == "/knowledge/data-sources/file/url":
            return ok({"uploadUrl": f"https://{UPLOAD_HOST}/kb/{body['metadata']['dataSourceId']}",
                       "metadata": body["metadata"]})
        match = re.fullmatch(r"/knowledge/data-sources/([^/]+)", path)
        if match and method == "DELETE":
            internal = match.group(1)
            for key, source in list(self.sources.items()):
                if source["id"] == internal:
                    del self.sources[key]
                    return ok()
            return missing

        return httpx.Response(404, json={"message": f"no fake route {method} {path}"})

    def trace_route(self, rest: str) -> httpx.Response:
        query = self.last_query
        if rest == "":
            rows = [r for r in self.trace_sessions
                    if (query.get("status") != "error" or r.get("errorCount"))
                    and r.get("lastSeen", "") >= query.get("since", "")]
            rows = sorted(rows, key=lambda r: r.get("lastSeen", ""), reverse=True)[:int(query.get("limit", 20))]
            return httpx.Response(200, json={"pagination": {}, "complete": True, "data": rows})
        match = re.match(r"^/([^/]+)/summary$", rest)
        if match:
            summary = self.trace_summaries.get(match.group(1))
            if summary is None:
                return httpx.Response(404, json={"message": "Resource NotFound : no trace"})
            return httpx.Response(200, json={"appId": self.app_id, "sessionId": match.group(1), **summary})
        match = re.match(r"^/([^/]+)/turns/([^/]+)$", rest)
        if match:
            turn = self.trace_turns.get((match.group(1), match.group(2)))
            if turn is None:
                return httpx.Response(404, json={"message": "Resource NotFound : no trace"})
            return httpx.Response(200, json={"appId": self.app_id, **turn, "detail": query.get("detail", "trimmed")})
        return httpx.Response(404, json={"message": f"no fake trace route {rest}"})

    def route_apps(self, method: str, body: dict) -> httpx.Response:
        if method == "GET":
            return httpx.Response(200, json=[self.app, *self.created_apps])
        if not self.can_create_apps:
            return httpx.Response(403, json={"message": "Unauthorized"})
        app = {**body, "appId": f"app-{len(self.created_apps) + 2}"}
        self.created_apps.append(app)
        return httpx.Response(201, json={"appId": app["appId"], "name": body.get("name")})

    def _masked(self, kind: str, item: dict) -> dict:
        if kind != "mcp-servers":
            return item
        masked = {**item, "hasAuthorizationToken": bool(item.get("authorizationToken"))}
        if item.get("authorizationToken"):
            masked["authorizationToken"] = "__stored__"
        return masked
