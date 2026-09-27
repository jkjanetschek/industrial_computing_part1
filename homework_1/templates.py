# ===========================================================================
# templates.py  --  HTML for the minimal web UI (client.py, WEB_MODE)
# ===========================================================================

def render_html(turns: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Homework Part: 1</title>

    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            padding: 40px 20px;
            font-family: Arial, sans-serif;
            background: #f1f5f9;
            color: #1e293b;
        }}

        .container {{
            width: 100%;
            max-width: 800px;
            margin: 0 auto;
            overflow: hidden;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
        }}

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

        .conversation {{
            min-height: 300px;
            max-height: 55vh;
            padding: 30px;
            overflow-y: auto;
            line-height: 1.6;
        }}

        .conversation .turn {{
            margin-bottom: 14px;
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

        input {{
            flex: 1;
            min-width: 0;
            padding: 13px 15px;
            font: inherit;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            outline: none;
        }}

        input:focus {{
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

        @media (max-width: 600px) {{
            form {{
                flex-direction: column;
            }}
        }}
    </style>
</head>

<body>
    <main class="container">
        <header>
            <h1>Homework - Part 1 - MCP Server</h1>
            <p>This is a computer parts shop. Ask questions about products and inventory.</p>
        </header>

        <section class="conversation" id="conversation">
            {turns}
        </section>

        <div class="error-banner" id="error-banner" hidden></div>

        <form method="post" action="/" id="chat-form">
            <input
                type="text"
                name="prompt"
                placeholder="Type your message..."
                aria-label="Message"
                autocomplete="off"
                autofocus
                required
            >
            <button type="submit">Send</button>
        </form>
    </main>

    <script>
        const form = document.getElementById('chat-form');
        const input = form.querySelector('input[name="prompt"]');
        const button = form.querySelector('button');
        const conversation = document.getElementById('conversation');
        const errorBanner = document.getElementById('error-banner');

        function setBusy(isBusy) {{
            input.disabled = isBusy;
            button.disabled = isBusy;
            button.textContent = isBusy ? 'Sending...' : 'Send';
        }}

        function addUserTurn(text) {{
            const turn = document.createElement('div');
            turn.className = 'turn user';
            turn.innerHTML = '<span class="role">user</span>';
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
            pending.innerHTML = '<span class="role">assistant</span><p>Thinking<span class="dots"></span></p>';
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
            const prompt = input.value.trim();
            if (!prompt) return; // guard clause: no early-return means no nested else

            hideError();
            input.value = '';
            setBusy(true);
            addUserTurn(prompt);
            const pending = addPendingTurn();

            try {{
                const response = await fetch('/', {{
                    method: 'POST',
                    body: new URLSearchParams({{ prompt }}),   // sets header automatically
                    signal: AbortSignal.timeout(120_000),
                }});
                if (!response.ok) throw new Error(`HTTP ${{response.status}}`);

                const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
                const fresh = doc.getElementById('conversation');
                if (!fresh) throw new Error('malformed response');

                conversation.replaceChildren(...fresh.childNodes); // avoids re-parsing HTML
            }} catch (error) {{
                pending.remove();
                showError(error.name === 'TimeoutError' ? 'Timed out.' : 'Could not reach the server.');
            }} finally {{
                setBusy(false);
                input.focus();
                conversation.scrollTop = conversation.scrollHeight;
            }}
        }});
    </script>
</body>
</html>"""
