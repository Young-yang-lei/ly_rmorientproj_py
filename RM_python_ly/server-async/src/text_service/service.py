"""In-memory baseline. Implement the task routes in handle()."""

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("GET", "/texts"),
    ("PUT", "/texts/{name}"),
    ("GET", "/texts/{name}"),
    ("DELETE", "/texts/{name}"),
)

TEXT_MAX_BYTES = 65_536


def route_error(method: str, path: str) -> int | None:
    allowed = {
        verb
        for verb, route in ROUTES
        if route == path or (route == "/texts/{name}" and path.startswith("/texts/"))
    }
    if not allowed:
        return 404
    return None if method in allowed else 405


def text_from_body(body: Any) -> str | tuple[int, dict[str, Any]]:
    if not isinstance(body, dict) or set(body) != {"text"}:
        return 400, {"message": "Expected text"}
    text = body["text"]
    if not isinstance(text, str):
        return 400, {"message": "text must be a string"}
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        return 400, {"message": "text must be valid Unicode"}
    if len(encoded) > TEXT_MAX_BYTES:
        return 413, {"message": "text is too large"}
    return text


@dataclass
class User:
    salt: bytes
    digest: bytes
    token: str | None = None
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if status := route_error(method, path):
            return status, {"message": "Not found" if status == 404 else "Method not allowed"}
        if method == "GET" and path == "/ping":
            return 200, {"data": "pong"}
        if method == "POST" and path == "/echo":
            text = text_from_body(body)
            if isinstance(text, tuple):
                return text
            return 200, {"data": text}
        text_name = path.removeprefix("/texts/") if path.startswith("/texts/") else None
        if text_name is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text_name):
            return 400, {"message": "Invalid text name"}
        if path in ("/users", "/sessions") and method == "POST":
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                return 400, {"message": "Expected username and password"}
            name, password = body["username"], body["password"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name)
                or not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                return 400, {"message": "Invalid username or password length"}
            try:
                password.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "Password must be valid Unicode"}
            # Hashing is outside the state lock; commit/check against current state under lock.
            if path == "/users":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
                with self.lock:
                    if name in self.users:
                        return 409, {"message": "Username exists"}
                    self.users[name] = User(salt, digest)
                return 201, {"data": {"username": name}}
            with self.lock:
                user = self.users.get(name)
                if user is None:
                    return 401, {"message": "Invalid username or password"}
                salt, expected = user.salt, user.digest
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
            with self.lock:
                if self.users.get(name) is not user or not hmac.compare_digest(digest, expected):
                    return 401, {"message": "Invalid username or password"}
                user.token = secrets.token_urlsafe(32)
                # Later server task: record a deadline and return expires_in.
                return 200, {"data": {"token": user.token}}
        text = None
        if text_name is not None and method == "PUT":
            text = text_from_body(body)
            if isinstance(text, tuple):
                return text
        protected = path in ("/texts", "/sessions/current") or text_name is not None
        if protected:
            token = (
                authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            )
            with self.lock:
                user = next((u for u in self.users.values() if token and u.token == token), None)
                if user is None:
                    return 401, {"message": "Login required"}
                # Later server task: check token expiry here, before reading or modifying state.
                if path == "/sessions/current" and method == "DELETE":
                    user.token = None
                    return 200, {"data": None}
                if path == "/texts" and method == "GET":
                    return 200, {"data": sorted(user.texts)}
                if text_name is not None and method == "PUT":
                    assert isinstance(text, str)
                    user.texts[text_name] = text
                    return 200, {"data": None}
                if text_name is not None and method == "GET":
                    saved = user.texts.get(text_name)
                    if saved is None:
                        return 404, {"message": "Text not found"}
                    return 200, {"data": saved}
                if text_name is not None and method == "DELETE":
                    if text_name not in user.texts:
                        return 404, {"message": "Text not found"}
                    del user.texts[text_name]
                    return 200, {"data": None}
        return 404, {"message": "Not found"}
