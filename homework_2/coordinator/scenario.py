"""One reproducible end-to-end run, for the submission screenshot and the smoke test.

    python -m coordinator.scenario
    docker compose exec coordinator python -m coordinator.scenario

Like coordinator/main.py this is an HTTP client of the running service, not a
second GAIA: what it prints is what the deployed stack actually did. It refreshes
the briefing first, so a run never depends on how long the containers have been
up or on what a previous operator already fixed.
"""

import sys

import httpx

from coordinator.main import ASK_PATH, BRIEFING_PATH, ClientFactory, default_client


INSTRUCTION = (
    "Deal with the situation your subfunctions just reported. Take the route that contains "
    "the problem fastest, apply it, and tell me the figures before and after. Call the "
    "subfunctions with the call_agent tool and do not invent actions or responses from them."
)


def run(client_factory: ClientFactory = default_client) -> int:
    """Refresh the briefing, ask the scenario question on the operations thread, print everything. Exit code 0 on success."""
    with client_factory() as client:
        try:
            # raise_for_status() only raises on 4xx/5xx, so the 303 this route
            # always returns is fine left unfollowed; nothing here needs the
            # HTML page it redirects to.
            client.post(BRIEFING_PATH).raise_for_status()
            briefing = client.get(BRIEFING_PATH).raise_for_status().json()
            print("=== BRIEFING ===")
            print(briefing["briefing"])
            print("\n=== OPERATOR ===")
            print(INSTRUCTION)
            answer = client.post(
                ASK_PATH, json={"question": INSTRUCTION, "thread_id": briefing["thread_id"]}
            ).raise_for_status().json()
        except httpx.HTTPError as error:
            print(f"cannot reach GAIA at {client.base_url}: {error}")
            return 1
    print("\n=== TOOL TRACE ===")
    for line in answer["trace"]:
        print(f"  {line}")
    print("\n=== GAIA ===")
    print(answer["answer"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
