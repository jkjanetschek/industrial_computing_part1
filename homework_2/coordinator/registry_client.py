"""Agent discovery for the coordinator, independent of where the agents come from.

Pattern: adapter at the boundary. ``get_agent_listing()`` is the ONE discovery
call the coordinator makes. Internally it talks either to the hand-rolled
registry (REGISTRY_MODE=own) or to the LiteLLM proxy (REGISTRY_MODE=litellm)
and maps both into the same ``AgentListing``. Nothing else in coordinator/
knows which mode is active.

LiteLLM specifics (verified in litellm 1.100.1 source, proxy/agent_endpoints):
  - GET /v1/agents returns a bare JSON list of AgentResponse objects, each with
    ``agent_id``, ``agent_name`` and ``agent_card_params`` (the stored card).
  - ``agent_card_params.url`` is the agent's REAL upstream url, the same value
    LiteLLM's own relay uses as api_base, so direct calls are possible from a
    LiteLLM listing. Beware: the same stored card also carries
    ``supportedInterfaces[0].url`` = the proxy relay address
    ``{proxy}/a2a/{agent_id}`` (written by LiteLLM's merge_agent_card). The
    adapter below must therefore take ``url`` explicitly, never the interface.
  - ``agent_id`` is what the relay path /v1/a2a/{agent_id}/... expects, so it
    becomes our AgentCard.id.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from shared.schemas import AgentCard, AgentListing
from shared.settings import RegistryMode, settings

OWN_REGISTRY_AGENTS_PATH = "/agents"
LITELLM_AGENTS_PATH = "/v1/agents"
LITELLM_AGENT_ID_KEY = "agent_id"
LITELLM_AGENT_NAME_KEY = "agent_name"
LITELLM_CARD_KEY = "agent_card_params"
LITELLM_UPSTREAM_URL_KEY = "url"
DISCOVERY_TIMEOUT_SECONDS = 5.0

# A factory (not a client) because the client must be created inside the
# running event loop and closed after each listing. Tests inject a factory
# returning a client with httpx.MockTransport, so no registry is needed.
ClientFactory = Callable[[str], httpx.AsyncClient]


def default_client(base_url: str) -> httpx.AsyncClient:
    """Production factory: a short-timeout client, because discovery must fail fast when the registry is down."""
    return httpx.AsyncClient(base_url=base_url, timeout=DISCOVERY_TIMEOUT_SECONDS)


async def get_agent_listing(client_factory: ClientFactory = default_client) -> AgentListing:
    """Every known agent, as the shared AgentListing, whatever REGISTRY_MODE is."""
    fetch = _FETCHERS[settings.REGISTRY_MODE]
    return await fetch(client_factory)


async def _from_own_registry(client_factory: ClientFactory) -> AgentListing:
    """REGISTRY_MODE=own: the hand-rolled registry already speaks AgentListing, so no mapping."""
    async with client_factory(settings.REGISTRY_URL) as client:
        response = await client.get(OWN_REGISTRY_AGENTS_PATH)
        response.raise_for_status()
        return AgentListing.model_validate(response.json())


async def _from_litellm(client_factory: ClientFactory) -> AgentListing:
    """REGISTRY_MODE=litellm: the proxy's agent list, each entry adapted into an AgentCard."""
    headers = {"Authorization": f"Bearer {settings.LITELLM_PROXY_API_KEY}"}
    async with client_factory(settings.LITELLM_PROXY_URL) as client:
        response = await client.get(LITELLM_AGENTS_PATH, headers=headers)
        response.raise_for_status()
        return AgentListing(agents=[_litellm_entry_to_card(entry) for entry in response.json()])


def _litellm_entry_to_card(entry: dict[str, Any]) -> AgentCard:
    """Adapter: one LiteLLM AgentResponse -> our AgentCard, reading the REAL upstream url (see module docstring)."""
    card = dict(entry.get(LITELLM_CARD_KEY) or {})
    # LiteLLM keeps the human name outside the card; fall back to it so a card
    # saved without a name still produces a usable listing entry.
    card.setdefault("name", entry[LITELLM_AGENT_NAME_KEY])
    upstream_url = card.get(LITELLM_UPSTREAM_URL_KEY)
    if not upstream_url:
        raise ValueError(f"LiteLLM agent '{entry.get(LITELLM_AGENT_ID_KEY)}' has no upstream url")
    return AgentCard.from_a2a_card(entry[LITELLM_AGENT_ID_KEY], card, url=upstream_url)


# The strategy table get_agent_listing consults; adding a registry mode is one
# entry here plus the Literal in shared/settings.py.
_FETCHERS: dict[RegistryMode, Callable[[ClientFactory], Awaitable[AgentListing]]] = {
    "own": _from_own_registry,
    "litellm": _from_litellm,
}
