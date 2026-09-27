"""The one way a sub-agent's state table is written to.

Pattern: guarded write. Every incident and remediation tool goes through
``apply_change``, so name matching, unknown-row handling and bound clamping are
identical across the three agents. The agents still own their own tables: what
they share here is only HOW a row is changed, exactly as ``agents.catalog``
shares only how a row is read.

Changes are DELTAS, never absolute values, because incidents and remedies
accumulate: two scrubber faults and one repair have to compose without any tool
knowing what the others did.
"""

from typing import Any

from agents.catalog import normalize

# The unit range each writable field is measured in: a percentage runs 0 to
# 100, a unit count is a small non-negative integer, whichever agent happens to
# hold the field. This registry is shared because the units are shared, not
# because the agents are, and each agent still owns its own table. A field
# that is not listed here cannot be changed by any tool, which is what keeps
# an invented incident from rewriting a sector's size or a Cauldron's name.
#
# Field names live in one namespace here. If two agents ever need the same
# field name with a different range, the bounds move next to each agent's
# table and get passed into apply_change instead of read from this module.
BOUNDS: dict[str, tuple[float, float]] = {
    "toxicity_pct": (0.0, 100.0),
    "particulate_index": (0.0, 100.0),
    "scrubbers": (0, 99),
    "scrubbers_faulted": (0, 99),
    "blight_severity": (0, 5),
    "coverage_pct": (0.0, 100.0),
    "available": (0, 99),
    "in_fabrication": (0, 99),
    "throttle_pct": (0.0, 90.0),
    "fabrication_days_per_batch": (1, 60),
}


class UnwritableField(KeyError):
    """A tool tried to write a field that is not in BOUNDS. That is a bug, not user input."""


def clamp(field: str, value: float) -> float:
    low, high = BOUNDS[field]
    return min(high, max(low, value))


def apply_change(table: dict[str, Any], name: str, changes: dict[str, float]) -> dict[str, Any] | None:
    """Add each delta to the named row, clamped to its bounds. None if the row is unknown.

    Validation happens before the first write, so a rejected call leaves the row
    exactly as it was. A partial write would be worse than no write: the caller
    sees an exception and has no way to know how far the change got.
    """
    unwritable = [field for field in changes if field not in BOUNDS]
    if unwritable:
        raise UnwritableField(*unwritable)
    row = table.get(normalize(name))
    if row is None:
        return None
    for field, delta in changes.items():
        row[field] = clamp(field, row.get(field, 0) + delta)
    return row
