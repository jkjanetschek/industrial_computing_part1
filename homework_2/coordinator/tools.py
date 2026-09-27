"""GAIA's two tools. Exactly two, and static: discovery stays dynamic through the
registry instead of through generated per-agent tools, so a new agent needs no
coordinator change at all.

Failures are returned as strings that start with "error:" rather than raised.
LangChain's agent loop feeds a tool's return value back to the model, so a
string lets GAIA read what went wrong, retry, or pick another agent; an
exception would end the whole run.
"""

import httpx
from langchain_core.tools import tool

from coordinator.agent_client import AgentCallError, AgentClient
from coordinator.registry_client import get_agent_listing
from shared.schemas import AgentCard

ERROR_PREFIX = "error:"

# Module-level so tests can swap it for a fake; one client per process is enough.
agent_client = AgentClient()


@tool
async def lookup_agents(capability: str) -> list[dict]:
    """Find registered sub-agents by capability, e.g. "atmosphere", "reseeding", "fabrication".

    Pass an empty string to list every registered agent. Each entry has the id
    to use with call_agent, a description, and the capability tags it advertises.
    """
    listing = await get_agent_listing()
    matches = [card for card in listing.agents if _matches(card, capability)]
    return [_summary(card) for card in matches]


@tool
async def call_agent(agent_id: str, message: str) -> str:
    """Send a plain-text request to one registered sub-agent (by id from lookup_agents) and return its answer."""
    listing = await get_agent_listing()
    # Re-read the listing on every call rather than caching it: an agent that
    # restarted on a new url must be reached at the new url, and a stale cache
    # is exactly the kind of failure GAIA cannot reason her way out of.
    # next() with a default gives the first matching card or None, never raises.
    card = next((card for card in listing.agents if card.id == agent_id), None)
    if card is None:
        known = ", ".join(card.id for card in listing.agents)
        return f"{ERROR_PREFIX} unknown agent '{agent_id}'. Registered agents: {known}"
    try:
        return await agent_client.call(card, message)
    except (httpx.HTTPError, AgentCallError) as error:
        return f"{ERROR_PREFIX} agent '{agent_id}' call failed: {error}"


def _matches(card: AgentCard, capability: str) -> bool:
    """Case-insensitive substring match over everything a card says about itself; empty needle matches all."""
    needle = capability.strip().lower()
    if not needle:
        return True
    haystack = [card.id, card.name, card.description, *card.capabilities]
    return any(needle in text.lower() for text in haystack)


def _summary(card: AgentCard) -> dict:
    """What the model gets to see per agent: enough to choose, without the url it must never call directly."""
    return {"id": card.id, "description": card.description, "capabilities": card.capabilities}
