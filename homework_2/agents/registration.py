"""Agent self-registration: each agent announces itself to the registry at startup.

Population strategy decision (push, not pull): the registry never polls agents,
so it needs no list of agent addresses. Adding an agent is a compose-only
change; it appears in GET /agents a few seconds after its container starts.

How this fits the server stack (see web_doc/agent_server_lifespan_explained.html):

    uvicorn            opens the port, sends ASGI events to the app
      -> Starlette     the framework; owns routes, middleware and the LIFESPAN
        -> FastA2A     fasta2a's subclass of Starlette (agent card + JSON-RPC routes)
          -> agent_to_a2a()   builds the FastA2A app around a PydanticAI Agent

The object agent_to_a2a() returns IS a Starlette app. A lifespan is Starlette's
"run this once at startup, and this once at shutdown" hook. uvicorn accepts HTTP
traffic only after the startup half has finished, so it is the one place where
"register, then serve" is guaranteed in that order.

Known weakness: the registry is in-memory, so if the registry container
restarts it comes back empty and running agents do not re-register. Restart the
agents (or the whole stack) in that case; documented in README.md.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
# Only used as a type: the app we receive is a FastA2A, which extends Starlette.
# Typing it as Starlette says "anything with a router and a lifespan will do".
from starlette.applications import Starlette

from shared.schemas import AgentCard
from shared.settings import settings

AGENT_CARD_PATH = "/.well-known/agent-card.json"
REGISTER_PATH = "/agents/register"
# The registry may start a second or two after the agent (compose starts
# services in parallel); 10 x 1 s covers that without hiding a real outage.
REGISTRATION_ATTEMPTS = 10
REGISTRATION_RETRY_SECONDS = 1.0
# Placeholder host for httpx. With ASGITransport no DNS lookup or socket is
# involved; httpx only needs *some* host to put into the ASGI scope.
IN_PROCESS_BASE_URL = "http://agent.internal"

log = logging.getLogger("register")

# A factory (not a client) because the client must be created inside the
# running event loop and closed right after the POST. Tests inject a factory
# that returns a client with httpx.MockTransport, so no registry is needed.
RegistryClientFactory = Callable[[], httpx.AsyncClient]


REGISTRY_TIMEOUT_SECONDS = 5.0


def registry_client() -> httpx.AsyncClient:
    """Production factory: a client aimed at the registry from the shared settings."""
    return httpx.AsyncClient(base_url=settings.REGISTRY_URL, timeout=REGISTRY_TIMEOUT_SECONDS)


async def register_with_registry(card: AgentCard, client: httpx.AsyncClient) -> None:
    """POST the card, retrying while the registry is still coming up.

    Raising after the last attempt is deliberate: it fails the app's startup,
    uvicorn exits non-zero, and compose shows the container as failed instead
    of silently running an agent nobody can discover.
    """
    for attempt in range(1, REGISTRATION_ATTEMPTS + 1):
        try:
            response = await client.post(REGISTER_PATH, json=card.model_dump())
            response.raise_for_status()
            log.info("registered agent '%s' at %s", card.id, card.url)
            return
        except httpx.HTTPError as error:
            log.warning("registration attempt %d/%d failed: %s", attempt, REGISTRATION_ATTEMPTS, error)
            await asyncio.sleep(REGISTRATION_RETRY_SECONDS)
    raise RuntimeError(f"agent '{card.id}' could not register with {settings.REGISTRY_URL}")


async def fetch_own_card(app: Starlette) -> dict:
    """Read the card this app serves, in-process, without a network round trip.

    Why not GET http://localhost:<port>/...: this runs during startup, and the
    port is not open yet (uvicorn opens it after startup completes). ASGITransport
    skips the socket entirely and calls app(scope, receive, send) directly, the
    same way Starlette's TestClient does. The result is the exact JSON the world
    will see, so there is no hand-built copy of the card that could drift.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=IN_PROCESS_BASE_URL) as client:
        response = await client.get(AGENT_CARD_PATH)
        response.raise_for_status()
        return response.json()


def with_self_registration(
    app: Starlette,
    agent_id: str,
    client_factory: RegistryClientFactory = registry_client,
) -> Starlette:
    """Wrap the app's lifespan so it registers once it is actually serving.

    Pattern: decorator around a context manager. agent_to_a2a installs its own
    lifespan (worker_lifespan: task manager + agent + background worker) and
    that lifespan is what executes message/send tasks. Passing our own via
    agent_to_a2a(lifespan=...) would REPLACE it (the source is
    ``lifespan = lifespan or worker_lifespan``), leaving an agent whose tasks
    never complete. So instead we read the installed lifespan back, and install
    a new one that enters the original first and then registers.

    Startup order this produces:
        our lifespan -> worker lifespan (task manager, worker up)
                     -> fetch own card -> POST /agents/register
                     -> yield (uvicorn now accepts requests)
    Shutdown runs the same nesting in reverse.
    """
    # Starlette keeps the lifespan as a plain attribute on its router; it is
    # read lazily when uvicorn sends "lifespan.startup", so capturing it here
    # and replacing it below (before uvicorn.run) is safe.
    inner_lifespan = app.router.lifespan_context

    # @asynccontextmanager turns this async generator into an async context
    # manager: the code before ``yield`` is __aenter__ (startup), the code after
    # it is __aexit__ (shutdown). That is exactly the shape Starlette expects.
    @asynccontextmanager
    async def lifespan_with_registration(started_app: Starlette) -> AsyncIterator[None]:
        # Enter the original lifespan first: FastA2A refuses any HTTP request
        # (including our in-process card fetch) until task_manager is running.
        async with inner_lifespan(started_app):
            card = AgentCard.from_a2a_card(agent_id, await fetch_own_card(started_app))
            async with client_factory() as client:
                await register_with_registry(card, client)
            # Code before yield is the custom context manager's startup phase.
            # The server handles requests while paused at yield; after yield,
            # this wrapper exits and the inner lifespan performs its shutdown.
            yield

    app.router.lifespan_context = lifespan_with_registration
    return app
