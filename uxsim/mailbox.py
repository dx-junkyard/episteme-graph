"""ファイル受け渡しの「頭脳」接続（mailbox）。

ローカルのテストで OpenAI のトークンを消費する代わりに、Claude Code のセッション（子エージェント）が
LLM の役を肩代わりする経路。呼び出し側（ペルソナ側 LLM・製品側 proxy）は 1 要求 = 1 ファイル
``req-<seq>.json`` を置いて ``res-<seq>.json`` を待つ。応える側（Claude Code の子エージェント）は
要求を読んで応答ファイルを書く。外部 API を呼ばない。

要求の形: {"seq", "kind": "persona"|"product", "created_at", ...kind 固有}
  persona: {"system", "messages", "schema_hint"}            → 応答 {"output": {...JSON...}}
  product: {"model", "messages", "response_format", "note"} → 応答 {"content": "<文字列>"}
応答に {"error": "..."} を書けば呼び出し側で失敗として扱う。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_TIMEOUT_S = 45 * 60
_lock = threading.Lock()


class MailboxTimeout(TimeoutError):
    pass


class MailboxError(RuntimeError):
    pass


class Mailbox:
    def __init__(self, root: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s

    def _next_seq(self) -> int:
        with _lock:
            counter = self.root / ".seq"
            n = int(counter.read_text()) + 1 if counter.is_file() else 1
            counter.write_text(str(n))
            return n

    def ask(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        seq = self._next_seq()
        req = self.root / f"req-{seq:05d}.json"
        res = self.root / f"res-{seq:05d}.json"
        body = {"seq": seq, "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
        tmp = req.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, req)  # 書きかけを読まれないように原子的に置く
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            if res.is_file():
                try:
                    data = json.loads(res.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(0.5)  # 書きかけ
                    continue
                if isinstance(data, dict) and data.get("error"):
                    raise MailboxError(str(data["error"]))
                return data if isinstance(data, dict) else {}
            time.sleep(1.0)
        raise MailboxTimeout(f"mailbox 応答待ちが {self.timeout_s:.0f} 秒を超えた: {req.name}")


def default_root() -> Path:
    return Path(os.environ.get("UXSIM_MAILBOX_DIR") or (Path(__file__).resolve().parent / "runs" / "mailbox"))
