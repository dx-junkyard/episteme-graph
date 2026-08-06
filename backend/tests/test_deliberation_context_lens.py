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
        stage_nodes = [{"id": "theory_op_0001", "graph_layer": "main", "label": "Equation system", "linked_claim_ids": ["c1"]}]
        for item in context_lens._stage_participation_items(stage_nodes, {"c1": "c1"}, ["c1"], "doc-1"):
            emitted_relations.add(item["relation"])
        # W層設計書 §16 で追加した evidence / derivation 用ヘルパも同じ語彙に収まること。
        for item in context_lens._chains_referencing_evidence(
            [{"derivation_id": "d1", "source_evidence_ids": ["ev_1"], "steps": []}], "doc-1", "ev_1"
        ):
            emitted_relations.add(item["relation"])
        for item in context_lens._section_items_from_ids(
            ["sec_1"], {"sec_1": {"section_id": "sec_1", "title": "Theory"}}, "doc-1"
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
        # "part"/"thesis"/"section"/"symbol"/"stage" は仕様上 navigable になり得ない
        # 要素型（設計書 §5 の ITEM 契約）。evidence / derivation は W層設計書 §16 で
        # 解決対象になったため navigable になり得る（test_deliberation_evidence_derivation.py）。
        for element_type in ("part", "thesis", "section", "symbol", "stage"):
            item = context_lens._item(
                element_type, "some-id", "doc-1", "label", "contains", CONTEXT_STATUS_CANDIDATE
            )
            assert item["navigable"] is False, element_type

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
    """優先順位（提示再設計 §4.4）: committed → self_described → structural →
    unidentified。3値タプル ``(text, status, source)`` を返し、``source`` が
    ``focus.contextual_role_source`` になる。"""

    def _stage_item(self, label="理論の土台", status=CONTEXT_STATUS_SOURCE_BACKED):
        return context_lens._item(
            "stage", None, "doc-1", label, "belongs_to_stage", status,
            group=context_lens.GROUP_STAGE,
        )

    def test_no_upper_items_and_no_annotations_is_unidentified(self):
        role, status, source = context_lens._derive_contextual_role([], [])
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        assert source == context_lens.ROLE_SOURCE_UNIDENTIFIED

    def test_structural_summary_uses_a_template_not_machine_concatenation(self):
        # RC7 の是正: 「label + relation_label」の機械連結は作らない。
        upper = [self._stage_item()]
        role, status, source = context_lens._derive_contextual_role(upper, [])
        assert role == "理論の土台の段階に位置づけられる"
        assert context_lens.RELATION_LABELS["belongs_to_stage"] not in role
        assert status == CONTEXT_STATUS_SOURCE_BACKED
        assert source == context_lens.ROLE_SOURCE_STRUCTURAL

    def test_structural_summary_ignores_groups_that_cannot_speak_of_placement(self):
        # 掲載節・記号・導出は「位置づけ」を語れないので役割の素材にしない。
        upper = [
            context_lens._item(
                "section", None, "doc-1", "2.1 節", "appears_in_section",
                CONTEXT_STATUS_SOURCE_BACKED, group=context_lens.GROUP_SECTION,
            )
        ]
        role, status, source = context_lens._derive_contextual_role(upper, [])
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        assert source == context_lens.ROLE_SOURCE_UNIDENTIFIED

    def test_structural_summary_ignores_candidate_and_unresolved_items(self):
        upper = [
            self._stage_item(status=CONTEXT_STATUS_CANDIDATE),
            context_lens._item(
                "thesis", None, "doc-1", "中心命題", "supports_thesis",
                CONTEXT_STATUS_SOURCE_BACKED, group=context_lens.GROUP_THESIS, unresolved=True,
            ),
        ]
        role, _status, source = context_lens._derive_contextual_role(upper, [])
        assert role is None
        assert source == context_lens.ROLE_SOURCE_UNIDENTIFIED

    def test_structural_summary_prefers_stage_over_thesis_and_claim(self):
        upper = [
            context_lens._item(
                "theory_claim", "c1", "doc-1", "主張A", "quantifies",
                CONTEXT_STATUS_SOURCE_BACKED, group=context_lens.GROUP_CLAIM,
            ),
            context_lens._item(
                "thesis", None, "doc-1", "中心命題", "supports_thesis",
                CONTEXT_STATUS_SOURCE_BACKED, group=context_lens.GROUP_THESIS,
            ),
            self._stage_item(),
        ]
        role, _status, _source = context_lens._derive_contextual_role(upper, [])
        assert role == "理論の土台の段階に位置づけられる"

    def test_committed_annotation_overrides_structural_summary(self):
        upper = [self._stage_item()]
        annotations = [
            {
                "status": ANNOTATION_STATUS_COMMITTED,
                "kind": ANNOTATION_KIND_INTERPRETATION,
                "body": {"text": "教員による人間確定の役割説明"},
            }
        ]
        role, status, source = context_lens._derive_contextual_role(upper, annotations)
        assert role == "教員による人間確定の役割説明"
        assert status == CONTEXT_STATUS_CONFIRMED
        assert source == context_lens.ROLE_SOURCE_COMMITTED

    def test_candidate_annotation_is_ignored(self):
        # candidate 状態の注釈は文脈上の役割として採用しない（W2: AI 出力は確定しない）。
        annotations = [
            {
                "status": ANNOTATION_STATUS_CANDIDATE,
                "kind": ANNOTATION_KIND_MEANING,
                "body": {"text": "まだ候補の説明"},
            }
        ]
        role, status, source = context_lens._derive_contextual_role([], annotations)
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        assert source == context_lens.ROLE_SOURCE_UNIDENTIFIED

    def test_blank_committed_text_falls_back_to_structural_summary(self):
        upper = [
            context_lens._item(
                "thesis", None, "doc-1", "中心命題", "supports_thesis",
                CONTEXT_STATUS_SOURCE_BACKED, group=context_lens.GROUP_THESIS,
            )
        ]
        annotations = [
            {"status": ANNOTATION_STATUS_COMMITTED, "kind": ANNOTATION_KIND_MEANING, "body": {"text": "   "}}
        ]
        role, status, source = context_lens._derive_contextual_role(upper, annotations)
        assert role == "中心命題を支える"
        assert status == CONTEXT_STATUS_SOURCE_BACKED
        assert source == context_lens.ROLE_SOURCE_STRUCTURAL

    def test_self_described_role_wins_over_structural_summary(self):
        # 要素が自分で名乗る役割（equation の role_in_argument + stage 訳、component の
        # role_in_thesis）は構造要約より優先する（人間確定注釈には劣後）。
        upper = [self._stage_item()]
        role, status, source = context_lens._derive_contextual_role(
            upper, [], fallback_role="Provides the theoretical basis",
            fallback_status=CONTEXT_STATUS_SOURCE_BACKED,
        )
        assert role == "Provides the theoretical basis"
        assert status == CONTEXT_STATUS_SOURCE_BACKED
        assert source == context_lens.ROLE_SOURCE_SELF_DESCRIBED

    def test_committed_annotation_overrides_fallback_role(self):
        annotations = [
            {"status": ANNOTATION_STATUS_COMMITTED, "kind": ANNOTATION_KIND_INTERPRETATION, "body": {"text": "人間確定"}}
        ]
        role, status, source = context_lens._derive_contextual_role(
            [], annotations, fallback_role="Provides the theoretical basis"
        )
        assert role == "人間確定"
        assert status == CONTEXT_STATUS_CONFIRMED
        assert source == context_lens.ROLE_SOURCE_COMMITTED

    def test_fallback_role_prevents_unidentified_even_with_no_upper(self):
        role, status, source = context_lens._derive_contextual_role(
            [], [], fallback_role="States the central result"
        )
        assert role == "States the central result"
        assert status == CONTEXT_STATUS_SOURCE_BACKED
        assert source == context_lens.ROLE_SOURCE_SELF_DESCRIBED

    def test_blank_fallback_role_is_ignored(self):
        role, status, source = context_lens._derive_contextual_role([], [], fallback_role="   ")
        assert role is None
        assert status == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        assert source == context_lens.ROLE_SOURCE_UNIDENTIFIED


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


class TestArtifactClaimTextIndex:
    """課題A: ClaimObjectBuilder artifact から DB 未解決 claim_id の本文を補う索引。"""

    def test_indexes_by_claim_id_using_text(self):
        claims = [
            {"claim_id": "claim_span_001_sub01", "text": "運動量は保存される"},
            {"claim_id": "claim_span_002_sub01", "text": "別の主張"},
        ]
        index = context_lens._artifact_claim_text_index(claims)
        assert index == {
            "claim_span_001_sub01": "運動量は保存される",
            "claim_span_002_sub01": "別の主張",
        }

    def test_records_without_claim_id_are_skipped(self):
        claims = [{"text": "claim_id の無い行"}, {"claim_id": "", "text": "空文字の claim_id"}]
        assert context_lens._artifact_claim_text_index(claims) == {}

    def test_blank_text_falls_back_to_normalized_text(self):
        claims = [{"claim_id": "claim_span_003_sub01", "text": "  ", "normalized_text": "正規化済み本文"}]
        assert context_lens._artifact_claim_text_index(claims) == {
            "claim_span_003_sub01": "正規化済み本文",
        }

    def test_no_text_and_no_normalized_text_is_omitted(self):
        claims = [{"claim_id": "claim_span_004_sub01"}]
        assert context_lens._artifact_claim_text_index(claims) == {}

    def test_non_dict_entries_are_skipped(self):
        assert context_lens._artifact_claim_text_index([None, "bogus"]) == {}


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


class TestThesisContextUpperItems:
    """theory_components.thesis_context → 中心命題 / 支持構造 上位項目（B: 上位リンク拡充）。"""

    def test_central_thesis_node_uses_headline_and_is_source_backed(self):
        thesis = {"headline_claim": "側帯波の共振応答"}
        thesis_context = {"supports_thesis_node_ids": ["central_thesis"]}
        items = context_lens._thesis_context_upper_items(thesis, thesis_context, "doc-1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "thesis"
        assert item["element_id"] is None
        assert item["navigable"] is False
        assert item["relation"] == "supports_thesis"
        assert item["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED
        assert item["label"] == "側帯波の共振応答"
        assert item["evidence_refs"] == ["central_thesis"]

    def test_support_structure_node_is_localized_and_carries_excerpt(self):
        thesis = {
            "support_structure": {
                "assumptions": [{"text": "理想共振条件を仮定する"}],
            }
        }
        thesis_context = {"supports_thesis_node_ids": ["support:assumptions:0"]}
        items = context_lens._thesis_context_upper_items(thesis, thesis_context, "doc-1")
        assert len(items) == 1
        assert items[0]["label"].startswith("支持構造「前提」")
        assert "理想共振条件を仮定する" in items[0]["label"]
        assert items[0]["evidence_refs"] == ["support:assumptions:0"]

    def test_missing_thesis_artifact_degrades_to_headline_default(self):
        thesis_context = {"supports_thesis_node_ids": ["central_thesis", "support:direct_supports:1"]}
        items = context_lens._thesis_context_upper_items(None, thesis_context, "doc-1")
        assert [i["label"] for i in items] == ["中心命題", "支持構造「直接支持」"]

    def test_unknown_node_id_form_is_kept_not_guessed(self):
        thesis_context = {"supports_thesis_node_ids": ["weird_ref_x"]}
        items = context_lens._thesis_context_upper_items({}, thesis_context, "doc-1")
        assert len(items) == 1
        assert items[0]["label"] == "weird_ref_x"
        assert items[0]["relation"] == "supports_thesis"

    def test_no_node_ids_returns_empty(self):
        assert context_lens._thesis_context_upper_items({}, {"supports_thesis_node_ids": []}, "doc-1") == []
        assert context_lens._thesis_context_upper_items({}, None, "doc-1") == []
        assert context_lens._thesis_context_upper_items({}, {}, "doc-1") == []

    def test_blank_and_non_string_node_ids_are_skipped(self):
        thesis_context = {"supports_thesis_node_ids": ["", "  ", "central_thesis"]}
        items = context_lens._thesis_context_upper_items({"headline_claim": "H"}, thesis_context, "doc-1")
        assert [i["label"] for i in items] == ["H"]


class TestSectionItemsFromIds:
    """source_chunks → 掲載セクション上位項目（B: 上位リンク拡充・副軸）。"""

    def test_resolvable_section_ids_become_source_backed_items(self):
        sections_by_id = {"s1": {"section_id": "s1", "title": "2. Cavity response"}}
        items = context_lens._section_items_from_ids(["s1"], sections_by_id, "doc-1")
        assert len(items) == 1
        assert items[0]["element_type"] == "section"
        assert items[0]["relation"] == "appears_in_section"
        assert items[0]["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED
        assert items[0]["label"] == "2. Cavity response"

    def test_unlabeled_section_ids_are_dropped(self):
        # 見出しの引けない section_id は本流の位置づけノイズになるので出さない。
        items = context_lens._section_items_from_ids(["s_missing"], {}, "doc-1")
        assert items == []

    def test_duplicate_section_ids_are_collapsed(self):
        sections_by_id = {"s1": {"section_id": "s1", "title": "Intro"}}
        items = context_lens._section_items_from_ids(["s1", "s1", ""], sections_by_id, "doc-1")
        assert len(items) == 1


# ---------------------------------------------------------------------------
# 導出チェーン所属の事実項目（claim / equation 共通ヘルパ）
# ---------------------------------------------------------------------------


class TestDerivationMembershipFacts:
    """§5.2 / CP2: 内部 ID をラベルに出さず、equation は向きを分けて判定する。"""

    def test_step_level_hit_keeps_step_id_out_of_the_label(self):
        chains = [
            {
                "derivation_id": "d1",
                "chain_type": "claim_chain",
                "operation": "derive",
                "steps": [{"step_id": "s1", "input_claim_ids": ["c1"]}],
            }
        ]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c1")
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "derivation"
        # W層設計書 §16: derivation は refs.py の解決対象になったため element_id
        # （= chain の derivation_id）を持ち、navigable になる。step は独立の要素に
        # しないので element_id は chain 単位で、step_id は evidence_refs に残る。
        assert item["element_id"] == "d1"
        assert item["navigable"] is True
        assert item["evidence_refs"] == ["d1", "s1"]
        assert item["relation"] == "belongs_to_derivation"
        # EC3′: 「導出「d1」のステップ「s1」」という内部 ID2連の機械連結は作らない。
        assert "d1" not in item["label"] and "s1" not in item["label"]
        assert item["label"] == "導出（主張の導出）"
        assert item["qualifier"] == "claim_chain"
        assert item["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED

    def test_all_matching_steps_are_recorded_not_only_the_first(self):
        # RC3: break で最初の1件に潰さない（複数ステップ関与は evidence_refs に残す）。
        chains = [
            {
                "derivation_id": "d1",
                "chain_type": "claim_chain",
                "steps": [
                    {"step_id": "s1", "input_claim_ids": ["c1"]},
                    {"step_id": "s2", "output_claim_ids": ["c1"]},
                ],
            }
        ]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c1")
        assert len(items) == 1
        assert items[0]["evidence_refs"] == ["d1", "s1", "s2"]

    def test_chain_level_hit_without_step_match(self):
        chains = [{"derivation_id": "d2", "chain_type": "claim_chain", "steps": [], "assumption_ids": ["c9"]}]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c9")
        assert len(items) == 1
        assert items[0]["element_id"] == "d2"
        assert items[0]["navigable"] is True
        assert "d2" not in items[0]["label"]

    def test_chain_without_derivation_id_is_not_navigable(self):
        # derivation_id が空の chain は中心に据えられない（推測で ID を作らない）。
        chains = [{"derivation_id": "", "steps": [], "assumption_ids": ["c9"]}]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c9")
        assert len(items) == 1
        assert items[0]["element_id"] is None
        assert items[0]["navigable"] is False

    def test_no_match_returns_empty(self):
        chains = [{"derivation_id": "d1", "steps": []}]
        assert context_lens._derivation_membership_facts(chains, "doc-1", lambda x: False) == []

    def test_non_dict_chain_entries_are_skipped(self):
        assert context_lens._derivation_membership_facts([None, "bogus"], "doc-1", lambda x: True) == []

    def test_equation_kind_splits_input_and_output_directions(self):
        chains = [
            {"derivation_id": "d_in", "chain_type": "equation_chain", "steps": [],
             "input_equation_ids": ["eq_a"], "output_equation_ids": ["eq_b"]},
            {"derivation_id": "d_out", "chain_type": "equation_chain", "steps": [],
             "input_equation_ids": ["eq_z"], "output_equation_ids": ["eq_a"]},
        ]
        items = context_lens._derivation_membership_facts(
            chains, "doc-1", lambda x: x == "eq_a", kind="equation"
        )
        by_id = {i["element_id"]: i for i in items}
        assert by_id["d_in"]["relation"] == "feeds_derivation"
        assert by_id["d_in"]["group"] == context_lens.GROUP_DERIVATION_OUT
        assert by_id["d_out"]["relation"] == "produced_by_derivation"
        assert by_id["d_out"]["group"] == context_lens.GROUP_DERIVATION_IN

    def test_equation_that_is_both_input_and_output_is_intermediate(self):
        chains = [
            {"derivation_id": "d1", "chain_type": "equation_chain", "steps": [],
             "input_equation_ids": ["eq_a"], "output_equation_ids": ["eq_a"]},
        ]
        items = context_lens._derivation_membership_facts(
            chains, "doc-1", lambda x: x == "eq_a", kind="equation"
        )
        assert items[0]["relation"] == "used_in_derivation"
        assert "中間量として" in items[0]["sublabel"]

    def test_declared_intermediate_equation_is_intermediate(self):
        chains = [
            {"derivation_id": "d1", "chain_type": "equation_chain", "steps": [],
             "intermediate_equation_ids": ["eq_a"]},
        ]
        items = context_lens._derivation_membership_facts(
            chains, "doc-1", lambda x: x == "eq_a", kind="equation"
        )
        assert items[0]["relation"] == "used_in_derivation"

    def test_claim_kind_keeps_the_neutral_membership_relation(self):
        # 向きの契約は equation focus のためのもの。claim は従来どおり所属を述べる。
        chains = [{"derivation_id": "d1", "chain_type": "claim_chain", "steps": [],
                   "output_claim_ids": ["c1"]}]
        items = context_lens._derivation_membership_facts(chains, "doc-1", lambda x: x == "c1")
        assert items[0]["relation"] == "belongs_to_derivation"

    def test_conditions_and_operations_go_to_the_sublabel(self):
        chains = [
            {
                "derivation_id": "d1", "chain_type": "equation_chain", "operation": "transform",
                "conditions": ["Perturbations stay small."],
                "steps": [{"step_id": "s1", "operation": "linearize", "input_equation_ids": ["eq_a"]}],
            }
        ]
        items = context_lens._derivation_membership_facts(
            chains, "doc-1", lambda x: x == "eq_a", kind="equation"
        )
        sublabel = items[0]["sublabel"]
        assert "操作: 線形化" in sublabel
        assert "条件: Perturbations stay small." in sublabel


# ---------------------------------------------------------------------------
# 課題B: TheoryOperationGraph main ステージノードへの claim 交差接続
# ---------------------------------------------------------------------------


class TestStageParticipationItems:
    def test_overlap_via_claim_lookup_translation_emits_item(self):
        # グラフ側は agent 側 claim ID のまま、component 側は DB UUID という表記差を
        # claim_lookup（agent ID → DB UUID、DB UUID は恒等写像）越しに一致させる。
        graph_nodes = [
            {
                "id": "theory_op_0001", "graph_layer": "main",
                "component_type": "TheoryOperationNode", "label": "Equation system",
                "linked_claim_ids": ["claim_agent_1"],
            }
        ]
        claim_lookup = {"claim_agent_1": "claim-db-1", "claim-db-1": "claim-db-1"}
        items = context_lens._stage_participation_items(
            graph_nodes, claim_lookup, ["claim-db-1"], "doc-1"
        )
        assert len(items) == 1
        item = items[0]
        assert item["element_type"] == "stage"
        assert item["element_id"] is None
        assert item["navigable"] is False
        # CP4 / RC10: 英語 stage 表示名ではなく訳語（element_vocab が正本）。
        assert item["label"] == "式の体系"
        assert item["qualifier"] == "equation_system"
        assert item["group"] == context_lens.GROUP_STAGE
        assert item["relation"] == "participates_in_stage"
        assert item["relation_status"] == CONTEXT_STATUS_CANDIDATE
        assert item["evidence_refs"] == ["claim-db-1"]

    def test_equation_detail_and_debug_layers_are_ignored(self):
        graph_nodes = [
            {"id": "eq-step-1", "graph_layer": "equation_detail", "linked_claim_ids": ["c1"], "label": "step"},
            {"id": "fallback-1", "graph_layer": "debug", "linked_claim_ids": ["c1"], "label": "fallback"},
        ]
        items = context_lens._stage_participation_items(graph_nodes, {"c1": "c1"}, ["c1"], "doc-1")
        assert items == []

    def test_no_overlap_returns_empty(self):
        graph_nodes = [{"id": "theory_op_0002", "graph_layer": "main", "linked_claim_ids": ["c9"], "label": "Elimination"}]
        items = context_lens._stage_participation_items(graph_nodes, {}, ["c1"], "doc-1")
        assert items == []

    def test_no_component_claim_ids_returns_empty_without_scanning(self):
        graph_nodes = [{"id": "theory_op_0003", "graph_layer": "main", "linked_claim_ids": ["c1"], "label": "X"}]
        assert context_lens._stage_participation_items(graph_nodes, {}, [], "doc-1") == []

    def test_blank_label_degrades_to_a_generic_stage_label_not_the_node_id(self):
        # EC3′: 解決できないときも内部 ID（theory_op_0004）はラベルにしない。
        graph_nodes = [{"id": "theory_op_0004", "graph_layer": "main", "linked_claim_ids": ["c1"], "label": ""}]
        items = context_lens._stage_participation_items(graph_nodes, {"c1": "c1"}, ["c1"], "doc-1")
        assert items[0]["label"] == "理論の段階"
        assert items[0]["unresolved"] is True
        assert items[0]["evidence_refs"] == ["c1"]

    def test_missing_graph_layer_defaults_to_main(self):
        # normalizer.py の既存規約（getattr default "main"）に合わせ、graph_layer が
        # 無いノードも main として扱う（後方互換）。
        graph_nodes = [{"id": "theory_op_0005", "linked_claim_ids": ["c1"], "label": "Legacy stage"}]
        items = context_lens._stage_participation_items(graph_nodes, {"c1": "c1"}, ["c1"], "doc-1")
        assert len(items) == 1

    def test_evidence_refs_capped_at_three_and_sorted(self):
        graph_nodes = [
            {
                "id": "theory_op_0006", "graph_layer": "main", "label": "Consistency relation",
                "linked_claim_ids": ["c1", "c2", "c3", "c4"],
            }
        ]
        items = context_lens._stage_participation_items(
            graph_nodes, {}, ["c1", "c2", "c3", "c4"], "doc-1"
        )
        assert len(items) == 1
        assert items[0]["evidence_refs"] == ["c1", "c2", "c3"]


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

    def test_matches_by_normalized_figure_key_despite_chapter_label_punctuation(self):
        """バグB回帰: figure_table_semantics の figure_id は句読点保持
        (``fig_3.3``)、document_figures.figure_key はアンダースコア正規化済み
        (``fig_3_3``)。caption_block_id が無くても normalize_figure_join_key 経由で
        両者が一致すること（章番号付きラベルでの表記ゆれ）。"""
        figure = {"id": "db-id-3-3", "figure_key": "fig_3_3", "caption_block_id": None}
        record = {"figure_id": "fig_3.3", "source_location": {}}
        assert context_lens._matching_figure_record([record], figure) is record

    def test_empty_normalized_keys_do_not_match_each_other(self):
        """正規化後に空文字列になる組み合わせ同士も一致とみなさない（P4 の裏返し:
        情報が無い図同士を誤接続しない）。"""
        figure = {"id": "", "figure_key": "", "caption_block_id": ""}
        record = {"figure_id": "", "figure_key": "", "source_location": {}}
        assert context_lens._matching_figure_record([record], figure) is None


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
# F3: 図単位の装置コンポーネント対応 (figure_id/figure_key 一致 vs legacy 縮退)
# ---------------------------------------------------------------------------


def _base_figure_row(figure_id: str, figure_key: str = "fig_3_1") -> dict:
    return {
        "id": figure_id,
        "document_id": "doc-1",
        "figure_key": figure_key,
        "figure_label": "Figure 3.1",
        "caption_text": "An apparatus diagram.",
        "caption_block_id": None,
        "page": 10,
        "bbox": [0, 0, 100, 100],
        "suggested_mode": None,
        "mode_reason": None,
        "analysis_profile": {},
        "reviewed_mode": None,
        "mode_review_status": "pending",
        "reviewed_analysis_mode": None,
        "reviewed_analysis_profile": {},
        "analysis_review_status": "pending",
    }


def _patch_build_figure_common(
    monkeypatch,
    figure_row: dict,
    artifacts: dict,
    apparatus_components: list,
    *,
    claim_lookup: dict | None = None,
    claims_by_id: dict | None = None,
    components_with_evidence_claims: list | None = None,
) -> None:
    monkeypatch.setattr(context_lens, "_load_figure_row", lambda _id: figure_row)
    monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
    monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: dict(claim_lookup or {}))
    monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
    monkeypatch.setattr(context_lens, "_claims_by_id", lambda ids: {
        i: claims_by_id[i] for i in ids if claims_by_id and i in claims_by_id
    })
    monkeypatch.setattr(context_lens, "_components_by_id", lambda _ids: {})
    monkeypatch.setattr(context_lens, "_load_apparatus_components", lambda _doc: apparatus_components)
    monkeypatch.setattr(
        context_lens, "_load_components_with_evidence_claims",
        lambda _doc: list(components_with_evidence_claims or []),
    )
    monkeypatch.setattr(context_lens, "presentation_payload", lambda *_a, **_k: {})
    monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])


class TestBuildFigureApparatusComponentScoping:
    def test_figure_id_match_shows_only_matching_component_without_paper_wide_note(self, monkeypatch):
        figure_id = "33333333-3333-3333-3333-333333333333"
        figure_row = _base_figure_row(figure_id)
        apparatus_components = [
            {
                "id": "comp-match", "name": "Matching device", "component_type": "apparatus",
                "status": "candidate", "review_status": "teacher_review_required", "summary": "",
                "source_scope": {"figure_id": figure_id, "figure_key": "fig_3_1"},
            },
            {
                "id": "comp-other", "name": "Other figure device", "component_type": "apparatus",
                "status": "candidate", "review_status": "teacher_review_required", "summary": "",
                "source_scope": {"figure_id": "other-fig-id", "figure_key": "fig_9_9"},
            },
        ]
        _patch_build_figure_common(monkeypatch, figure_row, {}, apparatus_components)

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        lower_ids = {item["element_id"] for item in result["lower"]}
        assert "comp-match" in lower_ids
        assert "comp-other" not in lower_ids
        assert not any("論文単位" in note for note in result["notes"])

    def test_figure_key_match_associates_component_even_when_figure_id_differs(self, monkeypatch):
        figure_id = "55555555-5555-5555-5555-555555555555"
        figure_row = _base_figure_row(figure_id, figure_key="fig_7_2")
        apparatus_components = [
            {
                "id": "comp-keyed", "name": "Keyed device", "component_type": "apparatus",
                "status": "candidate", "review_status": "teacher_review_required", "summary": "",
                # figure_id is stale/mismatched but figure_key matches.
                "source_scope": {"figure_id": "stale-id-not-matching", "figure_key": "fig_7_2"},
            },
        ]
        _patch_build_figure_common(monkeypatch, figure_row, {}, apparatus_components)

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        lower_ids = {item["element_id"] for item in result["lower"]}
        assert "comp-keyed" in lower_ids
        assert not any("論文単位" in note for note in result["notes"])

    def test_legacy_components_without_figure_key_fall_back_to_document_wide_list_with_note(self, monkeypatch):
        figure_id = "44444444-4444-4444-4444-444444444444"
        figure_row = _base_figure_row(figure_id)
        apparatus_components = [
            {
                "id": "comp-legacy", "name": "Legacy device", "component_type": "apparatus",
                "status": "candidate", "review_status": "teacher_review_required", "summary": "",
                "source_scope": {"document_id": "doc-1", "legacy_ids": ["apparatus_x"]},
            },
        ]
        _patch_build_figure_common(monkeypatch, figure_row, {}, apparatus_components)

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        lower_ids = {item["element_id"] for item in result["lower"]}
        assert "comp-legacy" in lower_ids
        assert any("論文単位" in note for note in result["notes"])

    def test_no_apparatus_components_yields_no_note(self, monkeypatch):
        figure_id = "22222222-2222-2222-2222-222222222222"
        figure_row = _base_figure_row(figure_id)
        _patch_build_figure_common(monkeypatch, figure_row, {}, [])

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        assert not any("論文単位" in note for note in result["notes"])


# ---------------------------------------------------------------------------
# F3: 図 → component（claim 交差）/ 図 → thesis（linked_claim_ids 経由）
# ---------------------------------------------------------------------------


class TestBuildFigureClaimComponentIntersection:
    def test_component_sharing_linked_claim_appears_in_upper_as_inferred_candidate(self, monkeypatch):
        figure_id = "66666666-6666-6666-6666-666666666666"
        figure_row = _base_figure_row(figure_id)
        artifacts = {
            "figure_table_semantics": {
                "figures": [{
                    "figure_id": "fig_3.1",
                    "figure_key": "fig_3_1",
                    "linked_claim_ids": ["claim-raw-1"],
                    "linked_component_candidates": [],
                }]
            },
        }
        _patch_build_figure_common(
            monkeypatch, figure_row, artifacts, [],
            claim_lookup={"claim-raw-1": "claim-db-1"},
            claims_by_id={"claim-db-1": {"text": "運動量は保存される"}},
            components_with_evidence_claims=[
                {"id": "comp-shared", "name": "Shared component", "evidence_claims": ["claim-db-1"]},
                {"id": "comp-unrelated", "name": "Unrelated component", "evidence_claims": ["claim-db-9"]},
            ],
        )

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        matches = [i for i in result["upper"] if i["element_id"] == "comp-shared"]
        assert len(matches) == 1
        assert matches[0]["relation"] == "related_component_candidate"
        assert matches[0]["relation_status"] == CONTEXT_STATUS_CANDIDATE
        assert not any(i["element_id"] == "comp-unrelated" for i in result["upper"])

    def test_no_linked_claims_means_no_intersection_lookup_needed(self, monkeypatch):
        figure_id = "77777777-7777-7777-7777-777777777777"
        figure_row = _base_figure_row(figure_id)
        artifacts = {
            "figure_table_semantics": {
                "figures": [{
                    "figure_id": "fig_3.1",
                    "figure_key": "fig_3_1",
                    "linked_claim_ids": [],
                    "linked_component_candidates": [],
                }]
            },
        }
        _patch_build_figure_common(
            monkeypatch, figure_row, artifacts, [],
            components_with_evidence_claims=[
                {"id": "comp-should-not-appear", "name": "x", "evidence_claims": []},
            ],
        )

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        assert not any(i["element_id"] == "comp-should-not-appear" for i in result["upper"])


class TestBuildFigureThesisViaLinkedClaim:
    def test_linked_claim_via_thesis_appears_in_upper(self, monkeypatch):
        figure_id = "88888888-8888-8888-8888-888888888888"
        figure_row = _base_figure_row(figure_id)
        artifacts = {
            "figure_table_semantics": {
                "figures": [{
                    "figure_id": "fig_3.1",
                    "figure_key": "fig_3_1",
                    "linked_claim_ids": ["claim-raw-1"],
                    "linked_component_candidates": [],
                }]
            },
            "thesis_reconstruction": {
                "headline_claim": "統一理論の帰結",
                "central_thesis": {"claim_ids": ["claim-raw-1"]},
            },
        }
        _patch_build_figure_common(
            monkeypatch, figure_row, artifacts, [],
            claim_lookup={"claim-raw-1": "claim-db-1"},
            claims_by_id={"claim-db-1": {"text": "運動量は保存される"}},
        )

        result = context_lens._build_figure(ElementRef(
            scope="document", element_type=ELEMENT_FIGURE, element_id=figure_id, document_id="doc-1",
        ))

        assert any(
            item["element_type"] == "thesis" and item["relation"] == "supports_thesis"
            for item in result["upper"]
        )


# ---------------------------------------------------------------------------
# F3: claim レンズ側の既存 fallback（linked_claim_ids 逆引き → 図が lower に出る）
# ---------------------------------------------------------------------------


class TestBuildClaimWiring:
    def test_linked_figure_fallback_appears_in_lower(self, monkeypatch):
        claim_id = "claim-db-1"
        claim_row = {
            "id": claim_id,
            "document_id": "doc-1",
            "claim_type": "empirical",
            "text": "運動量は保存される",
            "normalized_text": "運動量は保存される",
            "support_status": "source_backed",
            "review_status": "teacher_approved",
            "evidence_text": "",
            "source_scope": {},
        }
        artifacts = {
            "figure_table_semantics": {
                "figures": [{
                    "figure_id": "fig-uuid-1",
                    "caption": "Figure 3.1: apparatus diagram.",
                    "linked_claim_ids": ["claim-raw-1"],
                }]
            },
        }
        monkeypatch.setattr(context_lens, "_load_claim_row", lambda _id: claim_row)
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {"claim-raw-1": claim_id})
        monkeypatch.setattr(context_lens, "_components_supporting_claim", lambda _doc, _cid: [])
        monkeypatch.setattr(context_lens, "_claims_by_id", lambda _ids: {})
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])

        result = context_lens._build_claim(ElementRef(
            scope="document", element_type=ELEMENT_THEORY_CLAIM, element_id=claim_id, document_id="doc-1",
        ))

        assert result is not None
        matches = [
            i for i in result["lower"]
            if i["element_type"] == "figure" and i["relation"] == "evidenced_by_figure"
        ]
        assert len(matches) == 1
        assert matches[0]["element_id"] == "fig-uuid-1"
        assert matches[0]["label"] == "Figure 3.1: apparatus diagram."


