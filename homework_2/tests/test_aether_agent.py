"""AETHER tools are pure functions over static sector data; no LLM involved."""

from agents import aether_agent


def test_sector_lookup_is_case_and_article_insensitive() -> None:
    assert aether_agent.get_sector_atmosphere("the Sacred Lands") == aether_agent.get_sector_atmosphere("sacred lands")


def test_toxic_sector_is_not_viable_for_flora() -> None:
    report = aether_agent.get_sector_atmosphere("Sacred Lands")

    assert report["toxicity_pct"] > aether_agent.FLORA_TOXICITY_LIMIT_PCT
    assert report["viable_for_flora"] is False


def test_clean_sector_is_viable_and_needs_no_scrubbing() -> None:
    assert aether_agent.get_sector_atmosphere("Sundom")["viable_for_flora"] is True
    assert aether_agent.estimate_scrubbing_days("Sundom")["scrubbing_days"] == 0


def test_scrubbing_days_cover_the_excess_toxicity() -> None:
    report = aether_agent.get_sector_atmosphere("Sacred Lands")
    excess = report["toxicity_pct"] - aether_agent.FLORA_TOXICITY_LIMIT_PCT
    rate = report["working_scrubbers"] * aether_agent.SCRUBBING_RATE_PCT_PER_DAY_PER_SCRUBBER
    expected_days = -(-excess // rate)  # ceil

    assert aether_agent.estimate_scrubbing_days("Sacred Lands")["scrubbing_days"] == expected_days


def test_unknown_sector_lists_known_sectors_instead_of_raising() -> None:
    result = aether_agent.get_sector_atmosphere("Mordor")

    assert "unknown sector" in result["error"]
    assert "sacred lands" in result["known_sectors"]


def test_spec_matches_registry_contract() -> None:
    assert aether_agent.SPEC.agent_id == "aether"
    assert [skill["id"] for skill in aether_agent.SPEC.skills] == ["atmosphere-assessment"]


def test_scrubbing_slows_when_scrubbers_fault() -> None:
    before = aether_agent.estimate_scrubbing_days("Sacred Lands")["scrubbing_days"]

    aether_agent.record_scrubber_fault("Sacred Lands", 2)

    assert aether_agent.estimate_scrubbing_days("Sacred Lands")["scrubbing_days"] > before


def test_a_sector_with_every_scrubber_faulted_reports_itself_blocked() -> None:
    aether_agent.record_scrubber_fault("Sacred Lands", 99)

    result = aether_agent.estimate_scrubbing_days("Sacred Lands")

    assert result["working_scrubbers"] == 0
    assert result["scrubbing_days"] is None
    assert "repair" in result["blocked"]


def test_repairing_scrubbers_cannot_exceed_the_faulted_count() -> None:
    aether_agent.record_scrubber_fault("Cut", 1)

    repaired = aether_agent.repair_scrubbers("Cut", 10)

    assert repaired["scrubbers_faulted"] == 0
    assert repaired["repaired"] == 1


def test_dispatching_scrubbers_also_clears_particulates() -> None:
    before = aether_agent.get_sector_atmosphere("Cut")["particulate_index"]
    units = 2

    after = aether_agent.dispatch_scrubbers("Cut", units)

    assert after["scrubbers"] == aether_agent.SECTOR_ATMOSPHERE["cut"]["scrubbers"]
    assert after["particulate_index"] == before - aether_agent.PARTICULATE_DROP_PER_SCRUBBER * units


def test_toxicity_spike_raises_toxicity_and_can_end_viability() -> None:
    before = aether_agent.get_sector_atmosphere("Sundom")["toxicity_pct"]
    delta_pct = 6.0

    result = aether_agent.record_toxicity_spike("Sundom", delta_pct)

    assert result["toxicity_pct"] == before + delta_pct
    assert aether_agent.get_sector_atmosphere("Sundom")["viable_for_flora"] is False


def test_incident_and_remediation_tools_reject_non_positive_units() -> None:
    assert "error" in aether_agent.dispatch_scrubbers("Cut", 0)
    assert "error" in aether_agent.record_scrubber_fault("Cut", -1)


def test_situation_report_covers_every_sector() -> None:
    report = aether_agent.situation_report()

    assert set(report["sectors"]) == set(aether_agent.SECTOR_ATMOSPHERE)
    for row in report["sectors"].values():
        assert "working_scrubbers" in row
        assert "particulate_index" in row


def test_unknown_sector_is_reported_by_every_tool() -> None:
    assert "known_sectors" in aether_agent.dispatch_scrubbers("Mordor", 1)
    assert "known_sectors" in aether_agent.record_toxicity_spike("Mordor", 1.0)
    assert "known_sectors" in aether_agent.record_scrubber_fault("Mordor", 1)
    assert "known_sectors" in aether_agent.repair_scrubbers("Mordor", 1)
