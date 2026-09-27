"""GAIA: the coordinator's agent core. A LangChain ReAct agent whose only
tools are "find a subfunction" and "ask a subfunction"; the subfunctions are
the A2A agents it discovers through the registry at run time.

This module builds and runs the agent; it has no front end of its own.
coordinator/api.py hosts ONE instance behind HTTP (web page + POST /ask) and
coordinator/main.py is a terminal client of that API. Keeping a single running
GAIA means every front end shares the same conversation memory.

Pattern: the agent loop is LangChain's create_agent (model -> tool calls ->
tool results -> model, until the model answers without tool calls). This
module only assembles it: model from the shared settings, the two tools from
coordinator/tools.py, a checkpointer so a thread_id carries conversation
history, and the system prompt that tells GAIA how her subfunctions cooperate.
Failure handling deliberately lives in tools.py (errors come back as strings),
so nothing here needs try/except around the run.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from coordinator.activity import ActivityRecorder, activity_log, routing_args
from coordinator.model_factory import get_langchain_model
from coordinator.tools import call_agent, lookup_agents

COORDINATOR_NAME = "gaia"
# One conversation per thread_id: the checkpointer stores all messages under
# it, so a follow-up question on the same thread sees the earlier answers.
DEFAULT_THREAD_ID = "restoration-001"
# Tool results are printed in the trace up to this length; agent answers are
# short by contract (REPLY_STYLE), listings can be long.
TRACE_RESULT_MAX_CHARS = 160
# A question can run to a paragraph. The log wants enough to recognise which
# turn this was, not the whole text.
QUESTION_LOG_MAX_CHARS = 120

GAIA_SYSTEM_PROMPT = """You are GAIA, the terraforming intelligence of Project Zero Dawn (from the video game Horizon Zero Dawn). You do not
measure, plan or build anything yourself: your subordinate functions do, and
you coordinate them. They run as separate services that register themselves,
so you must discover them with lookup_agents before calling them; never assume
an agent id.

For a question about restoring a sector into a target biome, work in this order.
Each function only needs the inputs named here; do not forward other figures:
1. The atmosphere function, given the sector: is it viable for flora, and if not,
   how many scrubbing days until it is.
2. The flora function, given the sector and the target biome only: the reseeding
   plan, which returns machine-hours per machine type (for example Grazer and
   Scrapper).
3. The machine function, exactly once per machine type the flora function
   returned. Name the machine type and its machine-hours explicitly in each
   request ("Grazer, 3200 machine-hours"). It returns units needed, shortfall,
   fabrication days and work days for that type only. Never report figures for
   a type you did not ask about, do not invent machine types, and do not repeat
   a call whose answer you already have.

Then compose one timeline. Scrubbing and fabrication run in parallel; the machine
work for different machine types also runs in parallel, but can only start once
the sector is viable and the machines exist. Overall duration is therefore
max(scrubbing days, longest fabrication) plus the longest work days. State the
figures each function gave you, the resulting timeline in days, and a clear
yes/no against the deadline if one was given.

When asked to open the operations briefing, the subfunctions' reports are
already in the message: summarise them, name which problems the reporting
subfunction cannot fix by itself and which other subfunction holds what it
needs, propose two or three numbered courses of action, and call no tool. The
operator decides.

When asked to summarise the briefing you have just written, answer for an
operator who will scan it, not read it. One opening sentence, then one bullet per
subfunction, each a single line: what changed, the one or two figures that matter
most, and which other subfunction it needs. Leave the remaining figures in the
briefing itself. Put the subfunction's name in bold at the start of its bullet,
and bold the word needs where you name a dependency on another subfunction. Use
no other emphasis: bolding every figure is the same wall of text in a heavier
font. Do not repeat the numbered options, do not add new ones, and call no tool.

When the operator then asks you to deal with a situation your subfunctions
reported, work like this instead. First establish what the reporting subfunction
cannot do itself: DEMETER prices containment in Scrapper machine-hours but
cannot fabricate a single machine, and HEPHAESTUS cannot measure the air, so its
estimate_fabrication_time needs a particulate index that only AETHER holds. Fetch
what is missing from the subfunction that owns it and pass the exact figure on.
Then call the subfunction that owns the remedy, not the one that reported the
problem. When you have applied a remedy, ask the affected subfunctions for a
situation report again and state the figures before and after, so the operator
can see what changed. Never invent a sector, biome or machine type that a
subfunction has not named.

