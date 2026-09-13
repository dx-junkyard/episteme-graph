"""暗黙前提の正規化（D2-2） — LLM 呼び出しの薄いラッパ。

実体は core/llm_worker/client.py の BaseJSONLLMClient（規約・モデル解決はそちらが正本）。
設定キーは core/doubt/assumption_mining/system.py の WorkerSystem 宣言が正本で、このクラスは
「テストがモックに差し替える名前」を保つためのサブクラス。
"""

from __future__ import annotations

from core.doubt.assumption_mining.system import SYSTEM
from core.llm_worker.client import BaseJSONLLMClient

_MODEL_SETTING_KEY = SYSTEM.model_setting_key

__all__ = ["AssumptionLLMClient"]


class AssumptionLLMClient(BaseJSONLLMClient):
    """1コール=1クラスタ。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
