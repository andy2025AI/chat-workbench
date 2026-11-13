"""A single-user task workbench served on this computer, using Python stdlib."""

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
from urllib.parse import urlparse
import webbrowser


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("WORKBENCH_DATA", str(ROOT / "data"))).expanduser()
FILE = DATA / "workspace.json"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
MAX_BODY = 5_000_000
MAX_REVISION = 2**53 - 1  # JSON numbers must remain exact in the browser.
CATEGORIES = ["项目工作", "学习实践", "内容创作", "工具维护", "其他"]
STATUSES = ["待开始", "准备中", "进行中", "待决定", "已完成", "暂缓"]
TASK_FIELDS = ("id", "title", "category", "status", "next", "acceptance",
               "url", "artifact", "note")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def validate(document):
    """Validate before writing; bool must not count as an integer revision."""
    if not isinstance(document, dict):
        raise ValueError("工作台数据必须是 JSON 对象")
    if not isinstance(document.get("focus"), str) or len(document["focus"]) > 5000:
        raise ValueError("重点必须是文字，且不超过 5000 字符")
    if not isinstance(document.get("tasks"), list) or len(document["tasks"]) > 2000:
        raise ValueError("任务必须是列表，且不超过 2000 项")
    revision = document.get("revision")
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise ValueError("版本号无效，请使用工作台导出的备份")

    ids = set()
    tasks = []
    for task in document["tasks"]:
        if not isinstance(task, dict):
            raise ValueError("每项任务必须是 JSON 对象")
        for field in TASK_FIELDS:
            if not isinstance(task.get(field), str) or len(task[field]) > 10000:
                raise ValueError("任务字段不完整、不是文字或超过 10000 字符")
        if not task["id"].strip() or task["id"] in ids:
            raise ValueError("任务编号为空或重复")
        if not task["title"].strip() or len(task["title"]) > 200:
            raise ValueError("任务标题不能为空，且不超过 200 字符")
        if task["category"] not in CATEGORIES or task["status"] not in STATUSES:
            raise ValueError("未知分类或状态")
        for field in ("url", "artifact"):
            if task[field]:
                parsed = urlparse(task[field])
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ValueError("链接须为完整 http/https 地址；本地文件位置请填备注")
        ids.add(task["id"])
        tasks.append({field: task[field] for field in TASK_FIELDS})
    return {"focus": document["focus"], "tasks": tasks, "revision": revision}


def load_workspace():
    return validate(json.loads(FILE.read_text(encoding="utf-8")))


def lock_data_directory():
    """Keep one server per directory; the OS releases the lock on process exit."""
    handle = (DATA / ".workbench.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise
    return handle


def save_workspace(document):
    """Back up the previous snapshot, then atomically replace the JSON file."""
    if FILE.exists():
        backups = DATA / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        shutil.copy2(FILE, backups / (stamp + ".json"))
    descriptor, temporary = tempfile.mkstemp(dir=DATA, prefix=".workspace-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, document):
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def trusted_host(self):
        # Loopback binding alone does not validate the HTTP Host header.
        port = self.server.server_address[1]
        allowed = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
        if port == 80:
            allowed.update(("127.0.0.1", "localhost"))
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self.reply(403, {"error": "请使用本机 127.0.0.1 或 localhost 地址"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_host():
            return
        if self.path == "/api/workspace":
            try:
                self.reply(200, {"data": load_workspace(), "token": TOKEN})
            except (OSError, ValueError, RecursionError):
                self.reply(500, {"error": "数据文件无法读取。请保留文件并检查备份或目录权限"})
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path not in STATIC_FILES:
            self.send_error(404)
            return
        filename, content_type = STATIC_FILES[self.path]
        try:
            body = (ROOT / filename).read_bytes()
        except OSError:
            self.reply(500, {"error": "页面文件缺失或不可读，请检查源码是否解压完整"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.trusted_host():
            return
        if self.path != "/api/workspace":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        expected_origin = "http://" + self.headers["Host"]
        supplied_token = self.headers.get("X-Workbench-Token", "")
        if (not supplied_token.isascii()
                or not secrets.compare_digest(supplied_token, TOKEN)
                or origin not in (None, expected_origin)):
            self.reply(403, {"error": "请从本地工作台保存"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 1 or length > MAX_BODY:
                raise ValueError("文件为空或超过 5 MB")
            candidate = validate(json.loads(self.rfile.read(length)))
            with LOCK:
                try:
                    current = load_workspace()
                except (OSError, ValueError, RecursionError):
                    self.reply(500, {"error": "原数据无法读取，已停止保存。请先检查文件与备份"})
                    return
                if candidate["revision"] != current["revision"]:
                    self.reply(409, {"error": "另一个页面已保存更新。请先导出当前内容，再刷新核对"})
                    return
                candidate["revision"] = current["revision"] + 1
                if candidate["revision"] > MAX_REVISION:
                    raise ValueError("版本号已达到上限，请导出备份后建立新的数据目录")
                compact = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                if len(compact.encode("utf-8")) > MAX_BODY:
                    raise ValueError("保存后的备份会超过 5 MB，请减少内容后重试")
                save_workspace(candidate)
            self.reply(200, {"data": candidate})
        except (ValueError, TypeError, RecursionError) as error:
            self.reply(400, {"error": str(error)})
        except OSError:
            self.reply(500, {"error": "保存失败，请检查磁盘空间和目录权限"})


def main():
    parser = argparse.ArgumentParser(description="在本机运行对话工作台")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    arguments = parser.parse_args()
    if not 0 <= arguments.port <= 65535:
        parser.error("端口须在 0 到 65535 之间")
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        directory_lock = lock_data_directory()
    except OSError:
        raise SystemExit("数据目录正在被另一服务使用或不可写。请停止原服务或另选 WORKBENCH_DATA")
    with directory_lock:
        try:
            if not FILE.exists():
                save_workspace({"focus": "", "tasks": [], "revision": 0})
            load_workspace()
        except (OSError, ValueError, RecursionError):
            raise SystemExit("无法读取工作台数据。请先保留原文件，检查备份和目录权限")
        try:
            server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
        except OSError:
            raise SystemExit("端口被占用或不可用，请使用 --port 8766 指定其他端口")
        address = "http://127.0.0.1:" + str(server.server_address[1])
        print("对话工作台：" + address, flush=True)
        if arguments.open:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