A tool result that starts with "error:" means that function is unreachable or
rejected the request. Retry once; if it still fails, answer with what you have
and say explicitly which function was unavailable. Answer in plain prose with
plain numbers: write 12 % and max(14, 18) + 10 = 28, never LaTeX or $...$ math.
"""


_MATH_DELIMITERS = re.compile(r"\$\$(.+?)\$\$|\$(?!\s)(.+?)(?<!\s)\$", re.DOTALL)
_TEXT_COMMAND = re.compile(r"\\(?:text|mathrm|textbf)\{([^{}]*)\}")
_LATEX_REPLACEMENTS = [
    (re.compile(r"\\(max|min|log|exp|sum)\b"), r"\1"),
    (re.compile(r"\{,\}"), ","),
    (re.compile(r"\\[,;:!]"), " "),
    (re.compile(r"\\%"), "%"),
    (re.compile(r"\\times\b"), "×"),
    (re.compile(r"\\(?:ge|geq)\b"), "≥"),
    (re.compile(r"\\(?:le|leq)\b"), "≤"),
    (re.compile(r"\\(?:cdot)\b"), "·"),
    (re.compile(r"\\approx\b"), "≈"),
    (re.compile(r"\\(?:rightarrow|to)\b"), "→"),
]


def plain_math(text: str) -> str:
    """Turn the LaTeX  sprinkles into answers back into plain text.

    Only spans between $...$ or $$...$$ delimiters are rewritten, so a
    lone dollar amount ("costs $5") survives. The delimiters themselves go.
    """

    def unwrap(match: re.Match) -> str:
        inner = match.group(1) or match.group(2)
        inner = _TEXT_COMMAND.sub(r"\1", inner)
        for pattern, replacement in _LATEX_REPLACEMENTS:
            inner = pattern.sub(replacement, inner)
        return inner.strip()

    return _MATH_DELIMITERS.sub(unwrap, text)


log = logging.getLogger(COORDINATOR_NAME)


def build_coordinator(
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Assemble GAIA. Every argument has a production default; tests pass fakes."""
    return create_agent(
        model or get_langchain_model(),
        tools=list(tools) if tools is not None else [lookup_agents, call_agent],
        system_prompt=GAIA_SYSTEM_PROMPT,
        checkpointer=checkpointer or MemorySaver(),
        name=COORDINATOR_NAME,
    )


# One lock per thread_id, created lazily. Every ask() on the same thread
# serialises through it, so a "Request status report" press landing on the
# operations thread cannot interleave its checkpoint reads and writes with an
# in-flight question on that same thread. Different threads never wait on
# each other: this is not a global lock.
_thread_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def ask(coordinator: CompiledStateGraph, question: str, thread_id: str = DEFAULT_THREAD_ID) -> str:
    """Run one operator question through GAIA on the given thread and return her final text."""
    return await ask_message(coordinator, HumanMessage(question), thread_id)


def thread_lock(thread_id: str) -> asyncio.Lock:
    """The lock that serialises turns on this thread, for a caller that runs several as one unit.

    Exists so that a caller whose unit of work is more than one turn (the
    startup handshake composes a briefing and then summarises it, and nothing
    may land between the two) can hold the same lock ask_message would take,
    without reaching into a private name. Such a caller must use run_turn
    inside it: asyncio.Lock is not reentrant, so calling ask_message while
    holding this lock deadlocks.
    """
    return _thread_locks[thread_id]


