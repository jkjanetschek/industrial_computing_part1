"""HTML for the coordinator's web page (coordinator/api.py, GET and POST /).

Reused from homework part 1 (templates.py + the web part of client.py): same
container/header/conversation/form layout, the same JavaScript submit that
shows a pending turn and swaps in the fresh conversation, the same error
banner. Adapted for GAIA: a thread id travels with the form so one browser tab
is one conversation, each answer carries the tool trace that produced it, and
the timeout allows for a three-agent turn.
"""

from collections.abc import Iterator
from html import escape

from markdown_it import MarkdownIt

from coordinator.briefing import BRIEFING_FULL, BRIEFING_SUMMARY
from coordinator.gaia import Turn

# html_block/html_inline disabled so literal HTML in an answer (or in a tool
# result echoed by the model) is rendered as text, not markup.
_markdown = MarkdownIt("commonmark").disable(["html_block", "html_inline"])

PAGE_TITLE = "Homework Part: 2"
HEADER_TITLE = "Homework - Part 2 - Distributed A2A System"
HEADER_SUBTITLE = (
    "GAIA coordinates her subfunctions AETHER, DEMETER and HEPHAESTUS, three A2A agents "
    "discovered through the registry. They report their own incidents; you decide what she does."
)
PLACEHOLDER = "What do you propose to fix any problems?"
# Milliseconds the browser waits for one answer: three agent calls plus gpt-5 reasoning.
SUBMIT_TIMEOUT_MS = 300_000


def render_page(thread_id: str, turns: list[Turn]) -> str:
    return render_html("".join(_render_conversation(turns)), thread_id)


# Polled by the waiting page below. One second is responsive without being
# chatty, against a handshake that takes about two minutes.
READY_POLL_MS = 1000


def render_waiting_page(thread_id: str) -> str:
    """The console before the opening briefing exists.

    The same shell, CSS and rail as the real page, with a notice where the
    conversation goes. That is the point of serving it at all: the rail is
    already streaming the handshake, so instead of a spinner the operator
    watches the three subfunctions get polled and GAIA compose. When
    GET /ready flips, the page reloads itself into the real console.
    """
    notice = (
        '<div class="turn gaia waiting">'
        '<span class="role">gaia</span>'
        "<p>Composing the operations briefing<span class=\"dots\"></span></p>"
        "<p class=\"hint\">Polling each subfunction for a situation report, then composing "
        "and summarising. About two minutes on a cold start. The activity rail shows "
        "what is happening; this page opens by itself when the briefing lands.</p>"
        "</div>"
    )
    return render_html(notice, thread_id)


def _render_conversation(turns: list[Turn]) -> Iterator[str]:
    """Render each turn, folding the handshake's two turns into one bubble.

    The handshake writes two prompts the operator never typed, marked FULL and
    SUMMARY. Neither renders a question. The full briefing is held back and
    emitted inside the summary's disclosure, so the operator reads three
    sentences and can open the detail if they want it. GAIA keeps both in
    context either way, which is what lets "do the second option" resolve.
    """
    held_full = ""
    for turn in turns:
        if turn.marker == BRIEFING_FULL:
            held_full = turn.answer
            continue
        if turn.marker == BRIEFING_SUMMARY:
            yield _render_briefing(turn, held_full)
            held_full = ""
            continue
        yield _render_turn(turn)

    # A briefing_full with no briefing_summary after it means the handshake
    # crashed between its two turns. Without this flush the compose turn would
    # vanish from the page entirely and the operator would see an empty console
    # with no sign a briefing was ever composed. With no summary to lead with,
    # the full text is the only briefing there is, so it is shown rather than
    # folded away.
    if held_full:
        yield _render_orphaned_briefing(held_full)


def _render_briefing(summary: Turn, full: str) -> str:
    detail = (
        f'<details class="briefing"><summary>full briefing</summary>{_markdown.render(full)}</details>' if full else ""
    )
    return f'<div class="turn gaia"><span class="role">gaia</span>{_markdown.render(summary.answer)}{detail}</div>'


