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
    # 走通健康检查、注册、重复注册、错误密码、重复登录和退出流程，验证新登录替换旧令牌且退出立即撤销令牌。
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
    # 向注册和登录传入缺字段、多字段、错误类型、非法用户名、越界密码及非法 Unicode，验证统一返回 400。
    service = Service()
    for body in (
        None,
        [],
        {},
        {"username": "alice"},
        {"username": "alice", "password": "password1", "extra": True},
        {"username": True, "password": "password1"},
        {"username": "", "password": "password1"},
        {"username": "a" * 33, "password": "password1"},
        {"username": "a/b", "password": "password1"},
        {"username": "alice", "password": True},
        {"username": "alice", "password": "x" * 7},
        {"username": "alice", "password": "x" * 129},
        {"username": "alice", "password": "\ud800" * 8},
    ):
        assert service.handle("POST", "/users", body, "")[0] == 400
        assert service.handle("POST", "/sessions", body, "")[0] == 400


@pytest.mark.parametrize(
    "account",
    [
        {"username": "a", "password": "😀" * 8},
        {"username": "a" * 32, "password": "x" * 128},
    ],
)
def test_account_field_boundaries_are_accepted(account: dict[str, str]) -> None:
    # 验证用户名和密码长度的合法边界，包括 Unicode 密码按字符计数，均能成功注册并登录。
    service = Service()
    assert service.handle("POST", "/users", account, "")[0] == 201
    assert service.handle("POST", "/sessions", account, "")[0] == 200


def test_unknown_user_and_missing_authentication() -> None:
    # 验证不存在的用户不能登录，缺失或无效 Bearer 令牌不能访问文本或执行退出操作。
    service = Service()
    account = {"username": "missing", "password": "password1"}
    assert service.handle("POST", "/sessions", account, "")[0] == 401
    assert service.handle("GET", "/texts", None, "")[0] == 401
    assert service.handle("DELETE", "/sessions/current", None, "Bearer invalid")[0] == 401


@pytest.mark.parametrize("text", ["", "plain text", "你好\nRM", "x" * 65_536, "😀" * 16_384])
def test_echo(text: str) -> None:
    # 覆盖空串、普通文本、Unicode、换行和最大字节边界，验证 echo 原样返回合法文本。
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
    # 覆盖请求体结构、字段类型、Unicode 合法性和 UTF-8 大小超限，验证分别返回 400 或 413。
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
    # 验证未知路径返回 404，而已知路径使用不支持的 HTTP 方法返回 405。
    assert route_error(method, path) == expected


def test_write_read_and_overwrite_text() -> None:
    # 注册登录后创建并读取文本，再用空字符串覆盖同名文本，验证 PUT 的创建和覆盖语义。
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
    # 验证文本不存在、名称非法、正文类型错误、正文超限和令牌无效时返回对应错误状态。
    service = Service()
    token = register_and_login(service)
    auth = f"Bearer {token}"
    assert service.handle("GET", "/texts/missing", None, auth)[0] == 404
    assert service.handle("GET", "/texts/bad.name", None, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": 1}, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": "x" * 65_537}, auth)[0] == 413
    assert service.handle("GET", "/texts/note", None, "Bearer invalid")[0] == 401


@pytest.mark.parametrize("name", ["", "a" * 65, "bad.name", "a/b", "你好"])
def test_invalid_text_names(name: str) -> None:
    # 对空名称、过长名称、标点、斜杠和非 ASCII 名称执行增读删，验证所有入口均拒绝非法名称。
    service = Service()
    auth = f"Bearer {register_and_login(service)}"
    assert service.handle("PUT", f"/texts/{name}", {"text": "value"}, auth)[0] == 400
    assert service.handle("GET", f"/texts/{name}", None, auth)[0] == 400
    assert service.handle("DELETE", f"/texts/{name}", None, auth)[0] == 400


