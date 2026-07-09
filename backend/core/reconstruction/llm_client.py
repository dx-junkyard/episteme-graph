"""item オーサリング用 LLM 呼び出しの薄いラッパ（オーサリングのみ・非同期 worker から使用）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由（ベンダ SDK 直接利用禁止）。
`system` ロール・`temperature` は使わず、instruction + 入力を user ロール1本に連結する。
モデルは fast tier 既定（settings.recon_llm_model で上書き可）。
"""

from __future__ import annotations

import json
import re

from core.config import get_settings
from core.llm import generate_text


def resolve_model() -> str:
    """RECON_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    settings = get_settings()
    return getattr(settings, "recon_llm_model", "") or settings.llm_fast_model


def parse_json_response(text: str) -> dict:
    """LLM 応答から JSON を取り出す（markdown フェンス・前後プロースを許容）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise ValueError("LLM output is not valid JSON")


class ReconstructionLLMClient:
    """1コール=1 claim。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        self._model = model

    def complete_json(self, content: str) -> dict:
        answer = generate_text(
            messages=[{"role": "user", "content": content}],
            model=self._model or resolve_model(),
        )
        return parse_json_response(answer)
