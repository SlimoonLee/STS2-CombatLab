#!/usr/bin/env python3
"""Local web front end for the game-outside Combat Lab.

The server intentionally uses only Python's standard library.  It serves the
catalog and game thumbnails locally, stores plans below ``combat_lab/runs`` and
delegates evaluation to the existing ``combat_lab.py`` CLI.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from combat_lab_saves import SaveImportError, discover_saves, freeze_source, public_state, reconstruct
from combat_lab_relics import COMMON_FIELDS, RELIC_FIELDS
from combat_lab_cards import enrich_card_catalog


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "combat_lab" / "web"
ASSET_ROOT = ROOT / "combat_lab" / "assets"
CATALOG_PATH = ROOT / "combat_lab" / "game_catalog_zhs.json"
RUN_ROOT = ROOT / "combat_lab" / "runs" / "web"
PLAN_PATH = RUN_ROOT / ".combat-lab-web-plan.json"
_HARNESS_CANDIDATES = (
    # Keep the web service on the same Release output as the migrated upstream
    # project, otherwise an old DLL in the former directory can be selected.
    ROOT / "CombatSolver" / ".local" / "tool-build" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
    ROOT / "CombatSolver" / "tools" / "search" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
    ROOT / "CombatSolver" / "tools" / "OfflineSearchHarness" / "bin" / "Release" / "net9.0" / "OfflineSearchHarness.dll",
)
HARNESS_PATH = next((path for path in _HARNESS_CANDIDATES if path.exists()), _HARNESS_CANDIDATES[0])


def _read_catalog() -> dict[str, Any]:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取游戏目录：{CATALOG_PATH}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("游戏目录根节点必须是对象")
    data["relicStateFields"] = {"common": COMMON_FIELDS, "relics": RELIC_FIELDS}
    enrich_card_catalog(data)
    return data


CATALOG = _read_catalog()
CATALOG_BYTES = json.dumps(CATALOG, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _safe_child(root: Path, relative: str) -> Path | None:
    """Resolve a URL path below root without allowing traversal."""

    try:
        candidate = (root / unquote(relative).lstrip("/\\")).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, plan: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job_dir = RUN_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        plan_path = job_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        job = {
            "id": job_id,
            "status": "queued",
            "createdAt": time.time(),
            "lines": [],
            "planPath": str(plan_path),
            "outputDir": str(job_dir),
            "process": None,
            "report": None,
            "error": None,
            "progress": {
                "phase": "排队中",
                "completed": 0,
                "total": 0,
                "current": None,
                "percent": 0,
                "indeterminate": False,
                "elapsedMs": 0,
                "expanded": None,
                "transitions": None,
                "caseWorkers": 1,
                "dop": 1,
                "updatedAt": time.time(),
            },
        }
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return self.public(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self.public(job) if job else None

    def stop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            process = job.get("process") if job else None
            if not process or process.poll() is not None:
                return False
            job["status"] = "stopping"
            job["lines"].append("正在停止评估进程…")
            job["progress"].update(
                phase="正在停止",
                indeterminate=False,
                updatedAt=time.time(),
            )
            # combat_lab.py may have several independent dotnet harness
            # children when case-level parallelism is enabled.  Terminating
            # only the Python coordinator would leave those game hosts alive
            # on Windows, so kill the complete process tree there.
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except OSError:
                    process.terminate()
            else:
                process.terminate()
            return True

    def public(self, job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {
            key: value
            for key, value in job.items()
            if key not in {"process"}
        }

    def _append(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                clean = line.rstrip()
                if clean.startswith("LAB_PROGRESS "):
                    try:
                        event = json.loads(clean.removeprefix("LAB_PROGRESS "))
                    except json.JSONDecodeError:
                        event = None
                    if isinstance(event, dict):
                        self._apply_progress_event(job, event)
                        phase = str(event.get("phase") or "处理中")
                        current = event.get("current")
                        total = event.get("total")
                        if isinstance(current, int) and isinstance(total, int) and total > 0:
                            detail = f"进度：{phase} · 第 {current}/{total} 场"
                        else:
                            detail = f"进度：{phase}"
                        job["lines"].append(detail)
                    else:
                        job["lines"].append(clean)
                else:
                    self._apply_native_progress_line(job, clean)
                    job["lines"].append(clean)
                if len(job["lines"]) > 500:
                    del job["lines"][:-500]

    @staticmethod
    def _apply_progress_event(job: dict[str, Any], event: dict[str, Any]) -> None:
        progress = job["progress"]
        for key in (
            "phase",
            "completed",
            "total",
            "current",
            "percent",
            "elapsedMs",
            "expanded",
            "transitions",
            "caseWorkers",
            "dop",
            "encounter",
            "label",
        ):
            if key in event:
                progress[key] = event[key]
        progress["indeterminate"] = event.get("event") == "case-start"
        if event.get("event") in {"finished", "case-complete"}:
            progress["indeterminate"] = False
        progress["updatedAt"] = time.time()

    @staticmethod
    def _apply_native_progress_line(job: dict[str, Any], line: str) -> None:
        """Capture native harness progress without making it the percentage source."""

        if not line.startswith("[search-progress]"):
            return
        progress = job["progress"]
        values: dict[str, str] = {}
        for token in line.removeprefix("[search-progress]").strip().split():
            if "=" in token:
                key, value = token.split("=", 1)
                values[key] = value
        if values.get("phase"):
            progress["phase"] = values["phase"]
        for source, target in (
            ("wall_ms", "elapsedMs"),
            ("expanded", "expanded"),
            ("reviewed_worldlines", "reviewedWorldlines"),
            ("max_nodes", "maxNodes"),
            ("turn_layers", "turnLayers"),
            ("play_depth", "playDepth"),
            ("frontier", "frontier"),
        ):
            if source not in values:
                continue
            try:
                progress[target] = int(values[source])
            except ValueError:
                continue
        progress["indeterminate"] = True
        progress["updatedAt"] = time.time()

    @staticmethod
    def _failure_detail(output_dir: Path) -> str | None:
        """Extract the first harness error so the result page is actionable."""

        candidates = sorted(output_dir.glob("cases/*/result.json"))
        candidates += sorted(output_dir.glob("cases/*/harness-result.json"))
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                error_type = str(error.get("type") or "求解器错误")
                message = str(error.get("message") or "未提供详细信息")
                return f"{error_type}: {message}"
            if error:
                return str(error)
        return None

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            plan_path = Path(job["planPath"])
            output_dir = Path(job["outputDir"])
            job["startedAt"] = time.time()
            job["progress"].update(phase="启动评估", updatedAt=time.time())
        command = [
            sys.executable,
            "-u",
            str(ROOT / "combat_lab.py"),
            "evaluate",
            "--input",
            str(plan_path),
            "--out",
            str(output_dir),
            "--force",
        ]
        if not HARNESS_PATH.exists():
            self._append(job_id, f"找不到 OfflineSearchHarness：{HARNESS_PATH}")
            with self._lock:
                job["status"] = "failed"
                job["error"] = "OfflineSearchHarness 尚未构建。"
                job["finishedAt"] = time.time()
                job["progress"].update(phase="评估失败", indeterminate=False, updatedAt=time.time())
            return
        try:
            process_env = os.environ.copy()
            # combat_lab.py emits the streamed protocol as UTF-8.  Without an
            # explicit encoding, Windows can select the active GBK code page
            # for a redirected child stdout and fail on game-localized text.
            process_env["PYTHONIOENCODING"] = "utf-8:replace"
            process_env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._lock:
                job["process"] = process
            assert process.stdout is not None
            for line in process.stdout:
                self._append(job_id, line)
            return_code = process.wait()
        except OSError as exc:
            self._append(job_id, f"启动评估失败：{exc}")
            return_code = -1
        report_path = output_dir / "evaluation.json"
        report: dict[str, Any] | None = None
        if report_path.exists():
            try:
                loaded = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    report = loaded
            except (OSError, json.JSONDecodeError) as exc:
                self._append(job_id, f"读取 evaluation.json 失败：{exc}")
        with self._lock:
            job["process"] = None
            job["returnCode"] = return_code
            job["report"] = report
            job["finishedAt"] = time.time()
            if job["status"] == "stopping":
                job["status"] = "stopped"
                job["progress"].update(phase="已停止", indeterminate=False, updatedAt=time.time())
            elif return_code == 0 and report is not None:
                job["status"] = "completed"
                job["progress"].update(
                    phase="评估完成",
                    percent=100,
                    indeterminate=False,
                    updatedAt=time.time(),
                )
            else:
                job["status"] = "failed"
                detail = self._failure_detail(output_dir)
                job["error"] = (
                    f"评估失败：{detail}"
                    if detail
                    else ("评估未生成报告" if return_code == 0 else f"评估进程退出码：{return_code}")
                )
                job["progress"].update(phase="评估失败", indeterminate=False, updatedAt=time.time())


JOBS = JobStore()


class Handler(BaseHTTPRequestHandler):
    server_version = "CombatLabWeb/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Keep the local service console useful without logging every asset.
        if self.path.startswith("/api/"):
            super().log_message(format, *args)

    def _send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This is a local development tool; avoid stale CSS/JS after an update.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(_json_bytes(value), "application/json; charset=utf-8", status)

    def _not_found(self, message: str = "找不到资源") -> None:
        self._send_json({"error": message}, HTTPStatus.NOT_FOUND)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8 * 1024 * 1024:
                raise ValueError("请求体为空或过大")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求 JSON 无法解析：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("请求 JSON 必须是对象")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True, "harness": HARNESS_PATH.exists(), "catalogVersion": CATALOG.get("catalogVersion")})
            return
        if path == "/api/catalog":
            self._send(CATALOG_BYTES, "application/json; charset=utf-8")
            return
        if path == "/api/saves":
            self._send_json(discover_saves())
            return
        if path == "/api/plan":
            if not PLAN_PATH.exists():
                self._not_found("尚未保存网页计划")
                return
            try:
                value = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self._send_json({"error": f"计划读取失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(value)
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/").strip("/")
            job = JOBS.get(job_id)
            if job is None:
                self._not_found("找不到评估任务")
            else:
                self._send_json(job)
            return
        if path == "/" or path == "/index.html":
            self._serve_file(WEB_ROOT / "index.html")
            return
        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            candidate = _safe_child(WEB_ROOT / "static", relative)
            self._serve_file(candidate)
            return
        if path.startswith("/assets/"):
            relative = path.removeprefix("/assets/")
            candidate = _safe_child(ASSET_ROOT, relative)
            self._serve_file(candidate)
            return
        self._not_found()

    def _serve_file(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            self._not_found()
            return
        try:
            body = path.read_bytes()
        except OSError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or path.suffix in {".js", ".json"}:
            content_type += "; charset=utf-8"
        self._send(body, content_type)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path in {"/api/saves/import", "/api/saves/state"}:
            try:
                source_id = body.get("sourceId")
                if path == "/api/saves/import":
                    source_id = freeze_source(path=str(body.get("path", "")), content=body.get("content"), name=str(body.get("name", "")))
                if not isinstance(source_id, str):
                    raise SaveImportError("请先导入存档")
                self._send_json(public_state(reconstruct(source_id, body.get("selection"))))
            except (SaveImportError, OSError, TypeError, KeyError, IndexError, ValueError) as exc:
                self._send_json({"error": f"存档导入失败：{exc}"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/plan":
            RUN_ROOT.mkdir(parents=True, exist_ok=True)
            PLAN_PATH.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            self._send_json({"ok": True, "path": str(PLAN_PATH)})
            return
        if path == "/api/evaluate":
            try:
                job = JOBS.create(body)
            except (OSError, TypeError, ValueError) as exc:
                self._send_json({"error": f"无法建立评估任务：{exc}"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(job, HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/jobs/") and path.endswith("/stop"):
            job_id = path.removeprefix("/api/jobs/").removesuffix("/stop").strip("/")
            self._send_json({"ok": JOBS.stop(job_id)})
            return
        self._not_found()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Combat Lab 本地网页服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只允许本机访问")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    address = f"http://{args.host}:{args.port}/"
    print(f"Combat Lab 网页服务已启动：{address}")
    print("按 Ctrl+C 停止服务。")
    if args.open:
        threading.Timer(0.35, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 Combat Lab 网页服务…")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
