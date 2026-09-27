"""HEPHAESTUS: GAIA's machine-fabrication subfunction, served as its own A2A process.

Given a machine type, a workload in machine-hours and a particulate index,
reports what the Cauldrons have on hand and how long it takes to fabricate the
shortfall and then do the work. This is the last step of GAIA's answer:
without it the coordinator can say WHAT is needed but not WHEN it can be done.

Also invents fabrication incidents (Cauldron faults, batch delays) into its
own mutable state when GAIA asks for a situation report, and carries out the
remediations GAIA orders: prioritising an in-flight batch or repairing a
faulted Cauldron. HEPHAESTUS holds no tool that can measure the air: the
particulate index that throttles a Cauldron's output must come from AETHER,
so a dirty sector always needs AETHER to resolve, not HEPHAESTUS alone.

    python -m agents.hephaestus_agent      (binds the port in HEPHAESTUS_AGENT_URL)
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

AGENT_ID = "hephaestus"
# A deployed machine works this many hours per day (the rest is recharge).
MACHINE_HOURS_PER_DAY = 20
# HEPHAESTUS sizes a fleet so the work itself finishes within this window.
TARGET_WORK_DAYS = 10
# Particulate index a Cauldron's intake filters tolerate before output falls off.
PARTICULATE_THROTTLE_THRESHOLD = 30.0
# Throttle percent added per index point above the threshold.
THROTTLE_PCT_PER_INDEX_POINT = 1.0
# A Cauldron never stops entirely, however filthy the air.
MAX_THROTTLE_PCT = 90.0
# Standing throttle a fault leaves behind until the Cauldron is repaired.
FAULT_THROTTLE_PCT = 15.0

# One Cauldron per machine type; a batch is what one fabrication cycle yields.
CAULDRONS: dict[str, dict] = {
    "grazer": {"cauldron": "SIGMA", "available": 4, "in_fabrication": 1, "batch_size": 2, "fabrication_days_per_batch": 3, "throttle_pct": 0.0},
    "scrapper": {"cauldron": "RHO", "available": 6, "in_fabrication": 0, "batch_size": 3, "fabrication_days_per_batch": 2, "throttle_pct": 0.0},
    "lancehorn": {"cauldron": "XI", "available": 2, "in_fabrication": 2, "batch_size": 2, "fabrication_days_per_batch": 4, "throttle_pct": 0.0},
    "tallneck": {"cauldron": "ZETA", "available": 1, "in_fabrication": 0, "batch_size": 1, "fabrication_days_per_batch": 12, "throttle_pct": 0.0},
}

log = logging.getLogger(AGENT_ID)


def _throttle(particulate_index: float, standing_pct: float) -> float:
    """Airborne dust plus any standing fault throttle, as a percentage of lost output."""
    airborne = max(0.0, particulate_index - PARTICULATE_THROTTLE_THRESHOLD) * THROTTLE_PCT_PER_INDEX_POINT
    return min(MAX_THROTTLE_PCT, airborne + standing_pct)


def check_cauldron_inventory(machine_type: str) -> dict:
    """Which Cauldron builds a machine type and how many units are available or being fabricated.

    Args:
        machine_type: Machine type, e.g. "Grazer", "Scrapper", "Lancehorn", "Tallneck".
    """
    cauldron = lookup(CAULDRONS, machine_type)
    if cauldron is None:
        return unknown("machine type", machine_type, CAULDRONS)
    return {
        "machine_type": normalize(machine_type),
        "cauldron": cauldron["cauldron"],
        "available": cauldron["available"],
        "in_fabrication": cauldron["in_fabrication"],
    }


def estimate_fabrication_time(machine_type: str, machine_hours: float, particulate_index: float) -> dict:
    """Units needed for a workload, the shortfall to fabricate, and days until the work is done.

    Args:
        machine_type: Machine type, e.g. "Grazer".
        machine_hours: Total machine-hours of work this type must perform.
        particulate_index: The sector's airborne particulate index, from AETHER. HEPHAESTUS
            cannot measure the air, so this must be supplied; Cauldron intake filters throttle
            output above the threshold.
    """
    cauldron = lookup(CAULDRONS, machine_type)
    if cauldron is None:
        return unknown("machine type", machine_type, CAULDRONS)
    invalid = reject_if_not_positive(machine_hours)
    if invalid:
        return invalid
    throttle_pct = _throttle(particulate_index, cauldron["throttle_pct"])
    units_needed = max(1, math.ceil(machine_hours / (MACHINE_HOURS_PER_DAY * TARGET_WORK_DAYS)))
    shortfall = max(0, units_needed - cauldron["available"])
    batches = math.ceil(shortfall / cauldron["batch_size"])
    clean_days = batches * cauldron["fabrication_days_per_batch"]
    fabrication_days = math.ceil(clean_days / (1 - throttle_pct / 100))
    work_days = math.ceil(machine_hours / (units_needed * MACHINE_HOURS_PER_DAY))
    estimate = {
        "machine_type": normalize(machine_type),
        "machine_hours": machine_hours,
        "particulate_index": particulate_index,
        "throttle_pct": throttle_pct,
        "units_needed": units_needed,
        "available": cauldron["available"],
        "shortfall": shortfall,
        "fabrication_days": fabrication_days,
        "work_days": work_days,
        "total_days": fabrication_days + work_days,
    }
    log.info("fabrication estimate: %s", estimate)
    return estimate


def situation_report() -> dict:
    """Everything HEPHAESTUS currently knows about every Cauldron, for GAIA's briefing and re-checks."""
    # Each row is copied so a caller cannot write to live state around apply_change.
    return {"cauldrons": {name: dict(cauldron) for name, cauldron in CAULDRONS.items()}}


