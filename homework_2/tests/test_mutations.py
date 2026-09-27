"""The one write path every incident and remediation tool goes through."""

import pytest

from agents import mutations


def table() -> dict[str, dict]:
    return {"cut": {"toxicity_pct": 4.0, "blight_severity": 0}}


def test_apply_change_adds_deltas_and_returns_the_updated_row() -> None:
    row = mutations.apply_change(table(), "Cut", {"toxicity_pct": 2.5, "blight_severity": 3})

    assert row == {"toxicity_pct": 6.5, "blight_severity": 3}


def test_apply_change_matches_names_like_the_read_path() -> None:
    assert mutations.apply_change(table(), "the CUT", {"blight_severity": 1})["blight_severity"] == 1


def test_apply_change_clamps_to_the_declared_bounds() -> None:
    row = mutations.apply_change(table(), "cut", {"blight_severity": 99, "toxicity_pct": -100.0})

    assert row["blight_severity"] == mutations.BOUNDS["blight_severity"][1]
    assert row["toxicity_pct"] == 0.0


def test_apply_change_returns_none_for_an_unknown_row() -> None:
    assert mutations.apply_change(table(), "Mordor", {"blight_severity": 1}) is None


def test_apply_change_rejects_a_field_no_tool_may_write() -> None:
    with pytest.raises(mutations.UnwritableField):
        mutations.apply_change(table(), "cut", {"hectares": 10})


def test_apply_change_leaves_the_row_untouched_when_one_field_is_unwritable() -> None:
    row = table()["cut"]

    with pytest.raises(mutations.UnwritableField):
        mutations.apply_change({"cut": row}, "cut", {"toxicity_pct": 5.0, "hectares": 10})

    assert row["toxicity_pct"] == 4.0


def test_apply_change_keeps_an_integer_bounded_field_as_an_int() -> None:
    row = mutations.apply_change(table(), "cut", {"blight_severity": 2})

    assert row["blight_severity"] == 2
    assert isinstance(row["blight_severity"], int)


def test_apply_change_composes_deltas_across_separate_calls() -> None:
    data = table()

    mutations.apply_change(data, "cut", {"toxicity_pct": 2.0})
    row = mutations.apply_change(data, "cut", {"toxicity_pct": 1.5})

    assert row["toxicity_pct"] == 7.5


# These two tests deliberately break the "tests must be independent" rule: they
# only prove anything about the reset_agent_state fixture (tests/conftest.py) when
# they run in this file order, which pytest does by default. Part one dirties the
# module-level table directly, bypassing apply_change, so the assertion in part two
# fails unless something outside these two tests restores it between them.
def test_agent_tables_are_restored_between_tests_part_one() -> None:
    from agents import aether_agent

    aether_agent.SECTOR_ATMOSPHERE["cut"]["toxicity_pct"] = 99.0

    assert aether_agent.SECTOR_ATMOSPHERE["cut"]["toxicity_pct"] == 99.0


def test_agent_tables_are_restored_between_tests_part_two() -> None:
    from agents import aether_agent

    assert aether_agent.SECTOR_ATMOSPHERE["cut"]["toxicity_pct"] == 4.0
