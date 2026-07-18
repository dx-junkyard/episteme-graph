"""LLM worker 系統共通のコスト制御プリミティブ（in-memory カウンタ）。

tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining の5系統に ``_check_and_count_llm_call`` という同名関数が
別々に定義されていた「session上限 + daily上限のプロセス内カウンタ」を集約する。

API サーバーは単一プロセス運用のため in-memory で足りる（再起動でリセットされる。
上限は安全側の防波堤であり厳密な会計ではない）。環境変数名・既定値はこのモジュールに
持ち込まない — 各 worker.py が settings から解決した数値・キーを渡す。
"""

from __future__ import annotations

import datetime
import threading
from typing import Hashable


def today_str() -> str:
    return datetime.date.today().isoformat()


class InMemoryCounterGate:
    """任意のキーに対する単純な in-memory 回数制限ゲート。

    LLM コール数以外の「セッション内〇回まで」（例: structure_anchor の
    帰属確認プロンプト表示回数）も同じ形なので、CostGate とは独立に単体でも使う。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counts: dict[Hashable, int] = {}

    def check_and_count(self, key: Hashable, limit: int, *, prune_others: bool = False) -> bool:
        """limit 未満なら True を返しカウントを進める。上限到達なら False。"""
        with self._lock:
            if self.counts.get(key, 0) >= limit:
                return False
            if prune_others:
                for stale in [k for k in self.counts if k != key]:
                    self.counts.pop(stale, None)
            self.counts[key] = self.counts.get(key, 0) + 1
        return True


class CostGate:
    """LLM 呼び出しのコスト上限ゲート（session上限 + daily上限の2段）。

    ``session_limit`` / ``session_key`` を渡さなければ daily 上限のみのチェックになる
    （reconstruction / doubt.scope_candidates / doubt.assumption_mining の形）。
    tension / structure_anchor は両方を渡す。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.session_counts: dict[Hashable, int] = {}
        self.daily_counts: dict[Hashable, int] = {}

    def check_and_count(
        self,
        *,
        daily_limit: int,
        daily_key: Hashable,
        session_limit: int | None = None,
        session_key: Hashable | None = None,
        prune_stale_daily: bool = False,
    ) -> bool:
        """上限内なら True を返しカウントを進める（session→daily の順にチェック）。

        ``prune_stale_daily=True`` は daily_key 以外の古い日付キーを増分前に破棄する
        （doubt.scope_candidates / doubt.assumption_mining の既存挙動＝メモリリーク防止）。
        """
        with self._lock:
            if session_limit is not None and self.session_counts.get(session_key, 0) >= session_limit:
                return False
            if self.daily_counts.get(daily_key, 0) >= daily_limit:
                return False
            if prune_stale_daily:
                for stale in [k for k in self.daily_counts if k != daily_key]:
                    self.daily_counts.pop(stale, None)
            if session_limit is not None:
                self.session_counts[session_key] = self.session_counts.get(session_key, 0) + 1
            self.daily_counts[daily_key] = self.daily_counts.get(daily_key, 0) + 1
        return True

    def daily_remaining(self, *, daily_limit: int, daily_key: Hashable) -> int:
        """daily_key の残回数（負にならない）。読み取りのみでカウントは進めない。"""
        with self._lock:
            used = self.daily_counts.get(daily_key, 0)
        return max(0, daily_limit - used)

    def count_extra_daily(self, *, daily_key: Hashable, amount: int) -> None:
        """事後の実測計上。チェックせず daily カウンタへ amount を加算する
        （上限を跨いだ実測も正直に記録する会計用。amount<=0 は no-op）。"""
        if amount <= 0:
            return
        with self._lock:
            self.daily_counts[daily_key] = self.daily_counts.get(daily_key, 0) + amount
