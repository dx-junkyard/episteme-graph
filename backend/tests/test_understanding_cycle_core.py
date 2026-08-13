"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 — core/cycle/derive.py の純関数テスト。

対象仕様: docs/features/understanding_cycle_design.md §4/§5（UC1〜UC10）。
DB に触れない fake rows（dict）だけで単体テストする
（core/personal_graph/derive.py と同じ「純粋関数と DB 読み出しの分離」方針）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core.cycle.derive import (  # noqa: E402
    build_intention_dto,
    build_landing_candidates,
    build_revisit_facts,
)


def _tension_row(trace_id: str, text: str, status: str = "articulated") -> dict:
    return {
        "id": trace_id,
        "kind": "tension",
        "status": status,
        "payload": {"text": text, "learner_text": text},
        "created_at": "2026-08-13T00:00:00+00:00",
    }


def _anchor_row(
    trace_id: str, text: str, quick_label: str = "curious",
    revisit: bool = False, anchor_status: str = "active",
) -> dict:
    return {
        "id": trace_id,
        "kind": "anchor_mark",
        "status": "open",
        "payload": {
            "text": text,
            "quick_label": quick_label,
            "revisit": revisit,
            "structure_anchor": {"status": anchor_status},
        },
        "created_at": "2026-08-13T00:00:00+00:00",
    }


def _question_row(
    trace_id: str, text: str, attribution: str = "confirmed",
) -> dict:
    return {
        "id": trace_id,
        "kind": "question",
        "status": "open",
        "payload": {
            "text": text,
            "structure_anchor": {"attribution_source": attribution},
        },
        "created_at": "2026-08-13T00:00:00+00:00",
    }


_CARRYOVER = {"id": "carry-1", "text": "これはなぜ成り立つのか", "created_at": "2026-08-12T00:00:00+00:00"}


class TestBuildRevisitFacts:
    def test_no_carryover_returns_empty(self):
        rows = [_tension_row("t1", "気になった点")]
        assert build_revisit_facts(None, rows) == []

    def test_empty_rows_returns_empty(self):
        assert build_revisit_facts(_CARRYOVER, []) == []

    def test_tension_fact_wording(self):
        facts = build_revisit_facts(_CARRYOVER, [_tension_row("t1", "境界条件の扱いが気になる")])
        assert len(facts) == 1
        assert "境界条件の扱いが気になる" in facts[0]
        assert "言葉にしています" in facts[0]

    def test_anchor_mark_fact_includes_quick_label(self):
        facts = build_revisit_facts(
            _CARRYOVER, [_anchor_row("a1", "この式の導出", quick_label="not_yet")]
        )
        assert len(facts) == 1
        assert "この式の導出" in facts[0]
        assert "印を残しています" in facts[0]
        assert "まだ分からない" in facts[0]

    def test_question_fact_wording(self):
        facts = build_revisit_facts(_CARRYOVER, [_question_row("q1", "この仮定は妥当か")])
        assert len(facts) == 1
        assert "この仮定は妥当か" in facts[0]
        assert "問いを確定しています" in facts[0]

    def test_caps_at_three_facts(self):
        rows = [_tension_row(f"t{i}", f"引っかかり{i}") for i in range(5)]
        facts = build_revisit_facts(_CARRYOVER, rows)
        assert len(facts) == 3

    def test_truncates_long_text_with_ellipsis(self):
        long_text = "あ" * 100
        facts = build_revisit_facts(_CARRYOVER, [_tension_row("t1", long_text)])
        assert len(facts) == 1
        assert "…" in facts[0]
        assert ("あ" * 100) not in facts[0]

    def test_skips_dismissed_anchor(self):
        rows = [_anchor_row("a1", "消えたはず", anchor_status="dismissed")]
        assert build_revisit_facts(_CARRYOVER, rows) == []

    def test_skips_candidate_question(self):
        rows = [_question_row("q1", "未確定の問い", attribution="llm_candidate")]
        assert build_revisit_facts(_CARRYOVER, rows) == []

    def test_skips_non_articulated_tension(self):
        rows = [_tension_row("t1", "候補のまま", status="candidate")]
        assert build_revisit_facts(_CARRYOVER, rows) == []

    def test_no_numeric_count_language(self):
        """UC9: 件数・率のような数値文言を組み立てない。"""
        rows = [_tension_row(f"t{i}", f"引っかかり{i}") for i in range(3)]
        facts = build_revisit_facts(_CARRYOVER, rows)
        for f in facts:
            assert "件" not in f
            assert "%" not in f


