"""PaperSkeletonLLMClient: OpenAI JSON mode で skeleton を生成する。

- backend/core/llm.py が利用できない src/ 環境向けのスタンドアロン実装。
- 環境変数 LLM_API_KEY / OPENAI_API_KEY からキーを取得。
- Reasoning model (o1/o3/o4) では system → developer ロール変換を行う。
- temperature / max_tokens は reasoning model では除去する（CLAUDE.md 規約）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Prefixes that identify reasoning models requiring special handling
_REASONING_PREFIXES = ("o1", "o3", "o4")


class PaperSkeletonLLMClient:
    """JSON mode で LLM 呼び出しを行うクライアント。

    テスト時は generate() を mock する。
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = (
            model
            or os.environ.get("LLM_ANALYSIS_MODEL")
            or "o3-mini"
        )
        self._api_key = (
            api_key
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    @property
    def model(self) -> str:
        return self._model

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> dict:
        """LLM を呼び出して JSON dict を返す。

        Parameters
        ----------
        messages:
            [{"role": "system"|"user", "content": "..."}] 形式。
        response_schema:
            現在は参照のみ（JSON mode に切り替える際のヒント）。

        Returns
        -------
        dict
            LLM が返した JSON をパースしたもの。
        """
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai package is required for PaperSkeletonLLMClient") from exc

        client = openai.OpenAI(api_key=self._api_key)
        adapted = self._adapt_messages(messages)
        kwargs = self._build_kwargs()

        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=adapted,
                response_format={"type": "json_object"},
                **kwargs,
            )
        except openai.BadRequestError:
            # Some reasoning models don't support response_format json_object
            response = client.chat.completions.create(
                model=self._model,
                messages=adapted,
                **kwargs,
            )

        raw_text = response.choices[0].message.content or "{}"
        return self._parse_json(raw_text)

    # ------------------------------------------------------------------

    def _is_reasoning_model(self) -> bool:
        return any(self._model.startswith(p) for p in _REASONING_PREFIXES)

    def _adapt_messages(self, messages: list[dict]) -> list[dict]:
        """Reasoning model では system → developer に変換する。"""
        if not self._is_reasoning_model():
            return messages
        adapted = []
        for m in messages:
            if m.get("role") == "system":
                adapted.append({**m, "role": "developer"})
            else:
                adapted.append(m)
        return adapted

    def _build_kwargs(self) -> dict[str, Any]:
        """Reasoning model では temperature / max_tokens を除外する。"""
        if self._is_reasoning_model():
            return {}
        return {}  # future: add temperature / max_tokens for non-reasoning models

    @staticmethod
    def _parse_json(text: str) -> dict:
        """JSON テキストをパースし、失敗したら空 dict を返す。"""
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM JSON output: %s", exc)
            return {}
