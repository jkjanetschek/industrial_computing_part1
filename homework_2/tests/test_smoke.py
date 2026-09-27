"""End-to-end against a live stack. Skipped unless a coordinator is actually running.

This is the only test in the suite that talks to real agents and a real model, so
it is deliberately shallow: it asserts that the cooperation happened, not what the
model said about it.

Only one live HTTP round trip runs in this whole module. Two independent round
trips against the same coordinator would both land on the shared "operations"
thread and both mutate the live agents' in-memory state, so the second one could
observe a world the first one already changed, for reasons that have nothing to
do with a real regression. One run, checked from two angles, avoids that.
"""

import httpx
import pytest

from coordinator import scenario
from shared.settings import settings

HEALTH_TIMEOUT_SECONDS = 2.0


def stack_is_up() -> bool:
    try:
        return httpx.get(f"{settings.COORDINATOR_URL}/health", timeout=HEALTH_TIMEOUT_SECONDS).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not stack_is_up(), reason="no coordinator on COORDINATOR_URL")


def test_scenario_reaches_all_three_subfunctions(capsys: pytest.CaptureFixture) -> None:
    """One scenario run: exits clean, and its printed trace shows GAIA genuinely
    calling all three subfunctions, not merely mentioning their ids.

    A bare substring check on the whole trace would also pass on a single
    lookup_agents() call, whose own result lists every registered agent's id.
    Matching call_agent's rendered form (coordinator/gaia.py's _format_call:
    "call_agent(agent_id='...')") requires that subfunction to have actually
    been invoked.
    """
    exit_code = scenario.run()
    printed = capsys.readouterr().out

    assert exit_code == 0
    for agent_id in ("aether", "demeter", "hephaestus"):
        assert f"call_agent(agent_id='{agent_id}')" in printed, f"{agent_id} was never called:\n{printed}"