# ---------------------------------------------------------------------------
# B: コンポーネント上位リンク拡充（thesis_context / section）の統合配線
# ---------------------------------------------------------------------------


class TestBuildComponentUpperFromThesisContext:
    """グラフノードでない component でも thesis_context から本流へ結びつく（B）。"""

    def _component_row(self):
        # 画面再現: component_graph にノードが無く、evidence_claims は agent 側 span ID
        # のまま（DB claim に解決できない）。thesis_context が本流への唯一の橋になる。
        return {
            "id": "comp-db-1",
            "document_id": "doc-1",
            "name": "Ideal resonance assumptions",
            "component_type": "theory",
            "summary": "Sets the resonance and approximation regime.",
            "status": "candidate",
            "review_status": "teacher_review_required",
            "dependencies": [],
            "evidence_claims": ["claim_span_001_sub01"],
            "source_scope": {"legacy_ids": ["comp-agent-1"]},
            "thesis_context": {
                "role_in_thesis": "Provides the theoretical basis",
                "supports_thesis_node_ids": ["central_thesis", "support:assumptions:0"],
                "support_role": "assumption",
                "support_distance_to_headline_claim": 2,
            },
            "source_chunks": ["chunk-1"],
        }

    def _patch(self, monkeypatch, *, artifacts):
        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: self._component_row())
        # component_graph は空（node is None）— 画面のケースを再現する。
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        # evidence_claims は解決できない（screenshot 同様、ID そのまま）。
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claims_by_id", lambda _ids: {})
        monkeypatch.setattr(context_lens, "_chunk_section_ids", lambda _ids: ["s1"])
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        # build() が触る generic ブロックは DB を叩かないよう空リンクに固定。
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

    def _ref(self):
        return ElementRef(
            scope="document", element_type=ELEMENT_THEORY_COMPONENT,
            element_id="comp-db-1", document_id="doc-1",
        )

    def test_thesis_context_produces_upper_and_role(self, monkeypatch):
        artifacts = {
            "thesis_reconstruction": {
                "headline_claim": "側帯波の共振応答",
                "support_structure": {"assumptions": [{"text": "理想共振条件を仮定する"}]},
            },
            "document_structure": {"sections": [{"section_id": "s1", "title": "2. Cavity response"}]},
        }
        self._patch(monkeypatch, artifacts=artifacts)

        result = context_lens.build(self._ref())

        # 上位が「未同定」ではなくなる: 中心命題 + 支持構造への接続が出る。
        thesis_items = [i for i in result["upper"] if i["element_type"] == "thesis"]
        labels = {i["label"] for i in thesis_items}
        assert "側帯波の共振応答" in labels
        assert any(l.startswith("支持構造「前提」") for l in labels)
        assert all(i["relation"] == "supports_thesis" for i in thesis_items)
        assert all(i["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED for i in thesis_items)

        # 掲載セクションも副軸として出る。
        assert any(
            i["element_type"] == "section" and i["label"] == "2. Cavity response"
            for i in result["upper"]
        )

        # この文脈での役割は role_in_thesis を採用（未同定にならない）。
        assert result["focus"]["contextual_role"] == "Provides the theoretical basis"
        assert result["focus"]["contextual_role_status"] == CONTEXT_STATUS_SOURCE_BACKED
        assert "thesis_context" in result["focus"]["provenance"]
        assert "chunks" in result["focus"]["provenance"]

    def test_role_in_thesis_without_node_ids_still_sets_role(self, monkeypatch):
        row = self._component_row()
        row["thesis_context"] = {"role_in_thesis": "States the central result"}
        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: row)
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claims_by_id", lambda _ids: {})
        monkeypatch.setattr(context_lens, "_chunk_section_ids", lambda _ids: [])
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

        result = context_lens.build(self._ref())

        # supports_thesis_node_ids が無くても、役割説明で「未同定」を脱する。
        assert result["focus"]["contextual_role"] == "States the central result"
        assert result["focus"]["contextual_role_status"] == CONTEXT_STATUS_SOURCE_BACKED

    def test_no_thesis_context_stays_unidentified(self, monkeypatch):
        row = self._component_row()
        row["thesis_context"] = None
        row["source_chunks"] = []
        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: row)
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claims_by_id", lambda _ids: {})
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

        result = context_lens.build(self._ref())

        # thesis_context も上位も無ければ従来どおり未同定（推測で埋めない）。
        assert not [i for i in result["upper"] if i["element_type"] == "thesis"]
        assert result["focus"]["contextual_role_status"] == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        assert "thesis_context" not in result["focus"]["provenance"]


