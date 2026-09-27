"""The coordinator service: one GAIA per process, JSON for scripts, HTML for a browser, memory per thread."""

import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from coordinator import api
from test_coordinator import ScriptedModel, call_agent, lookup_agents, tool_call

# How long a live_server-backed test waits for uvicorn to bind its socket
# before giving up. Generous because it only matters when startup is slow.
SERVER_START_TIMEOUT_SECONDS = 5.0


def scripted(*answers: str) -> ScriptedModel:
    """Each question: one call_agent round trip, then the given answer."""
    messages = []
    for index, answer in enumerate(answers):
        messages.append(tool_call(f"c{index}", "call_agent", agent_id="aether", message="q"))
        messages.append(AIMessage(content=answer))
    return ScriptedModel(messages=iter(messages))


def _use_scripted_coordinator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap in a scripted model and skip the startup briefing, for either kind of client below."""
    from coordinator import gaia

    monkeypatch.setattr(
        api,
        "build_coordinator",
        lambda: gaia.build_coordinator(model=scripted("28 days", "17 days"), tools=[lookup_agents, call_agent]),
    )
    # Without this, the lifespan runs the real startup handshake against a
    # registry that does not exist in tests: five seconds of discovery timeout
    # burned on every test, and it consumes an answer from the scripted model.
    # Settings is frozen, so the instance itself cannot be mutated; replace the
    # module-level name api.py reads with a copy that has the one field flipped.
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"BRIEFING_ON_STARTUP": False}))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _use_scripted_coordinator(monkeypatch)
    with TestClient(api.app) as test_client:
        yield test_client


def _wait_until_started(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start within the timeout")
        time.sleep(0.01)


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch) -> httpx.Client:
    """A real server on a real socket, for the two /activity/stream tests below.

    Starlette's in-process TestClient runs the whole ASGI call to completion
    before it hands back a response at all, because it drains the body into a
    buffer first. That works for every other route, which all finish, but
    /activity/stream is designed never to finish on its own; it ends only when
    the browser goes away. Against the in-process client there is no browser
    to go away: the response object the test would read never arrives, so the
    test hangs forever regardless of whether the route is correct.

    A real socket fixes this because it is what the route's docstring actually
    describes: closing the connection is a real TCP close, uvicorn's protocol
    implementation notices it, and that is what lets Starlette cancel the
    generator. This is the same mechanism a browser tab closing relies on.
    """
    _use_scripted_coordinator(monkeypatch)

    config = uvicorn.Config(api.app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    port = server.servers[0].sockets[0].getsockname()[1]

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=SERVER_START_TIMEOUT_SECONDS) as http_client:
        yield http_client

    server.should_exit = True
    thread.join(timeout=SERVER_START_TIMEOUT_SECONDS)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ask_returns_answer_thread_and_trace(client: TestClient) -> None:
    body = client.post("/ask", json={"question": "Sacred Lands as forest?"}).json()

    assert body["answer"] == "28 days"
    assert body["thread_id"]
    assert body["trace"] == ["call_agent(agent_id='aether') -> aether: figures"]


def test_same_thread_id_continues_the_conversation(client: TestClient) -> None:
    first = client.post("/ask", json={"question": "forest?"}).json()
    second = client.post("/ask", json={"question": "grassland instead?", "thread_id": first["thread_id"]}).json()

    assert second["answer"] == "17 days"
    page = client.get("/", params={"thread_id": first["thread_id"]}).text
    assert "forest?" in page and "28 days" in page
    assert "grassland instead?" in page and "17 days" in page


def test_ask_rejects_empty_question(client: TestClient) -> None:
    assert client.post("/ask", json={"question": "   "}).status_code == 422


def test_web_form_round_trip_keeps_thread(client: TestClient) -> None:
    landing = client.get("/")
    assert landing.status_code == 200
    assert 'name="thread_id"' in landing.text

    posted = client.post("/", data={"question": "forest?", "thread_id": "web-1"}, follow_redirects=True)

    assert posted.status_code == 200
    assert "28 days" in posted.text
    assert "call_agent(agent_id=&#x27;aether&#x27;)" in posted.text or "call_agent(agent_id='aether')" in posted.text
    assert 'value="web-1"' in posted.text


def test_root_lands_on_the_briefing_thread(client: TestClient) -> None:
    from coordinator.briefing import BRIEFING_THREAD_ID

    assert f"thread {BRIEFING_THREAD_ID}" in client.get("/").text


def test_there_is_no_new_conversation_escape_hatch(client: TestClient) -> None:
    """/new landed the operator on a thread GAIA had no memory of, where the
    briefing did not exist and a follow-up referring to it made no sense. An
    explicit thread_id still works for scripts and the terminal client."""
    assert client.get("/new", follow_redirects=False).status_code == 404
    assert 'href="/new"' not in client.get("/").text


def test_briefing_endpoint_returns_the_latest_briefing(client: TestClient) -> None:
    from coordinator.briefing import BRIEFING_THREAD_ID

    body = client.get("/briefing").json()

    assert body["thread_id"] == BRIEFING_THREAD_ID
    assert "briefing" in body


def test_the_stream_replays_what_happened_before_the_browser_connected(live_server: httpx.Client) -> None:
    from coordinator.activity import activity_log

    activity_log.append("replay-thread", "start", "startup handshake begins")

    with live_server.stream("GET", "/activity/stream", params={"thread_id": "replay-thread"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "startup handshake begins" in line
                break


def test_the_stream_skips_what_the_browser_has_already_seen(live_server: httpx.Client) -> None:
    from coordinator.activity import activity_log

    first = activity_log.append("cursor-thread", "call", "already seen")
    activity_log.append("cursor-thread", "call", "the new one")

    with live_server.stream("GET", "/activity/stream", params={"thread_id": "cursor-thread", "after": first.n}) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "the new one" in line
                break


def test_the_stream_ignores_a_cursor_from_a_previous_process(live_server: httpx.Client) -> None:
    """A restarted coordinator's counter starts over; a stale browser id must not suppress replay.

    999999 stands in for a Last-Event-ID an EventSource kept from before a
    coordinator restart. Before the fix this line hangs on the keep-alive
    instead of replaying, so the request carries its own short timeout: a
    hang must fail the test, not the whole suite.
    """
    from coordinator.activity import activity_log

    activity_log.append("restart-thread", "start", "alpha, before the restart")
    activity_log.append("restart-thread", "call", "beta, also before the restart")

    with live_server.stream(
        "GET",
        "/activity/stream",
        params={"thread_id": "restart-thread"},
        headers={"Last-Event-ID": "999999"},
        timeout=2,
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "alpha, before the restart" in line
                break


def test_the_stream_survives_a_garbage_last_event_id(live_server: httpx.Client) -> None:
    from coordinator.activity import activity_log

    activity_log.append("garbage-header-thread", "start", "still here")

    with live_server.stream(
        "GET",
        "/activity/stream",
        params={"thread_id": "garbage-header-thread"},
        headers={"Last-Event-ID": "not-a-number"},
        timeout=2,
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "still here" in line
                break


def test_the_briefing_endpoint_returns_the_summary_and_the_full_text(client: TestClient) -> None:
    from coordinator.briefing import BRIEFING_THREAD_ID

    body = client.get("/briefing").json()

    assert set(body) == {"thread_id", "briefing", "full_briefing"}
    assert body["thread_id"] == BRIEFING_THREAD_ID


def test_the_briefing_endpoint_ignores_later_operator_turns(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordinator import api as api_module
    from coordinator.briefing import BRIEFING_FULL, BRIEFING_SUMMARY
    from coordinator.gaia import Turn

    monkeypatch.setattr(
        api_module,
        "turns",
        lambda _messages: [
            Turn(question="q", answer="the full briefing", marker=BRIEFING_FULL),
            Turn(question="q", answer="the summary", marker=BRIEFING_SUMMARY),
            Turn(question="do the second option", answer="done", marker=None),
        ],
    )

    body = client.get("/briefing").json()

    assert body["briefing"] == "the summary"
    assert body["full_briefing"] == "the full briefing"


def test_a_failed_startup_handshake_explains_itself_in_the_rail(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from coordinator import activity, api as api_module

    log = activity.ActivityLog()
    monkeypatch.setattr(api_module, "activity_log", log)

    async def failing_handshake(_coordinator):
        raise httpx.ConnectError("registry not up")

    monkeypatch.setattr(api_module, "run_handshake", failing_handshake)

    class App:
        class state:
            coordinator = None

    import anyio

    anyio.run(api_module._open_briefing, App)

    from coordinator.briefing import BRIEFING_THREAD_ID

    assert [event.kind for event in log.since(BRIEFING_THREAD_ID, 0)] == ["error"]


def test_a_startup_handshake_that_fails_on_a_model_error_does_not_kill_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only agent failures were caught, so a model or proxy error killed the lifespan.

    RuntimeError() carries no message on purpose: rendered with str() the rail
    line would be "startup briefing failed: " and say nothing at all.
    """
    from coordinator import activity, api as api_module
    from coordinator.briefing import BRIEFING_THREAD_ID

    log = activity.ActivityLog()
    monkeypatch.setattr(api_module, "activity_log", log)

    async def failing_handshake(_coordinator):
        raise RuntimeError()

    monkeypatch.setattr(api_module, "run_handshake", failing_handshake)

    class App:
        class state:
            coordinator = None

    import anyio

    anyio.run(api_module._open_briefing, App)

    [event] = log.since(BRIEFING_THREAD_ID, 0)
    assert event.kind == "error"
    assert "RuntimeError" in event.text


