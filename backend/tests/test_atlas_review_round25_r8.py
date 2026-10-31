"""第 25 周 面 C（分野の地図の運用）第 8 波の是正（IK-1045 / IK-1055 / IK-1056 + IK-1046・IK-1044 の残り）。

- IK-1045: L2 の選出は、版の改訂で加わった（教員が確定した）関係に上限の半分を先に配り、組ごとに両端を
  そろえて入れる。描かなかった確定済みの関係は事実文で名前を言う（件数なし）。
- IK-1055: 「いま学んでいる場所」は学習者本人の状態（焦点・いまここ・足跡）が効いたときだけ。
  コースのトピックの対応は「このコースのトピックが結ばれている場所」。
- IK-1056: 承認の記録が無い行は状態に依らず 1 つの閉世界の文（旧い短い文は読み時に置き換え）。
- IK-1046 残り: 切り詰めた略（Cep ⇔ Cepheus）も同じものの別表記として注記から外す。
- IK-1044 残り（管理）: 見送りの理由の見出し・下書きなしの未採用カードの案内文。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_state as st  # noqa: E402
from core import label_vocab  # noqa: E402
from core.landscape import schema as ls_schema  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"
ADMIN_JS = JS / "admin.js"
ROUTES = BACKEND / "api" / "routes"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _view():
    from routes import atlas_view

    return atlas_view


# ---------------------------------------------------------------------------
# IK-1045
# ---------------------------------------------------------------------------


class TestConfirmedRelationsReserve:
    def test_relations_survive_many_topic_bindings(self):
        """トピックの対応と配置で上限が埋まるコースでも、確定済みの関係の両端は描かれる（第 8 周の再現）。"""
        v = _view()
        concepts = [f"c{i}" for i in range(40)]
        course_bound = [f"c{i}" for i in range(0, 8)]
        placed = [f"c{i}" for i in range(8, 20)]
        pairs = [("c30", "c31"), ("c32", "c33"), ("c34", "c35")]
        chosen = v.select_l2_concept_ids(
            concepts,
            [],
            leading=["c39"],
            relation_pairs=pairs,
            relation_reserve=10,
            priority=course_bound,
            course_hints=placed,
            limit=20,
        )
        assert len(chosen) == 20
        for a, b in pairs:
            assert a in chosen and b in chosen
        assert "c39" in chosen

    def test_pairs_are_taken_whole_or_not_at_all(self):
        """関係は離散の辺（原則①′）: 片端だけを入れて線の無い点を増やさない。"""
        v = _view()
        concepts = [f"c{i}" for i in range(20)]
        chosen = v.select_l2_concept_ids(
            concepts,
            [],
            relation_pairs=[("c10", "c11"), ("c12", "c13")],
            relation_reserve=3,
            priority=[],
            limit=3,
        )
        # 枠 3: 1 組目（2 点）は入り、2 組目は残り 1 点では入らない → 骨格順で埋まる
        assert {"c10", "c11"} <= chosen
        assert "c12" not in chosen and "c13" not in chosen

    def test_without_new_kwargs_behaves_as_before(self):
        v = _view()
        concepts = [f"c{i}" for i in range(30)]
        chosen = v.select_l2_concept_ids(
            concepts, [("c25", "c3")], priority=["c20"], course_hints=["c25", "c26"], limit=4
        )
        assert chosen == {"c20", "c25", "c26", "c3"}

    def test_undrawn_relations_are_named_without_numbers(self):
        note = label_vocab.atlas_l2_omitted_note(
            [label_vocab.ATLAS_L2_HINT_COURSE_TOPICS],
            [("ブラックホール", "超大質量ブラックホール"), ("惑星形成", "星形成")],
        )
        assert "ブラックホール — 超大質量ブラックホール" in note
        assert "惑星形成 — 星形成" in note
        assert not re.search(r"[0-9０-９]", note)
        many = label_vocab.atlas_l2_undrawn_relations_line(
            [(f"概念{chr(0x3042 + i)}", f"相手{chr(0x3042 + i)}") for i in range(10)]
        )
        assert many.endswith(f"、{label_vocab.ATLAS_L2_UNDRAWN_RELATIONS_MORE}。")
        assert label_vocab.atlas_l2_undrawn_relations_line([]) == ""
        # 描かなかった関係が無ければ従来の文と同じ
        assert label_vocab.atlas_l2_omitted_note(
            [label_vocab.ATLAS_L2_HINT_LEARNING], []
        ) == label_vocab.ATLAS_L2_OMITTED_NOTE

    def test_route_reserves_relations_and_names_undrawn(self):
        src = _read(ROUTES / "atlas_view.py")
        assert "relation_pairs=list(added_pairs)" in src
        assert "relation_reserve=_MAX_L2_NODES // 2" in src
        assert "l2_undrawn_relations" in src
        assert "label_vocab.atlas_l2_omitted_note(\n" in src or "atlas_l2_omitted_note(" in src


# ---------------------------------------------------------------------------
# IK-1055
# ---------------------------------------------------------------------------


class TestL2HintWords:
    def test_course_topics_word_is_separate_from_learning(self):
        note = label_vocab.atlas_l2_omitted_note([label_vocab.ATLAS_L2_HINT_COURSE_TOPICS])
        assert "このコースのトピックが結ばれている場所" in note
        assert label_vocab.ATLAS_L2_HINT_LEARNING not in note

    def test_route_says_learning_only_for_learner_state(self):
        src = _read(ROUTES / "atlas_view.py")
        assert "learner_state_ids = learner_leading + learner_footprints" in src
        assert "if any(cid in l2_selected for cid in learner_state_ids):" in src
        assert "label_vocab.ATLAS_L2_HINT_COURSE_TOPICS" in src
        # course_bound を「いま学んでいる場所」の判定に混ぜない
        assert "learning_priority" not in src


# ---------------------------------------------------------------------------
# IK-1056
# ---------------------------------------------------------------------------


class TestEndorseNoneOneSentence:
    def test_no_record_line_is_the_same_for_every_status(self):
        signals = st.ConceptSignals(key="k")
        for status in (st.STATUS_ASSUMED, st.STATUS_VERIFIED, st.STATUS_UNKNOWN):
            line = st.endorse_line_for(status, signals)
            assert line == label_vocab.ATLAS_ENDORSE_NONE_LINE
            assert "このコーパスの中では" in line

    def test_legacy_short_line_is_projected(self):
        assert (
            st.learner_endorse_line(label_vocab.ATLAS_ENDORSE_LEGACY_NONE_LINE)
            == label_vocab.ATLAS_ENDORSE_NONE_LINE
        )
        src = _read(BACKEND / "core" / "atlas_state.py")
        assert '"承認: 承認記録なし"' not in src


# ---------------------------------------------------------------------------
# IK-1046 残り
# ---------------------------------------------------------------------------


class TestTruncatedAbbreviation:
    def test_cep_matches_cepheus(self):
        assert ls_schema.truncation_matched_in("Cepheus", ["in the Cep B molecular cloud."])
        assert ls_schema.truncation_matched_in("Cep", ["the Cepheus B cloud"])

    def test_short_or_numeric_terms_are_not_matched(self):
        assert not ls_schema.truncation_matched_in("Ia", ["type Iax supernova"])
        assert not ls_schema.truncation_matched_in("DR2", ["DR2 and DR3"])
        assert not ls_schema.truncation_matched_in("GWTC", ["the GW catalog"])

    def test_note_drops_truncation_but_keeps_unrelated_terms(self):
        quotes = ["Observations in the Cep B molecular cloud."]
        assert ls_schema.reason_beyond_quote_note("Cepheus B の分子雲の観測", quotes) == ""
        note = ls_schema.reason_beyond_quote_note("GWTC-4 と Cepheus の比較", quotes)
        assert "GWTC-4" in note and "Cepheus" not in note


# ---------------------------------------------------------------------------
# IK-1044 残り（管理画面）
# ---------------------------------------------------------------------------


class TestAdminDecisionWording:
    def test_dismissed_note_heading_is_reason(self):
        src = _read(ADMIN_JS)
        assert src.count('decision.status === "dismissed" ? "見送りの理由" : "判断のメモ"') >= 4
        assert "判断のメモ: ' + escHtml" not in src

    def test_not_accepted_text_without_draft_points_to_section_button(self):
        src = _read(ADMIN_JS)
        assert "EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT" in src
        assert "return draftExists ? EDGE_NOT_ACCEPTED_TEXT : EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT;" in src
