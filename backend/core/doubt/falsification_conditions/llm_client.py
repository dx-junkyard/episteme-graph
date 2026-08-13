"""反証条件候補 — LLM 呼び出しの薄いラッパ（SL-1）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由（ベンダ SDK 直接利用禁止）。
`system` ロール・`temperature` は使わず、instruction + 入力を user ロール1本に連結する。
モデルは fast tier 既定（settings.doubt_falsification_llm_model で上書き可）。

共通実装は core/llm_worker/client.py（BaseJSONLLMClient への薄いアダプタ）。
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response
from core.llm_worker.client import resolve_model as _resolve_model

_MODEL_SETTING_KEY = "doubt_falsification_llm_model"

__all__ = ["resolve_model", "parse_json_response", "FalsificationConditionLLMClient"]


def resolve_model() -> str:
    """DOUBT_FALSIFICATION_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model(_MODEL_SETTING_KEY)


class FalsificationConditionLLMClient(BaseJSONLLMClient):
    """1コール=1対象。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
