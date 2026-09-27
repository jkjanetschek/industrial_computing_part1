"""Self-registration: an agent_to_a2a app announces its real card to the registry on startup."""

import httpx
import pytest
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from starlette.testclient import TestClient

from agents import registration
from conftest import FakeRegistry

AGENT_URL = "http://aether-agent:8001"
SKILL = {
    "id": "atmosphere-assessment",
    "name": "Atmosphere assessment",
    "description": "Report oxygen, toxicity and particulates for a sector.",
    "tags": ["aether"],
    "input_modes": ["text/plain"],
    "output_modes": ["text/plain"],
}


def build_agent_app():
    model = OpenAIChatModel("never-called", provider=OpenAIProvider(base_url="http://x", api_key="x"))
    return agent_to_a2a(Agent(model, name="aether"), name="aether", url=AGENT_URL, skills=[SKILL])


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registration, "REGISTRATION_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(registration, "REGISTRATION_ATTEMPTS", 3)


def test_registers_real_card_on_startup() -> None:
    fake = FakeRegistry()
    app = registration.with_self_registration(build_agent_app(), "aether", fake.client_factory)

    with TestClient(app) as client:
        assert client.get("/.well-known/agent-card.json").status_code == 200

    assert len(fake.registered) == 1
    card = fake.registered[0]
    assert card["id"] == "aether"
    assert card["url"] == AGENT_URL
    assert card["capabilities"] == ["atmosphere-assessment", "aether"]


def test_retries_until_registry_is_up() -> None:
    fake = FakeRegistry(failures_before_success=2)
    app = registration.with_self_registration(build_agent_app(), "aether", fake.client_factory)

    with TestClient(app):
        pass

    assert len(fake.registered) == 1


def test_gives_up_after_max_attempts() -> None:
    fake = FakeRegistry(failures_before_success=99)
    app = registration.with_self_registration(build_agent_app(), "aether", fake.client_factory)

    # The worker's task group wraps startup errors in an ExceptionGroup.
    with pytest.raises(ExceptionGroup) as raised:
        with TestClient(app):
            pass
    assert raised.group_contains(RuntimeError, match="could not register")


def test_worker_lifespan_still_runs() -> None:
    app = registration.with_self_registration(build_agent_app(), "aether", FakeRegistry().client_factory)
    with TestClient(app):
        assert app.task_manager.is_running
