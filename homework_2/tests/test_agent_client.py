"""The coordinator's A2A call: message/send, poll tasks/get, return the text; same in both AGENT_CALL_MODEs."""

import json

import httpx
import pytest

from coordinator import agent_client
from coordinator.agent_client import AgentCallError, AgentClient
from shared.schemas import AgentCard
from shared.settings import settings

CARD = AgentCard(id="aether", name="aether", url="http://aether-agent:8001", capabilities=["atmosphere"])
ANSWER = "Not viable: toxicity 12 %, 14 scrubbing days."


class FakeA2AServer:
    """Speaks fasta2a 2.x's wire shape: message/send -> result.task, tasks/get -> result (the task)."""

    def __init__(self, final_state: str = "completed", polls_until_done: int = 1) -> None:
        self.final_state = final_state
        self.polls_left = polls_until_done
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if body["method"] == "message/send":
            return self._rpc(body["id"], {"task": {"id": "task-1", "status": {"state": "submitted"}}})
        if body["method"] == "tasks/get":
            if self.polls_left > 0:
                self.polls_left -= 1
                return self._rpc(body["id"], {"id": "task-1", "status": {"state": "working"}})
            return self._rpc(body["id"], self._finished_task())
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "no such method"}})

    def _finished_task(self) -> dict:
        task = {"id": "task-1", "status": {"state": self.final_state}}
        if self.final_state == "completed":
            task["artifacts"] = [{"parts": [{"kind": "text", "text": ANSWER}]}]
        return task

    @staticmethod
    def _rpc(request_id: str, result: dict) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": result})

    def client_factory(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url=base_url)

    def methods(self) -> list[str]:
        return [json.loads(r.content)["method"] for r in self.requests]


def set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(
        agent_client,
        "settings",
        settings.model_copy(update={"AGENT_CALL_MODE": mode, "LITELLM_PROXY_URL": "http://litellm:4000", "LITELLM_PROXY_API_KEY": "sk-test"}),
    )


@pytest.fixture(autouse=True)
def no_poll_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_client, "POLL_INTERVAL_SECONDS", 0.0)


@pytest.mark.anyio
async def test_direct_mode_posts_to_card_url_and_returns_artifact_text(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "direct")
    server = FakeA2AServer(polls_until_done=2)

    answer = await AgentClient(server.client_factory).call(CARD, "Is the Sacred Lands viable?")

    assert answer == ANSWER
    assert server.methods() == ["message/send", "tasks/get", "tasks/get", "tasks/get"]
    assert all(r.url.host == "aether-agent" and r.url.path == "/" for r in server.requests)
    sent = json.loads(server.requests[0].content)
    assert sent["params"]["message"]["parts"] == [{"kind": "text", "text": "Is the Sacred Lands viable?"}]
    assert sent["params"]["message"]["role"] == "user"


@pytest.mark.anyio
async def test_litellm_mode_relays_through_proxy_with_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "litellm")
    server = FakeA2AServer()

    answer = await AgentClient(server.client_factory).call(CARD, "hi")

    assert answer == ANSWER
    for request in server.requests:
        assert request.url.host == "litellm"
        assert request.url.path == "/a2a/aether"
        assert request.headers["Authorization"] == "Bearer sk-test"


@pytest.mark.anyio
async def test_failed_task_raises_agent_call_error(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "direct")
    server = FakeA2AServer(final_state="failed")

    with pytest.raises(AgentCallError, match="failed"):
        await AgentClient(server.client_factory).call(CARD, "hi")


@pytest.mark.anyio
async def test_jsonrpc_error_raises_agent_call_error(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "direct")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32000, "message": "agent exploded"}})

    factory = lambda base_url: httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)
    with pytest.raises(AgentCallError, match="agent exploded"):
        await AgentClient(factory).call(CARD, "hi")


@pytest.mark.anyio
async def test_immediate_message_result_is_returned_without_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "direct")

    def handle(request: httpx.Request) -> httpx.Response:
        message = {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "instant"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": message})

    factory = lambda base_url: httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=base_url)
    assert await AgentClient(factory).call(CARD, "hi") == "instant"


@pytest.mark.anyio
async def test_gives_up_after_max_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "direct")
    monkeypatch.setattr(agent_client, "MAX_POLLS", 3)
    server = FakeA2AServer(polls_until_done=99)

    with pytest.raises(AgentCallError, match="did not finish"):
        await AgentClient(server.client_factory).call(CARD, "hi")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
