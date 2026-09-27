# Architecture diagrams (C4)

The system drawn at four zoom levels, each answering one question for one
audience. All diagrams are Mermaid in Markdown, so GitHub renders them and
there is nothing to export or keep in sync by hand. Every element traces to a
compose service, a module or a class; every arrow was taken from a route
decorator or an `import` line, with the line number in the file's edge table.

| Level | File | Question it answers | Audience |
|---|---|---|---|
| L1 Context | [c4-l1-context.md](c4-l1-context.md) | What is this system, who uses it, what does it depend on? | Everyone, including the lecturer |
| L2 Containers | [c4-l2-containers.md](c4-l2-containers.md) | What are the deployable units and how do they talk? | Whoever runs `docker compose up`, the reviewer |
| L3 Components, coordinator | [c4-l3-coordinator.md](c4-l3-coordinator.md) | What are the parts inside GAIA and which depends on which? | Someone changing `coordinator/` |
| L3 Components, sub-agent | [c4-l3-agent.md](c4-l3-agent.md) | What are the parts inside one A2A agent process? | Someone adding or changing an agent |
| L4 Code, agent client | [c4-l4-agent-client.md](c4-l4-agent-client.md) | How does one A2A call actually proceed? | The developer editing `agent_client.py` |
| L4 Code, activity rail | [c4-l4-activity.md](c4-l4-activity.md) | How does a tool call become a line on the browser's rail? | The developer editing `activity.py` or the SSE route |


**Notation choice.** The diagrams are C4 in *content* (person, system,
container, component, labelled relationships, one level per diagram) but are
written as Mermaid flowcharts (`graph`) with C4 colours, not with Mermaid's
`C4Context`/`C4Container` syntax. That syntax was tried first: its renderer
has no layout engine, and at seven containers it overlapped every label and
put the LLM backend in the middle of the stack. A flowchart with the same
elements is legible, and the C4 semantics are carried by the labels
(`[Container: FastAPI]`) and the legend below.

## Legend, used on every diagram

| Notation | Meaning |
|---|---|
| Person (L1, L2) | A human role, never a system |
| Highlighted system / boundary box | The system under design; at L2 the `docker compose` stack |
| External system | Something we call but do not build (the LLM backend, the optional LiteLLM proxy) |
| Container (L2) | One process started by compose, labelled `name [technology]` |
| Component (L3) | One Python module inside a container |
| Solid arrow `A --> B` | A uses / calls B, in the direction of the dependency. The label says what flows and over what (`GET /agents [AgentListing]`, `A2A JSON-RPC`) |
| Dashed arrow `A -.-> B` | Same meaning, but optional (a mode switch) or in-process (an `import`, or a lifespan hook) |
| Cylinder | A store or a backend service we treat as opaque |

Arrows never mean data flow in one diagram and containment in another: a
container that holds components is drawn as a boundary box, not an arrow.

## Regenerating

Edit the Markdown. To check a diagram renders before pushing:

```bash
npx -y @mermaid-js/mermaid-cli -i <file>.mmd -o /tmp/out.svg
```

(where `<file>.mmd` is the fenced block copied out of the Markdown; on Ubuntu
with AppArmor user-namespace restrictions add `-p` with a puppeteer config
containing `{"args": ["--no-sandbox"]}`).
