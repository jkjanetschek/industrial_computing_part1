# L1: System context

**Question:** what is this system, who uses it, and what outside itself does
it depend on?
**Audience:** everyone. Legend in [README.md](README.md).

```mermaid
graph LR
    operator(["Operator<br/>[Person]<br/>Asks GAIA to restore sectors, reads the<br/>operations briefing, decides which remedy she applies"])

    gaia["GAIA distributed A2A system<br/>[Software system, this repo]<br/>A coordinator agent that discovers three specialised<br/>sub-agents through a registry and delegates to them over A2A.<br/>Registry + three agents + coordinator"]

    llm["LLM backend<br/>[External system]<br/>OpenAI, Gemini or a LiteLLM proxy.<br/>One chat/completions wire format for all three (LLM_BACKEND)"]

    proxy["LiteLLM proxy as infrastructure<br/>[External system, OPTIONAL]<br/>Agent registry (GET /v1/agents) and A2A relay (/a2a/id)<br/>only when REGISTRY_MODE or AGENT_CALL_MODE is litellm"]

    operator -->|"Web console, terminal client, curl<br/>HTTP: GET/POST /, POST /ask"| gaia
    gaia -->|"Reasons with (four agents in total)<br/>chat/completions over HTTP"| llm
    gaia -.->|"Discovers agents, relays A2A calls<br/>HTTP with Bearer key, litellm modes only"| proxy

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef system fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#999999,stroke:#6b6b6b,color:#fff
    class operator person
    class gaia system
    class llm,proxy external
```

## What the diagram says

The system is one box here on purpose: to the operator it is "GAIA", a
single conversation partner reachable through three doors (browser, terminal,
`curl`) that all share one memory per thread. What makes it a *distributed*
system is invisible at this level and is the subject of L2.

Two things outside the box:

- The **LLM backend** is where every model call goes, for GAIA and for each
  sub-agent. `shared/settings.py` selects OpenAI, Gemini or LiteLLM by
  changing only `base_url` and key; no provider SDK is used anywhere
  (`agents/model_factory.py`, `coordinator/model_factory.py`).
- The **LiteLLM proxy as infrastructure** is drawn dashed because it is a
  mode, not a requirement. With the defaults (`REGISTRY_MODE=own`,
  `AGENT_CALL_MODE=direct`) it is not contacted at all; the stack has its own
  registry and calls agents directly. `docs/design-notes.md` "Registry mode" and
  "Coordinator: calling agents" explain the two switches.

Decision embodied: the coordinator cannot answer a restoration question
alone. AETHER knows the air, DEMETER knows the flora, HEPHAESTUS knows the
machines, and each incident they report needs another one to resolve. That
is the assignment's "one planner that must delegate", and it is why the box
contains more than one agent.

## Traceability

| Element | Where it is defined |
|---|---|
| Operator doors | `coordinator/templates.py` (page), `coordinator/main.py` (terminal), `POST /ask` in `coordinator/api.py` |
| System boundary | `docker-compose.yml`: five services, one image |
| LLM backend | `shared/settings.py` `LLM_BACKEND`, `_BackendEndpoints`, `resolved_base_url` |
| LiteLLM proxy | `shared/settings.py` `REGISTRY_MODE`, `AGENT_CALL_MODE`, `LITELLM_PROXY_URL`; `coordinator/registry_client.py`, `coordinator/agent_client.py` |