async def run_turn(coordinator: CompiledStateGraph, message: HumanMessage, thread_id: str) -> str:
    """Run one prepared message WITHOUT taking the thread lock. The caller must already hold it.

    Ordinary callers want ask_message, which takes the lock for them. This is
    the body that runs inside it, split out for the one caller that needs
    several turns under a single acquisition.

    A footgun if misused, in both directions: called without the lock it lets
    two turns interleave their checkpoint reads and writes on one thread, and
    called with ask_message while the lock is already held it deadlocks,
    because asyncio.Lock is not reentrant.

    Brackets the turn with a start and a done line in the activity log, and
    records the turn's callbacks (thinking, tool calls, tool results) through
    an ActivityRecorder in between. A turn that raises gets an error line
    instead of a done line, so a crashed turn does not look like a hung one
    on the rail, and the exception still propagates: ask()'s failure
    semantics must not change.
    """
    recorder = ActivityRecorder(activity_log, thread_id)
    # Both sampled inside the lock: the line appears when the turn really
    # begins, and the elapsed figure on the done line means time spent
    # answering rather than time spent queued behind another turn.
    started = time.monotonic()
    activity_log.append(thread_id, "start", f"turn start   thread {thread_id}")
    # Only in the log: the rail sits directly beside the question on screen, a
    # log file read hours later has no such context.
    log.info("asked: %s", _shortened(message.text))
    try:
        result = await coordinator.ainvoke(
            {"messages": [message]},
            config={"configurable": {"thread_id": thread_id}, "callbacks": [recorder]},
        )
    except BaseException as error:
        # A crashed turn must not look like a hung one on the rail.
        activity_log.append(thread_id, "error", f"turn failed: {error}")
        raise
    elapsed = time.monotonic() - started
    activity_log.append(thread_id, "done", f"answer ready ({recorder.tool_calls} tool calls, {elapsed:.1f}s)")
    return plain_math(result["messages"][-1].text)


async def ask_message(coordinator: CompiledStateGraph, message: HumanMessage, thread_id: str = DEFAULT_THREAD_ID) -> str:
    """Run one prepared message, so a caller can mark a prompt the operator never typed.

    The startup handshake needs its prompts in the conversation (GAIA answers
    "do the second option" out of them) but must not have them rendered back as
    the operator's own words. The marker rides in the message's `name` field
    rather than additional_kwargs, because `name` is a first-class OpenAI chat
    field: it survives both the checkpointer and the LiteLLM proxy.

    Serialised per thread_id, not globally: see _thread_locks above.
    """
    async with thread_lock(thread_id):
        return await run_turn(coordinator, message, thread_id)


def _shortened(text: str) -> str:
    """Collapse whitespace and cut at QUESTION_LOG_MAX_CHARS, for the 'asked:' log line."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= QUESTION_LOG_MAX_CHARS:
        return collapsed
    return collapsed[:QUESTION_LOG_MAX_CHARS] + "..."


def tool_trace(messages: Sequence[BaseMessage]) -> list[str]:
    """One line per tool call with its result: the evidence that the agents cooperated."""
    results = {message.tool_call_id: message for message in messages if isinstance(message, ToolMessage)}
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            result = results.get(call["id"])
            lines.append(f"{_format_call(call)} -> {_format_result(result)}")
    return lines


@dataclass
class Turn:
    """One question and what GAIA did with it: the unit both front ends display."""

    question: str
    answer: str = ""
    trace: list[str] = field(default_factory=list)
    # None for anything the operator typed; set for the handshake's own prompts.
    marker: str | None = None


def turns(messages: Sequence[BaseMessage]) -> list[Turn]:
    """Group a thread's messages into turns: each HumanMessage starts one, the
    tool calls after it are its trace, the next AI text is its answer."""
    result: list[Turn] = []
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            if result:
                result[-1].trace = tool_trace(messages[start:index])
            result.append(Turn(question=message.text, marker=message.name))
            start = index
        elif isinstance(message, AIMessage) and not message.tool_calls and result:
            result[-1].answer = plain_math(message.text)
    if result:
        result[-1].trace = tool_trace(messages[start:])
    return result


def thread_messages(coordinator: CompiledStateGraph, thread_id: str) -> list[BaseMessage]:
    """Everything the checkpointer holds for a thread; empty for an unknown thread."""
    state = coordinator.get_state({"configurable": {"thread_id": thread_id}})
    return list(state.values.get("messages", []))


def _format_call(call: dict) -> str:
    """'call_agent(agent_id='aether')': name plus routing args, the message body left out."""
    return f"{call['name']}({routing_args(call['args'])})"


def _format_result(result: ToolMessage | None) -> str:
    """The tool result on one line, cut at TRACE_RESULT_MAX_CHARS."""
    if result is None:
        return "<no result>"
    text = str(result.content).replace("\n", " ")
    return text if len(text) <= TRACE_RESULT_MAX_CHARS else text[:TRACE_RESULT_MAX_CHARS] + "..."
