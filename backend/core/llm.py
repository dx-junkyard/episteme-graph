"""OpenAI client factory for Episteme Graph.

設定はすべて環境変数（.env）から読み込む。

環境変数
--------
OPENAI_API_KEY          OpenAI API キー（必須）
OPENAI_ANALYSIS_MODEL   文章解析・構造抽出に使う reasoning モデル名
                        デフォルト: gpt-4o
OPENAI_EMBEDDING_MODEL  Embedding に使うモデル名
                        デフォルト: text-embedding-3-large
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import json

from dotenv import load_dotenv
from openai import OpenAI

# プロジェクトルートの .env を読み込む（既に設定済みなら上書きしない）
load_dotenv()


@dataclass(frozen=True)
class LLMSettings:
    """環境変数から読み込んだ LLM 設定。"""

    api_key: str
    analysis_model: str
    embedding_model: str

    @classmethod
    def from_env(cls) -> "LLMSettings":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key or api_key.startswith("sk-your"):
            raise EnvironmentError(
                "OPENAI_API_KEY が設定されていません。"
                " .env ファイルに OPENAI_API_KEY=sk-... を追記してください。"
            )
        return cls(
            api_key=api_key,
            analysis_model=os.environ.get("OPENAI_ANALYSIS_MODEL", "gpt-4o"),
            embedding_model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
        )


@lru_cache(maxsize=1)
def get_settings() -> LLMSettings:
    """LLMSettings のシングルトンを返す。"""
    return LLMSettings.from_env()


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """OpenAI クライアントのシングルトンを返す。"""
    settings = get_settings()
    return OpenAI(api_key=settings.api_key)


# ---------------------------------------------------------------------------
# Missing Link Suggestion — LLM prompt engineering
# ---------------------------------------------------------------------------

def generate_missing_link_suggestions(
    pattern_name: str,
    pattern_description: str,
    structural_rules: list[str],
    variables_template: list[str],
    existing_fields: list[str] | None = None,
) -> dict:
    """パターンメタデータを受け取り、構造的空白を検知して分野横断の検索クエリを生成する。

    Returns a dict matching the MissingLinkSuggestion schema (without pattern_id).

    NOTE: Reasoning モデルでは system ロールと temperature/max_tokens を使わない。
    """
    client = get_client()
    settings = get_settings()

    rules_text = "\n".join(f"  - {r}" for r in structural_rules) if structural_rules else "  (none)"
    vars_text = ", ".join(variables_template) if variables_template else "(none)"
    existing_text = ", ".join(existing_fields) if existing_fields else "none known"

    prompt = f"""You are a cross-domain research advisor for the Episteme Graph system.

Given the following abstraction pattern, suggest academic fields where this structural pattern
likely occurs but is NOT yet represented in our pattern library.

## Pattern Information
- **Name**: {pattern_name}
- **Description**: {pattern_description}
- **Abstract Variables**: {vars_text}
- **Structural Rules**:
{rules_text}
- **Fields already covered**: {existing_text}

## Your Task
1. Identify 3-5 academic fields/domains where this same structural pattern likely manifests,
   but which are NOT in the "already covered" list.
2. For each field, explain WHY this pattern would appear there (concrete reasoning, not generic).
3. For each field, provide 2-4 arXiv search keywords that combine the pattern's structural
   concepts with field-specific terminology. Keywords should be specific enough to find relevant
   papers, mixing both generic structural terms and specialized domain terms.

## Output Format (strict JSON)
Return ONLY a JSON object with this structure:
{{
  "suggestions": [
    {{
      "field": "<academic field name>",
      "reasoning": "<1-2 sentences explaining why this pattern appears in this field>",
      "keywords": ["<keyword1>", "<keyword2>", "<keyword3>"]
    }}
  ]
}}

Important:
- Do NOT include fields already covered.
- Keywords must be suitable for arXiv search (English, technical terms).
- Balance generic structural terms with field-specific jargon to mitigate hallucination."""

    response = client.chat.completions.create(
        model=settings.analysis_model,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content or "{}"
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    return json.loads(cleaned)
