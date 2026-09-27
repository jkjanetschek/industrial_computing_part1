"""Tiny input helpers shared by the sub-agents' tools.

Every agent keeps its own static table (sectors, biomes, Cauldrons); what they
share is only HOW a free-text name from the LLM is matched against a table and
how bad input is reported. A miss or an invalid number is returned as data,
never raised: the agent's LLM turns the ``known_*`` list or the error text into
a helpful answer instead of crashing the task.
"""

from typing import Any

LEADING_ARTICLE = "the "
NOT_POSITIVE_ERROR = {"error": "value must be greater than zero"}


def normalize(name: str) -> str:
    """'The Sacred Lands' -> 'sacred lands', so table keys are lower case without article."""
    cleaned = " ".join(name.lower().split())
    if cleaned.startswith(LEADING_ARTICLE):
        cleaned = cleaned[len(LEADING_ARTICLE):]
    return cleaned


def lookup(table: dict[str, Any], name: str) -> Any | None:
    """The table row for a free-text name, or None when the name is not in the table."""
    return table.get(normalize(name))


def unknown(kind: str, name: str, table: dict[str, Any]) -> dict[str, Any]:
    """The tool result for a name that is not in ``table``."""
    return {"error": f"unknown {kind} '{name}'", f"known_{kind.replace(' ', '_')}s": sorted(table)}


def reject_if_not_positive(value: float) -> dict | None:
    """Guard clause for tools that take a positive number: the error result, or None if the value passed.

    Incident and remedy tools take deltas (units, days, percentage points) that
    the LLM invents. Zero or negative would either do nothing or silently undo
    another incident, so the tool answers with an error it can read instead.
    """
    if value <= 0:
        return dict(NOT_POSITIVE_ERROR)
    return None
