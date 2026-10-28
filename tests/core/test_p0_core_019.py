import logging

from atlian_assiant.core.redaction import MASK, RedactingFilter, Redactor
from atlian_assiant.core.telemetry import Telemetry


def test_text_redaction_of_configured_secrets_and_patterns():
    r = Redactor(["tok-abcdef123"])
    out = r.text("token tok-abcdef123 and Authorization: Bearer abcdefghijklmnop and password=hunter2, api_key: xyz123456")
    assert "tok-abcdef123" not in out and "abcdefghijklmnop" not in out and "hunter2" not in out and "xyz123456" not in out
    assert out.count(MASK) >= 4 and Redactor().text("nothing secret here") == "nothing secret here"


def test_mapping_redaction_is_recursive_and_key_based():
    r = Redactor(["s3cr3t-value"])
    out = r.mapping({"Authorization": "Basic abc", "nested": {"api_token": "x", "ok": 1}, "note": "has s3cr3t-value inside", "n": 5})
    assert out == {"Authorization": MASK, "nested": {"api_token": MASK, "ok": 1}, "note": f"has {MASK} inside", "n": 5}


def test_short_secrets_are_ignored_to_avoid_mangling_text():
    assert Redactor(["ab"]).text("about") == "about"


def test_logging_filter_masks_formatted_records(caplog):
    logger = logging.getLogger("t.redact")
    logger.addFilter(RedactingFilter(Redactor(["supersecretvalue"])))
    with caplog.at_level(logging.INFO, logger="t.redact"):
        logger.info("calling with %s", "supersecretvalue")
    assert "supersecretvalue" not in caplog.text and MASK in caplog.text


def test_telemetry_span_records_duration_and_hooks_get_redacted_data():
    t = Telemetry(Redactor(["abcd1234"]))
    got = []
    t.on_request.append(got.append)
    with t.span("jira.issue.get", backend="rest") as info:
        pass
    assert info["operation"] == "jira.issue.get" and info["duration_ms"] >= 0
    t.request({"url": "https://x?token=abcd1234", "headers": {"Authorization": "x"}})
    assert got[0]["headers"]["Authorization"] == MASK and "abcd1234" not in got[0]["url"]
