"""Stage 2(B) — LLM 呼び出しの薄いラッパ。

実体は core/llm_worker/client.py の BaseJSONLLMClient（規約・モデル解決はそちらが正本）。
設定キーは core/structure_anchor/system.py の WorkerSystem 宣言が正本で、このクラスは
「テストがモックに差し替える名前」を保つためのサブクラス。
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient
from core.structure_anchor.system import SYSTEM

_MODEL_SETTING_KEY = SYSTEM.model_setting_key

__all__ = ["AnchorLLMClient"]


class AnchorLLMClient(BaseJSONLLMClient):
    """1コール=1問いバッチ。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        super().__init__(model_setting_key=_MODEL_SETTING_KEY, model=model)
