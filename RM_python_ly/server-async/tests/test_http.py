from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app

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
    ping = await client.get("/ping")
    assert ping.status_code == 200
    assert ping.json() == {"data": "pong"}
    response = await client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
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
    assert (await client.post("/users", content=body)).status_code == 400


async def test_body_limit_and_routing(client: AsyncClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    assert (await client.get("/echo")).status_code == 405
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


async def test_failed_request_does_not_break_later_requests(client: AsyncClient) -> None:
    assert (await client.post("/users", content=b"not JSON")).status_code == 400
    response = await client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"data": "pong"}


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    assert (await client.patch(path)).status_code == 405


@pytest.mark.parametrize("text", ["", "你好\nRM", "😀" * 16_384])
async def test_echo(client: AsyncClient, text: str) -> None:
    response = await client.post("/echo", json={"text": text})
    assert response.status_code == 200
    assert response.json() == {"data": text}


async def test_echo_text_size_limit(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={"text": "x" * 65_536})).status_code == 200
    assert (await client.post("/echo", json={"text": "x" * 65_537})).status_code == 413


async def test_text_write_and_read(client: AsyncClient) -> None:
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
    assert (await client.post("/texts/note", json={"text": "value"})).status_code == 405


async def test_text_list_and_delete(client: AsyncClient) -> None:
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
