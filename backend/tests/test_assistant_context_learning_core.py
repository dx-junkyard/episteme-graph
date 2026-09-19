"""画面文脈アダプター Phase 4 — 学習側 core の振る舞い（設計 §11.3 / §11.4）。

固定するのは4つ:

- 解決器が**学習者射影の DTO だけ**から期待どおりの事実文を作ること。
- 入力を mutate しないこと・壊れた DTO で例外を出さないこと（SA2）。
- 予算（``MAX_BLOCK_CHARS_LEARNING``）と学習側ヘッダで描画されること、
  既定の ``render_block`` は Phase 1 とバイト等価であること（後方互換 = §11.10）。
- 選択逐語ブロックが一致の有無を必ず併記すること（§11.4）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.assistant_context import (  # noqa: E402
    BLOCK_HEADER,
    BLOCK_HEADER_LEARNING,
    MAX_BLOCK_CHARS,
    MAX_BLOCK_CHARS_LEARNING,
    SCREEN_LEARNING,
    ScreenContext,
    infer_selection_kind,
    registered_kinds,
    render_block,
    render_selection_block,
    resolve,
)
from core.assistant_context import schema as ac_schema  # noqa: E402
from core.assistant_context.resolvers import learning as lr  # noqa: E402


# ---------------------------------------------------------------------------
# フィクスチャ（学習者射影 DTO の形をそのまま模す）
# ---------------------------------------------------------------------------


def _ctx(*, selection=None, view=None, entities=None) -> ScreenContext:
    return ScreenContext(
        screen=SCREEN_LEARNING,
        selection=selection
        if selection is not None
        else {
            "course_id": "course-1",
            "topic_id": "t1",
            "kind": "element",
            "element_type": "component",
            "element_id": "comp-uuid",
        },
        view=view if view is not None else {"mode": "chat"},
        visible_entities=entities or [],
    )


def _component_context() -> dict:
    """``core.component_context.build_component_context`` の戻り値の形。"""
    return {
        "component_id": "comp-uuid",
        "instance": {
            "component": {
                "label": "観測量の線形化",
                "summary": "観測量を一次まで展開する操作",
                "component_type": "operation",
                "teaching_takeaway": "",
            },
            "in_paper": {
                "document": {"id": "doc-1", "title": "Linearized observables", "section": "3"},
                "narrative_role": "観測量を作る段",
                "graph_summary_excerpt": "",
            },
            "supports": {
                "preconditions": [
                    {"text": "揺らぎが小さい", "refs": {"claim_ids": [], "equation_ids": []}},
                    {"text": "背景は静的", "refs": {"claim_ids": [], "equation_ids": []}},
                    {"text": "境界は無視できる", "refs": {"claim_ids": [], "equation_ids": []}},
                    {"text": "四つ目の前提", "refs": {"claim_ids": [], "equation_ids": []}},
                ],
                "inputs": [],
                "outputs": [],
                "cautions": [],
                "equations": [
                    {"id": "eq_2_7", "label": "式 (12)", "role": "input"},
                    {"id": "eq_op_0007", "label": "eq_op_0007", "role": "linked"},
                ],
                "claims": [
                    {"id": "claim-uuid", "excerpt": "線形化は一次までで十分な精度を持つ"}
                ],
                "dependencies": [],
            },
            "explanation": {"body": "この操作は観測量を近似します", "status": "candidate"},
            "provenance": "course_freeze",
        },
        "shared_part": None,
        "graph": None,
    }


def _claim_context() -> dict:
    """``core.element_context.build_element_context`` の戻り値の形（claim）。"""
    return {
        "available": True,
        "element_type": "claim",
        "element_id": "claim-uuid",
        "focus": {
            "element_type": "claim",
            "element_id": "claim-uuid",
            "document_id": "doc-1",
            "label": "線形化は一次までで十分",
            "headline": "線形化は一次までで十分",
            "intrinsic_summary": "高次項は観測精度より小さい",
        },
        "upper": [{"label": "観測量の線形化", "navigable": True}],
        "lower": [{"label": "高次項の評価", "navigable": False}],
        "notes": ["この主張は出典の第3節に現れます"],
        "provenance": "course_freeze",
    }


def _ledger() -> dict:
    """``routes/doubt.py::get_learner_ledger_line`` の戻り値の形。"""
    return {
        "target_id": "comp-uuid",
        "target_type": "component",
        "verification_status": "untested",
        "verification_status_label": "未検証",
        "scopes": [],
        "fact_line": "この内容の検証スコープはまだ記帳されていません。",
        "support_fact_line": "この内容を支える経路は、このコーパスの中では一本だけです。",
        "falsification_conditions": [
            {
                "statement": "高次項が観測精度を超えて効く場合",
                "kind_label": "観測",
                "reachability_label": "未評価",
                "source_label": "教員の記帳",
            },
            {
                "statement": "背景が時間変化する場合",
                "kind_label": "観測",
                "reachability_label": "未評価",
                "source_label": "教員の記帳",
            },
        ],
    }


def _landscape() -> dict:
    """``core.landscape.projection.learner_landscape_dto`` の戻り値の形。"""
    return {
        "course_domain_key": "astrophysics",
        "domains": [
            {
                "domain_key": "astrophysics",
                "domain_name": "宇宙物理",
                "frozen_version": "3",
                "is_course_map": True,
            }
        ],
        "documents": [
            {
                "document_id": "doc-1",
                "title": "Linearized observables",
                "placements": [
                    {
                        "domain_key": "astrophysics",
                        "node_id": "n-wave",
                        "node_label": "重力波の観測",
                        "region_id": "r-obs",
                        "node_kind": "concept",
                        "perspective_label": "主題",
                        "status": "inferred",
                        "provenance_label": "AIによる推定（未確認）",
                        "reason": "要旨が観測手法を述べているため",
                        "evidence": [{"quote": "we observe"}],
                    },
                    {
                        "domain_key": "astrophysics",
                        "node_id": "r-obs",
                        "node_label": "観測天文学",
                        "region_id": "r-obs",
                        "node_kind": "region",
                        "perspective_label": "主題",
                        "status": "confirmed",
                        "provenance_label": "教員確認済み",
                        "reason": "",
                        "evidence": [],
                    },
                ],
            }
        ],
        "corpus": {"source_document_count": 3, "placed_document_count": 1},
    }


def _sources(**overrides) -> dict:
    payload = {
        "element": {"element_type": "component", "context": _component_context(), "item": None},
        "ledger": _ledger(),
        "landscape": _landscape(),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_learning_resolvers_are_registered_in_budget_order(self):
        """登録順 = 予算の優先順位（具体的なものから先に載る）。

        末尾の ``retrieved_structure``（知識の転用層 P4-2）は**別ブロック**として
        描画されるので、画面文脈ブロックの解決（``kinds=None``）では
        ``sources["retrieved_structure"]`` が渡らず何も出さない。
        """
        assert registered_kinds(SCREEN_LEARNING) == (
            "element",
            "verification",
            "placement",
            "view",
            "retrieved_structure",
        )

    def test_retrieved_structure_stays_out_of_the_screen_context_block(self):
        """P4-2 の解決器は画面文脈の ``sources`` では黙って空を返す（混ざらない）。"""
        assert resolve(_ctx(), _sources(), kinds=("retrieved_structure",)) == []

    def test_kinds_filter_keeps_only_the_named_resolvers(self):
        """§11.5: ``cycle_mode="elicit"`` は表示モードの事実1行しか許さない。"""
        facts = resolve(_ctx(), _sources(), kinds=("view",))
        assert facts == ["学習者は通常のチャット画面を見ています。"]

    def test_kinds_none_is_phase1_behaviour(self):
        assert len(resolve(_ctx(), _sources())) > 1

    def test_unknown_kind_in_the_filter_yields_nothing(self):
        assert resolve(_ctx(), _sources(), kinds=("topic",)) == []


# ---------------------------------------------------------------------------
# kind "element"
# ---------------------------------------------------------------------------


class TestElementResolver:
    def test_component_facts(self):
        facts = lr.resolve_element(_ctx(), _sources())
        assert facts[0] == (
            "学習者が選んでいるのは論理要素「観測量の線形化」で、"
            "論文『Linearized observables』に由来します"
        )
        assert "「観測量の線形化」の論文の流れの中での役割: 観測量を作る段" in facts
        # 前提は3件で打ち切り、件数ではなく「ほか」で正直に示す。
        assert (
            "「観測量の線形化」が前提にしているのは"
            "「揺らぎが小さい」「背景は静的」「境界は無視できる」・ほかです" in facts
        )
        assert "「観測量の線形化」を支える主張: 「線形化は一次までで十分な精度を持つ」" in facts

    def test_equation_labels_use_the_printed_number_and_drop_internal_ids(self):
        facts = lr.resolve_element(_ctx(), _sources())
        equation_line = next(f for f in facts if "が使う式は" in f)
        assert "式 (12)" in equation_line
        assert "eq_op_" not in equation_line

    def test_candidate_explanation_is_labelled_as_unapproved(self):
        facts = lr.resolve_element(_ctx(), _sources())
        assert any(
            f.startswith("この要素の説明（候補（未承認））: ") for f in facts
        ), facts

    def test_explanation_without_status_is_the_route_filtered_approved_one(self):
        """実 DTO（``_first_approved_component_explanation``）は承認済みしか充填せず
        ``status`` キーを持たない。status 不在を「候補」と誤ラベルしない。"""
        sources = _sources()
        del sources["element"]["context"]["instance"]["explanation"]["status"]
        facts = lr.resolve_element(_ctx(), sources)
        assert any(f.startswith("この要素の説明（承認済み）: ") for f in facts)
        assert not any("候補（未承認）" in f for f in facts)

    def test_approved_explanation_keeps_its_own_label(self):
        sources = _sources()
        sources["element"]["context"]["instance"]["explanation"]["status"] = "approved"
        facts = lr.resolve_element(_ctx(), sources)
        assert any(f.startswith("この要素の説明（承認済み）: ") for f in facts)

    def test_summary_replaces_the_role_line_when_the_role_is_missing(self):
        sources = _sources()
        sources["element"]["context"]["instance"]["in_paper"]["narrative_role"] = ""
        facts = lr.resolve_element(_ctx(), sources)
        assert "「観測量の線形化」の要約: 観測量を一次まで展開する操作" in facts

    def test_facts_are_capped(self):
        assert len(lr.resolve_element(_ctx(), _sources())) <= (
            ac_schema.MAX_LEARNING_ELEMENT_FACTS
        )

    def test_claim_lens_facts(self):
        ctx = _ctx(
            selection={"kind": "element", "element_type": "claim", "element_id": "claim-uuid"}
        )
        sources = {"element": {"element_type": "claim", "context": _claim_context()}}
        facts = lr.resolve_element(ctx, sources)
        assert facts[0] == "学習者が選んでいるのは主張「線形化は一次までで十分」です"
        assert "「線形化は一次までで十分」の要約: 高次項は観測精度より小さい" in facts
        assert "「線形化は一次までで十分」の上位にあたるのは「観測量の線形化」です" in facts
        assert "「線形化は一次までで十分」の下位にあたるのは「高次項の評価」です" in facts
        assert "この主張は出典の第3節に現れます" in facts

    def test_unavailable_lens_yields_nothing(self):
        ctx = _ctx(selection={"kind": "element", "element_type": "equation", "element_id": "e1"})
        sources = {
            "element": {
                "element_type": "equation",
                "context": {"available": False, "note": "文脈は取得できませんでした"},
            }
        }
        assert lr.resolve_element(ctx, sources) == []

    def test_figure_uses_the_caption_only(self):
        ctx = _ctx(selection={"kind": "element", "element_type": "figure", "element_id": "f1"})
        sources = {
            "element": {
                "element_type": "figure",
                "context": None,
                "item": {"kind": "figure", "caption": "装置の配置", "title": "図: 装置の配置"},
            }
        }
        assert lr.resolve_element(ctx, sources) == ["学習者が選んでいるのは図です: 装置の配置"]

    def test_non_element_selection_is_not_resolved(self):
        """4-c（topic / visible）は実装しない。要素以外の選択では何も出さない。"""
        for selection in (
            {"kind": "topic", "topic_id": "t1"},
            {"kind": "segment", "segment_id": "3"},
            {"kind": "chunk", "chunk_id": "c1"},
            {"course_id": "course-1"},
        ):
            assert lr.resolve_element(_ctx(selection=selection), _sources()) == []

    def test_unknown_element_type_is_ignored(self):
        ctx = _ctx(selection={"kind": "element", "element_type": "evidence", "element_id": "e"})
        assert lr.resolve_element(ctx, {"element": {"element_type": "evidence"}}) == []

    def test_missing_sources_yield_nothing(self):
        assert lr.resolve_element(_ctx(), {}) == []


# ---------------------------------------------------------------------------
# kind "verification" / "placement" / "view"
# ---------------------------------------------------------------------------


class TestVerificationResolver:
    def test_fact_lines_are_passed_through_verbatim(self):
        facts = lr.resolve_verification(_ctx(), _sources())
        assert facts[0] == "この内容の検証スコープはまだ記帳されていません。"
        assert facts[1] == "この内容を支える経路は、このコーパスの中では一本だけです。"

    def test_falsification_conditions_are_capped_with_the_other_lines(self):
        facts = lr.resolve_verification(_ctx(), _sources())
        assert len(facts) == ac_schema.MAX_LEARNING_VERIFICATION_FACTS
        assert facts[2] == "覆る条件（観測）: 「高次項が観測精度を超えて効く場合」"

    def test_recorded_by_and_scopes_are_never_projected(self):
        sources = _sources()
        sources["ledger"]["scopes"] = [{"condition": "低温", "domain": "実験室"}]
        sources["ledger"]["recorded_by"] = "teacher-uuid"
        facts = lr.resolve_verification(_ctx(), sources)
        assert all("teacher-uuid" not in fact for fact in facts)
        assert all("実験室" not in fact for fact in facts)

    def test_missing_ledger_yields_nothing(self):
        assert lr.resolve_verification(_ctx(), {}) == []


class TestPlacementResolver:
    def test_placement_facts_keep_the_provenance_label(self):
        facts = lr.resolve_placement(_ctx(), _sources())
        assert facts[0] == (
            "論文『Linearized observables』は、分野の地図（版 3）の"
            "「観測天文学／重力波の観測」に置かれています（AIによる推定（未確認））"
        )

    def test_placements_are_capped(self):
        assert len(lr.resolve_placement(_ctx(), _sources())) <= (
            ac_schema.MAX_LEARNING_PLACEMENT_FACTS
        )

    def test_version_is_omitted_when_the_skeleton_version_is_unknown(self):
        sources = _sources()
        sources["landscape"]["domains"] = []
        facts = lr.resolve_placement(_ctx(), sources)
        assert "分野の地図の" in facts[0] and "版" not in facts[0]

    def test_region_is_omitted_when_it_cannot_be_resolved(self):
        sources = _sources()
        placements = sources["landscape"]["documents"][0]["placements"]
        del placements[1]
        facts = lr.resolve_placement(_ctx(), sources)
        assert "「重力波の観測」に置かれています" in facts[0]
        assert "r-obs" not in facts[0]

    def test_counts_are_not_projected(self):
        facts = lr.resolve_placement(_ctx(), _sources())
        assert all("3件" not in fact for fact in facts)

    def test_missing_landscape_yields_nothing(self):
        assert lr.resolve_placement(_ctx(), {}) == []


class TestViewResolver:
    def test_mode_precision_slide_and_scope(self):
        ctx = _ctx(
            selection={"kind": "element", "element_type": "component", "segment_id": "3"},
            view={"mode": "lecture", "precision_reading": True, "discuss_scope": "course_sources"},
        )
        assert lr.resolve_view(ctx, {}) == [
            "学習者は精読モードでレクチャー再生画面を見ています。"
            "表示中の区画: スライド3。検索範囲: このコースのソース論文。"
        ]

    def test_unknown_mode_yields_nothing(self):
        assert lr.resolve_view(_ctx(selection={}, view={"mode": "hologram"}), {}) == []

    def test_voice_mode(self):
        assert lr.resolve_view(_ctx(selection={}, view={"mode": "voice"}), {}) == [
            "学習者は音声会話を見ています。"
        ]


# ---------------------------------------------------------------------------
# SA2 / SA4 — mutate しない・例外を出さない・数値と内部 ID を出さない
# ---------------------------------------------------------------------------


class TestSafety:
    def test_inputs_are_not_mutated(self):
        ctx = _ctx()
        sources = _sources()
        snapshot_ctx = copy.deepcopy(ctx)
        snapshot = copy.deepcopy(sources)
        resolve(ctx, sources)
        assert sources == snapshot
        assert ctx == snapshot_ctx

    def test_malformed_dtos_never_raise(self):
        broken = [
            {"element": "not-a-dict", "ledger": 3, "landscape": []},
            {"element": {"element_type": "component", "context": {"instance": None}}},
            {"landscape": {"documents": [None, {"placements": "x"}]}},
            {"ledger": {"falsification_conditions": [None, 1, {}]}},
        ]
        for sources in broken:
            assert isinstance(resolve(_ctx(), sources), list)

    def test_no_numbers_or_internal_ids_reach_the_facts(self):
        facts = resolve(_ctx(), _sources())
        blob = "\n".join(facts)
        for token in ("eq_op_", "theory_op_", "ev_", "synth_", "span_", "support:", "comp_"):
            assert token not in blob, token
        # 学習者射影が持つ内部 UUID がそのまま出ていない。
        for uuid_like in ("comp-uuid", "claim-uuid", "doc-1", "n-wave", "r-obs"):
            assert uuid_like not in blob, uuid_like

    def test_internal_id_text_is_dropped_from_free_text(self):
        sources = _sources()
        sources["element"]["context"]["instance"]["in_paper"]["narrative_role"] = (
            "synth_claim_0001 を定量化する"
        )
        facts = lr.resolve_element(_ctx(), sources)
        assert all("synth_claim_0001" not in fact for fact in facts)


# ---------------------------------------------------------------------------
# render_block — 学習側の予算とヘッダ / Phase 1 のバイト等価
# ---------------------------------------------------------------------------


class TestRenderBlock:
    def test_learning_header_and_budget(self):
        block = render_block(
            resolve(_ctx(), _sources()),
            header=BLOCK_HEADER_LEARNING,
            max_chars=MAX_BLOCK_CHARS_LEARNING,
        )
        assert block.startswith(BLOCK_HEADER_LEARNING)
        assert len(block) <= MAX_BLOCK_CHARS_LEARNING

    def test_truncation_happens_at_a_line_boundary(self):
        facts = [f"事実{i}" + "あ" * 200 for i in range(20)]
        block = render_block(
            facts, header=BLOCK_HEADER_LEARNING, max_chars=MAX_BLOCK_CHARS_LEARNING
        )
        assert len(block) <= MAX_BLOCK_CHARS_LEARNING
        assert block.endswith(ac_schema.TRUNCATION_LINE)
        for line in block.split("\n")[1:-1]:
            assert line.startswith("- ") and line[2:] in facts

    def test_empty_facts_render_to_an_empty_string(self):
        assert render_block([], header=BLOCK_HEADER_LEARNING, max_chars=1200) == ""
        assert render_block(None, header=BLOCK_HEADER_LEARNING, max_chars=1200) == ""

    def test_defaults_are_byte_identical_to_phase1(self):
        """後方互換（§11.10）: kwarg を渡さなければ Phase 1 の出力と1バイトも変わらない。"""
        facts = ["一つ目の事実", "二つ目の事実"]
        assert render_block(facts) == "\n".join(
            [BLOCK_HEADER, "- 一つ目の事実", "- 二つ目の事実"]
        )

    def test_defaults_still_truncate_with_the_teacher_budget(self):
        """既定の打ち切りも Phase 1 のまま（学習側の狭い予算を既定にしていない）。"""
        facts = ["あ" * 900 for _ in range(6)]
        block = render_block(facts)
        assert block.startswith(BLOCK_HEADER)
        assert MAX_BLOCK_CHARS_LEARNING < len(block) <= MAX_BLOCK_CHARS
        assert block.endswith(ac_schema.TRUNCATION_LINE)

    def test_learning_budget_is_smaller_than_the_teacher_one(self):
        assert MAX_BLOCK_CHARS_LEARNING < MAX_BLOCK_CHARS


# ---------------------------------------------------------------------------
# 選択逐語ブロック（§11.4）
# ---------------------------------------------------------------------------


class TestSelectionBlock:
    def test_match_is_confirmed_across_whitespace_differences(self):
        block = render_selection_block(
            "  揺らぎが小さい\n背景は静的 ", "前提として 揺らぎが小さい　背景は静的 とする"
        )
        assert block.splitlines()[0] == ac_schema.SELECTION_BLOCK_HEADER
        assert block.splitlines()[1] == ac_schema.SELECTION_MATCH_CONFIRMED
        assert block.splitlines()[2:] == ["> 揺らぎが小さい", "> 背景は静的"]

    def test_mismatch_is_still_projected_but_flagged(self):
        block = render_selection_block("どこにもない文", "教材の本文")
        assert ac_schema.SELECTION_MATCH_UNCONFIRMED in block
        assert "> どこにもない文" in block

    def test_missing_material_is_unconfirmed(self):
        assert ac_schema.SELECTION_MATCH_UNCONFIRMED in render_selection_block("文", None)

    def test_text_is_capped(self):
        block = render_selection_block("あ" * 5000, "")
        quoted = block.splitlines()[2]
        assert len(quoted) == ac_schema.MAX_SELECTION_TEXT_CHARS + 2  # "> " を含む

    def test_control_sequences_are_stripped(self):
        block = render_selection_block("\x1b[31m赤い文\x1b[0m", "赤い文")
        assert "\x1b" not in block and "[31m" not in block
        assert "> 赤い文" in block
        assert ac_schema.SELECTION_MATCH_CONFIRMED in block

    def test_empty_selection_yields_an_empty_string(self):
        for value in (None, "", "   ", "\n\n"):
            assert render_selection_block(value, "本文") == ""

    def test_never_raises(self):
        assert isinstance(render_selection_block(123, object()), str)


# ---------------------------------------------------------------------------
# infer_selection_kind（§11.2）
# ---------------------------------------------------------------------------


class TestInferSelectionKind:
    def test_declared_kind_wins(self):
        assert infer_selection_kind({"kind": "topic", "element_id": "x"}) == "topic"

    def test_unknown_declared_kind_falls_back_to_inference(self):
        assert infer_selection_kind({"kind": "galaxy", "chunk_id": "c"}) == "chunk"

    def test_inference_order(self):
        full = {"element_id": "e", "chunk_id": "c", "segment_id": "s", "topic_id": "t"}
        assert infer_selection_kind(full) == "element"
        del full["element_id"]
        assert infer_selection_kind(full) == "chunk"
        del full["chunk_id"]
        assert infer_selection_kind(full) == "segment"
        del full["segment_id"]
        assert infer_selection_kind(full) == "topic"

    def test_empty_selection_degrades_to_document(self):
        for value in (None, {}, {"course_id": "c"}, "not-a-mapping", []):
            assert infer_selection_kind(value) == "document"
