import io
import json
import logging

from cheap_flights.logging_setup import configure_logging

SECRET = "123456:secret-token"


def test_text_format_and_redaction():
    stream = io.StringIO()
    configure_logging("INFO", "text", [SECRET], stream)
    logging.getLogger("cheap_flights.test").info("posting to /bot%s/sendMessage", SECRET)
    logging.getLogger("cheap_flights.test").debug("hidden at INFO")
    output = stream.getvalue()
    assert "INFO" in output and "cheap_flights.test" in output
    assert "/bot***/sendMessage" in output
    assert SECRET not in output and "hidden" not in output


def test_json_format_includes_redacted_traceback():
    stream = io.StringIO()
    configure_logging("DEBUG", "json", [SECRET], stream)
    try:
        raise RuntimeError(f"token {SECRET}")
    except RuntimeError:
        logging.getLogger("x").exception("failed")
    record = json.loads(stream.getvalue().strip())
    assert record["level"] == "ERROR" and record["message"] == "failed"
    assert "RuntimeError: token ***" in record["exception"]
    assert SECRET not in stream.getvalue()


def test_library_logs_stay_quiet_even_at_debug():
    stream = io.StringIO()
    configure_logging("DEBUG", "text", [], stream)
    logging.getLogger("h2.codec.framed_read").debug("frame received")
    logging.getLogger("hickory_net.xfer").info("dns query")
    logging.getLogger("httpx").info("HTTP Request")
    logging.getLogger("primp").warning("library warning")
    logging.getLogger("cheap_flights.google").debug("search ok")
    output = stream.getvalue()
    assert "frame received" not in output and "dns query" not in output
    assert "HTTP Request" not in output
    assert "library warning" in output and "search ok" in output


def test_json_format_redacts_secrets_that_json_would_escape():
    secret = 'pa"ss\\word'
    stream = io.StringIO()
    configure_logging("INFO", "json", [secret], stream)
    try:
        raise RuntimeError(f"login failed for {secret}")
    except RuntimeError:
        logging.getLogger("x").exception("smtp said %s", secret)
    output = stream.getvalue()
    assert secret not in output
    assert json.dumps(secret)[1:-1] not in output
    record = json.loads(output.strip())
    assert record["message"] == "smtp said ***"
    assert "login failed for ***" in record["exception"]


def test_file_gets_the_same_redacted_lines(tmp_path):
    path = tmp_path / "logs" / "app.log"
    stream = io.StringIO()
    configure_logging("INFO", "text", [SECRET], stream, file=path)
    logging.getLogger("cheap_flights.test").info("token %s", SECRET)
    written = path.read_text()
    assert "token ***" in written and SECRET not in written
    assert "token ***" in stream.getvalue()


def test_file_rotates_and_keeps_backups(tmp_path):
    path = tmp_path / "app.log"
    configure_logging("INFO", "text", [], io.StringIO(), file=path, max_bytes=200, backups=2)
    for i in range(50):
        logging.getLogger("cheap_flights.x").info("line %03d %s", i, "y" * 40)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["app.log", "app.log.1", "app.log.2"]
    assert path.stat().st_size <= 200


def test_unwritable_log_file_warns_and_keeps_logging_to_stdout(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    stream = io.StringIO()
    configure_logging("INFO", "text", [], stream, file=blocker / "app.log")
    logging.getLogger("cheap_flights.x").info("still here")
    output = stream.getvalue()
    assert "cannot write log file" in output and "still here" in output


def test_log_buffer_keeps_recent_redacted_lines():
    from cheap_flights.logging_setup import LogBuffer

    buffer = LogBuffer(capacity=3)
    configure_logging("DEBUG", "text", [SECRET], io.StringIO(), buffer=buffer)
    log = logging.getLogger("cheap_flights.test")
    log.debug("one")
    log.info("two %s", SECRET)
    log.warning("three")
    log.error("four")
    entries = buffer.entries()
    assert [e.level for e in entries] == ["INFO", "WARNING", "ERROR"]
    assert "two ***" in entries[0].text and SECRET not in entries[0].text
    assert [e.level for e in buffer.entries(min_level="WARNING")] == ["WARNING", "ERROR"]
