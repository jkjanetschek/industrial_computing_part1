# GAIA: a distributed A2A system (Homework Teil 2)

One coordinator agent (GAIA, LangChain) that cannot answer alone: it discovers
three sub-agents (AETHER, DEMETER, HEPHAESTUS; PydanticAI served over A2A)
through a registry and delegates to them. The domain is Horizon Zero Dawn's
terraforming system: air, flora and machine fabrication, each owned by one
agent, each incident needing another agent to resolve.

## Start it

Requirements: Python 3.12, Docker with compose, and an OpenAI-compatible
LLM endpoint that returns structured tool calls (OpenAI, Gemini, a LiteLLM
proxy, or LM Studio with a model such as Qwen2.5-14B-Instruct).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.lock

cp .env.example .env          # set LLM_API_KEY, LLM_MODEL, and COMPOSE_LLM_BASE_URL for your endpoint
docker compose up --build -d  # one image, five services: registry, three agents, coordinator
docker compose ps             # wait until all five are "healthy" (about 20 s)
```

Open <http://localhost:8080>.

Other doors to the same GAIA (same conversation memory per thread):

```bash
python -m coordinator.main                 # chat in the terminal
python -m coordinator.main "Can we reforest the Sacred Lands into temperate forest?"
python -m coordinator.scenario             # one scripted end-to-end run
curl -s localhost:8000/agents              # what the registry knows
LLM_API_KEY=x python -m pytest -q          # 179 unit tests, no network
docker compose down
```

## The web page

- **Left, the conversation.** On a cold start you see a waiting page while
  GAIA polls each sub-agent for a situation report and composes the
  *operations briefing* (about two minutes); the page opens by itself when it
  lands. The briefing bubble shows the short summary; "full briefing" unfolds
  the complete text. Type what GAIA should do about the reported incidents
  (for example "resolve the blight outbreak first") and send.
- **Under every answer, "tool trace (n calls)"** lists which sub-agents GAIA
  actually called and what they returned. Zero calls on a question that
  needs data means the model did not delegate.
- **Right, the activity rail.** Live, as it happens: `thinking`, `-> call_agent(agent_id='aether')`,
  `<- ok`, `<- error: ...`, `answer ready (n tool calls, 14.0s)`. Red lines
  are failures, including an unreachable sub-agent.
- **"Request status report"** re-runs the briefing: the sub-agents invent new
  incidents into their state, and GAIA composes a fresh briefing.

## Logs

Each service writes to `logs/<service>.log` (`registry`, `aether`, `demeter`,
`hephaestus`, `gaia`), bind-mounted from the containers, so a run can be read
afterwards. `gaia.log` has one line per question, tool call and answer, the
same lines the rail shows. Third-party HTTP chatter is filtered out.

## How the repo works

| Path | What it is |
|---|---|
| `registry_service/main.py` | In-memory registry: agents `POST /agents/register` at startup, GAIA `GET /agents`. |
| `agents/*_agent.py` | The three sub-agents: a static table, tool functions over it (read, incident, remedy), a system prompt, an `AgentSpec`. |
| `agents/a2a_server.py`, `registration.py` | Shared plumbing: PydanticAI `Agent` → fasta2a A2A app → self-registration in the lifespan → uvicorn. |
| `agents/catalog.py`, `mutations.py` | Shared helpers: name matching and error shapes; guarded delta writes with bounds. |
| `coordinator/gaia.py` | GAIA's agent core: LangChain `create_agent` with two tools, `MemorySaver` per `thread_id`, per-thread locks. |
| `coordinator/tools.py` | `lookup_agents(capability)` and `call_agent(agent_id, message)`; failures return `error:` strings. |
| `coordinator/registry_client.py`, `agent_client.py` | Discovery and A2A JSON-RPC calls; `REGISTRY_MODE` and `AGENT_CALL_MODE` are strategy tables (own registry or LiteLLM proxy). |
| `coordinator/briefing.py` | Startup handshake: deterministic fan-out to every agent, then two GAIA turns (compose, summarise). |
| `coordinator/activity.py`, `api.py`, `templates.py` | Activity rail (LangChain callbacks → ring buffer → server-sent events), FastAPI routes, the page. |
| `coordinator/main.py`, `scenario.py` | Terminal client and scripted run; HTTP clients of the one running coordinator. |
| `shared/` | `settings.py` (one frozen config for every process), `schemas.py` (`AgentCard`), `logging_setup.py`. |
| `tests/` | Unit tests with fakes; `test_smoke.py` runs the scenario against a live stack when one is reachable. |

Configuration is `.env` only; `.env.example` documents every variable.
`LLM_BACKEND` switches OpenAI / Gemini / LiteLLM by changing `base_url` and
key, never the SDK.

## Further reading

- [docs/architecture/README.md](docs/architecture/README.md): C4 diagrams,
  L1 context to L4 code, with every arrow traced to a line in the source.

