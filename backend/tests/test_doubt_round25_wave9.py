"""第 25 周 面 B 第 9 波（IK-1075 / IK-1059 残り / IK-1074 / IK-1057 残り / 疑義の重複告知）の固定。

- IK-1075: 学習者に届く部品の文脈は、部品の文脈 API と学習チャットの画面文脈（SA層 kind ``element``）が
  同じ解決点 ``resolve_learner_component_context`` を通る。凍結の ⚓ の後継が決まらなければ、AI の材料にも
  別の live 部品を渡さず「後継が決まっていません」の事実だけを渡す。
- IK-1059 残り: AI に渡る地図の札の説明・わたしの地図の札と条件の行・わたしの地図の画面の事実文を
  学習者の語（``label_vocab.learner_atlas_wording``）に揃える。
- IK-1074: 前提の地図の「計算できない前提」の各行に、被覆の段・論文・記帳の日時の 1 行を添える。
- IK-1057 残り: 段（B）による代理の同定は、手法の選択（method_choice）を観測と数えない。
- 軽: 同じ型・同じ理由の未取り下げの疑義があれば、記帳は止めず事実文で告知する。
"""

from __future__ import annotations

import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
import routes.learning as learning_routes  # noqa: E402
from core import label_vocab  # noqa: E402
from core.assistant_context.resolvers import learning as learning_resolvers  # noqa: E402
from core.assistant_context.schema import (  # noqa: E402
    LEARNING_PERSONAL_MAP_SCREEN_FACT,
    ScreenContext,
)
from core.doubt import observation_targets as ot  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_DENY = label_vocab.ATLAS_LEARNER_WORDING_DENYLIST


def _no_ledger_words(text: str) -> bool:
    return not any(word in str(text) for word in _DENY)


# ---------------------------------------------------------------------------
# IK-1075 — 凍結の ⚓ の後継解決は AI の材料にも掛かる
# ---------------------------------------------------------------------------


class TestIk1075SingleResolutionPoint:
    def test_both_learner_paths_go_through_the_shared_resolver(self):
        api_src = inspect.getsource(learning_routes.get_course_component_context)
        screen_src = inspect.getsource(learning_routes._screen_element_sources)
        for src in (api_src, screen_src):
            assert "resolve_learner_component_context(" in src
            # 解決点を迂回して部品の文脈を直接組まない（後継解決を素通りする経路を作らない）。
            assert "_component_context_with_explanation(" not in src
        shared = inspect.getsource(learning_routes.resolve_learner_component_context)
        assert "_resolve_frozen_component_ref(" in shared
        assert "_superseded_component_payload(" in shared

    def test_superseded_anchor_never_resolves_to_a_live_component(self):
        superseded = {"status": "superseded", "id": "uuid-old", "frozen_label": "EOS network map"}
        with patch.object(learning_routes, "_resolve_frozen_component_ref", return_value=superseded), \
                patch.object(learning_routes, "_component_context_with_explanation") as build:
            out = learning_routes.resolve_learner_component_context(
                {"topics": []}, "comp_001", "course-1", {"doc-1"}, topic_id="t11",
                preferred_document_ids=("doc-1",), user_id="u1",
            )
        build.assert_not_called()
        assert out["available"] is False
        assert out["frozen_label"] == "EOS network map"
        assert "EOS network map" in out["frozen_label_line"]

    def test_screen_element_passes_topic_and_course_to_the_resolver(self):
        ctx = ScreenContext(
            screen="learning",
            selection={"element_type": "component", "element_id": "comp_001"},
        )
        with patch.object(learning_routes, "resolve_learner_component_context",
                          return_value={"available": False, "frozen_label": "X",
                                        "frozen_label_line": "line"}) as resolver, \
                patch.object(learning_routes, "build_topic_evidence_items", return_value=[]):
            out = learning_routes._screen_element_sources(
                ctx, course_id="course-1", topic_info={"id": "t11"},
                course_data={"topics": [{"id": "t11"}]},
                grounding_document_ids={"doc-1"}, preferred_document_ids=("doc-1",),
            )
        args, kwargs = resolver.call_args
        assert args[0] == {"topics": [{"id": "t11"}]}
        assert kwargs["topic_id"] == "t11"
        assert out["context"]["available"] is False

    def test_resolver_tells_the_ai_the_same_fact_as_the_panel(self):
        line = label_vocab.frozen_component_superseded_line("EOS network map", "")
        ctx = ScreenContext(
            screen="learning",
            selection={"element_type": "component", "element_id": "comp_001"},
        )
        sources = {"element": {
            "element_type": "component",
            "context": {"available": False, "frozen_label": "EOS network map",
                        "frozen_label_line": line, "note": "…"},
            "item": {"title": "EOS network map（原文）"},
        }}
        facts = learning_resolvers.resolve_element(ctx, sources)
        assert facts[0] == "学習者が選んでいるのは論理要素「EOS network map」です"
        assert facts[1] == line.rstrip("。")
        # 別の部品の役割・式・検証は言わない（材料に無い）。
        assert len(facts) == 2

    def test_superseded_context_has_no_ledger_target(self):
        """後継の決まらない ⚓ は台帳の対象 ID を持たない → 検証スコープを AI に渡さない。"""
        payload = learning_routes._superseded_component_payload(
            {"frozen_label": "EOS network map"}, "note"
        )
        assert "component_id" not in payload and "element_id" not in payload


# ---------------------------------------------------------------------------
# IK-1059 残り — AI の材料とわたしの地図の札・条件も学習者の語
# ---------------------------------------------------------------------------


