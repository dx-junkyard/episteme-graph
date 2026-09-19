"""LLM worker 共通基盤 — LLM を呼ぶ系統の「組み方」を集約するパッケージ。

出発点は 2026-07 の Tier2 提案6（tension / structure_anchor / reconstruction /
doubt.scope_candidates / doubt.assumption_mining の非同期 worker が持っていた
「LLM 1コール → validation 失敗時の修復再試行（最大2回）→ in-memory コスト上限ゲート」
の骨格の共通化）。2026-09-10 の全件棚卸し
（docs/architecture/agent_inventory_and_refactoring_2026-09-10.md）で、同型の骨格を持つ
残りの系統も次の4モジュールに寄せた。

ドメイン固有のロジック（会話窓構築・claim 選定・台帳ロック・冪等性フラグの持ち場・
DB書き込み・トリガー条件・grounding の中身・429 の文言）は各モジュールに残る。
このパッケージは FastAPI を import しない（``api/quota.py`` が 429 への写像を持つ）。

- ``client.py``: ``BaseJSONLLMClient`` / ``resolve_model`` / ``parse_json_response``
- ``repair.py``: ``run_with_repair`` (validate/build_repair_prompt/on_repair_failed を注入。
  ``call=`` で ``complete_json`` 以外の呼び出し形も受ける)
- ``cost_gate.py``: ``CostGate`` (session+daily) / ``InMemoryCounterGate`` (単一カウンタ)
- ``history.py``: ``window_history`` (チャット型の会話履歴ウィンドウ化。
  正本: docs/features/assistant_common_infra_design.md §2)
- ``system.py``: ``WorkerSystem`` / ``CostSpec`` — 非同期 worker 7系統の宣言的スペック
  （client 生成・gate・run_with_repair 結線・daemon thread 起動を1箇所で決める）
- ``chat_turn.py``: ``structured_turn`` / ``build_turn_messages`` / ``spoken_variant`` /
  ``TurnResult`` — 同期の会話ターン骨格（grounding 注入 → 1 structured コール → degraded）
- ``single_shot.py``: ``extract_json`` / ``json_call`` / ``structured_call`` — 単発呼び出しの
  JSON 抽出（フェンス・最外 ``{...}``・LaTeX バックスラッシュ修復・切り詰め復元）と
  structured → text 降格
- ``embedding.py``: ``embed_with_context`` — U層帰属つき embedding の唯一のラッパ

なお ``src/episteme_graph/agents/llm_step.py``（パイプライン agent の repair ループ）は
**意図的に別実装**（例外で break・``generate(messages)`` プロトコル）。統合しない。
"""

from __future__ import annotations

from core.llm_worker.chat_turn import (
    MATH_DELIMITER_INSTRUCTION,
    SPOKEN_CONTRACT,
    TurnResult,
    build_turn_messages,
    spoken_variant,
    structured_turn,
)
from core.llm_worker.client import BaseJSONLLMClient, parse_json_response, resolve_model
from core.llm_worker.cost_gate import CostGate, InMemoryCounterGate, today_str
from core.llm_worker.embedding import embed_with_context
from core.llm_worker.history import window_history
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair
from core.llm_worker.single_shot import (
    LLMSingleShotError,
    extract_json,
    json_call,
    strip_code_fence,
    structured_call,
)
from core.llm_worker.system import CostSpec, WorkerSystem

__all__ = [
    "BaseJSONLLMClient",
    "parse_json_response",
    "resolve_model",
    "CostGate",
    "InMemoryCounterGate",
    "today_str",
    "MAX_REPAIR_ATTEMPTS",
    "run_with_repair",
    "window_history",
    "CostSpec",
    "WorkerSystem",
    "MATH_DELIMITER_INSTRUCTION",
    "SPOKEN_CONTRACT",
    "TurnResult",
    "build_turn_messages",
    "spoken_variant",
    "structured_turn",
    "LLMSingleShotError",
    "extract_json",
    "json_call",
    "strip_code_fence",
    "structured_call",
    "embed_with_context",
]
