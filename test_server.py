"""HTTP-level tests: temporary data only; never import the application module."""

import copy
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent


def task(**changes):
    result = {
        "id": "example-task",
        "title": "整理一次代码学习记录",
        "category": "学习实践",
        "status": "待开始",
        "next": "运行测试并解释结果",
        "acceptance": "能说明 HTTP 409 如何保护旧版本",
        "url": "https://example.com/conversation",
        "artifact": "https://example.com/result",
        "note": "合成测试数据",
    }
    result.update(changes)
    return result


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.log = (self.data_dir / "server.log").open("w", encoding="utf-8")
        self.addCleanup(self.log.close)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.process = None
        self.addCleanup(self.stop_server)
        self.start_server()

    def start_server(self):
        env = {**os.environ, "WORKBENCH_DATA": str(self.data_dir),
               "PYTHONDONTWRITEBYTECODE": "1"}
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=self.log, stderr=self.log,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail("server exited before accepting requests")
            try:
                status, result = self.request("GET", "/api/workspace")
                if status == 200:
                    self.snapshot = result["data"]
                    self.token = result["token"]
                    return
            except OSError:
                time.sleep(0.025)
        self.fail("server did not become ready")

    def stop_server(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def request(self, method, path, data=None, headers=None, raw=None):
        body = raw if raw is not None else (
            json.dumps(data, ensure_ascii=False).encode("utf-8")
            if data is not None else None
        )
        actual_headers = dict(headers or {})
        if body is not None:
            actual_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=actual_headers)
            response = connection.getresponse()
            payload = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                result = json.loads(payload)
            else:
                result = payload.decode("utf-8", errors="replace")
            return response.status, result
        finally:
            connection.close()

    def post(self, data, **headers):
        return self.request("POST", "/api/workspace", data=data,
                            headers={"X-Workbench-Token": self.token, **headers})

    def test_starts_with_a_blank_workspace(self):
        self.assertEqual(len(self.snapshot["tasks"]), 0)
        self.assertEqual(self.snapshot["focus"], "")
        self.assertEqual(self.snapshot["revision"], 0)

    def test_generic_task_and_unicode_survive_restart(self):
        candidate = {"focus": "完成第一个可复核成果", "tasks": [task()], "revision": 0}
        status, result = self.post(candidate)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["revision"], 1)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.snapshot, result["data"])

    def test_stale_save_returns_conflict_without_overwriting(self):
        candidate = copy.deepcopy(self.snapshot)
        candidate["focus"] = "第一个页面已保存"
        self.assertEqual(self.post(candidate)[0], 200)
        candidate["focus"] = "旧页面的内容"
        self.assertEqual(self.post(candidate)[0], 409)
        status, current = self.request("GET", "/api/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(current["data"]["focus"], "第一个页面已保存")

    def test_previous_snapshot_can_be_restored(self):
        candidate = copy.deepcopy(self.snapshot)
        candidate["focus"] = "更新后的重点"
        status, result = self.post(candidate)
        self.assertEqual(status, 200)
        backups = list((self.data_dir / "backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
        restored = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(restored["focus"], "")
        restored["revision"] = result["data"]["revision"]
        self.assertEqual(self.post(restored)[0], 200)
        self.assertEqual(json.loads((self.data_dir / "workspace.json").read_text(
            encoding="utf-8"))["focus"], "")

    def test_rejects_boolean_negative_string_and_missing_revisions(self):
        for revision in [False, True, -1, "0", 0.0, None, 2**53]:
            with self.subTest(revision=revision):
                candidate = copy.deepcopy(self.snapshot)
                candidate["revision"] = revision
                self.assertEqual(self.post(candidate)[0], 400)
        candidate = copy.deepcopy(self.snapshot)
        del candidate["revision"]
        self.assertEqual(self.post(candidate)[0], 400)
        self.assertEqual(json.loads((self.data_dir / "workspace.json").read_text(
            encoding="utf-8"))["revision"], 0)

    def test_invalid_import_does_not_change_the_file(self):
        before = (self.data_dir / "workspace.json").read_bytes()
        invalid_tasks = [
            task(url="javascript:alert(1)"),
            task(artifact="file:///private/example"),
            task(title=" "),
            task(category="unknown"),
            task(status="unknown"),
            task(note=12),
        ]
        for invalid in invalid_tasks:
            with self.subTest(title=invalid["title"]):
                self.assertEqual(self.post({"focus": "", "tasks": [invalid],
                                            "revision": 0})[0], 400)
                self.assertEqual((self.data_dir / "workspace.json").read_bytes(), before)

    def test_duplicate_task_ids_are_rejected(self):
        candidate = {"focus": "", "tasks": [task(), task(title="重复编号")], "revision": 0}
        self.assertEqual(self.post(candidate)[0], 400)

    def test_missing_or_incorrect_write_token_is_rejected(self):
        self.assertEqual(self.request("POST", "/api/workspace", data=self.snapshot)[0], 403)
        self.assertEqual(self.post(self.snapshot, **{"X-Workbench-Token": "wrong"})[0], 403)

    def test_cross_origin_write_is_rejected(self):
        self.assertEqual(self.post(self.snapshot, Origin="https://example.com")[0], 403)
        self.assertEqual(self.post(self.snapshot,
                                   Origin=f"http://127.0.0.1:{self.port}")[0], 200)

    def test_untrusted_host_cannot_read_tokens_or_static_files(self):
        for path in ["/api/workspace", "/", "/app.js"]:
            with self.subTest(path=path):
                status, result = self.request("GET", path, headers={"Host": f"example.com:{self.port}"})
                self.assertEqual(status, 403)
                self.assertNotIn("token", result)

    def test_localhost_host_is_accepted(self):
        self.assertEqual(self.request("GET", "/api/workspace",
                                     headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_data_and_arbitrary_paths_are_not_served(self):
        for path in ["/data/workspace.json", "/server.py", "/../server.py", "/not-found"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_malformed_json_is_rejected_and_server_remains_available(self):
        self.assertEqual(self.request("POST", "/api/workspace", raw=b'{"focus":',
                                     headers={"X-Workbench-Token": self.token})[0], 400)
        self.assertEqual(self.request("GET", "/api/workspace")[0], 200)

    def test_non_ascii_write_token_is_rejected_without_disconnect(self):
        self.assertEqual(self.post(self.snapshot, **{"X-Workbench-Token": "é"})[0], 403)
        self.assertEqual(self.request("GET", "/api/workspace")[0], 200)

    def test_second_process_cannot_use_the_same_data_directory(self):
        env = {**os.environ, "WORKBENCH_DATA": str(self.data_dir),
               "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--port", "0"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                self.fail("a second server kept running against the same data directory")
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("数据目录", (stdout + stderr).decode("utf-8"))
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_saved_snapshot_must_fit_the_import_limit_after_revision_increases(self):
        current = {"focus": "", "tasks": [], "revision": 9}
        (self.data_dir / "workspace.json").write_text(
            json.dumps(current), encoding="utf-8")
        before = (self.data_dir / "workspace.json").read_bytes()
        candidate = {"focus": "", "tasks": [
            task(id="size-" + str(index), note="") for index in range(2000)
        ], "revision": 9}
        encode = lambda value: json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        remaining = 5_000_000 - len(encode(candidate))
        size, extra = divmod(remaining, 2000)
        for item in candidate["tasks"]:
            item["note"] = "x" * size
        candidate["tasks"][0]["note"] += "x" * extra
        self.assertEqual(len(encode(candidate)), 5_000_000)
        status, _ = self.request(
            "POST", "/api/workspace", raw=encode(candidate),
            headers={"X-Workbench-Token": self.token})
        self.assertEqual(status, 400)
        self.assertEqual((self.data_dir / "workspace.json").read_bytes(), before)

    def test_disk_snapshot_and_automatic_backups_fit_the_import_limit(self):
        candidate = {"focus": "", "tasks": [
            task(id="backup-size-" + str(index), note="") for index in range(2000)
        ], "revision": 0}
        compact_size = len(json.dumps(
            candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        note_size = (4_950_000 - compact_size) // 2000
        for item in candidate["tasks"]:
            item["note"] = "x" * note_size
        status, result = self.post(candidate)
        self.assertEqual(status, 200)
        saved_size = (self.data_dir / "workspace.json").stat().st_size
        self.assertEqual(self.post({
            "focus": "", "tasks": [], "revision": result["data"]["revision"]
        })[0], 200)
        backups = list((self.data_dir / "backups").glob("*.json"))
        self.assertLessEqual(saved_size, 5_000_000)
        self.assertTrue(all(item.stat().st_size <= 5_000_000 for item in backups))


if __name__ == "__main__":
    unittest.main()
