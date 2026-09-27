"""The startup handshake: ask every registered subfunction what has happened,
then have GAIA compose one operator briefing out of their answers, then have
her summarise that briefing for the page.

Pattern: deterministic fan-out, model-driven synthesis. ``collect_reports`` runs
no GAIA turn at all. It is a plain loop over the registry listing, because
letting the model decide whether to contact all three agents is exactly the
failure this module exists to remove: a for loop cannot skip an agent.
``run_handshake`` then spends ONE GAIA turn turning the collected text into
the briefing the operator reads, and a second, equally deterministic turn
condensing that briefing into the few sentences the page can actually show as
its opening message. Deterministic means: plain function calls, not tools GAIA
may choose to invoke. A tool call can be skipped, and a skipped summary would
leave the page with nothing to show.

The briefing runs on a fixed thread, and coordinator/api.py makes that thread
the page's landing thread. That is load-bearing: GAIA's memory is per thread, so
a briefing composed on one thread and merely rendered on another would leave her
unable to answer "do the second option".
"""

import asyncio
import logging

import httpx
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from coordinator.activity import BYTES_PER_KB, activity_log
from coordinator.agent_client import AgentCallError, AgentClient
from coordinator.gaia import run_turn, thread_lock
from coordinator.registry_client import get_agent_listing
from shared.schemas import AgentCard

log = logging.getLogger("briefing")

# One conversation for the whole operation: the briefing and everything the
# operator decides in response to it.
BRIEFING_THREAD_ID = "operations"
# What an agent's entry says when it could not be reached.
UNREACHABLE = "did not respond"

# Markers for the two prompts this module writes that the operator never typed.
# They are marked DIFFERENTLY, not just flagged as synthetic, because the page
# has to know which answer becomes the visible bubble and which goes inside the
# disclosure, and GET /briefing has to select the summary specifically.
BRIEFING_FULL = "briefing_full"
BRIEFING_SUMMARY = "briefing_summary"

# Asks for structure, not just brevity. An earlier version asked for "three or
# four sentences naming the numbers that matter" and got exactly that: one dense
# block of thirty figures that no operator could scan.
SUMMARY_REQUEST = (
    "Summarise the briefing you just wrote for the operator. Open with one sentence "
    "saying how many subfunctions reported and whether anything needs the operator's "
    "decision. Then one bullet per subfunction, each a single line: what changed, the "
    "one or two figures that matter most, and which other subfunction it needs. Leave "
    "the rest of the figures in the briefing itself. Put the subfunction's name in bold "
    "at the start of its bullet, and bold the word needs where you name a dependency on "
    "another subfunction. Use no other emphasis. Do not repeat the numbered options "
    "and do not call any tool."
)

# Each agent's own prompt decides how often something goes wrong (DEMETER always
# reports at least one incident, the others have routine days), so this asks for
# whatever happened rather than dictating a count.
STATUS_REQUEST = (
    "GAIA requests a situation report. Record whatever has happened in your own domain "
    "since she last asked with your incident tools, following your own rules on how "
    "often something goes wrong, then call your situation_report tool. Report what "
    "changed, the exact figures your tools returned, and anything you need another "
    "subfunction for."
)

COMPOSE_REQUEST = """Open the operations briefing. Your subfunctions have just reported:

{reports}

Summarise this for the human operator: what each subfunction reported with its exact figures, then say plainly which problems the reporting subfunction cannot fix by itself and which other subfunction holds what it needs. Finish with two or three numbered courses of action the operator can choose between. Do not act on any of them and do not call any tool now: the operator decides."""


async def collect_reports(client: AgentClient | None = None) -> dict[str, str]:
    """One A2A call per registered agent, all in flight at once. An unreachable agent is recorded, not raised.

    The calls run concurrently because each one may poll the agent for up to
    two minutes, and three in a row would keep the operator waiting three
    times as long for the console. Concurrency does not weaken the guarantee:
    every card in the listing gets exactly one call, and the reports come back
    in listing order.
    """
    caller = client or AgentClient()
    listing = await get_agent_listing()
    answers = await asyncio.gather(*(_ask_for_report(caller, card) for card in listing.agents))
    return {card.id: answer for card, answer in zip(listing.agents, answers)}


async def _ask_for_report(caller: AgentClient, card: AgentCard) -> str:
    """One agent's situation report, with a rail line before and after; failure becomes UNREACHABLE."""
    activity_log.append(BRIEFING_THREAD_ID, "call", f"-> {card.id} situation report")
    try:
        answer = await caller.call(card, STATUS_REQUEST)
    except (httpx.HTTPError, AgentCallError) as error:
        log.warning("status request to %s failed: %s", card.id, error)
        activity_log.append(BRIEFING_THREAD_ID, "error", f"<- {card.id} {UNREACHABLE}")
        return UNREACHABLE
    activity_log.append(BRIEFING_THREAD_ID, "ok", f"<- {card.id} replied, {len(answer) / BYTES_PER_KB:.1f} kB")
    return answer


def _compose_message(reports: dict[str, str]) -> HumanMessage:
    """The compose prompt, marked so the page knows the operator never typed it."""
    joined = "\n".join(f"{agent_id.upper()}: {text}" for agent_id, text in reports.items())
    return HumanMessage(COMPOSE_REQUEST.format(reports=joined), name=BRIEFING_FULL)


def _summary_message() -> HumanMessage:
    """The summary prompt, marked differently so GET /briefing can select it."""
    return HumanMessage(SUMMARY_REQUEST, name=BRIEFING_SUMMARY)


async def run_handshake(coordinator: CompiledStateGraph) -> str:
    """Fan out, compose, summarise. Returns the summary; the full briefing stays in the thread.

    The two GAIA turns run under ONE acquisition of the operations thread lock,
    so the handshake is atomic. Taking the lock per turn, which is what
    ask_message does, would let a second handshake (a double click on "Request
    status report") compose its briefing between these two turns, and would let
    an operator question slip in and make "the briefing you just wrote" name the
    wrong message. Holding the lock is why the turns go through run_turn:
    asyncio.Lock is not reentrant.

    The fan-out stays outside the lock. It runs no GAIA turn and can take
    minutes, and an operator question should not queue behind it.

    The summary prompt is short because the full briefing is already in GAIA's
    context on this thread, which is the other reason both turns must land on
    the same thread.
    """
    activity_log.append(BRIEFING_THREAD_ID, "start", "startup handshake begins")
    reports = await collect_reports()
    async with thread_lock(BRIEFING_THREAD_ID):
        await run_turn(coordinator, _compose_message(reports), BRIEFING_THREAD_ID)
        summary = await run_turn(coordinator, _summary_message(), BRIEFING_THREAD_ID)
    # Closes the outer bracket opened above. Success path only: if a turn
    # raises, run_turn already writes its own error line and re-raises, so
    # forcing this close with try/finally would make a crashed handshake look
    # like it finished cleanly.
    activity_log.append(BRIEFING_THREAD_ID, "done", "startup handshake complete")
    return summary
