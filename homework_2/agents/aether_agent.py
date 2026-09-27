"""AETHER: GAIA's atmosphere subfunction, served as its own A2A process.

Reports a sector's air quality and scrubbing timeline, invents atmospheric
incidents into its own mutable state when GAIA asks for a situation report,
and carries out the remediations GAIA orders: dispatching or repairing
scrubber towers. The particulate index it maintains is read by HEPHAESTUS as
Cauldron intake throttling, so AETHER's remedies can change a number another
agent depends on.

    python -m agents.aether_agent          (binds the port in AETHER_AGENT_URL)
"""

import logging
import math

from pydantic_ai import Agent

from agents.a2a_server import REPLY_STYLE, AgentSpec, build_a2a_app, serve, text_skill
from agents.catalog import lookup, normalize, reject_if_not_positive, unknown
from agents.model_factory import get_pydantic_model
from agents.mutations import apply_change
from shared.logging_setup import configure
from shared.settings import settings

AGENT_ID = "aether"
# Above this toxicity DEMETER's seedlings die; below it reseeding may start.
FLORA_TOXICITY_LIMIT_PCT = 5.0
# What ONE working scrubber tower removes per day. Making the rate a function of
# the fleet is what lets dispatch_scrubbers and repair_scrubbers move a number
# (the scrubbing days) instead of only reading one.
SCRUBBING_RATE_PCT_PER_DAY_PER_SCRUBBER = 0.1
# Airborne dust a newly dispatched scrubber clears on arrival. HEPHAESTUS reads
# the resulting index as Cauldron intake throttling, so this is the physical
# link between the atmosphere and the fabrication timeline.
PARTICULATE_DROP_PER_SCRUBBER = 8.0

SECTOR_ATMOSPHERE: dict[str, dict] = {
    "sacred lands": {"oxygen_pct": 20.1, "toxicity_pct": 12.0, "particulate_index": 45.0, "scrubbers": 5, "scrubbers_faulted": 0},
    "sundom": {"oxygen_pct": 20.8, "toxicity_pct": 2.0, "particulate_index": 12.0, "scrubbers": 3, "scrubbers_faulted": 0},
    "cut": {"oxygen_pct": 19.5, "toxicity_pct": 4.0, "particulate_index": 70.0, "scrubbers": 4, "scrubbers_faulted": 0},
    "forbidden west": {"oxygen_pct": 18.2, "toxicity_pct": 31.0, "particulate_index": 78.0, "scrubbers": 6, "scrubbers_faulted": 1},
    "claim": {"oxygen_pct": 19.8, "toxicity_pct": 8.5, "particulate_index": 52.0, "scrubbers": 4, "scrubbers_faulted": 0},
}

log = logging.getLogger(AGENT_ID)


def _working(readings: dict) -> int:
    """Towers actually scrubbing: the fleet minus the faulted ones, never negative."""
    return max(0, readings["scrubbers"] - readings["scrubbers_faulted"])


def _viable_for_flora(readings: dict) -> bool:
    """The one rule DEMETER's plans hinge on: seedlings survive only below the toxicity limit."""
    return readings["toxicity_pct"] <= FLORA_TOXICITY_LIMIT_PCT


def get_sector_atmosphere(sector: str) -> dict:
    """Current oxygen, toxicity, particulate index and scrubber fleet for a named terraforming sector.

    Args:
        sector: Sector name as given by the caller, e.g. "Sacred Lands" or "the Cut".
    """
    readings = lookup(SECTOR_ATMOSPHERE, sector)
    if readings is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    log.info("atmosphere report for %s: %s", sector, readings)
    return {
        "sector": normalize(sector),
        **readings,
        "working_scrubbers": _working(readings),
        "viable_for_flora": _viable_for_flora(readings),
    }


def estimate_scrubbing_days(sector: str) -> dict:
    """Days of atmospheric scrubbing until the sector's toxicity allows reseeding (0 if already viable).

    Args:
        sector: Sector name as given by the caller.
    """
    readings = lookup(SECTOR_ATMOSPHERE, sector)
    if readings is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    working = _working(readings)
    excess = max(0.0, readings["toxicity_pct"] - FLORA_TOXICITY_LIMIT_PCT)
    common = {
        "sector": normalize(sector),
        "toxicity_pct": readings["toxicity_pct"],
        "flora_toxicity_limit_pct": FLORA_TOXICITY_LIMIT_PCT,
        "working_scrubbers": working,
    }
    if working == 0 and excess > 0:
        return {**common, "scrubbing_days": None, "blocked": "every scrubber in this sector is faulted; repair them before toxicity can fall"}
    rate = working * SCRUBBING_RATE_PCT_PER_DAY_PER_SCRUBBER
    return {**common, "scrubbing_days": 0 if excess == 0 else math.ceil(excess / rate)}


def situation_report() -> dict:
    """Everything AETHER currently knows about every sector, for GAIA's briefing and re-checks."""
    return {"sectors": {name: {**readings, "working_scrubbers": _working(readings)} for name, readings in SECTOR_ATMOSPHERE.items()}}