def _render_orphaned_briefing(full: str) -> str:
    return f'<div class="turn gaia"><span class="role">gaia</span>{_markdown.render(full)}</div>'


def _render_turn(turn: Turn) -> str:
    question = f'<div class="turn user"><span class="role">you</span><p>{escape(turn.question)}</p></div>'
    if not turn.answer:
        return question
    trace_lines = "\n".join(escape(line) for line in turn.trace)
    trace = (
        f'<details class="trace"><summary>tool trace ({len(turn.trace)} calls)</summary>'
        f"<pre>{trace_lines}</pre></details>"
    )
    answer = f'<div class="turn gaia"><span class="role">gaia</span>{_markdown.render(turn.answer)}{trace}</div>'
    return question + answer


def render_html(turns: str, thread_id: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{PAGE_TITLE}</title>

    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            /* A DEFINITE height, not min-height: overflow-y on a flex child only
               creates a scroll container when an ancestor bounds its height. With
               min-height the shell grew with its content and the document scrolled,
               which moved both panes together. overflow: hidden keeps the page
               itself from scrolling so the two panes are the only scrollers. */
            height: 100vh;
            overflow: hidden;
            font-family: Arial, sans-serif;
            background: #f1f5f9;
            color: #1e293b;
        }}

        /* Layout: the console fills the window, with the activity rail pinned to
           the right edge at a fixed width and the chat taking whatever is left.
           Under 900px the rail moves below the chat so the page still works on a
           phone. */
        .shell {{
            display: flex;
            align-items: stretch;
            height: 100vh;
            /* min-height: 0 on a flex child overrides the default min-height: auto,
               which otherwise refuses to shrink below the content and defeats the
               overflow on the panes inside. Needed on every link of the chain. */
            min-height: 0;
        }}

        .container {{
            display: flex;
            flex: 1;
            min-width: 0;
            min-height: 0;
            flex-direction: column;
            background: white;
            border-right: 1px solid #e2e8f0;
        }}

        .rail {{
            display: flex;
            flex-direction: column;
            width: 340px;
            flex-shrink: 0;
            min-height: 0;
            background: #0f172a;
            color: #7dd3fc;
            font-family: monospace;
            font-size: 12px;
        }}

        .rail h2 {{
            margin: 0;
            padding: 10px 14px;
            font-size: 11px;
            font-weight: normal;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            color: #94a3b8;
            border-bottom: 1px solid #1e293b;
        }}

        .rail .lines {{
            flex: 1;
            min-height: 0;
            padding: 10px 14px;
            overflow-y: auto;
            line-height: 1.7;
            white-space: pre-wrap;
        }}

        .rail .line .at {{ color: #475569; }}
        .rail .line.ok {{ color: #4ade80; }}
        .rail .line.error {{ color: #f87171; }}
        .rail .line.think {{ color: #94a3b8; }}
        .rail .line.start,
        .rail .line.done {{ color: #e2e8f0; }}

        header {{
            padding: 24px 30px;
            color: white;
            background: #2563eb;
        }}

        header h1 {{
            margin: 0 0 5px;
            font-size: 26px;
        }}

        header p {{
            margin: 0;
            color: #dbeafe;
        }}

        .thread {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            padding: 8px 30px;
            font-family: monospace;
            font-size: 12px;
            color: #64748b;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
        }}

        .thread a {{
            color: #2563eb;
        }}

        .conversation {{
            flex: 1;
            min-height: 0;
            max-height: none;
            padding: 30px;
            overflow-y: auto;
            line-height: 1.6;
        }}

        @media (max-width: 900px) {{
            .shell {{ flex-direction: column; }}
            .rail {{ width: auto; height: 240px; flex-shrink: 0; }}
        }}

        .conversation .turn {{
            /* Layout C gives the chat the whole window minus the rail, which on a
               wide monitor is far past a readable line. Cap the TEXT rather than
               the pane: 78ch is roughly 70 characters of prose, and ch scales with
               the font so this holds if the type size changes. Centred, because a
               left-hugging column looks lopsided in a very wide pane. */
            max-width: 78ch;
            margin: 0 auto 14px;
            padding: 14px 16px;
            background: #f8fafc;
            border-left: 4px solid #2563eb;
            border-radius: 8px;
        }}

        .conversation .turn.user {{
            background: #eef2ff;
            border-left-color: #64748b;
        }}

        .conversation .turn .role {{
            display: block;
            margin-bottom: 6px;
            font-weight: bold;
            text-transform: capitalize;
            color: #2563eb;
        }}

        .conversation .turn.user .role {{
            color: #475569;
        }}

        .conversation .turn > *:first-of-type {{
            margin-top: 0;
        }}

        .conversation .turn > *:last-child {{
            margin-bottom: 0;
        }}

        .conversation .turn p,
        .conversation .turn ul,
        .conversation .turn ol {{
            margin: 8px 0;
        }}

        .conversation .turn ul,
        .conversation .turn ol {{
            padding-left: 22px;
        }}

        .conversation .turn li {{
            margin: 4px 0;
        }}

        .conversation .turn h1,
        .conversation .turn h2,
        .conversation .turn h3 {{
            margin: 14px 0 6px;
            font-size: 16px;
        }}

        /* Markdown gives one emphasis element, so this is the single highlight
           colour. The prompts ask for bold on exactly two things: the subfunction
           name that opens a bullet, and the word "needs" that introduces a
           dependency on another subfunction, which is the cooperation constraint
           this system exists to show. */
        .conversation .turn strong {{
            color: #1d4ed8;
        }}

        .conversation .turn code {{
            padding: 2px 5px;
            background: #e2e8f0;
            border-radius: 4px;
            font-family: monospace;
        }}

        .conversation .turn pre {{
            padding: 10px;
            overflow-x: auto;
            background: #0f172a;
            color: #e2e8f0;
            border-radius: 6px;
        }}

        /* The tool trace: which sub-agents GAIA called for this answer. */
        .conversation .trace {{
            margin-top: 10px;
            font-size: 13px;
            color: #64748b;
        }}

        .conversation .trace summary {{
            cursor: pointer;
        }}

        .conversation .trace pre {{
            margin: 8px 0 0;
            font-size: 12px;
            white-space: pre-wrap;
            color: #7dd3fc;
        }}

        .conversation .waiting .hint {{
            color: #64748b;
            font-size: 14px;
        }}

        .conversation .pending .role {{
            color: #94a3b8;
        }}

        /* Discrete content animation: content isn't interpolable, so the
           browser jumps between the keyframe values instead of blending
           them -- exactly what a "." -> ".." -> "..." cycle needs. */
        @keyframes thinking-dots {{
            0%, 100% {{ content: "."; }}
            33%      {{ content: ".."; }}
            66%      {{ content: "..."; }}
        }}

        .pending .dots::after {{
            content: ".";
            animation: thinking-dots 1.2s steps(1, end) infinite;
        }}

        .error-banner {{
            margin: 0 30px;
            padding: 10px 14px;
            color: #b91c1c;
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-radius: 8px;
            font-size: 14px;
        }}

        form {{
            display: flex;
            gap: 10px;
            padding: 20px 30px;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
        }}

        input[type="text"] {{
            flex: 1;
            min-width: 0;
            padding: 13px 15px;
            font: inherit;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            outline: none;
        }}

        input[type="text"]:focus {{
            border-color: #2563eb;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
        }}

        button {{
            padding: 13px 22px;
            color: white;
            font: inherit;
            font-weight: bold;
            background: #2563eb;
            border: 0;
            border-radius: 8px;
            cursor: pointer;
        }}

        button:hover {{
            background: #1d4ed8;
        }}

        /* Both controls are disabled while a question or a status report is in
           flight, and a status report blocks for about two minutes. Without this
           the button keeps its full blue and looks pressable throughout, so the
           operator cannot tell the console is busy from the console it is not.
           The :hover pair is needed too, or pointing at a dead button repaints
           it as though it were live. */
        button:disabled,
        button:disabled:hover {{
            color: #64748b;
            background: #cbd5e1;
            cursor: not-allowed;
        }}

        input[type="text"]:disabled {{
            color: #94a3b8;
            background: #f1f5f9;
            cursor: not-allowed;
        }}

        @media (max-width: 600px) {{
            form {{
                flex-direction: column;
            }}
        }}
    </style>
</head>

<body>
    <div class="shell">
    <main class="container">
        <header>
            <h1>{HEADER_TITLE}</h1>
            <p>{HEADER_SUBTITLE}</p>
        </header>

        <div class="thread">
            <span>thread {escape(thread_id)}</span>
        </div>

        <form method="post" action="/briefing" class="briefing-form" id="briefing-form">
            <button type="submit">Request status report</button>
        </form>

        <section class="conversation" id="conversation">
            {turns}
        </section>

        <div class="error-banner" id="error-banner" hidden></div>

        <form method="post" action="/" id="chat-form">
            <input type="hidden" name="thread_id" value="{escape(thread_id)}">
            <input
                type="text"
                id="question"
                name="question"
                placeholder="{escape(PLACEHOLDER)}"
                aria-label="Question"
                autocomplete="off"
                autofocus
                required
            >
            <button type="submit">Ask GAIA</button>
        </form>
    </main>

    <aside class="rail">
        <h2>gaia activity</h2>
        <div class="lines" id="rail-lines"></div>
    </aside>
    </div>

    <script>
        const form = document.getElementById('chat-form');
        const input = form.querySelector('input[name="question"]');
        const threadId = form.querySelector('input[name="thread_id"]').value;
        const button = form.querySelector('button');
        const conversation = document.getElementById('conversation');
        const errorBanner = document.getElementById('error-banner');
        const briefingForm = document.getElementById('briefing-form');
        const briefingButton = briefingForm.querySelector('button');

        // Both controls move together. A question and a status report both take
        // the same per-thread lock on the coordinator, so starting one while the
        // other runs does not fail: it silently queues for up to two minutes,
        // which is indistinguishable from a hang. Disabling both is what makes
        // the wait legible.
        function setControlsDisabled(disabled) {{
            input.disabled = disabled;
            button.disabled = disabled;
            briefingButton.disabled = disabled;
        }}

        function setBusy(isBusy) {{
            setControlsDisabled(isBusy);
            button.textContent = isBusy ? 'Asking...' : 'Ask GAIA';
        }}

        function addUserTurn(text) {{
            const turn = document.createElement('div');
            turn.className = 'turn user';
            turn.innerHTML = '<span class="role">you</span>';
            const p = document.createElement('p');
            p.textContent = text; // textContent, not innerHTML: avoids HTML injection from user input
            turn.appendChild(p);
            conversation.appendChild(turn);
            conversation.scrollTop = conversation.scrollHeight;
            return turn;
        }}

        function addPendingTurn() {{
            const pending = document.createElement('div');
            pending.className = 'turn pending';
            pending.innerHTML = '<span class="role">gaia</span><p>Consulting subfunctions<span class="dots"></span></p>';
            conversation.appendChild(pending);
            conversation.scrollTop = conversation.scrollHeight;
            return pending;
        }}

        function showError(message) {{
            errorBanner.textContent = message;
            errorBanner.hidden = false;
        }}

        function hideError() {{
            errorBanner.hidden = true;
        }}

        form.addEventListener('submit', async (event) => {{
            event.preventDefault();
            const question = input.value.trim();
            if (!question) return; // guard clause: no early-return means no nested else

            hideError();
            input.value = '';
            setBusy(true);
            addUserTurn(question);
            const pending = addPendingTurn();

            try {{
                // POST / answers with a redirect to GET /?thread_id=...; fetch
                // follows it, so the response is the freshly rendered page.
                const response = await fetch('/', {{
                    method: 'POST',
                    body: new URLSearchParams({{ question, thread_id: threadId }}),   // sets header automatically
                    signal: AbortSignal.timeout({SUBMIT_TIMEOUT_MS}),
                }});
                if (!response.ok) throw new Error(`HTTP ${{response.status}}`);

                const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
                const fresh = doc.getElementById('conversation');
                if (!fresh) throw new Error('malformed response');

                conversation.replaceChildren(...fresh.childNodes); // avoids re-parsing HTML
            }} catch (error) {{
                pending.remove();
                showError(error.name === 'TimeoutError' ? 'Timed out.' : 'Could not reach GAIA.');
            }} finally {{
                setBusy(false);
                input.focus();
                conversation.scrollTop = conversation.scrollHeight;
            }}
        }});

        // The status report button is a plain form navigation, not a fetch, and
        // the handshake behind it blocks for roughly two minutes. Without
        // feedback the page looks frozen and a second click starts a second
        // handshake. A status report is a full navigation, so nothing re-enables
        // these: the page that comes back is freshly rendered.
        briefingForm.addEventListener('submit', () => {{
            setControlsDisabled(true);
            briefingButton.textContent = 'Asking subfunctions...';
        }});

        // The waiting page polls until the briefing exists, then reloads itself
        // into the real console. Only the waiting page carries .waiting, so the
        // real page never starts this timer.
        if (document.querySelector('.waiting')) {{
            // The startup handshake is already running and holds the same lock,
            // so neither control can do anything useful until it finishes. The
            // button keeps its resting label: the operator did not press it, and
            // a busy label on a control nobody touched reads as a rename.
            setControlsDisabled(true);

            const readyPoll = setInterval(async () => {{
                try {{
                    const response = await fetch('/ready');
                    if (!response.ok) return;
                    const body = await response.json();
                    if (body.ready) {{
                        clearInterval(readyPoll);
                        location.reload();
                    }}
                }} catch (error) {{
                    // A refused poll means the coordinator is restarting. Keep
                    // trying: the next tick will succeed once it is back.
                }}
            }}, {READY_POLL_MS});
        }}

        // The activity rail. EventSource is a plain HTTP GET that the server
        // never closes; each frame the server writes arrives here as one
        // message. It reconnects by itself if the connection drops and tells
        // the server the last event id it saw, so no line is missed or repeated.
        const railLines = document.getElementById('rail-lines');
        // How many pixels short of the bottom still counts as "at the bottom":
        // enough to survive a part-scrolled last line, small enough that a
        // deliberate scroll up is respected.
        const RAIL_BOTTOM_SLACK_PX = 40;
        const stream = new EventSource(`/activity/stream?thread_id=${{encodeURIComponent(threadId)}}`);

        stream.onmessage = (message) => {{
            // Measured BEFORE the line is appended, because appending changes
            // scrollHeight: an operator who has scrolled up to read an earlier
            // line must not be yanked to the bottom by the next event, and a
            // scenario run is many events.
            const nearBottom = railLines.scrollHeight - railLines.scrollTop - railLines.clientHeight < RAIL_BOTTOM_SLACK_PX;

            const event = JSON.parse(message.data);
            const line = document.createElement('div');
            line.className = `line ${{event.kind}}`;
            line.innerHTML = '<span class="at"></span> ';
            line.querySelector('.at').textContent = event.at;
            line.appendChild(document.createTextNode(event.text)); // textContent, not innerHTML: the text quotes tool output
            railLines.appendChild(line);
            if (nearBottom) {{ railLines.scrollTop = railLines.scrollHeight; }}
        }};
    </script>
</body>
</html>"""
