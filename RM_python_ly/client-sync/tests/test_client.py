import json
from typing import Self

import httpx
import pytest

from text_service import client as client_module
from text_service.client import exchange, login_token, read_multiline, should_clear_token, text_path


def test_request() -> None:
    # 验证 exchange 会把令牌放进 Authorization 请求头，发送 GET /texts，并同时返回状态码和 JSON 数据。
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
    # 模拟用户逐行输入，验证单独的点结束输入、两个点转义为字面量点，以及多行文本用换行符拼接。
    entered = iter(lines)
    monkeypatch.setattr("builtins.input", lambda: next(entered))
    assert read_multiline() == expected


def test_echo_request() -> None:
    # 验证 echo 命令使用 POST /echo、发送 JSON 文本且不附带不必要的 Authorization 头，并保留 Unicode 和换行。
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
    # 验证文本名会进行 URL 路径片段编码，斜杠、空格和中文不会被误解释为额外路径或非法字节。
    assert text_path(name) == expected


def test_put_request() -> None:
    # 验证上传文本使用 PUT、携带 Bearer 令牌和 JSON 请求体，并把特殊名称作为单一路径片段发送。
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
    # 验证读取文本使用 GET、携带令牌且不发送请求体，同时完整保留服务端返回的文本。
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
    # 分别模拟删除成功和文本不存在，验证客户端不改写服务端状态码或响应 JSON。
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


def test_delete_user_request() -> None:
    # 验证注销账户使用 DELETE /users/me、携带当前令牌且没有请求体，并解析 null 数据响应。
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/users/me"
        assert request.headers["Authorization"] == "Bearer example"
        assert request.content == b""
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "DELETE", "/users/me", token="example") == (
            200,
            {"data": None},
        )


@pytest.mark.parametrize("command", ["logout", "delete-user"])
def test_successful_session_or_account_deletion_clears_token(command: str) -> None:
    # 验证 logout 和 delete-user 成功后客户端都会清除本地令牌，避免继续使用已撤销身份。
    assert should_clear_token(command, 200)


def test_failed_account_deletion_keeps_token() -> None:
    # 验证注销失败时保留令牌，而收到 401 时仍会清除令牌，区分暂时失败与身份失效。
    assert not should_clear_token("delete-user", 500)
    assert should_clear_token("delete-user", 401)


def test_non_json_response_falls_back_to_text() -> None:
    # 模拟服务端返回纯文本错误，验证 exchange 在 JSON 解析失败时把原始文本包装成 message 返回。
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream unavailable")

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/ping") == (
            502,
            {"message": "upstream unavailable"},
        )


def test_login_token() -> None:
    # 验证客户端能从规范的登录响应中提取非空字符串令牌，供后续请求复用。
    assert login_token({"data": {"token": "example", "expires_in": 300}}) == "example"


def test_login_requires_logout_before_another_login(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 模拟交互式客户端连续输入两次 login，验证已有令牌时不会发起第二次登录，并提示先 logout。
    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    entered = iter(["login", "alice", "login", "q"])
    requests: list[tuple[str, str, str, object]] = []
    password_prompts: list[str] = []

    def fake_exchange(
        client: object, method: str, path: str, token: str = "", body: object = None
    ) -> tuple[int, object]:
        requests.append((method, path, token, body))
        return 200, {"data": {"token": "example", "expires_in": 300}}

    def fake_getpass(prompt: str) -> str:
        password_prompts.append(prompt)
        return "password1"

    monkeypatch.setattr("sys.argv", ["rm-client"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(entered))
    monkeypatch.setattr(client_module.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(client_module.httpx, "Client", FakeClient)
    monkeypatch.setattr(client_module, "exchange", fake_exchange)

    client_module.main()

    assert requests == [("POST", "/sessions", "", {"username": "alice", "password": "password1"})]
    assert password_prompts == ["password: "]
    assert "Already logged in. Please log out before logging in again." in capsys.readouterr().out


@pytest.mark.parametrize(
    "result",
    [None, [], {}, {"data": None}, {"data": {}}, {"data": {"token": ""}}, {"data": {"token": 1}}],
)
def test_login_token_rejects_malformed_response(result: object) -> None:
    # 覆盖缺少 data、token 类型错误或空 token 等登录响应，验证客户端拒绝保存无效身份信息。
    with pytest.raises((TypeError, ValueError), match="Login response"):
        login_token(result)


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_multiline_input_allows_normal_terminal_exit(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    # 模拟 EOF 和 Ctrl-C，验证多行输入函数把终端的正常退出信号原样交给上层处理。
    def stop() -> str:
        raise error

    monkeypatch.setattr("builtins.input", stop)
    with pytest.raises(type(error)):
        read_multiline()