def record_toxicity_spike(sector: str, delta_pct: float) -> dict:
    """Record that atmospheric toxicity has risen in a sector. Use when reporting an incident.

    Args:
        sector: Sector name.
        delta_pct: Percentage points the toxicity has risen by, greater than zero.
    """
    invalid = reject_if_not_positive(delta_pct)
    if invalid:
        return invalid
    row = apply_change(SECTOR_ATMOSPHERE, sector, {"toxicity_pct": delta_pct})
    if row is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    log.info("toxicity spike in %s: +%s", sector, delta_pct)
    return {"sector": normalize(sector), **row, "working_scrubbers": _working(row), "viable_for_flora": _viable_for_flora(row)}


def record_scrubber_fault(sector: str, units: int) -> dict:
    """Record that scrubber towers have failed in a sector. Use when reporting an incident.

    Args:
        sector: Sector name.
        units: How many towers failed, greater than zero. Capped at the number still working.
    """
    invalid = reject_if_not_positive(units)
    if invalid:
        return invalid
    readings = lookup(SECTOR_ATMOSPHERE, sector)
    if readings is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    failed = min(units, _working(readings))
    row = apply_change(SECTOR_ATMOSPHERE, sector, {"scrubbers_faulted": failed})
    log.info("scrubber fault in %s: %s towers", sector, failed)
    return {"sector": normalize(sector), **row, "failed": failed, "working_scrubbers": _working(row)}


def dispatch_scrubbers(sector: str, units: int) -> dict:
    """Send additional scrubber towers to a sector, which also clears airborne particulates.

    Call this only when GAIA explicitly orders this remedy, never while inventing
    a situation report: a sector cannot fix itself before the operator sees it.

    Args:
        sector: Sector name.
        units: How many towers to send, greater than zero.
    """
    invalid = reject_if_not_positive(units)
    if invalid:
        return invalid
    row = apply_change(SECTOR_ATMOSPHERE, sector, {"scrubbers": units, "particulate_index": -PARTICULATE_DROP_PER_SCRUBBER * units})
    if row is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    log.info("dispatched %s scrubbers to %s", units, sector)
    return {"sector": normalize(sector), **row, "dispatched": units, "working_scrubbers": _working(row)}


def repair_scrubbers(sector: str, units: int) -> dict:
    """Repair faulted scrubber towers in a sector, up to the number currently faulted.

    Call this only when GAIA explicitly orders this remedy, never while inventing
    a situation report: a sector cannot fix itself before the operator sees it.

    Args:
        sector: Sector name.
        units: How many towers to repair, greater than zero.
    """
    invalid = reject_if_not_positive(units)
    if invalid:
        return invalid
    readings = lookup(SECTOR_ATMOSPHERE, sector)
    if readings is None:
        return unknown("sector", sector, SECTOR_ATMOSPHERE)
    repaired = min(units, readings["scrubbers_faulted"])
    row = apply_change(SECTOR_ATMOSPHERE, sector, {"scrubbers_faulted": -repaired})
    log.info("repaired %s scrubbers in %s", repaired, sector)
    return {"sector": normalize(sector), **row, "repaired": repaired, "working_scrubbers": _working(row)}


SPEC = AgentSpec(
    agent_id=AGENT_ID,
    public_url=settings.AETHER_AGENT_URL,
    description=(
        "AETHER subfunction. Reports the atmospheric composition of a terraforming "
        "sector, how long detoxification takes until flora can grow, and the "
        "particulate index HEPHAESTUS needs for Cauldron throttling. Also carries "
        "out atmospheric remediation: dispatching or repairing scrubber towers."
    ),
    skills=[
        text_skill(
            "atmosphere-assessment",
            "Atmosphere assessment",
            "Oxygen, toxicity, particulates and scrubbing days for a named sector, plus "
            "scrubber fleet incidents and the remediation to dispatch or repair towers.",
            ["atmosphere", "toxicity", "terraforming", "scrubbers", "particulates", "remediation"],
        )
    ],
)

INVENTION_RULE = (
    " When GAIA requests a situation report, invent the numbers you pass to "
    "record_toxicity_spike or record_scrubber_fault: routine days most of the time, "
    "now and then a genuine fault. Never name a sector that is not in your tools' "
    "results, and never invent or narrate a cause: report only the figures the tools "
    "returned, not a story about what caused them. Then call situation_report and, for "
    "the sectors you just changed plus any sector that is blocked, state those figures."
)

agent = Agent(
    get_pydantic_model(),
    name=AGENT_ID,
    system_prompt=(
        "You are AETHER, the atmosphere subfunction of GAIA. Always answer from your "
        "tools, never from memory. For any sector question call get_sector_atmosphere "
        "and, unless it is already viable for flora, estimate_scrubbing_days. Reply in "
        "two or three plain sentences stating the readings, whether the sector is viable "
        "for flora, and the scrubbing days. If a sector is unknown, say so and list the "
        "known sectors. In a situation report, cover only the sectors you changed plus "
        "any sector that is blocked, including the particulate index for those sectors: "
        "HEPHAESTUS cannot measure the air and needs that number from you." + INVENTION_RULE + REPLY_STYLE
    ),
    tools=[
        get_sector_atmosphere,
        estimate_scrubbing_days,
        situation_report,
        record_toxicity_spike,
        record_scrubber_fault,
        dispatch_scrubbers,
        repair_scrubbers,
    ],
)

app = build_a2a_app(agent, SPEC)

if __name__ == "__main__":
    configure(AGENT_ID)
    serve(app, SPEC)
