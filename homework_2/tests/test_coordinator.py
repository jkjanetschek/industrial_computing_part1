"""GAIA's loop with a scripted model: discovery, three calls in order, one composed answer, memory per thread."""

import asyncio
import time

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from coordinator import gaia

QUESTION = "Can we reforest the Sacred Lands into temperate forest before next season?"
FINAL = "Yes: 14 days scrubbing and 18 days fabrication overlap, then 10 days of work, about 28 days."


class ScriptedModel(GenericFakeChatModel):
    """Replays fixed AIMessages; create_agent binds tools, which the generic fake does not support."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call(call_id: str, name: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def scripted_run() -> ScriptedModel:
    return ScriptedModel(
        messages=iter(
            [
                tool_call("c1", "lookup_agents", capability=""),
                tool_call("c2", "call_agent", agent_id="aether", message="Sacred Lands viable?"),
                tool_call("c3", "call_agent", agent_id="demeter", message="Sacred Lands temperate forest"),
                tool_call("c4", "call_agent", agent_id="hephaestus", message="grazer 3200 hours"),
                AIMessage(content=FINAL),
            ]
        )
    )


calls: list[tuple[str, dict]] = []


@tool
async def lookup_agents(capability: str) -> list[dict]:
    """fake"""
    calls.append(("lookup_agents", {"capability": capability}))
    return [{"id": "aether"}, {"id": "demeter"}, {"id": "hephaestus"}]


@tool
async def call_agent(agent_id: str, message: str) -> str:
    """fake"""
    calls.append(("call_agent", {"agent_id": agent_id}))
    return f"{agent_id}: figures"


@pytest.fixture(autouse=True)
def reset_calls() -> None:
    calls.clear()


@pytest.mark.anyio
async def test_gaia_discovers_then_calls_all_three_and_answers() -> None:
    coordinator = gaia.build_coordinator(model=scripted_run(), tools=[lookup_agents, call_agent])

    answer = await gaia.ask(coordinator, QUESTION, thread_id="t-1")

    assert answer == FINAL
    assert [name for name, _ in calls] == ["lookup_agents", "call_agent", "call_agent", "call_agent"]
    assert [args.get("agent_id") for _, args in calls[1:]] == ["aether", "demeter", "hephaestus"]


@pytest.mark.anyio
async def test_thread_id_keeps_history_between_questions() -> None:
    model = ScriptedModel(messages=iter([AIMessage(content="first"), AIMessage(content="second")]))
    coordinator = gaia.build_coordinator(model=model, tools=[lookup_agents, call_agent])

    await gaia.ask(coordinator, "one", thread_id="same")
    await gaia.ask(coordinator, "two", thread_id="same")

    history = coordinator.get_state({"configurable": {"thread_id": "same"}}).values["messages"]
    assert [m.content for m in history if m.type == "human"] == ["one", "two"]


@pytest.mark.anyio
async def test_tool_trace_lists_agent_calls_in_order() -> None:
    coordinator = gaia.build_coordinator(model=scripted_run(), tools=[lookup_agents, call_agent])
    await gaia.ask(coordinator, QUESTION, thread_id="t-2")

    trace = gaia.tool_trace(coordinator.get_state({"configurable": {"thread_id": "t-2"}}).values["messages"])

    assert trace == [
        # LangChain JSON-encodes non-string tool results into the ToolMessage.
        'lookup_agents(capability=\'\') -> [{"id": "aether"}, {"id": "demeter"}, {"id": "hephaestus"}]',
        "call_agent(agent_id='aether') -> aether: figures",
        "call_agent(agent_id='demeter') -> demeter: figures",
        "call_agent(agent_id='hephaestus') -> hephaestus: figures",
    ]


class SlowCoordinator:
    """A minimal ask()-compatible stand-in that takes real time inside ainvoke,
    so a test can observe whether two calls on the same thread overlapped."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []

    async def ainvoke(self, state: dict, config: dict) -> dict:
        thread_id = config["configurable"]["thread_id"]
        self.log.append(("start", thread_id))
        await asyncio.sleep(0.05)
        self.log.append(("end", thread_id))
        return {"messages": [AIMessage(content=f"done-{thread_id}")]}


@pytest.mark.anyio
async def test_ask_serialises_two_calls_on_the_same_thread() -> None:
    """A refresh landing on the same thread as an in-flight question must queue,
    not interleave: the first call's ainvoke has to finish before the second starts."""
    coordinator = SlowCoordinator()

    await asyncio.gather(
        gaia.ask(coordinator, "first", "same-thread"),
        gaia.ask(coordinator, "second", "same-thread"),
    )

    starts = [i for i, (event, _) in enumerate(coordinator.log) if event == "start"]
    ends = [i for i, (event, _) in enumerate(coordinator.log) if event == "end"]
    assert ends[0] < starts[1]


