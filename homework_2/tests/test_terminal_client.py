"""python -m coordinator.main: a thin HTTP client of the API, one-shot or as a chat loop."""

import json

import httpx
import pytest

from coordinator import main as terminal


class FakeApi:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        # The interactive path fetches the briefing (a GET with no JSON body)
        # before it ever posts a question, so that has to be handled first.
        if request.method == "GET" and request.url.path == "/briefing":
            return httpx.Response(200, json={"thread_id": "t", "briefing": ""})
        body = json.loads(request.content)
        self.requests.append(body)
        return httpx.Response(200, json={"answer": f"answer to {body['question']}", "thread_id": body.get("thread_id") or "t", "trace": ["call_agent(agent_id='aether') -> x"]})

    def client_factory(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), base_url="http://gaia")


def test_one_shot_posts_question_and_prints_trace_and_answer(capsys: pytest.CaptureFixture) -> None:
    fake = FakeApi()

    terminal.run(["Sacred Lands as forest?"], client_factory=fake.client_factory)

    assert fake.requests[0]["question"] == "Sacred Lands as forest?"
    out = capsys.readouterr().out
    assert "call_agent(agent_id='aether') -> x" in out
    assert "answer to Sacred Lands as forest?" in out


def test_chat_loop_reuses_one_thread_until_quit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    fake = FakeApi()
    lines = iter(["forest?", "grassland instead?", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    terminal.run([], client_factory=fake.client_factory)

    assert [r["question"] for r in fake.requests] == ["forest?", "grassland instead?"]
    assert fake.requests[0]["thread_id"] == fake.requests[1]["thread_id"]
    assert "answer to grassland instead?" in capsys.readouterr().out


def test_unreachable_api_is_reported_not_raised(capsys: pytest.CaptureFixture) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    factory = lambda: httpx.Client(transport=httpx.MockTransport(down), base_url="http://gaia")
    terminal.run(["hi"], client_factory=factory)

    assert "cannot reach GAIA" in capsys.readouterr().out


def test_interactive_start_prints_the_briefing_and_uses_its_thread(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/briefing":
            return httpx.Response(200, json={"thread_id": "operations", "briefing": "blight in the Cut"})
        return httpx.Response(200, json={"answer": "done", "thread_id": "operations", "trace": []})

    monkeypatch.setattr("builtins.input", lambda _: "quit")
    terminal.run([], lambda: httpx.Client(transport=httpx.MockTransport(handle), base_url="http://coordinator"))

    printed = capsys.readouterr().out
    assert "blight in the Cut" in printed
    assert "operations" in printed