# ---------------------------------------------------------------------------
# 課題A: evidence_claims 解決の統合配線（バッチ取得 + artifact フォールバック）
# ---------------------------------------------------------------------------


class TestBuildComponentEvidenceClaimsResolution:
    """theory_components.evidence_claims は DB UUID（remap 済みの親 claim）と agent 側
    atomic sub-claim ID の混在。DB 解決可能な ID は1回のバッチ取得、それ以外は
    ClaimObjectBuilder artifact の本文、どちらも無ければ生ID表示に縮退する。"""

    def _component_row(self):
        return {
            "id": "comp-db-1",
            "document_id": "doc-1",
            "name": "Some component",
            "component_type": "theory",
            "summary": "",
            "status": "candidate",
            "review_status": "teacher_review_required",
            "dependencies": [],
            "evidence_claims": ["claim-db-1", "claim_span_001_sub01", "claim-totally-unknown"],
            "source_scope": {},
            "thesis_context": None,
            "source_chunks": [],
        }

    def _ref(self):
        return ElementRef(
            scope="document", element_type=ELEMENT_THEORY_COMPONENT,
            element_id="comp-db-1", document_id="doc-1",
        )

    def test_resolves_db_artifact_and_raw_id_fallbacks_in_order(self, monkeypatch):
        artifacts = {
            "claim_object_builder": {
                "claims": [{"claim_id": "claim_span_001_sub01", "text": "サブ主張の本文"}],
            },
        }
        claims_by_id_calls: list[list[str]] = []

        def fake_claims_by_id(ids):
            claims_by_id_calls.append(list(ids))
            return {"claim-db-1": {"text": "親主張の本文"}}

        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: self._component_row())
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {"claim-db-1": "claim-db-1"})
        monkeypatch.setattr(context_lens, "_claims_by_id", fake_claims_by_id)
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

        result = context_lens.build(self._ref())

        claim_items = [i for i in result["lower"] if i["element_type"] == "theory_claim"]
        assert len(claim_items) == 3
        resolved, artifact_backed, unknown = claim_items

        assert resolved["element_id"] == "claim-db-1"
        assert resolved["label"] == "親主張の本文"
        assert resolved["navigable"] is True
        assert resolved["relation"] == "backed_by_claim"
        assert resolved["relation_status"] == CONTEXT_STATUS_SOURCE_BACKED
        assert resolved["evidence_refs"] == []

        assert artifact_backed["element_id"] is None
        assert artifact_backed["label"] == "サブ主張の本文"
        assert artifact_backed["navigable"] is False
        assert artifact_backed["evidence_refs"] == ["claim_span_001_sub01"]

        assert unknown["element_id"] is None
        assert unknown["label"] == "claim-totally-unknown"
        assert unknown["navigable"] is False
        assert unknown["evidence_refs"] == []

        # N+1 回避: 3件の evidence_claims に対しバッチ取得は1回だけ。
        assert len(claims_by_id_calls) == 1
        assert claims_by_id_calls[0] == ["claim-db-1"]

    def test_db_fetch_failure_still_allows_artifact_fallback(self, monkeypatch):
        # _claims_by_id が例外を投げても（_safe が1呼び出しだけに握る粒度のため）、
        # artifact フォールバックは独立して機能する。
        row = self._component_row()
        row["evidence_claims"] = ["claim-db-1", "claim_span_001_sub01"]
        artifacts = {
            "claim_object_builder": {
                "claims": [{"claim_id": "claim_span_001_sub01", "text": "サブ主張の本文"}],
            },
        }

        def boom(_ids):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: row)
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: {"claim-db-1": "claim-db-1"})
        monkeypatch.setattr(context_lens, "_claims_by_id", boom)
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

        result = context_lens.build(self._ref())

        claim_items = [i for i in result["lower"] if i["element_type"] == "theory_claim"]
        assert len(claim_items) == 2
        # DB 取得は失敗したので、解決できた ID は生 db_id ラベルへ縮退する（P4）。
        assert claim_items[0]["element_id"] == "claim-db-1"
        assert claim_items[0]["label"] == "claim-db-1"
        # 一方、未解決 ID の artifact フォールバックは失敗の影響を受けない。
        assert claim_items[1]["label"] == "サブ主張の本文"


