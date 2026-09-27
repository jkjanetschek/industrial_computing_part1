"""The coordinator's one way to talk to a sub-agent: A2A over JSON-RPC 2.0.

Pattern: strategy per AGENT_CALL_MODE (a dict of route builders, like the
fetchers in registry_client). ``AgentClient.call(card, message)`` looks the
same to its callers whether the request goes straight to the agent (direct)
or through LiteLLM's relay (litellm); only the URL and headers differ.

Protocol shapes (verified live against fasta2a 2.x; LiteLLM's relay forwards
the same bodies unchanged, see proxy/agent_endpoints/a2a_endpoints.py):

    Request, both methods, always POST to one path
    {
      "jsonrpc": "2.0",
      "id": "<uuid, echoed back so responses can be matched>",
      "method": "message/send" | "tasks/get",
      "params": { ...see below... }
    }

    message/send params                          response
    {                                            {
      "message": {                                 "jsonrpc": "2.0", "id": "...",
        "messageId": "<uuid>",                     "result": {
        "kind": "message",                           "task": {
        "role": "user",                                "id": "<task id>",
        "parts": [                                     "contextId": "...",
          {"kind": "text", "text": "<prompt>"}         "status": {"state": "submitted"},
        ]                                              "history": [...]
      }                                              }
    }                                              }
                                                 }
    Spec-allowed alternative: "result" is a message ({"kind": "message",
    "parts": [...]}) when the agent answers synchronously; fasta2a never does,
    but the client accepts it so another A2A server would work unchanged.

    tasks/get params                             response
    {"id": "<task id>"}                          {
                                                   "jsonrpc": "2.0", "id": "...",
                                                   "result": {              <- the task itself,
                                                     "id": "<task id>",        NOT nested in "task"
                                                     "status": {"state": "working" | "completed" | "failed" | ...},
                                                     "artifacts": [
                                                       {"parts": [{"kind": "text", "text": "<answer>"}]}
                                                     ],
                                                     "history": [...]
                                                   }
                                                 }

    Error (either method)
    {"jsonrpc": "2.0", "id": "...", "error": {"code": -32000, "message": "..."}}

Why two round trips: fasta2a runs the agent in a background worker and
answers message/send immediately with a "submitted" task, so the answer has
to be fetched with tasks/get once the state is terminal. Streaming
(message/stream, SSE) would avoid polling but adds nothing for one-shot
tool calls and LiteLLM's relay treats it differently; polling is the
simplest thing that works in both modes.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from shared.schemas import AgentCard
from shared.settings import AgentCallMode, settings

JSONRPC_VERSION = "2.0"
MESSAGE_SEND_METHOD = "message/send"
TASKS_GET_METHOD = "tasks/get"
# LiteLLM registers this path (and two /message/send aliases) for ALL JSON-RPC
# methods; the segment may be the agent id or the agent_name.
LITELLM_RELAY_PATH = "/a2a/{agent_id}"
# A2A task states after which the task will not change any more.
TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})
COMPLETED_STATE = "completed"

POLL_INTERVAL_SECONDS = 1.0
MAX_POLLS = 120
# Per HTTP request, not per call: message/send returns at once and each
# tasks/get is quick; 60 s only matters for a network that is really down.
CALL_TIMEOUT_SECONDS = 60.0

# Tests inject a factory returning a client with httpx.MockTransport, so no
# agent process is needed to exercise the client.
ClientFactory = Callable[[str], httpx.AsyncClient]


class AgentCallError(Exception):
    """The agent answered, but not with a usable result (JSON-RPC error, failed task, timeout).

    Deliberately NOT an httpx error: transport failures (agent down) and
    protocol-level failures (agent up but the task failed) are different
    situations for the coordinator to report, even though tools.py turns both
    into strings.
    """


@dataclass(frozen=True)
class Route:
    """Where one agent's JSON-RPC requests go in the active call mode.

    Both methods use the same route; only the body differs. Keeping base_url
    and path apart lets the client factory own connection settings per host.
    """

    base_url: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)


def default_client(base_url: str) -> httpx.AsyncClient:
    """Production client factory: one AsyncClient per call, closed by ``call`` when done."""
    return httpx.AsyncClient(base_url=base_url, timeout=CALL_TIMEOUT_SECONDS)


class AgentClient:
    """Used by tools.call_agent, once per sub-agent request GAIA makes.

    Holds only the client factory; there is no per-agent state because the
    registry card passed to ``call`` already carries everything needed to
    reach the agent.
    """

    def __init__(self, client_factory: ClientFactory = default_client) -> None:
        self._client_factory = client_factory

    async def call(self, card: AgentCard, message: str) -> str:
        """Send one user message to the agent and return its final text answer.

        Steps: pick the route for the mode, message/send, then either return
        an immediate message or poll tasks/get until the task is terminal.
        One httpx client covers the whole exchange so the connection is reused
        across the polls.
        """
        route = _ROUTES[settings.AGENT_CALL_MODE](card)
        async with self._client_factory(route.base_url) as client:
            result = await _post_rpc(client, route, MESSAGE_SEND_METHOD, {"message": _user_message(message)})
            if _is_message(result):
                return _text_of_parts(result.get("parts", []))
            task = await _wait_for_terminal(client, route, _task_of(result))
        state = task["status"]["state"]
        if state != COMPLETED_STATE:
            raise AgentCallError(f"agent '{card.id}' task {task.get('id')} ended in state '{state}'")
        return _text_of_task(task)


def _direct_route(card: AgentCard) -> Route:
    """AGENT_CALL_MODE=direct: the agent's own url from the registry, JSON-RPC at its root path."""
    return Route(base_url=card.url, path="/")


