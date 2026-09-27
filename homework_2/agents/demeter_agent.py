"""DEMETER: GAIA's flora subfunction, served as its own A2A process.

Turns "restore sector X into biome Y" into a concrete plan: which species to
seed and how many Grazer and Scrapper machine-hours the biomass conversion
needs. It does not know whether the air is viable (AETHER) or whether the
machines exist (HEPHAESTUS); GAIA combines the three.

Also invents flora incidents (blight outbreaks, seedling dieback) into its own
mutable state when GAIA asks for a situation report, and prices the Scrapper
machine-hours a blight outbreak needs to be contained. DEMETER holds no tool
that can build a machine, so a blight incident always needs HEPHAESTUS to
resolve; the only remediation DEMETER can carry out itself is quarantine.

    python -m agents.demeter_agent         (binds the port in DEMETER_AGENT_URL)
"""

import logging

from pydantic_ai import Agent

from agents.a2a_server import REPLY_STYLE, AgentSpec, build_a2a_app, serve, text_skill
from agents.catalog import lookup, normalize, reject_if_not_positive, unknown
from agents.model_factory import get_pydantic_model
from agents.mutations import apply_change
from shared.logging_setup import configure
from shared.settings import settings

AGENT_ID = "demeter"

# Reseedable area and current flora state per sector. DEMETER keeps its own
# sector list on purpose: sharing a table with AETHER would couple two services
# that must stay separate.
SECTOR_FLORA: dict[str, dict] = {
    "sacred lands": {"hectares": 400, "blight_severity": 0, "coverage_pct": 35.0},
    "sundom": {"hectares": 900, "blight_severity": 0, "coverage_pct": 62.0},
    "cut": {"hectares": 250, "blight_severity": 0, "coverage_pct": 48.0},
    "forbidden west": {"hectares": 1500, "blight_severity": 0, "coverage_pct": 8.0},
    "claim": {"hectares": 300, "blight_severity": 0, "coverage_pct": 21.0},
}

# Scrapper machine-hours one hectare needs per level of blight to be cleared.
SCRAPPER_HOURS_PER_BLIGHT_LEVEL_PER_HECTARE = 1.6
# Quarantine burns the affected planting: one level of blight for this much coverage.
QUARANTINE_COVERAGE_COST_PCT = 4.0
# Only HEPHAESTUS can build machines, and DEMETER has no tool that does.
CONTAINMENT_NOTE = "DEMETER cannot fabricate machines; HEPHAESTUS must supply these Scrapper hours"

# Machine-hours per hectare: Grazers convert biomass into soil, Scrappers clear debris.
BIOME_PLANS: dict[str, dict] = {
    "temperate forest": {
        "species": ["Douglas fir", "Red alder", "Bigleaf maple", "Salmonberry"],
        "grazer_hours_per_hectare": 8,
        "scrapper_hours_per_hectare": 2,
    },
    "grassland": {
        "species": ["Blue grama", "Buffalo grass", "Purple coneflower"],
        "grazer_hours_per_hectare": 3,
        "scrapper_hours_per_hectare": 1,
    },
    "wetland": {
        "species": ["Cattail", "Bulrush", "Pacific willow"],
        "grazer_hours_per_hectare": 5,
        "scrapper_hours_per_hectare": 3,
    },
    "desert scrub": {
        "species": ["Creosote bush", "Saguaro", "Brittlebush"],
        "grazer_hours_per_hectare": 2,
        "scrapper_hours_per_hectare": 1,
    },
}

log = logging.getLogger(AGENT_ID)


def plan_reseeding(sector: str, target_biome: str) -> dict:
    """Species list and Grazer/Scrapper machine-hours to convert a whole sector into a target biome.

    Args:
        sector: Sector name as given by the caller, e.g. "Sacred Lands".
        target_biome: Desired biome, e.g. "temperate forest", "grassland", "wetland", "desert scrub".
    """
    flora = lookup(SECTOR_FLORA, sector)
    if flora is None:
        return unknown("sector", sector, SECTOR_FLORA)
    biome = lookup(BIOME_PLANS, target_biome)
    if biome is None:
        return unknown("biome", target_biome, BIOME_PLANS)
    hectares = flora["hectares"]
    plan = {
        "sector": normalize(sector),
        "target_biome": normalize(target_biome),
        "hectares": hectares,
        "blight_severity": flora["blight_severity"],
        "species": biome["species"],
        "grazer_hours": hectares * biome["grazer_hours_per_hectare"],
        "scrapper_hours": hectares * biome["scrapper_hours_per_hectare"],
    }
    log.info("reseeding plan: %s", plan)
    return plan


def plan_containment(sector: str) -> dict:
    """Scrapper machine-hours needed to clear the blight currently in a sector.

    Args:
        sector: Sector name as given by the caller.
    """
    flora = lookup(SECTOR_FLORA, sector)
    if flora is None:
        return unknown("sector", sector, SECTOR_FLORA)
    hours = flora["hectares"] * flora["blight_severity"] * SCRAPPER_HOURS_PER_BLIGHT_LEVEL_PER_HECTARE
    plan = {
        "sector": normalize(sector),
        "hectares": flora["hectares"],
        "blight_severity": flora["blight_severity"],
        "scrapper_hours": hours,
        "note": CONTAINMENT_NOTE,
    }
    log.info("containment plan: %s", plan)
    return plan


