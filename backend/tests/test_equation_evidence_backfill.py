"""式 ⇄ evidence の結線欠陥（missing_evidence_link）を塞ぐバックフィルの回帰テスト。

背景: ``EquationSemantics.source_evidence_ids`` は LLM 出力フィールドだが
``equation_semantics`` の prompt / validator はこの項目を要求しないため、実際には
**常に空**で agent を出る。式ブロックの逐語 evidence（``equation_quote``。
``_build_evidence_registry`` が式ブロックごとに登録する）との結線は
``to_equations_export(evidence_index=...)`` の中にしか無く、**export ルートにしか
流れていなかった**。その結果 derivation step（式レコードの evidence をそのまま持つ）と
理論操作グラフの全ノードが ``linked_evidence_ids`` 空 =
``missing_evidence_link`` で出ていた（evidence は登録済みで、結線だけが欠けている
＝処理の欠陥）。

本テストは
``_hook_equation_evidence_backfill`` / ``_backfill_equation_evidence_refs`` /
``_backfill_derivation_evidence_refs`` の契約（export と同一の block 索引・additive・
冪等・evidence の無い式は空のまま＝捏造しない・非致命）と、
バックフィル後にグラフノードの ``missing_evidence_link`` が消えること、
``missing_atomic_claim`` は #306 の設計どおり残ることを固定する。
DB・LLM・FastAPI には触らない。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_SRC_DIR = BACKEND.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.document_pipeline import orchestrator as orch  # noqa: E402
from episteme_graph.agents.claim_object_builder.schema import (  # noqa: E402
    ClaimObjectBuildResult,
)
from episteme_graph.agents.component_assembly.schema import (  # noqa: E402
    ComponentAssemblyResult,
)
from episteme_graph.agents.component_graph.agent import _build_claim_index  # noqa: E402
from episteme_graph.agents.component_graph.normalizer import (  # noqa: E402
    ComponentGraphNormalizer,
)
from episteme_graph.agents.component_graph.schema import (  # noqa: E402
    GRAPH_SCHEMA_VERSION,
    ComponentGraphResult,
)
from episteme_graph.agents.derivation_chain.schema import (  # noqa: E402
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.equation_semantics.schema import (  # noqa: E402
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.evidence_registry.schema import (  # noqa: E402
    EvidenceRecord,
    EvidenceRegistryResult,
    EvidenceSource,
)


# --------------------------------------------------------------------------- #
# fixtures（本物の Result dataclass を使う）
# --------------------------------------------------------------------------- #

def _equation(eq_id: str, *, block_id: str, evidence=()) -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="x",
        latex="x=y",
        plain_text="x=y",
        source_location={"page": 1, "section_id": "doc:sec", "block_id": block_id, "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="definition",
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.85,
        reason="",
        defined_symbols=[DefinedSymbol(symbol="S3", definition_status="defined")],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence),
        linked_claim_ids=[],
        summary="",
        review_flags=[],
    )
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc",
        label=eq_id,
        candidate_trace_ids=[f"eqcand_{eq_id}"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=EquationConfidencePolicy.derive(src, rec, sem),
    )


def _equations(records) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc",
        cartridge_id=None,
        equation_candidates=[],
        equations=list(records),
        validation_issues=[],
    )


def _evidence(*, evidence_id: str, block_id: str, text: str = "the moment vanishes") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        document_id="doc",
        source=EvidenceSource(page=4, section_id="sec_1", block_id=block_id),
        evidence_text=text,
    )


def _registry(records) -> EvidenceRegistryResult:
    return EvidenceRegistryResult(
        document_id="doc", cartridge_id=None, records=list(records), validation_issues=[],
    )


def _step(step_id: str, *, outputs=("eq_1",), inputs=("eq_in",), evidence=()) -> DerivationStep:
    return DerivationStep(
        step_id=step_id,
        input_equation_ids=list(inputs),
        operation="eliminate_second_order_parameter",
        output_equation_ids=list(outputs),
        source_evidence_ids=list(evidence),
        review_status="teacher_review_required",
    )


def _derivations(steps) -> DerivationChainResult:
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv", document_id="doc", source_section_ids=[], steps=list(steps),
            )
        ],
        validation_issues=[],
    )


def _ctx(*, equations, evidence, derivations=None, claims=()):
    saved: list[tuple[str, object]] = []
    return SimpleNamespace(
        document_id="doc",
        material_id="mat",
        equations=equations,
        evidence=evidence,
        derivations=derivations,
        claim_objects=ClaimObjectBuildResult(
            document_id="doc", cartridge_id=None, claims=list(claims)
        ),
        saved=saved,
        save_artifact=lambda stage, value: saved.append((stage, value)),
    )


def _saved_stages(ctx) -> list[str]:
    return [stage for stage, _ in ctx.saved]


def _sem_evidence(equations, index: int = 0) -> list[str]:
    return list(equations.equations[index].semantics.source_evidence_ids)


# --------------------------------------------------------------------------- #
# ① block 索引（export と同じ規則）
# --------------------------------------------------------------------------- #

def test_evidence_index_groups_by_block_and_dedupes():
    registry = _registry([
        _evidence(evidence_id="ev_1", block_id="blk_1"),
        _evidence(evidence_id="ev_2", block_id="blk_1"),
        _evidence(evidence_id="ev_1", block_id="blk_1"),  # 重複 ID
        _evidence(evidence_id="ev_3", block_id="blk_2"),
    ])
    assert orch._evidence_ids_by_block(registry) == {
        "blk_1": ["ev_1", "ev_2"],
        "blk_2": ["ev_3"],
    }
    assert orch._evidence_ids_by_block(None) == {}


def test_equation_block_id_reads_source_location():
    equation = _equation("eq_1", block_id="blk_eq_1")
    assert orch._equation_block_id(equation) == "blk_eq_1"
    assert orch._equation_block_id(SimpleNamespace()) == ""


# --------------------------------------------------------------------------- #
# ② 式レコードへのバックフィル
# --------------------------------------------------------------------------- #

def test_equation_evidence_is_linked_from_its_own_block():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1")])
    registry = _registry([_evidence(evidence_id="ev_1", block_id="blk_eq_1")])
    assert _sem_evidence(equations) == []

    report = orch._backfill_equation_evidence_refs(equations=equations, evidence=registry)

    assert _sem_evidence(equations) == ["ev_1"]
    assert report == {"equations_changed": 1, "refs_added": 1}


def test_equation_without_matching_evidence_block_stays_empty():
    # 別ブロックの evidence を式に付けない（捏造しない）。
    equations = _equations([_equation("eq_1", block_id="blk_eq_1")])
    registry = _registry([_evidence(evidence_id="ev_1", block_id="blk_other")])

    report = orch._backfill_equation_evidence_refs(equations=equations, evidence=registry)

    assert _sem_evidence(equations) == []
    assert report == {"equations_changed": 0, "refs_added": 0}


def test_existing_equation_evidence_refs_are_kept():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1", evidence=["ev_manual"])])
    registry = _registry([_evidence(evidence_id="ev_1", block_id="blk_eq_1")])

    orch._backfill_equation_evidence_refs(equations=equations, evidence=registry)

    assert _sem_evidence(equations) == ["ev_1", "ev_manual"]


def test_equation_backfill_is_idempotent():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1")])
    registry = _registry([_evidence(evidence_id="ev_1", block_id="blk_eq_1")])

    orch._backfill_equation_evidence_refs(equations=equations, evidence=registry)
    second = orch._backfill_equation_evidence_refs(equations=equations, evidence=registry)

    assert second == {"equations_changed": 0, "refs_added": 0}


def test_equation_backfill_is_a_no_op_without_inputs():
    empty = {"equations_changed": 0, "refs_added": 0}
    assert orch._backfill_equation_evidence_refs(equations=None, evidence=None) == empty
    assert orch._backfill_equation_evidence_refs(
        equations=_equations([]), evidence=_registry([]),
    ) == empty
    assert orch._backfill_equation_evidence_refs(
        equations=_equations([_equation("eq_1", block_id="blk_eq_1")]), evidence=_registry([]),
    ) == empty


# --------------------------------------------------------------------------- #
# ③ フック（artifact 保存・非致命）
# --------------------------------------------------------------------------- #

def test_hook_saves_the_equation_artifact_only_when_something_changed():
    ctx = _ctx(
        equations=_equations([_equation("eq_1", block_id="blk_eq_1")]),
        evidence=_registry([_evidence(evidence_id="ev_1", block_id="blk_eq_1")]),
    )
    orch._hook_equation_evidence_backfill(ctx)
    assert _sem_evidence(ctx.equations) == ["ev_1"]
    assert "equation_semantics" in _saved_stages(ctx)

    ctx.saved.clear()
    orch._hook_equation_evidence_backfill(ctx)
    assert "equation_semantics" not in _saved_stages(ctx)


def test_hook_is_non_fatal_when_the_registry_is_missing():
    ctx = _ctx(equations=_equations([_equation("eq_1", block_id="blk_eq_1")]), evidence=None)
    orch._hook_equation_evidence_backfill(ctx)  # 例外にならない
    assert ctx.saved == []


def test_hook_is_registered_between_evidence_registry_and_claim_object_builder():
    names = [
        step.name if step.name else getattr(step.execute, "__name__", "")
        for step in orch._PIPELINE_STEPS
    ]
    hook = "_hook_equation_evidence_backfill"
    assert hook in names
    assert names.index("evidence_registry") < names.index(hook) < names.index("claim_object_builder")
    # フックはモデルを呼ばない（M層対象外）。
    step = next(s for s in orch._PIPELINE_STEPS if s.name is None and s.execute.__name__ == hook)
    assert step.llm_kind == orch.LLM_KIND_NONE and step.model_policy is False


# --------------------------------------------------------------------------- #
# ④ derivation step へのバックフィル（resume 相当の防御）
# --------------------------------------------------------------------------- #

def test_step_evidence_is_backfilled_from_its_equations():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1", evidence=["ev_1"])])
    derivations = _derivations([_step("step_001", outputs=["eq_1"], inputs=[])])

    report = orch._backfill_derivation_evidence_refs(derivations=derivations, equations=equations)

    assert derivations.chains[0].steps[0].source_evidence_ids == ["ev_1"]
    assert report == {"steps_changed": 1, "refs_added": 1}


def test_step_evidence_also_uses_input_equations():
    # system_level の step は output_equation_ids が空になり得る。
    equations = _equations([_equation("eq_in", block_id="blk_in", evidence=["ev_in"])])
    derivations = _derivations([_step("step_sys", outputs=[], inputs=["eq_in"])])

    orch._backfill_derivation_evidence_refs(derivations=derivations, equations=equations)

    assert derivations.chains[0].steps[0].source_evidence_ids == ["ev_in"]


def test_step_without_equation_evidence_stays_empty():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1")])
    derivations = _derivations([_step("step_001", outputs=["eq_1"], inputs=[])])

    report = orch._backfill_derivation_evidence_refs(derivations=derivations, equations=equations)

    assert derivations.chains[0].steps[0].source_evidence_ids == []
    assert report == {"steps_changed": 0, "refs_added": 0}


def test_step_evidence_backfill_is_idempotent_and_keeps_existing_refs():
    equations = _equations([_equation("eq_1", block_id="blk_eq_1", evidence=["ev_1"])])
    derivations = _derivations([
        _step("step_001", outputs=["eq_1"], inputs=[], evidence=["ev_agent"]),
    ])

    orch._backfill_derivation_evidence_refs(derivations=derivations, equations=equations)
    assert derivations.chains[0].steps[0].source_evidence_ids == ["ev_1", "ev_agent"]

    second = orch._backfill_derivation_evidence_refs(derivations=derivations, equations=equations)
    assert second == {"steps_changed": 0, "refs_added": 0}


def test_step_evidence_backfill_is_a_no_op_without_inputs():
    empty = {"steps_changed": 0, "refs_added": 0}
    assert orch._backfill_derivation_evidence_refs(derivations=None, equations=None) == empty
    assert orch._backfill_derivation_evidence_refs(
        derivations=orch._empty_derivation_chain_result("doc", None), equations=_equations([]),
    ) == empty


# --------------------------------------------------------------------------- #
# ⑤ エンドツーエンド配線（バックフィル → 理論操作グラフ）
# --------------------------------------------------------------------------- #

def _normalize(ctx):
    empty_graph = ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )
    empty_components = ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )
    return ComponentGraphNormalizer().normalize(
        empty_graph,
        empty_components,
        ctx.derivations,
        claim_index=_build_claim_index(orch._component_graph_claims(ctx)),
    )


def _pipeline_ctx():
    """式ブロックの evidence は登録済み・結線だけが無い状態（バグの再現条件）。"""
    return _ctx(
        equations=_equations([_equation("eq_1", block_id="blk_eq_1")]),
        evidence=_registry([_evidence(evidence_id="ev_1", block_id="blk_eq_1")]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"], inputs=[])]),
    )


def test_graph_nodes_report_missing_evidence_without_the_backfill():
    # バグ再現ガード: 結線が無いと全ノードが missing_evidence_link で出る。
    result = _normalize(_pipeline_ctx())
    assert result.nodes, "ノードが1つも出ないとテストの前提が崩れる"
    for node in result.nodes:
        assert node.linked_evidence_ids == []
        assert "missing_evidence_link" in node.review_reasons


def test_backfill_links_evidence_and_clears_the_missing_evidence_reason():
    ctx = _pipeline_ctx()
    orch._hook_equation_evidence_backfill(ctx)
    orch._backfill_derivation_evidence_refs(derivations=ctx.derivations, equations=ctx.equations)

    result = _normalize(ctx)
    assert result.nodes
    for node in result.nodes:
        assert node.linked_evidence_ids == ["ev_1"]
        assert "missing_evidence_link" not in node.review_reasons
        # 式 + evidence が揃うので #306 の source_backed になる。
        assert node.source_backing_status == "source_backed"
        assert node.review_status == "source_backed"


def test_atomic_claim_gap_is_still_reported_after_the_backfill():
    """documented limitation (#306): evidence で source_backed になっても、最小命題の
    claim が無いことは warning として残す（空の evidence_text を強い backing に
    しないための設計）。"""
    ctx = _pipeline_ctx()
    orch._hook_equation_evidence_backfill(ctx)
    orch._backfill_derivation_evidence_refs(derivations=ctx.derivations, equations=ctx.equations)

    for node in _normalize(ctx).nodes:
        assert node.review_reasons == ["missing_atomic_claim"]