# ---------------------------------------------------------------------------
# 課題B: TheoryOperationGraph main ステージノードへの claim 交差接続（統合配線）
# ---------------------------------------------------------------------------


class TestBuildComponentStageParticipation:
    """node が無い component でも main ステージノードとの claim 交差で「どの理論段階に
    関与するか」を upper に出す。node があるときは member_of 経由で親 main ノードが
    既に出るため、stage 参加項目は追加しない。"""

    def _component_row(self, *, evidence_claims):
        return {
            "id": "comp-db-1",
            "document_id": "doc-1",
            "name": "Some component",
            "component_type": "theory",
            "summary": "",
            "status": "candidate",
            "review_status": "teacher_review_required",
            "dependencies": [],
            "evidence_claims": evidence_claims,
            "source_scope": {},
            "thesis_context": None,
            "source_chunks": [],
        }

    def _ref(self):
        return ElementRef(
            scope="document", element_type=ELEMENT_THEORY_COMPONENT,
            element_id="comp-db-1", document_id="doc-1",
        )

    def _patch(self, monkeypatch, *, row, graph_nodes, claim_lookup):
        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: row)
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": graph_nodes, "edges": []})
        monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: {})
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: {})
        monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: dict(claim_lookup))
        monkeypatch.setattr(context_lens, "_claims_by_id", lambda _ids: {})
        monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *_a, **_k: [])

    def test_node_is_none_emits_stage_participation_item(self, monkeypatch):
        row = self._component_row(evidence_claims=["claim-db-1"])
        # グラフ側は agent 側 claim ID（linked_claim_ids は remap されない）。
        graph_nodes = [
            {
                "id": "theory_op_0001", "graph_layer": "main",
                "component_type": "TheoryOperationNode", "label": "Equation system",
                "linked_claim_ids": ["claim_agent_1"],
            }
        ]
        claim_lookup = {"claim_agent_1": "claim-db-1", "claim-db-1": "claim-db-1"}
        self._patch(monkeypatch, row=row, graph_nodes=graph_nodes, claim_lookup=claim_lookup)

        result = context_lens.build(self._ref())

        stage_items = [i for i in result["upper"] if i["element_type"] == "stage"]
        assert len(stage_items) == 1
        assert stage_items[0]["label"] == "式の体系"
        assert stage_items[0]["element_id"] is None
        assert stage_items[0]["navigable"] is False
        assert stage_items[0]["relation"] == "participates_in_stage"
        assert stage_items[0]["relation_status"] == CONTEXT_STATUS_CANDIDATE

    def test_node_present_suppresses_stage_participation(self, monkeypatch):
        row = self._component_row(evidence_claims=["claim-db-1"])
        graph_nodes = [
            # 自身が equation_detail ノードとしてグラフに存在する（node is not None）。
            {"id": "comp-db-1", "graph_layer": "equation_detail", "label": "step", "linked_claim_ids": []},
            {
                "id": "theory_op_0001", "graph_layer": "main", "label": "Equation system",
                "linked_claim_ids": ["claim_agent_1"],
            },
        ]
        claim_lookup = {"claim_agent_1": "claim-db-1", "claim-db-1": "claim-db-1"}
        self._patch(monkeypatch, row=row, graph_nodes=graph_nodes, claim_lookup=claim_lookup)

        result = context_lens.build(self._ref())

        assert not [i for i in result["upper"] if i["element_type"] == "stage"]


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

    def test_equation_label_never_uses_the_formula_itself(self):
        """RC2 / EH1: plain_text / latex / raw_text はどの段でもラベルにしない。"""
        record = {
            "equation_id": "eq_tex_b14",
            "reconstruction": {"plain_text": "F equals m a"},
            "source_extraction": {"plain_text": "F = m*a (raw)", "latex": "F=ma"},
            "semantics": {"summary": "Force relates to acceleration."},
        }
        label = context_lens._equation_label(record)
        assert label.text == "Force relates to acceleration."
        assert "F equals m a" not in label.text
        assert "F=ma" not in label.text

    def test_equation_label_uses_the_paper_equation_number_when_present(self):
        record = {"equation_id": "eq_2_7", "semantics": {}}
        assert context_lens._equation_label(record).text == "式 (2.7)"

    def test_equation_label_composes_symbol_and_role(self):
        record = {
            "equation_id": "eq_tex_b14",
            "semantics": {
                "role_in_argument": "definition",
                "defined_symbols": [{"symbol": "delta", "definition_status": "defined"}],
            },
        }
        assert context_lens._equation_label(record).text == "delta を定義する式"

    def test_equation_label_degrades_to_a_generic_label_with_unresolved(self):
        # 合成 ID（eq_tex_*）は式番号ではないので、素材が尽きたら一般ラベル。
        label = context_lens._equation_label({"equation_id": "eq_tex_b14", "semantics": {}})
        assert label.text == "数式"
        assert label.unresolved is True

    def test_equation_label_none_record_is_a_generic_unresolved_label(self):
        label = context_lens._equation_label(None)
        assert label.text == "数式"
        assert label.unresolved is True


