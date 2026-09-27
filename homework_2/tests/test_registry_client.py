"""The coordinator's discovery call returns the same AgentListing in both REGISTRY_MODEs."""

import json

import httpx
import pytest

from coordinator import registry_client
from shared.settings import settings

REAL_AGENT_URL = "http://aether-agent:8001"

# What our own registry returns from GET /agents.
OWN_REGISTRY_RESPONSE = {
    "agents": [
        {
            "id": "aether",
            "name": "aether",
            "description": "Atmosphere status per sector.",
            "url": REAL_AGENT_URL,
            "capabilities": ["atmosphere-assessment", "aether"],
            "skills": [{"id": "atmosphere-assessment", "name": "Atmosphere", "description": "", "tags": ["aether"]}],
        }
    ]
}

# What LiteLLM 1.100.1 returns from GET /v1/agents: a bare list of AgentResponse,
# with the stored card (top-level url, camelCase) under agent_card_params.
LITELLM_RESPONSE = [
    {
        "agent_id": "aether",
        "agent_name": "aether",
        "litellm_params": {},
        "agent_card_params": {
            "name": "aether",
            "description": "Atmosphere status per sector.",
            "url": REAL_AGENT_URL,
            "version": "1.0.0",
            "skills": [{"id": "atmosphere-assessment", "name": "Atmosphere", "description": "", "tags": ["aether"]}],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            # LiteLLM's merge writes ITS OWN relay address here; url above stays upstream.
            "supportedInterfaces": [
                {"url": "http://litellm:4000/a2a/aether", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ],
        },
    }
]


class FakeServer:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.payload)

    def client_factory(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url=base_url)


def set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # Settings is frozen; swap the module-level instance for the test.
    monkeypatch.setattr(registry_client, "settings", settings.model_copy(update={"REGISTRY_MODE": mode, "LITELLM_PROXY_API_KEY": "sk-test"}))


@pytest.mark.anyio
async def test_own_mode_hits_registry_service(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "own")
    server = FakeServer(OWN_REGISTRY_RESPONSE)

    listing = await registry_client.get_agent_listing(server.client_factory)

    assert server.requests[0].url.path == "/agents"
    assert listing.agents[0].url == REAL_AGENT_URL


@pytest.mark.anyio
async def test_litellm_mode_hits_proxy_with_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "litellm")
    server = FakeServer(LITELLM_RESPONSE)

    listing = await registry_client.get_agent_listing(server.client_factory)

    request = server.requests[0]
    assert request.url.path == "/v1/agents"
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert listing.agents[0].id == "aether"
    # Must be the upstream url, not LiteLLM's relay address from supportedInterfaces.
    assert listing.agents[0].url == REAL_AGENT_URL


@pytest.mark.anyio
async def test_both_modes_yield_identical_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(monkeypatch, "own")
    own = await registry_client.get_agent_listing(FakeServer(OWN_REGISTRY_RESPONSE).client_factory)
    set_mode(monkeypatch, "litellm")
    via_litellm = await registry_client.get_agent_listing(FakeServer(LITELLM_RESPONSE).client_factory)

    assert own == via_litellm


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
