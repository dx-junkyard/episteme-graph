"""暗黙前提の正規化 — LLM 呼び出しの薄いラッパ（D2-2）。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由。
`system` ロール・`temperature` は使わず user ロール1本。
モデルは fast tier 既定（settings.doubt_assumption_llm_model で上書き可）。
"""

from __future__ import annotations

import json
import re

from core.config import get_settings
from core.llm import generate_text


def resolve_model() -> str:
    settings = get_settings()
    return getattr(settings, "doubt_assumption_llm_model", "") or settings.llm_fast_model


def parse_json_response(text: str) -> dict:
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


class AssumptionLLMClient:
    """1コール=1クラスタ。テストではこのクラスをモックに差し替える。"""

    def __init__(self, model: str | None = None):
        self._model = model

    def complete_json(self, content: str) -> dict:
        answer = generate_text(
            messages=[{"role": "user", "content": content}],
            model=self._model or resolve_model(),
        )
        return parse_json_response(answer)
