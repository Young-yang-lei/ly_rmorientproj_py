import pytest

from text_service.service import Service, route_error


def register_and_login(service: Service, username: str = "alice") -> str:
    account = {"username": username, "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    return service.handle("POST", "/sessions", account, "")[1]["data"]["token"]


def test_account_lifecycle() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    assert service.handle("POST", "/users", account, "")[0] == 201
    assert service.handle("POST", "/users", account, "")[0] == 409
    assert service.handle("POST", "/sessions", {**account, "password": "incorrect"}, "")[0] == 401
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    next_token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert token != next_token
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}") == (200, {"data": []})
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 401


def test_validation() -> None:
    service = Service()
    for body in (
        None,
        [],
        {},
        {"username": True, "password": "password1"},
        {"username": "a/b", "password": "password1"},
    ):
        assert service.handle("POST", "/users", body, "")[0] == 400


def test_unknown_user_and_missing_authentication() -> None:
    service = Service()
    account = {"username": "missing", "password": "password1"}
    assert service.handle("POST", "/sessions", account, "")[0] == 401
    assert service.handle("GET", "/texts", None, "")[0] == 401
    assert service.handle("DELETE", "/sessions/current", None, "Bearer invalid")[0] == 401


@pytest.mark.parametrize("text", ["", "plain text", "你好\nRM", "x" * 65_536, "😀" * 16_384])
def test_echo(text: str) -> None:
    service = Service()
    assert service.handle("POST", "/echo", {"text": text}, "") == (200, {"data": text})


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, 400),
        ([], 400),
        ({}, 400),
        ({"text": "ok", "extra": True}, 400),
        ({"text": 1}, 400),
        ({"text": "\ud800"}, 400),
        ({"text": "x" * 65_537}, 413),
        ({"text": "😀" * 16_385}, 413),
    ],
)
def test_echo_validation(body: object, expected: int) -> None:
    assert Service().handle("POST", "/echo", body, "")[0] == expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/missing", 404),
        ("PATCH", "/ping", 405),
        ("POST", "/ping", 405),
        ("GET", "/users", 405),
    ],
)
def test_route_errors(method: str, path: str, expected: int) -> None:
    assert route_error(method, path) == expected


def test_write_read_and_overwrite_text() -> None:
    service = Service()
    token = register_and_login(service)
    auth = f"Bearer {token}"
    assert service.handle("PUT", "/texts/note", {"text": "first"}, auth) == (
        200,
        {"data": None},
    )
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "first"})
    assert service.handle("PUT", "/texts/note", {"text": ""}, auth)[0] == 200
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": ""})


def test_text_read_and_write_validation() -> None:
    service = Service()
    token = register_and_login(service)
    auth = f"Bearer {token}"
    assert service.handle("GET", "/texts/missing", None, auth)[0] == 404
    assert service.handle("GET", "/texts/bad.name", None, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": 1}, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": "x" * 65_537}, auth)[0] == 413
    assert service.handle("GET", "/texts/note", None, "Bearer invalid")[0] == 401


def test_users_can_store_different_text_under_same_name() -> None:
    service = Service()
    alice = f"Bearer {register_and_login(service, 'alice')}"
    bob = f"Bearer {register_and_login(service, 'bob')}"
    assert service.handle("PUT", "/texts/note", {"text": "alice text"}, alice)[0] == 200
    assert service.handle("PUT", "/texts/note", {"text": "bob text"}, bob)[0] == 200
    assert service.handle("GET", "/texts/note", None, alice)[1] == {"data": "alice text"}
    assert service.handle("GET", "/texts/note", None, bob)[1] == {"data": "bob text"}


def test_concurrent_registration() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
