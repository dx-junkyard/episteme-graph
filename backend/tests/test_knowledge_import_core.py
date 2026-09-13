"""束 → 行の純関数（knowledge_transfer_design.md §4.2 / KT2・KT4・KO7）。

DB を触らずに固定するのは:

- **KT4**: ``stable_key`` は取り込み先の ``document_id`` で再計算され、束の値は
  ``import.source_stable_key`` に事実として残る（同じ束を別の教材へ入れるとキーが変わる）。
- **T-1 / KT2**: 取り込み行の ``review_status`` は常に ``teacher_review_required``、
  component の ``status`` は ``candidate``。束の値は ``source_review_status`` に残るだけ。
- **KO7**: 語彙外の ``claim_type`` / ``component_type`` は丸められ、自称は ``*_text`` に残る。
- 束の manifest / zip の検証（必須ファイル・スキーマ版・JSON 不正・上限）。
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.knowledge_import import apply as import_apply  # noqa: E402
from core.knowledge_import import rows as import_rows  # noqa: E402
from core.knowledge_import.bundle import (  # noqa: E402
    MAX_BUNDLE_BYTES,
    MIN_SCHEMA_VERSION,
    BundleError,
    parse_bundle,
)

DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"

SOURCE = {
    "export_id": "export_x",
    "object_type": "document",
    "object_id": "doc-source",
    "document_ids": ["doc-source"],
    "exported_at": "2026-09-13T00:00:00Z",
    "bundle_sha256": "deadbeef",
    "schema_version": "0.3.0",
}

CLAIMS = [
    {
        "claim_id": "claim_1",
        "document_id": "doc-source",
        "text": "The observable is defined by the ratio.",
        "normalized_text": "the observable is defined by the ratio",
        "claim_type": "definition",
        "support_status": "source_backed",
        # 束の中で teacher_approved でも、取り込み先の承認にはならない（T-1）。
        "review_status": "teacher_approved",
        "stable_key": "k1:from-the-other-instance",
        "source_evidence_ids": ["ev_1"],
        "equation_ids": ["eq_1"],
        "concepts": [{"name": "ratio", "normalized": "ratio"}],
        "source_scope": {"section_id": "sec_1", "span_id": "span_1"},
    },
    {
        "claim_id": "synth_claim_2",
        "document_id": "doc-source",
        "text": "eq_1 relates A and B.",
        "claim_type": "made_up_type_not_in_the_vocabulary",
        "synthesis_method": "equation_synthesis",
        "source_evidence_ids": [],
    },
]

COMPONENTS = [
    {
        "component_id": "comp_1",
        "name": "Observable construction",
        "component_type": "DomainObservableComponent",
        "primary_operation": "construct_observable",
        "summary": "…",
        "review_status": "teacher_approved",
        "status": "teacher_reviewed",
        "linked_claim_ids": ["claim_1"],
        "evidence_claims": ["claim_1"],
        "linked_evidence_ids": ["ev_1"],
        "stable_key": "k1:comp-from-the-other-instance",
    },
]

EQUATIONS = [
    {
        "equation_id": "eq_1",
        "label": "(1)",
        "latex": "R = A / B",
        "plain_text": "R = A / B",
        "source_location": {"block_id": "blk_1", "section_id": "sec_1", "page": 3},
        "equation_type": "definition",
    },
]

EVIDENCE = [
    {
        "evidence_id": "ev_1",
        "block_id": "blk_1",
        "section_id": "sec_1",
        "page": 3,
        "span_start": 0,
        "span_end": 12,
        "evidence_text": "the ratio R",
    },
]

CHAINS = [
    {
        "derivation_id": "d_1",
        "chain_type": "equation_chain",
        "steps": [
            {
                "step_id": "step_1",
                "operation": "derive_result",
                "input_equation_ids": ["eq_1"],
                "output_equation_ids": [],
                "review_status": "teacher_approved",
            }
        ],
    },
]


def _bundle_zip(*, manifest_overrides=None, drop=(), broken_json=None) -> bytes:
    manifest = {
        "export_schema_version": "0.3.0",
        "export_id": "export_x",
        "exported_at": "2026-09-13T00:00:00Z",
        "app": {"name": "episteme-graph"},
        "scope": {"type": "document", "document_id": "doc-source", "document_ids": ["doc-source"]},
    }
    manifest.update(manifest_overrides or {})
    files = {
        "manifest.json": manifest,
        "claims/claims.json": {"claims": CLAIMS},
        "components/components.json": {"components": COMPONENTS},
        "equations/equations.json": {"equations": EQUATIONS},
        "evidence/evidence_snippets.json": {"snippets": EVIDENCE},
        "derivations/derivation_chains.json": {"chains": CHAINS},
        "graph/component_graph.json": {
            "nodes": [{"component_id": "comp_1", "graph_layer": "main"}],
            "edges": [],
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in files.items():
            if name in drop:
                continue
            if broken_json and name == broken_json:
                zf.writestr(name, "{ not json")
                continue
            zf.writestr(name, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 束の読み取りと検証
# ---------------------------------------------------------------------------


class TestParseBundle:
    def test_reads_every_section(self):
        bundle = parse_bundle(_bundle_zip())
        assert bundle.schema_version == "0.3.0"
        assert bundle.export_id == "export_x"
        assert bundle.source_object_type == "document"
        assert bundle.source_object_id == "doc-source"
        assert bundle.source_document_ids == ["doc-source"]
        assert bundle.counts() == {
            "claims": 2, "components": 1, "equations": 1,
            "evidence": 1, "derivation_steps": 1, "graph_nodes": 1,
        }
        assert bundle.sha256

    def test_optional_sections_may_be_absent(self):
        bundle = parse_bundle(_bundle_zip(drop=("equations/equations.json",)))
        assert bundle.equations == []
        assert bundle.counts()["equations"] == 0

    @pytest.mark.parametrize("missing", ["manifest.json", "claims/claims.json", "components/components.json"])
    def test_required_files_are_required(self, missing):
        with pytest.raises(BundleError) as exc:
            parse_bundle(_bundle_zip(drop=(missing,)))
        assert missing in " ".join(exc.value.facts)

    def test_schema_version_below_minimum_is_rejected(self):
        with pytest.raises(BundleError) as exc:
            parse_bundle(_bundle_zip(manifest_overrides={"export_schema_version": "0.2.0"}))
        facts = " ".join(exc.value.facts)
        assert "0.2.0" in facts and MIN_SCHEMA_VERSION in facts

    def test_missing_schema_version_is_rejected(self):
        with pytest.raises(BundleError):
            parse_bundle(_bundle_zip(manifest_overrides={"export_schema_version": ""}))

    def test_broken_json_is_rejected(self):
        with pytest.raises(BundleError) as exc:
            parse_bundle(_bundle_zip(broken_json="claims/claims.json"))
        assert "JSON" in " ".join(exc.value.facts)

    def test_not_a_zip_is_rejected(self):
        with pytest.raises(BundleError):
            parse_bundle(b"not a zip at all")

    def test_empty_is_rejected(self):
        with pytest.raises(BundleError):
            parse_bundle(b"")

    def test_oversized_is_rejected_before_unzipping(self):
        with pytest.raises(BundleError) as exc:
            parse_bundle(b"x" * (MAX_BUNDLE_BYTES + 1))
        assert "上限" in " ".join(exc.value.facts)


# ---------------------------------------------------------------------------
# KT4: stable_key の再計算
# ---------------------------------------------------------------------------


class TestStableKeyRecomputation:
    def test_claim_key_is_not_the_bundle_key(self):
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        first = items[0]
        assert first["stable_key"].startswith("k1:")
        assert first["stable_key"] != "k1:from-the-other-instance"
        # 束の値は捨てずに出所として残す。
        block = first["values"]["source_scope"][import_rows.IMPORT_PROVENANCE_KEY]
        assert block["source_stable_key"] == "k1:from-the-other-instance"

    def test_claim_key_differs_per_target_document(self):
        a = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        b = import_rows.claim_rows(DOC_B, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        assert a[0]["stable_key"] != b[0]["stable_key"]

    def test_claim_key_is_deterministic(self):
        a = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        b = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        assert [i["stable_key"] for i in a] == [i["stable_key"] for i in b]

    def test_claim_key_matches_phase1_for_the_same_material(self):
        """Phase 1 と同じ関数・同じ材料（出典 block 集合）でキーを作る。"""
        from core.knowledge_objects import stable_key as ko_keys

        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        expected = ko_keys.claim_stable_key(
            DOC_A, "the observable is defined by the ratio", ["blk_1"]
        )
        assert items[0]["stable_key"] == expected

    def test_component_key_uses_label_operation_and_blocks(self):
        from core.knowledge_objects import stable_key as ko_keys

        items = import_rows.component_rows(
            DOC_A, COMPONENTS, claims=CLAIMS, evidence=EVIDENCE, source=SOURCE
        )
        expected = ko_keys.component_stable_key(
            DOC_A, "Observable construction", "construct_observable", ["blk_1"]
        )
        assert items[0]["stable_key"] == expected

    def test_equation_and_evidence_keys_are_recomputed(self):
        from core.knowledge_objects import stable_key as ko_keys

        eq = import_rows.equation_rows(DOC_A, EQUATIONS, source=SOURCE)[0]
        assert eq["stable_key"] == ko_keys.equation_stable_key(
            DOC_A, "R = A / B", "R = A / B", "blk_1", "(1)"
        )
        ev = import_rows.evidence_rows(DOC_A, EVIDENCE, source=SOURCE)[0]
        assert ev["stable_key"] == ko_keys.evidence_stable_key(DOC_A, "blk_1", "the ratio R")

    def test_derivation_step_key_uses_recomputed_equation_keys(self):
        keys = import_rows.equation_key_map(DOC_A, EQUATIONS)
        step = import_rows.derivation_step_rows(
            DOC_A, CHAINS, equations=EQUATIONS, source=SOURCE
        )[0]
        from core.knowledge_objects import stable_key as ko_keys

        assert step["stable_key"] == ko_keys.derivation_step_stable_key(
            DOC_A, "derive_result", [keys["eq_1"]], []
        )

    def test_colliding_keys_are_deduped_deterministically(self):
        twins = [
            {"claim_id": "claim_a", "text": "same", "normalized_text": "same"},
            {"claim_id": "claim_b", "text": "same", "normalized_text": "same"},
        ]
        items = import_rows.dedupe(import_rows.claim_rows(DOC_A, twins, source=SOURCE))
        keys = sorted(i["stable_key"] for i in items)
        assert len(set(keys)) == 2
        assert any(k.endswith("#2") for k in keys)


# ---------------------------------------------------------------------------
# T-1 / KT2: 取り込みは候補で着地する
# ---------------------------------------------------------------------------


class TestImportLandsAsCandidate:
    def test_claim_review_status_is_always_teacher_review_required(self):
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        for item in items:
            assert item["values"]["review_status"] == "teacher_review_required"

    def test_component_status_and_review_status_are_candidate(self):
        items = import_rows.component_rows(
            DOC_A, COMPONENTS, claims=CLAIMS, evidence=EVIDENCE, source=SOURCE
        )
        values = items[0]["values"]
        assert values["status"] == "candidate"
        assert values["review_status"] == "teacher_review_required"

    def test_the_bundle_review_status_is_kept_as_a_fact(self):
        claim = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)[0]
        block = claim["values"]["source_scope"][import_rows.IMPORT_PROVENANCE_KEY]
        assert block["source_review_status"] == "teacher_approved"

        comp = import_rows.component_rows(
            DOC_A, COMPONENTS, claims=CLAIMS, evidence=EVIDENCE, source=SOURCE
        )[0]
        cblock = comp["values"]["agent_payload"][import_rows.IMPORT_PROVENANCE_KEY]
        assert cblock["source_review_status"] == "teacher_approved"
        assert cblock["source_status"] == "teacher_reviewed"

    def test_knowledge_objects_also_land_as_candidates(self):
        for item in (
            import_rows.equation_rows(DOC_A, EQUATIONS, source=SOURCE)
            + import_rows.evidence_rows(DOC_A, EVIDENCE, source=SOURCE)
            + import_rows.derivation_step_rows(DOC_A, CHAINS, equations=EQUATIONS, source=SOURCE)
        ):
            assert item["values"]["review_status"] == "teacher_review_required"

    def test_maturity_source_is_imported_not_teacher_reviewed(self):
        comp = import_rows.component_rows(DOC_A, COMPONENTS, source=SOURCE)[0]
        assert comp["values"]["maturity_source"] == "imported"


# ---------------------------------------------------------------------------
# KO7: 型語彙の丸め
# ---------------------------------------------------------------------------


class TestVocabularyRounding:
    def test_unknown_claim_type_rounds_and_keeps_its_own_name(self):
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        synthesized = next(i for i in items if i["agent_id"] == "synth_claim_2")
        assert synthesized["values"]["claim_type"] == "unknown"
        assert synthesized["values"]["claim_type_text"] == "made_up_type_not_in_the_vocabulary"

    def test_known_claim_type_survives(self):
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        assert items[0]["values"]["claim_type"] == "definition"

    def test_component_type_rounds_and_keeps_its_own_name(self):
        comp = import_rows.component_rows(DOC_A, COMPONENTS, source=SOURCE)[0]
        assert comp["values"]["component_type"] in ("theory", "DomainObservableComponent")
        assert comp["values"]["component_type_text"] == "DomainObservableComponent"

    def test_apparatus_types_are_kept_verbatim(self):
        comp = import_rows.component_rows(
            DOC_A, [{"component_id": "c", "name": "n", "component_type": "apparatus"}], source=SOURCE
        )[0]
        assert comp["values"]["component_type"] == "apparatus"

    def test_claim_origin_is_derived_without_inventing_a_parent(self):
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        origins = {i["agent_id"]: i["values"]["origin"] for i in items}
        assert origins["claim_1"] == "claim_object"
        assert origins["synth_claim_2"] == "equation_synthesis"
        # 親子リンクは束に無いので atomic_rewrite を名乗らない。
        assert "atomic_rewrite" not in set(origins.values())


# ---------------------------------------------------------------------------
# 出所と参照
# ---------------------------------------------------------------------------


class TestProvenanceAndReferences:
    def test_provenance_block_records_the_bundle_not_the_person(self):
        claim = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)[0]
        block = claim["values"]["source_scope"][import_rows.IMPORT_PROVENANCE_KEY]
        assert block["export_id"] == "export_x"
        assert block["bundle_sha256"] == "deadbeef"
        assert block["source_document_id"] == "doc-source"
        assert block["source_id"] == "claim_1"
        for key in block:
            assert "by" not in key or key in ("source_review_status",), key
        assert "imported_by" not in block

    def test_claims_do_not_land_on_a_chunk(self):
        """束は本文を運ばないので出典 chunk には着地しない（事実として NULL）。"""
        claim = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)[0]
        assert claim["values"]["chunk_id"] is None

    def test_component_claim_refs_are_remapped_to_new_uuids(self):
        comp = import_rows.component_rows(
            DOC_A, COMPONENTS, claims=CLAIMS, evidence=EVIDENCE,
            claim_id_map={"claim_1": "uuid-claim-1"}, source=SOURCE,
        )[0]
        assert comp["values"]["linked_claim_ids"] == ["uuid-claim-1"]
        assert comp["values"]["evidence_claims"] == ["uuid-claim-1"]

    def test_unmappable_refs_are_kept_as_is(self):
        comp = import_rows.component_rows(
            DOC_A, COMPONENTS, claims=CLAIMS, evidence=EVIDENCE,
            claim_id_map={}, source=SOURCE,
        )[0]
        assert comp["values"]["linked_claim_ids"] == ["claim_1"]

    def test_component_is_not_bound_to_a_course(self):
        comp = import_rows.component_rows(DOC_A, COMPONENTS, source=SOURCE)[0]
        assert comp["values"]["course_id"] is None

    def test_items_without_an_id_are_skipped(self):
        assert import_rows.claim_rows(DOC_A, [{"text": "no id"}], source=SOURCE) == []
        assert import_rows.component_rows(DOC_A, [{"name": "no id"}], source=SOURCE) == []


# ---------------------------------------------------------------------------
# グラフの書き換え（純関数）
# ---------------------------------------------------------------------------


class TestGraphRewrite:
    def test_nodes_and_edges_are_remapped(self):
        graph = import_apply.rewrite_graph(
            {
                "nodes": [{"component_id": "comp_1", "label": "Theory basis"}],
                "edges": [{
                    "source_component_id": "comp_1",
                    "target_component_id": "comp_2",
                    "evidence": {"evidence_claims": ["claim_1"]},
                }],
            },
            document_id=DOC_A,
            component_id_map={"comp_1": "uuid-comp-1"},
            claim_id_map={"claim_1": "uuid-claim-1"},
        )
        node = graph["nodes"][0]
        assert node["id"] == node["component_id"] == "uuid-comp-1"
        assert node["agent_component_id"] == "comp_1"
        edge = graph["edges"][0]
        assert edge["source_component_id"] == "uuid-comp-1"
        # 写せない ID はそのまま残す（推測で結び直さない）。
        assert edge["target_component_id"] == "comp_2"
        assert edge["evidence"]["evidence_claims"] == ["uuid-claim-1"]
        assert graph["document_id"] == DOC_A
        assert graph["scope"] == {"level": "paper"}

    def test_input_graph_is_not_mutated(self):
        source = {"nodes": [{"component_id": "comp_1"}], "edges": []}
        import_apply.rewrite_graph(
            source, document_id=DOC_A, component_id_map={"comp_1": "u"}, claim_id_map={}
        )
        assert source["nodes"][0] == {"component_id": "comp_1"}


# ---------------------------------------------------------------------------
# dry-run の事実文
# ---------------------------------------------------------------------------


class TestPlanFacts:
    def test_facts_state_the_two_rules(self):
        bundle = parse_bundle(_bundle_zip())
        facts = import_apply.plan_facts(bundle, has_live=False, replace=False)
        joined = "".join(facts)
        assert "未確認" in joined
        assert "計算し直します" in joined

    def test_live_rows_without_replace_say_nothing_is_written(self):
        bundle = parse_bundle(_bundle_zip())
        joined = "".join(import_apply.plan_facts(bundle, has_live=True, replace=False))
        assert "置き換えを明示しない限り" in joined

    def test_live_rows_with_replace_say_confirmed_state_is_kept(self):
        bundle = parse_bundle(_bundle_zip())
        joined = "".join(import_apply.plan_facts(bundle, has_live=True, replace=True))
        assert "教員が確定した状態は保たれます" in joined

    def test_missing_sections_are_reported_as_facts(self):
        bundle = parse_bundle(_bundle_zip(drop=("evidence/evidence_snippets.json",)))
        joined = "".join(import_apply.plan_facts(bundle, has_live=False, replace=False))
        assert "根拠" in joined

    def test_facts_carry_no_numbers(self):
        bundle = parse_bundle(_bundle_zip())
        for fact in import_apply.plan_facts(bundle, has_live=True, replace=True):
            assert not any(ch.isdigit() for ch in fact), fact