class TestIk1059RemainingExits:
    def test_pill_note_given_to_the_ai_uses_learner_words(self):
        for status in label_vocab.ATLAS_PILL_NOTES:
            ctx = ScreenContext(screen="learning", view={
                "atlas_node_label": "暗黒エネルギー", "atlas_node_status": status,
            })
            facts = learning_resolvers._atlas_node_facts(ctx.view, {})
            assert facts, status
            assert all(_no_ledger_words(f) for f in facts), (status, facts)

    def test_personal_map_screen_fact_uses_learner_words(self):
        assert _no_ledger_words(LEARNING_PERSONAL_MAP_SCREEN_FACT)
        assert "記録された条件" in LEARNING_PERSONAL_MAP_SCREEN_FACT

    def test_personal_map_pill_and_scope_line_use_learner_words(self):
        from core.doubt.schema import (
            LEARNER_SCOPE_LINE_MORE,
            LEARNER_SCOPE_LINE_PREFIX,
            learner_scope_line,
        )
        from core.personal_graph.nearby import _verification
        from core.personal_graph.queries import ComponentLedgerStatuses

        assert _no_ledger_words(LEARNER_SCOPE_LINE_PREFIX)
        assert _no_ledger_words(LEARNER_SCOPE_LINE_MORE)
        line = learner_scope_line("directly_verified", [{"condition": "単調な範囲"}])
        assert line.startswith("記録された条件: ")
        for status in label_vocab.VERIFICATION_STATUS_LABELS_LEDGER:
            ledger = ComponentLedgerStatuses({"n1": status})
            ledger.scope_lines["n1"] = "記帳された条件: 領域「分子雲」"  # 旧い文が残っても読み替える
            out = _verification("n1", ledger)
            assert out is not None
            assert _no_ledger_words(out["label"]), out
            assert _no_ledger_words(out["scope_line"]), out
        assert _verification("n1", ComponentLedgerStatuses({"n1": "directly_verified"}))["label"] == "直接検証の記録あり"

    def test_teacher_ledger_vocabulary_is_unchanged(self):
        # 教員の台帳画面の語（宛先差）は変えない。
        assert label_vocab.VERIFICATION_STATUS_LABELS_LEDGER["directly_verified"] == "直接検証の記帳あり"


# ---------------------------------------------------------------------------
# IK-1074 — 計算できない前提の行に被覆・論文・日時
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _Session:
    def __init__(self, created):
        self._created = created

    def execute(self, *_a, **_k):
        return _Rows(self._created)

    def rollback(self):
        pass


class TestIk1074UnplottedDistinguishers:
    def test_detail_line_carries_coverage_paper_and_time(self):
        line = label_vocab.assumption_unplotted_detail_line(
            "1件", "Cepheus の磁場", datetime(2026, 10, 27, 3, 5, tzinfo=timezone.utc)
        )
        assert line == "検証スコープの被覆: 1件 ／ 論文『Cepheus の磁場』 ／ 記帳: 2026-10-27 12:05"

    def test_same_text_assumptions_are_told_apart(self):
        unplotted = [
            {"target_id": "a1", "target_type": "assumption", "document_id": "d1", "scope_coverage": "none"},
            {"target_id": "a2", "target_type": "assumption", "document_id": "d1", "scope_coverage": "single"},
        ]
        session = _Session([
            ("a1", datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)),
            ("a2", datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)),
        ])
        with patch.object(doubt, "_document_title_entry", return_value={"document_title": "Cepheus"}):
            doubt._attach_unplotted_detail_lines(session, unplotted)
        a1, a2 = (p["detail_line"] for p in unplotted)
        assert a1 != a2
        assert "記帳なし" in a1 and "1件" in a2
        assert "論文『Cepheus』" in a1

    def test_route_and_screen_draw_the_line(self):
        assert "_attach_unplotted_detail_lines(session, unplotted)" in inspect.getsource(doubt.get_assumption_atlas)
        assert "data-doubt-unplotted-detail" in _ATLAS_JS and "p.detail_line" in _ATLAS_JS
        assert re.search(r"doubt-atlas\.js\?v=round25-doubt-202610\d\d-doubt(?:9|1\d)\b", _ADMIN_HTML)


# ---------------------------------------------------------------------------
# IK-1057 残り — 段の代理は手法の選択を観測と数えない
# ---------------------------------------------------------------------------


class TestIk1057MethodChoice:
    def test_stage_proxy_drops_method_choice(self):
        assert ot._counts_as_observation(("claim_object", "method_choice"), ot._IDENTIFIED_VIA_STAGE) is False
        # 主張自身の記述（C 段・DSL）は外さない。解釈・比較は従来どおり。
        assert ot._counts_as_observation(("claim_object", "method_choice"), ot._IDENTIFIED_VIA_DSL) is True
        assert ot._counts_as_observation(("claim_object", "interpretation"), ot._IDENTIFIED_VIA_STAGE) is True


# ---------------------------------------------------------------------------
# 軽 — 同じ型・同じ理由の疑義の告知（記帳は止めない）
# ---------------------------------------------------------------------------


class TestDuplicateChallengeNote:
    def test_note_is_a_fact_without_numbers(self):
        note = label_vocab.CHALLENGE_DUPLICATE_NOTE
        assert "記帳しました" in note
        assert not any(ch.isdigit() for ch in note)

    def test_route_and_screen_wire_the_note(self):
        src = inspect.getsource(doubt.create_challenge)
        assert "duplicate_note" in src and "status <> 'withdrawn'" in src
        assert "data.duplicate_note" in _ATLAS_JS