def record_cauldron_fault(machine_type: str, units_lost: int) -> dict:
    """Record that a Cauldron has faulted, losing finished units and throttling output. Use when reporting an incident.

    Args:
        machine_type: Machine type the Cauldron builds, e.g. "Scrapper".
        units_lost: Finished units destroyed by the fault, greater than zero. Capped at the units available.
    """
    invalid = reject_if_not_positive(units_lost)
    if invalid:
        return invalid
    cauldron = lookup(CAULDRONS, machine_type)
    if cauldron is None:
        return unknown("machine type", machine_type, CAULDRONS)
    # Cap before writing and report the real loss, so the briefing never claims
    # more units were destroyed than the Cauldron held.
    lost = min(units_lost, cauldron["available"])
    row = apply_change(CAULDRONS, machine_type, {"available": -lost, "throttle_pct": FAULT_THROTTLE_PCT})
    log.info("cauldron fault for %s: -%s units", machine_type, lost)
    return {"machine_type": normalize(machine_type), **row, "lost": lost}


def record_batch_delay(machine_type: str, extra_days: int) -> dict:
    """Record that a Cauldron's fabrication cycle has slowed. Use when reporting an incident.

    Args:
        machine_type: Machine type the Cauldron builds.
        extra_days: Days added to each fabrication batch, greater than zero.
    """
    invalid = reject_if_not_positive(extra_days)
    if invalid:
        return invalid
    row = apply_change(CAULDRONS, machine_type, {"fabrication_days_per_batch": extra_days})
    if row is None:
        return unknown("machine type", machine_type, CAULDRONS)
    log.info("batch delay for %s: +%s days", machine_type, extra_days)
    return {"machine_type": normalize(machine_type), **row}


def prioritize_fabrication(machine_type: str) -> dict:
    """Land the Cauldron's in-flight batch immediately, moving it into available units.

    Call this only when GAIA explicitly orders this remedy, never while inventing
    a situation report: a Cauldron cannot fix itself before the operator sees it.

    Args:
        machine_type: Machine type to prioritise, e.g. "Scrapper".
    """
    cauldron = lookup(CAULDRONS, machine_type)
    if cauldron is None:
        return unknown("machine type", machine_type, CAULDRONS)
    landed = cauldron["in_fabrication"]
    row = apply_change(CAULDRONS, machine_type, {"available": landed, "in_fabrication": -landed})
    log.info("prioritised %s: %s units landed", machine_type, landed)
    return {"machine_type": normalize(machine_type), **row, "landed": landed}


def repair_cauldron(machine_type: str) -> dict:
    """Clear a Cauldron's standing fault throttle. Airborne particulates are AETHER's to fix.

    Call this only when GAIA explicitly orders this remedy, never while inventing
    a situation report: a Cauldron cannot fix itself before the operator sees it.

    Args:
        machine_type: Machine type whose Cauldron to repair.
    """
    cauldron = lookup(CAULDRONS, machine_type)
    if cauldron is None:
        return unknown("machine type", machine_type, CAULDRONS)
    row = apply_change(CAULDRONS, machine_type, {"throttle_pct": -cauldron["throttle_pct"]})
    log.info("repaired cauldron for %s", machine_type)
    return {"machine_type": normalize(machine_type), **row}


SPEC = AgentSpec(
    agent_id=AGENT_ID,
    public_url=settings.HEPHAESTUS_AGENT_URL,
    description=(
        "HEPHAESTUS subfunction. Checks Cauldron machine inventory and estimates how "
        "long fabricating and deploying machines for a workload takes, throttled by a "
        "particulate index that only AETHER can supply. Also reports Cauldron faults "
        "and batch delays, and carries out remediation: prioritising an in-flight batch "
        "or repairing a faulted Cauldron."
    ),
    skills=[
        text_skill(
            "machine-fabrication",
            "Machine fabrication",
            "Available units, shortfall and particulate-throttled fabrication days for a machine "
            "type, machine-hour workload and AETHER particulate index, plus Cauldron fault and "
            "batch delay incidents and the prioritisation and repair remediation.",
            ["fabrication", "machines", "cauldron", "throttle", "particulates", "fault", "prioritisation", "remediation"],
        )
    ],
)

INVENTION_RULE = (
    " When GAIA requests a situation report, invent one or two incidents and make them "
    "real with record_cauldron_fault or record_batch_delay. You invent the numbers you pass "
    "to those tools and nothing else: report only what the tools returned, and never "
    "describe a cause or narrate the event. Never name a machine type that is not in your "
    "tools' results. Then name only the Cauldrons you changed rather than every Cauldron "
    "you hold."
)

agent = Agent(
    get_pydantic_model(),
    name=AGENT_ID,
    system_prompt=(
        "You are HEPHAESTUS, the machine-fabrication subfunction of GAIA. Always answer "
        "from your tools. When given a machine type, machine-hours and a particulate index "
        "call estimate_fabrication_time; when only asked about stock call "
        "check_cauldron_inventory. You cannot measure the air: if no particulate index was "
        "given, say plainly that you need one from AETHER and do not guess. Reply in two or "
        "three plain sentences with the exact numbers: units needed, available, shortfall, "
        "throttle, fabrication days, work days and total days. If the machine type is "
        "unknown, say so and list the known types." + INVENTION_RULE + REPLY_STYLE
    ),
    tools=[
        check_cauldron_inventory,
        estimate_fabrication_time,
        situation_report,
        record_cauldron_fault,
        record_batch_delay,
        prioritize_fabrication,
        repair_cauldron,
    ],
)

app = build_a2a_app(agent, SPEC)

if __name__ == "__main__":
    configure(AGENT_ID)
    serve(app, SPEC)