# ---------------------------------------------------------------------------
# evidence quote / structure index ヘルパ
# ---------------------------------------------------------------------------


class TestEvidenceRecordLookup:
    """§5.6: evidence の逐語は ``_evidence_record_for`` でレコードごと引き、
    ``labels.evidence_label`` が「逐語そのもの」をラベルにする（旧 ``_evidence_quote``
    の文字列だけ返すヘルパは、意味を evidence_refs に押し込む経路とともに廃止した）。"""

    def test_finds_matching_evidence_record(self):
        artifacts = {"evidence_registry": {"records": [{"evidence_id": "ev1", "evidence_text": "原文引用"}]}}
        record = context_lens._evidence_record_for(artifacts, "ev1")
        assert record is not None
        assert record["evidence_text"] == "原文引用"

    def test_no_match_returns_none(self):
        artifacts = {"evidence_registry": {"records": [{"evidence_id": "ev1", "evidence_text": "x"}]}}
        assert context_lens._evidence_record_for(artifacts, "ev999") is None

    def test_missing_artifact_returns_none(self):
        assert context_lens._evidence_record_for({}, "ev1") is None


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
            "generic",
        }
        assert result["upper"] == []
        assert result["lower"] == []
        assert result["notes"]
        assert result["focus"]["contextual_role_status"] == CONTEXT_ROLE_STATUS_UNIDENTIFIED
        # 設計書 §6 Phase 3: リンク無し・読み取り失敗（DB 非接続環境を含む）は None に
        # 縮退する（fail-soft・既存の縮退契約を壊さない）。
        assert result["focus"]["generic"] is None

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


