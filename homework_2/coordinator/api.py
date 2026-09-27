"""The coordinator as a service: the ONE running GAIA.

    python -m uvicorn coordinator.api:app --port 8080

Routes
    GET  /health   compose healthcheck
    POST /ask      JSON {"question": ..., "thread_id"?: ...} -> {"answer", "thread_id", "trace"}
                   for curl, the terminal client (coordinator/main.py) and the smoke test
    GET  /         web page, landing on the operations thread (?thread_id=... for any other)
    POST /         the page's form; redirects back to GET /?thread_id=...
    GET  /briefing JSON {"thread_id", "briefing", "full_briefing"} of the latest
                   operations briefing: the summary the operator reads and the
                   full text behind it
    POST /briefing re-runs the startup handshake, then redirects to GET /?thread_id=operations
    GET  /activity/stream server-sent events of the activity log for a thread

One coordinator is built at startup and kept on app.state, so its MemorySaver
lives as long as the process: every front end that reuses a thread_id
continues the same conversation, whichever door it came through. At startup
the coordinator also runs the operations briefing handshake (unless
BRIEFING_ON_STARTUP is off), so the page has something to show before any
operator has asked a question.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from coordinator.activity import Event, activity_log
from coordinator.briefing import BRIEFING_FULL, BRIEFING_SUMMARY, BRIEFING_THREAD_ID, run_handshake
from coordinator.gaia import Turn, ask, build_coordinator, thread_messages, turns
from coordinator.templates import render_page, render_waiting_page
from coordinator.thread_ids import new_thread_id
from shared.logging_setup import configure
from shared.settings import settings

log = logging.getLogger("coordinator")

# "See Other" after a form POST: the browser follows with a GET, so a refresh
# re-reads the page instead of re-submitting the question.
SEE_OTHER = 303


class AskRequest(BaseModel):
    """Body of POST /ask. No thread_id starts a new conversation."""

    question: str = Field(min_length=1)
    thread_id: str | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class AskResponse(BaseModel):
    """Reply of POST /ask: the thread_id is returned so the caller can continue the conversation."""

    answer: str
    thread_id: str
    trace: list[str]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start serving at once, and compose the briefing alongside the server.

    uvicorn awaits this function's startup before it calls create_server, so
    anything awaited before the yield happens with NO listening socket: a
    browser gets a connection reset, not a slow page. The handshake takes about
    two minutes, so awaiting it here made the console unreachable for that whole
    window. Running it as a task instead lets GET / answer immediately with a
    waiting page whose rail is already streaming the handshake as it happens.
    """
    configure("gaia")
    # Built here, not at import, so tests can swap build_coordinator first.
    app.state.coordinator = build_coordinator()
    # With no handshake to wait for there is nothing to be un-ready about.
    app.state.briefing_ready = not settings.BRIEFING_ON_STARTUP
    task = asyncio.create_task(_open_briefing(app)) if settings.BRIEFING_ON_STARTUP else None
    yield
    if task is not None and not task.done():
        task.cancel()


STARTUP_BRIEFING = "startup briefing"
BRIEFING_REFRESH = "briefing refresh"


async def _open_briefing(app: FastAPI) -> None:
    """Best effort: the coordinator must come up even when a subfunction does not.

    The ready flag is set in a finally, so a FAILED handshake releases the page
    too. Waiting only on success would leave the operator watching a spinner
    forever, when the console would have shown them the error line on the rail.
    """
    try:
        await run_handshake(app.state.coordinator)
    except Exception as error:
        _record_briefing_failure(STARTUP_BRIEFING, error)
    finally:
        app.state.briefing_ready = True


def _record_briefing_failure(stage: str, error: Exception) -> None:
    """Log a failed handshake and put it on the rail, rather than taking the console down with it.

    Also in the rail, not only in the container log: an operator looking at an
    empty console should be able to see why it is empty.

    `Exception`, not `BaseException`: gaia.run_turn catches BaseException and
    re-raises precisely so a CancelledError or a KeyboardInterrupt still aborts
    startup, and swallowing either here would defeat that.

    `{error!r}` rather than `{error}`: a bare RuntimeError() formats as an empty
    string, and the rail line would then say nothing at all.
    """
    log.warning("%s failed, continuing without one: %r", stage, error)
    activity_log.append(BRIEFING_THREAD_ID, "error", f"{stage} failed: {error!r}")


