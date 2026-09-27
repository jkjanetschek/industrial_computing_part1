"""Terminal client for GAIA. Talks to the running service (coordinator/api.py),
it does not build an agent of its own, so the terminal, the browser and curl
all share one GAIA and one memory per thread.

    python -m coordinator.main                      # chat loop on one thread, 'quit' to leave
    python -m coordinator.main "your question"      # one question, print trace and answer
    docker compose exec coordinator python -m coordinator.main

Where the service is comes from COORDINATOR_URL (default http://127.0.0.1:8080).
"""

import sys
from collections.abc import Callable

import httpx

from coordinator.thread_ids import new_thread_id
from shared.settings import settings

ASK_PATH = "/ask"
BRIEFING_PATH = "/briefing"
QUIT_WORDS = frozenset({"quit", "exit", "q"})
# One answer can take three agent calls plus model reasoning; generous on purpose.
REQUEST_TIMEOUT_SECONDS = 300.0

# Tests inject a factory returning a client with httpx.MockTransport, so no
# coordinator process is needed to exercise the terminal client.
ClientFactory = Callable[[], httpx.Client]


def default_client() -> httpx.Client:
    """Production factory: a synchronous client aimed at the running coordinator."""
    return httpx.Client(base_url=settings.COORDINATOR_URL, timeout=REQUEST_TIMEOUT_SECONDS)


def run(argv: list[str], client_factory: ClientFactory = default_client) -> None:
    """One question if given on the command line, otherwise the briefing and a chat loop."""
    question = " ".join(argv).strip()
    with client_factory() as client:
        if question:
            ask_and_print(client, question, new_thread_id())
            return
        chat_loop(client, print_briefing(client))


def print_briefing(client: httpx.Client) -> str:
    """Print the coordinator's current briefing and return the thread it lives on.

    A fresh thread would leave GAIA with no memory of the options she just
    offered, so the terminal joins the operations conversation rather than
    starting its own.
    """
    try:
        response = client.get(BRIEFING_PATH)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"cannot reach GAIA at {client.base_url}: {error}")
        return new_thread_id()
    body = response.json()
    if body["briefing"]:
        print(f"gaia> {body['briefing']}")
    return body["thread_id"]


def chat_loop(client: httpx.Client, thread_id: str) -> None:
    """Read questions until the operator quits or sends an empty line; every one lands on the same thread."""
    # No unconditional "ready" claim: print_briefing may already have reported
    # GAIA as unreachable, and this line follows it either way.
    print(f"thread {thread_id}. Type a question, or 'quit'.")
    while True:
        question = input("you> ").strip()
        if not question or question.lower() in QUIT_WORDS:
            return
        ask_and_print(client, question, thread_id)


def ask_and_print(client: httpx.Client, question: str, thread_id: str) -> None:
    """POST one question to /ask and print the tool trace (indented) followed by the answer."""
    try:
        response = client.post(ASK_PATH, json={"question": question, "thread_id": thread_id})
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"cannot reach GAIA at {client.base_url}: {error}")
        return
    body = response.json()
    for line in body["trace"]:
        print(f"  {line}")
    print(f"gaia> {body['answer']}")


if __name__ == "__main__":
    run(sys.argv[1:])
