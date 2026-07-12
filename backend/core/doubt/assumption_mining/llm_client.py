"""暗黙前提の正規化 — LLM 呼び出しの薄いラッパ（D2-2）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由。
`system` ロール・`temperature` は使わず user ロール1本。
モデルは fast tier 既定（settings.doubt_assumption_llm_model で上書き可）。

共通実装は core/llm_worker/client.py（5系統で90-95%同一だったクライアントを集約）。
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response
from core.llm_worker.client import resolve_model as _resolve_model

_MODEL_SETTING_KEY = "doubt_assumption_llm_model"

__all__ = ["resolve_model", "parse_json_response", "AssumptionLLMClient"]


def resolve_model() -> str:
    return _resolve_model(_MODEL_SETTING_KEY)


class AssumptionLLMClient(BaseJSONLLMClient):
    """1コール=1クラスタ。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
