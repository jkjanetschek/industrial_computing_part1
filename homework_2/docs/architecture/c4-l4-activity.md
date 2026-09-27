# L4: Code, the activity rail (`coordinator/activity.py`, `gaia.run_turn`, `api.activity_stream`)

**Question:** how does a tool call inside a GAIA turn become a line on the
browser's activity rail, and how does a browser that connects late still see
it?
**Audience:** the developer editing `activity.py`, `run_turn` or the SSE
route. Legend in [README.md](README.md).

## Shape

```mermaid
classDiagram
    direction LR

    class Event {
        <<frozen dataclass>>
        +n: int, global sequence number, the SSE id
        +at: str, HH:MM:SS
        +kind: str, one of start think call ok error done
        +text: str
        +as_json() str
    }

    class ActivityLog {
        -_threads: OrderedDict of deque of Event, max 50 threads x 500 events
        -_last_number: int
        -_subscribers: dict of set of asyncio.Queue
        +subscribe(thread_id) contextmanager Queue
        +append(thread_id, kind, text) Event
        +since(thread_id, after) list of Event
        +newest_number int
        -_thread(thread_id) deque
    }

    class ActivityRecorder {
        <<langchain BaseCallbackHandler, run_inline = True>>
        -_log: ActivityLog
        -_thread_id: str
        -_in_flight: dict run_id to name and start time
        +tool_calls: int
        +on_chat_model_start()
        +on_tool_start(run_id, inputs)
        +on_tool_end(output, run_id)
        +on_tool_error(error, run_id)
        -_finish(run_id) name and elapsed
    }

    class run_turn {
        <<gaia.py, holds the thread lock>>
        writes start line, ainvoke with callbacks, done or error line
    }

    class activity_stream {
        <<api.py GET /activity/stream, SSE>>
        _cursor(request, after) int
        frames() async generator
        _frame(event) str
    }

    class AsyncioQueue {
        <<asyncio.Queue, one per connected browser>>
    }

    ActivityLog "1" *-- "many" Event : stores per thread
    ActivityRecorder --> ActivityLog : append(kind, text)
    run_turn --> ActivityRecorder : one per turn
    run_turn --> ActivityLog : start / done / error lines
    activity_stream --> ActivityLog : subscribe(), since(), newest_number
    ActivityLog ..> AsyncioQueue : put_nowait on every append
```

`activity_log` is one module-level `ActivityLog` per process
(`activity.py:211`), like `tools.agent_client`; tests swap the attribute.

## Mechanism: from a tool call to a rail line, and to a late browser

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser (EventSource)
    participant S as api.activity_stream
    participant L as activity_log (ActivityLog)
    participant G as gaia.run_turn
    participant R as ActivityRecorder
    participant LC as LangGraph agent loop

    Note over G,LC: a turn on thread T, lock held
    G->>L: append(T, "start", "turn start")
    G->>LC: ainvoke(messages, callbacks=[recorder])
    LC->>R: on_chat_model_start
    R->>L: append(T, "think", "thinking")
    LC->>R: on_tool_start(run_id, inputs)
    R->>R: _in_flight[run_id] = (name, now)
    R->>L: append(T, "call", "-> call_agent(agent_id='aether')")
    LC->>R: on_tool_end(output, run_id)
    R->>R: _finish(run_id): elapsed
    alt output starts with "error:"
        R->>L: append(T, "error", "<- error: ... (1.2s)")
    else
        R->>L: append(T, "ok", "<- ok, 0.3 kB (1.2s)")
    end
    LC-->>G: final messages
    G->>L: append(T, "done", "answer ready (2 tool calls, 14.0s)")

    Note over L: every append: n += 1, store in the thread's deque,<br/>log line, put_nowait into each subscriber queue

    Note over B,S: meanwhile, or later: a browser opens the rail
    B->>S: GET /activity/stream?thread_id=T (Last-Event-ID on reconnect)
    S->>S: after = _cursor(request, after), reset to 0 if beyond newest_number
    S->>L: subscribe(T) -> queue   (BEFORE replaying, so nothing is missed)
    S->>L: since(T, after)
    L-->>S: backlog newer than the cursor
    S-->>B: one frame per event: "id: n\ndata: {...}\n\n"
    loop until the browser goes away
        S->>L: await queue.get() with 15 s timeout
        alt timeout
            S-->>B: ": keep-alive" comment frame
        else event, and event.n > seen
            S-->>B: frame (duplicates from the overlap of replay and queue are dropped by n)
        end
    end
    Note over S,L: on disconnect, subscribe()'s finally removes the queue<br/>and drops the thread key when its last queue goes
```

## Why it is built this way (each point is a comment in the code)

- **`run_inline = True`** on the recorder: langchain-core would otherwise
  dispatch a sync handler on an executor thread, and `asyncio.Queue` is not
  thread-safe. Inline also keeps the callback lines ordered with the
  start/done lines `run_turn` writes on the loop.
- **A queue per subscriber, not a shared `asyncio.Event`.** Set-then-clear
  is missed by any subscriber not yet at its `await`; a queue holds the
  event until read.
- **Store, do not just stream.** The ring buffer is what lets a browser that
  connects after the startup handshake (which runs in the lifespan, before
  any browser exists) still see it, and what survives a page reload.
- **Bounded on both axes.** 500 events per thread, 50 threads, oldest
  touched thread evicted; and `since()` never creates a thread entry,
  because `thread_id` arrives from an unauthenticated query parameter.
- **Global event numbers as SSE ids.** `EventSource` reconnects on its own
  and sends `Last-Event-ID`; `_cursor` honours it unless it is from a
  previous process (greater than `newest_number`), in which case it replays
  from the start rather than filtering everything out.
- **Error detection by prefix.** A failed A2A call does not raise (by
  design, so GAIA can reason about it), so `on_tool_end` checks for
  `tools.ERROR_PREFIX`; without it a failed call would render as `ok`.

| Element | Line |
|---|---|
| `routing_args` | `activity.py:50` |
| `Event` | `:61` |
| `ActivityLog` (`subscribe`, `append`, `newest_number`, `since`, `_thread`) | `:74` |
| `ActivityRecorder` | `:151` |
| `activity_log` singleton | `:211` |
| `gaia.run_turn` | `gaia.py:190` |
| `api.activity_stream`, `_frame`, `_cursor` | `api.py:231`, `:278`, `:283` |
