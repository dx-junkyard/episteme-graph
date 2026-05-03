"""8 agent pipeline orchestrator (issue #226).

PDF → DocumentStructure → SourceChunking → SourceEmbedding → PaperSkeleton →
RhetoricalRole → ClaimQualification → EquationSemantics → ThesisReconstruction →
DSLLinking → DSLEmbedding → ComponentAssembly → Persist → Completed
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from .chunker import build_source_chunks
from .dsl_text import dsl_result_to_search_text
from .persistence import (
    persist_component_graph,
    persist_components,
    persist_document_embedding,
    persist_qualified_claims,
    persist_source_chunks,
    upsert_analysis_run,
)

logger = logging.getLogger(__name__)


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

    run_id = upsert_analysis_run(
        document_id=document_id,
        material_id=material_id,
        cartridge_id=cartridge_id,
        status="running",
        current_stage="save_pdf",
    )

    def report(stage: str, payload: dict | None = None) -> None:
        if progress_callback:
            try:
                progress_callback(stage, payload or {})
            except Exception:
                logger.warning("progress_callback raised", exc_info=True)
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage=stage,
            stage_outputs={stage: payload or {}},
        )

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
        report("save_pdf", {"size_bytes": len(pdf_bytes)})
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pdf", delete=False
        ) as f:
            f.write(pdf_bytes)
            pdf_path = f.name

        # ── Stage 2: document_structure ────────────────────────────────────
        try:
            ds_agent = agent_classes["DocumentStructureAgent"]() if isinstance(
                agent_classes["DocumentStructureAgent"], type
            ) else agent_classes["DocumentStructureAgent"]
            structure = ds_agent.run(pdf_path=pdf_path, cartridge_id=cartridge_id)
            structure.document_id = document_id  # 強制的に上書きして後段一貫
        except Exception as exc:
            raise PipelineStageError("document_structure", str(exc), cause=exc) from exc
        report("document_structure", {
            "block_count": len(structure.blocks),
            "section_count": len(structure.sections),
        })

        # ── Stage 3: source_chunking ───────────────────────────────────────
        try:
            source_chunks = build_source_chunks(structure)
        except Exception as exc:
            raise PipelineStageError("source_chunking", str(exc), cause=exc) from exc
        if not source_chunks:
            raise PipelineStageError(
                "source_chunking", "no source chunks produced from document structure"
            )
        report("source_chunking", {"chunk_count": len(source_chunks)})

        # ── Stage 4: source_embedding ──────────────────────────────────────
        try:
            chunk_index = persist_source_chunks(
                document_id=document_id,
                material_id=material_id,
                chunks=source_chunks,
            )
        except Exception as exc:
            raise PipelineStageError("source_embedding", str(exc), cause=exc) from exc
        result.chunk_count = len(chunk_index)
        report("source_embedding", {"saved_chunks": len(chunk_index)})

        # ── Stage 5: paper_skeleton ────────────────────────────────────────
        try:
            ps_agent = _instantiate(agent_classes["PaperSkeletonAgent"])
            skeleton = ps_agent.run(structure=structure, cartridge_id=cartridge_id)
        except Exception as exc:
            raise PipelineStageError("paper_skeleton", str(exc), cause=exc) from exc
        report("paper_skeleton", {"document_id": document_id})

        # ── Stage 6: rhetorical_role ───────────────────────────────────────
        try:
            rr_agent = _instantiate(agent_classes["RhetoricalRoleAgent"])
            roles = rr_agent.run(
                structure=structure, skeleton=skeleton, cartridge_id=cartridge_id,
            )
        except Exception as exc:
            raise PipelineStageError("rhetorical_role", str(exc), cause=exc) from exc
        report("rhetorical_role", {})

        # ── Stage 7: claim_qualification ───────────────────────────────────
        try:
            cq_agent = _instantiate(agent_classes["ClaimQualificationAgent"])
            qualified = cq_agent.run(
                structure=structure, skeleton=skeleton, roles=roles,
                cartridge_id=cartridge_id,
            )
        except Exception as exc:
            raise PipelineStageError("claim_qualification", str(exc), cause=exc) from exc
        report("claim_qualification", {
            "qualified_count": len(qualified.qualified_spans),
        })

        # ── Stage 8: equation_semantics ────────────────────────────────────
        try:
            eq_agent = _instantiate(agent_classes["EquationSemanticsAgent"])
            equations = eq_agent.run(
                structure=structure, skeleton=skeleton, roles=roles,
                cartridge_id=cartridge_id,
            )
        except Exception as exc:
            raise PipelineStageError("equation_semantics", str(exc), cause=exc) from exc
        report("equation_semantics", {})

        # ── Stage 9: thesis_reconstruction ─────────────────────────────────
        try:
            th_agent = _instantiate(agent_classes["ThesisReconstructionAgent"])
            thesis = th_agent.run(
                skeleton=skeleton, qualified_claims=qualified, equations=equations,
                cartridge_id=cartridge_id,
            )
        except Exception as exc:
            raise PipelineStageError("thesis_reconstruction", str(exc), cause=exc) from exc
        report("thesis_reconstruction", {})

        # ── Stage 10: dsl_linking ──────────────────────────────────────────
        try:
            dsl_agent = _instantiate(agent_classes["DSLLinkingAgent"])
            dsl = dsl_agent.run(
                qualified_claims=qualified, equations=equations, thesis=thesis,
            )
        except Exception as exc:
            raise PipelineStageError("dsl_linking", str(exc), cause=exc) from exc
        result.dsl_node_count = len(dsl.nodes)
        result.dsl_edge_count = len(dsl.edges)
        report("dsl_linking", {
            "nodes": len(dsl.nodes), "edges": len(dsl.edges),
        })

        # ── Stage 11: dsl_embedding ────────────────────────────────────────
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
        except Exception as exc:
            # embedding は best-effort（agent pipeline 全体の致命傷にはしない）
            logger.exception("dsl_embedding stage failed (non-fatal): %s", exc)
        report("dsl_embedding", {})

        # ── Stage 12: component_assembly ───────────────────────────────────
        try:
            ca_agent = _instantiate(agent_classes["ComponentAssemblyAgent"])
            component_result = ca_agent.run(
                qualified_claims=qualified, equations=equations,
                thesis=thesis, dsl=dsl, cartridge_id=cartridge_id,
            )
        except Exception as exc:
            raise PipelineStageError("component_assembly", str(exc), cause=exc) from exc
        result.component_count = len(component_result.components)
        report("component_assembly", {
            "components": len(component_result.components),
        })

        # ── Stage 13: persist_claims_components_graph ──────────────────────
        try:
            saved_claims = persist_qualified_claims(
                document_id=document_id,
                qualified_result=qualified,
                chunk_index=chunk_index,
            )
            result.claim_count = len(saved_claims)

            id_map = persist_components(
                document_id=document_id,
                component_result=component_result,
                course_id=course_id,
            )
            persist_component_graph(
                document_id=document_id,
                component_id_map=id_map,
                component_result=component_result,
                dsl_result=dsl,
                course_id=course_id,
            )
        except Exception as exc:
            raise PipelineStageError(
                "persist_claims_components_graph", str(exc), cause=exc
            ) from exc
        report("persist_claims_components_graph", {
            "claims": result.claim_count,
            "components": result.component_count,
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
        report("completed", {
            "chunks": result.chunk_count,
            "claims": result.claim_count,
            "components": result.component_count,
        })
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
