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
            DOC_A, "derive_result", [keys["eq_1"]], [],
            derivation_id="d_1", step_index=0,
        )

    def test_step_ids_are_made_unique_across_chains(self):
        """V-2: agent の ``step_id`` はチェーン内でしか一意でない。

        同名 step を持つ 2 チェーンが 1 件に潰れると、live の一意制約
        （``uq_knowledge_derivation_steps_stable_key_live``）で取り込みが落ちる。
        文書内一意な ID の作り方は ``knowledge_objects`` 側が正本。
        """
        from core.knowledge_objects import stable_key as ko_keys

        chains = [
            {"derivation_id": "d_1", "steps": [
                {"step_id": "step_001", "operation": "derive_result"},
            ]},
            {"derivation_id": "d_2", "steps": [
                {"step_id": "step_001", "operation": "derive_result"},
            ]},
        ]
        items = import_rows.dedupe(
            import_rows.derivation_step_rows(DOC_A, chains, source=SOURCE)
        )
        assert [i["agent_id"] for i in items] == [
            ko_keys.derivation_step_agent_id("d_1", "step_001"),
            ko_keys.derivation_step_agent_id("d_2", "step_001"),
        ]
        # 同じ operation でもチェーンが違えば別の行として残る（潰れない）。
        assert len({i["stable_key"] for i in items}) == 2
        # 束の中の元の ID は出所として残る。
        provenance = items[0]["values"]["agent_payload"]["import"]
        assert provenance["source_id"] == "step_001"
        assert provenance["source_derivation_id"] == "d_1"

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
            + import_rows.derivation_step_rows(DOC_A, CHAINS, equations=EQUATIONS, source=SOURCE)
        ):
            assert item["values"]["review_status"] == "teacher_review_required"

    def test_evidence_rows_do_not_write_a_review_status(self):
        """V-1: ``knowledge_evidence`` に ``review_status`` 列は無い（逐語の写し）。

        書き手（``persistence._evidence_items``）と同じ扱い。ここに値を積むと
        実 DB では INSERT が落ちる。
        """
        for item in import_rows.evidence_rows(DOC_A, EVIDENCE, source=SOURCE):
            assert "review_status" not in item["values"]

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

    def test_claim_origin_is_taken_from_the_bundle_or_left_alone(self):
        """V-11: origin は内容列。束が黙っているなら**書かない**（上書きしない）。"""
        items = import_rows.claim_rows(DOC_A, CLAIMS, evidence=EVIDENCE, source=SOURCE)
        by_id = {i["agent_id"]: i["values"] for i in items}
        # 束が origin を持たない claim_1 は origin を含まない（既存行を触らない）。
        assert "origin" not in by_id["claim_1"]
        # 合成 claim は決定論で判定できるので書く。
        assert by_id["synth_claim_2"]["origin"] == "equation_synthesis"

    def test_a_declared_origin_is_restored(self):
        """V-11: 束が載せた由来はそのまま復元する（atomic_rewrite を含む）。"""
        claims = [
            {"claim_id": "c_parent", "text": "parent", "origin": "span"},
            {
                "claim_id": "c_child", "text": "child",
                "origin": "atomic_rewrite", "parent_claim_id": "c_parent",
            },
            {"claim_id": "c_bogus", "text": "x", "origin": "not_in_the_vocabulary"},
        ]
        by_id = {
            i["agent_id"]: i["values"]
            for i in import_rows.claim_rows(DOC_A, claims, source=SOURCE)
        }
        assert by_id["c_child"]["origin"] == "atomic_rewrite"
        assert by_id["c_parent"]["origin"] == "span"
        # 語彙外は名乗らせない（= 書かない）。
        assert "origin" not in by_id["c_bogus"]

    def test_parent_links_are_read_from_the_bundle_id_space(self):
        """V-11: 親は束の claim_id 空間。束に無い親・自己参照は結ばない。"""
        claims = [
            {"claim_id": "c_parent", "text": "p"},
            {"claim_id": "c_child", "text": "c", "parent_claim_id": "c_parent"},
            {"claim_id": "c_self", "text": "s", "parent_claim_id": "c_self"},
            {"claim_id": "c_orphan", "text": "o", "parent_claim_id": "not-in-the-bundle"},
        ]
        assert import_rows.claim_parent_links(claims) == [("c_child", "c_parent")]


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

    def test_live_rows_with_replace_say_what_drops_out_and_what_stays(self):
        """P4-R2: 「確定した状態は保たれます」とだけ言わない。

        束に無い既存の項目は表示対象から外れる（= 事実として先に言う）。そのうえで
        教員が確定した項目は外さない、と分けて言う。
        """
        bundle = parse_bundle(_bundle_zip())
        facts = import_apply.plan_facts(bundle, has_live=True, replace=True)
        joined = "".join(facts)
        assert "束に無い既存の項目" in joined
        assert "表示対象から外れます" in joined
        assert "教員が確定した項目（承認・却下・要修正）は、束に無くても表示対象から外しません。" in facts
        # 旧文言（何が外れるかを言わずに「保たれます」とだけ言う）は復活させない。
        assert "教員が確定した状態は保たれます" not in joined

    def test_missing_sections_are_reported_as_facts(self):
        bundle = parse_bundle(_bundle_zip(drop=("evidence/evidence_snippets.json",)))
        joined = "".join(import_apply.plan_facts(bundle, has_live=False, replace=False))
        assert "根拠" in joined

    def test_facts_carry_no_numbers(self):
        bundle = parse_bundle(_bundle_zip())
        for fact in import_apply.plan_facts(bundle, has_live=True, replace=True):
            assert not any(ch.isdigit() for ch in fact), fact


