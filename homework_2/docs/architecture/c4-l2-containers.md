# L2: Containers

**Question:** what are the deployable units, and how do they talk to each
other?
**Audience:** whoever runs `docker compose up`; the reviewer of the
submission. Legend in [README.md](README.md).

```mermaid
graph TB
    operator(["Operator<br/>[Person]"])

    browser["Web console<br/>[Container: HTML + JS served by the coordinator]<br/>Conversation page with activity rail,<br/>polls /ready until the briefing exists"]
    cli["Terminal client and scenario<br/>[Container: Python, httpx]<br/>coordinator/main.py chat loop,<br/>coordinator/scenario.py one scripted run"]

    subgraph stack["docker compose network gaia: one image gaia-a2a:local, five services"]
        direction TB
        coordinator["GAIA coordinator, port 8080<br/>[Container: FastAPI, LangChain create_agent, MemorySaver]<br/>One agent process: /ask, web page,<br/>/briefing, /activity/stream"]
        registry["Registry, port 8000<br/>[Container: FastAPI, uvicorn, in-memory dict]<br/>POST /agents/register upserts a card,<br/>GET /agents lists them"]
        aether["AETHER agent, port 8001<br/>[Container: PydanticAI, fasta2a, uvicorn]<br/>Atmosphere tools. A2A JSON-RPC at /,<br/>card at /.well-known/agent-card.json"]
        demeter["DEMETER agent, port 8002<br/>[Container: PydanticAI, fasta2a, uvicorn]<br/>Flora tools, same shape"]
        hephaestus["HEPHAESTUS agent, port 8003<br/>[Container: PydanticAI, fasta2a, uvicorn]<br/>Fabrication tools, same shape"]
    end

    llm[("LLM backend<br/>[External system]<br/>OpenAI / Gemini / LiteLLM<br/>chat/completions")]

    operator --> browser
    operator --> cli
    browser -->|"GET/POST /, GET /ready, POST /briefing,<br/>GET /activity/stream (SSE) [HTTP]"| coordinator
    cli -->|"POST /ask, GET+POST /briefing<br/>[HTTP, JSON]"| coordinator

    coordinator -->|"GET /agents on every tool call<br/>[HTTP, AgentListing JSON] (REGISTRY_MODE=own)"| registry
    aether -->|"POST /agents/register<br/>at startup, retried [AgentCard JSON]"| registry
    demeter -->|"POST /agents/register"| registry
    hephaestus -->|"POST /agents/register"| registry

    coordinator -->|"message/send, then poll tasks/get<br/>[A2A JSON-RPC 2.0] (AGENT_CALL_MODE=direct)"| aether
    coordinator -->|"[A2A JSON-RPC 2.0]"| demeter
    coordinator -->|"[A2A JSON-RPC 2.0]"| hephaestus

    coordinator -->|"chat/completions"| llm
    aether -->|"chat/completions"| llm
    demeter -->|"chat/completions"| llm
    hephaestus -->|"chat/completions"| llm

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef external fill:#999999,stroke:#6b6b6b,color:#fff
    class operator person
    class browser,cli,coordinator,registry,aether,demeter,hephaestus container
    class llm external
```

## What the diagram says

Five processes from one image; only the `command:` differs
(`docker-compose.yml`, `Dockerfile`). The three agents are separate boxes and
not one "agents" box because they are separate processes that share no data:
each holds its own table (`SECTOR_ATMOSPHERE`, `SECTOR_FLORA`, `CAULDRONS`),
and what they share is only code (`agents/a2a_server.py`, `registration.py`,
`catalog.py`, `mutations.py`, `shared/`).

**Startup order** (compose `depends_on` with `condition: service_healthy`):
registry -> the three agents -> coordinator. An agent's healthcheck is its
own agent card; the coordinator's is `/health`, which answers as soon as the
process serves because the briefing handshake runs as a background task.

**The two arrows that are mode switches.** `coordinator -> registry` is
`REGISTRY_MODE=own`; with `litellm` the same call goes to the LiteLLM proxy's
`GET /v1/agents` instead (L1 shows the proxy). `coordinator -> agent` is
`AGENT_CALL_MODE=direct`; with `litellm` the same JSON-RPC body goes to the
proxy's `/a2a/{agent_id}` relay. Both switches are a dict of functions in one
module each (`registry_client._FETCHERS`, `agent_client._ROUTES`), so no
other container knows the mode.

**Why the clients are containers.** The web console is HTML+JS served by the
coordinator, and the terminal client is a separate Python process; both are
clients of the *one* running GAIA. There is deliberately no second agent in
the terminal: two processes would mean two `MemorySaver`s and two
conversations (`docs/design-notes.md`, "GAIA: the coordinator").

**Ports.** 8000 to 8003 are published for inspection from the host (`curl
localhost:8000/agents`, agent cards). GAIA never uses them; inside the network
she talks service names (`http://aether-agent:8001`), which are the URLs the
agents register with.

## Traceability

| Arrow | Source |
|---|---|
| agent -> registry | `agents/registration.py:63` `register_with_registry`, wired by `with_self_registration` (`:98`) into the app's lifespan |
| coordinator -> registry | `coordinator/registry_client.py:55` `_from_own_registry`, called from `tools.lookup_agents` (`tools.py:25`) and `tools.call_agent` (`:37`) and `briefing.collect_reports` (`briefing.py:82`) |
| coordinator -> agents | `coordinator/agent_client.py:120` `AgentClient.call` |
| every agent -> LLM | `agents/model_factory.py`, `coordinator/model_factory.py` |
| browser -> coordinator | routes in `coordinator/api.py:132` to `:231`; fetch/EventSource calls in `coordinator/templates.py` |
| terminal -> coordinator | `coordinator/main.py` `ASK_PATH`, `BRIEFING_PATH`; `coordinator/scenario.py` |
| startup order, ports, healthchecks | `docker-compose.yml` |