app = FastAPI(title="GAIA coordinator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """Liveness for compose and the terminal client: the process serves, nothing more."""
    return {"status": "ok"}


def _briefing_ready(request: Request) -> bool:
    """Whether the startup handshake has finished (or was never started). Default True: nothing to wait for."""
    return bool(getattr(request.app.state, "briefing_ready", True))


@app.get("/ready")
async def ready(request: Request) -> dict:
    """Whether the opening briefing is done, either way.

    Distinct from /health, which now answers as soon as the process serves.
    A failed handshake counts as ready: there is nothing further to wait for.
    """
    return {"ready": _briefing_ready(request)}


@app.post("/ask", response_model=AskResponse)
async def ask_gaia(request: Request, body: AskRequest) -> AskResponse:
    """The JSON door: one question in, GAIA's answer and the tool trace that produced it out."""
    coordinator = request.app.state.coordinator
    thread_id = body.thread_id or new_thread_id()
    answer = await ask(coordinator, body.question, thread_id)
    latest = turns(thread_messages(coordinator, thread_id))[-1]
    return AskResponse(answer=answer, thread_id=thread_id, trace=latest.trace)


@app.get("/", response_class=HTMLResponse)
async def page(request: Request, thread_id: str | None = None) -> str:
    """The web console: the whole conversation of one thread, rendered server-side."""
    coordinator = request.app.state.coordinator
    # No thread_id means the operations thread, the one the briefing was composed
    # on. Minting a fresh id here would render the briefing into a conversation
    # GAIA has no memory of.
    thread_id = thread_id or BRIEFING_THREAD_ID
    if thread_id == BRIEFING_THREAD_ID and not _briefing_ready(request):
        # Only the operations thread waits. Any other thread_id, from a script or
        # a bookmarked URL, has no briefing coming and should open straight away.
        return render_waiting_page(thread_id)
    return render_page(thread_id, turns(thread_messages(coordinator, thread_id)))


@app.post("/")
async def submit(request: Request, question: str = Form(...), thread_id: str = Form(...)) -> RedirectResponse:
    """The page's form: ask on the given thread, then send the browser back to GET / for that thread."""
    coordinator = request.app.state.coordinator
    if question.strip():
        await ask(coordinator, question.strip(), thread_id)
    # Redirect after POST so a browser refresh does not re-ask the question.
    return RedirectResponse(url=f"/?thread_id={thread_id}", status_code=SEE_OTHER)


@app.get("/briefing")
async def latest_briefing(request: Request) -> dict:
    """The current briefing: the summary the operator reads, and the full text behind it."""
    coordinator = request.app.state.coordinator
    thread = turns(thread_messages(coordinator, BRIEFING_THREAD_ID))
    return {
        "thread_id": BRIEFING_THREAD_ID,
        "briefing": _latest_marked(thread, BRIEFING_SUMMARY),
        "full_briefing": _latest_marked(thread, BRIEFING_FULL),
    }


def _latest_marked(thread: list[Turn], marker: str) -> str:
    """The newest answer to a prompt carrying this marker, or "" if there is none.

    Selecting by marker rather than by position: the briefing used to be read as
    thread[-1], which stopped being the briefing the moment the operator asked
    anything on the shared operations thread.
    """
    answers = [turn.answer for turn in thread if turn.marker == marker]
    return answers[-1] if answers else ""


@app.post("/briefing")
async def refresh_briefing(request: Request) -> RedirectResponse:
    """Re-run the handshake: fresh incidents from every registered subfunction.

    Degrades the same way startup does. This button is a plain form navigation,
    so an unhandled failure would replace the whole console with a bare 500
    page, including the rail that would have explained what went wrong. The
    operator gets the console back with the failure on the rail instead.
    """
    try:
        await run_handshake(request.app.state.coordinator)
    except Exception as error:
        _record_briefing_failure(BRIEFING_REFRESH, error)
    return RedirectResponse(url=f"/?thread_id={BRIEFING_THREAD_ID}", status_code=SEE_OTHER)


KEEP_ALIVE_SECONDS = 15


@app.get("/activity/stream")
async def activity_stream(request: Request, thread_id: str = BRIEFING_THREAD_ID, after: int = 0) -> StreamingResponse:
    """Server-sent events: what GAIA is doing on this thread, as she does it.

    How it works, end to end:

    1. The browser opens a long-lived GET and holds it. Unlike every other route
       here, this response never finishes; it yields frames until the browser
       goes away.
    2. We subscribe BEFORE replaying the backlog. Subscribing second would drop
       anything appended in between.
    3. We replay everything newer than the cursor, so a browser that connects
       after the startup handshake still sees it. This is the whole reason the
       log is stored rather than merely streamed.
    4. Then we forward whatever lands in our queue. Because steps 2 and 3
       overlap, an event can arrive by both routes; `seen` discards the
       duplicate.
    5. A silent connection is dropped by browsers and proxies, so an idle
       timeout emits a comment frame (a line starting with ':') that means
       nothing to EventSource but keeps the socket warm.
    6. EventSource reconnects on its own after a drop and sends back the last id
       it saw as the Last-Event-ID header, which we read as the cursor. That is
       why every frame carries `id:`, and why the log numbers events globally.
       A cursor left over from a previous process (a restart happened while a
       tab was open) is reset to 0 rather than honoured; see `_cursor`.
    """
    after = _cursor(request, after)

    async def frames() -> AsyncIterator[str]:
        with activity_log.subscribe(thread_id) as queue:
            seen = after
            for event in activity_log.since(thread_id, after):
                seen = event.n
                yield _frame(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEP_ALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event.n <= seen:
                    continue
                seen = event.n
                yield _frame(event)

    return StreamingResponse(frames(), media_type="text/event-stream")


def _frame(event: Event) -> str:
    """One server-sent event: an id line (the reconnect cursor), a data line, and the blank line that ends it."""
    return f"id: {event.n}\ndata: {event.as_json()}\n\n"


def _cursor(request: Request, after: int) -> int:
    """Where to resume: the Last-Event-ID the browser sent, else the query parameter.

    A cursor beyond the newest number this process has issued cannot be ours:
    EventSource reconnects after a coordinator restart still carrying the
    previous process's id, and honouring it would filter out every new event
    until the fresh counter overtook it. Treat it as "start from the beginning".
    A header that is not an integer is ignored for the same reason a 500 would
    be wrong here: EventSource treats a non-200 as permanent and stops retrying.
    """
    header = request.headers.get("last-event-id")
    try:
        cursor = int(header) if header else after
    except ValueError:
        cursor = after
    return 0 if cursor > activity_log.newest_number else cursor
