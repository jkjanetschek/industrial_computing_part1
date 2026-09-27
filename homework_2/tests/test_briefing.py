"""The startup handshake: a deterministic fan-out, then one GAIA turn."""

import asyncio

import httpx
import pytest

from coordinator import briefing, gaia
from shared.schemas import AgentCard, AgentListing
from test_coordinator import ScriptedModel
from langchain_core.messages import AIMessage


def card(agent_id: str) -> AgentCard:
    return AgentCard(
        id=agent_id,
        name=agent_id,
        description=f"{agent_id} subfunction",
        url=f"http://{agent_id}:8001",
        capabilities=[agent_id],
        skills=[],
    )


LISTING = AgentListing(agents=[card("aether"), card("demeter"), card("hephaestus")])


class FakeAgentClient:
    """Records every call; optionally raises for one named agent."""

    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing = failing

    async def call(self, agent_card: AgentCard, message: str) -> str:
        self.calls.append((agent_card.id, message))
        if agent_card.id == self.failing:
            raise httpx.ConnectError("agent down")
        return f"{agent_card.id} reports one incident"


@pytest.fixture(autouse=True)
def fake_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def listing() -> AgentListing:
        return LISTING

    monkeypatch.setattr(briefing, "get_agent_listing", listing)


@pytest.mark.anyio
async def test_every_registered_agent_is_asked_exactly_once() -> None:
    client = FakeAgentClient()

    reports = await briefing.collect_reports(client)

    # Calls run concurrently, so only the set of callees is fixed; the reports
    # themselves come back keyed in listing order regardless of completion order.
    assert sorted(agent_id for agent_id, _ in client.calls) == ["aether", "demeter", "hephaestus"]
    assert list(reports) == ["aether", "demeter", "hephaestus"]


@pytest.mark.anyio
async def test_the_status_request_is_the_same_for_every_agent() -> None:
    client = FakeAgentClient()

    await briefing.collect_reports(client)

    assert {message for _, message in client.calls} == {briefing.STATUS_REQUEST}


@pytest.mark.anyio
async def test_an_unreachable_agent_does_not_prevent_a_briefing() -> None:
    reports = await briefing.collect_reports(FakeAgentClient(failing="demeter"))

    assert reports["demeter"] == briefing.UNREACHABLE
    assert reports["aether"].startswith("aether")


@pytest.mark.anyio
async def test_the_compose_turn_carries_every_report(monkeypatch: pytest.MonkeyPatch) -> None:
    async def two_reports(client=None) -> dict[str, str]:
        return {"aether": "clear", "demeter": "blight"}

    monkeypatch.setattr(briefing, "collect_reports", two_reports)
    model = ScriptedModel(messages=iter([AIMessage(content="the briefing"), AIMessage(content="the summary")]))
    coordinator = gaia.build_coordinator(model=model, tools=[])

    await briefing.run_handshake(coordinator)

    sent = gaia.thread_messages(coordinator, briefing.BRIEFING_THREAD_ID)[0].text
    assert "AETHER: clear" in sent
    assert "DEMETER: blight" in sent


@pytest.mark.anyio
async def test_the_handshake_composes_then_summarises_on_one_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_reports(client=None) -> dict[str, str]:
        return {"aether": "clear"}

    monkeypatch.setattr(briefing, "collect_reports", no_reports)
    model = ScriptedModel(messages=iter([AIMessage(content="the full briefing"), AIMessage(content="the summary")]))
    coordinator = gaia.build_coordinator(model=model, tools=[])

    returned = await briefing.run_handshake(coordinator)

    assert returned == "the summary"
    answers = [turn.answer for turn in gaia.turns(gaia.thread_messages(coordinator, briefing.BRIEFING_THREAD_ID))]
    assert answers == ["the full briefing", "the summary"]


@pytest.mark.anyio
async def test_both_handshake_prompts_are_marked_and_marked_differently(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_reports(client=None) -> dict[str, str]:
        return {"aether": "clear"}

    monkeypatch.setattr(briefing, "collect_reports", no_reports)
    model = ScriptedModel(messages=iter([AIMessage(content="full"), AIMessage(content="summary")]))
    coordinator = gaia.build_coordinator(model=model, tools=[])

    await briefing.run_handshake(coordinator)

    markers = [turn.marker for turn in gaia.turns(gaia.thread_messages(coordinator, briefing.BRIEFING_THREAD_ID))]
    assert markers == [briefing.BRIEFING_FULL, briefing.BRIEFING_SUMMARY]


@pytest.mark.anyio
async def test_the_fan_out_records_a_line_per_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from coordinator import activity

    log = activity.ActivityLog()
    monkeypatch.setattr(briefing, "activity_log", log)

    await briefing.collect_reports(FakeAgentClient(failing="demeter"))

    lines = [event.text for event in log.since(briefing.BRIEFING_THREAD_ID, 0)]
    assert any("aether" in line and line.startswith("->") for line in lines)
    assert any("demeter" in line and briefing.UNREACHABLE in line for line in lines)
    assert [event.kind for event in log.since(briefing.BRIEFING_THREAD_ID, 0)].count("error") == 1


@pytest.mark.anyio
async def test_the_handshake_is_bracketed_by_its_own_start_and_done_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    from coordinator import activity

    async def no_reports(client=None) -> dict[str, str]:
        return {"aether": "clear"}

    monkeypatch.setattr(briefing, "collect_reports", no_reports)
    # Both modules hold their own imported reference to activity_log, so both
    # need patching for the inner turns' start/done lines to land in the same log.
    log = activity.ActivityLog()
    monkeypatch.setattr(briefing, "activity_log", log)
    monkeypatch.setattr(gaia, "activity_log", log)
    model = ScriptedModel(messages=iter([AIMessage(content="the full briefing"), AIMessage(content="the summary")]))
    coordinator = gaia.build_coordinator(model=model, tools=[])

    await briefing.run_handshake(coordinator)

    events = log.since(briefing.BRIEFING_THREAD_ID, 0)
    assert events[0].kind == "start"
    assert events[0].text == "startup handshake begins"
    assert events[-1].kind == "done"
    assert events[-1].text == "startup handshake complete"
    inner_kinds = [event.kind for event in events[1:-1]]
    assert inner_kinds.count("start") >= 2
    assert inner_kinds.count("done") >= 2


@pytest.mark.anyio
async def test_a_second_handshake_cannot_interleave_with_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two handshakes on the operations thread run one after the other, not turn by turn.

    A double click on "Request status report" starts two. If the thread lock is
    taken per turn instead of per handshake, the second compose turn lands
    between the first compose turn and its summary, so the page drops the older
    full briefing and "the briefing you just wrote" names the wrong message.
    """

    async def no_reports(client=None) -> dict[str, str]:
        return {"aether": "clear"}

    monkeypatch.setattr(briefing, "collect_reports", no_reports)
    model = ScriptedModel(messages=iter([AIMessage(content=f"answer {index}") for index in range(4)]))
    coordinator = gaia.build_coordinator(model=model, tools=[])

    await asyncio.gather(briefing.run_handshake(coordinator), briefing.run_handshake(coordinator))

    markers = [turn.marker for turn in gaia.turns(gaia.thread_messages(coordinator, briefing.BRIEFING_THREAD_ID))]
    assert markers == [
        briefing.BRIEFING_FULL,
        briefing.BRIEFING_SUMMARY,
        briefing.BRIEFING_FULL,
        briefing.BRIEFING_SUMMARY,
    ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
