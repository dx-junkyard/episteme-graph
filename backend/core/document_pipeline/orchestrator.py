"""8 agent pipeline orchestrator (issue #226, #266).

PDF → DocumentStructure → SourceChunking → SourceEmbedding → PaperSkeleton →
RhetoricalRole → ClaimQualification → EquationSemantics → ThesisReconstruction →
DSLLinking → DSLEmbedding → ComponentAssembly → ComponentGraph →
CoursMapping → Blueprint → ExportValidation → Persist → Completed
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

from .chunker import build_source_chunks
from .dsl_text import dsl_result_to_search_text
from .persistence import (
    get_latest_analysis_run,
    load_source_chunk_index,
    persist_component_graph,
    persist_components,
    persist_document_embedding,
    persist_qualified_claims,
    persist_source_chunks,
    upsert_analysis_run,
)

logger = logging.getLogger(__name__)

ARTIFACTS_KEY = "_artifacts"


PIPELINE_STAGES = [
    "save_pdf",
    "document_structure",
    "source_chunking",
    "source_embedding",
    "paper_skeleton",
    "rhetorical_role",
    "claim_qualification",
    "equation_semantics",
    "evidence_registry",
    "claim_object_builder",
    "derivation_chain",
    "figure_table_semantics",
    "thesis_reconstruction",
    "dsl_linking",
    "dsl_embedding",
    "component_assembly",
    "component_graph",
    "course_mapping",
    "blueprint",
    "export_validation",
    "persist_claims_components_graph",
    "completed",
]


class PipelineStageError(RuntimeError):
    """pipeline stage 失敗時に raise する。`stage` 属性で停止段階を伝える。"""

    def __init__(self, stage: str, message: str, *, cause: BaseException | None = None):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.__cause__ = cause


@dataclass
class DocumentPipelineResult:
    document_id: str
    material_id: str
    cartridge_id: str | None
    run_id: str | None
    chunk_count: int = 0
    claim_count: int = 0
    component_count: int = 0
    dsl_node_count: int = 0
    dsl_edge_count: int = 0
    final_stage: str = "completed"
    stage_outputs: dict[str, Any] = field(default_factory=dict)


def _import_agents() -> dict:
    """Lazy import. PYTHONPATH に src/ が入っていることを前提とする。"""
    from episteme_graph.agents.blueprint.agent import BlueprintAgent
    from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
    from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
    from episteme_graph.agents.component_assembly.agent import ComponentAssemblyAgent
    from episteme_graph.agents.component_graph.agent import ComponentGraphAgent
    from episteme_graph.agents.course_mapping.agent import CourseMappingAgent
    from episteme_graph.agents.derivation_chain.agent import DerivationChainAgent
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.dsl_linking.agent import DSLLinkingAgent
    from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent
    from episteme_graph.agents.evidence_registry.builder import EvidenceRegistryBuilder
    from episteme_graph.agents.figure_table_semantics.agent import FigureTableSemanticsAgent
    from episteme_graph.agents.paper_skeleton.agent import PaperSkeletonAgent
    from episteme_graph.agents.rhetorical_role.agent import RhetoricalRoleAgent
    from episteme_graph.agents.thesis_reconstruction.agent import ThesisReconstructionAgent

    return {
        "DocumentStructureAgent": DocumentStructureAgent,
        "PaperSkeletonAgent": PaperSkeletonAgent,
        "RhetoricalRoleAgent": RhetoricalRoleAgent,
        "ClaimQualificationAgent": ClaimQualificationAgent,
        "EquationSemanticsAgent": EquationSemanticsAgent,
        "ThesisReconstructionAgent": ThesisReconstructionAgent,
        "DSLLinkingAgent": DSLLinkingAgent,
        "ComponentAssemblyAgent": ComponentAssemblyAgent,
        "ComponentGraphAgent": ComponentGraphAgent,
        "EvidenceRegistryBuilder": EvidenceRegistryBuilder,
        "ClaimObjectBuilder": ClaimObjectBuilder,
        "DerivationChainAgent": DerivationChainAgent,
        "FigureTableSemanticsAgent": FigureTableSemanticsAgent,
        "CourseMappingAgent": CourseMappingAgent,
        "BlueprintAgent": BlueprintAgent,
    }


def run_document_pipeline(
    *,
    pdf_bytes: bytes,
    document_id: str,
    material_id: str,
    filename: str | None = None,
    cartridge_id: str | None = None,
    course_id: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    agents: dict | None = None,
    resume: bool = True,
) -> DocumentPipelineResult:
    """新 pipeline 本体。同期実行。

    Args:
        pdf_bytes: PDF バイナリ。一時ファイルに書き出して DocumentStructureAgent
            に渡す。
        document_id: documents.id。後続で chunks/claims 等の document_id に使う。
        material_id: 教材 ID（chunks.material_id）。
        filename: 元ファイル名（任意・ログ用）。
        cartridge_id: 使用カートリッジ。指定なしなら EPISTEME_DEFAULT_CARTRIDGE_ID
            から決定。
        course_id: 任意。指定された場合のみ component graph を course にも紐づける。
        progress_callback: 各 stage 完了時に (stage_name, info_dict) で呼ばれる。
            background_tasks への進捗反映に使う。
        agents: テスト用に注入可能な agent インスタンス dict。

    Raises:
        PipelineStageError: 任意 stage で復旧不能な失敗が起きた場合。
    """
    if cartridge_id is None:
        cartridge_id = os.getenv("EPISTEME_DEFAULT_CARTRIDGE_ID") or None

    previous_run = (
        get_latest_analysis_run(document_id=document_id, material_id=material_id)
        if resume and agents is None else None
    )
    if previous_run and previous_run.get("status") == "completed":
        previous_run = None
    previous_outputs = dict((previous_run or {}).get("stage_outputs") or {})
    previous_artifacts = dict(previous_outputs.get(ARTIFACTS_KEY) or {})
    run_id = (previous_run or {}).get("id")
    if run_id:
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage=(previous_run or {}).get("current_stage") or "save_pdf",
            stage_outputs={"resume": {"resumed": True}},
        )
    else:
        run_id = upsert_analysis_run(
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage="save_pdf",
        )

    def report(stage: str, payload: dict | None = None, *, run_status: str = "running") -> None:
        payload = payload or {}
        if progress_callback:
            try:
                progress_callback(stage, payload)
            except Exception:
                logger.warning("progress_callback raised", exc_info=True)
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status=run_status,
            current_stage=stage,
            stage_outputs={stage: payload},
        )

    def report_start(stage: str, *, total: int | None = None, unit: str | None = None) -> None:
        payload: dict[str, Any] = {"status": "running", "processed": 0}
        if total is not None:
            payload["total"] = total
            payload["progress"] = 0
        if unit:
            payload["unit"] = unit
        report(stage, payload)

    def report_item(stage: str, processed: int, total: int, unit: str | None = None) -> None:
        payload: dict[str, Any] = {
            "status": "running",
            "processed": processed,
            "total": total,
            "progress": int(processed / total * 100) if total else 100,
        }
        if unit:
            payload["unit"] = unit
        report(stage, payload)

    def report_done(stage: str, payload: dict | None = None, *, run_status: str = "running") -> None:
        done_payload = {"status": "completed", "progress": 100}
        done_payload.update(payload or {})
        report(stage, done_payload, run_status=run_status)

    def save_artifact(stage: str, value: Any) -> None:
        previous_artifacts[stage] = _to_plain_data(value)
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage=stage,
            stage_outputs={ARTIFACTS_KEY: previous_artifacts},
        )

    def artifact(stage: str) -> Any | None:
        return previous_artifacts.get(stage)

    result = DocumentPipelineResult(
        document_id=document_id,
        material_id=material_id,
        cartridge_id=cartridge_id,
        run_id=run_id,
    )

    if agents is None:
        agent_classes = _import_agents()
    else:
        # Tests may override only a subset; fall back to default imports for the rest.
        try:
            default_agents = _import_agents()
        except Exception:
            default_agents = {}
        default_agents.update(agents)
        agent_classes = default_agents

    pdf_path: str | None = None
    try:
        # ── Stage 1: save_pdf (一時ファイル化。MinIO への保存は呼び出し側担当) ─
        report_done("save_pdf", {"size_bytes": len(pdf_bytes)})
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pdf", delete=False
        ) as f:
            f.write(pdf_bytes)
            pdf_path = f.name

        # ── Stage 2: document_structure ────────────────────────────────────
        structure_artifact = artifact("document_structure")
        if structure_artifact:
            structure = _from_agent_dict("document_structure", structure_artifact)
            logger.info("Resuming document pipeline: loaded document_structure artifact for document %s", document_id)
        else:
            report_start("document_structure", total=1, unit="document")
            try:
                ds_agent = agent_classes["DocumentStructureAgent"]() if isinstance(
                    agent_classes["DocumentStructureAgent"], type
                ) else agent_classes["DocumentStructureAgent"]
                structure = ds_agent.run(pdf_path=pdf_path, cartridge_id=cartridge_id)
                structure.document_id = document_id  # 強制的に上書きして後段一貫
            except Exception as exc:
                raise PipelineStageError("document_structure", str(exc), cause=exc) from exc
            save_artifact("document_structure", structure)
        report_done("document_structure", {
            "block_count": len(structure.blocks),
            "section_count": len(structure.sections),
        })

        # ── Stage 3: source_chunking ───────────────────────────────────────
        source_chunks_artifact = artifact("source_chunking")
        if source_chunks_artifact:
            source_chunks = _from_source_chunks(source_chunks_artifact)
            logger.info("Resuming document pipeline: loaded %d source chunks for document %s", len(source_chunks), document_id)
        else:
            report_start("source_chunking", total=len(structure.blocks), unit="blocks")
            try:
                source_chunks = build_source_chunks(structure)
            except Exception as exc:
                raise PipelineStageError("source_chunking", str(exc), cause=exc) from exc
            save_artifact("source_chunking", source_chunks)
        if not source_chunks:
            raise PipelineStageError(
                "source_chunking",
                "no source chunks produced from document structure",
            )
        report_done("source_chunking", {"chunk_count": len(source_chunks)})

        # ── Stage 4: source_embedding ──────────────────────────────────────
        if artifact("source_embedding"):
            chunk_index = load_source_chunk_index(document_id=document_id)
            logger.info("Resuming document pipeline: loaded %d persisted chunks for document %s", len(chunk_index), document_id)
        else:
            report_start("source_embedding", total=len(source_chunks), unit="chunks")
            try:
                chunk_index = persist_source_chunks(
                    document_id=document_id,
                    material_id=material_id,
                    chunks=source_chunks,
                )
            except Exception as exc:
                raise PipelineStageError("source_embedding", str(exc), cause=exc) from exc
            save_artifact("source_embedding", {"saved_chunks": len(chunk_index)})
        result.chunk_count = len(chunk_index)
        report_done("source_embedding", {"saved_chunks": len(chunk_index), "total": len(source_chunks), "processed": len(source_chunks)})

        # ── Stage 5: paper_skeleton ────────────────────────────────────────
        skeleton_artifact = artifact("paper_skeleton")
        if skeleton_artifact:
            skeleton = _from_agent_dict("paper_skeleton", skeleton_artifact)
            logger.info("Resuming document pipeline: loaded paper_skeleton artifact for document %s", document_id)
        else:
            report_start("paper_skeleton", total=1, unit="llm_call")
            try:
                ps_agent = _instantiate(agent_classes["PaperSkeletonAgent"])
                skeleton = ps_agent.run(structure=structure, cartridge_id=cartridge_id)
            except Exception as exc:
                logger.exception("paper_skeleton stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("paper_skeleton", str(exc), cause=exc) from exc
            save_artifact("paper_skeleton", skeleton)
        report_done("paper_skeleton", {"document_id": document_id, "total": 1, "processed": 1})

        # ── Stage 6: rhetorical_role ───────────────────────────────────────
        roles_artifact = artifact("rhetorical_role")
        if roles_artifact:
            roles = _from_agent_dict("rhetorical_role", roles_artifact)
            logger.info("Resuming document pipeline: loaded rhetorical_role artifact for document %s", document_id)
        else:
            report_start("rhetorical_role", total=_agent_input_count("rhetorical_role", agent_classes, structure, skeleton, cartridge_id), unit="blocks")
            try:
                rr_agent = _instantiate(agent_classes["RhetoricalRoleAgent"])
                roles = rr_agent.run(
                    structure=structure,
                    skeleton=skeleton,
                    cartridge_id=cartridge_id,
                    progress_callback=lambda processed, total: report_item("rhetorical_role", processed, total, "blocks"),
                )
            except Exception as exc:
                logger.exception("rhetorical_role stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("rhetorical_role", str(exc), cause=exc) from exc
            save_artifact("rhetorical_role", roles)
        report_done("rhetorical_role", getattr(roles, "summary_stats", {}) or {})

        # ── Stage 7: claim_qualification ───────────────────────────────────
        qualified_artifact = artifact("claim_qualification")
        if qualified_artifact:
            qualified = _from_agent_dict("claim_qualification", qualified_artifact)
            logger.info("Resuming document pipeline: loaded claim_qualification artifact for document %s", document_id)
        else:
            report_start("claim_qualification", total=_agent_input_count("claim_qualification", agent_classes, structure, skeleton, cartridge_id, roles=roles), unit="spans")
            try:
                cq_agent = _instantiate(agent_classes["ClaimQualificationAgent"])
                qualified = cq_agent.run(
                    structure=structure, skeleton=skeleton, roles=roles,
                    cartridge_id=cartridge_id,
                    progress_callback=lambda processed, total: report_item("claim_qualification", processed, total, "spans"),
                )
            except Exception as exc:
                logger.exception("claim_qualification stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("claim_qualification", str(exc), cause=exc) from exc
            save_artifact("claim_qualification", qualified)
        report_done("claim_qualification", {
            "qualified_count": len(qualified.qualified_spans),
        })

        # ── Stage 8: equation_semantics ────────────────────────────────────
        equations_artifact = artifact("equation_semantics")
        if equations_artifact:
            equations = _from_agent_dict("equation_semantics", equations_artifact)
            logger.info("Resuming document pipeline: loaded equation_semantics artifact for document %s", document_id)
        else:
            report_start("equation_semantics", total=_agent_input_count("equation_semantics", agent_classes, structure, skeleton, cartridge_id, roles=roles), unit="equations")
            try:
                eq_agent = _instantiate(agent_classes["EquationSemanticsAgent"])
                equations = eq_agent.run(
                    structure=structure, skeleton=skeleton, roles=roles,
                    cartridge_id=cartridge_id,
                    progress_callback=lambda processed, total: report_item("equation_semantics", processed, total, "equations"),
                )
            except Exception as exc:
                logger.exception("equation_semantics stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("equation_semantics", str(exc), cause=exc) from exc
            save_artifact("equation_semantics", equations)
        report_done("equation_semantics", {"equations": len(getattr(equations, "equations", []) or [])})

        # ── Stage 8b: evidence_registry (deterministic, source-backed) ─────
        evidence_artifact = artifact("evidence_registry")
        if evidence_artifact:
            evidence = _from_agent_dict("evidence_registry", evidence_artifact)
            logger.info("Resuming document pipeline: loaded evidence_registry artifact for document %s", document_id)
        else:
            report_start("evidence_registry", total=1, unit="builder")
            try:
                evidence = _build_evidence_registry(
                    agent_classes=agent_classes,
                    document_id=document_id,
                    cartridge_id=cartridge_id,
                    structure=structure,
                    qualified=qualified,
                    equations=equations,
                )
            except Exception as exc:
                logger.exception(
                    "evidence_registry stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                evidence = _empty_evidence_registry(document_id, cartridge_id)
            save_artifact("evidence_registry", evidence)
        report_done("evidence_registry", {
            "records": len(getattr(evidence, "records", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 8c: claim_object_builder (deterministic claims.json) ─────
        claim_object_artifact = artifact("claim_object_builder")
        if claim_object_artifact:
            claim_objects = _from_agent_dict("claim_object_builder", claim_object_artifact)
            logger.info("Resuming document pipeline: loaded claim_object_builder artifact for document %s", document_id)
        else:
            report_start("claim_object_builder", total=1, unit="builder")
            try:
                claim_objects = _build_claim_objects(
                    agent_classes=agent_classes,
                    document_id=document_id,
                    cartridge_id=cartridge_id,
                    qualified=qualified,
                    equations=equations,
                    evidence=evidence,
                )
            except Exception as exc:
                logger.exception(
                    "claim_object_builder stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                claim_objects = _empty_claim_object_result(document_id, cartridge_id)
            save_artifact("claim_object_builder", claim_objects)
        report_done("claim_object_builder", {
            "claims": len(getattr(claim_objects, "claims", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 8d: derivation_chain (deterministic from equation links) ─
        derivation_artifact = artifact("derivation_chain")
        if derivation_artifact:
            derivations = _from_agent_dict("derivation_chain", derivation_artifact)
            logger.info("Resuming document pipeline: loaded derivation_chain artifact for document %s", document_id)
        else:
            report_start("derivation_chain", total=1, unit="builder")
            try:
                derivations = _build_derivation_chains(
                    agent_classes=agent_classes,
                    cartridge_id=cartridge_id,
                    equations=equations,
                    claim_objects=claim_objects,
                    evidence=evidence,
                )
            except Exception as exc:
                logger.exception(
                    "derivation_chain stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                derivations = _empty_derivation_chain_result(document_id, cartridge_id)
            save_artifact("derivation_chain", derivations)
        report_done("derivation_chain", {
            "chains": len(getattr(derivations, "chains", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 8e: figure_table_semantics (caption-first deterministic) ─
        fig_tbl_artifact = artifact("figure_table_semantics")
        if fig_tbl_artifact:
            fig_tbl = _from_agent_dict("figure_table_semantics", fig_tbl_artifact)
            logger.info("Resuming document pipeline: loaded figure_table_semantics artifact for document %s", document_id)
        else:
            report_start("figure_table_semantics", total=1, unit="builder")
            try:
                fig_tbl = _build_figure_table_semantics(
                    agent_classes=agent_classes,
                    cartridge_id=cartridge_id,
                    structure=structure,
                    evidence=evidence,
                    claim_objects=claim_objects,
                )
            except Exception as exc:
                logger.exception(
                    "figure_table_semantics stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                fig_tbl = _empty_figure_table_result(document_id, cartridge_id)
            save_artifact("figure_table_semantics", fig_tbl)
        report_done("figure_table_semantics", {
            "figures": len(getattr(fig_tbl, "figures", []) or []),
            "tables": len(getattr(fig_tbl, "tables", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 9: thesis_reconstruction ─────────────────────────────────
        thesis_artifact = artifact("thesis_reconstruction")
        if thesis_artifact:
            thesis = _from_agent_dict("thesis_reconstruction", thesis_artifact)
            logger.info("Resuming document pipeline: loaded thesis_reconstruction artifact for document %s", document_id)
        else:
            report_start("thesis_reconstruction", total=1, unit="llm_call")
            try:
                th_agent = _instantiate(agent_classes["ThesisReconstructionAgent"])
                thesis = th_agent.run(
                    skeleton=skeleton, qualified_claims=qualified, equations=equations,
                    cartridge_id=cartridge_id,
                )
            except Exception as exc:
                logger.exception("thesis_reconstruction stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("thesis_reconstruction", str(exc), cause=exc) from exc
            save_artifact("thesis_reconstruction", thesis)
        report_done("thesis_reconstruction", {"total": 1, "processed": 1})

        # ── Stage 10: dsl_linking ──────────────────────────────────────────
        dsl_artifact = artifact("dsl_linking")
        if dsl_artifact:
            dsl = _from_agent_dict("dsl_linking", dsl_artifact)
            logger.info("Resuming document pipeline: loaded dsl_linking artifact for document %s", document_id)
        else:
            report_start("dsl_linking", total=1, unit="llm_call")
            try:
                dsl_agent = _instantiate(agent_classes["DSLLinkingAgent"])
                dsl = dsl_agent.run(
                    qualified_claims=qualified, equations=equations, thesis=thesis,
                )
            except Exception as exc:
                logger.exception("dsl_linking stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("dsl_linking", str(exc), cause=exc) from exc
            save_artifact("dsl_linking", dsl)
        result.dsl_node_count = len(dsl.nodes)
        result.dsl_edge_count = len(dsl.edges)
        report_done("dsl_linking", {
            "nodes": len(dsl.nodes), "edges": len(dsl.edges), "total": 1, "processed": 1,
        })

        # ── Stage 11: dsl_embedding ────────────────────────────────────────
        report_start("dsl_embedding", total=1, unit="embedding")
        if not artifact("dsl_embedding"):
            try:
                dsl_text = dsl_result_to_search_text(dsl, document_id=document_id)
                persist_document_embedding(
                    document_id=document_id,
                    material_id=material_id,
                    embedding_type="dsl_graph",
                    text=dsl_text,
                    metadata={
                        "node_count": len(dsl.nodes),
                        "edge_count": len(dsl.edges),
                    },
                )
                save_artifact("dsl_embedding", {"saved": True})
            except Exception as exc:
                # embedding は best-effort（agent pipeline 全体の致命傷にはしない）
                logger.exception("dsl_embedding stage failed (non-fatal): document=%s material=%s error=%s", document_id, material_id, exc)
        report_done("dsl_embedding", {"total": 1, "processed": 1})

        # ── Stage 12: component_assembly ───────────────────────────────────
        component_artifact = artifact("component_assembly")
        if component_artifact:
            component_result = _from_agent_dict("component_assembly", component_artifact)
            logger.info("Resuming document pipeline: loaded component_assembly artifact for document %s", document_id)
        else:
            report_start("component_assembly", total=1, unit="llm_call")
            try:
                ca_agent = _instantiate(agent_classes["ComponentAssemblyAgent"])
                component_result = ca_agent.run(
                    qualified_claims=qualified, equations=equations,
                    thesis=thesis, dsl=dsl, cartridge_id=cartridge_id,
                    claim_objects=claim_objects,
                    evidence_registry=evidence,
                    derivations=derivations,
                )
            except Exception as exc:
                logger.exception("component_assembly stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("component_assembly", str(exc), cause=exc) from exc
            save_artifact("component_assembly", component_result)
        result.component_count = len(component_result.components)
        report_done("component_assembly", {
            "components": len(component_result.components), "total": 1, "processed": 1,
        })

        # ── Stage 12a: component_graph (hybrid deterministic/LLM edge builder) ─
        component_graph_artifact = artifact("component_graph")
        if component_graph_artifact:
            component_graph_result = _from_agent_dict("component_graph", component_graph_artifact)
            logger.info("Resuming document pipeline: loaded component_graph artifact for document %s", document_id)
        else:
            report_start("component_graph", total=1, unit="llm_call")
            try:
                cg_agent = _instantiate(agent_classes["ComponentGraphAgent"])
                # Flatten claims for Material 4 context
                flat_claims = [
                    {"claim_id": c.claim_id, "text": c.text}
                    for c in (getattr(claim_objects, "claims", []) or [])
                ]
                # Flatten evidence records for Material 4 context
                flat_evidence = [
                    {"evidence_id": r.evidence_id, "evidence_text": r.evidence_text}
                    for r in (getattr(evidence, "records", []) or [])
                ]
                component_graph_result = cg_agent.run(
                    components=component_result,
                    dsl=dsl,
                    derivations=derivations,
                    claims=flat_claims,
                    evidence_snippets=flat_evidence,
                    cartridge_id=cartridge_id,
                )
            except Exception as exc:
                logger.exception(
                    "component_graph stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                # フォールバック: ノードのみ、エッジなし
                from episteme_graph.agents.component_graph.schema import ComponentGraphResult
                component_graph_result = ComponentGraphResult.make_fallback(
                    document_id, cartridge_id, str(exc)
                )
            save_artifact("component_graph", component_graph_result)
        report_done("component_graph", {
            "nodes": len(getattr(component_graph_result, "nodes", []) or []),
            "edges": len(getattr(component_graph_result, "edges", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 12b: course_mapping (deterministic component → topic map) ─
        course_mapping_artifact = artifact("course_mapping")
        if course_mapping_artifact:
            course_mapping = _from_agent_dict("course_mapping", course_mapping_artifact)
            logger.info("Resuming document pipeline: loaded course_mapping artifact for document %s", document_id)
        else:
            report_start("course_mapping", total=1, unit="builder")
            try:
                course_mapping = _build_course_mapping(
                    agent_classes=agent_classes,
                    document_id=document_id,
                    cartridge_id=cartridge_id,
                    component_result=component_result,
                    claim_objects=claim_objects,
                )
            except Exception as exc:
                logger.exception(
                    "course_mapping stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                course_mapping = _empty_course_mapping_result(document_id, cartridge_id)
            save_artifact("course_mapping", course_mapping)
        report_done("course_mapping", {
            "topics": len(getattr(course_mapping, "topics", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 12c: blueprint (narrative arc) ───────────────────────────
        blueprint_artifact_data = artifact("blueprint")
        if blueprint_artifact_data:
            blueprint = _from_agent_dict("blueprint", blueprint_artifact_data)
            logger.info("Resuming document pipeline: loaded blueprint artifact for document %s", document_id)
        else:
            report_start("blueprint", total=1, unit="builder")
            try:
                blueprint = _build_blueprint(
                    agent_classes=agent_classes,
                    course_mapping=course_mapping,
                    component_result=component_result,
                    course_id=course_id,
                )
            except Exception as exc:
                logger.exception(
                    "blueprint stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                blueprint = _empty_blueprint_result(document_id, course_id)
            save_artifact("blueprint", blueprint)
        report_done("blueprint", {
            "steps": len(getattr(blueprint, "narrative_arc", []) or []),
            "total": 1,
            "processed": 1,
        })

        # ── Stage 12d: export_validation ───────────────────────────────────
        export_validation_artifact = artifact("export_validation")
        if export_validation_artifact:
            validation_result_dict = export_validation_artifact
            logger.info("Resuming document pipeline: loaded export_validation artifact for document %s", document_id)
        else:
            report_start("export_validation", total=1, unit="gate")
            try:
                from .export_validation_gate import ExportValidationGate

                gate = ExportValidationGate()
                validation_result = gate.run(
                    artifacts=dict(previous_artifacts),
                    component_result=component_result,
                    course_mapping=course_mapping,
                    claim_objects=claim_objects,
                    evidence=evidence,
                    dsl=dsl,
                )
                validation_result_dict = validation_result.to_dict()
            except Exception as exc:
                logger.exception(
                    "export_validation stage failed (non-fatal): document=%s material=%s error=%s",
                    document_id, material_id, exc,
                )
                validation_result_dict = {
                    "status": "passed_with_warnings",
                    "exportable": True,
                    "publish_ready": False,
                    "errors": [],
                    "warnings": [{"code": "GATE_ERROR", "message": str(exc), "artifact": "export_validation"}],
                    "review_items": [],
                    "summary": {"error_count": 0, "warning_count": 1, "review_required_count": 0},
                }
            save_artifact("export_validation", validation_result_dict)
        report_done("export_validation", {
            "status": validation_result_dict.get("status"),
            "error_count": (validation_result_dict.get("summary") or {}).get("error_count", 0),
            "warning_count": (validation_result_dict.get("summary") or {}).get("warning_count", 0),
            "total": 1,
            "processed": 1,
        })

        # Block persist / completed if validation failed
        if validation_result_dict.get("status") == "failed_validation":
            error_count = (validation_result_dict.get("summary") or {}).get("error_count", 0)
            upsert_analysis_run(
                run_id=run_id,
                document_id=document_id,
                material_id=material_id,
                cartridge_id=cartridge_id,
                status="failed",
                current_stage="export_validation",
                error_message=f"ExportValidationGate: {error_count} hard error(s) found; persist blocked",
            )
            result.final_stage = "export_validation"
            logger.warning(
                "ExportValidationGate blocked persist for document=%s: %d error(s)",
                document_id, error_count,
            )
            raise PipelineStageError(
                "export_validation",
                f"Validation failed with {error_count} hard error(s); see export_validation artifact",
            )

        # ── Stage 13: persist_claims_components_graph ──────────────────────
        report_start("persist_claims_components_graph", total=3, unit="tables")
        if artifact("persist_claims_components_graph"):
            persisted = artifact("persist_claims_components_graph") or {}
            result.claim_count = int(persisted.get("claims") or 0)
        else:
            try:
                saved_claims = persist_qualified_claims(
                    document_id=document_id,
                    qualified_result=qualified,
                    chunk_index=chunk_index,
                )
                result.claim_count = len(saved_claims)
                report_item("persist_claims_components_graph", 1, 3, "tables")

                id_map = persist_components(
                    document_id=document_id,
                    component_result=component_result,
                    course_id=course_id,
                )
                report_item("persist_claims_components_graph", 2, 3, "tables")
                persist_component_graph(
                    document_id=document_id,
                    component_id_map=id_map,
                    component_result=component_result,
                    dsl_result=dsl,
                    course_id=course_id,
                    component_graph_result=component_graph_result,
                )
                save_artifact("persist_claims_components_graph", {
                    "claims": result.claim_count,
                    "components": result.component_count,
                })
            except Exception as exc:
                logger.exception("persist_claims_components_graph stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError(
                    "persist_claims_components_graph", str(exc), cause=exc
                ) from exc
        report_done("persist_claims_components_graph", {
            "claims": result.claim_count,
            "components": result.component_count,
            "total": 3,
            "processed": 3,
        })

        # ── Stage 14: completed ────────────────────────────────────────────
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="completed",
            current_stage="completed",
            stage_outputs={"completed": {
                "chunks": result.chunk_count,
                "claims": result.claim_count,
                "components": result.component_count,
                "dsl_nodes": result.dsl_node_count,
                "dsl_edges": result.dsl_edge_count,
            }},
        )
        result.final_stage = "completed"
        report_done("completed", {
            "chunks": result.chunk_count,
            "claims": result.claim_count,
            "components": result.component_count,
        }, run_status="completed")
        return result

    except PipelineStageError as exc:
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="failed",
            current_stage=exc.stage,
            error_message=str(exc),
        )
        result.final_stage = exc.stage
        raise
    finally:
        if pdf_path:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass


def _instantiate(agent_class_or_instance):
    """class 渡しなら ()、instance 渡しならそのまま返す。"""
    if isinstance(agent_class_or_instance, type):
        return agent_class_or_instance()
    return agent_class_or_instance


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_to_plain_data(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain_data(v) for k, v in value.items()}
    return value


def _from_source_chunks(value: list[dict]) -> list:
    from .chunker import SourceChunk

    return [
        SourceChunk(
            chunk_index=int(v.get("chunk_index", i)),
            section_id=v.get("section_id"),
            block_ids=list(v.get("block_ids") or []),
            page_start=int(v.get("page_start") or 1),
            page_end=int(v.get("page_end") or v.get("page_start") or 1),
            text=v.get("text") or "",
            metadata=dict(v.get("metadata") or {}),
            formulas=list(v.get("formulas") or []),
        )
        for i, v in enumerate(value or [])
    ]


def _from_agent_dict(stage: str, value: dict) -> Any:
    if stage == "document_structure":
        from episteme_graph.agents.document_structure.schema import DocumentStructureResult
        return DocumentStructureResult.from_dict(value)
    if stage == "paper_skeleton":
        from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
        return PaperSkeletonResult.from_dict(value)
    if stage == "rhetorical_role":
        from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult
        return RhetoricalRoleResult.from_dict(value)
    if stage == "claim_qualification":
        from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
        return ClaimQualificationResult.from_dict(value)
    if stage == "equation_semantics":
        from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
        return EquationSemanticsResult.from_dict(value)
    if stage == "thesis_reconstruction":
        from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
        return ThesisReconstructionResult.from_dict(value)
    if stage == "dsl_linking":
        from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
        return DSLLinkingResult.from_dict(value)
    if stage == "component_assembly":
        from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
        return ComponentAssemblyResult.from_dict(value)
    if stage == "component_graph":
        from episteme_graph.agents.component_graph.schema import ComponentGraphResult
        return ComponentGraphResult.from_dict(value)
    if stage == "evidence_registry":
        from episteme_graph.agents.evidence_registry.schema import EvidenceRegistryResult
        return EvidenceRegistryResult.from_dict(value)
    if stage == "claim_object_builder":
        from episteme_graph.agents.claim_object_builder.schema import ClaimObjectBuildResult
        return ClaimObjectBuildResult.from_dict(value)
    if stage == "derivation_chain":
        from episteme_graph.agents.derivation_chain.schema import DerivationChainResult
        return DerivationChainResult.from_dict(value)
    if stage == "figure_table_semantics":
        # FigureTableSemanticsResult lacks from_dict; use raw dict for resume.
        return value
    if stage == "course_mapping":
        # CourseMappingResult lacks from_dict; use raw dict for resume.
        return value
    if stage == "blueprint":
        from episteme_graph.agents.blueprint.schema import BlueprintResult
        return BlueprintResult.from_dict(value)
    return value


# ---------------------------------------------------------------------------
# Helpers for the deterministic / cross-link agents
# ---------------------------------------------------------------------------


def _resolve_agent_factory(agent_classes: dict, key: str, default_factory):
    """Pick agent class from the merged dict, or fall back to default_factory().

    Tests may inject a partial ``agents`` dict that omits the deterministic
    builders; we still want them to run so artifacts are populated.
    """
    cls = agent_classes.get(key)
    if cls is None:
        return default_factory()
    return _instantiate(cls)


def _build_evidence_registry(
    *,
    agent_classes: dict,
    document_id: str,
    cartridge_id: str | None,
    structure: Any,
    qualified: Any,
    equations: Any,
):
    from episteme_graph.agents.evidence_registry.builder import EvidenceRegistryBuilder

    builder_cls = agent_classes.get("EvidenceRegistryBuilder") or EvidenceRegistryBuilder
    builder = builder_cls(structure)

    seen_block_ids: set[str] = set()

    # Register evidence for each accepted qualified span.
    for span in getattr(qualified, "qualified_spans", []) or []:
        block_id = getattr(span, "block_id", None)
        if not block_id or block_id in seen_block_ids:
            continue
        qual = getattr(span, "qualification", {}) or {}
        if isinstance(qual, dict) and qual.get("status") not in (None, "accepted"):
            continue
        builder.add_for_block(
            block_id,
            evidence_role="source_quote",
            review_note=getattr(span, "reason", "") or "",
        )
        seen_block_ids.add(block_id)

    # Register evidence for each equation block.
    for record in getattr(equations, "equations", []) or []:
        src = getattr(record, "source_extraction", None)
        loc = getattr(src, "source_location", None) if src else None
        block_id = (loc.get("block_id") if isinstance(loc, dict) else None) or getattr(record, "block_id", None)
        if not block_id or block_id in seen_block_ids:
            continue
        builder.add_for_block(block_id, evidence_role="equation_quote")
        seen_block_ids.add(block_id)

    # Register evidence for figure/table caption blocks from structure.
    for block in getattr(structure, "blocks", []) or []:
        block_type = getattr(block, "block_type", None)
        block_id = getattr(block, "block_id", None)
        if not block_id or block_id in seen_block_ids:
            continue
        if block_type == "figure_caption":
            builder.add_for_block(block_id, evidence_role="figure_caption_quote")
            seen_block_ids.add(block_id)
        elif block_type == "table_caption":
            builder.add_for_block(block_id, evidence_role="table_caption_quote")
            seen_block_ids.add(block_id)

    return builder.build(document_id=document_id, cartridge_id=cartridge_id)


def _empty_evidence_registry(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.evidence_registry.schema import EvidenceRegistryResult

    return EvidenceRegistryResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        records=[],
        validation_issues=[],
    )


def _build_claim_objects(
    *,
    agent_classes: dict,
    document_id: str,
    cartridge_id: str | None,
    qualified: Any,
    equations: Any,
    evidence: Any,
):
    from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder

    builder_cls = agent_classes.get("ClaimObjectBuilder") or ClaimObjectBuilder
    equation_index: dict[str, Any] = {}
    for record in getattr(equations, "equations", []) or []:
        eq_id = getattr(record, "equation_id", None)
        if eq_id:
            equation_index[eq_id] = record

    builder = builder_cls(
        evidence_registry=evidence,
        equation_index=equation_index,
        cartridge_ontology=None,
        equation_semantics_result=equations,
    )
    spans = list(getattr(qualified, "qualified_spans", []) or [])
    return builder.build(
        document_id=document_id,
        qualified_spans=spans,
        cartridge_id=cartridge_id,
    )


def _empty_claim_object_result(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.claim_object_builder.schema import ClaimObjectBuildResult

    return ClaimObjectBuildResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        claims=[],
        validation_issues=[],
    )


def _build_derivation_chains(
    *,
    agent_classes: dict,
    cartridge_id: str | None,
    equations: Any,
    claim_objects: Any,
    evidence: Any = None,
):
    from episteme_graph.agents.derivation_chain.agent import DerivationChainAgent

    agent = _resolve_agent_factory(
        agent_classes, "DerivationChainAgent", DerivationChainAgent
    )

    # equation_id -> [claim_id, ...] from claim_object_builder.
    claim_link_index: dict[str, list[str]] = {}
    for claim in getattr(claim_objects, "claims", []) or []:
        cid = getattr(claim, "claim_id", None)
        if not cid:
            continue
        for eq_id in getattr(claim, "equation_ids", []) or []:
            claim_link_index.setdefault(eq_id, []).append(cid)

    return agent.run(
        equations=equations,
        cartridge_id=cartridge_id,
        claim_link_index=claim_link_index,
        claim_build_result=claim_objects,
        evidence_registry=evidence,
    )


def _empty_derivation_chain_result(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

    return DerivationChainResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        chains=[],
        validation_issues=[],
    )


def _build_figure_table_semantics(
    *,
    agent_classes: dict,
    cartridge_id: str | None,
    structure: Any,
    evidence: Any,
    claim_objects: Any,
):
    from episteme_graph.agents.figure_table_semantics.agent import (
        FigureTableSemanticsAgent,
    )

    agent = _resolve_agent_factory(
        agent_classes, "FigureTableSemanticsAgent", FigureTableSemanticsAgent
    )

    # block_id -> [evidence_id, ...] for caption blocks.
    evidence_index: dict[str, list[str]] = {}
    for record in getattr(evidence, "records", []) or []:
        block_id = getattr(getattr(record, "source", None), "block_id", None)
        ev_id = getattr(record, "evidence_id", None)
        if block_id and ev_id:
            evidence_index.setdefault(block_id, []).append(ev_id)

    # block_id -> [claim_id, ...] from claim_objects (claim's source span block).
    claim_link_index: dict[str, list[str]] = {}
    for claim in getattr(claim_objects, "claims", []) or []:
        for span_id in getattr(claim, "source_span_ids", []) or []:
            claim_link_index.setdefault(span_id, []).append(claim.claim_id)

    return agent.run(
        structure,
        cartridge_id=cartridge_id,
        evidence_index=evidence_index,
        claim_link_index=claim_link_index,
    )


def _empty_figure_table_result(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.figure_table_semantics.schema import (
        FigureTableSemanticsResult,
    )

    return FigureTableSemanticsResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        figures=[],
        tables=[],
        validation_issues=[],
    )


def _build_course_mapping(
    *,
    agent_classes: dict,
    document_id: str,
    cartridge_id: str | None,
    component_result: Any,
    claim_objects: Any,
):
    from episteme_graph.agents.course_mapping.agent import CourseMappingAgent

    agent = _resolve_agent_factory(
        agent_classes, "CourseMappingAgent", CourseMappingAgent
    )
    components = list(getattr(component_result, "components", []) or [])
    claims = list(getattr(claim_objects, "claims", []) or [])
    return agent.run(
        document_id=document_id,
        components=components,
        claims=claims,
        cartridge_id=cartridge_id,
    )


def _empty_course_mapping_result(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.course_mapping.schema import CourseMappingResult

    return CourseMappingResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        topics=[],
        validation_issues=[],
    )


def _build_blueprint(
    *,
    agent_classes: dict,
    course_mapping: Any,
    component_result: Any,
    course_id: str | None,
):
    from episteme_graph.agents.blueprint.agent import BlueprintAgent

    agent = _resolve_agent_factory(agent_classes, "BlueprintAgent", BlueprintAgent)
    return agent.run(
        course_mapping,
        component_result,
        course_id=course_id,
    )


def _empty_blueprint_result(document_id: str, course_id: str | None):
    from episteme_graph.agents.blueprint.schema import BlueprintResult

    return BlueprintResult(
        blueprint_id=f"blueprint_{document_id}",
        document_id=document_id,
        source_course_id=course_id or document_id,
        audience_level="graduate_seminar",
        narrative_arc=[],
        review_notes=[],
        validation_issues=[],
    )


def _agent_input_count(
    stage: str,
    agent_classes: dict,
    structure: Any,
    skeleton: Any,
    cartridge_id: str | None,
    *,
    roles: Any | None = None,
) -> int:
    try:
        if stage == "rhetorical_role":
            agent = _instantiate(agent_classes["RhetoricalRoleAgent"])
            cartridge = agent._load_cartridge(cartridge_id)
            return len(agent._input_builder.build(structure, skeleton, cartridge=cartridge))
        if stage == "claim_qualification":
            agent = _instantiate(agent_classes["ClaimQualificationAgent"])
            cartridge = agent._load_cartridge(cartridge_id)
            return len(agent._input_builder.build(structure, skeleton, roles, cartridge=cartridge))
        if stage == "equation_semantics":
            agent = _instantiate(agent_classes["EquationSemanticsAgent"])
            cartridge = agent._load_cartridge(cartridge_id)
            return len(agent._input_builder.build(structure, skeleton=skeleton, roles=roles, cartridge=cartridge))
    except Exception:
        logger.warning("Failed to estimate %s input count", stage, exc_info=True)
    return 0
