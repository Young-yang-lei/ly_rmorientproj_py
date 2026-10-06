import argparse
import getpass
from typing import Any
from urllib.parse import quote

import httpx


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
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
                elif command in ("delete-user", "delete"):
                    print("This task is not implemented in the starting code yet.")
                    continue
                else:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = result["data"]["token"]
                    if status == 401:
                        print("Please log in again.")
                    if status == 401 or (command == "logout" and status == 200):
                        token = ""
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
