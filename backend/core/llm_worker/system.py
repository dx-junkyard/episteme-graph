"""LLM worker 系統の宣言的スペック（WorkerSystem）。

``client.py`` / ``repair.py`` / ``cost_gate.py`` は骨格の**部品**を提供するが、
各系統（7系統）はその部品を**同じ順序・同じ組み方**で毎回組み立てる
糊コードを各々100〜150行ずつ持っていた（クライアント生成・修復ループ呼び出し・
コスト上限ゲート・デーモンスレッド起動）。このモジュールはその「組み方」だけを
1つのデータ宣言に集約する。

各系統は ``core/<system>/system.py`` に

    SYSTEM = WorkerSystem(
        name="<系統名>",
        model_setting_key="<Settings のモデル設定フィールド名>",
        feature="<U層の feature 文字列>",
        log_label="<ログ用の表示名>",
        cost=CostSpec(day_setting="<日次上限の設定フィールド名>", day_default=10),
    )

を1つ置き、llm_client / repair / worker はそれを参照するだけの薄い層になる。

**ドメイン語彙はここに持ち込まない**（環境変数名・設定フィールド名・feature 文字列は
すべて各系統の ``system.py`` が保持する文字列であり、このモジュールは受け取った値を
そのまま使うだけ）。既存ガードレール
（``tests/test_llm_worker_guardrails.py::TestNoEnvVarNameLeakage``）と同じ規律。

このモジュールは FastAPI を import しない。``core.config`` は
「呼び出し側が settings を渡さなかったときだけ」遅延 import する（テストが各 worker の
``get_settings`` を monkeypatch する既存の継ぎ目を壊さないため、通常は呼び出し側が
自分の名前空間の ``get_settings()`` の結果を渡す）。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable

from core.llm_worker.client import BaseJSONLLMClient
from core.llm_worker.cost_gate import CostGate, today_str
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair

logger = logging.getLogger(__name__)

__all__ = ["CostSpec", "WorkerSystem"]


@dataclass(frozen=True)
class CostSpec:
    """1系統ぶんのコスト上限の宣言（in-memory カウンタの読み方）。

    Attributes
    ----------
    day_setting / day_default:
        1日あたり上限の Settings フィールド名と、設定が無いときの既定値。
    session_setting / session_default:
        セッション上限（学習者セッション単位の上限を持つ系統のみ）。
        ``None`` なら日次上限だけを見る。
    prune_stale_daily:
        現在の daily_key 以外の（＝過去日の）カウンタを増分前に破棄する
        （プロセス常駐でのメモリリーク防止）。**daily_key にユーザー等の識別子を
        含める系統では False にする** — 他ユーザーの当日カウンタまで消してしまうため
        （既定は安全側の False）。
    """

    day_setting: str
    day_default: int
    session_setting: str | None = None
    session_default: int = 0
    prune_stale_daily: bool = False

    def limits(self, settings: Any) -> tuple[int, int | None]:
        """(daily_limit, session_limit) を settings から解決する。"""
        daily = int(getattr(settings, self.day_setting, self.day_default))
        if self.session_setting is None:
            return daily, None
        return daily, int(getattr(settings, self.session_setting, self.session_default))


@dataclass
class WorkerSystem:
    """1系統ぶんの骨格スペック。ドメインロジック（SQL・冪等マーカー・トリガー条件・
    確定/候補の意味論）は一切持たない。"""

    name: str
    model_setting_key: str
    feature: str
    cost: CostSpec
    log_label: str = ""
    gate: CostGate = field(default_factory=CostGate, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.log_label:
            self.log_label = self.name

    # -- LLM クライアント ---------------------------------------------------

    def client(self, model: str | None = None) -> BaseJSONLLMClient:
        """この系統の設定キーを注入した共通クライアントを返す。

        各系統の ``<Xxx>LLMClient`` は「テストがモックに差し替える名前」を保つための
        サブクラスであり、実体はこの1本に集約されている。
        """
        return BaseJSONLLMClient(model_setting_key=self.model_setting_key, model=model)

    # -- 修復再試行ループ ---------------------------------------------------

    def run(
        self,
        llm_client: Any,
        base_content: str,
        *,
        validate: Callable[[dict], tuple[Any, list[str], list[str]]],
        build_repair_prompt: Callable[[str, list[str]], str],
        on_repair_failed: Callable[[list[str]], Any],
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        call: Callable[[str], Any] | None = None,
    ) -> Any:
        """``core.llm_worker.repair.run_with_repair`` へ log_label を添えて委譲する。

        ``call`` は共通ループ側の拡張（``complete_json`` を持たない呼び出し口の注入）を
        そのまま素通しする。既定（None）は従来どおり ``llm_client.complete_json``。
        """
        extra = {"call": call} if call is not None else {}
        return run_with_repair(
            llm_client,
            base_content,
            validate=validate,
            build_repair_prompt=build_repair_prompt,
            on_repair_failed=on_repair_failed,
            max_attempts=max_attempts,
            log_label=self.log_label,
            **extra,
        )

    # -- コスト上限ゲート ---------------------------------------------------

    def check_and_count(
        self,
        *,
        daily_key: Hashable | None = None,
        session_key: Hashable | None = None,
        settings: Any = None,
        gate: CostGate | None = None,
    ) -> bool:
        """上限内なら True を返しカウントを進める。上限超過なら False（ログ1行）。

        ``settings`` は呼び出し側（worker.py）が自分の名前空間の ``get_settings()`` で
        解決して渡す（テストの monkeypatch の継ぎ目を worker 側に残すため）。
        ``gate`` も同様に worker 側のモジュール属性 ``_cost_gate`` を渡す
        （既存テストがゲートを丸ごと差し替える継ぎ目）。
        """
        if settings is None:  # pragma: no cover - 通常は呼び出し側が渡す
            from core.config import get_settings

            settings = get_settings()
        daily_limit, session_limit = self.cost.limits(settings)
        key = today_str() if daily_key is None else daily_key
        ok = (gate or self.gate).check_and_count(
            daily_limit=daily_limit,
            daily_key=key,
            session_limit=session_limit if session_key is not None else None,
            session_key=session_key,
            prune_stale_daily=self.cost.prune_stale_daily,
        )
        if not ok:
            logger.info(
                "%s skipped: cost cap reached (session=%s, daily=%s)",
                self.log_label, session_key, key,
            )
        return ok

    # -- 非同期スレッド起動 -------------------------------------------------

    def spawn(
        self,
        target: Callable[..., Any],
        *,
        thread_factory: Callable[..., Any],
        args: tuple | None = None,
        thread_name: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """target をデーモンスレッドで起動する。起動できたら True、失敗したら False。

        ``thread_factory`` は呼び出し側（worker.py）が **自分のモジュール名前空間の**
        ``threading.Thread`` を渡す（テストが ``worker.threading.Thread`` を
        monkeypatch する継ぎ目を保つ）。スレッド名は診断用の付加情報なので、
        ``thread_factory`` が ``name`` を受け取らない場合は黙って省略する
        （命名の有無で起動の成否を変えない）。
        """
        thread_kwargs: dict[str, Any] = {"target": target, "daemon": True}
        if args is not None:
            thread_kwargs["args"] = args
        if kwargs:
            thread_kwargs["kwargs"] = kwargs
        name = thread_name or f"{self.name}-worker"
        if _accepts_name(thread_factory):
            thread_kwargs["name"] = name
        try:
            thread_factory(**thread_kwargs).start()
            return True
        except Exception as exc:
            logger.warning("%s: failed to start background thread: %s", self.log_label, exc)
            return False


def _accepts_name(thread_factory: Callable[..., Any]) -> bool:
    """thread_factory が ``name=`` キーワードを受け取れるか（受け取れなければ省略する）。"""
    try:
        params = inspect.signature(thread_factory).parameters
    except (TypeError, ValueError):  # pragma: no cover - シグネチャ不明なら付けない
        return False
    if "name" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