def test_a_failed_refresh_redirects_to_the_console_instead_of_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button is a full navigation, so a 500 would cost the operator the whole console."""
    from coordinator import activity, api as api_module
    from coordinator.briefing import BRIEFING_THREAD_ID

    log = activity.ActivityLog()
    monkeypatch.setattr(api_module, "activity_log", log)

    async def failing_handshake(_coordinator):
        raise RuntimeError("the proxy said 503")

    monkeypatch.setattr(api_module, "run_handshake", failing_handshake)

    response = client.post("/briefing", follow_redirects=False)

    assert response.status_code == api_module.SEE_OTHER
    assert response.headers["location"] == f"/?thread_id={BRIEFING_THREAD_ID}"
    assert [event.kind for event in log.since(BRIEFING_THREAD_ID, 0)] == ["error"]


def _briefing_page() -> str:
    from coordinator.briefing import BRIEFING_FULL, BRIEFING_SUMMARY
    from coordinator.gaia import Turn
    from coordinator.templates import render_page

    return render_page(
        "operations",
        [
            Turn(question="SYNTHETIC COMPOSE PROMPT", answer="the full briefing text", marker=BRIEFING_FULL),
            Turn(question="SYNTHETIC SUMMARY PROMPT", answer="the short summary", marker=BRIEFING_SUMMARY),
        ],
    )


def test_the_page_never_shows_a_prompt_the_operator_did_not_type() -> None:
    page = _briefing_page()

    assert "SYNTHETIC COMPOSE PROMPT" not in page
    assert "SYNTHETIC SUMMARY PROMPT" not in page


def test_the_summary_is_the_visible_bubble_and_the_full_text_is_folded_away() -> None:
    page = _briefing_page()

    assert "the short summary" in page
    assert "the full briefing text" in page
    assert "<summary>full briefing</summary>" in page


def test_an_operator_turn_still_renders_its_question() -> None:
    from coordinator.gaia import Turn
    from coordinator.templates import render_page

    page = render_page("operations", [Turn(question="do the second option", answer="done")])

    assert "do the second option" in page


def test_a_briefing_whose_summary_never_arrived_is_still_shown() -> None:
    from coordinator.briefing import BRIEFING_FULL
    from coordinator.gaia import Turn
    from coordinator.templates import render_page

    page = render_page(
        "operations",
        [Turn(question="SYNTHETIC COMPOSE PROMPT", answer="the full briefing text", marker=BRIEFING_FULL)],
    )

    assert "the full briefing text" in page
    assert "SYNTHETIC COMPOSE PROMPT" not in page
    assert "<summary>full briefing</summary>" not in page


def test_the_normal_pair_still_folds_after_the_flush_exists() -> None:
    page = _briefing_page()

    assert "the short summary" in page
    assert "the full briefing text" in page
    assert "<summary>full briefing</summary>" in page
    assert page.count('class="turn gaia"') == 1


def test_the_page_carries_a_rail_wired_to_the_activity_stream() -> None:
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert 'id="rail-lines"' in page
    assert "/activity/stream?thread_id=" in page
    assert "EventSource" in page


def test_the_rail_scrolls_only_when_the_operator_is_already_at_the_bottom() -> None:
    """An operator scrolled up to read an earlier line must not be dragged away by the next event."""
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert "nearBottom" in page
    assert "if (nearBottom) { railLines.scrollTop = railLines.scrollHeight; }" in page
    # Exactly one: the unconditional scroll is the whole bug.
    assert page.count("railLines.scrollTop = railLines.scrollHeight") == 1


def test_submitting_a_status_report_locks_the_console() -> None:
    """The handshake blocks for about two minutes. A second click starts a second
    one, and a question fired meanwhile queues invisibly behind it, so submitting
    now disables BOTH controls rather than only the button that was pressed."""
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert 'id="briefing-form"' in page
    assert "briefingForm.addEventListener('submit', () => {" in page
    assert "setControlsDisabled(true);" in page
    assert "Asking subfunctions..." in page


def test_the_two_panes_are_independently_scrollable() -> None:
    """Both panes scroll on their own, and the page itself does not.

    A tripwire for a regression that already happened once: the panes had
    overflow-y: auto but nothing bounded their height, because body and .shell
    used min-height: 100vh rather than height. A flex child only becomes a
    scroll container when an ancestor bounds it, so the document scrolled
    instead and the two panes moved together.
    """
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert "min-height: 100vh" not in page, "a bounded height is what makes the panes scroll"
    assert "height: 100vh" in page
    # Every link of the flex chain must opt out of the default min-height: auto,
    # or it refuses to shrink below its content and the overflow never engages.
    assert page.count("min-height: 0") >= 5


def test_the_chat_text_stops_at_a_readable_measure() -> None:
    """The pane fills the window; the prose inside it must not.

    Layout C removed the old 800px column, so without a cap a line runs the
    full width of the window minus the rail. Capping the turn rather than the
    pane keeps the rail flush right while the text stays readable.
    """
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert "max-width: 78ch" in page


def test_the_two_summary_prompts_do_not_contradict_each_other() -> None:
    """briefing.SUMMARY_REQUEST and GAIA_SYSTEM_PROMPT both describe the summary.

    When they disagree the model splits the difference, which is how the first
    version produced a wall of text. Both must ask for the same shape.
    """
    from coordinator.briefing import SUMMARY_REQUEST
    from coordinator.gaia import GAIA_SYSTEM_PROMPT

    # Both are wrapped prose, so compare with runs of whitespace flattened:
    # the system prompt happens to break "bullet per subfunction" across a line.
    request = " ".join(SUMMARY_REQUEST.split())
    system = " ".join(GAIA_SYSTEM_PROMPT.split())

    assert "bullet per subfunction" in request
    assert "bullet per subfunction" in system
    # The markup instruction has to be in both too, or one prompt asks for bold
    # and the other does not, and the model splits the difference as before.
    assert "in bold" in request
    assert "in bold" in system
    # The superseded instruction must be gone from BOTH, not just one.
    assert "three or four sentences" not in request
    assert "three or four sentences" not in system


def test_ready_is_true_when_there_is_no_handshake_to_wait_for(client: TestClient) -> None:
    """The suite runs with BRIEFING_ON_STARTUP off, so nothing is pending."""
    assert client.get("/ready").json() == {"ready": True}


def test_the_page_waits_while_the_briefing_is_still_being_composed(client: TestClient) -> None:
    """GET / serves the waiting page, carrying the live rail, until the briefing lands."""
    client.app.state.briefing_ready = False

    page = client.get("/").text

    assert "Composing the operations briefing" in page
    # The whole point of serving during startup: the rail is already streaming.
    assert 'id="rail-lines"' in page
    assert "/activity/stream?thread_id=" in page
    assert "/ready" in page


def test_a_fresh_thread_does_not_wait_for_a_briefing_it_will_never_get(client: TestClient) -> None:
    """Only the operations thread has a briefing coming; /new must open at once."""
    client.app.state.briefing_ready = False

    page = client.get("/", params={"thread_id": "abc123"}).text

    assert "Composing the operations briefing" not in page
    assert 'value="abc123"' in page


def test_the_real_page_is_served_once_the_briefing_is_ready(client: TestClient) -> None:
    client.app.state.briefing_ready = True

    page = client.get("/").text

    assert "Composing the operations briefing" not in page
    assert 'name="thread_id"' in page


@pytest.mark.anyio
async def test_a_failed_handshake_still_releases_the_page() -> None:
    """A briefing that raises must flip the flag too, or the operator waits forever."""
    from coordinator import api as api_module

    class App:
        class state:
            coordinator = None
            briefing_ready = False

    async def failing(_coordinator):
        raise RuntimeError()

    original = api_module.run_handshake
    api_module.run_handshake = failing
    try:
        await api_module._open_briefing(App)
    finally:
        api_module.run_handshake = original

    assert App.state.briefing_ready is True



def test_both_controls_lock_together() -> None:
    """A question and a status report take the same lock on the coordinator, so
    starting one during the other queues silently for up to two minutes. The page
    must disable both rather than let that look like a hang."""
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert "function setControlsDisabled(disabled)" in page
    assert "briefingButton.disabled = disabled" in page


def test_the_waiting_page_ships_its_controls_already_disabled() -> None:
    """The waiting page reuses the real page's markup, so its buttons are live
    while the STARTUP handshake runs. Pressing them there would queue a second
    handshake behind the first."""
    from coordinator.templates import render_waiting_page

    import re

    from coordinator.templates import render_page

    def status_button(page: str) -> str:
        """The status report button's label. It is the first button in the markup."""
        return re.findall(r"<button[^>]*>([^<]*)</button>", page)[0]

    waiting = render_waiting_page("operations")

    assert "setControlsDisabled(true)" in waiting
    # Deliberately not asserting the wording: the invariant is that the waiting
    # page does not RELABEL a button the operator never pressed, whatever that
    # button currently says. Hardcoding the text made this fail on every rename.
    assert status_button(waiting) == status_button(render_page("operations", []))


def test_a_disabled_control_looks_disabled() -> None:
    """Both controls are disabled for up to two minutes during a status report.
    Without a :disabled rule they keep their full blue and still look pressable,
    so the operator cannot tell a busy console from an idle one. The :hover pair
    matters as much as the base rule: without it, pointing at a dead button
    repaints it as though it were live."""
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert "button:disabled," in page
    assert "button:disabled:hover" in page
    assert 'input[type="text"]:disabled' in page
    assert "cursor: not-allowed" in page


def test_emphasis_is_given_its_own_colour() -> None:
    """The prompts ask GAIA to bold the subfunction name and the word needs. That
    only helps readability if bold is visually distinct from the body text."""
    from coordinator.templates import render_page

    page = render_page("operations", [])

    assert ".conversation .turn strong" in page
