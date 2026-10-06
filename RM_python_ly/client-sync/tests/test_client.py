import json

import httpx
import pytest

from text_service.client import exchange, read_multiline, text_path


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


@pytest.mark.parametrize(
    ("name", "expected"),
    [("note", "/texts/note"), ("a/b c", "/texts/a%2Fb%20c"), ("你好", "/texts/%E4%BD%A0%E5%A5%BD")],
)
def test_text_path_encodes_name_as_one_segment(name: str, expected: str) -> None:
    assert text_path(name) == expected


def test_put_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.raw_path == b"/texts/a%2Fb"
        assert request.headers["Authorization"] == "Bearer example"
        assert json.loads(request.content) == {"text": "new text"}
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(
            client,
            "PUT",
            text_path("a/b"),
            token="example",
            body={"text": "new text"},
        ) == (200, {"data": None})


def test_get_request_has_no_body() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/texts/note"
        assert request.headers["Authorization"] == "Bearer example"
        assert request.content == b""
        return httpx.Response(200, json={"data": "saved text"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", text_path("note"), token="example") == (
            200,
            {"data": "saved text"},
        )


@pytest.mark.parametrize(
    ("status", "result"),
    [(200, {"data": None}), (404, {"message": "Text not found"})],
)
def test_delete_request_preserves_response(status: int, result: dict[str, object]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/texts/note"
        assert request.headers["Authorization"] == "Bearer example"
        assert request.content == b""
        return httpx.Response(status, json=result)

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "DELETE", text_path("note"), token="example") == (
            status,
            result,
        )
