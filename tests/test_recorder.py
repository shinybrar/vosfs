"""Tests for the opt-in fixture recorder's sanitization (section 15.3)."""

from recorder import Recorder, sanitize_headers, sanitize_url


def test_sanitize_headers_redacts_secrets() -> None:
    cleaned = sanitize_headers(
        {
            "Authorization": "Bearer secret-token",
            "Cookie": "sid=abc",
            "Set-Cookie": "sid=abc; Path=/",
            "Content-Type": "text/xml",
        },
    )
    assert cleaned["Authorization"] == "<redacted>"
    assert cleaned["Cookie"] == "<redacted>"
    assert cleaned["Set-Cookie"] == "<redacted>"
    assert cleaned["Content-Type"] == "text/xml"


def test_sanitize_url_redacts_userinfo_and_tokens() -> None:
    cleaned = sanitize_url("https://user:pass@host.test/files?token=abcdef&p=%2Fx")
    assert "user" not in cleaned
    assert "pass" not in cleaned
    assert "abcdef" not in cleaned
    assert "token=%3Credacted%3E" in cleaned
    assert "p=%2Fx" in cleaned


def test_recorder_records_sanitized_interaction() -> None:
    recorder = Recorder()
    recorder.record(
        method="GET",
        url="https://host.test/files?signature=deadbeef",
        request_headers={"Authorization": "Bearer t"},
        status=200,
        response_headers={"Set-Cookie": "s=1"},
        body_sha256="0" * 64,
    )
    interaction = recorder.interactions[0]
    assert interaction.request_headers["Authorization"] == "<redacted>"
    assert interaction.response_headers["Set-Cookie"] == "<redacted>"
    assert "deadbeef" not in interaction.url


def test_sanitize_url_redacts_preauth_path_token() -> None:
    url = "https://ws/minoc/files/preauth:SEKRET_TOKEN_123/cadc:T/f.fits"
    out = sanitize_url(url)
    assert "SEKRET_TOKEN_123" not in out
    assert "preauth:<redacted>" in out
