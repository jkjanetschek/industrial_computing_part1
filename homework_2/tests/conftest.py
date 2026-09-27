"""Shared test doubles, plus the one fixture every test needs whether it imports it or not.

reset_agent_state is autouse because the agent modules hold their domain tables
(SECTOR_ATMOSPHERE, SECTOR_FLORA, CAULDRONS) at module level. Once incidents
and remedies write to those tables (agents/mutations.py), a test that mutates
one leaks that change into every test that runs after it, in the same process,
whether or not the later test ever imports the fixture by name. Making the
fixture global and autouse is what keeps the tests independent instead of
relying on every test author to remember to opt in.

Importing those three modules here means the whole session needs a model
credential before it can collect, because each agent builds its PydanticAI model
at import time. Run pytest with LLM_API_KEY set to anything, or with a .env
present; the value is never used, since no test reaches the network.
"""

import copy
import json

import httpx
import pytest

from agents import aether_agent, demeter_agent, hephaestus_agent


class FakeRegistry:
    """Stands in for registry_service over httpx.MockTransport.

    Records every registration; optionally fails the first N requests with a
    connection error to exercise the retry path.
    """

    def __init__(self, failures_before_success: int = 0) -> None:
        self.failures_left = failures_before_success
        self.registered: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise httpx.ConnectError("registry not up yet", request=request)
        self.registered.append(json.loads(request.content))
        return httpx.Response(200, json=self.registered[-1])

    def client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url="http://registry")


# Every table a tool may write to. Add a new agent's table here when you add the agent.
_MUTABLE_TABLES = [
    (aether_agent, "SECTOR_ATMOSPHERE"),
    (demeter_agent, "SECTOR_FLORA"),
    (hephaestus_agent, "CAULDRONS"),
]


@pytest.fixture(autouse=True)
def reset_agent_state():
    """Agent state mutates now, so every test starts from the module's own table.

    The tables are restored in place (clear then update) rather than rebound,
    because the agent modules hold the dict object itself in their tool
    closures: rebinding the module attribute would leave the tools writing to
    the old object.
    """
    saved = [(module, name, copy.deepcopy(getattr(module, name))) for module, name in _MUTABLE_TABLES]
    yield
    for module, name, original in saved:
        table = getattr(module, name)
        # clear() before update() so a key a test ADDED is removed, not merged over.
        table.clear()
        table.update(original)


@pytest.fixture(autouse=True)
def keep_tests_out_of_the_real_logs(tmp_path_factory, monkeypatch):
    """Tests must not append to logs/, which is for reviewing real runs.

    Several tests start an app through TestClient, whose lifespan calls
    shared.logging_setup.configure(). Left alone that wrote fake registrations
    and lookups into the same files a reviewer reads to see what actually
    happened, which is worse than no log at all.
    """
    from shared import logging_setup

    monkeypatch.setattr(logging_setup, "LOG_DIR", tmp_path_factory.mktemp("logs"))