def test_text_name_length_boundary_is_accepted() -> None:
    # 使用恰好 64 个字符的文本名写入并读取，验证名称长度上限本身仍是合法值。
    service = Service()
    auth = f"Bearer {register_and_login(service)}"
    name = "a" * 64
    assert service.handle("PUT", f"/texts/{name}", {"text": "value"}, auth)[0] == 200
    assert service.handle("GET", f"/texts/{name}", None, auth)[1] == {"data": "value"}


def test_users_can_store_different_text_under_same_name() -> None:
    # 两个用户保存同名文本并分别读取，验证文本按用户隔离，不会互相覆盖。
    service = Service()
    alice = f"Bearer {register_and_login(service, 'alice')}"
    bob = f"Bearer {register_and_login(service, 'bob')}"
    assert service.handle("PUT", "/texts/note", {"text": "alice text"}, alice)[0] == 200
    assert service.handle("PUT", "/texts/note", {"text": "bob text"}, bob)[0] == 200
    assert service.handle("GET", "/texts/note", None, alice)[1] == {"data": "alice text"}
    assert service.handle("GET", "/texts/note", None, bob)[1] == {"data": "bob text"}


def test_delete_text_and_sorted_list() -> None:
    # 以非排序顺序创建文本，验证列表升序返回；再删除一项并验证列表更新及重复删除返回 404。
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
    # 让 Alice 保存私有文本，验证 Bob 的列表不可见且不能读取或删除，而 Alice 仍能读取原内容。
    service = Service()
    alice = f"Bearer {register_and_login(service, 'alice')}"
    bob = f"Bearer {register_and_login(service, 'bob')}"
    assert service.handle("PUT", "/texts/private", {"text": "secret"}, alice)[0] == 200
    assert service.handle("GET", "/texts", None, bob) == (200, {"data": []})
    assert service.handle("GET", "/texts/private", None, bob)[0] == 404
    assert service.handle("DELETE", "/texts/private", None, bob)[0] == 404
    assert service.handle("GET", "/texts/private", None, alice)[1] == {"data": "secret"}


def test_concurrent_text_overwrites_remain_complete() -> None:
    # 用线程池并发覆盖同一文本，验证所有写入成功且最终内容必为某次完整写入，不出现撕裂或拼接数据。
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
    # 注销账户后验证旧令牌和写入权限失效；同名重新注册登录后文本为空，证明旧状态已全部删除。
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
    # 并发执行注销与文本写入，验证二者按某一顺序原子完成：写入可先成功或因注销返回 401，但旧令牌最终必失效。
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
    # 暂停旧账号的密码校验，同时注销并重新注册同名账号，验证旧登录完成时不能把令牌绑定到新账号。
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
    # 使用可控时钟检查固定 TTL：到期前可访问、达到期限立即 401，操作不续期，重新登录产生不同的新令牌。
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


def test_concurrent_logins_leave_exactly_one_valid_token() -> None:
    # 同时登录同一用户四次，验证每次生成不同令牌，但状态中最终只保留一个有效令牌，其余均返回 401。
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    with ThreadPoolExecutor(max_workers=4) as pool:
        logins = list(
            pool.map(lambda _: service.handle("POST", "/sessions", account, ""), range(4))
        )
    tokens = [result[1]["data"]["token"] for result in logins]
    assert len(set(tokens)) == 4
    statuses = [service.handle("GET", "/texts", None, f"Bearer {token}")[0] for token in tokens]
    assert statuses.count(200) == 1
    assert statuses.count(401) == 3


@pytest.mark.parametrize("ttl", [0, -1])
def test_token_ttl_must_be_positive(ttl: int) -> None:
    # 验证零或负数令牌有效期属于非法配置，Service 构造时必须立即抛出清晰异常。
    with pytest.raises(ValueError, match="positive"):
        Service(token_ttl_seconds=ttl)


def test_concurrent_registration() -> None:
    # 四个线程同时注册同名用户，验证共享状态检查与写入受锁保护，只能一个 201、其余三个 409。
    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
