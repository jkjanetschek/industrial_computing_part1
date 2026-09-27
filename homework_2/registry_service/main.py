"""The hand-rolled agent registry: where agents announce themselves and GAIA looks them up.

Pattern: service registry. A lookup table (agent id -> AgentCard) with a network
interface, running as its own process with zero LiteLLM dependency. In-memory on
purpose: the assignment needs reproducible discovery, not durability.

Population strategy: agents self-register at startup via
POST /agents/register (see agents/registration.py). This service does NOT poll
agent cards; pick one strategy, do not build both.

Endpoints:
    POST /agents/register    -> accept an AgentCard, store (or replace) it
    GET  /agents             -> AgentListing of every known agent
    GET  /agents/{agent_id}  -> one AgentCard, 404 if unknown
    GET  /health             -> liveness check
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from shared.logging_setup import configure
from shared.schemas import AgentCard, AgentListing

log = logging.getLogger("registry")

# Only for `python -m registry_service.main`; compose and the README run uvicorn directly.
DEFAULT_REGISTRY_HOST = "0.0.0.0"
DEFAULT_REGISTRY_PORT = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup hook: logging is configured here rather than at import, so merely
    importing this module in a test does not start writing files."""
    configure("registry")
    log.info("registry up")
    yield


app = FastAPI(title="A2A Agent Registry", version="1.0.0", lifespan=lifespan)

# The whole registry state. Re-registering an id replaces the entry, so an agent
# that restarts (possibly on a new url) simply overwrites its old record.
_agents: dict[str, AgentCard] = {}


# response_model on every route means FastAPI validates what goes out with the
# same shared schema that validated what came in; the wire shape cannot drift.
@app.post("/agents/register", response_model=AgentCard)
def register_agent(card: AgentCard) -> AgentCard:
    """Upsert by id: a restarted agent (possibly on a new url) overwrites itself. Echoes the stored card."""
    action = "re-registered" if card.id in _agents else "registered"
    _agents[card.id] = card
    log.info("%s %s [%s] at %s", action, card.id, ", ".join(card.capabilities), card.url)
    return card


@app.get("/agents", response_model=AgentListing)
def list_agents() -> AgentListing:
    """Discovery: every registered card, in registration order. What lookup_agents reads."""
    listing = AgentListing(agents=list(_agents.values()))
    log.info("listing -> %s", ", ".join(card.id for card in listing.agents) or "none")
    return listing


@app.get("/agents/{agent_id}", response_model=AgentCard)
def get_agent(agent_id: str) -> AgentCard:
    """One card by id, 404 if unknown. For inspection with curl; the coordinator only ever lists."""
    card = _agents.get(agent_id)
    log.info("lookup %s -> %s", agent_id, "hit" if card else "miss")
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent id '{agent_id}'",
        )
    return card


# Liveness for compose: agents wait on `depends_on: registry: condition:
# service_healthy` before they try to register.
@app.get("/health")
def health() -> dict[str, object]:
    """Liveness plus the registered count, so `curl /health` shows whether the agents have arrived."""
    return {"status": "ok", "registered_agents": len(_agents)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=DEFAULT_REGISTRY_HOST, port=DEFAULT_REGISTRY_PORT)
