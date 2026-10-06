from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import text_service.service as service_module
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


def test_delete_text_and_sorted_list() -> None:
    service = Service()
    auth = f"Bearer {register_and_login(service)}"
    for name in ("zeta", "alpha", "middle"):
        assert service.handle("PUT", f"/texts/{name}", {"text": name}, auth)[0] == 200
    assert service.handle("GET", "/texts", None, auth) == (
        200,
        {"data": ["alpha", "middle", "zeta"]},
    )
    assert service.handle("DELETE", "/texts/middle", None, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts", None, auth)[1] == {"data": ["alpha", "zeta"]}
    assert service.handle("DELETE", "/texts/middle", None, auth)[0] == 404


def test_user_cannot_read_list_or_delete_another_users_text() -> None:
    service = Service()
    alice = f"Bearer {register_and_login(service, 'alice')}"
    bob = f"Bearer {register_and_login(service, 'bob')}"
    assert service.handle("PUT", "/texts/private", {"text": "secret"}, alice)[0] == 200
    assert service.handle("GET", "/texts", None, bob) == (200, {"data": []})
    assert service.handle("GET", "/texts/private", None, bob)[0] == 404
    assert service.handle("DELETE", "/texts/private", None, bob)[0] == 404
    assert service.handle("GET", "/texts/private", None, alice)[1] == {"data": "secret"}


def test_concurrent_text_overwrites_remain_complete() -> None:
    service = Service()
    auth = f"Bearer {register_and_login(service)}"
    values = [f"value-{index}-" + "x" * 1_000 for index in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(
            pool.map(
                lambda value: service.handle("PUT", "/texts/shared", {"text": value}, auth)[0],
                values,
            )
        )
    assert statuses == [200] * len(values)
    assert service.handle("GET", "/texts/shared", None, auth)[1]["data"] in values


def test_account_deletion_removes_token_and_texts() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    token = register_and_login(service)
    auth = f"Bearer {token}"
    assert service.handle("PUT", "/texts/note", {"text": "old"}, auth)[0] == 200
    assert service.handle("DELETE", "/users/me", None, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts", None, auth)[0] == 401
    assert service.handle("PUT", "/texts/note", {"text": "stale"}, auth)[0] == 401

    assert service.handle("POST", "/users", account, "")[0] == 201
    new_token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert service.handle("GET", "/texts", None, f"Bearer {new_token}") == (200, {"data": []})


def test_deletion_and_text_write_are_atomic() -> None:
    service = Service()
    token = register_and_login(service)
    auth = f"Bearer {token}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        delete = pool.submit(service.handle, "DELETE", "/users/me", None, auth)
        write = pool.submit(service.handle, "PUT", "/texts/note", {"text": "value"}, auth)
    assert delete.result()[0] == 200
    assert write.result()[0] in (200, 401)
    assert service.handle("GET", "/texts", None, auth)[0] == 401


def test_stale_login_cannot_attach_to_reregistered_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    token = register_and_login(service)
    started = Event()
    release = Event()
    original_hash = service_module.hashlib.pbkdf2_hmac

    def delayed_hash(hash_name: str, password: bytes, salt: bytes, iterations: int) -> bytes:
        started.set()
        assert release.wait(timeout=5)
        return original_hash(hash_name, password, salt, iterations)

    monkeypatch.setattr(service_module.hashlib, "pbkdf2_hmac", delayed_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_login = pool.submit(service.handle, "POST", "/sessions", account, "")
        assert started.wait(timeout=5)
        monkeypatch.setattr(service_module.hashlib, "pbkdf2_hmac", original_hash)
        assert service.handle("DELETE", "/users/me", None, f"Bearer {token}")[0] == 200
        assert service.handle("POST", "/users", account, "")[0] == 201
        release.set()
    assert old_login.result()[0] == 401


def test_token_expiration_is_fixed_and_relogin_issues_new_token() -> None:
    now = [100.0]
    service = Service(token_ttl_seconds=5, clock=lambda: now[0])
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    login = service.handle("POST", "/sessions", account, "")
    token = login[1]["data"]["token"]
    auth = f"Bearer {token}"
    assert login == (200, {"data": {"token": token, "expires_in": 5}})

    assert service.handle("PUT", "/texts/note", {"text": "value"}, auth)[0] == 200
    now[0] = 104.999
    assert service.handle("GET", "/texts/note", None, auth)[0] == 200
    now[0] = 105.0
    assert service.handle("GET", "/texts", None, auth)[0] == 401
    assert service.handle("DELETE", "/sessions/current", None, auth)[0] == 401
    assert service.handle("DELETE", "/users/me", None, auth)[0] == 401

    next_login = service.handle("POST", "/sessions", account, "")
    next_token = next_login[1]["data"]["token"]
    assert next_token != token
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, auth)[0] == 401


@pytest.mark.parametrize("ttl", [0, -1])
def test_token_ttl_must_be_positive(ttl: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        Service(token_ttl_seconds=ttl)


def test_concurrent_registration() -> None:
    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
