"""item オーサリング用 LLM 呼び出しの薄いラッパ（オーサリングのみ・非同期 worker から使用）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由（ベンダ SDK 直接利用禁止）。
`system` ロール・`temperature` は使わず、instruction + 入力を user ロール1本に連結する。
モデルは fast tier 既定（settings.recon_llm_model で上書き可）。

共通実装は core/llm_worker/client.py（5系統で90-95%同一だったクライアントを集約）。
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response
from core.llm_worker.client import resolve_model as _resolve_model

_MODEL_SETTING_KEY = "recon_llm_model"

__all__ = ["resolve_model", "parse_json_response", "ReconstructionLLMClient"]


def resolve_model() -> str:
    """RECON_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model(_MODEL_SETTING_KEY)


class ReconstructionLLMClient(BaseJSONLLMClient):
    """1コール=1 claim。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