class TestBuildLandingCandidates:
    def test_empty_rows_returns_empty(self):
        assert build_landing_candidates([]) == []

    def test_candidate_shape_is_minimal(self):
        rows = [_tension_row("t1", "残しておきたい問い")]
        candidates = build_landing_candidates(rows)
        assert len(candidates) == 1
        assert set(candidates[0].keys()) == {"trace_id", "kind", "label", "revisit"}

    def test_priority_order(self):
        rows = [
            _question_row("q1", "確認済みの問い"),
            _anchor_row("a1", "あとで見る箇所", quick_label="return_later", revisit=False),
            _tension_row("t1", "自分の言葉での理解"),
            _anchor_row("a2", "戻りたい箇所", quick_label="return_later", revisit=True),
        ]
        candidates = build_landing_candidates(rows)
        kinds_with_revisit = [(c["kind"], c["revisit"]) for c in candidates]
        # revisit=true anchor_mark が先頭、次に articulated tension、
        # 次にその他の anchor_mark、最後に question。
        assert kinds_with_revisit[0] == ("anchor_mark", True)
        assert kinds_with_revisit[1] == ("tension", False)
        assert kinds_with_revisit[2] == ("anchor_mark", False)
        assert kinds_with_revisit[3] == ("question", False)

    def test_limit_applied(self):
        rows = [_tension_row(f"t{i}", f"問い{i}") for i in range(10)]
        candidates = build_landing_candidates(rows, limit=5)
        assert len(candidates) == 5

    def test_excludes_dismissed_anchor(self):
        rows = [_anchor_row("a1", "消えた", anchor_status="dismissed")]
        assert build_landing_candidates(rows) == []

    def test_excludes_candidate_question(self):
        rows = [_question_row("q1", "未確定", attribution="llm_candidate")]
        assert build_landing_candidates(rows) == []

    def test_excludes_non_articulated_tension(self):
        rows = [_tension_row("t1", "まだ候補", status="candidate")]
        assert build_landing_candidates(rows) == []

    def test_no_numeric_keys_in_candidates(self):
        rows = [_tension_row("t1", "問い")]
        candidates = build_landing_candidates(rows)
        for c in candidates:
            assert "confidence" not in c
            assert "load_score" not in c
            assert "score" not in c


class TestBuildIntentionDto:
    def test_first_time_no_carryover(self):
        dto = build_intention_dto(None, False)
        assert dto == {"carryover": None, "has_motive": False}

    def test_returning_with_carryover(self):
        dto = build_intention_dto(_CARRYOVER, True)
        assert dto == {
            "carryover": {
                "trace_id": "carry-1",
                "text": "これはなぜ成り立つのか",
                "created_at": "2026-08-12T00:00:00+00:00",
            },
            "has_motive": True,
        }

    def test_has_motive_without_carryover(self):
        """intention 痕跡はあるが carryover は無い（motive のみ既出）場合。"""
        dto = build_intention_dto(None, True)
        assert dto["carryover"] is None
        assert dto["has_motive"] is True

    def test_no_numeric_keys(self):
        dto = build_intention_dto(_CARRYOVER, True)
        assert "confidence" not in dto
        assert "load_score" not in dto
        assert "score" not in dto
