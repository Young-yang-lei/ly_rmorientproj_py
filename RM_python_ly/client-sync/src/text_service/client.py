import argparse
import getpass
from typing import Any
from urllib.parse import quote

import httpx


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is None:
        response = client.request(method, path, headers=headers)
    else:
        response = client.request(method, path, json=body, headers=headers)
    try:
        result = response.json()
    except ValueError:
        result = {"message": response.text}
    return response.status_code, result


def read_multiline() -> str:
    """Read lines until a single dot; two dots represent a literal dot line."""
    lines: list[str] = []
    while True:
        line = input()
        if line == ".":
            return "\n".join(lines)
        lines.append("." if line == ".." else line)


def text_path(name: str) -> str:
    return f"/texts/{quote(name, safe='')}"


def should_clear_token(command: str, status: int) -> bool:
    return status == 401 or (command in ("logout", "delete-user") and status == 200)


def login_token(result: Any) -> str:
    if not isinstance(result, dict):
        raise TypeError("Login response must be a JSON object")
    data = result.get("data")
    if not isinstance(data, dict):
        raise TypeError("Login response is missing data")
    token = data.get("token")
    if not isinstance(token, str):
        raise TypeError("Login response contains an invalid token")
    if not token:
        raise ValueError("Login response contains an invalid token")
    return token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    args = parser.parse_args()
    token = ""
    with httpx.Client(
        base_url=args.url, timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            while True:
                command = input(
                    "ping / register / login / logout / list / echo / "
                    "delete-user / put / get / delete / q > "
                ).strip()
                body = None
                if command == "q":
                    break
                if command in ("register", "login"):
                    body = {
                        "username": input("username: "),
                        "password": getpass.getpass("password: "),
                    }
                    method, path = "POST", "/users" if command == "register" else "/sessions"
                elif command in ("ping", "logout", "list"):
                    method, path = {
                        "ping": ("GET", "/ping"),
                        "logout": ("DELETE", "/sessions/current"),
                        "list": ("GET", "/texts"),
                    }[command]
                elif command == "echo":
                    print("text (enter . on its own line to finish; enter .. for a dot line):")
                    method, path = "POST", "/echo"
                    body = {"text": read_multiline()}
                elif command == "put":
                    path = text_path(input("name: "))
                    print("text (enter . on its own line to finish; enter .. for a dot line):")
                    method, body = "PUT", {"text": read_multiline()}
                elif command == "get":
                    method, path = "GET", text_path(input("name: "))
                elif command == "delete":
                    method, path = "DELETE", text_path(input("name: "))
                elif command == "delete-user":
                    method, path = "DELETE", "/users/me"
                else:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = login_token(result)
                    if status == 401:
                        print("Please log in again.")
                    if should_clear_token(command, status):
                        token = ""
                except (httpx.HTTPError, TypeError, ValueError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
