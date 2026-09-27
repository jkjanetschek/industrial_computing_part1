"""The shared logging setup: one file per service, compact lines, no noise."""

import logging

from shared.logging_setup import configure


def test_a_service_gets_its_own_file(tmp_path) -> None:
    configure("registry", log_dir=tmp_path)

    logging.getLogger("registry").info("registered aether")

    assert (tmp_path / "registry.log").read_text().strip().endswith("registered aether")


def test_the_line_is_compact_and_aligned(tmp_path) -> None:
    """Time, level, source, message. No date: these are per-run files."""
    configure("gaia", log_dir=tmp_path)

    logging.getLogger("gaia").info("-> call_agent(agent_id='aether')")

    line = (tmp_path / "gaia.log").read_text().strip()
    stamp, level, source, message = line.split(maxsplit=3)
    assert stamp.count(":") == 2 and "-" not in stamp
    assert level == "INFO"
    assert source == "gaia"
    assert message == "-> call_agent(agent_id='aether')"


def test_calling_it_twice_does_not_double_every_line(tmp_path) -> None:
    configure("gaia", log_dir=tmp_path)
    configure("gaia", log_dir=tmp_path)

    logging.getLogger("gaia").info("once")

    assert (tmp_path / "gaia.log").read_text().count("once") == 1


def test_uvicorn_keeps_its_own_logs_out_of_ours(tmp_path) -> None:
    """The health probe runs every five seconds, so uvicorn's access log would be
    twelve lines a minute of nothing in every file. It is excluded for free
    rather than filtered: uvicorn's LOGGING_CONFIG detaches these loggers from
    root. This test pins that assumption, because if a future uvicorn stopped
    doing it every service's file would quietly fill with health probes."""
    from uvicorn.config import LOGGING_CONFIG

    assert LOGGING_CONFIG["loggers"]["uvicorn.access"]["propagate"] is False
    assert LOGGING_CONFIG["loggers"]["uvicorn"]["propagate"] is False

    configure("gaia", log_dir=tmp_path)
    logging.getLogger("gaia").info("our own loggers do reach the file")

    assert "our own loggers do reach the file" in (tmp_path / "gaia.log").read_text()


def test_every_rail_event_reaches_the_log(caplog) -> None:
    """The log is the same event stream the rail shows, not a second opinion."""
    from coordinator.activity import ActivityLog

    with caplog.at_level(logging.INFO, logger="gaia"):
        ActivityLog().append("t", "call", "-> call_agent(agent_id='aether')")

    assert "-> call_agent(agent_id='aether')" in caplog.text


def test_an_error_event_is_logged_above_info(caplog) -> None:
    """A failed sub-agent call should stand out in a file skimmed after the fact."""
    from coordinator.activity import ActivityLog

    with caplog.at_level(logging.INFO, logger="gaia"):
        ActivityLog().append("t", "error", "<- error: agent 'demeter' call failed")

    levels = {record.levelno for record in caplog.records}
    assert logging.WARNING in levels


def test_the_registry_logs_registrations_and_lookups(caplog) -> None:
    """The registry was silent, and it holds the only record of how discovery
    was populated: which agent claimed which capabilities, at which url."""
    from fastapi.testclient import TestClient

    from registry_service.main import app

    card = {
        "id": "aether",
        "name": "AETHER",
        "description": "atmosphere",
        "url": "http://aether-agent:8001",
        "capabilities": ["atmosphere", "scrubbing"],
        "skills": [],
    }
    with caplog.at_level(logging.INFO, logger="registry"), TestClient(app) as client:
        client.post("/agents/register", json=card)
        client.get("/agents")
        client.get("/agents/nobody")

    assert "registered aether [atmosphere, scrubbing] at http://aether-agent:8001" in caplog.text
    assert "listing -> aether" in caplog.text
    assert "lookup nobody -> miss" in caplog.text


def test_a_long_question_is_shortened_for_the_log() -> None:
    from coordinator.gaia import QUESTION_LOG_MAX_CHARS, _shortened

    assert _shortened("  keeps   short   ones  ") == "keeps short ones"
    assert _shortened("x" * 500).endswith("...")
    assert len(_shortened("x" * 500)) == QUESTION_LOG_MAX_CHARS + 3


def test_third_party_request_chatter_is_kept_out(tmp_path) -> None:
    """httpx narrates every request at INFO. One GAIA turn produced a dozen such
    lines in a real run, swamping the handful that said what GAIA actually did."""
    configure("gaia", log_dir=tmp_path)

    logging.getLogger("httpx").info('HTTP Request: POST http://aether:8001/ "200 OK"')
    logging.getLogger("httpx2").info("HTTP Request: POST http://litellm:4000/v1/chat")
    logging.getLogger("httpx").warning("connection pool exhausted")
    logging.getLogger("gaia").info("-> aether situation report")

    written = (tmp_path / "gaia.log").read_text()
    assert "HTTP Request" not in written
    # A failing request is still worth knowing about.
    assert "connection pool exhausted" in written
    assert "-> aether situation report" in written
