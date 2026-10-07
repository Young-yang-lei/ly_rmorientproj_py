# RM HTTP 文本服务

这是一个使用 Python 实现的 HTTP 用户文本服务，包含异步服务端和同步命令行客户端。项目用于练习 HTTP API、用户认证、并发状态管理、输入校验和自动化测试。

用户可以注册和登录，并在自己的账户下保存、读取、列出和删除文本。不同用户的数据相互隔离，登录令牌会过期。服务端数据保存在内存中，因此服务停止后，账户、令牌和文本都会清空。

## 功能

- 健康检查和文本回显
- 用户注册、登录、退出和账户删除
- Bearer Token 身份认证，默认有效期为 300 秒
- 用户私有文本的创建、读取、列表和删除
- 请求格式、文本大小、用户名和文本名称校验
- 并发请求下的共享状态保护
- 服务层、HTTP 层和客户端自动化测试

## 项目结构

```text
RM_python_ly/
├── server-async/        # FastAPI + Uvicorn 异步服务端
│   ├── src/text_service/
│   │   ├── server.py    # HTTP 应用、日志和命令行入口
│   │   └── service.py   # 用户、令牌和文本业务逻辑
│   └── tests/           # 服务层与 HTTP 层测试
└── client-sync/         # httpx 同步命令行客户端
    ├── src/text_service/client.py
    └── tests/           # 客户端测试
```

## 从零开始启动

### 1. 准备环境

需要安装：

- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

项目使用 Python 3.13。`uv` 会根据项目中的 `.python-version` 创建独立虚拟环境；本机没有对应版本时，通常会自动下载。

macOS 或 Linux 可以安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell 可以安装 `uv`：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

确认安装成功：

```bash
git --version
uv --version
```

### 2. 克隆仓库

```bash
git clone https://github.com/Young-yang-lei/ly_rmorientproj_py.git
cd ly_rmorientproj_py
```

### 3. 启动服务端

打开第一个终端：

```bash
cd RM_python_ly/server-async
uv sync --locked
uv run rm-server
```

服务默认运行在 `http://127.0.0.1:7878`。看到路由列表后保持此终端运行。

可选启动参数：

```bash
uv run rm-server --host 127.0.0.1 --port 7878 --token-ttl-seconds 300
```

### 4. 启动客户端

打开第二个终端，并回到仓库根目录后执行：

```bash
cd RM_python_ly/client-sync
uv sync --locked
uv run rm-client
```

如果服务端使用了其他地址或端口，通过 `--url` 指定：

```bash
uv run rm-client --url http://127.0.0.1:7878
```

### 5. 完成一次基本操作

客户端出现提示符后，可以依次输入：

```text
ping
register
login
put
list
get
delete
logout
q
```

`register` 和 `login` 会继续询问用户名与密码。用户名只能包含字母、数字、下划线或连字符，长度为 1 至 32 个字符；密码长度为 8 至 128 个字符。

执行 `put` 或 `echo` 输入多行文本时，单独输入 `.` 表示结束；要保存仅包含一个点的行，请输入 `..`。

客户端命令说明：

| 命令 | 作用 | 是否需要登录 |
| --- | --- | --- |
| `ping` | 检查服务是否可用 | 否 |
| `echo` | 回显输入的文本 | 否 |
| `register` | 注册账户 | 否 |
| `login` | 登录并保存令牌 | 否 |
| `logout` | 退出当前账户 | 是 |
| `list` | 列出自己的文本名称 | 是 |
| `put` | 新建或覆盖一条文本 | 是 |
| `get` | 读取一条文本 | 是 |
| `delete` | 删除一条文本 | 是 |
| `delete-user` | 删除当前账户及其文本 | 是 |
| `q` | 退出客户端 | 否 |

## 运行检查

服务端和客户端是两个独立的 Python 项目，请分别进入对应目录运行检查。

服务端：

```bash
cd RM_python_ly/server-async
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

客户端：

```bash
cd RM_python_ly/client-sync
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## 注意事项

- 当前没有数据库，重启服务端会丢失所有数据。
- 服务端固定使用单个 Uvicorn worker，进程内状态由锁保护。
- 客户端收到 `401` 后会清除本地令牌，需要重新登录。
- 默认只监听本机地址；除非明确需要，不要将服务暴露到公网。

## License

本项目使用 [MIT License](LICENSE)。
