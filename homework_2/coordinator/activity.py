"""What GAIA is doing, while she does it.

The web page shows a finished answer and nothing in between, so a turn that
spends thirty seconds across four A2A calls looks identical to a hung one. This
module records those steps so a front end can show them.

Pattern: an observer fed by LangChain's callback protocol, writing into a
bounded per-thread ring buffer. The buffer is what makes the record survive a
page reload, and what lets a browser that connects late still see the startup
handshake, which runs in the coordinator's lifespan before any browser exists.

Bounded on both axes deliberately. Events per thread are capped because a long
conversation would otherwise grow without limit; threads themselves are capped
because any caller may name a thread id it has never used, so an unbounded mapping would grow for
the life of the process.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from coordinator.tools import ERROR_PREFIX

_log = logging.getLogger("gaia")

MAX_EVENTS_PER_THREAD = 500
MAX_THREADS = 50
CLOCK_FORMAT = "%H:%M:%S"

# Excluded from a rendered call: the full request text, which is prose and would
# swamp the line. Only the routing arguments say anything useful at a glance.
NOT_ROUTING = ("message",)

ERROR_KIND = "error"
BYTES_PER_KB = 1000
# An error line quotes the failure but must not paste a whole agent reply into the rail.
ERROR_EXCERPT_MAX_CHARS = 120


def routing_args(args: dict) -> str:
    """Render a tool call's routing arguments, dropping the message body.

    Lives here rather than in gaia.py so that gaia.py can import this module
    without a cycle: gaia needs the recorder, and nothing here needs gaia.
    """
    shown = {key: value for key, value in args.items() if key not in NOT_ROUTING}
    return ", ".join(f"{key}={value!r}" for key, value in shown.items())


@dataclass(frozen=True)
class Event:
    """One line in the rail. `kind` drives its colour, nothing more."""

    n: int
    at: str
    kind: str
    text: str

    def as_json(self) -> str:
        """The SSE data payload; the browser's rail parses exactly these four keys."""
        return json.dumps({"n": self.n, "at": self.at, "kind": self.kind, "text": self.text})


