"""HEPHAESTUS tools are pure functions over static Cauldron data; no LLM involved."""

from agents import hephaestus_agent

CLEAN_AIR = 0.0


def test_inventory_reports_cauldron_and_units() -> None:
    inventory = hephaestus_agent.check_cauldron_inventory("Grazer")
    expected = hephaestus_agent.CAULDRONS["grazer"]

    assert inventory["cauldron"] == expected["cauldron"]
    assert inventory["available"] == expected["available"]
    assert inventory["in_fabrication"] == expected["in_fabrication"]


def test_workload_within_available_units_needs_no_fabrication() -> None:
    available = hephaestus_agent.CAULDRONS["grazer"]["available"]
    small_workload = available * hephaestus_agent.MACHINE_HOURS_PER_DAY  # one day for the existing fleet

    estimate = hephaestus_agent.estimate_fabrication_time("grazer", small_workload, CLEAN_AIR)

    assert estimate["shortfall"] == 0
    assert estimate["fabrication_days"] == 0
    assert estimate["total_days"] == estimate["work_days"]


def test_large_workload_requires_fabrication_before_work() -> None:
    estimate = hephaestus_agent.estimate_fabrication_time("grazer", 3200, CLEAN_AIR)
    cauldron = hephaestus_agent.CAULDRONS["grazer"]
    units_needed = -(-3200 // (hephaestus_agent.MACHINE_HOURS_PER_DAY * hephaestus_agent.TARGET_WORK_DAYS))
    shortfall = units_needed - cauldron["available"]
    batches = -(-shortfall // cauldron["batch_size"])

    assert estimate["units_needed"] == units_needed
    assert estimate["shortfall"] == shortfall
    assert estimate["fabrication_days"] == batches * cauldron["fabrication_days_per_batch"]
    assert estimate["total_days"] == estimate["fabrication_days"] + estimate["work_days"]


def test_unknown_machine_type_lists_known_types() -> None:
    result = hephaestus_agent.check_cauldron_inventory("Thunderjaw")

    assert "unknown machine type" in result["error"]
    assert "grazer" in result["known_machine_types"]


def test_spec_matches_registry_contract() -> None:
    assert hephaestus_agent.SPEC.agent_id == "hephaestus"
    assert [skill["id"] for skill in hephaestus_agent.SPEC.skills] == ["machine-fabrication"]


DIRTY_AIR = 70.0


def test_dirty_air_lengthens_fabrication_but_not_the_work_itself() -> None:
    clean = hephaestus_agent.estimate_fabrication_time("grazer", 3200, CLEAN_AIR)
    dirty = hephaestus_agent.estimate_fabrication_time("grazer", 3200, DIRTY_AIR)

    # The Cut's index of 70 against a threshold of 30 is the demo's 40 percent throttle.
    assert dirty["throttle_pct"] == 40.0
    assert clean["throttle_pct"] == 0.0
    assert dirty["fabrication_days"] > clean["fabrication_days"]
    assert dirty["work_days"] == clean["work_days"]


def test_air_below_the_threshold_does_not_throttle() -> None:
    assert hephaestus_agent.estimate_fabrication_time("grazer", 3200, hephaestus_agent.PARTICULATE_THROTTLE_THRESHOLD)["throttle_pct"] == 0.0


def test_a_cauldron_fault_removes_units_and_adds_standing_throttle() -> None:
    before = hephaestus_agent.CAULDRONS["scrapper"]["available"]

    result = hephaestus_agent.record_cauldron_fault("Scrapper", 2)

    assert result["available"] == before - 2
    assert result["throttle_pct"] == hephaestus_agent.FAULT_THROTTLE_PCT


def test_a_cauldron_fault_is_capped_at_the_units_available() -> None:
    available = hephaestus_agent.CAULDRONS["scrapper"]["available"]

    result = hephaestus_agent.record_cauldron_fault("Scrapper", available + 50)

    assert result["lost"] == available
    assert result["available"] == 0


def test_repairing_a_cauldron_clears_its_standing_throttle() -> None:
    hephaestus_agent.record_cauldron_fault("Scrapper", 1)

    assert hephaestus_agent.repair_cauldron("Scrapper")["throttle_pct"] == 0.0


def test_prioritising_fabrication_lands_the_in_flight_batch_now() -> None:
    cauldron = hephaestus_agent.CAULDRONS["grazer"]
    expected = cauldron["available"] + cauldron["in_fabrication"]

    result = hephaestus_agent.prioritize_fabrication("Grazer")

    assert result["available"] == expected
    assert result["in_fabrication"] == 0


def test_a_batch_delay_lengthens_fabrication() -> None:
    before = hephaestus_agent.estimate_fabrication_time("grazer", 3200, CLEAN_AIR)["fabrication_days"]

    hephaestus_agent.record_batch_delay("Grazer", 2)

    assert hephaestus_agent.estimate_fabrication_time("grazer", 3200, CLEAN_AIR)["fabrication_days"] > before


def test_non_positive_workload_is_rejected() -> None:
    assert "error" in hephaestus_agent.estimate_fabrication_time("grazer", 0, CLEAN_AIR)


def test_situation_report_covers_every_cauldron() -> None:
    assert set(hephaestus_agent.situation_report()["cauldrons"]) == set(hephaestus_agent.CAULDRONS)


def test_unknown_machine_type_is_reported_by_every_tool() -> None:
    assert "known_machine_types" in hephaestus_agent.record_cauldron_fault("Thunderjaw", 1)
    assert "known_machine_types" in hephaestus_agent.prioritize_fabrication("Thunderjaw")
