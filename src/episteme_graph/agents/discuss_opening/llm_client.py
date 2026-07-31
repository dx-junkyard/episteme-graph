"""LLM client for DiscussOpeningAgent — 8th consumer of ``core/llm_worker/``.

This is a thin **adapter** (not a copy) onto
``core.llm_worker.client.BaseJSONLLMClient``: one call = one document, one user
message, JSON out, U層 (usage metering) accounting inherited from
``core.llm.generate_text``. The骨格 (repair loop / cost gate) comes from the same
package — see ``repair.py`` and the orchestrator's ``CostGate``.

The import of ``core.llm_worker`` is deliberately **lazy**: ``backend/`` is not
on ``sys.path`` when the agent test-suite under ``src/tests/`` runs on its own,
and every other agent in this package follows the same rule (see
``episteme_graph.agents.llm_json_client``). Tests replace ``complete_json``.
"""
from __future__ import annotations

# Settings 属性名の規約（core/llm_worker/client.py::resolve_model）。専用の Settings
# フィールドは持たないため（``DISCUSS_OPENING_LLM_MODEL`` を読む正本は M層
# ``core/llm_policy.py::_FEATURE_DIRECT_ENV``）、モデル名は呼び出し側
# （orchestrator の ``_discuss_opening_model()``）が解決して渡す。
MODEL_SETTING_KEY = "discuss_opening_llm_model"


class DiscussOpeningLLMClient:
    """``BaseJSONLLMClient`` への薄い委譲 + 呼び出し回数の計測。"""

    def __init__(self, model: str | None = None) -> None:
        self._model = model
        self._impl = None
        self.calls = 0

    @property
    def model(self) -> str | None:
        return self._model

    def complete_json(self, content: str) -> dict:
        if self._impl is None:
            from core.llm_worker.client import BaseJSONLLMClient

            self._impl = BaseJSONLLMClient(MODEL_SETTING_KEY, model=self._model)
        self.calls += 1
        return self._impl.complete_json(content)
