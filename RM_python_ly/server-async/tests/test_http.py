import argparse
import asyncio
from collections.abc import AsyncGenerator
from threading import Event
from typing import Any

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app, positive_int
from text_service.service import Service

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_http_routes(client: AsyncClient) -> None:
    # 通过真实 ASGI 请求覆盖 ping、注册、登录、鉴权访问，以及非法 JSON 和超大 HTTP 请求体的状态码。
    ping = await client.get("/ping")
    assert ping.status_code == 200
    assert ping.json() == {"data": "pong"}
    response = await client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    assert response.json()["data"]["expires_in"] == 300
    token = response.json()["data"]["token"]
    assert (
        await client.get("/texts", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (await client.get("/texts")).status_code == 401
    assert (
        await client.post(
            "/users", content=b"not JSON", headers={"Content-Type": "application/json"}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/users", content=b"x" * 524289, headers={"Content-Type": "application/json"}
        )
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
async def test_invalid_json(client: AsyncClient, body: bytes) -> None:
    # 发送非法 UTF-8、普通非 JSON 文本和 NaN，验证 HTTP 层在进入业务逻辑前返回 400。
    assert (await client.post("/users", content=body)).status_code == 400


async def test_body_limit_and_routing(client: AsyncClient) -> None:
    # 验证请求体恰好达到上限仍进入业务校验，超过上限返回 413，并区分未知路径 404 与错误方法 405。
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    assert (await client.get("/echo")).status_code == 405
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


async def test_failed_request_does_not_break_later_requests(client: AsyncClient) -> None:
    # 先发送坏请求，再发送正常 ping，验证单个请求失败不会破坏 ASGI 应用处理后续请求的能力。
    assert (await client.post("/users", content=b"not JSON")).status_code == 400
    response = await client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"data": "pong"}


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    # 对公开和受保护路径统一发送 PATCH，验证路由方法检查优先于鉴权检查并返回 405，而不是 401。
    assert (await client.patch(path)).status_code == 405


@pytest.mark.parametrize("text", ["", "你好\nRM", "😀" * 16_384])
async def test_echo(client: AsyncClient, text: str) -> None:
    # 通过 HTTP 层验证 echo 保留空串、Unicode 和换行，并在响应 JSON 的 data 中原样返回。
    response = await client.post("/echo", json={"text": text})
    assert response.status_code == 200
    assert response.json() == {"data": text}


async def test_echo_text_size_limit(client: AsyncClient) -> None:
    # 验证 echo 文本恰好 65,536 字节成功，超过一个字节则返回 413。
    assert (await client.post("/echo", json={"text": "x" * 65_536})).status_code == 200
    assert (await client.post("/echo", json={"text": "x" * 65_537})).status_code == 413


async def test_text_write_and_read(client: AsyncClient) -> None:
    # 通过完整 HTTP 流程注册登录、上传、读取、覆盖并读取不存在文本，验证文本路由和 Bearer 鉴权协作正确。
    account = {"username": "alice", "password": "password1"}
    assert (await client.post("/users", json=account)).status_code == 201
    login = await client.post("/sessions", json=account)
    headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    assert (
        await client.put("/texts/note", json={"text": "first"}, headers=headers)
    ).status_code == 200
    response = await client.get("/texts/note", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": "first"}

    assert (
        await client.put("/texts/note", json={"text": "second"}, headers=headers)
    ).status_code == 200
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": "second"}
    assert (await client.get("/texts/missing", headers=headers)).status_code == 404


async def test_text_routes_reject_wrong_method(client: AsyncClient) -> None:
    # 对文本写入、echo 和文本列表使用错误方法，验证 HTTP 层统一返回 405。
    assert (await client.post("/texts/note", json={"text": "value"})).status_code == 405
    assert (await client.delete("/echo")).status_code == 405
    assert (await client.post("/texts", json={})).status_code == 405


async def test_text_list_and_delete(client: AsyncClient) -> None:
    # 通过 HTTP 请求验证文本列表按名称排序、删除后列表更新，以及重复删除返回 404。
    account = {"username": "alice", "password": "password1"}
    assert (await client.post("/users", json=account)).status_code == 201
    login = await client.post("/sessions", json=account)
    headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    for name in ("zeta", "alpha"):
        assert (
            await client.put(f"/texts/{name}", json={"text": name}, headers=headers)
        ).status_code == 200
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["alpha", "zeta"]}
    assert (await client.delete("/texts/alpha", headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["zeta"]}
    assert (await client.delete("/texts/alpha", headers=headers)).status_code == 404


async def test_account_deletion_invalidates_token_and_removes_texts(client: AsyncClient) -> None:
    # 上传文本后注销账户，验证旧令牌失效、旧文本被删除，同名重新注册后得到空文本列表。
    account = {"username": "alice", "password": "password1"}
    assert (await client.post("/users", json=account)).status_code == 201
    login = await client.post("/sessions", json=account)
    headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    assert (
        await client.put("/texts/note", json={"text": "old"}, headers=headers)
    ).status_code == 200
    assert (await client.delete("/users/me", headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).status_code == 401

    assert (await client.post("/users", json=account)).status_code == 201
    login = await client.post("/sessions", json=account)
    headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    assert (await client.get("/texts", headers=headers)).json() == {"data": []}


async def test_configured_token_ttl_is_returned() -> None:
    # 创建自定义 17 秒 TTL 的应用，验证登录响应返回实际配置的 expires_in，而不是固定默认值。
    app = create_app(token_ttl_seconds=17)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        account = {"username": "alice", "password": "password1"}
        assert (await client.post("/users", json=account)).status_code == 201
        response = await client.post("/sessions", json=account)
        assert response.json()["data"]["expires_in"] == 17


async def test_concurrent_http_registration_has_one_winner(client: AsyncClient) -> None:
    # 用 asyncio.gather 并发发送四个注册请求，验证从 HTTP 层观察到一个 201 和三个 409。
    account = {"username": "alice", "password": "password1"}
    responses = await asyncio.gather(*(client.post("/users", json=account) for _ in range(4)))
    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409]


async def test_blocking_service_work_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 人为阻塞注册业务线程，同时在同一事件循环请求 ping，验证 asyncio.to_thread 让 ping 仍能在超时前完成。
    started = Event()
    release = Event()
    original_handle = Service.handle

    def delayed_handle(
        self: Service, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and path == "/users":
            started.set()
            assert release.wait(timeout=5)
        return original_handle(self, method, path, body, authorization)

    monkeypatch.setattr(Service, "handle", delayed_handle)
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as local_client,
    ):
        registration = asyncio.create_task(
            local_client.post("/users", json={"username": "alice", "password": "password1"})
        )
        try:
            assert await asyncio.to_thread(started.wait, 5)
            response = await asyncio.wait_for(local_client.get("/ping"), timeout=1)
            assert response.status_code == 200
        finally:
            release.set()
        assert (await registration).status_code == 201


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("300", 300)])
def test_positive_int(value: str, expected: int) -> None:
    # 验证命令行正整数解析器接受合法字符串并转换为对应整数。
    assert positive_int(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_positive_int_rejects_invalid_values(value: str) -> None:
    # 验证零、负数和非数字参数被 argparse 类型转换器拒绝，并抛出 ArgumentTypeError。
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(value)
