"""GAIA's two tools: discover by capability, call by id; failures come back as strings, never exceptions."""

import httpx
import pytest

from coordinator import tools
from coordinator.agent_client import AgentCallError
from shared.schemas import AgentCard, AgentListing

LISTING = AgentListing(
    agents=[
        AgentCard(id="aether", name="aether", description="Atmosphere per sector.", url="http://a:8001", capabilities=["atmosphere-assessment", "atmosphere", "toxicity"]),
        AgentCard(id="demeter", name="demeter", description="Reseeding plans.", url="http://d:8002", capabilities=["reseeding-plan", "reseeding", "flora"]),
        AgentCard(id="hephaestus", name="hephaestus", description="Machine fabrication.", url="http://h:8003", capabilities=["machine-fabrication", "fabrication", "machines"]),
    ]
)


class FakeAgentClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def call(self, card: AgentCard, message: str) -> str:
        self.calls.append((card.id, message))
        if self.error:
            raise self.error
        return f"{card.id} says hi"


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeAgentClient:
    async def fake_listing() -> AgentListing:
        return LISTING

    client = FakeAgentClient()
    monkeypatch.setattr(tools, "get_agent_listing", fake_listing)
    monkeypatch.setattr(tools, "agent_client", client)
    return client


@pytest.mark.anyio
async def test_lookup_matches_capability_case_insensitively(fake_client: FakeAgentClient) -> None:
    result = await tools.lookup_agents.ainvoke({"capability": "Fabrication"})

    assert [agent["id"] for agent in result] == ["hephaestus"]
    assert result[0]["capabilities"] == ["machine-fabrication", "fabrication", "machines"]


@pytest.mark.anyio
async def test_lookup_with_empty_capability_lists_everyone(fake_client: FakeAgentClient) -> None:
    result = await tools.lookup_agents.ainvoke({"capability": ""})

    assert [agent["id"] for agent in result] == ["aether", "demeter", "hephaestus"]


@pytest.mark.anyio
async def test_call_agent_delegates_to_client(fake_client: FakeAgentClient) -> None:
    answer = await tools.call_agent.ainvoke({"agent_id": "demeter", "message": "plan it"})

    assert answer == "demeter says hi"
    assert fake_client.calls == [("demeter", "plan it")]


@pytest.mark.anyio
async def test_call_agent_with_unknown_id_returns_error_string_listing_ids(fake_client: FakeAgentClient) -> None:
    answer = await tools.call_agent.ainvoke({"agent_id": "hades", "message": "reset the world"})

    assert answer.startswith("error:")
    assert "hades" in answer
    assert "aether, demeter, hephaestus" in answer
    assert fake_client.calls == []


@pytest.mark.anyio
async def test_call_agent_turns_transport_errors_into_strings(fake_client: FakeAgentClient) -> None:
    fake_client.error = httpx.ConnectError("connection refused")

    answer = await tools.call_agent.ainvoke({"agent_id": "aether", "message": "hi"})

    assert answer.startswith("error:")
    assert "connection refused" in answer


@pytest.mark.anyio
async def test_call_agent_turns_agent_failures_into_strings(fake_client: FakeAgentClient) -> None:
    fake_client.error = AgentCallError("task failed")

    answer = await tools.call_agent.ainvoke({"agent_id": "aether", "message": "hi"})

    assert answer == "error: agent 'aether' call failed: task failed"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
