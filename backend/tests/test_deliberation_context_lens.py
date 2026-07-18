"""W層 面③「要素中心コンテキストビュー」（``core/deliberation/context_lens.py``）の
純粋部の単体テスト。

設計書 `docs/features/element_context_lens_design.md`。DB 実接続は必要とせず、
fake データ（artifact 相当の dict）で各要素型の投影ロジックを検証する。
``test_deliberation_positioning.py`` と同じ方針: DB を読み出す薄いラッパ自体は
ここでは検証しない（docker 復帰後に実データで確認する）。ただし ``build()`` の
契約（fail-soft・キー構成）は DB 接続の成否によらず必ず成り立つよう設計されている
ため、実 DB が無い/接続できない環境でも ``TestBuildContract`` は通る。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import context_lens  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    ANNOTATION_KIND_INTERPRETATION,
    ANNOTATION_KIND_MEANING,
    ANNOTATION_STATUS_CANDIDATE,
    ANNOTATION_STATUS_COMMITTED,
    CONTEXT_ROLE_STATUS_UNIDENTIFIED,
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    ElementRef,
)


# ---------------------------------------------------------------------------
# RELATION_LABELS 完全性: emit される relation は全て語彙内・relation_label 非空
# ---------------------------------------------------------------------------


class TestRelationLabelsCompleteness:
    def test_every_label_is_non_empty_string(self):
        assert context_lens.RELATION_LABELS
        for relation, label in context_lens.RELATION_LABELS.items():
            assert isinstance(relation, str) and relation
            assert isinstance(label, str) and label.strip()

    def test_starter_vocabulary_is_present(self):
        # 設計書が明示した初期語彙は削除しない（拡張は可）。
        starter = {
            "provides_evidence_for", "quantifies", "supports_thesis", "supports_component",
            "appears_in_section", "belongs_to_derivation", "member_of", "contains",
            "derives_from", "leads_to", "uses_symbol", "rests_on_evidence", "has_subclaim",
            "subclaim_of", "quantified_by", "evidenced_by_figure", "requires",
            "related_component_candidate",
        }
        assert starter <= set(context_lens.RELATION_LABELS)

    def test_item_raises_keyerror_for_unknown_relation(self):
        # _item() は RELATION_LABELS[relation] を通常の辞書アクセスで引く。未登録の
        # relation を使うコードは即座に KeyError で落ちる（silent な語彙の抜け漏れを防ぐ）。
        try:
            context_lens._item("theory_claim", "c1", "doc-1", "label", "totally_unknown_relation", CONTEXT_STATUS_SOURCE_BACKED)
        except KeyError:
            pass
        else:  # pragma: no cover
            raise AssertionError("unknown relation must raise KeyError from RELATION_LABELS")

    def test_all_relations_emitted_by_helpers_are_registered(self):
        # 実際に本モジュールの各ヘルパが emit する relation を全て収集し、漏れなく
        # RELATION_LABELS に登録されていることを確認する。
        thesis_claim = {"central_thesis": {"claim_ids": ["c1"]}}
        thesis_eq = {"central_thesis": {"equation_ids": ["eq1"]}}
        chains = [{"derivation_id": "d1", "steps": [{"step_id": "s1", "input_claim_ids": ["c1"]}]}]

        emitted_relations = set()
        for item in context_lens._thesis_upper_items_for_claim(thesis_claim, {"c1": "c1"}, "c1", "doc-1"):
            emitted_relations.add(item["relation"])
        for item in context_lens._thesis_upper_items_for_equation(thesis_eq, "eq1", "doc-1"):
            emitted_relations.add(item["relation"])
        for item in context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c1"):
            emitted_relations.add(item["relation"])
        for item in context_lens._figure_part_items_from_profile(
            {"functions": [{"name": "Laser"}]}, "reviewed", "doc-1"
        ):
            emitted_relations.add(item["relation"])
        for item in context_lens._figure_part_items_from_apparatus_record(
            {"parts": [{"name": "EOM", "evidence_quote": "q"}]}, "doc-1"
        ):
            emitted_relations.add(item["relation"])
        for item in context_lens._figure_apparatus_component_items(
            [{"id": "comp-1", "name": "Apparatus", "review_status": "teacher_approved"}], "doc-1"
        ):
            emitted_relations.add(item["relation"])

        assert emitted_relations
        assert emitted_relations <= set(context_lens.RELATION_LABELS)


# ---------------------------------------------------------------------------
# _item(): navigable 判定・relation_label 解決
# ---------------------------------------------------------------------------


class TestItemConstructor:
    def test_navigable_true_for_navigable_type_with_id(self):
        item = context_lens._item("theory_claim", "c1", "doc-1", "label", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
        assert item["navigable"] is True
        assert item["element_id"] == "c1"
        assert item["relation_label"] == context_lens.RELATION_LABELS["has_subclaim"]

    def test_navigable_false_when_element_id_is_none(self):
        item = context_lens._item("theory_claim", None, "doc-1", "label", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
        assert item["navigable"] is False

    def test_navigable_false_for_non_navigable_type_even_with_id(self):
        # "part"/"thesis"/"section"/"derivation"/"symbol"/"evidence" は仕様上
        # navigable になり得ない要素型（設計書 §5 の ITEM 契約）。
        item = context_lens._item("part", "some-id", "doc-1", "label", "contains", CONTEXT_STATUS_CANDIDATE)
        assert item["navigable"] is False

    def test_evidence_refs_strips_blank_entries(self):
        item = context_lens._item(
            "theory_claim", "c1", "doc-1", "label", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED,
            evidence_refs=["  ", "", "q1"],
        )
        assert item["evidence_refs"] == ["q1"]


# ---------------------------------------------------------------------------
# 状態マッピング（_status_for_link は単一の testable helper）
# ---------------------------------------------------------------------------


class TestStatusForLink:
    def test_explicit_maps_to_source_backed(self):
        assert context_lens._status_for_link("explicit") == CONTEXT_STATUS_SOURCE_BACKED

    def test_inferred_maps_to_candidate(self):
        assert context_lens._status_for_link("inferred") == CONTEXT_STATUS_CANDIDATE

    def test_committed_maps_to_confirmed(self):
        assert context_lens._status_for_link("committed") == CONTEXT_STATUS_CONFIRMED

    def test_unknown_kind_defaults_to_candidate_not_source_backed(self):
        # 安全側に倒す: 未知の source_kind を source_backed だと僭称しない。
        assert context_lens._status_for_link("something_new") == CONTEXT_STATUS_CANDIDATE

    def test_link_kind_from_backing_status_source_backed(self):
        assert context_lens._link_kind_from_backing_status("source_backed") == "explicit"

    def test_link_kind_from_backing_status_other_values_are_inferred(self):
        for value in ("partially_source_backed", "inferred", "review_required", "", None):
            assert context_lens._link_kind_from_backing_status(value) == "inferred"


# ---------------------------------------------------------------------------
# 縮退: contextual_role の導出（人間確定 > 上位構造先頭 > unidentified）
# ---------------------------------------------------------------------------


class TestDeriveContextualRole:
    def test_no_upper_items_and_no_annotations_is_unidentified(self):
        role, status = context_lens._derive_contextual_role([], [])
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED

    def test_uses_top_upper_item_when_no_committed_annotation(self):
        upper = [
            context_lens._item(
                "theory_claim", "c1", "doc-1", "Claim C12", "provides_evidence_for", CONTEXT_STATUS_SOURCE_BACKED,
            )
        ]
        role, status = context_lens._derive_contextual_role(upper, [])
        assert role == "Claim C12" + context_lens.RELATION_LABELS["provides_evidence_for"]
        assert status == CONTEXT_STATUS_SOURCE_BACKED

    def test_committed_annotation_overrides_upper_derivation(self):
        upper = [
            context_lens._item(
                "theory_claim", "c1", "doc-1", "Claim C12", "provides_evidence_for", CONTEXT_STATUS_SOURCE_BACKED,
            )
        ]
        annotations = [
            {
                "status": ANNOTATION_STATUS_COMMITTED,
                "kind": ANNOTATION_KIND_INTERPRETATION,
                "body": {"text": "教員による人間確定の役割説明"},
            }
        ]
        role, status = context_lens._derive_contextual_role(upper, annotations)
        assert role == "教員による人間確定の役割説明"
        assert status == CONTEXT_STATUS_CONFIRMED

    def test_candidate_annotation_is_ignored(self):
        # candidate 状態の注釈は文脈上の役割として採用しない（W2: AI 出力は確定しない）。
        upper = []
        annotations = [
            {
                "status": ANNOTATION_STATUS_CANDIDATE,
                "kind": ANNOTATION_KIND_MEANING,
                "body": {"text": "まだ候補の説明"},
            }
        ]
        role, status = context_lens._derive_contextual_role(upper, annotations)
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED

    def test_blank_committed_text_falls_back_to_upper(self):
        upper = [
            context_lens._item("thesis", None, "doc-1", "中心命題", "supports_thesis", CONTEXT_STATUS_SOURCE_BACKED)
        ]
        annotations = [
            {"status": ANNOTATION_STATUS_COMMITTED, "kind": ANNOTATION_KIND_MEANING, "body": {"text": "   "}}
        ]
        role, status = context_lens._derive_contextual_role(upper, annotations)
        assert role == "中心命題" + context_lens.RELATION_LABELS["supports_thesis"]
        assert status == CONTEXT_STATUS_SOURCE_BACKED


# ---------------------------------------------------------------------------
# 縮退: レーン上限（20件）超過時の notes 記録
# ---------------------------------------------------------------------------


class TestCapLane:
    def test_under_limit_is_untouched(self):
        items = [context_lens._item("theory_claim", str(i), "doc-1", str(i), "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED) for i in range(5)]
        notes: list[str] = []
        result = context_lens._cap_lane(items, notes, "下位構造")
        assert result == items
        assert notes == []

    def test_over_limit_truncates_and_notes_the_omission(self):
        items = [
            context_lens._item("theory_claim", str(i), "doc-1", str(i), "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
            for i in range(25)
        ]
        notes: list[str] = []
        result = context_lens._cap_lane(items, notes, "下位構造")
        assert len(result) == context_lens._CONTEXT_LANE_MAX == 20
        assert len(notes) == 1
        assert "下位構造" in notes[0]
        assert "5" in notes[0]  # 25 - 20 = 5 件省略

    def test_does_not_silently_drop_without_a_note(self):
        items = [context_lens._item("theory_claim", str(i), "doc-1", str(i), "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED) for i in range(21)]
        notes: list[str] = []
        context_lens._cap_lane(items, notes, "上位構造")
        assert notes  # 1件超過でも必ず notes に事実を残す


class TestDedupeItems:
    def test_removes_exact_duplicates(self):
        item = context_lens._item("theory_claim", "c1", "doc-1", "label", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
        result = context_lens._dedupe_items([item, dict(item)])
        assert len(result) == 1

    def test_keeps_distinct_items(self):
        a = context_lens._item("theory_claim", "c1", "doc-1", "label", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
        b = context_lens._item("theory_claim", "c2", "doc-1", "label2", "has_subclaim", CONTEXT_STATUS_SOURCE_BACKED)
        result = context_lens._dedupe_items([a, b])
        assert result == [a, b]


# ---------------------------------------------------------------------------
# claim id マッピング（fake rows での双方向解決）
# ---------------------------------------------------------------------------


class TestClaimIdLookupFromRows:
    def test_maps_legacy_id_and_span_id_and_db_id_to_db_id(self):
        rows = [
            ("db-uuid-1", {"span_id": "span_7", "legacy_ids": ["span_7", "claim_span_7"]}),
        ]
        lookup = context_lens._claim_id_lookup_from_rows(rows)
        assert lookup["db-uuid-1"] == "db-uuid-1"
        assert lookup["span_7"] == "db-uuid-1"
        assert lookup["claim_span_7"] == "db-uuid-1"

    def test_missing_legacy_ids_still_maps_db_identity(self):
        rows = [("db-uuid-2", {})]
        lookup = context_lens._claim_id_lookup_from_rows(rows)
        assert lookup == {"db-uuid-2": "db-uuid-2"}

    def test_multiple_rows_do_not_collide(self):
        rows = [
            ("db-1", {"legacy_ids": ["claim_a"]}),
            ("db-2", {"legacy_ids": ["claim_b"]}),
        ]
        lookup = context_lens._claim_id_lookup_from_rows(rows)
        assert lookup["claim_a"] == "db-1"
        assert lookup["claim_b"] == "db-2"

    def test_already_remapped_db_id_passes_through(self):
        # theory_components.evidence_claims 等、既に DB uuid へ remap 済みの参照を
        # 渡しても素通りする（同じ辞書で両方の形式を扱える）。
        rows = [("db-3", {"legacy_ids": ["claim_x"]})]
        lookup = context_lens._claim_id_lookup_from_rows(rows)
        assert lookup.get("db-3") == "db-3"


class TestComponentIdLookupFromRows:
    def test_maps_agent_component_id_to_db_id(self):
        rows = [("comp-db-1", {"legacy_ids": ["apparatus_fig_3_2"]})]
        lookup = context_lens._component_id_lookup_from_rows(rows)
        assert lookup["apparatus_fig_3_2"] == "comp-db-1"
        assert lookup["comp-db-1"] == "comp-db-1"

    def test_no_source_scope_still_maps_identity(self):
        rows = [("comp-db-2", {})]
        lookup = context_lens._component_id_lookup_from_rows(rows)
        assert lookup == {"comp-db-2": "comp-db-2"}


class TestClaimObjectFor:
    def test_finds_matching_record_via_lookup(self):
        claims = [
            {"claim_id": "claim_span_7", "text": "運動量は保存される"},
            {"claim_id": "claim_span_9", "text": "別の主張"},
        ]
        lookup = {"claim_span_7": "db-1", "claim_span_9": "db-2"}
        found = context_lens._claim_object_for(claims, lookup, "db-1")
        assert found is not None
        assert found["text"] == "運動量は保存される"

    def test_no_match_returns_none(self):
        claims = [{"claim_id": "claim_x", "text": "..."}]
        lookup = {"claim_x": "db-1"}
        assert context_lens._claim_object_for(claims, lookup, "db-999") is None

    def test_empty_claim_id_is_ignored(self):
        claims = [{"claim_id": "", "text": "..."}]
        assert context_lens._claim_object_for(claims, {}, "db-1") is None


# ---------------------------------------------------------------------------
# 上位構造: thesis_reconstruction artifact との関係（claim / equation）
# ---------------------------------------------------------------------------


class TestThesisUpperItemsForClaim:
    def test_central_thesis_membership_is_source_backed(self):
        thesis = {"central_thesis": {"claim_ids": ["claim_a"]}, "headline_claim": "統一理論の帰結"}
        items = context_lens._thesis_upper_items_for_claim(thesis, {"claim_a": "db-1"}, "db-1", "doc-1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "thesis"
        assert item["element_id"] is None
        assert item["navigable"] is False
        assert item["relation"] == "supports_thesis"
        assert item["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED
        assert item["label"] == "統一理論の帰結"

    def test_support_structure_section_label_is_localized(self):
        thesis = {"support_structure": {"assumptions": [{"claim_ids": ["claim_a"]}]}}
        items = context_lens._thesis_upper_items_for_claim(thesis, {"claim_a": "db-1"}, "db-1", "doc-1")
        assert any("前提" in i["label"] for i in items)

    def test_no_match_returns_empty_list(self):
        thesis = {"central_thesis": {"claim_ids": ["claim_a"]}}
        assert context_lens._thesis_upper_items_for_claim(thesis, {"claim_a": "db-1"}, "db-999", "doc-1") == []

    def test_non_dict_thesis_is_ignored(self):
        assert context_lens._thesis_upper_items_for_claim(None, {}, "db-1", "doc-1") == []


class TestThesisUpperItemsForEquation:
    def test_central_thesis_equation_membership(self):
        thesis = {"central_thesis": {"equation_ids": ["eq_1"]}}
        items = context_lens._thesis_upper_items_for_equation(thesis, "eq_1", "doc-1")
        assert len(items) == 1
        assert items[0]["relation"] == "supports_thesis"
        assert items[0]["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED

    def test_no_match_returns_empty_list(self):
        thesis = {"central_thesis": {"equation_ids": ["eq_1"]}}
        assert context_lens._thesis_upper_items_for_equation(thesis, "eq_999", "doc-1") == []


# ---------------------------------------------------------------------------
# 導出チェーン所属の事実項目（claim / equation 共通ヘルパ）
# ---------------------------------------------------------------------------


class TestDerivationMembershipFacts:
    def test_step_level_hit_mentions_step_id(self):
        chains = [
            {
                "derivation_id": "d1",
                "steps": [{"step_id": "s1", "input_claim_ids": ["c1"]}],
            }
        ]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "derivation"
        assert item["element_id"] is None
        assert item["relation"] == "belongs_to_derivation"
        assert "d1" in item["label"] and "s1" in item["label"]
        assert item["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED

    def test_chain_level_hit_without_step_match(self):
        chains = [{"derivation_id": "d2", "steps": [], "assumption_ids": ["c9"]}]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c9")
        assert len(items) == 1
        assert "d2" in items[0]["label"]

    def test_no_match_returns_empty(self):
        chains = [{"derivation_id": "d1", "steps": []}]
        assert context_lens._derivation_membership_facts(chains, "doc-1", lambda x: False) == []

    def test_non_dict_chain_entries_are_skipped(self):
        assert context_lens._derivation_membership_facts([None, "bogus"], "doc-1", lambda x: True) == []


# ---------------------------------------------------------------------------
# 図: パーツ → 親図の経路（プロファイル / apparatus record / 昇格済み component）
# ---------------------------------------------------------------------------


class TestFigurePartItemsFromProfile:
    def test_reviewed_profile_parts_are_confirmed(self):
        profile = {"functions": [{"id": "f1", "name": "EOM"}]}
        items = context_lens._figure_part_items_from_profile(profile, "reviewed", "doc-1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "part"
        assert item["element_id"] is None
        assert item["navigable"] is False
        assert item["relation"] == "contains"
        assert item["relation_status"] == CONTEXT_STATUS_CONFIRMED
        assert item["label"] == "EOM"

    def test_pending_profile_parts_are_candidate(self):
        profile = {"subjects": [{"id": "s1", "name": "Laser diode"}]}
        items = context_lens._figure_part_items_from_profile(profile, "pending", "doc-1")
        assert items[0]["relation_status"] == CONTEXT_STATUS_CANDIDATE

    def test_empty_profile_returns_empty_list(self):
        assert context_lens._figure_part_items_from_profile({}, "reviewed", "doc-1") == []

    def test_falls_back_to_id_when_name_missing(self):
        profile = {"functions": [{"id": "f1"}]}
        items = context_lens._figure_part_items_from_profile(profile, "reviewed", "doc-1")
        assert items[0]["label"] == "f1"


class TestFigurePartItemsFromApparatusRecord:
    def test_parts_are_always_candidate_and_non_navigable(self):
        record = {"parts": [{"name": "EOM", "evidence_quote": "電気光学変調器"}]}
        items = context_lens._figure_part_items_from_apparatus_record(record, "doc-1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "part"
        assert item["navigable"] is False
        assert item["relation_status"] == CONTEXT_STATUS_CANDIDATE
        assert item["evidence_refs"] == ["電気光学変調器"]

    def test_no_parts_returns_empty_list(self):
        assert context_lens._figure_part_items_from_apparatus_record({}, "doc-1") == []
        assert context_lens._figure_part_items_from_apparatus_record(None, "doc-1") == []

    def test_blank_part_name_is_skipped(self):
        record = {"parts": [{"name": "  "}]}
        assert context_lens._figure_part_items_from_apparatus_record(record, "doc-1") == []


class TestFigureApparatusComponentItems:
    def test_promoted_component_is_navigable_theory_component(self):
        components = [{"id": "comp-1", "name": "Modulation apparatus", "review_status": "teacher_review_required"}]
        items = context_lens._figure_apparatus_component_items(components, "doc-1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "theory_component"
        assert item["element_id"] == "comp-1"
        assert item["navigable"] is True
        assert item["relation"] == "contains"
        assert item["relation_status"] == CONTEXT_STATUS_CANDIDATE  # teacher_review_required のまま

    def test_teacher_approved_component_is_confirmed(self):
        components = [{"id": "comp-2", "name": "Cavity", "review_status": "teacher_approved"}]
        items = context_lens._figure_apparatus_component_items(components, "doc-1")
        assert items[0]["relation_status"] == CONTEXT_STATUS_CONFIRMED

    def test_empty_list_returns_empty(self):
        assert context_lens._figure_apparatus_component_items([], "doc-1") == []


class TestMatchingFigureRecord:
    def test_matches_figure_table_record_by_caption_block_id(self):
        figure = {
            "id": "11111111-1111-1111-1111-111111111111",
            "figure_key": "fig_6_8",
            "caption_block_id": "cap-6-8",
        }
        record = {
            "figure_id": "fig_6.8",
            "source_location": {"caption_block_id": "cap-6-8"},
        }
        assert context_lens._matching_figure_record([record], figure) is record

    def test_matches_apparatus_record_by_db_uuid(self):
        figure = {
            "id": "11111111-1111-1111-1111-111111111111",
            "figure_key": "p39_i0",
            "caption_block_id": None,
        }
        record = {"figure_id": figure["id"], "figure_key": "p39_i0"}
        assert context_lens._matching_figure_record([record], figure) is record

    def test_matches_legacy_apparatus_record_by_figure_key(self):
        figure = {"id": "db-id", "figure_key": "fig_5_18", "caption_block_id": None}
        record = {"figure_id": "legacy-id", "figure_key": "fig_5_18"}
        assert context_lens._matching_figure_record([record], figure) is record

    def test_blank_keys_do_not_cross_link_orphan_images(self):
        figure = {"id": "db-id", "figure_key": "p39_i0", "caption_block_id": None}
        unrelated = {"figure_id": "other-id", "figure_key": "", "source_location": {}}
        assert context_lens._matching_figure_record([unrelated], figure) is None


class TestBuildFigureWiring:
    def test_caption_record_and_persisted_profile_feed_both_lanes(self, monkeypatch):
        figure_id = "11111111-1111-1111-1111-111111111111"
        figure_row = {
            "id": figure_id,
            "document_id": "doc-1",
            "figure_key": "fig_6_8",
            "figure_label": "Figure 6.8",
            "caption_text": "Transmissions under simultaneous resonance.",
            "caption_block_id": "cap-6-8",
            "page": 39,
            "bbox": [100, 100, 400, 300],
            "suggested_mode": "functional_diagram",
            "mode_reason": "vision",
            "analysis_profile": {"functions": [{"id": "laser", "name": "Laser"}]},
            "reviewed_mode": None,
            "mode_review_status": "pending",
            "reviewed_analysis_mode": None,
            "reviewed_analysis_profile": {},
            "analysis_review_status": "pending",
        }
        artifacts = {
            "document_structure": {
                "blocks": [{"block_id": "cap-6-8", "section_id": "sec-6"}],
                "sections": [{"section_id": "sec-6", "title": "Simultaneous resonance"}],
            },
            "figure_table_semantics": {
                "figures": [{
                    "figure_id": "fig_6.8",
                    "source_location": {"caption_block_id": "cap-6-8"},
                    "linked_claim_ids": ["claim-raw"],
                    "linked_component_candidates": [],
                }]
            },
            "apparatus_semantics": {"skipped_by_option": True},
        }

        monkeypatch.setattr(context_lens, "_load_figure_row", lambda _figure_id: figure_row)
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {"claim-raw": "claim-db"})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(
            context_lens,
            "_claims_by_id",
            lambda _ids: {"claim-db": {"text": "The cavity reaches simultaneous resonance"}},
        )
        monkeypatch.setattr(context_lens, "_load_apparatus_components", lambda _doc: [])
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_args, **_kwargs: [])

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE,
            element_id=figure_id, document_id="doc-1",
        ))

        assert result is not None
        assert any(item["element_type"] == "section" for item in result["upper"])
        assert any(item["element_id"] == "claim-db" for item in result["upper"])
        assert any(item["label"] == "Laser" for item in result["lower"])
        assert "figure_table_semantics" in result["focus"]["provenance"]
        assert any("画像解析オプション" in note for note in result["notes"])

    def test_figure_loader_selects_persisted_analysis_columns(self, monkeypatch):
        captured: dict = {}
        expected = {
            "id": "11111111-1111-1111-1111-111111111111",
            "analysis_profile": {"functions": [{"name": "Laser"}]},
            "reviewed_analysis_profile": {},
            "analysis_review_status": "pending",
        }

        class _Result:
            def mappings(self):
                return self

            def first(self):
                return expected

        class _Session:
            def execute(self, statement, params):
                captured["sql"] = str(statement)
                captured["params"] = params
                return _Result()

            def close(self):
                captured["closed"] = True

        monkeypatch.setattr(context_lens, "get_session", lambda: _Session())

        row = context_lens._load_figure_row(expected["id"])

        assert row == expected
        for column in (
            "analysis_profile", "reviewed_analysis_profile", "analysis_review_status",
            "suggested_mode", "reviewed_mode", "reviewed_analysis_mode",
        ):
            assert column in captured["sql"]
        assert captured["params"] == {"id": expected["id"]}
        assert captured["closed"] is True


# ---------------------------------------------------------------------------
# equation ヘルパ
# ---------------------------------------------------------------------------


class TestEquationByIdAndLabel:
    def test_equation_by_id_indexes_by_equation_id_key(self):
        records = [{"equation_id": "eq_1", "label": "F=ma"}, {"equation_id": "eq_2"}]
        index = context_lens._equation_by_id(records)
        assert set(index) == {"eq_1", "eq_2"}

    def test_equation_by_id_skips_records_without_id(self):
        records = [{"label": "no id"}]
        assert context_lens._equation_by_id(records) == {}

    def test_equation_label_prefers_reconstruction_plain_text(self):
        record = {
            "reconstruction": {"plain_text": "F equals m a"},
            "source_extraction": {"plain_text": "F = m*a (raw)"},
        }
        assert context_lens._equation_label(record) == "F equals m a"

    def test_equation_label_falls_back_to_source_extraction(self):
        record = {"reconstruction": {}, "source_extraction": {"latex": "F=ma"}}
        assert context_lens._equation_label(record) == "F=ma"

    def test_equation_label_falls_back_to_equation_id(self):
        record = {"equation_id": "eq_7"}
        assert context_lens._equation_label(record) == "eq_7"

    def test_equation_label_none_record_returns_empty_string(self):
        assert context_lens._equation_label(None) == ""


# ---------------------------------------------------------------------------
# evidence quote / structure index ヘルパ
# ---------------------------------------------------------------------------


class TestEvidenceQuote:
    def test_finds_matching_evidence_text(self):
        artifacts = {"evidence_registry": {"records": [{"evidence_id": "ev1", "evidence_text": "原文引用"}]}}
        assert context_lens._evidence_quote(artifacts, "ev1") == "原文引用"

    def test_no_match_returns_empty_string(self):
        artifacts = {"evidence_registry": {"records": [{"evidence_id": "ev1", "evidence_text": "x"}]}}
        assert context_lens._evidence_quote(artifacts, "ev999") == ""

    def test_missing_artifact_returns_empty_string(self):
        assert context_lens._evidence_quote({}, "ev1") == ""


class TestStructureIndexHelpers:
    def test_sections_by_id(self):
        artifacts = {"document_structure": {"sections": [{"section_id": "s1", "title": "Intro"}]}}
        assert context_lens._sections_by_id(artifacts)["s1"]["title"] == "Intro"

    def test_blocks_by_id(self):
        artifacts = {"document_structure": {"blocks": [{"block_id": "b1", "section_id": "s1"}]}}
        assert context_lens._blocks_by_id(artifacts)["b1"]["section_id"] == "s1"

    def test_section_label_handles_none(self):
        assert context_lens._section_label(None) == ""
        assert context_lens._section_label({"title": "  Discussion  "}) == "Discussion"


# ---------------------------------------------------------------------------
# build() 契約: shared_part は None・それ以外は fail-soft で常にキーが揃う
# ---------------------------------------------------------------------------


class TestBuildContract:
    def test_shared_part_returns_none(self):
        ref = ElementRef(
            scope="domain", element_type=ELEMENT_SHARED_PART, element_id="00000000-0000-0000-0000-000000000000",
            domain_key="particle_physics",
        )
        assert context_lens.build(ref) is None

    def test_nonexistent_claim_is_fail_soft_with_full_contract(self):
        ref = ElementRef(
            scope="document", element_type=ELEMENT_THEORY_CLAIM,
            element_id="00000000-0000-0000-0000-000000000000", document_id="does-not-exist",
        )
        result = context_lens.build(ref)
        assert result is not None
        assert set(result.keys()) == {"focus", "upper", "lower", "notes"}
        assert set(result["focus"].keys()) == {
            "element_type", "element_id", "document_id", "label",
            "intrinsic_summary", "contextual_role", "contextual_role_status", "provenance",
        }
        assert result["upper"] == []
        assert result["lower"] == []
        assert result["notes"]
        assert result["focus"]["contextual_role_status"] == CONTEXT_ROLE_STATUS_UNIDENTIFIED

    def test_nonexistent_component_is_fail_soft(self):
        ref = ElementRef(
            scope="document", element_type=ELEMENT_THEORY_COMPONENT,
            element_id="00000000-0000-0000-0000-000000000000", document_id="does-not-exist",
        )
        result = context_lens.build(ref)
        assert result["upper"] == [] and result["lower"] == []

    def test_nonexistent_figure_is_fail_soft(self):
        ref = ElementRef(
            scope="document", element_type=ELEMENT_FIGURE,
            element_id="00000000-0000-0000-0000-000000000000", document_id="does-not-exist",
        )
        result = context_lens.build(ref)
        assert result["upper"] == [] and result["lower"] == []

    def test_nonexistent_equation_is_fail_soft(self):
        ref = ElementRef(
            scope="document", element_type=ELEMENT_EQUATION,
            element_id="eq_does_not_exist", document_id="does-not-exist",
        )
        result = context_lens.build(ref)
        assert result["upper"] == [] and result["lower"] == []

    def test_unknown_element_type_returns_none(self):
        ref = ElementRef(scope="document", element_type="theory_claim", element_id="x", document_id="d")
        ref.element_type = "something_bogus"  # bypass validate(); build() must still fail-soft
        assert context_lens.build(ref) is None

    def test_build_never_raises_for_broken_builder(self, monkeypatch):
        def boom(_ref):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setitem(context_lens._BUILDERS, ELEMENT_THEORY_CLAIM, boom)
        ref = ElementRef(scope="document", element_type=ELEMENT_THEORY_CLAIM, element_id="c1", document_id="doc-1")
        result = context_lens.build(ref)
        assert result is not None
        assert result["notes"]
