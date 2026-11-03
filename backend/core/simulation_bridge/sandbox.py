"""検証シミュレーション層（SB層）— sim-runner（隔離実行コンテナ）の HTTP クライアント。

設計: ``docs/features/simulation_bridge_design.md`` §5.5。

api-server のプロセス内でコードを実行しない（SB3）。実行は ``simrunner/server.py``
（ネットワーク無し・時間 / メモリ上限付きのコンテナ）へ ``POST {base_url}/run`` で依頼する。
HTTP は ``transport`` 注入で差し替えられる（テストはネットワークを出さない）。

runner の応答契約: ``{"ok": bool, "outputs": {...}, "error": "...", "stderr_tail": "..."}``。

stdlib ``urllib`` のみ（httpx 等に依存しない）。FastAPI / sqlalchemy / LLM 非 import。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable

from core.simulation_bridge import schema
from core.text_hygiene import strip_control_sequences

__all__ = ["SandboxClient", "SandboxUnreachable", "sandbox_client_from_settings"]

#: エラー文の上限（runner の stderr をそのまま DTO に流さない）。
_ERROR_MAX_CHARS = 500

Transport = Callable[[dict], dict]


class SandboxUnreachable(Exception):
    """runner へ到達できない（接続拒否・名前解決失敗・タイムアウト）。transport が投げてよい。"""


def _clip(text: Any) -> str:
    cleaned = strip_control_sequences(str(text or ""))
    return cleaned[:_ERROR_MAX_CHARS]


class SandboxClient:
    """sim-runner への同期クライアント。``run()`` は例外を外に出さない。"""

    def __init__(self, base_url: str, *, timeout_s: float, transport: Transport | None = None) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout_s = float(timeout_s) if timeout_s and float(timeout_s) > 0 else 20.0
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    # ------------------------------------------------------------------
    def _default_transport(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/run",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # runner 側の子プロセス上限 + 通信の余裕。
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s + 5.0) as response:  # noqa: S310 (宛先は設定値のみ)
                raw = response.read()
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                detail = ""
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise SandboxUnreachable(str(exc)) from exc
        if not (200 <= int(status) < 300):
            raise RuntimeError(f"HTTP {status}")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"応答を JSON として読めません: {exc}") from exc
        return decoded

    # ------------------------------------------------------------------
    def run(
        self,
        source: str,
        *,
        entrypoint: str,
        inputs: dict,
        params: dict,
        seed: int | None = None,
    ) -> schema.SandboxResult:
        if not self.configured:
            return schema.SandboxResult(
                status=schema.RUN_STATUS_FAILED,
                error="not_configured",
                facts=[schema.FACT_SANDBOX_NOT_CONFIGURED],
            )
        payload = {
            "source": source or "",
            "entrypoint": entrypoint,
            "inputs": inputs or {},
            "params": params or {},
            "seed": seed,
            "timeout_s": self.timeout_s,
        }
        transport = self._transport or self._default_transport
        try:
            response = transport(payload)
        except (SandboxUnreachable, socket.timeout, TimeoutError, ConnectionError) as exc:
            return schema.SandboxResult(
                status=schema.RUN_STATUS_FAILED,
                error=_clip(f"unreachable: {exc}"),
                facts=[schema.FACT_SANDBOX_UNREACHABLE],
            )
        except Exception as exc:  # noqa: BLE001 — 非 2xx・壊れた応答
            return schema.SandboxResult(status=schema.RUN_STATUS_FAILED, error=_clip(exc))

        if not isinstance(response, dict):
            return schema.SandboxResult(status=schema.RUN_STATUS_FAILED, error="実行環境の応答の形が不正です。")
        if response.get("ok") is True:
            outputs = response.get("outputs")
            if not isinstance(outputs, dict):
                return schema.SandboxResult(
                    status=schema.RUN_STATUS_FAILED,
                    error="実行結果の outputs が辞書ではありません。",
                )
            return schema.SandboxResult(status=schema.RUN_STATUS_SUCCEEDED, outputs=outputs)
        error = response.get("error") or "実行に失敗しました。"
        tail = response.get("stderr_tail") or ""
        text = f"{error}\n{tail}".strip() if tail else str(error)
        return schema.SandboxResult(status=schema.RUN_STATUS_FAILED, error=_clip(text))


def sandbox_client_from_settings(settings: Any, *, transport: Transport | None = None) -> SandboxClient:
    """``Settings`` の ``sim_runner_url`` / ``simulation_run_timeout_seconds`` から組み立てる。"""
    base_url = str(getattr(settings, "sim_runner_url", "") or "")
    try:
        timeout_s = float(getattr(settings, "simulation_run_timeout_seconds", 20) or 20)
    except (TypeError, ValueError):
        timeout_s = 20.0
    return SandboxClient(base_url, timeout_s=timeout_s, transport=transport)