class ActivityLog:
    """A bounded record of recent events, per conversation thread."""

    def __init__(self) -> None:
        self._threads: OrderedDict[str, deque[Event]] = OrderedDict()
        self._last_number = 0
        # One queue per connected browser. A shared asyncio.Event would be
        # smaller and wrong: set-then-clear is missed by any subscriber that has
        # not yet reached its await, and that line never reaches the page.
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    @contextlib.contextmanager
    def subscribe(self, thread_id: str) -> Iterator[asyncio.Queue]:
        """A queue fed every event appended to this thread, for as long as the caller holds it.

        The finally is load-bearing: without it every page reload would leave a
        dead queue behind, and append() would push into it forever.

        Dropping the key once its last queue goes is load-bearing too. Unlike
        _threads there is no cap here, and thread_id arrives straight from an
        unauthenticated query parameter on GET /activity/stream, so a loop of
        requests for junk ids would otherwise grow this map for the life of the
        process, one empty set per id.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(thread_id, set()).add(queue)
        try:
            yield queue
        finally:
            listeners = self._subscribers.get(thread_id, set())
            listeners.discard(queue)
            if not listeners:
                self._subscribers.pop(thread_id, None)

    def append(self, thread_id: str, kind: str, text: str) -> Event:
        """Record one event, push it to any live subscriber, and log it.

        The log line is not a second opinion about what matters: it is the same
        event the rail shows. A reviewer reading the file afterwards and an
        operator who watched the rail at the time cannot end up disagreeing.
        """
        events = self._thread(thread_id)
        _log.log(logging.WARNING if kind == ERROR_KIND else logging.INFO, "%s", text)
        self._last_number += 1
        event = Event(n=self._last_number, at=datetime.now().strftime(CLOCK_FORMAT), kind=kind, text=text)
        events.append(event)
        for queue in self._subscribers.get(thread_id, ()):
            queue.put_nowait(event)
        return event

    @property
    def newest_number(self) -> int:
        """The highest event number this process has issued; 0 before any append."""
        return self._last_number

    def since(self, thread_id: str, after: int) -> list[Event]:
        """Everything this thread holds that is newer than `after`.

        Must not create a thread entry: a read exposed to query parameters must
        not mutate the map, or stray requests for junk ids will evict real
        conversations.
        """
        if thread_id not in self._threads:
            return []
        self._threads.move_to_end(thread_id)
        return [event for event in self._threads[thread_id] if event.n > after]

    def _thread(self, thread_id: str) -> deque[Event]:
        if thread_id in self._threads:
            self._threads.move_to_end(thread_id)
            return self._threads[thread_id]
        self._threads[thread_id] = deque(maxlen=MAX_EVENTS_PER_THREAD)
        if len(self._threads) > MAX_THREADS:
            self._threads.popitem(last=False)  # oldest touched thread
        return self._threads[thread_id]


class ActivityRecorder(BaseCallbackHandler):
    """Translates one GAIA turn's callbacks into rail lines.

    Pattern: LangChain's callback protocol, which LangGraph propagates into both
    the model and the tools. Observing from here rather than instrumenting
    tools.py means tools.py keeps doing one job, and it is the only way to see
    "thinking", which happens between tool calls inside the model.

    One instance per turn, because it holds that turn's in-flight tool calls.
    """

    # Run on the event loop, not on an executor thread. langchain-core dispatches
    # a sync handler through run_in_executor unless this is set, and append()
    # pushes into asyncio.Queues, which are not thread-safe. Every method here is
    # a dict write and a list append, so inline costs nothing and keeps the
    # events in order with the start/done lines that gaia.py writes on the loop.
    run_inline = True

    def __init__(self, log: ActivityLog, thread_id: str) -> None:
        self._log = log
        self._thread_id = thread_id
        self._in_flight: dict[UUID, tuple[str, float]] = {}
        self.tool_calls = 0

    def on_chat_model_start(self, serialized: dict, messages: list, **kwargs: Any) -> None:
        """The model is about to reason: between tool calls this is the only sign of life."""
        self._log.append(self._thread_id, "think", "thinking")

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, inputs: dict | None = None, **kwargs: Any) -> None:
        """A tool call begins: remember its name and start time under LangChain's run_id so on_tool_end can pair them."""
        name = serialized.get("name", "tool")
        self._in_flight[run_id] = (name, time.monotonic())
        self.tool_calls += 1
        self._log.append(self._thread_id, "call", f"-> {name}({routing_args(inputs or {})})")

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """A tool call returned: an ok line with size and elapsed time, or an error line if the tool reported one."""
        name, elapsed = self._finish(run_id)
        text = str(getattr(output, "content", output))
        # A sub-agent that is unreachable does NOT raise: tools.call_agent
        # returns a string starting with ERROR_PREFIX by deliberate design, so
        # without this check a failed A2A call would render as a success.
        if text.startswith(ERROR_PREFIX):
            self._log.append(self._thread_id, "error", f"<- {text[:ERROR_EXCERPT_MAX_CHARS]} ({elapsed:.1f}s)")
            return
        size = len(text) / BYTES_PER_KB
        self._log.append(self._thread_id, "ok", f"<- ok, {size:.1f} kB ({elapsed:.1f}s)")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """A tool RAISED (not the designed error string): should not happen for our two tools, shown if it does."""
        name, elapsed = self._finish(run_id)
        self._log.append(self._thread_id, "error", f"<- {name} raised: {error} ({elapsed:.1f}s)")

    def _finish(self, run_id: UUID) -> tuple[str, float]:
        """Pop the in-flight entry for this run and return (name, seconds elapsed); tolerant of an unknown run_id."""
        name, started = self._in_flight.pop(run_id, ("tool", time.monotonic()))
        return name, time.monotonic() - started


# One log per process, like tools.agent_client. Tests swap this attribute.
activity_log = ActivityLog()
