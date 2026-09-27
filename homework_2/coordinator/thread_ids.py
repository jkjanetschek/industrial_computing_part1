"""How a new conversation gets its id.

One tiny module rather than a helper in api.py or main.py, because both need
it and neither should import the other: main.py is an HTTP client of the API
and must not pull FastAPI and the agent stack into a terminal process.
"""

from uuid import uuid4

# uuid4 is 36 chars; a short prefix is unique enough for a conversation id and
# readable in a URL or a terminal prompt.
THREAD_ID_LENGTH = 8


def new_thread_id() -> str:
    """A fresh conversation id: the first characters of a random UUID."""
    return uuid4().hex[:THREAD_ID_LENGTH]