# ---------------------------------------------------------------------------
# focus.generic（Phase 3: 汎用×固有の結線）
# 正本: docs/features/hierarchical_context_explanation_design.md §6。
# ---------------------------------------------------------------------------


class TestGenericBlockForFocus:
    """``_generic_block_for_focus``: confirmed な同一性リンク先の active な L層エントリ
    のみを focus.generic として返す。candidate/rejected・非active・読み取り失敗は None
    （fail-soft・KN-3 の「確定は人間のみ」を継承）。
    """

    def _ref(self, document_id="doc-1"):
        return ElementRef(
            scope="document", element_type=ELEMENT_THEORY_COMPONENT, element_id="c1",
            document_id=document_id,
        )

    def test_no_links_returns_none(self, monkeypatch):
        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", lambda *a, **k: [])
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_candidate_link_is_ignored(self, monkeypatch):
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: [{"status": "candidate", "shared_part_id": "sp-1"}],
        )
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_rejected_link_is_ignored(self, monkeypatch):
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: [{"status": "rejected", "shared_part_id": "sp-1"}],
        )
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_confirmed_link_but_entry_not_active_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: [{"status": "confirmed", "shared_part_id": "sp-1"}],
        )
        monkeypatch.setattr(
            context_lens.library_store_mod, "get_entry",
            lambda entry_id: {
                "status": "retired", "name": "X", "summary": "Y",
                "standardization_status": "novel",
            },
        )
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_confirmed_link_but_entry_missing_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: [{"status": "confirmed", "shared_part_id": "sp-1"}],
        )
        monkeypatch.setattr(context_lens.library_store_mod, "get_entry", lambda entry_id: None)
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_confirmed_link_and_active_entry_returns_generic_block(self, monkeypatch):
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: [{"status": "confirmed", "shared_part_id": "sp-1"}],
        )
        monkeypatch.setattr(
            context_lens.library_store_mod, "get_entry",
            lambda entry_id: {
                "status": "active", "name": "EOM", "summary": "electro-optic modulator",
                "standardization_status": "field_standard",
            },
        )
        generic = context_lens._generic_block_for_focus(self._ref())
        assert generic == {
            "entry_id": "sp-1",
            "name": "EOM",
            "summary": "electro-optic modulator",
            "standardization_status": "field_standard",
        }

    def test_lookup_failure_is_fail_soft(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(context_lens.identity_links_mod, "list_for_instance", boom)
        assert context_lens._generic_block_for_focus(self._ref()) is None

    def test_domain_scoped_ref_returns_none_without_lookup(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(
            context_lens.identity_links_mod, "list_for_instance",
            lambda *a, **k: calls.append(1) or [],
        )
        ref = ElementRef(scope="domain", element_type=ELEMENT_SHARED_PART, element_id="sp-1", domain_key="dk")
        assert context_lens._generic_block_for_focus(ref) is None
        assert not calls

    def test_build_attaches_generic_key_from_successful_builder(self, monkeypatch):
        """build() は builder 成功時にも focus.generic を必ず付与すること。"""

        def fake_builder(ref):
            return {
                "focus": {
                    "element_type": ref.element_type, "element_id": ref.element_id,
                    "document_id": ref.document_id, "label": "L", "intrinsic_summary": "",
                    "contextual_role": None, "contextual_role_status": CONTEXT_ROLE_STATUS_UNIDENTIFIED,
                    "provenance": [],
                },
                "upper": [], "lower": [], "notes": [],
            }

        monkeypatch.setitem(context_lens._BUILDERS, ELEMENT_THEORY_COMPONENT, fake_builder)
        monkeypatch.setattr(
            context_lens, "_generic_block_for_focus",
            lambda ref: {"entry_id": "sp-1", "name": "N", "summary": "S", "standardization_status": "standard"},
        )
        result = context_lens.build(self._ref())
        assert result["focus"]["generic"] == {
            "entry_id": "sp-1", "name": "N", "summary": "S", "standardization_status": "standard",
        }
