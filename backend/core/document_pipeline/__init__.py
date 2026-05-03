"""Document-first analysis pipeline (issue #226).

PDFアップロード後の解析を `src/episteme_graph/agents/` の8 agent群で実行する
オーケストレータと、結果を既存の Postgres スキーマに保存する adapter 群を提供する。

公開 API:
    run_document_pipeline(pdf_bytes, document_id, material_id, ...)
        → DocumentPipelineResult

stage 一覧:
    save_pdf → document_structure → source_chunking → source_embedding
    → paper_skeleton → rhetorical_role → claim_qualification → equation_semantics
    → thesis_reconstruction → dsl_linking → dsl_embedding → component_assembly
    → persist_claims_components_graph → completed
"""

from .orchestrator import (
    DocumentPipelineResult,
    PipelineStageError,
    run_document_pipeline,
)

__all__ = [
    "DocumentPipelineResult",
    "PipelineStageError",
    "run_document_pipeline",
]
