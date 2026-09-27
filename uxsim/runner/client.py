"""製品 API のクライアント（nginx の frontend 経由 — api-server は publish されない）。

HTTP エラーで例外を上げない（ペルソナが「画面で見たもの」として扱うため）。全呼び出しを
``HttpTrace`` として残し、429 の回数を数える。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from uxsim.schema import HttpTrace

EXCERPT_CHARS = 2000


class EpistemeClient:
    """ログイン済みの bearer を持つ薄い HTTP クライアント。"""

    def __init__(self, base_url: str, timeout_s: float = 130.0, transport: Optional[httpx.BaseTransport] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str = ""
        self.count_429 = 0
        self.traces: list[HttpTrace] = []
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> tuple[bool, HttpTrace]:
        """``POST /api/auth/login``。成功すれば bearer を保持する。"""
        status, body, _ = self.call("POST", "/api/auth/login", json={"username": username, "password": password},
                                    auth=False)
        trace = self.traces[-1]
        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            self.token = str(body["access_token"])
            return True, trace
        return False, trace

    def logout(self) -> None:
        """クライアント側の bearer を捨てる（UI のログアウトと同じく製品側の状態は触らない）。"""
        self.token = ""

    def call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        files: Optional[dict] = None,
        data: Optional[dict] = None,
        auth: bool = True,
    ) -> tuple[Optional[int], Any, int]:
        """1 回呼ぶ。戻り値 ``(status, body, elapsed_ms)``。接続失敗は status=None。"""
        headers = {"Authorization": f"Bearer {self.token}"} if (auth and self.token) else {}
        started = time.monotonic()
        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "")} or None
        try:
            resp = self._http.request(method, path, json=json, params=clean_params, files=files, data=data,
                                      headers=headers)
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            self.traces.append(HttpTrace(method=method, path=path, status=None, elapsed_ms=elapsed,
                                         error=f"{type(exc).__name__}: {exc}"))
            return None, None, elapsed
        elapsed = int((time.monotonic() - started) * 1000)
        if resp.status_code == 429:
            self.count_429 += 1
        body = _decode(resp)
        self.traces.append(HttpTrace(method=method, path=path, status=resp.status_code, elapsed_ms=elapsed,
                                     response_excerpt=_excerpt(body)))
        return resp.status_code, body, elapsed

    def wait_task(self, task_id: str, timeout_s: float = 1800.0, poll_s: float = 10.0,
                  sleep=time.sleep) -> tuple[Optional[int], Any]:
        """``GET /api/admin/tasks/{task_id}`` を完了（completed / failed 等）まで待つ。"""
        deadline = time.monotonic() + timeout_s
        status, body = None, None
        while True:
            status, body, _ = self.call("GET", f"/api/admin/tasks/{task_id}")
            state = str((body or {}).get("status", "")).lower() if isinstance(body, dict) else ""
            if status != 200 or state in ("completed", "done", "failed", "error", "cancelled", "success"):
                return status, body
            if time.monotonic() >= deadline:
                return status, body
            sleep(poll_s)

    def take_traces(self) -> list[HttpTrace]:
        """溜まった trace を取り出して空にする（1 ステップ分の trace を切り出すのに使う）。"""
        out, self.traces = self.traces, []
        return out


def _decode(resp: httpx.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        try:
            return resp.json()
        except ValueError:
            return resp.text
    if ctype.startswith(("audio/", "image/", "application/octet-stream")):
        return {"_binary": ctype, "_bytes": len(resp.content)}
    return resp.text


def _excerpt(body: Any) -> str:
    if body is None:
        return ""
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return text[:EXCERPT_CHARS]
