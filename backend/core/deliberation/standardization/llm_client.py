"""標準化判定（Phase S）— LLM 呼び出しの薄いラッパ（llm_worker 6系統目アダプタ）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由（ベンダ SDK 直接利用禁止）。
`system` ロール・`temperature` は使わず、instruction + 入力を user ロール1本に連結する。
モデルは fast tier 既定（settings.stdpart_llm_model で上書き可）。

共通実装は core/llm_worker/client.py（tension / structure_anchor / reconstruction /
doubt.scope_candidates / doubt.assumption_mining に続く6系統目としてこれに接続する。
コピペせず model_setting_key 注入のみで差分化する）。
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response
from core.llm_worker.client import resolve_model as _resolve_model

_MODEL_SETTING_KEY = "stdpart_llm_model"

__all__ = ["resolve_model", "parse_json_response", "StandardizationLLMClient"]


def resolve_model() -> str:
    """STDPART_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model(_MODEL_SETTING_KEY)


class StandardizationLLMClient(BaseJSONLLMClient):
    """1コール=1共通部品（library_entry）。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