def _litellm_route(card: AgentCard) -> Route:
    """AGENT_CALL_MODE=litellm: LiteLLM's relay, authenticated with the proxy key.

    The relay resolves the path segment by agent id and then by agent_name;
    our registry id equals the LiteLLM agent_name (litellm/config.yaml), so
    the same card works in both modes without a mapping table.
    """
    return Route(
        base_url=settings.LITELLM_PROXY_URL,
        path=LITELLM_RELAY_PATH.format(agent_id=card.id),
        headers={"Authorization": f"Bearer {settings.LITELLM_PROXY_API_KEY}"},
    )


# The strategy table ``call`` consults; adding a call mode is one entry here
# plus the Literal in shared/settings.py.
_ROUTES: dict[AgentCallMode, Callable[[AgentCard], Route]] = {
    "direct": _direct_route,
    "litellm": _litellm_route,
}


async def _post_rpc(client: httpx.AsyncClient, route: Route, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One JSON-RPC exchange: wrap params in the envelope, POST, unwrap ``result``.

    Used for both methods so the envelope, the HTTP error check and the
    JSON-RPC error check exist exactly once. ``raise_for_status`` covers
    transport-level failures (404 relay path, 401 bad proxy key); the
    ``error`` member covers protocol-level failures the agent reports itself.
    """
    envelope = {"jsonrpc": JSONRPC_VERSION, "id": str(uuid4()), "method": method, "params": params}
    response = await client.post(route.path, json=envelope, headers=route.headers)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        error = body["error"]
        raise AgentCallError(f"{method} failed: {error.get('message')} (code {error.get('code')})")
    return body["result"]


async def _wait_for_terminal(client: httpx.AsyncClient, route: Route, task: dict[str, Any]) -> dict[str, Any]:
    """Poll tasks/get until the task reaches a terminal state or MAX_POLLS is spent.

    The task from message/send is checked first: if an agent ever answers
    "completed" straight away, no tasks/get is sent at all. Giving up raises
    instead of returning a "working" task so a hung agent becomes a visible
    tool error rather than an empty answer.
    """
    for _ in range(MAX_POLLS):
        if task["status"]["state"] in TERMINAL_STATES:
            return task
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        task = _task_of(await _post_rpc(client, route, TASKS_GET_METHOD, {"id": task["id"]}))
    raise AgentCallError(f"task {task.get('id')} did not finish within {MAX_POLLS} polls")


def _user_message(text: str) -> dict[str, Any]:
    """The A2A message object for message/send params; a fresh messageId per call."""
    return {"messageId": str(uuid4()), "kind": "message", "role": "user", "parts": [{"kind": "text", "text": text}]}


def _is_message(result: dict[str, Any]) -> bool:
    """True when message/send answered with a message instead of a task (spec-allowed, fasta2a never does)."""
    return result.get("kind") == "message" or ("parts" in result and "status" not in result)


def _task_of(result: dict[str, Any]) -> dict[str, Any]:
    """message/send nests the task under 'task'; tasks/get returns it bare. Accept both."""
    return result.get("task", result)


def _text_of_task(task: dict[str, Any]) -> str:
    """The agent's answer: every text part of every artifact, in order."""
    parts = [part for artifact in task.get("artifacts", []) for part in artifact.get("parts", [])]
    return _text_of_parts(parts)


def _text_of_parts(parts: list[dict[str, Any]]) -> str:
    """Join text parts; non-text parts (files, data) are ignored because GAIA only consumes text."""
    return "\n".join(part["text"] for part in parts if "text" in part).strip()