class TestWriterAndImporterAgreeOnKeys:
    """V-2 / V-5: 書き手（パイプライン）と取り込みが**同じ**キーを出す。

    ここがずれると、同じ内容の step が再解析と取り込みで別行になり（あるいは
    live の部分一意索引に当たって取り込みが落ち）、同一性が壊れる。
    """

    CHAINS = [
        {
            "derivation_id": "deriv_a",
            "chain_type": "equation_chain",
            "steps": [
                {"step_id": "step_001", "operation": "linearize_field",
                 "input_equation_ids": ["eq_1"], "output_equation_ids": ["eq_2"]},
                {"step_id": "step_002", "operation": "solve_for_amplitude",
                 "input_equation_ids": ["eq_2"], "output_equation_ids": []},
            ],
        },
        {
            "derivation_id": "deriv_b",
            "chain_type": "equation_chain",
            # 別チェーンの同名 step（V-2 の衝突源）。
            "steps": [
                {"step_id": "step_001", "operation": "linearize_field",
                 "input_equation_ids": ["eq_1"], "output_equation_ids": ["eq_2"]},
            ],
        },
    ]

    def _writer_items(self):
        from types import SimpleNamespace

        from core.document_pipeline import persistence

        return persistence._derivation_items(
            DOC_A, SimpleNamespace(chains=self.CHAINS), {}
        )

    def _import_items(self):
        return import_rows.derivation_step_rows(DOC_A, self.CHAINS, source=SOURCE)

    def test_agent_ids_and_stable_keys_match_the_writer(self):
        writer = [(i["agent_id"], i["stable_key"]) for i in self._writer_items()]
        imported = [(i["agent_id"], i["stable_key"]) for i in self._import_items()]
        assert imported == writer

    def test_same_named_steps_in_different_chains_stay_distinct(self):
        items = import_rows.dedupe(self._import_items())
        assert len({i["agent_id"] for i in items}) == 3
        assert len({i["stable_key"] for i in items}) == 3
        # ``#n`` サフィックスで無理やり分けているのではなく、素キーが別（V-5）。
        assert not any(i["stable_key"].endswith("#2") for i in items)