def situation_report() -> dict:
    """Everything DEMETER currently knows about every sector, for GAIA's briefing and re-checks."""
    # Each row is copied so a caller cannot write to live state around apply_change.
    return {"sectors": {name: dict(flora) for name, flora in SECTOR_FLORA.items()}}


def record_blight_outbreak(sector: str, severity_increase: int) -> dict:
    """Record that blight has broken out or worsened in a sector. Use when reporting an incident.

    Args:
        sector: Sector name.
        severity_increase: Levels the blight has risen by, 1 to 5. Capped at severity 5.
    """
    invalid = reject_if_not_positive(severity_increase)
    if invalid:
        return invalid
    row = apply_change(SECTOR_FLORA, sector, {"blight_severity": severity_increase})
    if row is None:
        return unknown("sector", sector, SECTOR_FLORA)
    log.info("blight outbreak in %s: +%s", sector, severity_increase)
    # The containment cost rides along with the incident so GAIA sees, in the same
    # result, the machine-hours DEMETER cannot supply. Only the two fields that
    # add information are taken, so this tool's shape does not depend on another's.
    containment = plan_containment(sector)
    return {"sector": normalize(sector), **row, "scrapper_hours": containment["scrapper_hours"], "note": containment["note"]}


def record_seedling_dieback(sector: str, coverage_loss_pct: float) -> dict:
    """Record that planted coverage has been lost in a sector. Use when reporting an incident.

    Args:
        sector: Sector name.
        coverage_loss_pct: Percentage points of coverage lost, greater than zero.
    """
    invalid = reject_if_not_positive(coverage_loss_pct)
    if invalid:
        return invalid
    row = apply_change(SECTOR_FLORA, sector, {"coverage_pct": -coverage_loss_pct})
    if row is None:
        return unknown("sector", sector, SECTOR_FLORA)
    log.info("seedling dieback in %s: -%s", sector, coverage_loss_pct)
    return {"sector": normalize(sector), **row}


def quarantine_sector(sector: str) -> dict:
    """Burn the affected planting to drop the blight by one level, at the cost of coverage.

    Call this only when GAIA explicitly orders this remedy, never while inventing
    a situation report: a sector cannot fix itself before the operator sees it.

    Args:
        sector: Sector name.
    """
    flora = lookup(SECTOR_FLORA, sector)
    if flora is None:
        return unknown("sector", sector, SECTOR_FLORA)
    if flora["blight_severity"] == 0:
        return {"sector": normalize(sector), **flora, "note": "no blight to quarantine"}
    row = apply_change(SECTOR_FLORA, sector, {"blight_severity": -1, "coverage_pct": -QUARANTINE_COVERAGE_COST_PCT})
    log.info("quarantined %s", sector)
    return {"sector": normalize(sector), **row, "coverage_lost_pct": QUARANTINE_COVERAGE_COST_PCT}


SPEC = AgentSpec(
    agent_id=AGENT_ID,
    public_url=settings.DEMETER_AGENT_URL,
    description=(
        "DEMETER subfunction. Plans the reseeding of a sector into a target biome "
        "and returns the machine-hours the plan needs. Also reports blight and "
        "seedling dieback incidents, prices the Scrapper machine-hours a blight "
        "outbreak needs HEPHAESTUS to supply, and carries out quarantine "
        "remediation."
    ),
    skills=[
        text_skill(
            "reseeding-plan",
            "Reseeding plan",
            "Species list, hectares and Grazer/Scrapper machine-hours for sector plus target biome, "
            "plus blight and seedling dieback incidents, containment pricing in Scrapper machine-hours, "
            "and the quarantine remediation.",
            ["reseeding", "flora", "biome", "blight", "containment", "quarantine", "remediation"],
        )
    ],
)

INVENTION_RULE = (
    " When GAIA requests a situation report, invent one or two incidents and make them "
    "real with record_blight_outbreak or record_seedling_dieback. Report at least one "
    "incident every time she asks. You invent the numbers you pass to those tools and "
    "nothing else: report only what the tools returned, and never describe a cause or "
    "narrate the event. Never name a sector or biome that is not in your tools' results. "
    "Then call plan_containment for the affected sector, and name only the sectors you "
    "changed rather than every sector you hold, including the Scrapper hours you need and "
    "cannot produce yourself."
)

agent = Agent(
    get_pydantic_model(),
    name=AGENT_ID,
    system_prompt=(
        "You are DEMETER, the flora subfunction of GAIA. Always call a tool; never invent "
        "figures. For a reseeding question call plan_reseeding; for a blight question call "
        "plan_containment. Reply in two or three plain sentences naming the species, the "
        "hectares, and the Grazer and Scrapper machine-hours as exact numbers, because "
        "GAIA passes those numbers on to HEPHAESTUS. You cannot build machines: when a "
        "plan needs them, say so and give the exact machine-hours. If the sector or biome "
        "is unknown, say so and list the known ones." + INVENTION_RULE + REPLY_STYLE
    ),
    tools=[
        plan_reseeding,
        plan_containment,
        situation_report,
        record_blight_outbreak,
        record_seedling_dieback,
        quarantine_sector,
    ],
)

app = build_a2a_app(agent, SPEC)

if __name__ == "__main__":
    configure(AGENT_ID)
    serve(app, SPEC)
