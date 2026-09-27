"""
Runs the registry in-process with FastAPI's TestClient: no network, no LiteLLM.
"""

import pytest
from fastapi.testclient import TestClient

from registry_service import main as registry
from shared.schemas import AgentCard

# Exactly what fasta2a 2.x serves at /.well-known/agent-card.json (camelCase,
# url nested under supportedInterfaces, no id).
SERVED_A2A_CARD = {
    "name": "aether",
    "description": "AETHER subfunction: atmospheric composition and detoxification status per sector.",
    "version": "1.0.0",
    "supportedInterfaces": [
        {"protocolBinding": "JSONRPC", "url": "http://aether-agent:8001", "protocolVersion": "1.0"}
    ],
    "skills": [
        {
            "id": "atmosphere-assessment",
            "name": "Atmosphere assessment",
            "description": "Report oxygen, toxicity and particulates for a sector.",
            "tags": ["atmosphere", "terraforming"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        }
    ],
    "defaultInputModes": ["application/json"],
    "defaultOutputModes": ["application/json"],
    "capabilities": {"streaming": True, "pushNotifications": False},
}


@pytest.fixture
def client() -> TestClient:
    registry._agents.clear()
    return TestClient(registry.app)


def test_from_a2a_card_flattens_url_and_capabilities() -> None:
    card = AgentCard.from_a2a_card("aether", SERVED_A2A_CARD)

    assert card.id == "aether"
    assert card.url == "http://aether-agent:8001"
    assert card.capabilities == ["atmosphere-assessment", "atmosphere", "terraforming"]
    assert card.skills[0].name == "Atmosphere assessment"


def test_health_reports_registered_count(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "registered_agents": 0}


def test_register_then_list_returns_real_url(client: TestClient) -> None:
    card = AgentCard.from_a2a_card("aether", SERVED_A2A_CARD)

    response = client.post("/agents/register", json=card.model_dump())
    assert response.status_code == 200

    listing = client.get("/agents").json()
    assert [agent["id"] for agent in listing["agents"]] == ["aether"]
    assert listing["agents"][0]["url"] == "http://aether-agent:8001"


def test_get_by_id_and_404(client: TestClient) -> None:
    card = AgentCard.from_a2a_card("aether", SERVED_A2A_CARD)
    client.post("/agents/register", json=card.model_dump())

    assert client.get("/agents/aether").json()["name"] == "aether"
    assert client.get("/agents/nope").status_code == 404


def test_reregister_replaces_entry(client: TestClient) -> None:
    card = AgentCard.from_a2a_card("aether", SERVED_A2A_CARD)
    client.post("/agents/register", json=card.model_dump())

    moved = card.model_copy(update={"url": "http://aether-agent:9001"})
    client.post("/agents/register", json=moved.model_dump())

    listing = client.get("/agents").json()
    assert len(listing["agents"]) == 1
    assert listing["agents"][0]["url"] == "http://aether-agent:9001"


def test_register_rejects_card_without_url(client: TestClient) -> None:
    response = client.post("/agents/register", json={"id": "x", "name": "x"})
    assert response.status_code == 422
