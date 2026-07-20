"""コンポーネント候補生成の「0件」と「失敗」の区別（Admin Copilot 共通基盤整理 Phase 2-F, §4 B2）。

設計書: docs/features/assistant_common_infra_design.md §4

- LLM 呼び出し自体の失敗は `core.component_candidates.CandidateGenerationError` を送出する
  （握りつぶして空配列を返さない）。
- LLM が正常応答して候補が本当に0件の場合は従来どおり空の components を返す（例外にしない）。
- route(`routes/theory_components.py::create_candidates_from_query`)は
  `CandidateGenerationError` を 503 + 事実文の `HTTPException` に変換する。真の0件は
  従来どおり 200 相当（`created=[]`）。

DB 接続は行わず、`_ensure_editable` / `_claims_for_course` / `generate_component_candidates`
を monkeypatch して route 関数を直接呼び出す
（test_cross_layer_notifications.py と同じ monkeypatch 流儀）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# core.component_candidates 単体
# ---------------------------------------------------------------------------


class TestGenerateComponentCandidates:
    def test_llm_exception_raises_candidate_generation_error(self, monkeypatch):
        import core.component_candidates as cc

        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(cc, "generate_text_with_structured_output", _boom)

        with pytest.raises(cc.CandidateGenerationError):
            cc.generate_component_candidates("question", "answer", existing_claims=[])

    def test_llm_success_with_zero_candidates_returns_empty_result(self, monkeypatch):
        """LLM が正常応答して候補が本当に0件のケースは例外にしない。"""
        import core.component_candidates as cc

        empty = cc.CandidateGenerationResult(components=[])
        monkeypatch.setattr(cc, "generate_text_with_structured_output", lambda **kwargs: empty)

        result = cc.generate_component_candidates("question", "answer", existing_claims=[])
        assert result.components == []

    def test_sanitization_still_filters_hallucinated_claim_ids(self, monkeypatch):
        """既存のサニタイズ挙動（backing_claims の実在フィルタ）が壊れていないこと。"""
        import core.component_candidates as cc

        fake = cc.CandidateGenerationResult(
            components=[
                cc.CandidateComponent(
                    name="Something",
                    component_type="not_a_real_type",
                    summary="s",
                    backing_claims=[
                        cc.CandidateClaimLink(claim_id="valid-1", reason="r", confidence=0.9),
                        cc.CandidateClaimLink(claim_id="hallucinated", reason="r", confidence=0.9),
                    ],
                ),
                cc.CandidateComponent(name="", component_type="concept"),  # 名前なしは捨てる
            ]
        )
        monkeypatch.setattr(cc, "generate_text_with_structured_output", lambda **kwargs: fake)

        result = cc.generate_component_candidates(
            "q", "a", existing_claims=[{"id": "valid-1", "text": "claim text"}]
        )
        assert len(result.components) == 1
        comp = result.components[0]
        assert comp.component_type == "concept"
        assert [b.claim_id for b in comp.backing_claims] == ["valid-1"]


# ---------------------------------------------------------------------------
# route: create_candidates_from_query
# ---------------------------------------------------------------------------


class TestCreateCandidatesFromQueryRoute:
    def _setup(self, monkeypatch, *, generate):
        import routes.theory_components as tc

        monkeypatch.setattr(tc, "_ensure_editable", lambda course_id, user: None)
        monkeypatch.setattr(tc, "_claims_for_course", lambda course_id, user, limit=100: [])
        monkeypatch.setattr(tc, "generate_component_candidates", generate)
        return tc

    def test_llm_failure_becomes_503_with_factual_detail(self, monkeypatch):
        import core.component_candidates as cc

        def _raise(*a, **k):
            raise cc.CandidateGenerationError("boom")

        tc = self._setup(monkeypatch, generate=_raise)
        body = tc.CandidateFromQueryRequest(course_id="course-1", question="q", answer_text="a")

        with pytest.raises(tc.HTTPException) as excinfo:
            tc.create_candidates_from_query(body, {"id": "teacher-1", "role": "TEACHER"})

        assert excinfo.value.status_code == 503
        # 数値・内部例外メッセージを漏らさない事実文
        detail = excinfo.value.detail
        assert "候補の生成に失敗しました" in detail

    def test_true_zero_candidates_returns_created_empty(self, monkeypatch):
        import core.component_candidates as cc

        empty = cc.CandidateGenerationResult(components=[])
        tc = self._setup(monkeypatch, generate=lambda *a, **k: empty)
        body = tc.CandidateFromQueryRequest(course_id="course-1", question="q", answer_text="a")

        result = tc.create_candidates_from_query(body, {"id": "teacher-1", "role": "TEACHER"})

        assert result["candidates"] == []
        assert result["course_id"] == "course-1"
