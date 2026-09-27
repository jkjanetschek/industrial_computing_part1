"""The shared wrapper every sub-agent uses: spec -> A2A app with card, skills and self-registration."""

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from agents.a2a_server import AgentSpec, build_a2a_app, port_of, text_skill
from conftest import FakeRegistry

SPEC = AgentSpec(
    agent_id="probe",
    public_url="http://probe-agent:8123",
    description="A probe.",
    skills=[text_skill("probe-skill", "Probe", "Probes things.", ["probing", "sensors"])],
)


def test_port_of_reads_the_port_from_the_public_url() -> None:
    assert port_of("http://aether-agent:8001") == 8001


def test_port_of_rejects_url_without_port() -> None:
    with pytest.raises(ValueError, match="port"):
        port_of("http://aether-agent")


def test_text_skill_is_plain_text_in_and_out() -> None:
    skill = text_skill("s", "S", "d", ["t"])
    assert skill["input_modes"] == ["text/plain"]
    assert skill["output_modes"] == ["text/plain"]
    assert skill["tags"] == ["t"]


def test_built_app_serves_spec_as_card_and_registers_it() -> None:
    fake = FakeRegistry()
    app = build_a2a_app(Agent(TestModel(), name="probe"), SPEC, client_factory=fake.client_factory)

    with TestClient(app) as client:
        card = client.get("/.well-known/agent-card.json").json()

    assert card["name"] == "probe"
    assert card["description"] == "A probe."
    assert card["supportedInterfaces"][0]["url"] == "http://probe-agent:8123"
    assert [skill["id"] for skill in card["skills"]] == ["probe-skill"]
    assert fake.registered[0]["id"] == "probe"
    assert fake.registered[0]["capabilities"] == ["probe-skill", "probing", "sensors"]
