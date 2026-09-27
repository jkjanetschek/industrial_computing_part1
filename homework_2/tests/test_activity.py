"""The activity log: a bounded per-thread record of what GAIA did."""

import pytest
from uuid import uuid4

from coordinator.activity import MAX_EVENTS_PER_THREAD, MAX_THREADS, ActivityLog, ActivityRecorder


def test_append_returns_events_with_increasing_numbers() -> None:
    log = ActivityLog()

    first = log.append("t", "start", "turn start")
    second = log.append("t", "done", "answer ready")

    assert second.n > first.n
    assert first.kind == "start"
    assert first.text == "turn start"


def test_newest_number_is_zero_before_any_append_and_tracks_the_last_one() -> None:
    log = ActivityLog()
    assert log.newest_number == 0

    log.append("t", "call", "one")
    last = log.append("t", "call", "two")

    assert log.newest_number == last.n


def test_since_returns_only_what_is_newer() -> None:
    log = ActivityLog()
    first = log.append("t", "call", "one")
    log.append("t", "call", "two")

    assert [event.text for event in log.since("t", first.n)] == ["two"]


def test_threads_do_not_see_each_others_events() -> None:
    log = ActivityLog()
    log.append("a", "call", "for a")
    log.append("b", "call", "for b")

    assert [event.text for event in log.since("a", 0)] == ["for a"]


def test_a_thread_keeps_only_its_most_recent_events() -> None:
    log = ActivityLog()
    for index in range(MAX_EVENTS_PER_THREAD + 10):
        log.append("t", "call", f"line {index}")

    kept = log.since("t", 0)
    assert len(kept) == MAX_EVENTS_PER_THREAD
    assert kept[-1].text == f"line {MAX_EVENTS_PER_THREAD + 9}"


def test_the_oldest_thread_is_evicted_once_too_many_exist() -> None:
    log = ActivityLog()
    log.append("oldest", "call", "first ever")
    for index in range(MAX_THREADS):
        log.append(f"t{index}", "call", "later")

    assert log.since("oldest", 0) == []


def test_an_event_serialises_to_the_fields_the_rail_reads() -> None:
    import json

    event = json.loads(ActivityLog().append("t", "ok", "<- ok").as_json())

    assert set(event) == {"n", "at", "kind", "text"}


def test_since_on_unknown_thread_does_not_create_an_entry() -> None:
    log = ActivityLog()
    initial_count = len(log._threads)

    result = log.since("unknown", 0)

    assert result == []
    assert len(log._threads) == initial_count


def test_many_since_calls_on_unknown_threads_do_not_evict_real_events() -> None:
    log = ActivityLog()
    real_event = log.append("real_thread", "call", "real data")

    # Make many since() calls with unknown thread ids, more than MAX_THREADS
    for index in range(MAX_THREADS + 10):
        log.since(f"unknown_{index}", 0)

    # The real thread's event should still be there
    assert log.since("real_thread", 0) == [real_event]


@pytest.mark.anyio
async def test_a_subscriber_receives_events_appended_to_its_thread() -> None:
    log = ActivityLog()

    with log.subscribe("t") as queue:
        log.append("t", "call", "-> aether")

        assert (await queue.get()).text == "-> aether"


@pytest.mark.anyio
async def test_every_subscriber_on_a_thread_receives_the_same_event() -> None:
    log = ActivityLog()

    with log.subscribe("t") as first, log.subscribe("t") as second:
        log.append("t", "call", "-> aether")

        assert (await first.get()).text == "-> aether"
        assert (await second.get()).text == "-> aether"


@pytest.mark.anyio
async def test_a_subscriber_does_not_receive_another_threads_events() -> None:
    log = ActivityLog()

    with log.subscribe("a") as queue:
        log.append("b", "call", "-> for b")

        assert queue.empty()


@pytest.mark.anyio
async def test_leaving_the_context_unregisters_the_subscriber() -> None:
    log = ActivityLog()

    with log.subscribe("t") as queue:
        pass
    log.append("t", "call", "after the browser left")

    assert queue.empty()


@pytest.mark.anyio
async def test_leaving_the_context_leaves_no_key_behind() -> None:
    """The map must shrink back, not merely hold empty sets.

    thread_id comes straight from an unauthenticated query parameter and
    nothing caps this map, so asserting on its length is the point: an empty
    leftover set still behaves correctly, it just never goes away.
    """
    log = ActivityLog()

    for index in range(MAX_THREADS + 10):
        with log.subscribe(f"thread-{index}"):
            pass

    assert len(log._subscribers) == 0


@pytest.mark.anyio
async def test_one_subscriber_leaving_does_not_unregister_the_other() -> None:
    log = ActivityLog()

    with log.subscribe("t") as staying:
        with log.subscribe("t"):
            pass
        log.append("t", "call", "-> aether")

    assert (await staying.get()).text == "-> aether"


def test_appending_with_nobody_listening_is_fine() -> None:
    ActivityLog().append("t", "call", "-> aether")


def test_a_model_call_records_that_gaia_is_thinking() -> None:
    log = ActivityLog()

    ActivityRecorder(log, "t").on_chat_model_start({}, [])

    assert [event.kind for event in log.since("t", 0)] == ["think"]


def test_a_tool_call_records_its_routing_arguments_without_the_message_body() -> None:
    log = ActivityLog()

    ActivityRecorder(log, "t").on_tool_start(
        {"name": "call_agent"}, "", run_id=uuid4(), inputs={"agent_id": "aether", "message": "a long prose request"}
    )

    [event] = log.since("t", 0)
    assert event.text == "-> call_agent(agent_id='aether')"
    assert "long prose" not in event.text


def test_a_successful_tool_result_is_recorded_as_ok() -> None:
    log = ActivityLog()
    recorder, run_id = ActivityRecorder(log, "t"), uuid4()

    recorder.on_tool_start({"name": "call_agent"}, "", run_id=run_id, inputs={"agent_id": "aether"})
    recorder.on_tool_end("aether reports one incident", run_id=run_id)

    assert [event.kind for event in log.since("t", 0)] == ["call", "ok"]


def test_an_error_string_from_a_reachable_agent_is_recorded_as_an_error() -> None:
    log = ActivityLog()
    recorder, run_id = ActivityRecorder(log, "t"), uuid4()

    recorder.on_tool_start({"name": "call_agent"}, "", run_id=run_id, inputs={"agent_id": "aether"})
    recorder.on_tool_end("error: agent 'aether' call failed: timeout", run_id=run_id)

    assert [event.kind for event in log.since("t", 0)] == ["call", "error"]


def test_a_raising_tool_is_recorded_as_an_error() -> None:
    log = ActivityLog()
    recorder, run_id = ActivityRecorder(log, "t"), uuid4()

    recorder.on_tool_start({"name": "call_agent"}, "", run_id=run_id, inputs={"agent_id": "aether"})
    recorder.on_tool_error(RuntimeError("boom"), run_id=run_id)

    assert log.since("t", 0)[-1].kind == "error"


def test_the_recorder_counts_the_tool_calls_it_saw() -> None:
    recorder = ActivityRecorder(ActivityLog(), "t")

    recorder.on_tool_start({"name": "call_agent"}, "", run_id=uuid4(), inputs={"agent_id": "aether"})
    recorder.on_tool_start({"name": "call_agent"}, "", run_id=uuid4(), inputs={"agent_id": "demeter"})

    assert recorder.tool_calls == 2


def test_the_recorder_runs_inline_so_it_can_touch_asyncio_queues() -> None:
    assert ActivityRecorder.run_inline is True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
