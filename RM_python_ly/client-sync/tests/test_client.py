import json

import httpx
import pytest

from text_service.client import exchange, read_multiline


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["."], ""),
        (["hello", "."], "hello"),
        (["你好", "RM", "."], "你好\nRM"),
        (["hello", "", "."], "hello\n"),
        (["..", "."], "."),
    ],
)
def test_read_multiline(monkeypatch: pytest.MonkeyPatch, lines: list[str], expected: str) -> None:
    entered = iter(lines)
    monkeypatch.setattr("builtins.input", lambda: next(entered))
    assert read_multiline() == expected


def test_echo_request() -> None:
    text = "你好\n.\n"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/echo"
        assert "Authorization" not in request.headers
        assert json.loads(request.content) == {"text": text}
        return httpx.Response(200, json={"data": text})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "POST", "/echo", body={"text": text}) == (
            200,
            {"data": text},
        )