@pytest.mark.anyio
async def test_ask_does_not_serialise_calls_on_different_threads() -> None:
    """The lock is per thread_id, not global: two unrelated threads must run at once."""
    coordinator = SlowCoordinator()

    started = time.monotonic()
    await asyncio.gather(
        gaia.ask(coordinator, "first", "thread-a"),
        gaia.ask(coordinator, "second", "thread-b"),
    )
    elapsed = time.monotonic() - started

    # Serialised, two 0.05s calls would take about 0.1s; concurrent, about 0.05s.
    assert elapsed < 0.09


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("latex", "plain"),
    [
        (r"Toxicity is $12\%$, above $5\%$ ($12\% > 5\%$).", "Toxicity is 12%, above 5% (12% > 5%)."),
        (r"gate: $\max(14, 18) = 18$ days", "gate: max(14, 18) = 18 days"),
        (r"$$T=\max(D_\text{scrub}, D_\text{fab})+D_\text{work}$$", "T=max(D_scrub, D_fab)+D_work"),
        (r"Grazer $3{,}200$ h and $\ge 4$ units, $10 \times 2$", "Grazer 3,200 h and ≥ 4 units, 10 × 2"),
        ("costs $5 and then $7 more", "costs $5 and then $7 more"),
    ],
)
def test_plain_math_strips_the_latex_gpt5_emits(latex: str, plain: str) -> None:
    assert gaia.plain_math(latex) == plain


@pytest.mark.anyio
async def test_turns_and_ask_both_return_plain_math() -> None:
    model = ScriptedModel(messages=iter([AIMessage(content=r"gate $\max(14, 18)$ = $18$ days")]))
    coordinator = gaia.build_coordinator(model=model, tools=[lookup_agents, call_agent])

    answer = await gaia.ask(coordinator, "forest?", thread_id="latex")
    stored = gaia.turns(gaia.thread_messages(coordinator, "latex"))[-1].answer

    assert answer == stored == "gate max(14, 18) = 18 days"


@pytest.mark.anyio
async def test_a_marked_message_keeps_its_marker_through_the_checkpointer() -> None:
    coordinator = gaia.build_coordinator(model=ScriptedModel(messages=iter([AIMessage(content="ok")])), tools=[])

    await gaia.ask_message(coordinator, HumanMessage("synthetic", name="briefing_full"), "t1")

    [turn] = gaia.turns(gaia.thread_messages(coordinator, "t1"))
    assert turn.marker == "briefing_full"


@pytest.mark.anyio
async def test_an_operator_question_carries_no_marker() -> None:
    coordinator = gaia.build_coordinator(model=ScriptedModel(messages=iter([AIMessage(content="ok")])), tools=[])

    await gaia.ask(coordinator, "a real question", "t2")

    [turn] = gaia.turns(gaia.thread_messages(coordinator, "t2"))
    assert turn.marker is None


@pytest.mark.anyio
async def test_a_turn_is_bracketed_by_a_start_and_a_done_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from coordinator import activity

    log = activity.ActivityLog()
    monkeypatch.setattr(activity, "activity_log", log)
    monkeypatch.setattr(gaia, "activity_log", log)
    coordinator = gaia.build_coordinator(model=scripted_run(), tools=[lookup_agents, call_agent])

    await gaia.ask(coordinator, QUESTION, "traced")

    kinds = [event.kind for event in log.since("traced", 0)]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "call" in kinds and "ok" in kinds


@pytest.mark.anyio
async def test_callback_events_are_appended_on_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder pushes into asyncio.Queues, which are not thread-safe, so its
    callbacks must run inline on the loop rather than on an executor thread.
    A tripwire test asserts run_inline is True; this one proves the effect, and
    that the recorder fired at all rather than merely running on the loop."""
    import threading

    from coordinator import activity

    log = activity.ActivityLog()
    monkeypatch.setattr(gaia, "activity_log", log)
    loop_thread = threading.get_ident()
    seen_threads: set[int] = set()
    real_append = log.append

    def spying_append(thread_id: str, kind: str, text: str):
        seen_threads.add(threading.get_ident())
        return real_append(thread_id, kind, text)

    monkeypatch.setattr(log, "append", spying_append)
    coordinator = gaia.build_coordinator(model=scripted_run(), tools=[lookup_agents, call_agent])

    await gaia.ask(coordinator, QUESTION, "loop-thread")

    assert seen_threads == {loop_thread}
    kinds = [event.kind for event in log.since("loop-thread", 0)]
    assert "call" in kinds


class RaisingModel(ScriptedModel):
    """A model whose generation step always fails, to prove a crashed turn
    leaves an error line rather than a silently truncated rail."""

    def _generate(self, *args, **kwargs):
        raise RuntimeError("proxy down")

    async def _agenerate(self, *args, **kwargs):
        raise RuntimeError("proxy down")


@pytest.mark.anyio
async def test_a_turn_that_raises_leaves_an_error_line_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    from coordinator import activity

    log = activity.ActivityLog()
    monkeypatch.setattr(gaia, "activity_log", log)
    coordinator = gaia.build_coordinator(model=RaisingModel(messages=iter([])), tools=[lookup_agents, call_agent])

    with pytest.raises(RuntimeError):
        await gaia.ask(coordinator, QUESTION, "crashed")

    kinds = [event.kind for event in log.since("crashed", 0)]
    assert kinds[-1] == "error"
    assert "done" not in kinds
    [error_event] = [event for event in log.since("crashed", 0) if event.kind == "error"]
    assert "proxy down" in error_event.text
