"""8 agent pipeline orchestrator (issue #226, #266).

PDF/TeX archive → DocumentStructure → SourceChunking → SourceEmbedding → PaperSkeleton →
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
    _claim_legacy_keys,
    get_active_analysis_run_id,
    get_latest_analysis_run,
    set_active_analysis_run,
    load_source_chunk_index,
    persist_component_graph,
    persist_components,
    persist_document_embedding,
    persist_equation_previews_to_chunks,
    persist_qualified_claims,
    persist_source_chunks,
    upsert_analysis_run,
)
from .tex_archive import build_structure_from_tex_archive

logger = logging.getLogger(__name__)

ARTIFACTS_KEY = "_artifacts"


PIPELINE_STAGES = [
    "save_pdf",
    "grobid_parse",
    "document_structure",
    "source_chunking",
    "source_embedding",
    "paper_skeleton",
    "rhetorical_role",
    "claim_qualification",
    "equation_semantics",
    "evidence_registry",
    "claim_object_builder",
    "symbol_registry",
    "derivation_chain",
    "figure_table_semantics",
    "thesis_reconstruction",
    "dsl_linking",
    "dsl_embedding",
    "component_assembly",
    "component_graph",
    "narrative_annotator",
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
    source_kind: str = "pdf",
    cartridge_id: str | None = None,
    course_id: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    agents: dict | None = None,
    resume: bool = True,
    target_stage: str | None = None,
    start_stage: str | None = None,
) -> DocumentPipelineResult:
    """新 pipeline 本体。同期実行。

    Args:
        pdf_bytes: 入力バイナリ。source_kind="pdf" では PDF、source_kind="tex_archive"
            では .tar.gz TeX source archive。
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

    if target_stage is not None and target_stage not in PIPELINE_STAGES:
        raise ValueError(f"unknown pipeline stage: {target_stage}")
    if start_stage is not None and start_stage not in PIPELINE_STAGES:
        raise ValueError(f"unknown pipeline start stage: {start_stage}")
    stage_order = {stage: idx for idx, stage in enumerate(PIPELINE_STAGES)}
    start_index = stage_order[start_stage] if start_stage else None
    if start_stage and target_stage and stage_order[start_stage] > stage_order[target_stage]:
        raise ValueError(f"start_stage {start_stage!r} is after target_stage {target_stage!r}")

    previous_run = (
        get_latest_analysis_run(document_id=document_id, material_id=material_id)
        if resume and agents is None else None
    )
    if previous_run and previous_run.get("status") == "completed" and target_stage is None and start_stage is None:
        previous_run = None
    previous_outputs = dict((previous_run or {}).get("stage_outputs") or {})
    previous_artifacts = dict(previous_outputs.get(ARTIFACTS_KEY) or {})
    if start_index is not None:
        previous_artifacts = {
            stage: value
            for stage, value in previous_artifacts.items()
            if stage_order.get(stage, 10_000) < start_index
        }
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

    def should_use_artifact(stage: str) -> bool:
        has_artifact = artifact(stage) is not None
        if start_index is not None:
            if stage_order[stage] < start_index:
                if not has_artifact:
                    raise PipelineStageError(
                        stage,
                        f"required artifact '{stage}' is missing for restart from '{start_stage}'",
                    )
                return True
            return False
        if target_stage is not None and target_stage != stage and not has_artifact:
            raise PipelineStageError(
                stage,
                f"required artifact '{stage}' is missing for single-stage run '{target_stage}'",
            )
        return target_stage != stage and has_artifact

    def finish_target_stage(stage: str, payload: dict | None = None) -> bool:
        if target_stage != stage:
            return False
        result.final_stage = stage
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="completed",
            current_stage=stage,
            stage_outputs={stage: payload or {"status": "completed", "progress": 100}},
        )
        return True

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
        if source_kind not in {"pdf", "tex_archive"}:
            raise ValueError(f"unsupported source_kind: {source_kind}")

        source_suffix = ".pdf" if source_kind == "pdf" else ".tar.gz"

        # ── Stage 1: save_pdf (一時ファイル化。MinIO への保存は呼び出し側担当) ─
        report_done("save_pdf", {"size_bytes": len(pdf_bytes), "source_kind": source_kind})
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=source_suffix, delete=False
        ) as f:
            f.write(pdf_bytes)
            pdf_path = f.name

        # ── Stage 2: grobid_parse ──────────────────────────────────────────
        tei_xml: str | None = None
        grobid_artifact = artifact("grobid_parse")
        if should_use_artifact("grobid_parse"):
            tei_xml = (grobid_artifact or {}).get("tei_xml") or None
            logger.info("Resuming document pipeline: loaded grobid_parse artifact for document %s", document_id)
        elif source_kind == "tex_archive":
            save_artifact("grobid_parse", {
                "status": "skipped",
                "reason": "tex_archive",
                "tei_bytes": 0,
                "tei_xml": None,
            })
        else:
            report_start("grobid_parse", total=1, unit="document")
            try:
                tei_xml = _run_grobid_parse(pdf_bytes)
            except Exception:
                logger.warning(
                    "grobid_parse failed (non-fatal); will use PyMuPDF fallback: document=%s",
                    document_id,
                    exc_info=True,
                )
                tei_xml = None
            save_artifact("grobid_parse", {
                "status": "ok" if tei_xml else "fallback",
                "tei_bytes": len(tei_xml.encode()) if tei_xml else 0,
                "tei_xml": tei_xml,
            })
        grobid_status = "skipped" if source_kind == "tex_archive" else ("ok" if tei_xml else "fallback")
        report_done("grobid_parse", {
            "status": grobid_status,
            "tei_bytes": len(tei_xml.encode()) if tei_xml else 0,
        })
        if finish_target_stage("grobid_parse", {"status": grobid_status}):
            return result

        # ── Stage 3: document_structure ────────────────────────────────────
        structure_artifact = artifact("document_structure")
        if should_use_artifact("document_structure"):
            structure = _from_agent_dict("document_structure", structure_artifact)
            logger.info("Resuming document pipeline: loaded document_structure artifact for document %s", document_id)
        else:
            report_start("document_structure", total=1, unit="document")
            try:
                if source_kind == "tex_archive":
                    structure = build_structure_from_tex_archive(
                        pdf_bytes,
                        document_id=document_id,
                        source_file=filename or pdf_path,
                        cartridge_id=cartridge_id,
                    )
                else:
                    ds_agent = agent_classes["DocumentStructureAgent"]() if isinstance(
                        agent_classes["DocumentStructureAgent"], type
                    ) else agent_classes["DocumentStructureAgent"]
                    structure = ds_agent.run(
                        pdf_path=pdf_path,
                        cartridge_id=cartridge_id,
                        tei_xml=tei_xml,
                    )
                structure.document_id = document_id  # 強制的に上書きして後段一貫
            except Exception as exc:
                raise PipelineStageError("document_structure", str(exc), cause=exc) from exc
            save_artifact("document_structure", structure)
        report_done("document_structure", {
            "block_count": len(structure.blocks),
            "section_count": len(structure.sections),
        })
        structure.document_id = document_id
        if source_kind == "pdf":
            structure.source_file = pdf_path
        if finish_target_stage("document_structure", {"block_count": len(structure.blocks), "section_count": len(structure.sections)}):
            return result

        # ── Stage 3: source_chunking ───────────────────────────────────────
        source_chunks_artifact = artifact("source_chunking")
        if should_use_artifact("source_chunking"):
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
        if finish_target_stage("source_chunking", {"chunk_count": len(source_chunks)}):
            return result

        # ── Stage 4: source_embedding ──────────────────────────────────────
        if should_use_artifact("source_embedding"):
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
        if finish_target_stage("source_embedding", {"saved_chunks": len(chunk_index), "total": len(source_chunks), "processed": len(source_chunks)}):
            return result

        # ── Stage 5: paper_skeleton ────────────────────────────────────────
        skeleton_artifact = artifact("paper_skeleton")
        if should_use_artifact("paper_skeleton"):
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
        if finish_target_stage("paper_skeleton", {"document_id": document_id, "total": 1, "processed": 1}):
            return result

        # ── Stage 6: rhetorical_role ───────────────────────────────────────
        roles_artifact = artifact("rhetorical_role")
        if should_use_artifact("rhetorical_role"):
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
        if finish_target_stage("rhetorical_role", getattr(roles, "summary_stats", {}) or {}):
            return result

        # ── Stage 7: claim_qualification ───────────────────────────────────
        qualified_artifact = artifact("claim_qualification")
        if should_use_artifact("claim_qualification"):
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
        if finish_target_stage("claim_qualification", {"qualified_count": len(qualified.qualified_spans)}):
            return result

        # ── Stage 8: equation_semantics ────────────────────────────────────
        equations_artifact = artifact("equation_semantics")
        if should_use_artifact("equation_semantics"):
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
            try:
                persist_equation_previews_to_chunks(document_id, equations)
            except Exception:
                logger.warning(
                    "Failed to persist equation previews into chunks for document %s",
                    document_id,
                    exc_info=True,
                )
        report_done("equation_semantics", {"equations": len(getattr(equations, "equations", []) or [])})
        if finish_target_stage("equation_semantics", {"equations": len(getattr(equations, "equations", []) or [])}):
            return result

        # ── Stage 8b: evidence_registry (deterministic, source-backed) ─────
        evidence_artifact = artifact("evidence_registry")
        if should_use_artifact("evidence_registry"):
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
                    roles=roles,
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
        # Document completeness check at the DocumentStructure / EvidenceRegistry
        # exit (#366): record a deterministic ingest-completeness artifact and
        # propagate failures into document_structure.validation_issues so the
        # signal flows downstream (and a truncated document never silently
        # reaches publish-ready).
        try:
            # equation_semantics (stage 8) runs before this point, so the real
            # EquationRecords are available and MUST be passed so equation artifact
            # coverage reflects them (#420) — otherwise the saved artifact is
            # permanently incomplete for any TeX document with math.
            _record_document_completeness(
                structure=structure,
                evidence=evidence,
                equations=equations,
                document_id=document_id,
                save_artifact=save_artifact,
            )
        except Exception:
            logger.exception(
                "document_completeness check failed (non-fatal): document=%s", document_id
            )
        if finish_target_stage("evidence_registry", {"records": len(getattr(evidence, "records", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 8c: claim_object_builder (deterministic claims.json) ─────
        claim_object_artifact = artifact("claim_object_builder")
        if should_use_artifact("claim_object_builder"):
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
                    document_structure=structure,
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
        if finish_target_stage("claim_object_builder", {"claims": len(getattr(claim_objects, "claims", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 8c.1: claim ID canonicalization contract (issue #340) ────
        # claim_object_builder is the source of truth for claim IDs. Re-map or
        # drop any provisional claim refs still carried by equation_semantics so
        # the downstream derivation_chain / component / graph / export artifacts
        # only ever reference final claim IDs (or nothing). Dropped refs are kept
        # as review warnings — we never silently retain an unresolved ref.
        try:
            eq_dropped = _canonicalize_equation_claim_links(equations, claim_objects)
            if eq_dropped:
                save_artifact("equation_semantics", equations)
                logger.info(
                    "Canonicalized equation claim links for document %s: dropped provisional refs on %d equation(s)",
                    document_id, len(eq_dropped),
                )
        except Exception:
            logger.warning(
                "equation claim-link canonicalization failed (non-fatal): document=%s",
                document_id, exc_info=True,
            )

        # ── Stage 8c.1b: claim↔equation link symmetry (issue #358) ─────────
        # One-way links are demoted to inferred: moved out of the primary link
        # fields into inferred_equation_ids / inferred_claim_ids (kept, never
        # dropped) plus review metadata on both artifacts.
        try:
            from episteme_graph.agents.id_canonicalization import (
                annotate_claim_equation_link_asymmetries,
            )

            asymmetries = annotate_claim_equation_link_asymmetries(
                claim_objects, equations
            )
            if asymmetries:
                save_artifact("claim_object_builder", claim_objects)
                save_artifact("equation_semantics", equations)
                logger.info(
                    "Annotated %d one-way claim↔equation link(s) for document %s",
                    asymmetries, document_id,
                )
        except Exception:
            logger.warning(
                "claim-equation link symmetry annotation failed (non-fatal): document=%s",
                document_id, exc_info=True,
            )

        # ── Stage 8c.2: symbol_registry (deterministic from equations, #355) ─
        # Aggregates defined/used symbols into a document-wide registry and
        # annotates DefinedSymbol.symbol_id on the equations in place. Non-fatal:
        # downstream stages do not depend on it yet.
        symbol_registry_artifact = artifact("symbol_registry")
        if should_use_artifact("symbol_registry"):
            symbol_registry = _from_agent_dict("symbol_registry", symbol_registry_artifact)
            logger.info("Resuming document pipeline: loaded symbol_registry artifact for document %s", document_id)
        else:
            report_start("symbol_registry", total=1, unit="builder")
            symbol_registry = None
            try:
                from episteme_graph.agents.symbol_registry.builder import SymbolRegistryBuilder

                symbol_registry = SymbolRegistryBuilder().run(
                    equations, cartridge_id=cartridge_id
                )
                # Issue #432: derive inter-equation links from structural cues
                # (shared symbols from the registry + textual references), record
                # link_provenance, drop dangling links, and assign link_status.
                # Mutates the equation records in place before they are persisted.
                try:
                    from episteme_graph.agents.symbol_registry.link_normalizer import (
                        EquationLinkNormalizer,
                    )

                    link_summary = EquationLinkNormalizer().normalize(
                        equations, symbol_registry
                    )
                    logger.info(
                        "equation link normalization for document %s: %s links, "
                        "%d dangling dropped, statuses=%s",
                        document_id,
                        link_summary.get("link_count"),
                        link_summary.get("dangling_dropped", 0),
                        link_summary.get("link_status_counts"),
                    )
                except Exception:
                    logger.warning(
                        "equation link normalization failed (non-fatal): document=%s",
                        document_id, exc_info=True,
                    )
                # The builder set DefinedSymbol.symbol_id in place and the link
                # normalizer rewrote the equation links; persist the annotated
                # equations so resumes keep the registry references and links.
                save_artifact("equation_semantics", equations)
                save_artifact("symbol_registry", symbol_registry)
            except Exception as exc:
                logger.warning(
                    "symbol_registry stage failed (non-fatal): document=%s error=%s",
                    document_id, exc, exc_info=True,
                )
        symbol_count = len(getattr(symbol_registry, "records", []) or [])
        report_done("symbol_registry", {"symbols": symbol_count, "total": 1, "processed": 1})
        if finish_target_stage("symbol_registry", {"symbols": symbol_count, "total": 1, "processed": 1}):
            return result

        # ── Stage 8d: derivation_chain (deterministic from equation links) ─
        derivation_artifact = artifact("derivation_chain")
        if should_use_artifact("derivation_chain"):
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
        # Defensive canonicalization (issue #340): even though equations were
        # canonicalized before this stage, re-resolve every derivation step's
        # claim refs against the final claim set so no provisional claim ID can
        # reach component / graph / export from a resumed or stale artifact.
        try:
            deriv_dropped = _canonicalize_derivation_claim_refs(derivations, claim_objects)
            if deriv_dropped:
                save_artifact("derivation_chain", derivations)
                logger.info(
                    "Canonicalized derivation claim refs for document %s: dropped provisional refs on %d step(s)",
                    document_id, len(deriv_dropped),
                )
        except Exception:
            logger.warning(
                "derivation claim-ref canonicalization failed (non-fatal): document=%s",
                document_id, exc_info=True,
            )
        report_done("derivation_chain", {
            "chains": len(getattr(derivations, "chains", []) or []),
            "total": 1,
            "processed": 1,
        })
        if finish_target_stage("derivation_chain", {"chains": len(getattr(derivations, "chains", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 8d.1: equation/derivation claim synthesis (issue #388) ─
        # Turn source-backed equation structure and system-level derivations into
        # atomic equation_backed / derived_from_linked_artifacts claims so the
        # claim artifact is not weak when prose claims miss equation-expressed
        # propositions. Additive and non-fatal: synthesised claims are appended to
        # the claim_object_builder artifact (and to claim_objects so downstream
        # component assembly can cite them).
        try:
            synthesized = _synthesize_equation_claims(
                equations=equations, derivations=derivations, claim_objects=claim_objects,
            )
            if synthesized:
                claim_objects.claims = list(getattr(claim_objects, "claims", []) or []) + synthesized
                save_artifact("claim_object_builder", claim_objects)
                logger.info(
                    "Synthesised %d equation/derivation-backed claims for document %s",
                    len(synthesized), document_id,
                )
        except Exception:
            logger.warning(
                "equation claim synthesis failed (non-fatal): document=%s",
                document_id, exc_info=True,
            )

        # ── Stage 8e: figure_table_semantics (caption-first deterministic) ─
        fig_tbl_artifact = artifact("figure_table_semantics")
        if should_use_artifact("figure_table_semantics"):
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
        if finish_target_stage("figure_table_semantics", {"figures": len(getattr(fig_tbl, "figures", []) or []), "tables": len(getattr(fig_tbl, "tables", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 9: thesis_reconstruction ─────────────────────────────────
        thesis_artifact = artifact("thesis_reconstruction")
        if should_use_artifact("thesis_reconstruction"):
            thesis = _from_agent_dict("thesis_reconstruction", thesis_artifact)
            logger.info("Resuming document pipeline: loaded thesis_reconstruction artifact for document %s", document_id)
        else:
            report_start("thesis_reconstruction", total=1, unit="llm_call")
            try:
                th_agent = _instantiate(agent_classes["ThesisReconstructionAgent"])
                thesis = th_agent.run(
                    skeleton=skeleton, qualified_claims=qualified, equations=equations,
                    cartridge_id=cartridge_id, claim_objects=claim_objects,
                )
            except Exception as exc:
                logger.exception("thesis_reconstruction stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("thesis_reconstruction", str(exc), cause=exc) from exc
            save_artifact("thesis_reconstruction", thesis)
        report_done("thesis_reconstruction", {"total": 1, "processed": 1})
        if finish_target_stage("thesis_reconstruction", {"total": 1, "processed": 1}):
            return result

        # ── Stage 10: dsl_linking ──────────────────────────────────────────
        dsl_artifact = artifact("dsl_linking")
        if should_use_artifact("dsl_linking"):
            dsl = _from_agent_dict("dsl_linking", dsl_artifact)
            logger.info("Resuming document pipeline: loaded dsl_linking artifact for document %s", document_id)
        else:
            report_start("dsl_linking", total=1, unit="llm_call")
            try:
                dsl_agent = _instantiate(agent_classes["DSLLinkingAgent"])
                dsl = dsl_agent.run(
                    qualified_claims=qualified, equations=equations, thesis=thesis,
                    claim_objects=claim_objects,
                )
            except Exception as exc:
                logger.exception("dsl_linking stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("dsl_linking", str(exc), cause=exc) from exc
            # Issue #442: cross-link the thesis artifact and the DSL graph so the
            # thesis has explicit traversal anchors and the anchor nodes carry the
            # is_thesis_anchor flag. Deterministic, non-fatal — re-save both.
            try:
                from episteme_graph.agents.thesis_reconstruction.anchor_linker import (
                    link_thesis_anchors,
                )
                anchors = link_thesis_anchors(thesis, dsl)
                if anchors:
                    save_artifact("thesis_reconstruction", thesis)
            except Exception as exc:
                logger.warning(
                    "thesis anchor linking failed (non-fatal): document=%s error=%s",
                    document_id, exc,
                )
            save_artifact("dsl_linking", dsl)
        result.dsl_node_count = len(dsl.nodes)
        result.dsl_edge_count = len(dsl.edges)
        report_done("dsl_linking", {
            "nodes": len(dsl.nodes), "edges": len(dsl.edges), "total": 1, "processed": 1,
        })
        if finish_target_stage("dsl_linking", {"nodes": len(dsl.nodes), "edges": len(dsl.edges), "total": 1, "processed": 1}):
            return result

        # ── Stage 11: dsl_embedding ────────────────────────────────────────
        report_start("dsl_embedding", total=1, unit="embedding")
        if not should_use_artifact("dsl_embedding"):
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
        if finish_target_stage("dsl_embedding", {"total": 1, "processed": 1}):
            return result

        # ── Stage 12: component_assembly ───────────────────────────────────
        component_artifact = artifact("component_assembly")
        reuse_component_artifact = should_use_artifact("component_assembly")
        if reuse_component_artifact:
            component_result = _from_agent_dict("component_assembly", component_artifact)
            reuse_component_artifact = _component_assembly_artifact_reusable(
                component_result, document_id=document_id, material_id=material_id,
            )
        if reuse_component_artifact:
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
        component_done_payload: dict[str, Any] = {
            "components": len(component_result.components), "total": 1, "processed": 1,
        }
        fallback_info = _component_assembly_fallback_info(component_result)
        if fallback_info:
            logger.warning(
                "component_assembly used deterministic fallback: document=%s material=%s "
                "fallback_reason=%r original_failure_codes=%s fallback_components=%d",
                document_id, material_id,
                fallback_info["fallback_reason"],
                fallback_info["original_failure_codes"],
                fallback_info["fallback_component_count"],
            )
            component_done_payload.update(fallback_info)
        report_done("component_assembly", component_done_payload)
        if finish_target_stage("component_assembly", component_done_payload):
            return result

        # ── Stage 12a: component_graph (hybrid deterministic/LLM edge builder) ─
        component_graph_artifact = artifact("component_graph")
        if should_use_artifact("component_graph"):
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
        if finish_target_stage("component_graph", {"nodes": len(getattr(component_graph_result, "nodes", []) or []), "edges": len(getattr(component_graph_result, "edges", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 12a.1: narrative_annotator (reading layer for main graph, #360) ─
        # Annotation-only LLM stage: graph_summary / narrative_role /
        # transition_text are stored as a separate artifact and never modify the
        # graph. Non-fatal: downstream stages do not depend on it.
        narrative_artifact = artifact("narrative_annotator")
        if should_use_artifact("narrative_annotator"):
            narrative = _from_agent_dict("narrative_annotator", narrative_artifact)
            logger.info("Resuming document pipeline: loaded narrative_annotator artifact for document %s", document_id)
        else:
            report_start("narrative_annotator", total=1, unit="llm_call")
            narrative = None
            try:
                from episteme_graph.agents.narrative_annotator.agent import NarrativeAnnotator

                narrative = NarrativeAnnotator().run(
                    component_graph_result,
                    thesis=thesis,
                    derivations=derivations,
                    cartridge_id=cartridge_id,
                )
                save_artifact("narrative_annotator", narrative)
            except Exception as exc:
                logger.warning(
                    "narrative_annotator stage failed (non-fatal): document=%s error=%s",
                    document_id, exc, exc_info=True,
                )
        narrative_counts = {
            "node_narratives": len(getattr(narrative, "node_narratives", []) or []),
            "edge_narratives": len(getattr(narrative, "edge_narratives", []) or []),
            "total": 1,
            "processed": 1,
        }
        report_done("narrative_annotator", narrative_counts)
        if finish_target_stage("narrative_annotator", narrative_counts):
            return result

        # ── Stage 12b: course_mapping (deterministic component → topic map) ─
        course_mapping_artifact = artifact("course_mapping")
        if should_use_artifact("course_mapping"):
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
        if finish_target_stage("course_mapping", {"topics": len(getattr(course_mapping, "topics", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 12c: blueprint (narrative arc) ───────────────────────────
        blueprint_artifact_data = artifact("blueprint")
        if should_use_artifact("blueprint"):
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
        if finish_target_stage("blueprint", {"steps": len(getattr(blueprint, "narrative_arc", []) or []), "total": 1, "processed": 1}):
            return result

        # ── Stage 12d: export_validation ───────────────────────────────────
        export_validation_artifact = artifact("export_validation")
        if should_use_artifact("export_validation"):
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
        if finish_target_stage("export_validation", {"status": validation_result_dict.get("status"), "error_count": (validation_result_dict.get("summary") or {}).get("error_count", 0), "warning_count": (validation_result_dict.get("summary") or {}).get("warning_count", 0), "total": 1, "processed": 1}):
            return result

        # ── Export-validation failure: graceful degradation ─────────────────
        # 設計原則: source_embedding が成功した後は status=failed にしない。
        # chunks + embeddings は常に保存済みであり RAG は必ず動く。
        # validation エラーは「どの機能が使えないか」を示すだけで、
        # ユーザーを完全に詰まらせてはならない。
        #
        # エラーの種類に応じて persist をスキップし、completed（機能縮退）で完了する:
        #   component_graph エラー   → graph のみスキップ、claims + components は保存
        #   no_components /
        #   deterministic_fallback   → components + graph スキップ、claims は保存
        #   その他の品質エラー       → すべて保存（review_required フラグのみ）
        #
        # status=failed になる唯一のケースは source_chunking / source_embedding の
        # 失敗（= chunks が保存されていない）であり、それは既にパイプライン前段で
        # PipelineStageError として raise 済み。
        _COMPONENT_SKIP_CODES = {
            # LLM が完全に失敗して placeholder しかない → コンポーネント保存不可
            "component_assembly_deterministic_fallback",
            # コンポーネントが 1 件もない → 保存対象なし
            "no_components",
            # コンポーネント型が不正 → DB スキーマ違反になる
            "invalid_component_type",
        }
        _skip_graph_persist = False
        _skip_component_persist = False
        _degraded_stages: list[str] = []
        if validation_result_dict.get("status") == "failed_validation":
            all_val_errors = validation_result_dict.get("errors") or []
            component_skip_errors = [e for e in all_val_errors if e.get("code") in _COMPONENT_SKIP_CODES]
            graph_errors = [e for e in all_val_errors if e.get("artifact") == "component_graph"]

            if component_skip_errors:
                # components も graph も保存しない: claims だけ保存して completed
                _skip_component_persist = True
                _skip_graph_persist = True
                _degraded_stages = ["component_assembly", "component_graph"]
                logger.warning(
                    "ExportValidationGate: コンポーネント生成不可 (%d error(s)) — "
                    "claims のみ保存して completed に移行: document=%s material=%s",
                    len(all_val_errors), document_id, material_id,
                )
            elif graph_errors:
                # graph のみスキップ、claims + components は保存
                _skip_graph_persist = True
                _degraded_stages = ["component_graph"]
                logger.warning(
                    "ExportValidationGate: component_graph エラー (%d) — "
                    "graph のみスキップして completed に移行: document=%s material=%s",
                    len(graph_errors), document_id, material_id,
                )
            else:
                # その他の品質エラー: 全部保存して review_required フラグを残す
                _degraded_stages = []
                logger.warning(
                    "ExportValidationGate: 品質エラー (%d) — "
                    "全アーティファクトを保存して completed に移行: document=%s material=%s",
                    len(all_val_errors), document_id, material_id,
                )

        # ── Stage 13: persist_claims_components_graph ──────────────────────
        report_start("persist_claims_components_graph", total=3, unit="tables")
        if should_use_artifact("persist_claims_components_graph"):
            persisted = artifact("persist_claims_components_graph") or {}
            result.claim_count = int(persisted.get("claims") or 0)
        else:
            try:
                saved_claims = persist_qualified_claims(
                    document_id=document_id,
                    qualified_result=qualified,
                    chunk_index=chunk_index,
                )
                claim_id_map: dict[str, str] = {}
                for saved in saved_claims:
                    for key in _claim_legacy_keys(saved):
                        claim_id_map[key] = saved["claim_id"]
                result.claim_count = len(saved_claims)
                report_item("persist_claims_components_graph", 1, 3, "tables")

                id_map: dict[str, str] = {}
                if _skip_component_persist:
                    logger.warning(
                        "components persist skipped (validation errors): document=%s",
                        document_id,
                    )
                else:
                    id_map = persist_components(
                        document_id=document_id,
                        component_result=component_result,
                        course_id=course_id,
                        claim_id_map=claim_id_map,
                    )
                report_item("persist_claims_components_graph", 2, 3, "tables")

                if _skip_graph_persist or _skip_component_persist:
                    logger.warning(
                        "component_graph persist skipped (validation errors): document=%s",
                        document_id,
                    )
                else:
                    persist_component_graph(
                        document_id=document_id,
                        component_id_map=id_map,
                        component_result=component_result,
                        dsl_result=dsl,
                        course_id=course_id,
                        component_graph_result=component_graph_result,
                        claim_id_map=claim_id_map,
                        narrative_result=narrative,
                    )
                save_artifact("persist_claims_components_graph", {
                    "claims": result.claim_count,
                    "components": result.component_count,
                    "graph_skipped": _skip_graph_persist or _skip_component_persist,
                    "components_skipped": _skip_component_persist,
                    "degraded_stages": _degraded_stages,
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
        if finish_target_stage("persist_claims_components_graph", {"claims": result.claim_count, "components": result.component_count, "total": 3, "processed": 3}):
            return result

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
        # 初回 (initial) pipeline 完了時は、この Run を採用 (active) Run とする。
        # 再解析でも最新の completed initial run を active に進める（従来の
        # 「latest = 参照対象」挙動を維持）。revision Run はこの経路を通らず、
        # accept API でのみ optimistic に active を切り替える (#402)。
        # active pointer は best-effort: 失敗しても pipeline 自体は成功扱いにする。
        try:
            set_active_analysis_run(
                document_id=document_id,
                run_id=run_id,
                expected_run_id=get_active_analysis_run_id(document_id=document_id),
            )
        except Exception:
            logger.warning(
                "failed to set active analysis run for document=%s run=%s",
                document_id, run_id, exc_info=True,
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


def _component_assembly_artifact_reusable(
    component_result: Any,
    *,
    document_id: str,
    material_id: str,
) -> bool:
    """Decide whether a resumed component_assembly artifact may be reused.

    A deterministic-fallback artifact can never pass the export gate (#347), so
    reusing it would fail every resumed run at export_validation with the same
    hard error. Re-running the stage gives the LLM another chance, which is
    exactly what the gate error message instructs ("rerun component_assembly
    instead of persisting").
    """
    fallback_info = _component_assembly_fallback_info(component_result)
    if not fallback_info:
        return True
    logger.warning(
        "Resumed component_assembly artifact is a deterministic fallback "
        "(document=%s material=%s fallback_reason=%r original_failure_codes=%s); "
        "re-running component_assembly instead of reusing it",
        document_id, material_id,
        fallback_info["fallback_reason"],
        fallback_info["original_failure_codes"],
    )
    return False


def _component_assembly_fallback_info(component_result: Any) -> dict | None:
    """Detect a deterministic-fallback component_assembly result (#347).

    Works for both freshly built results and results reloaded from a stage
    artifact: the diagnostics carry fallback_reason, and each fallback
    component carries maturity_source="deterministic_fallback".
    """
    diagnostics = getattr(component_result, "diagnostics", {}) or {}
    fallback_components = [
        c for c in (getattr(component_result, "components", []) or [])
        if str(getattr(c, "maturity_source", "") or "") == "deterministic_fallback"
    ]
    fallback_reason = str(diagnostics.get("fallback_reason") or "")
    if not fallback_reason and not fallback_components:
        return None
    if not fallback_reason and fallback_components:
        fallback_reason = str(getattr(fallback_components[0], "fallback_reason", "") or "unknown")
    return {
        "fallback": True,
        "fallback_reason": fallback_reason,
        "original_failure_codes": list(diagnostics.get("original_failure_codes") or []),
        "fallback_component_count": len(fallback_components),
    }


def _summarize_export_validation_errors(validation_result_dict: dict, limit: int = 3) -> str:
    errors = validation_result_dict.get("errors") or []
    if not isinstance(errors, list) or not errors:
        return ""
    parts: list[str] = []
    for error in errors[:limit]:
        if not isinstance(error, dict):
            continue
        code = str(error.get("code") or "UNKNOWN")
        message = str(error.get("message") or "").strip()
        parts.append(f"{code}: {message}" if message else code)
    if not parts:
        return ""
    suffix = f"; first_errors={parts}"
    if len(errors) > limit:
        suffix += f" (+{len(errors) - limit} more)"
    return suffix


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


def _record_document_completeness(
    *,
    structure: Any,
    evidence: Any,
    equations: Any,
    document_id: str,
    save_artifact,
) -> dict:
    """Compute, persist, and propagate document completeness (#366 / #420).

    Runs at the DocumentStructure / equation_semantics / EvidenceRegistry exit.
    The equation_semantics result is passed through so equation artifact coverage
    reflects the real EquationRecords; without it the saved ``document_completeness``
    artifact would be permanently incomplete for any TeX document with math.
    Returns the report. Best-effort propagation onto ``structure``.
    """
    from .completeness import analyze_document_completeness

    report = analyze_document_completeness(
        _to_plain_data(structure),
        _to_plain_data(evidence),
        document_id=document_id,
        equations=_to_plain_data(equations),
    )
    save_artifact("document_completeness", report)
    if not report.get("complete", True):
        reasons = report.get("review_reasons") or []
        logger.warning(
            "document %s ingest looks incomplete: %s", document_id, reasons,
        )
        _propagate_completeness_to_structure(structure, reasons)
        save_artifact("document_structure", structure)
    return report


def _propagate_completeness_to_structure(structure: Any, review_reasons: list) -> None:
    """Attach document-completeness failures to structure.validation_issues (#366).

    Adds one warning ValidationIssue per completeness review reason (deduped by
    rule_id) so the signal propagates to downstream stages and the export
    validation gate instead of living only in a side artifact. Severity is
    ``warning`` — an incomplete ingest blocks publish-ready but is not a hard
    error. Best-effort: never raises into the pipeline.
    """
    issues = getattr(structure, "validation_issues", None)
    if issues is None:
        return
    try:
        from episteme_graph.agents.document_structure.schema import ValidationIssue
    except Exception:
        return
    existing = {getattr(i, "rule_id", None) for i in issues}
    for reason in review_reasons or []:
        rule_id = f"document_completeness_{reason}"
        if rule_id in existing:
            continue
        issues.append(ValidationIssue(
            rule_id=rule_id,
            severity="warning",
            message=f"document ingest completeness: {reason}",
        ))
        existing.add(rule_id)


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
    if stage == "narrative_annotator":
        from episteme_graph.agents.narrative_annotator.schema import NarrativeAnnotationResult
        return NarrativeAnnotationResult.from_dict(value)
    if stage == "evidence_registry":
        from episteme_graph.agents.evidence_registry.schema import EvidenceRegistryResult
        return EvidenceRegistryResult.from_dict(value)
    if stage == "claim_object_builder":
        from episteme_graph.agents.claim_object_builder.schema import ClaimObjectBuildResult
        return ClaimObjectBuildResult.from_dict(value)
    if stage == "symbol_registry":
        from episteme_graph.agents.symbol_registry.schema import SymbolRegistryResult
        return SymbolRegistryResult.from_dict(value)
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
    roles: Any = None,
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
        parent_evidence_id = builder.add_for_block(
            block_id,
            evidence_role="source_quote",
            review_note=getattr(span, "reason", "") or "",
        )
        # Sentence-level records (issue #363) so atomic claims can cite the
        # exact supporting sentence instead of the whole block.
        if parent_evidence_id:
            builder.add_sentences_for_block(
                block_id, parent_evidence_id=parent_evidence_id
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

    registry = builder.build(document_id=document_id, cartridge_id=cartridge_id)
    # RhetoricalRole span offsets vs evidence spans (issue #363).
    if roles is not None:
        from episteme_graph.agents.evidence_registry.builder import check_span_alignment

        check_span_alignment(roles, registry)
    return registry


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
    document_structure: Any = None,
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
        document_structure=document_structure,
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


def _canonicalize_equation_claim_links(equations: Any, claim_objects: Any) -> list[dict]:
    """Strip provisional claim refs from equation_semantics (issue #340).

    Resolves each equation's ``linked_claim_ids`` against the final claim set
    produced by claim_object_builder, dropping any that don't resolve and
    recording them as warning-level validation issues on the equations result.
    Returns the dropped-ref report (empty when nothing changed).
    """
    from episteme_graph.agents.equation_semantics.schema import (
        ValidationIssue as EqValidationIssue,
    )
    from episteme_graph.agents.id_canonicalization import (
        canonicalize_equation_claim_links,
    )

    dropped = canonicalize_equation_claim_links(equations, claim_objects)
    for entry in dropped:
        equations.validation_issues.append(EqValidationIssue(
            rule_id="unresolved_claim_ref_dropped",
            severity="warning",
            message=(
                f"equation {entry['equation_id']!r} dropped provisional claim ref(s) "
                f"not present in claims.json: {entry['dropped_claim_ids']}"
            ),
            field=entry["equation_id"],
        ))
    return dropped


def _canonicalize_derivation_claim_refs(derivations: Any, claim_objects: Any) -> list[dict]:
    """Strip provisional claim refs from derivation_chain steps (issue #340).

    Resolves every step's claim reference fields against the final claim set,
    dropping unresolved refs and recording them as warning-level validation
    issues on the derivation result. Returns the dropped-ref report.
    """
    from episteme_graph.agents.derivation_chain.schema import (
        ValidationIssue as DerivValidationIssue,
    )
    from episteme_graph.agents.id_canonicalization import (
        canonicalize_derivation_claim_refs,
    )

    dropped = canonicalize_derivation_claim_refs(derivations, claim_objects)
    for entry in dropped:
        derivations.validation_issues.append(DerivValidationIssue(
            rule_id="unresolved_claim_ref_dropped",
            severity="warning",
            message=(
                f"derivation {entry['derivation_id']!r} step {entry['step_id']!r} "
                f"dropped provisional claim ref(s) not present in claims.json: "
                f"{entry['dropped_claim_ids']}"
            ),
            field=entry["derivation_id"],
        ))
    return dropped


def _empty_derivation_chain_result(document_id: str, cartridge_id: str | None):
    from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

    return DerivationChainResult(
        document_id=document_id,
        cartridge_id=cartridge_id,
        chains=[],
        validation_issues=[],
    )


def _synthesize_equation_claims(*, equations: Any, derivations: Any, claim_objects: Any) -> list:
    """Synthesise equation/derivation-backed atomic claims (issue #388).

    Returns new ClaimObjectRecord objects to append to the claim artifact. The
    next synth index continues past any synthesised claims already present so a
    resumed run does not collide IDs.
    """
    from episteme_graph.agents.claim_object_builder.equation_claim_synthesis import (
        synthesize_equation_claims,
    )

    existing = list(getattr(claim_objects, "claims", []) or [])
    # Drop any previously synthesised claims so a resumed run rebuilds them
    # deterministically instead of duplicating, then re-synthesise from index 1.
    prose_claims = [c for c in existing if not str(getattr(c, "claim_id", "")).startswith("synth_claim_")]
    claim_objects.claims = prose_claims
    return synthesize_equation_claims(
        equations,
        derivations=derivations,
        existing_claims=prose_claims,
        start_index=1,
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


def _run_grobid_parse(pdf_bytes: bytes) -> str | None:
    """PDF bytes を GROBID に送信して TEI XML を返す。失敗時は None を返す。"""
    from core.extractor import extract_tei_xml_from_pdf_bytes
    return extract_tei_xml_from_pdf_bytes(pdf_bytes)


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
