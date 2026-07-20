"""LLM worker 系統共通の LLM 呼び出しクライアント。

tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining の5系統に90-95%同一のまま存在していた
``<Xxx>LLMClient`` を、設定キー注入のみで差分化する単一実装に集約する。

規約準拠: LLM 呼び出しは必ず core/llm.py の公開 API 経由（ベンダ SDK 直接利用禁止）。
`system` ロール・`temperature` は使わず、instruction + 入力を user ロール1本に連結する。
"""

from __future__ import annotations

import json
import re

from core.config import get_settings
from core.llm import generate_text


def resolve_model(model_setting_key: str, *, fallback: str = "fast") -> str:
    """settings.<model_setting_key> があればそれを、無ければ ``fallback`` tier のモデルを使う。

    各系統固有の環境変数名は core/config.py 側の Settings フィールド名
    （model_setting_key として渡される属性名）を通して間接的に解決される。
    このモジュールは系統ごとの環境変数の実名には一切触れない（呼び出し側の
    core/<system>/llm_client.py が Settings のフィールド名だけを注入する）。

    ``fallback`` は空文字時のフォールバック先 tier。``"fast"``（既定、後方互換）は
    ``settings.llm_fast_model``、``"analysis"`` は ``settings.llm_analysis_model`` に
    解決する。それ以外の値は ``ValueError``（正本:
    docs/features/assistant_common_infra_design.md §3。既定を変えずに tier を
    増やすための最小拡張）。
    """
    if fallback == "fast":
        fallback_attr = "llm_fast_model"
    elif fallback == "analysis":
        fallback_attr = "llm_analysis_model"
    else:
        raise ValueError(f"unknown fallback tier: {fallback!r}")

    settings = get_settings()
    return getattr(settings, model_setting_key, "") or getattr(settings, fallback_attr)


def parse_json_response(text: str) -> dict:
    """LLM 応答から JSON を取り出す（markdown フェンス・前後プロースを許容）。

    パース不能なら ValueError（呼び出し側の repair 対象）。
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 前後に説明文が付いた場合は最外の {...} を試す
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise ValueError("LLM output is not valid JSON")


class BaseJSONLLMClient:
    """1コール=1バッチ/1対象。各系統の ``<Xxx>LLMClient`` はこれを model_setting_key
    注入だけで使う（テストではサブクラスをモックに差し替える想定は従来どおり）。
    """

    def __init__(self, model_setting_key: str, model: str | None = None):
        self._model_setting_key = model_setting_key
        self._model = model

    def complete_json(self, content: str) -> dict:
        """user ロール1本で呼び出し、JSON dict を返す。"""
        answer = generate_text(
            messages=[{"role": "user", "content": content}],
            model=self._model or resolve_model(self._model_setting_key),
        )
        return parse_json_response(answer)
