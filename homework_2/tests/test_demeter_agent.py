"""DEMETER tools are pure functions over static biome data; no LLM involved."""

from agents import demeter_agent


def test_plan_scales_machine_hours_with_sector_size() -> None:
    plan = demeter_agent.plan_reseeding("Sacred Lands", "temperate forest")
    biome = demeter_agent.BIOME_PLANS["temperate forest"]

    assert plan["hectares"] == demeter_agent.SECTOR_FLORA["sacred lands"]["hectares"]
    assert plan["grazer_hours"] == plan["hectares"] * biome["grazer_hours_per_hectare"]
    assert plan["scrapper_hours"] == plan["hectares"] * biome["scrapper_hours_per_hectare"]
    assert plan["species"] == biome["species"]


def test_lookup_is_case_and_article_insensitive() -> None:
    assert demeter_agent.plan_reseeding("THE CUT", "Grassland") == demeter_agent.plan_reseeding("cut", "grassland")


def test_unknown_sector_and_biome_are_reported_not_raised() -> None:
    assert "known_sectors" in demeter_agent.plan_reseeding("Mordor", "grassland")
    assert "known_biomes" in demeter_agent.plan_reseeding("Sundom", "jungle")


def test_spec_matches_registry_contract() -> None:
    assert demeter_agent.SPEC.agent_id == "demeter"
    assert [skill["id"] for skill in demeter_agent.SPEC.skills] == ["reseeding-plan"]


def test_a_healthy_sector_needs_no_containment() -> None:
    assert demeter_agent.plan_containment("Sundom")["scrapper_hours"] == 0


def test_containment_hours_scale_with_blight_and_sector_size() -> None:
    demeter_agent.record_blight_outbreak("Cut", 3)

    plan = demeter_agent.plan_containment("Cut")
    hectares = demeter_agent.SECTOR_FLORA["cut"]["hectares"]

    assert plan["blight_severity"] == 3
    assert plan["scrapper_hours"] == hectares * 3 * demeter_agent.SCRAPPER_HOURS_PER_BLIGHT_LEVEL_PER_HECTARE


def test_containment_plan_says_demeter_cannot_supply_the_machines() -> None:
    demeter_agent.record_blight_outbreak("Cut", 2)

    assert "HEPHAESTUS" in demeter_agent.plan_containment("Cut")["note"]


def test_blight_severity_is_capped() -> None:
    demeter_agent.record_blight_outbreak("Cut", 99)

    assert demeter_agent.SECTOR_FLORA["cut"]["blight_severity"] == 5


def test_quarantine_trades_coverage_for_one_level_of_blight() -> None:
    demeter_agent.record_blight_outbreak("Cut", 3)
    coverage_before = demeter_agent.SECTOR_FLORA["cut"]["coverage_pct"]

    result = demeter_agent.quarantine_sector("Cut")

    assert result["blight_severity"] == 2
    assert result["coverage_pct"] == coverage_before - demeter_agent.QUARANTINE_COVERAGE_COST_PCT


def test_quarantining_a_healthy_sector_changes_nothing() -> None:
    coverage_before = demeter_agent.SECTOR_FLORA["sundom"]["coverage_pct"]

    result = demeter_agent.quarantine_sector("Sundom")

    assert result["blight_severity"] == 0
    assert result["coverage_pct"] == coverage_before


def test_seedling_dieback_lowers_coverage() -> None:
    before = demeter_agent.SECTOR_FLORA["claim"]["coverage_pct"]

    assert demeter_agent.record_seedling_dieback("Claim", 5.0)["coverage_pct"] == before - 5.0


def test_situation_report_covers_every_sector() -> None:
    assert set(demeter_agent.situation_report()["sectors"]) == set(demeter_agent.SECTOR_FLORA)


def test_unknown_sector_is_reported_by_every_tool() -> None:
    assert "known_sectors" in demeter_agent.plan_containment("Mordor")
    assert "known_sectors" in demeter_agent.record_blight_outbreak("Mordor", 1)
