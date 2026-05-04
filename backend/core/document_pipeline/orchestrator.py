"""8 agent pipeline orchestrator (issue #226).

PDF → DocumentStructure → SourceChunking → SourceEmbedding → PaperSkeleton →
RhetoricalRole → ClaimQualification → EquationSemantics → ThesisReconstruction →
DSLLinking → DSLEmbedding → ComponentAssembly → Persist → Completed
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
    "thesis_reconstruction",
    "dsl_linking",
    "dsl_embedding",
    "component_assembly",
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
    from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
    from episteme_graph.agents.component_assembly.agent import ComponentAssemblyAgent
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.dsl_linking.agent import DSLLinkingAgent
    from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent
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

    agent_classes = agents or _import_agents()

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
                )
            except Exception as exc:
                logger.exception("component_assembly stage failed for document=%s material=%s", document_id, material_id)
                raise PipelineStageError("component_assembly", str(exc), cause=exc) from exc
            save_artifact("component_assembly", component_result)
        result.component_count = len(component_result.components)
        report_done("component_assembly", {
            "components": len(component_result.components), "total": 1, "processed": 1,
        })

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
    return value


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
