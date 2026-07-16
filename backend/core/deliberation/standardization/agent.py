"""StandardizationAgent — 標準化判定・証拠①（LLM 事前知識）の抽出本体（Phase S）。

LLM 呼び出しは llm_client に分離。出力は validator でスキーマ検証し、失敗時は
repair（最大2回）を実行する。この LLM 呼び出しは出典テキストの書き写しではなく
一般知識の申告なので、対象（library_entry の名前・要約等）さえあれば必ず呼ぶ
（他の worker のような has_sources() 相当の事前スキップ判定は無い — library_entries.name
は NOT NULL のため対象記述は常に非空）。
"""

from __future__ import annotations

from core.deliberation.standardization.input_builder import StandardizationTargetContext
from core.deliberation.standardization.llm_client import StandardizationLLMClient
from core.deliberation.standardization.prompt import build_content
from core.deliberation.standardization.repair import run_with_repair
from core.deliberation.standardization.schema import LLMPriorKnowledge


class StandardizationAgent:
    def __init__(self, llm_client: StandardizationLLMClient | None = None):
        self._llm_client = llm_client or StandardizationLLMClient()

    def run(self, context: StandardizationTargetContext) -> LLMPriorKnowledge:
        content = build_content(context)
        return run_with_repair(self._llm_client, content)
