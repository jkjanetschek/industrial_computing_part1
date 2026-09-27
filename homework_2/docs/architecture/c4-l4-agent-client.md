# L4: Code, `coordinator/agent_client.py`

**Question:** how does one `call_agent` tool call actually reach a
sub-agent and come back with text?
**Audience:** the developer editing `agent_client.py` or debugging an A2A
call. Legend in [README.md](README.md).

C4 has no way to show *order*, and order is the whole point of this
module (two round trips, polling until terminal). So this level pairs a
class diagram (the shape) with a UML sequence diagram (the mechanism).

## Shape

```mermaid
classDiagram
    direction LR

    class AgentClient {
        -client_factory: ClientFactory
        +call(card: AgentCard, message: str) str
    }

    class Route {
        <<frozen dataclass>>
        +base_url: str
        +path: str
        +headers: dict
    }

    class AgentCallError {
        <<Exception>>
        The agent answered, but not with a usable result
    }

    class _ROUTES {
        <<dict AgentCallMode to function>>
        "direct": _direct_route(card)
        "litellm": _litellm_route(card)
    }

    class module_functions {
        <<private helpers>>
        _post_rpc(client, route, method, params) dict
        _wait_for_terminal(client, route, task) dict
        _user_message(text) dict
        _is_message(result) bool
        _task_of(result) dict
        _text_of_task(task) str
        _text_of_parts(parts) str
    }

    class AgentCard {
        <<shared.schemas>>
        +id: str
        +url: str
    }

    AgentClient ..> _ROUTES : picks route by settings.AGENT_CALL_MODE
    _ROUTES ..> Route : builds
    AgentClient ..> module_functions : uses
    AgentClient ..> AgentCallError : raises
    AgentClient ..> AgentCard : reads id and url
    module_functions ..> AgentCallError : raises on JSON-RPC error, non-completed task, poll budget spent
```

`ClientFactory` is `Callable[[str], httpx.AsyncClient]`: production passes
`default_client` (one `AsyncClient` per call, 60 s per request); tests pass
a factory returning a client with `httpx.MockTransport`, so no agent process
is needed.

## Mechanism: one `AgentClient.call(card, message)`

```mermaid
sequenceDiagram
    autonumber
    participant T as tools.call_agent
    participant C as AgentClient.call
    participant R as _ROUTES[mode](card)
    participant P as _post_rpc
    participant W as _wait_for_terminal
    participant A as Agent (fasta2a) or LiteLLM relay

    T->>C: call(card, message)
    C->>R: route for settings.AGENT_CALL_MODE
    R-->>C: Route(base_url, path, headers)
    Note over C: async with client_factory(route.base_url) as client
    C->>P: message/send, params={"message": _user_message(text)}
    P->>A: POST route.path, JSON-RPC envelope {jsonrpc, id, method, params}
    A-->>P: {"result": {"task": {..., "status": {"state": "submitted"}}}}
    P->>P: raise_for_status(), then raise AgentCallError if "error" in body
    P-->>C: result
    alt result is a message (spec-allowed, fasta2a never does)
        C-->>T: _text_of_parts(result.parts)
    else result is a task
        C->>W: _task_of(result)
        loop up to MAX_POLLS (120), sleeping POLL_INTERVAL_SECONDS (1 s)
            W->>W: state in TERMINAL_STATES? return task
            W->>P: tasks/get, params={"id": task id}
            P->>A: POST route.path
            A-->>P: {"result": {..., "status": {"state": "working" | "completed" | "failed"}}}
            P-->>W: task (bare, not nested under "task")
        end
        W-->>C: terminal task, or AgentCallError after MAX_POLLS
        alt state != "completed"
            C-->>T: raise AgentCallError("... ended in state 'failed'")
        else
            C-->>T: _text_of_task(task): every text part of every artifact
        end
    end
    Note over T: tools.call_agent turns httpx.HTTPError and AgentCallError<br/>into "error: agent 'x' call failed: ..." for the model
```

## Things the diagram makes visible that the code only implies

- **Two response shapes.** `message/send` nests the task under
  `result.task`; `tasks/get` returns it bare under `result`. `_task_of` is
  the one line that hides that difference (verified live against fasta2a
  2.x; the module docstring has both JSON shapes side by side).
- **Two failure families.** Transport failures (agent down, 404 relay path,
  401 bad proxy key) are `httpx.HTTPError` from `raise_for_status`;
  protocol failures (JSON-RPC `error` member, task `failed`, poll budget
  spent) are `AgentCallError`. They are kept distinct so the coordinator can
  report "unreachable" and "answered badly" differently, even though
  `tools.py` turns both into a string.
- **The route is the only mode-dependent thing.** Envelope, polling and
  text extraction are identical for `direct` and `litellm`; LiteLLM's relay
  forwards the JSON-RPC body unchanged and dispatches on `method`.

| Element | Line |
|---|---|
| `AgentCallError` | `agent_client.py:92` |
| `Route` | `:103` |
| `default_client` | `:115` |
| `AgentClient.call` | `:120` |
| `_direct_route`, `_litellm_route`, `_ROUTES` | `:151`, `:156`, `:172` |
| `_post_rpc` | `:178` |
| `_wait_for_terminal` | `:196` |
| `_user_message`, `_is_message`, `_task_of`, `_text_of_task`, `_text_of_parts` | `:212` to `:233` |
| Constants `TERMINAL_STATES`, `POLL_INTERVAL_SECONDS`, `MAX_POLLS`, `CALL_TIMEOUT_SECONDS` | `:77` to `:85` |
