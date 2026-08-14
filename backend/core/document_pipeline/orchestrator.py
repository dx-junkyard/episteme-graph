"""8 agent pipeline orchestrator (issue #226, #266).

PDF/TeX archive → DocumentStructure → SourceChunking → SourceEmbedding → PaperSkeleton →
RhetoricalRole → ClaimQualification → EquationSemantics → ThesisReconstruction →
DSLLinking → DSLEmbedding → ComponentAssembly → ComponentGraph →
CoursMapping → Blueprint → ExportValidation → Persist → Completed

Tier 3-19 (アーキテクチャ整理): 26 ステージがかつて ``run_document_pipeline`` 1関数に
インライン展開されていた（~1,400行）。現在は ``PipelineContext``（ステージ間で受け渡す
状態を1箇所に集約するデータ構造）+ ``PipelineStageDef`` のリスト（各ステージの実行本体
への参照）+ 薄いランナーループに分解してある。挙動保存を最優先し、各ステージの
artifact/resume/report/finish_target_stage の呼び出し順序・payload・非致命/致命の
分岐は元のコードと完全に同一。ステージごとの逸脱（options ゲート・非同期側の post
処理・resume 経路の違い等）が大きいため、無理な共通テンプレート化はせず
``_stage_<name>(ctx) -> bool``（True = target_stage で停止）という「本体を1関数に
括り出す」形を全ステージで採用している（一部の between-stage 決定論的後処理は
``_hook_*`` として PIPELINE_STAGES に無い独立ステップにしてある）。
"""
from __future__ import annotations

import inspect
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

from core.llm_policy import (
    SCENE_PIPELINE,
    SCENE_PIPELINE_VISION,
    SOURCE_RUN_OVERRIDE,
    model_override,
    resolve_scene_model,
)
from core.llm_usage.context import bind_usage_context, set_current_feature
from core.llm_worker.cost_gate import CostGate, today_str

from .chunker import build_source_chunks
from .dsl_text import dsl_result_to_search_text
from .persistence import (
    _claim_legacy_keys,
    get_active_analysis_run_id,
    get_latest_analysis_run,
    set_active_analysis_run,
    load_source_chunk_index,
    delete_component_graph,
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

# ---------------------------------------------------------------------------
# M層（LLM モデル選択）Phase 2: run 単位のステージ別モデル上書き + 使用モデルの記録。
# 正本設計: docs/features/llm_model_selection_design.md §3/§8/§10 Phase 2。
#
# ``run_document_pipeline`` はスレッドを起こさず全ステージを同一スレッドで順に
# 実行する（本モジュールに Thread/asyncio 系の生成は無い）ため、
# ``core.llm_policy.model_override`` の contextvar はそのままステージ内の
# ``generate_*`` 呼び出しへ伝播する。ステージ関数の中身は変更せず、
# ``_PIPELINE_STEPS`` を回すランナーループの1箇所だけで override を張る。
# ---------------------------------------------------------------------------

STAGE_MODELS_KEY = "_stage_models"

# options.models による run override / 使用モデル記録(M7)の対象ステージ。
# 「LLM-first」と明言されている、または vision/opt-in で実行が二値に決まる
# ステージのみを対象にする。document_structure（構造優先・曖昧箇所のみ LLM 補助）・
# figure_table_semantics（caption-first・LLM enricher 任意）は、実行時に LLM 呼び出しが
# 実際にあったかどうかを外側（ループ側）から正確に判定できないため、記録対象から
# 意図的に除外する（不正確な網羅より正直な部分記録を優先する、という Phase 2 依頼の
# 指示どおり）。symbol_registry / derivation_chain / course_mapping は非LLM・決定論的
# なので単純に対象外。component_graph は上記3ステージと異なり非LLMではない——
# agents/component_graph/agent.py が自ら「hybrid deterministic/LLM edge-building
# pipeline」と明記するとおり LLM クライアントを持ち、下記 `_stage_component_graph` も
# `report_start(..., unit="llm_call")` で進捗報告している。それでもなお本セットから
# 除外されている理由を裏付ける記録は見当たらず、歴史的な扱いの可能性がある（「LLM を
# 呼ぶステージか」の判定が LLM_STAGE_NAMES / llm_usage の feature 語彙 / report_start
# の unit 指定の3箇所で食い違っている既知の不整合。
# docs/architecture/doc_review_findings_2026-08-13.md の 7-1 参照）。
LLM_STAGE_NAMES = frozenset({
    "paper_skeleton",
    "rhetorical_role",
    "claim_qualification",
    "equation_semantics",
    "apparatus_semantics",
    "thesis_reconstruction",
    "dsl_linking",
    "component_assembly",
    "narrative_annotator",
    "contextual_explanation",
    "discuss_opening",
    "landscape_placement",
})

# 後方互換エイリアス（Phase 4 で `LLM_STAGE_NAMES` へ昇格・公開。旧名を参照する
# 外部コードのための薄いエイリアスで、正本は `LLM_STAGE_NAMES`）。
_LLM_STAGE_NAMES = LLM_STAGE_NAMES


def _resolve_stage_override_model(stage_name: str | None, effective_options: dict) -> str | None:
    """``effective_options["models"]`` から stage 単位の run override モデルを引く。

    解決順: ``pipeline:{stage_name}`` → (``apparatus_semantics`` だけ
    ``pipeline.vision``、それ以外は ``pipeline``)。どちらにも一致しなければ
    None を返す（override なし = 従来どおり env/tier 既定へフォールバックする）。
    between-stage の決定論的後処理フック（``stage_name is None``）は対象外。
    """
    if not stage_name:
        return None
    models = effective_options.get("models")
    if not isinstance(models, dict):
        return None
    candidate = models.get(f"pipeline:{stage_name}")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    fallback_key = SCENE_PIPELINE_VISION if stage_name == "apparatus_semantics" else SCENE_PIPELINE
    candidate = models.get(fallback_key)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


def _stage_artifact_indicates_llm_skip(artifact_value: Any) -> bool:
    """このステージが（実行はされたが）LLM 呼び出しをしなかったことを示す既知の
    placeholder かどうかを判定する。``apparatus_semantics`` の
    ``skipped_by_option`` / ``contextual_explanation`` の ``skipped_by_limit``・
    ``llm_calls == 0``（対象要素が無い/日次上限で0回だった場合を含む）を検出する。
    """
    if isinstance(artifact_value, dict):
        if artifact_value.get("skipped_by_option") or artifact_value.get("skipped_by_limit"):
            return True
        if artifact_value.get("llm_calls") == 0:
            return True
    return False


# contextual_explanation stage (hierarchical_context_explanation_design.md §5.1):
# a single process-lifetime CostGate for the daily LLM-call budget, matching
# figure_reanalysis.py's "CostGate + resolve_model only" partial-adoption of the
# core/llm_worker/ skeleton (this stage's own agent lives in
# episteme_graph.agents.contextual_explanation and is off-limits to edit here;
# only the orchestrator-level cost gate belongs in this module).
_ctxexpl_cost_gate = CostGate()

# discuss_opening stage (discuss_opening_authoring_design.md §4.1): same
# partial-adoption of the core/llm_worker/ skeleton as contextual_explanation —
# a single process-lifetime CostGate for the daily LLM-call budget
# (``DISCUSS_OPENING_MAX_CALLS_PER_DAY``). The repair loop / JSON client live in
# the agent (episteme_graph.agents.discuss_opening), which delegates to
# core/llm_worker/ as its 8th consumer.
_discuss_opening_cost_gate = CostGate()


PIPELINE_STAGES = [
    "save_pdf",
    "grobid_parse",
    "document_structure",
    "figure_image_extraction",
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
    "apparatus_semantics",
    "thesis_reconstruction",
    "dsl_linking",
    "dsl_embedding",
    "component_assembly",
    "component_graph",
    "narrative_annotator",
    "contextual_explanation",
    "discuss_opening",
    "landscape_placement",
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


# ---------------------------------------------------------------------------
# PipelineContext: 全ステージ間で受け渡す状態を1箇所に集約する。
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """1回の ``run_document_pipeline`` 呼び出しに閉じたステージ間共有状態。

    フィールドは元のインライン実装でローカル変数として持ち回されていた値
    （structure / skeleton / roles / ... 等）と、report_start/report_done/
    save_artifact/artifact/should_use_artifact/finish_target_stage の各クロージャ
    （``run_document_pipeline`` 側で定義されたものをそのまま bind する）で構成する。
    """

    # 固定入力（構築後は不変）
    pdf_bytes: bytes
    document_id: str
    material_id: str
    filename: str | None
    source_kind: str
    cartridge_id: str | None
    course_id: str | None
    run_id: str | None
    agent_classes: dict
    effective_options: dict
    result: DocumentPipelineResult

    # クロージャ（run_document_pipeline が定義したものを構築後に bind する）
    report: Callable | None = None
    report_start: Callable | None = None
    report_item: Callable | None = None
    report_done: Callable | None = None
    save_artifact: Callable | None = None
    artifact: Callable | None = None
    should_use_artifact: Callable | None = None
    finish_target_stage: Callable | None = None
    all_artifacts: Callable | None = None

    # ステージ間で受け渡される可変状態
    pdf_path: str | None = None
    tei_xml: str | None = None
    structure: Any = None
    figure_extraction_summary: dict | None = None
    source_chunks: list | None = None
    chunk_index: Any = None
    skeleton: Any = None
    roles: Any = None
    qualified: Any = None
    equations: Any = None
    evidence: Any = None
    claim_objects: Any = None
    symbol_registry: Any = None
    derivations: Any = None
    fig_tbl: Any = None
    apparatus_result: Any = None
    thesis: Any = None
    dsl: Any = None
    component_result: Any = None
    component_graph_result: Any = None
    narrative: Any = None
    course_mapping: Any = None
    blueprint: Any = None
    validation_result_dict: dict | None = None
    skip_graph_persist: bool = False
    skip_component_persist: bool = False
    degraded_stages: list = field(default_factory=list)


@dataclass
class PipelineStageDef:
    """``_PIPELINE_STEPS`` の1要素。

    ``name`` は PIPELINE_STAGES に対応するステージ名（between-stage の決定論的
    後処理フックには対応する PIPELINE_STAGES エントリが無いため None）。``execute``
    がステージ本体で、``ctx`` を受け取り「target_stage で停止すべきか」(bool) を返す。
    """

    name: str | None
    execute: Callable[["PipelineContext"], bool]


def run_document_pipeline(
    *,
    pdf_bytes: bytes,
    document_id: str,
    material_id: str,
    filename: str | None = None,
    source_kind: str = "pdf",
    cartridge_id: str | None = None,
    course_id: str | None = None,
    user_id: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    agents: dict | None = None,
    resume: bool = True,
    target_stage: str | None = None,
    start_stage: str | None = None,
    options: dict | None = None,
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
        user_id: 任意。この実行を起こした教員の users.id。U層の帰属（``usage_context``）に
            bind され、M層のモデル解決（``core.llm_policy.resolve_scene_model`` の
            解決順③ = ``scope='user'`` のポリシー行）が参照する。未指定でも従来どおり
            動作する（user 行はスキップされ system 行 → env → tier 既定の順になる）。
        progress_callback: 各 stage 完了時に (stage_name, info_dict) で呼ばれる。
            background_tasks への進捗反映に使う。
        agents: テスト用に注入可能な agent インスタンス dict。
        options: アップロード時オプションのスナップショット（migration 041 §3-2）。
            例: ``{"analyze_images": True}``。``document_analysis_runs.options`` に
            保存され、resume 時は明示指定が無ければ前回 run の options を引き継ぐ。

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

    fetched_previous_run = resume and agents is None
    previous_run = (
        get_latest_analysis_run(document_id=document_id, material_id=material_id)
        if fetched_previous_run else None
    )
    # options（migration 041 §3-2）: 明示指定があればそれを優先し、なければ前回 run の
    # options を引き継ぐ（再解析のたびに analyze_images 等を指定しなくても前回の選択が
    # 維持される）。completed 済み run は artifact 再利用の対象から外れる（下の reset）が、
    # options の引き継ぎ元としては有効なので reset **前**にここで確定させる。
    # resume=False の全体再実行（lecture studio の document-pipeline/run 等）でも
    # options だけは最新 run から引き継ぐ — previous_run は artifact 再利用のために
    # resume 時のみ読むが、options の継承はそれとは独立の関心事（agents 注入の
    # ユニットテスト実行では DB を読まない）。
    if options is not None:
        effective_options = dict(options)
    else:
        options_source_run = previous_run
        if not fetched_previous_run and agents is None:
            options_source_run = get_latest_analysis_run(
                document_id=document_id, material_id=material_id
            )
        effective_options = dict((options_source_run or {}).get("options") or {})
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
    # M7: 前回 run の使用モデル記録を引き継ぐ（resume で artifact 再利用したステージは
    # 前回のモデル記録をそのまま保持し、実際に再実行したステージだけを後段のループが
    # 上書きする。start_index より後ろのステージは今回作り直されるので、artifact と
    # 同じ規則で古い記録を落とす — 捏造しない）。
    stage_models: dict[str, str] = dict(previous_outputs.get(STAGE_MODELS_KEY) or {})
    if start_index is not None:
        stage_models = {
            stage: value
            for stage, value in stage_models.items()
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
            options=effective_options,
        )
    else:
        run_id = upsert_analysis_run(
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage="save_pdf",
            options=effective_options,
        )

    # U層（LLM 使用量帰属, 設計書 §6）: run_id 確定直後にこのスレッドの帰属文脈を
    # bind する。以降の generate_text 等の呼び出しは report_start() が差し替える
    # "pipeline:{stage}" feature で記録される。
    #
    # user_id も併せて bind する: M層（llm_model_selection_design.md §3）の解決順③
    # （``scope='user'`` のポリシー行）は ``current_usage_context().user_id`` を見るため、
    # ここで bind しないとユーザー別のモデル既定が pipeline 実行では常に無効になる
    # （アップロード UI は run override を常送するため隠れるが、models 未指定の
    # 再解析・API 直呼びでは解決層③に到達しない）。
    bind_usage_context(
        "pipeline",
        user_id=str(user_id) if user_id else None,
        document_id=str(document_id),
        run_id=str(run_id),
        course_id=str(course_id) if course_id else None,
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
        # U層: ステージ開始イベントでのみ帰属 feature を差し替える（帰属 ID は維持）。
        set_current_feature(f"pipeline:{stage}")
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

    def all_artifacts() -> dict:
        return dict(previous_artifacts)

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

    ctx = PipelineContext(
        pdf_bytes=pdf_bytes,
        document_id=document_id,
        material_id=material_id,
        filename=filename,
        source_kind=source_kind,
        cartridge_id=cartridge_id,
        course_id=course_id,
        run_id=run_id,
        agent_classes=agent_classes,
        effective_options=effective_options,
        result=result,
    )
    ctx.report = report
    ctx.report_start = report_start
    ctx.report_item = report_item
    ctx.report_done = report_done
    ctx.save_artifact = save_artifact
    ctx.artifact = artifact
    ctx.should_use_artifact = should_use_artifact
    ctx.finish_target_stage = finish_target_stage
    ctx.all_artifacts = all_artifacts

    def _record_stage_model_if_used(stage_name: str, had_artifact_before: bool) -> None:
        # M7: 実際に実行した（artifact 再利用ではない）LLM ステージのみ、使用モデルを
        # stage_outputs["_stage_models"] に記録する。resume で artifact を再利用した
        # ステージはここに到達しない前提（had_artifact_before が真）ため、前回の記録が
        # そのまま残る。
        if stage_name not in LLM_STAGE_NAMES or had_artifact_before:
            return
        new_artifact = ctx.artifact(stage_name)
        if new_artifact is None or _stage_artifact_indicates_llm_skip(new_artifact):
            return
        try:
            resolved_model = resolve_scene_model(f"pipeline:{stage_name}").model
        except Exception:
            logger.debug("failed to resolve stage model for %s", stage_name, exc_info=True)
            return
        if not resolved_model:
            return
        stage_models[stage_name] = resolved_model
        upsert_analysis_run(
            run_id=run_id,
            document_id=document_id,
            material_id=material_id,
            cartridge_id=cartridge_id,
            status="running",
            current_stage=stage_name,
            stage_outputs={STAGE_MODELS_KEY: dict(stage_models)},
        )

    try:
        if source_kind not in {"pdf", "tex_archive"}:
            raise ValueError(f"unsupported source_kind: {source_kind}")

        for step in _PIPELINE_STEPS:
            stage_name = step.name
            override_model = _resolve_stage_override_model(stage_name, effective_options)
            had_artifact_before = stage_name is not None and ctx.artifact(stage_name) is not None

            def _run_step(_stage_name=stage_name, _had_artifact_before=had_artifact_before) -> bool:
                stopped_inner = step.execute(ctx)
                if _stage_name is not None:
                    _record_stage_model_if_used(_stage_name, _had_artifact_before)
                return stopped_inner

            if override_model:
                with model_override(override_model, source=SOURCE_RUN_OVERRIDE):
                    stopped = _run_step()
            else:
                stopped = _run_step()

            if stopped:
                return ctx.result

        _stage_completed(ctx)
        return ctx.result

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
        ctx.result.final_stage = exc.stage
        raise
    finally:
        if ctx.pdf_path:
            try:
                os.unlink(ctx.pdf_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# ステージ本体（1関数 = 1 stage）。observable な挙動（artifact resume / report_*
# の呼び出し順序・payload / 致命・非致命の分岐）は元のインライン実装と完全一致させる。
# ---------------------------------------------------------------------------


def _stage_save_pdf(ctx: PipelineContext) -> bool:
    # ── Stage 1: save_pdf (一時ファイル化。MinIO への保存は呼び出し側担当) ─
    source_suffix = ".pdf" if ctx.source_kind == "pdf" else ".tar.gz"
    ctx.report_done("save_pdf", {"size_bytes": len(ctx.pdf_bytes), "source_kind": ctx.source_kind})
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=source_suffix, delete=False
    ) as f:
        f.write(ctx.pdf_bytes)
        ctx.pdf_path = f.name
    return False


def _stage_grobid_parse(ctx: PipelineContext) -> bool:
    # ── Stage 2: grobid_parse ──────────────────────────────────────────
    grobid_artifact = ctx.artifact("grobid_parse")
    if ctx.should_use_artifact("grobid_parse"):
        ctx.tei_xml = (grobid_artifact or {}).get("tei_xml") or None
        logger.info("Resuming document pipeline: loaded grobid_parse artifact for document %s", ctx.document_id)
    elif ctx.source_kind == "tex_archive":
        ctx.save_artifact("grobid_parse", {
            "status": "skipped",
            "reason": "tex_archive",
            "tei_bytes": 0,
            "tei_xml": None,
        })
    else:
        ctx.report_start("grobid_parse", total=1, unit="document")
        try:
            ctx.tei_xml = _run_grobid_parse(ctx.pdf_bytes)
        except Exception:
            logger.warning(
                "grobid_parse failed (non-fatal); will use PyMuPDF fallback: document=%s",
                ctx.document_id,
                exc_info=True,
            )
            ctx.tei_xml = None
        ctx.save_artifact("grobid_parse", {
            "status": "ok" if ctx.tei_xml else "fallback",
            "tei_bytes": len(ctx.tei_xml.encode()) if ctx.tei_xml else 0,
            "tei_xml": ctx.tei_xml,
        })
    grobid_status = "skipped" if ctx.source_kind == "tex_archive" else ("ok" if ctx.tei_xml else "fallback")
    ctx.report_done("grobid_parse", {
        "status": grobid_status,
        "tei_bytes": len(ctx.tei_xml.encode()) if ctx.tei_xml else 0,
    })
    return ctx.finish_target_stage("grobid_parse", {"status": grobid_status})


def _stage_document_structure(ctx: PipelineContext) -> bool:
    # ── Stage 3: document_structure ────────────────────────────────────
    structure_artifact = ctx.artifact("document_structure")
    if ctx.should_use_artifact("document_structure"):
        ctx.structure = _from_agent_dict("document_structure", structure_artifact)
        logger.info("Resuming document pipeline: loaded document_structure artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("document_structure", total=1, unit="document")
        try:
            if ctx.source_kind == "tex_archive":
                ctx.structure = build_structure_from_tex_archive(
                    ctx.pdf_bytes,
                    document_id=ctx.document_id,
                    source_file=ctx.filename or ctx.pdf_path,
                    cartridge_id=ctx.cartridge_id,
                )
            else:
                ds_agent = ctx.agent_classes["DocumentStructureAgent"]() if isinstance(
                    ctx.agent_classes["DocumentStructureAgent"], type
                ) else ctx.agent_classes["DocumentStructureAgent"]
                ctx.structure = ds_agent.run(
                    pdf_path=ctx.pdf_path,
                    cartridge_id=ctx.cartridge_id,
                    tei_xml=ctx.tei_xml,
                )
            ctx.structure.document_id = ctx.document_id  # 強制的に上書きして後段一貫
        except Exception as exc:
            raise PipelineStageError("document_structure", str(exc), cause=exc) from exc
        ctx.save_artifact("document_structure", ctx.structure)
    ctx.report_done("document_structure", {
        "block_count": len(ctx.structure.blocks),
        "section_count": len(ctx.structure.sections),
    })
    ctx.structure.document_id = ctx.document_id
    if ctx.source_kind == "pdf":
        ctx.structure.source_file = ctx.pdf_path
    return ctx.finish_target_stage("document_structure", {
        "block_count": len(ctx.structure.blocks), "section_count": len(ctx.structure.sections),
    })


def _stage_figure_image_extraction(ctx: PipelineContext) -> bool:
    # ── Stage 2b: figure_image_extraction (non-LLM, deterministic, always runs) ─
    # 画像パイプライン §4: caption ブロックの分類結果 (document_structure) を使う
    # ため直後に置く。チェックボックス (options.analyze_images) に関係なく常時
    # 実行する（決定 0-4-2）。非致命: 失敗しても pipeline は継続する。
    figure_extraction_artifact = ctx.artifact("figure_image_extraction")
    if ctx.should_use_artifact("figure_image_extraction"):
        ctx.figure_extraction_summary = figure_extraction_artifact or {}
        logger.info(
            "Resuming document pipeline: loaded figure_image_extraction artifact for document %s",
            ctx.document_id,
        )
    else:
        ctx.report_start("figure_image_extraction", total=1, unit="builder")
        if ctx.source_kind != "pdf":
            ctx.figure_extraction_summary = {"skipped": True, "reason": "not_pdf"}
        else:
            try:
                from .figure_images import extract_document_figures

                ctx.figure_extraction_summary = extract_document_figures(
                    pdf_bytes=ctx.pdf_bytes,
                    document_id=ctx.document_id,
                    run_id=ctx.run_id,
                    structure=ctx.structure,
                )
            except Exception as exc:
                logger.warning(
                    "figure_image_extraction stage failed (non-fatal): document=%s material=%s error=%s",
                    ctx.document_id, ctx.material_id, exc,
                    exc_info=True,
                )
                ctx.figure_extraction_summary = {"status": "completed", "error": str(exc)}
        ctx.save_artifact("figure_image_extraction", ctx.figure_extraction_summary)
    ctx.report_done("figure_image_extraction", dict(ctx.figure_extraction_summary or {}))
    return ctx.finish_target_stage("figure_image_extraction", dict(ctx.figure_extraction_summary or {}))


def _stage_source_chunking(ctx: PipelineContext) -> bool:
    # ── Stage 3: source_chunking ───────────────────────────────────────
    source_chunks_artifact = ctx.artifact("source_chunking")
    if ctx.should_use_artifact("source_chunking"):
        ctx.source_chunks = _from_source_chunks(source_chunks_artifact)
        logger.info("Resuming document pipeline: loaded %d source chunks for document %s", len(ctx.source_chunks), ctx.document_id)
    else:
        ctx.report_start("source_chunking", total=len(ctx.structure.blocks), unit="blocks")
        try:
            ctx.source_chunks = build_source_chunks(ctx.structure)
        except Exception as exc:
            raise PipelineStageError("source_chunking", str(exc), cause=exc) from exc
        ctx.save_artifact("source_chunking", ctx.source_chunks)
    if not ctx.source_chunks:
        raise PipelineStageError(
            "source_chunking",
            "no source chunks produced from document structure",
        )
    ctx.report_done("source_chunking", {"chunk_count": len(ctx.source_chunks)})
    return ctx.finish_target_stage("source_chunking", {"chunk_count": len(ctx.source_chunks)})


def _stage_source_embedding(ctx: PipelineContext) -> bool:
    # ── Stage 4: source_embedding ──────────────────────────────────────
    if ctx.should_use_artifact("source_embedding"):
        ctx.chunk_index = load_source_chunk_index(document_id=ctx.document_id)
        logger.info("Resuming document pipeline: loaded %d persisted chunks for document %s", len(ctx.chunk_index), ctx.document_id)
    else:
        ctx.report_start("source_embedding", total=len(ctx.source_chunks), unit="chunks")
        try:
            ctx.chunk_index = persist_source_chunks(
                document_id=ctx.document_id,
                material_id=ctx.material_id,
                chunks=ctx.source_chunks,
            )
        except Exception as exc:
            raise PipelineStageError("source_embedding", str(exc), cause=exc) from exc
        ctx.save_artifact("source_embedding", {"saved_chunks": len(ctx.chunk_index)})
    ctx.result.chunk_count = len(ctx.chunk_index)
    ctx.report_done("source_embedding", {"saved_chunks": len(ctx.chunk_index), "total": len(ctx.source_chunks), "processed": len(ctx.source_chunks)})
    return ctx.finish_target_stage("source_embedding", {"saved_chunks": len(ctx.chunk_index), "total": len(ctx.source_chunks), "processed": len(ctx.source_chunks)})


def _stage_paper_skeleton(ctx: PipelineContext) -> bool:
    # ── Stage 5: paper_skeleton ────────────────────────────────────────
    skeleton_artifact = ctx.artifact("paper_skeleton")
    if ctx.should_use_artifact("paper_skeleton"):
        ctx.skeleton = _from_agent_dict("paper_skeleton", skeleton_artifact)
        logger.info("Resuming document pipeline: loaded paper_skeleton artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("paper_skeleton", total=1, unit="llm_call")
        try:
            ps_agent = _instantiate(ctx.agent_classes["PaperSkeletonAgent"])
            ctx.skeleton = ps_agent.run(structure=ctx.structure, cartridge_id=ctx.cartridge_id)
        except Exception as exc:
            logger.exception("paper_skeleton stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("paper_skeleton", str(exc), cause=exc) from exc
        ctx.save_artifact("paper_skeleton", ctx.skeleton)
    ctx.report_done("paper_skeleton", {"document_id": ctx.document_id, "total": 1, "processed": 1})
    return ctx.finish_target_stage("paper_skeleton", {"document_id": ctx.document_id, "total": 1, "processed": 1})


def _stage_rhetorical_role(ctx: PipelineContext) -> bool:
    # ── Stage 6: rhetorical_role ───────────────────────────────────────
    roles_artifact = ctx.artifact("rhetorical_role")
    if ctx.should_use_artifact("rhetorical_role"):
        ctx.roles = _from_agent_dict("rhetorical_role", roles_artifact)
        logger.info("Resuming document pipeline: loaded rhetorical_role artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("rhetorical_role", total=_agent_input_count("rhetorical_role", ctx.agent_classes, ctx.structure, ctx.skeleton, ctx.cartridge_id), unit="blocks")
        try:
            rr_agent = _instantiate(ctx.agent_classes["RhetoricalRoleAgent"])
            ctx.roles = rr_agent.run(
                structure=ctx.structure,
                skeleton=ctx.skeleton,
                cartridge_id=ctx.cartridge_id,
                progress_callback=lambda processed, total: ctx.report_item("rhetorical_role", processed, total, "blocks"),
            )
        except Exception as exc:
            logger.exception("rhetorical_role stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("rhetorical_role", str(exc), cause=exc) from exc
        ctx.save_artifact("rhetorical_role", ctx.roles)
    ctx.report_done("rhetorical_role", getattr(ctx.roles, "summary_stats", {}) or {})
    return ctx.finish_target_stage("rhetorical_role", getattr(ctx.roles, "summary_stats", {}) or {})


def _stage_claim_qualification(ctx: PipelineContext) -> bool:
    # ── Stage 7: claim_qualification ───────────────────────────────────
    qualified_artifact = ctx.artifact("claim_qualification")
    if ctx.should_use_artifact("claim_qualification"):
        ctx.qualified = _from_agent_dict("claim_qualification", qualified_artifact)
        logger.info("Resuming document pipeline: loaded claim_qualification artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("claim_qualification", total=_agent_input_count("claim_qualification", ctx.agent_classes, ctx.structure, ctx.skeleton, ctx.cartridge_id, roles=ctx.roles), unit="spans")
        try:
            cq_agent = _instantiate(ctx.agent_classes["ClaimQualificationAgent"])
            ctx.qualified = cq_agent.run(
                structure=ctx.structure, skeleton=ctx.skeleton, roles=ctx.roles,
                cartridge_id=ctx.cartridge_id,
                progress_callback=lambda processed, total: ctx.report_item("claim_qualification", processed, total, "spans"),
            )
        except Exception as exc:
            logger.exception("claim_qualification stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("claim_qualification", str(exc), cause=exc) from exc
        ctx.save_artifact("claim_qualification", ctx.qualified)
    ctx.report_done("claim_qualification", {
        "qualified_count": len(ctx.qualified.qualified_spans),
    })
    return ctx.finish_target_stage("claim_qualification", {"qualified_count": len(ctx.qualified.qualified_spans)})


def _stage_equation_semantics(ctx: PipelineContext) -> bool:
    # ── Stage 8: equation_semantics ────────────────────────────────────
    equations_artifact = ctx.artifact("equation_semantics")
    if ctx.should_use_artifact("equation_semantics"):
        ctx.equations = _from_agent_dict("equation_semantics", equations_artifact)
        logger.info("Resuming document pipeline: loaded equation_semantics artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("equation_semantics", total=_agent_input_count("equation_semantics", ctx.agent_classes, ctx.structure, ctx.skeleton, ctx.cartridge_id, roles=ctx.roles), unit="equations")
        try:
            eq_agent = _instantiate(ctx.agent_classes["EquationSemanticsAgent"])
            ctx.equations = eq_agent.run(
                structure=ctx.structure, skeleton=ctx.skeleton, roles=ctx.roles,
                cartridge_id=ctx.cartridge_id,
                progress_callback=lambda processed, total: ctx.report_item("equation_semantics", processed, total, "equations"),
            )
        except Exception as exc:
            logger.exception("equation_semantics stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("equation_semantics", str(exc), cause=exc) from exc
        ctx.save_artifact("equation_semantics", ctx.equations)
        try:
            persist_equation_previews_to_chunks(ctx.document_id, ctx.equations)
        except Exception:
            logger.warning(
                "Failed to persist equation previews into chunks for document %s",
                ctx.document_id,
                exc_info=True,
            )
    ctx.report_done("equation_semantics", {"equations": len(getattr(ctx.equations, "equations", []) or [])})
    return ctx.finish_target_stage("equation_semantics", {"equations": len(getattr(ctx.equations, "equations", []) or [])})


def _stage_evidence_registry(ctx: PipelineContext) -> bool:
    # ── Stage 8b: evidence_registry (deterministic, source-backed) ─────
    evidence_artifact = ctx.artifact("evidence_registry")
    if ctx.should_use_artifact("evidence_registry"):
        ctx.evidence = _from_agent_dict("evidence_registry", evidence_artifact)
        logger.info("Resuming document pipeline: loaded evidence_registry artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("evidence_registry", total=1, unit="builder")
        try:
            ctx.evidence = _build_evidence_registry(
                agent_classes=ctx.agent_classes,
                document_id=ctx.document_id,
                cartridge_id=ctx.cartridge_id,
                structure=ctx.structure,
                qualified=ctx.qualified,
                equations=ctx.equations,
                roles=ctx.roles,
            )
        except Exception as exc:
            logger.exception(
                "evidence_registry stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.evidence = _empty_evidence_registry(ctx.document_id, ctx.cartridge_id)
        ctx.save_artifact("evidence_registry", ctx.evidence)
    ctx.report_done("evidence_registry", {
        "records": len(getattr(ctx.evidence, "records", []) or []),
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
            structure=ctx.structure,
            evidence=ctx.evidence,
            equations=ctx.equations,
            document_id=ctx.document_id,
            save_artifact=ctx.save_artifact,
        )
    except Exception:
        logger.exception(
            "document_completeness check failed (non-fatal): document=%s", ctx.document_id
        )
    return ctx.finish_target_stage("evidence_registry", {"records": len(getattr(ctx.evidence, "records", []) or []), "total": 1, "processed": 1})


def _stage_claim_object_builder(ctx: PipelineContext) -> bool:
    # ── Stage 8c: claim_object_builder (deterministic claims.json) ─────
    claim_object_artifact = ctx.artifact("claim_object_builder")
    if ctx.should_use_artifact("claim_object_builder"):
        ctx.claim_objects = _from_agent_dict("claim_object_builder", claim_object_artifact)
        logger.info("Resuming document pipeline: loaded claim_object_builder artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("claim_object_builder", total=1, unit="builder")
        try:
            ctx.claim_objects = _build_claim_objects(
                agent_classes=ctx.agent_classes,
                document_id=ctx.document_id,
                cartridge_id=ctx.cartridge_id,
                qualified=ctx.qualified,
                equations=ctx.equations,
                evidence=ctx.evidence,
                document_structure=ctx.structure,
            )
        except Exception as exc:
            logger.exception(
                "claim_object_builder stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.claim_objects = _empty_claim_object_result(ctx.document_id, ctx.cartridge_id)
        ctx.save_artifact("claim_object_builder", ctx.claim_objects)
    ctx.report_done("claim_object_builder", {
        "claims": len(getattr(ctx.claim_objects, "claims", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("claim_object_builder", {"claims": len(getattr(ctx.claim_objects, "claims", []) or []), "total": 1, "processed": 1})


def _hook_claim_equation_canonicalization(ctx: PipelineContext) -> bool:
    """Between claim_object_builder and symbol_registry: resume に関係なく毎回実行。

    PIPELINE_STAGES には無い（report_start/finish_target_stage を持たない）決定論的な
    後処理。claim_object_builder の finish_target_stage で停止した場合はここまで到達
    しない（元のインライン実装と同じ条件）。
    """
    # ── Stage 8c.1: claim ID canonicalization contract (issue #340) ────
    # claim_object_builder is the source of truth for claim IDs. Re-map or
    # drop any provisional claim refs still carried by equation_semantics so
    # the downstream derivation_chain / component / graph / export artifacts
    # only ever reference final claim IDs (or nothing). Dropped refs are kept
    # as review warnings — we never silently retain an unresolved ref.
    try:
        eq_dropped = _canonicalize_equation_claim_links(ctx.equations, ctx.claim_objects)
        if eq_dropped:
            ctx.save_artifact("equation_semantics", ctx.equations)
            logger.info(
                "Canonicalized equation claim links for document %s: dropped provisional refs on %d equation(s)",
                ctx.document_id, len(eq_dropped),
            )
    except Exception:
        logger.warning(
            "equation claim-link canonicalization failed (non-fatal): document=%s",
            ctx.document_id, exc_info=True,
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
            ctx.claim_objects, ctx.equations
        )
        if asymmetries:
            ctx.save_artifact("claim_object_builder", ctx.claim_objects)
            ctx.save_artifact("equation_semantics", ctx.equations)
            logger.info(
                "Annotated %d one-way claim↔equation link(s) for document %s",
                asymmetries, ctx.document_id,
            )
    except Exception:
        logger.warning(
            "claim-equation link symmetry annotation failed (non-fatal): document=%s",
            ctx.document_id, exc_info=True,
        )
    return False


def _stage_symbol_registry(ctx: PipelineContext) -> bool:
    # ── Stage 8c.2: symbol_registry (deterministic from equations, #355) ─
    # Aggregates defined/used symbols into a document-wide registry and
    # annotates DefinedSymbol.symbol_id on the equations in place. Non-fatal:
    # downstream stages do not depend on it yet.
    symbol_registry_artifact = ctx.artifact("symbol_registry")
    if ctx.should_use_artifact("symbol_registry"):
        ctx.symbol_registry = _from_agent_dict("symbol_registry", symbol_registry_artifact)
        logger.info("Resuming document pipeline: loaded symbol_registry artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("symbol_registry", total=1, unit="builder")
        ctx.symbol_registry = None
        try:
            from episteme_graph.agents.symbol_registry.builder import SymbolRegistryBuilder

            ctx.symbol_registry = SymbolRegistryBuilder().run(
                ctx.equations, cartridge_id=ctx.cartridge_id
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
                    ctx.equations, ctx.symbol_registry
                )
                logger.info(
                    "equation link normalization for document %s: %s links, "
                    "%d dangling dropped, statuses=%s",
                    ctx.document_id,
                    link_summary.get("link_count"),
                    link_summary.get("dangling_dropped", 0),
                    link_summary.get("link_status_counts"),
                )
            except Exception:
                logger.warning(
                    "equation link normalization failed (non-fatal): document=%s",
                    ctx.document_id, exc_info=True,
                )
            # The builder set DefinedSymbol.symbol_id in place and the link
            # normalizer rewrote the equation links; persist the annotated
            # equations so resumes keep the registry references and links.
            ctx.save_artifact("equation_semantics", ctx.equations)
            ctx.save_artifact("symbol_registry", ctx.symbol_registry)
        except Exception as exc:
            logger.warning(
                "symbol_registry stage failed (non-fatal): document=%s error=%s",
                ctx.document_id, exc, exc_info=True,
            )
    symbol_count = len(getattr(ctx.symbol_registry, "records", []) or [])
    ctx.report_done("symbol_registry", {"symbols": symbol_count, "total": 1, "processed": 1})
    return ctx.finish_target_stage("symbol_registry", {"symbols": symbol_count, "total": 1, "processed": 1})


def _stage_derivation_chain(ctx: PipelineContext) -> bool:
    # ── Stage 8d: derivation_chain (deterministic from equation links) ─
    derivation_artifact = ctx.artifact("derivation_chain")
    if ctx.should_use_artifact("derivation_chain"):
        ctx.derivations = _from_agent_dict("derivation_chain", derivation_artifact)
        logger.info("Resuming document pipeline: loaded derivation_chain artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("derivation_chain", total=1, unit="builder")
        try:
            ctx.derivations = _build_derivation_chains(
                agent_classes=ctx.agent_classes,
                cartridge_id=ctx.cartridge_id,
                equations=ctx.equations,
                claim_objects=ctx.claim_objects,
                evidence=ctx.evidence,
            )
        except Exception as exc:
            logger.exception(
                "derivation_chain stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.derivations = _empty_derivation_chain_result(ctx.document_id, ctx.cartridge_id)
        ctx.save_artifact("derivation_chain", ctx.derivations)
    # Defensive canonicalization (issue #340): even though equations were
    # canonicalized before this stage, re-resolve every derivation step's
    # claim refs against the final claim set so no provisional claim ID can
    # reach component / graph / export from a resumed or stale artifact.
    try:
        deriv_dropped = _canonicalize_derivation_claim_refs(ctx.derivations, ctx.claim_objects)
        if deriv_dropped:
            ctx.save_artifact("derivation_chain", ctx.derivations)
            logger.info(
                "Canonicalized derivation claim refs for document %s: dropped provisional refs on %d step(s)",
                ctx.document_id, len(deriv_dropped),
            )
    except Exception:
        logger.warning(
            "derivation claim-ref canonicalization failed (non-fatal): document=%s",
            ctx.document_id, exc_info=True,
        )
    ctx.report_done("derivation_chain", {
        "chains": len(getattr(ctx.derivations, "chains", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("derivation_chain", {"chains": len(getattr(ctx.derivations, "chains", []) or []), "total": 1, "processed": 1})


def _hook_equation_claim_synthesis(ctx: PipelineContext) -> bool:
    """Between derivation_chain and figure_table_semantics: resume に関係なく毎回実行。"""
    # ── Stage 8d.1: equation/derivation claim synthesis (issue #388) ─
    # Turn source-backed equation structure and system-level derivations into
    # atomic equation_backed / derived_from_linked_artifacts claims so the
    # claim artifact is not weak when prose claims miss equation-expressed
    # propositions. Additive and non-fatal: synthesised claims are appended to
    # the claim_object_builder artifact (and to claim_objects so downstream
    # component assembly can cite them).
    try:
        synthesized = _synthesize_equation_claims(
            equations=ctx.equations, derivations=ctx.derivations, claim_objects=ctx.claim_objects,
        )
        if synthesized:
            ctx.claim_objects.claims = list(getattr(ctx.claim_objects, "claims", []) or []) + synthesized
            ctx.save_artifact("claim_object_builder", ctx.claim_objects)
            logger.info(
                "Synthesised %d equation/derivation-backed claims for document %s",
                len(synthesized), ctx.document_id,
            )
    except Exception:
        logger.warning(
            "equation claim synthesis failed (non-fatal): document=%s",
            ctx.document_id, exc_info=True,
        )
    return False


def _stage_figure_table_semantics(ctx: PipelineContext) -> bool:
    # ── Stage 8e: figure_table_semantics (caption-first deterministic) ─
    fig_tbl_artifact = ctx.artifact("figure_table_semantics")
    if ctx.should_use_artifact("figure_table_semantics"):
        ctx.fig_tbl = _from_agent_dict("figure_table_semantics", fig_tbl_artifact)
        logger.info("Resuming document pipeline: loaded figure_table_semantics artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("figure_table_semantics", total=1, unit="builder")
        try:
            ctx.fig_tbl = _build_figure_table_semantics(
                agent_classes=ctx.agent_classes,
                cartridge_id=ctx.cartridge_id,
                structure=ctx.structure,
                evidence=ctx.evidence,
                claim_objects=ctx.claim_objects,
                # span_id -> block_id resolution for the F1 claim cross-link
                # (works both freshly-run and resumed: _stage_claim_qualification
                # restores ctx.qualified from the artifact via from_dict).
                qualified=ctx.qualified,
            )
        except Exception as exc:
            logger.exception(
                "figure_table_semantics stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.fig_tbl = _empty_figure_table_result(ctx.document_id, ctx.cartridge_id)
        ctx.save_artifact("figure_table_semantics", ctx.fig_tbl)
    ctx.report_done("figure_table_semantics", {
        "figures": len(getattr(ctx.fig_tbl, "figures", []) or []),
        "tables": len(getattr(ctx.fig_tbl, "tables", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("figure_table_semantics", {"figures": len(getattr(ctx.fig_tbl, "figures", []) or []), "tables": len(getattr(ctx.fig_tbl, "tables", []) or []), "total": 1, "processed": 1})


def _stage_apparatus_semantics(ctx: PipelineContext) -> bool:
    # ── Stage 8f: apparatus_semantics (vision LLM, opt-in via options.analyze_images) ─
    # 画像パイプライン §5: FigureRecord (figure_table_semantics) と caption 意味付けを
    # 入力に使うためこの位置に置く。出力は component_assembly が下流で消費する (§5-5)。
    apparatus_artifact = ctx.artifact("apparatus_semantics")
    if ctx.should_use_artifact("apparatus_semantics"):
        ctx.apparatus_result = _from_agent_dict("apparatus_semantics", apparatus_artifact)
        if ctx.apparatus_result is None:
            # 前回 run が skipped_by_option / エラーで保存した placeholder。
            # component_assembly には渡さず、そのまま正直に報告する。
            apparatus_done_payload = dict(apparatus_artifact or {})
            apparatus_done_payload.setdefault("status", "completed")
        else:
            apparatus_done_payload = {
                "status": "completed",
                "apparatus_records": len(getattr(ctx.apparatus_result, "apparatus_records", []) or []),
            }
        logger.info(
            "Resuming document pipeline: loaded apparatus_semantics artifact for document %s",
            ctx.document_id,
        )
    else:
        ctx.report_start("apparatus_semantics", total=1, unit="builder")
        if not ctx.effective_options.get("analyze_images"):
            ctx.apparatus_result = None
            apparatus_done_payload = {"status": "completed", "skipped_by_option": True}
            ctx.save_artifact("apparatus_semantics", {"skipped_by_option": True})
        else:
            try:
                ctx.apparatus_result, apparatus_done_payload = _build_apparatus_semantics(
                    document_id=ctx.document_id,
                    cartridge_id=ctx.cartridge_id,
                    fig_tbl=ctx.fig_tbl,
                    structure=ctx.structure,
                )
                ctx.save_artifact("apparatus_semantics", ctx.apparatus_result)
            except Exception as exc:
                logger.warning(
                    "apparatus_semantics stage failed (non-fatal): document=%s material=%s error=%s",
                    ctx.document_id, ctx.material_id, exc, exc_info=True,
                )
                ctx.apparatus_result = None
                apparatus_done_payload = {"status": "completed", "error": str(exc)}
                ctx.save_artifact("apparatus_semantics", {"status": "completed", "error": str(exc)})
    ctx.report_done("apparatus_semantics", apparatus_done_payload)
    return ctx.finish_target_stage("apparatus_semantics", apparatus_done_payload)


def _stage_thesis_reconstruction(ctx: PipelineContext) -> bool:
    # ── Stage 9: thesis_reconstruction ─────────────────────────────────
    thesis_artifact = ctx.artifact("thesis_reconstruction")
    if ctx.should_use_artifact("thesis_reconstruction"):
        ctx.thesis = _from_agent_dict("thesis_reconstruction", thesis_artifact)
        logger.info("Resuming document pipeline: loaded thesis_reconstruction artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("thesis_reconstruction", total=1, unit="llm_call")
        try:
            th_agent = _instantiate(ctx.agent_classes["ThesisReconstructionAgent"])
            ctx.thesis = th_agent.run(
                skeleton=ctx.skeleton, qualified_claims=ctx.qualified, equations=ctx.equations,
                cartridge_id=ctx.cartridge_id, claim_objects=ctx.claim_objects,
            )
        except Exception as exc:
            logger.exception("thesis_reconstruction stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("thesis_reconstruction", str(exc), cause=exc) from exc
        ctx.save_artifact("thesis_reconstruction", ctx.thesis)
    ctx.report_done("thesis_reconstruction", {"total": 1, "processed": 1})
    return ctx.finish_target_stage("thesis_reconstruction", {"total": 1, "processed": 1})


def _stage_dsl_linking(ctx: PipelineContext) -> bool:
    # ── Stage 10: dsl_linking ──────────────────────────────────────────
    dsl_artifact = ctx.artifact("dsl_linking")
    if ctx.should_use_artifact("dsl_linking"):
        ctx.dsl = _from_agent_dict("dsl_linking", dsl_artifact)
        logger.info("Resuming document pipeline: loaded dsl_linking artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("dsl_linking", total=1, unit="llm_call")
        try:
            dsl_agent = _instantiate(ctx.agent_classes["DSLLinkingAgent"])
            ctx.dsl = dsl_agent.run(
                qualified_claims=ctx.qualified, equations=ctx.equations, thesis=ctx.thesis,
                claim_objects=ctx.claim_objects,
            )
        except Exception as exc:
            logger.exception("dsl_linking stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("dsl_linking", str(exc), cause=exc) from exc
        # Issue #442: cross-link the thesis artifact and the DSL graph so the
        # thesis has explicit traversal anchors and the anchor nodes carry the
        # is_thesis_anchor flag. Deterministic, non-fatal — re-save both.
        try:
            from episteme_graph.agents.thesis_reconstruction.anchor_linker import (
                link_thesis_anchors,
            )
            anchors = link_thesis_anchors(ctx.thesis, ctx.dsl)
            if anchors:
                ctx.save_artifact("thesis_reconstruction", ctx.thesis)
        except Exception as exc:
            logger.warning(
                "thesis anchor linking failed (non-fatal): document=%s error=%s",
                ctx.document_id, exc,
            )
        ctx.save_artifact("dsl_linking", ctx.dsl)
    ctx.result.dsl_node_count = len(ctx.dsl.nodes)
    ctx.result.dsl_edge_count = len(ctx.dsl.edges)
    ctx.report_done("dsl_linking", {
        "nodes": len(ctx.dsl.nodes), "edges": len(ctx.dsl.edges), "total": 1, "processed": 1,
    })
    return ctx.finish_target_stage("dsl_linking", {"nodes": len(ctx.dsl.nodes), "edges": len(ctx.dsl.edges), "total": 1, "processed": 1})


def _stage_dsl_embedding(ctx: PipelineContext) -> bool:
    # ── Stage 11: dsl_embedding ────────────────────────────────────────
    ctx.report_start("dsl_embedding", total=1, unit="embedding")
    if not ctx.should_use_artifact("dsl_embedding"):
        try:
            dsl_text = dsl_result_to_search_text(ctx.dsl, document_id=ctx.document_id)
            persist_document_embedding(
                document_id=ctx.document_id,
                material_id=ctx.material_id,
                embedding_type="dsl_graph",
                text=dsl_text,
                metadata={
                    "node_count": len(ctx.dsl.nodes),
                    "edge_count": len(ctx.dsl.edges),
                },
            )
            ctx.save_artifact("dsl_embedding", {"saved": True})
        except Exception as exc:
            # embedding は best-effort（agent pipeline 全体の致命傷にはしない）
            logger.exception("dsl_embedding stage failed (non-fatal): document=%s material=%s error=%s", ctx.document_id, ctx.material_id, exc)
    ctx.report_done("dsl_embedding", {"total": 1, "processed": 1})
    return ctx.finish_target_stage("dsl_embedding", {"total": 1, "processed": 1})


def _stage_component_assembly(ctx: PipelineContext) -> bool:
    # ── Stage 12: component_assembly ───────────────────────────────────
    component_artifact = ctx.artifact("component_assembly")
    reuse_component_artifact = ctx.should_use_artifact("component_assembly")
    if reuse_component_artifact:
        ctx.component_result = _from_agent_dict("component_assembly", component_artifact)
        reuse_component_artifact = _component_assembly_artifact_reusable(
            ctx.component_result, document_id=ctx.document_id, material_id=ctx.material_id,
        )
    if reuse_component_artifact:
        logger.info("Resuming document pipeline: loaded component_assembly artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("component_assembly", total=1, unit="llm_call")
        try:
            ca_agent = _instantiate(ctx.agent_classes["ComponentAssemblyAgent"])
            ca_kwargs: dict[str, Any] = dict(
                qualified_claims=ctx.qualified, equations=ctx.equations,
                thesis=ctx.thesis, dsl=ctx.dsl, cartridge_id=ctx.cartridge_id,
                claim_objects=ctx.claim_objects,
                evidence_registry=ctx.evidence,
                derivations=ctx.derivations,
            )
            # 画像パイプライン §5-5: apparatus_semantics の出力を装置候補として
            # 下流に渡す。別チームが並行して agent 側に同名 kwarg を実装中のため、
            # 未対応なら渡さず素通りさせる（防御的）。
            if ctx.apparatus_result is not None:
                try:
                    run_sig = inspect.signature(ca_agent.run)
                    accepts_apparatus = (
                        "apparatus_semantics" in run_sig.parameters
                        or any(
                            p.kind == inspect.Parameter.VAR_KEYWORD
                            for p in run_sig.parameters.values()
                        )
                    )
                except (TypeError, ValueError):
                    accepts_apparatus = False
                if accepts_apparatus:
                    ca_kwargs["apparatus_semantics"] = ctx.apparatus_result
                else:
                    logger.info(
                        "component_assembly: ComponentAssemblyAgent.run() does not "
                        "accept apparatus_semantics yet; skipping (document=%s)",
                        ctx.document_id,
                    )
            ctx.component_result = ca_agent.run(**ca_kwargs)
        except Exception as exc:
            logger.exception("component_assembly stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError("component_assembly", str(exc), cause=exc) from exc
        ctx.save_artifact("component_assembly", ctx.component_result)
    ctx.result.component_count = len(ctx.component_result.components)
    component_done_payload: dict[str, Any] = {
        "components": len(ctx.component_result.components), "total": 1, "processed": 1,
    }
    fallback_info = _component_assembly_fallback_info(ctx.component_result)
    if fallback_info:
        logger.warning(
            "component_assembly used deterministic fallback: document=%s material=%s "
            "fallback_reason=%r original_failure_codes=%s fallback_components=%d",
            ctx.document_id, ctx.material_id,
            fallback_info["fallback_reason"],
            fallback_info["original_failure_codes"],
            fallback_info["fallback_component_count"],
        )
        component_done_payload.update(fallback_info)
    ctx.report_done("component_assembly", component_done_payload)
    return ctx.finish_target_stage("component_assembly", component_done_payload)


def _stage_component_graph(ctx: PipelineContext) -> bool:
    # ── Stage 12a: component_graph (hybrid deterministic/LLM edge builder) ─
    component_graph_artifact = ctx.artifact("component_graph")
    if ctx.should_use_artifact("component_graph"):
        ctx.component_graph_result = _from_agent_dict("component_graph", component_graph_artifact)
        logger.info("Resuming document pipeline: loaded component_graph artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("component_graph", total=1, unit="llm_call")
        try:
            cg_agent = _instantiate(ctx.agent_classes["ComponentGraphAgent"])
            # Flatten claims for Material 4 context
            flat_claims = [
                {"claim_id": c.claim_id, "text": c.text}
                for c in (getattr(ctx.claim_objects, "claims", []) or [])
            ]
            # Flatten evidence records for Material 4 context
            flat_evidence = [
                {"evidence_id": r.evidence_id, "evidence_text": r.evidence_text}
                for r in (getattr(ctx.evidence, "records", []) or [])
            ]
            ctx.component_graph_result = cg_agent.run(
                components=ctx.component_result,
                dsl=ctx.dsl,
                derivations=ctx.derivations,
                claims=flat_claims,
                evidence_snippets=flat_evidence,
                cartridge_id=ctx.cartridge_id,
            )
        except Exception as exc:
            logger.exception(
                "component_graph stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            # フォールバック: ノードのみ、エッジなし
            from episteme_graph.agents.component_graph.schema import ComponentGraphResult
            ctx.component_graph_result = ComponentGraphResult.make_fallback(
                ctx.document_id, ctx.cartridge_id, str(exc)
            )
        ctx.save_artifact("component_graph", ctx.component_graph_result)
    # Issue #449: propagate the thesis-anchor flag onto the component graph so
    # the UI can always highlight the argument's goal nodes. Deterministic and
    # non-fatal; applied on both fresh and resumed graphs (mutates in place,
    # so the DB persist below carries the flag).
    try:
        from episteme_graph.agents.component_graph.anchor_linker import (
            link_component_thesis_anchors,
        )
        link_component_thesis_anchors(ctx.thesis, ctx.component_graph_result)
    except Exception as exc:
        logger.warning(
            "component thesis-anchor linking failed (non-fatal): document=%s error=%s",
            ctx.document_id, exc,
        )
    # Issue #451: propagate DSL edge polarity onto the component graph so the UI
    # can visualise promotion vs. inhibition. Deterministic, non-fatal.
    try:
        from episteme_graph.agents.component_graph.edge_polarity_linker import (
            link_component_edge_polarity,
        )
        link_component_edge_polarity(ctx.dsl, ctx.component_graph_result)
    except Exception as exc:
        logger.warning(
            "component edge polarity linking failed (non-fatal): document=%s error=%s",
            ctx.document_id, exc,
        )
    ctx.report_done("component_graph", {
        "nodes": len(getattr(ctx.component_graph_result, "nodes", []) or []),
        "edges": len(getattr(ctx.component_graph_result, "edges", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("component_graph", {"nodes": len(getattr(ctx.component_graph_result, "nodes", []) or []), "edges": len(getattr(ctx.component_graph_result, "edges", []) or []), "total": 1, "processed": 1})


def _stage_narrative_annotator(ctx: PipelineContext) -> bool:
    # ── Stage 12a.1: narrative_annotator (reading layer for main graph, #360) ─
    # Annotation-only LLM stage: graph_summary / narrative_role /
    # transition_text are stored as a separate artifact and never modify the
    # graph. Non-fatal: downstream stages do not depend on it.
    narrative_artifact = ctx.artifact("narrative_annotator")
    if ctx.should_use_artifact("narrative_annotator"):
        ctx.narrative = _from_agent_dict("narrative_annotator", narrative_artifact)
        logger.info("Resuming document pipeline: loaded narrative_annotator artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("narrative_annotator", total=1, unit="llm_call")
        ctx.narrative = None
        try:
            from episteme_graph.agents.narrative_annotator.agent import NarrativeAnnotator

            ctx.narrative = NarrativeAnnotator().run(
                ctx.component_graph_result,
                thesis=ctx.thesis,
                derivations=ctx.derivations,
                cartridge_id=ctx.cartridge_id,
            )
            ctx.save_artifact("narrative_annotator", ctx.narrative)
        except Exception as exc:
            logger.warning(
                "narrative_annotator stage failed (non-fatal): document=%s error=%s",
                ctx.document_id, exc, exc_info=True,
            )
    narrative_counts = {
        "node_narratives": len(getattr(ctx.narrative, "node_narratives", []) or []),
        "edge_narratives": len(getattr(ctx.narrative, "edge_narratives", []) or []),
        "total": 1,
        "processed": 1,
    }
    ctx.report_done("narrative_annotator", narrative_counts)
    return ctx.finish_target_stage("narrative_annotator", narrative_counts)


def _stage_contextual_explanation(ctx: PipelineContext) -> bool:
    # ── Stage 12a.2: contextual_explanation (Track A, hierarchical_context_
    # explanation_design.md §5.1). Placed after component_graph /
    # narrative_annotator and before course_mapping: at this point thesis /
    # derivation / symbol_registry / figure iterative_analysis have all
    # already run (E7), and equation_semantics (stage 8, much earlier) finally
    # gets a position-in-the-paper it never had before (gap (a)). Non-fatal:
    # failures here never block course_mapping / persistence.
    ctxexpl_artifact = ctx.artifact("contextual_explanation")
    if ctx.should_use_artifact("contextual_explanation"):
        ctxexpl_payload = dict(ctxexpl_artifact or {})
        logger.info(
            "Resuming document pipeline: loaded contextual_explanation artifact for document %s",
            ctx.document_id,
        )
    else:
        ctx.report_start("contextual_explanation", total=1, unit="builder")
        try:
            ctxexpl_payload = _build_contextual_explanation(
                document_id=ctx.document_id,
                cartridge_id=ctx.cartridge_id,
                component_result=ctx.component_result,
                claim_objects=ctx.claim_objects,
                equations=ctx.equations,
                fig_tbl=ctx.fig_tbl,
                apparatus_result=ctx.apparatus_result,
                thesis=ctx.thesis,
                # 指示書 §5.2 の required equation 導出に使う（material_id =
                # 既存コース snapshot の逆引きキー、derivations = 導出結果の式）。
                material_id=ctx.material_id,
                derivations=ctx.derivations,
                effective_options=ctx.effective_options,
            )
        except Exception as exc:
            logger.warning(
                "contextual_explanation stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc, exc_info=True,
            )
            ctxexpl_payload = {"status": "completed", "error": str(exc)}
        ctx.save_artifact("contextual_explanation", ctxexpl_payload)
    ctx.report_done("contextual_explanation", dict(ctxexpl_payload))
    return ctx.finish_target_stage("contextual_explanation", dict(ctxexpl_payload))


def _stage_discuss_opening(ctx: PipelineContext) -> bool:
    # ── Stage 12a.3: discuss_opening (discuss_opening_authoring_design.md §4.1).
    # Registered after contextual_explanation and before course_mapping: thesis /
    # graph / derivation / narrative / figure analysis have all run by now, so the
    # only generated ingredient the opening screen lacks (「議論のきっかけ」) can be
    # grounded in D層の未検証前提 + derivation の operation 列 + thesis の合成文.
    # Non-fatal: a failure here never blocks course_mapping / persistence.
    discuss_artifact = ctx.artifact("discuss_opening")
    if ctx.should_use_artifact("discuss_opening"):
        discuss_payload = dict(discuss_artifact or {})
        logger.info(
            "Resuming document pipeline: loaded discuss_opening artifact for document %s",
            ctx.document_id,
        )
    else:
        ctx.report_start("discuss_opening", total=1, unit="builder")
        try:
            discuss_payload = _build_discuss_opening(
                document_id=ctx.document_id,
                cartridge_id=ctx.cartridge_id,
                artifacts=ctx.all_artifacts(),
                derivations=ctx.derivations,
                equations=ctx.equations,
            )
        except Exception as exc:
            logger.warning(
                "discuss_opening stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc, exc_info=True,
            )
            discuss_payload = {"status": "completed", "error": str(exc)}
        ctx.save_artifact("discuss_opening", discuss_payload)
    ctx.report_done("discuss_opening", dict(discuss_payload))
    return ctx.finish_target_stage("discuss_opening", dict(discuss_payload))


def _stage_landscape_placement(ctx: PipelineContext) -> bool:
    # ── Stage 12a.4: landscape_placement (knowledge_landscape_design.md §7.1).
    # Registered after discuss_opening and before course_mapping: thesis /
    # claim_object_builder / paper_skeleton have all run by now, so the paper can be
    # placed onto the frozen reference maps (atlas_skeletons) with resolved text as
    # the only material. Non-fatal: a failure here never blocks course_mapping /
    # persistence, and placements are always written as `inferred` (LS3 — a teacher
    # confirms them later).
    landscape_artifact = ctx.artifact("landscape_placement")
    if ctx.should_use_artifact("landscape_placement"):
        landscape_payload = dict(landscape_artifact or {})
        logger.info(
            "Resuming document pipeline: loaded landscape_placement artifact for document %s",
            ctx.document_id,
        )
    else:
        ctx.report_start("landscape_placement", total=1, unit="builder")
        try:
            landscape_payload = _build_landscape_placement(ctx)
        except Exception as exc:
            logger.warning(
                "landscape_placement stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc, exc_info=True,
            )
            landscape_payload = {"status": "completed", "error": str(exc)}
        ctx.save_artifact("landscape_placement", landscape_payload)
    ctx.report_done("landscape_placement", dict(landscape_payload))
    return ctx.finish_target_stage("landscape_placement", dict(landscape_payload))


def _stage_course_mapping(ctx: PipelineContext) -> bool:
    # ── Stage 12b: course_mapping (deterministic component → topic map) ─
    course_mapping_artifact = ctx.artifact("course_mapping")
    if ctx.should_use_artifact("course_mapping"):
        ctx.course_mapping = _from_agent_dict("course_mapping", course_mapping_artifact)
        logger.info("Resuming document pipeline: loaded course_mapping artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("course_mapping", total=1, unit="builder")
        try:
            ctx.course_mapping = _build_course_mapping(
                agent_classes=ctx.agent_classes,
                document_id=ctx.document_id,
                cartridge_id=ctx.cartridge_id,
                component_result=ctx.component_result,
                claim_objects=ctx.claim_objects,
            )
        except Exception as exc:
            logger.exception(
                "course_mapping stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.course_mapping = _empty_course_mapping_result(ctx.document_id, ctx.cartridge_id)
        ctx.save_artifact("course_mapping", ctx.course_mapping)
    ctx.report_done("course_mapping", {
        "topics": len(getattr(ctx.course_mapping, "topics", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("course_mapping", {"topics": len(getattr(ctx.course_mapping, "topics", []) or []), "total": 1, "processed": 1})


def _stage_blueprint(ctx: PipelineContext) -> bool:
    # ── Stage 12c: blueprint (narrative arc) ───────────────────────────
    blueprint_artifact_data = ctx.artifact("blueprint")
    if ctx.should_use_artifact("blueprint"):
        ctx.blueprint = _from_agent_dict("blueprint", blueprint_artifact_data)
        logger.info("Resuming document pipeline: loaded blueprint artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("blueprint", total=1, unit="builder")
        try:
            ctx.blueprint = _build_blueprint(
                agent_classes=ctx.agent_classes,
                course_mapping=ctx.course_mapping,
                component_result=ctx.component_result,
                course_id=ctx.course_id,
            )
        except Exception as exc:
            logger.exception(
                "blueprint stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.blueprint = _empty_blueprint_result(ctx.document_id, ctx.course_id)
        ctx.save_artifact("blueprint", ctx.blueprint)
    ctx.report_done("blueprint", {
        "steps": len(getattr(ctx.blueprint, "narrative_arc", []) or []),
        "total": 1,
        "processed": 1,
    })
    return ctx.finish_target_stage("blueprint", {"steps": len(getattr(ctx.blueprint, "narrative_arc", []) or []), "total": 1, "processed": 1})


def _stage_export_validation(ctx: PipelineContext) -> bool:
    # ── Stage 12d: export_validation ───────────────────────────────────
    export_validation_artifact = ctx.artifact("export_validation")
    if ctx.should_use_artifact("export_validation"):
        ctx.validation_result_dict = export_validation_artifact
        logger.info("Resuming document pipeline: loaded export_validation artifact for document %s", ctx.document_id)
    else:
        ctx.report_start("export_validation", total=1, unit="gate")
        try:
            from .export_validation_gate import ExportValidationGate

            gate = ExportValidationGate()
            validation_result = gate.run(
                artifacts=ctx.all_artifacts(),
                component_result=ctx.component_result,
                course_mapping=ctx.course_mapping,
                claim_objects=ctx.claim_objects,
                evidence=ctx.evidence,
                dsl=ctx.dsl,
            )
            ctx.validation_result_dict = validation_result.to_dict()
        except Exception as exc:
            logger.exception(
                "export_validation stage failed (non-fatal): document=%s material=%s error=%s",
                ctx.document_id, ctx.material_id, exc,
            )
            ctx.validation_result_dict = {
                "status": "passed_with_warnings",
                "exportable": True,
                "publish_ready": False,
                "errors": [],
                "warnings": [{"code": "GATE_ERROR", "message": str(exc), "artifact": "export_validation"}],
                "review_items": [],
                "summary": {"error_count": 0, "warning_count": 1, "review_required_count": 0},
            }
        ctx.save_artifact("export_validation", ctx.validation_result_dict)
    # Keep the gate verdict under `gate_status` (passed / passed_with_warnings
    # / needs_review / failed_validation) and let report_done/finish_target_stage
    # set `status="completed"`, so the UI stage mark reflects "this stage ran"
    # rather than mis-mapping the verdict string to a blank "not_started" dot.
    export_validation_payload = {
        "gate_status": ctx.validation_result_dict.get("status"),
        "error_count": (ctx.validation_result_dict.get("summary") or {}).get("error_count", 0),
        "warning_count": (ctx.validation_result_dict.get("summary") or {}).get("warning_count", 0),
        "total": 1,
        "processed": 1,
    }
    ctx.report_done("export_validation", export_validation_payload)
    return ctx.finish_target_stage("export_validation", {"status": "completed", **export_validation_payload})


# Export-validation failure: graceful degradation ─────────────────
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


def _compute_persist_degradation_flags(ctx: PipelineContext) -> None:
    """export_validation の結果から persist スキップフラグを計算する（#3ステージ間の非常時後処理）。

    元の実装では export_validation の finish_target_stage チェック直後・
    persist_claims_components_graph の report_start より前に無条件（resume 有無に
    関係なく）実行されていた。ここではその到達条件を維持するため
    ``_stage_persist_claims_components_graph`` の先頭で呼ぶ。
    """
    ctx.skip_graph_persist = False
    ctx.skip_component_persist = False
    ctx.degraded_stages = []
    if ctx.validation_result_dict.get("status") == "failed_validation":
        all_val_errors = ctx.validation_result_dict.get("errors") or []
        component_skip_errors = [e for e in all_val_errors if e.get("code") in _COMPONENT_SKIP_CODES]
        graph_errors = [e for e in all_val_errors if e.get("artifact") == "component_graph"]

        if component_skip_errors:
            # components も graph も保存しない: claims だけ保存して completed
            ctx.skip_component_persist = True
            ctx.skip_graph_persist = True
            ctx.degraded_stages = ["component_assembly", "component_graph"]
            logger.warning(
                "ExportValidationGate: コンポーネント生成不可 (%d error(s)) — "
                "claims のみ保存して completed に移行: document=%s material=%s",
                len(all_val_errors), ctx.document_id, ctx.material_id,
            )
        elif graph_errors:
            # graph のみスキップ、claims + components は保存
            ctx.skip_graph_persist = True
            ctx.degraded_stages = ["component_graph"]
            logger.warning(
                "ExportValidationGate: component_graph エラー (%d) — "
                "graph のみスキップして completed に移行: document=%s material=%s",
                len(graph_errors), ctx.document_id, ctx.material_id,
            )
        else:
            # その他の品質エラー: 全部保存して review_required フラグを残す
            ctx.degraded_stages = []
            logger.warning(
                "ExportValidationGate: 品質エラー (%d) — "
                "全アーティファクトを保存して completed に移行: document=%s material=%s",
                len(all_val_errors), ctx.document_id, ctx.material_id,
            )


def _stage_persist_claims_components_graph(ctx: PipelineContext) -> bool:
    _compute_persist_degradation_flags(ctx)
    # ── Stage 13: persist_claims_components_graph ──────────────────────
    ctx.report_start("persist_claims_components_graph", total=3, unit="tables")
    if ctx.should_use_artifact("persist_claims_components_graph"):
        persisted = ctx.artifact("persist_claims_components_graph") or {}
        ctx.result.claim_count = int(persisted.get("claims") or 0)
    else:
        try:
            saved_claims = persist_qualified_claims(
                document_id=ctx.document_id,
                qualified_result=ctx.qualified,
                chunk_index=ctx.chunk_index,
                thesis_result=ctx.thesis,
            )
            claim_id_map: dict[str, str] = {}
            for saved in saved_claims:
                for key in _claim_legacy_keys(saved):
                    claim_id_map[key] = saved["claim_id"]
            ctx.result.claim_count = len(saved_claims)
            ctx.report_item("persist_claims_components_graph", 1, 3, "tables")

            id_map: dict[str, str] = {}
            if ctx.skip_component_persist:
                logger.warning(
                    "components persist skipped (validation errors): document=%s",
                    ctx.document_id,
                )
            else:
                id_map = persist_components(
                    document_id=ctx.document_id,
                    component_result=ctx.component_result,
                    course_id=ctx.course_id,
                    claim_id_map=claim_id_map,
                )
            ctx.report_item("persist_claims_components_graph", 2, 3, "tables")

            if ctx.skip_graph_persist or ctx.skip_component_persist:
                logger.warning(
                    "component_graph persist skipped (validation errors): document=%s",
                    ctx.document_id,
                )
                # components を作り直した（新UUID）のに graph 保存だけスキップすると、
                # 旧UUIDを指す古い theory_component_graphs 行が残り、context_lens が
                # component の上位/下位を一切引けなくなる（古いグラフにノードが在るため
                # 「グラフ未保存」注記すら出ない stale 状態）。この不整合が生じるのは
                # 「components は再persist・graph はスキップ」の組み合わせのときだけなので、
                # その場合に限り古いグラフ行を明示削除して整合させる。
                # skip_component_persist（= components も未更新 → 旧 components + 旧 graph で
                # 整合済み）のときは触らない。
                if ctx.skip_graph_persist and not ctx.skip_component_persist:
                    delete_component_graph(ctx.document_id)
            else:
                persist_component_graph(
                    document_id=ctx.document_id,
                    component_id_map=id_map,
                    component_result=ctx.component_result,
                    dsl_result=ctx.dsl,
                    course_id=ctx.course_id,
                    component_graph_result=ctx.component_graph_result,
                    claim_id_map=claim_id_map,
                    narrative_result=ctx.narrative,
                )
            ctx.save_artifact("persist_claims_components_graph", {
                "claims": ctx.result.claim_count,
                "components": ctx.result.component_count,
                "graph_skipped": ctx.skip_graph_persist or ctx.skip_component_persist,
                "components_skipped": ctx.skip_component_persist,
                "degraded_stages": ctx.degraded_stages,
            })
        except Exception as exc:
            logger.exception("persist_claims_components_graph stage failed for document=%s material=%s", ctx.document_id, ctx.material_id)
            raise PipelineStageError(
                "persist_claims_components_graph", str(exc), cause=exc
            ) from exc
    ctx.report_done("persist_claims_components_graph", {
        "claims": ctx.result.claim_count,
        "components": ctx.result.component_count,
        "total": 3,
        "processed": 3,
    })
    return ctx.finish_target_stage("persist_claims_components_graph", {"claims": ctx.result.claim_count, "components": ctx.result.component_count, "total": 3, "processed": 3})


def _stage_completed(ctx: PipelineContext) -> None:
    # ── Stage 14: completed ────────────────────────────────────────────
    upsert_analysis_run(
        run_id=ctx.run_id,
        document_id=ctx.document_id,
        material_id=ctx.material_id,
        cartridge_id=ctx.cartridge_id,
        status="completed",
        current_stage="completed",
        stage_outputs={"completed": {
            "chunks": ctx.result.chunk_count,
            "claims": ctx.result.claim_count,
            "components": ctx.result.component_count,
            "dsl_nodes": ctx.result.dsl_node_count,
            "dsl_edges": ctx.result.dsl_edge_count,
        }},
    )
    # 初回 (initial) pipeline 完了時は、この Run を採用 (active) Run とする。
    # 再解析でも最新の completed initial run を active に進める（従来の
    # 「latest = 参照対象」挙動を維持）。revision Run はこの経路を通らず、
    # accept API でのみ optimistic に active を切り替える (#402)。
    # active pointer は best-effort: 失敗しても pipeline 自体は成功扱いにする。
    try:
        set_active_analysis_run(
            document_id=ctx.document_id,
            run_id=ctx.run_id,
            expected_run_id=get_active_analysis_run_id(document_id=ctx.document_id),
        )
    except Exception:
        logger.warning(
            "failed to set active analysis run for document=%s run=%s",
            ctx.document_id, ctx.run_id, exc_info=True,
        )
    ctx.result.final_stage = "completed"
    # D層 (D1-2): A層パイプライン完了後の後処理として認識的地位台帳を
    # 決定論的にバックフィルする（読むだけ・best-effort・失敗しても pipeline は成功扱い）。
    try:
        from core.doubt.ledger_builder import backfill_document_ledger

        backfill_document_ledger(document_id=ctx.document_id, course_id=ctx.course_id or "")
    except Exception:
        logger.warning(
            "epistemic ledger backfill skipped for document=%s", ctx.document_id, exc_info=True
        )
    # D層 (D2-1): 負荷度の再計算（非LLM・決定論的, best-effort）。
    try:
        from core.doubt.load_calculator import recompute_load_scores

        recompute_load_scores(document_id=ctx.document_id)
    except Exception:
        logger.warning(
            "load score recompute skipped for document=%s", ctx.document_id, exc_info=True
        )
    # D層 (D1-4): 検証スコープ候補の非同期 LLM 補助（P6: 同期パスに LLM を入れない）。
    try:
        from core.doubt.scope_candidates.worker import maybe_schedule_scope_candidates

        maybe_schedule_scope_candidates(document_id=ctx.document_id)
    except Exception:
        logger.warning(
            "scope candidate scheduling skipped for document=%s", ctx.document_id, exc_info=True
        )
    ctx.report_done("completed", {
        "chunks": ctx.result.chunk_count,
        "claims": ctx.result.claim_count,
        "components": ctx.result.component_count,
    }, run_status="completed")


# ---------------------------------------------------------------------------
# ステージ実行順序の正本。PIPELINE_STAGES と同じ順序（+ between-stage の決定論的
# 後処理フック。name=None で PIPELINE_STAGES に対応エントリが無いことを示す）。
# ---------------------------------------------------------------------------
_PIPELINE_STEPS: list[PipelineStageDef] = [
    PipelineStageDef("save_pdf", _stage_save_pdf),
    PipelineStageDef("grobid_parse", _stage_grobid_parse),
    PipelineStageDef("document_structure", _stage_document_structure),
    PipelineStageDef("figure_image_extraction", _stage_figure_image_extraction),
    PipelineStageDef("source_chunking", _stage_source_chunking),
    PipelineStageDef("source_embedding", _stage_source_embedding),
    PipelineStageDef("paper_skeleton", _stage_paper_skeleton),
    PipelineStageDef("rhetorical_role", _stage_rhetorical_role),
    PipelineStageDef("claim_qualification", _stage_claim_qualification),
    PipelineStageDef("equation_semantics", _stage_equation_semantics),
    PipelineStageDef("evidence_registry", _stage_evidence_registry),
    PipelineStageDef("claim_object_builder", _stage_claim_object_builder),
    PipelineStageDef(None, _hook_claim_equation_canonicalization),
    PipelineStageDef("symbol_registry", _stage_symbol_registry),
    PipelineStageDef("derivation_chain", _stage_derivation_chain),
    PipelineStageDef(None, _hook_equation_claim_synthesis),
    PipelineStageDef("figure_table_semantics", _stage_figure_table_semantics),
    PipelineStageDef("apparatus_semantics", _stage_apparatus_semantics),
    PipelineStageDef("thesis_reconstruction", _stage_thesis_reconstruction),
    PipelineStageDef("dsl_linking", _stage_dsl_linking),
    PipelineStageDef("dsl_embedding", _stage_dsl_embedding),
    PipelineStageDef("component_assembly", _stage_component_assembly),
    PipelineStageDef("component_graph", _stage_component_graph),
    PipelineStageDef("narrative_annotator", _stage_narrative_annotator),
    PipelineStageDef("contextual_explanation", _stage_contextual_explanation),
    PipelineStageDef("discuss_opening", _stage_discuss_opening),
    PipelineStageDef("landscape_placement", _stage_landscape_placement),
    PipelineStageDef("course_mapping", _stage_course_mapping),
    PipelineStageDef("blueprint", _stage_blueprint),
    PipelineStageDef("export_validation", _stage_export_validation),
    PipelineStageDef("persist_claims_components_graph", _stage_persist_claims_components_graph),
]


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
    if stage == "apparatus_semantics":
        # 画像パイプライン §5: skipped_by_option / エラー時は素の placeholder dict
        # (document_id を持たない) を artifact に保存しているため、正規の
        # ApparatusSemanticsResult とは区別して None を返す（呼び出し側が
        # component_assembly へ渡さないよう判定できるようにする）。
        if not isinstance(value, dict) or "document_id" not in value or "apparatus_records" not in value:
            return None
        from episteme_graph.agents.apparatus_semantics.schema import ApparatusSemanticsResult
        return ApparatusSemanticsResult.from_dict(value)
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
    qualified: Any = None,
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

    # claim_link_index: block_id -> [claim_id, ...] (F1 cross-link contract).
    #
    # The agent's mention cross-link pass (figure_table_semantics/crosslink.py)
    # looks this index up with *block ids*, but ClaimObjectRecord only stores
    # rhetorical-role span ids ("span_001"-style) in source_span_ids. Those span
    # ids are generated per block and routinely repeat across blocks, so two
    # joins back to the source block are used here:
    #   (1) claim.source_evidence_ids -> evidence record's block_id
    #       (unambiguous: evidence ids are unique and block-scoped, and the
    #       builder resolves a claim's evidence strictly from its own block);
    #   (2) span_id -> block_id via claim_qualification's QualifiedSpanRecord,
    #       used only when the span id maps to exactly one block.
    # A span id that is unknown or ambiguous (maps to multiple blocks) keeps its
    # raw span_id key so no information is dropped (P4); such keys simply never
    # match a body-paragraph block id in the cross-link pass.
    span_to_blocks: dict[str, set[str]] = {}
    for span in getattr(qualified, "qualified_spans", []) or []:
        span_id = getattr(span, "span_id", None)
        block_id = getattr(span, "block_id", None)
        if span_id and block_id:
            span_to_blocks.setdefault(span_id, set()).add(block_id)

    evidence_block_index: dict[str, str] = {}
    for block_id, ev_ids in evidence_index.items():
        for ev_id in ev_ids:
            evidence_block_index.setdefault(ev_id, block_id)

    claim_link_index: dict[str, list[str]] = {}

    def _index_claim(key: str, claim_id: str) -> None:
        bucket = claim_link_index.setdefault(key, [])
        if claim_id not in bucket:
            bucket.append(claim_id)

    for claim in getattr(claim_objects, "claims", []) or []:
        claim_id = getattr(claim, "claim_id", None)
        if not claim_id:
            continue
        for ev_id in getattr(claim, "source_evidence_ids", []) or []:
            block_id = evidence_block_index.get(ev_id)
            if block_id:
                _index_claim(block_id, claim_id)
        for span_id in getattr(claim, "source_span_ids", []) or []:
            blocks = span_to_blocks.get(span_id)
            if blocks and len(blocks) == 1:
                _index_claim(next(iter(blocks)), claim_id)
            else:
                _index_claim(span_id, claim_id)

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


def _apparatus_daily_remaining(settings: Any) -> int:
    """画像パイプライン §10: vision 呼び出しの日次上限に対する残数を概算する。

    新テーブルは作らず、当日 (UTC) に開始した run の
    ``stage_outputs.apparatus_semantics.vision_calls`` を合算する方式（design doc
    §5-5 dev note）。クエリ失敗は非致命（上限を守る側に倒し、既定は「消費なし」
    として続行する。過大な vision コールを防ぎたいので、失敗時は保守的に上限を
    使い切ったとみなさず 0 に倒すのではなく、設定上限をそのまま返す。
    """
    limit = max(0, int(getattr(settings, "apparatus_max_calls_per_day", 30) or 0))
    try:
        from sqlalchemy import text as sa_text

        from core.postgres import get_session as _pg_session

        session = _pg_session()
        try:
            row = session.execute(
                sa_text(
                    """
                    SELECT COALESCE(SUM((stage_outputs#>>'{apparatus_semantics,vision_calls}')::int), 0)
                    FROM document_analysis_runs
                    WHERE started_at >= date_trunc('day', now())
                    """
                )
            ).fetchone()
            used = int(row[0] or 0) if row else 0
        finally:
            session.close()
    except Exception:
        logger.warning("apparatus_semantics: daily usage query failed (non-fatal)", exc_info=True)
        used = 0
    return max(0, limit - used)


def _build_apparatus_semantics(
    *,
    document_id: str,
    cartridge_id: str | None,
    fig_tbl: Any,
    structure: Any = None,
) -> tuple[Any, dict]:
    """apparatus_semantics ステージ本体（画像パイプライン §5-3/5-5）。

    document_figures（figure_image_extraction の成果）を読み、MinIO から画像
    bytes を取得し、ライブラリ retrieval（凍結版のみ、§6-5）で few-shot 候補を
    注入して ApparatusSemanticsAgent を実行する。上限（1 document あたり /
    日次）を超えた図は agent に渡さず ``skipped_by_limit`` として記録する (P4)。
    ``structure``（document_structure の成果）から ``collect_figure_context``
    で図ごとの周辺本文・略語辞書を収集し agent 入力に配線する（G1 ギャップ解消）。

    Returns: (ApparatusSemanticsResult, done_payload dict)
    """
    from core.config import get_settings
    from core.storage import get_storage_client
    from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
    from episteme_graph.agents.apparatus_semantics.schema import (
        FigureImageInput,
        IterativeConfig,
        LibraryCandidate,
    )

    from .figure_context import collect_figure_context
    from .figure_images import load_document_figures

    settings = get_settings()
    figure_rows = [
        row for row in load_document_figures(document_id)
        if row.get("status") == "extracted"
    ]

    storage = get_storage_client()

    # figure_table_semantics の FigureRecord を figure_key / caption_block_id で
    # 対応付ける（一致しなければ figure_record=None のまま渡す。§5-2）。
    fig_record_by_key: dict[str, dict] = {}
    fig_record_by_caption_block: dict[str, dict] = {}
    for fig in getattr(fig_tbl, "figures", []) or []:
        fig_dict = _to_plain_data(fig)
        fid = str(fig_dict.get("figure_id") or "")
        if fid:
            fig_record_by_key[fid] = fig_dict
        loc = fig_dict.get("source_location") or {}
        cbid = str(loc.get("caption_block_id") or "")
        if cbid:
            fig_record_by_caption_block[cbid] = fig_dict

    max_images = max(0, int(getattr(settings, "apparatus_max_images_per_document", 20) or 0))
    daily_remaining = _apparatus_daily_remaining(settings)

    # 反復照合パイプライン（#499）: iterative モードは engine が
    # hypothesis(非vision) → observation(vision) → alignment(非vision) →
    # verification(vision)×N という複数コールを1図に費やすため、事前フィルタも
    # 図あたり `1 + max_iterations` の保守的な見積りコストで許容図数を絞る
    # （daily_remaining >= 1 なら最低1図は許可し、0図に縮退させない）。
    analysis_mode = str(getattr(settings, "apparatus_analysis_mode", "iterative") or "iterative")
    iterative_enabled = analysis_mode != "one_shot"
    verify_max_iterations = max(0, int(getattr(settings, "apparatus_verify_max_iterations", 3) or 0))
    # ``model_name`` は audit-record 用のヒントで、実際の生成モデル解決は
    # llm_client 側（``ApparatusSemanticsLLMClient`` → ``core.llm.py`` の
    # ``resolve_scene_model``）が行う（schema.py の ``IterativeConfig.model_name``
    # docstring 参照）。ここで ``resolve_scene_model`` を直接呼ぶことで、
    # run override（``options.models["pipeline.vision"]`` 等。ランナーループが
    # 張る ``model_override`` contextvar 経由で反映される）を含めた実際の解決結果を
    # 監査記録に正しく反映する（旧実装は env 設定値の素読みで、run override は
    # 反映されず、env 未設定時は空文字のまま記録されていた）。
    iterative_config = IterativeConfig(
        enabled=iterative_enabled,
        max_iterations=verify_max_iterations,
        vision_call_budget=daily_remaining,
        model_name=resolve_scene_model("pipeline:apparatus_semantics").model,
    )
    if iterative_enabled:
        cost_per_figure = max(1, 1 + verify_max_iterations)
        allowed_by_budget = daily_remaining // cost_per_figure
        if allowed_by_budget <= 0 and daily_remaining >= 1:
            allowed_by_budget = 1
    else:
        allowed_by_budget = daily_remaining
    allowed_images = min(max_images, max(0, allowed_by_budget))

    figure_inputs: list[FigureImageInput] = []
    skipped_by_limit: list[str] = []
    context_collected = 0
    for idx, row in enumerate(figure_rows):
        figure_key = str(row.get("figure_key") or "")
        if idx >= allowed_images:
            skipped_by_limit.append(figure_key)
            continue

        image_bytes = None
        minio_key = row.get("minio_key")
        if minio_key:
            try:
                image_bytes = storage.get_object("figure-images", minio_key)
            except Exception:
                logger.warning(
                    "apparatus_semantics: failed to load figure image document=%s figure=%s",
                    document_id, figure_key, exc_info=True,
                )
                image_bytes = None

        fig_record = (
            fig_record_by_caption_block.get(str(row.get("caption_block_id") or ""))
            or fig_record_by_key.get(figure_key)
        )
        caption_text = str(row.get("caption_text") or "")
        inner_labels = row.get("inner_labels") or []

        try:
            fig_context = collect_figure_context(
                structure, row,
                inner_labels=inner_labels,
                max_items=settings.apparatus_context_max_items,
                max_chars=settings.apparatus_context_max_chars,
            )
        except Exception:
            logger.warning(
                "apparatus_semantics: figure context collection failed document=%s figure=%s",
                document_id, figure_key, exc_info=True,
            )
            fig_context = None
        if fig_context and fig_context.nearby_text:
            context_collected += 1

        figure_inputs.append(FigureImageInput(
            figure_id=str(row.get("id") or figure_key),
            figure_key=figure_key,
            figure_label=row.get("figure_label"),
            caption_text=caption_text,
            image_bytes=image_bytes,
            nearby_text=(fig_context.nearby_text if fig_context else []),
            figure_record=fig_record,
            inner_labels=inner_labels,
            abbreviations=(fig_context.abbreviations if fig_context else {}),
        ))

    # ライブラリ retrieval（凍結版のみ、§5-3/6-5）: cartridge が無ければ候補なしに
    # 縮退する（原則 5）。retrieval 失敗も非致命（search_frozen_entries は例外を
    # 投げず空リストを返す契約）。
    library_candidates: dict[str, list[LibraryCandidate]] = {}
    if cartridge_id:
        try:
            from core.library.search import search_frozen_entries
        except Exception:
            search_frozen_entries = None  # type: ignore[assignment]
        if search_frozen_entries is not None:
            for fig_input in figure_inputs:
                query_text = " ".join(
                    t for t in [
                        fig_input.caption_text,
                        *(fig_input.nearby_text or []),
                        *sorted((fig_input.abbreviations or {}).values()),
                    ] if t
                ).strip()
                if not query_text:
                    continue
                try:
                    hits = search_frozen_entries(
                        domain_key=cartridge_id,
                        query_text=query_text,
                        top_k=settings.apparatus_retrieval_top_k,
                    )
                except Exception:
                    logger.warning(
                        "apparatus_semantics: library retrieval failed document=%s figure=%s",
                        document_id, fig_input.figure_id, exc_info=True,
                    )
                    hits = []
                if hits:
                    library_candidates[fig_input.figure_id] = [
                        LibraryCandidate(
                            entry_id=str(h.get("entry_id") or ""),
                            version_no=int(h.get("version_no") or 0),
                            name=str(h.get("name") or ""),
                            aliases=list(h.get("aliases") or []),
                            summary=str(h.get("summary") or ""),
                            body=dict(h.get("body") or {}),
                        )
                        for h in hits
                    ]

    agent = ApparatusSemanticsAgent(cartridge_id=cartridge_id, iterative_config=iterative_config)
    result = agent.run(
        document_id=document_id,
        figures=figure_inputs,
        library_candidates=library_candidates,
        cartridge_id=cartridge_id,
    )

    # vision_calls は実測値: iterative_analysis を持つ record はその
    # vision_calls を合算し、持たない record（one_shot 経路 / iterative 無効）は
    # 従来どおり image_bytes 有無で1とみなす（設計書「コスト制御」節）。
    records = getattr(result, "apparatus_records", []) or []
    figure_input_by_id = {fi.figure_id: fi for fi in figure_inputs}
    _convergence_keys = (
        "converged", "max_iterations_reached", "no_progress",
        "aborted_error", "aborted_cost_limit",
    )
    convergence_counts = {key: 0 for key in _convergence_keys}
    vision_calls = 0
    for record in records:
        iterative = getattr(record, "iterative_analysis", None)
        if iterative is not None:
            vision_calls += int(getattr(iterative, "vision_calls", 0) or 0)
            status = getattr(iterative, "convergence_status", None)
            if status in convergence_counts:
                convergence_counts[status] += 1
        else:
            fig_input = figure_input_by_id.get(getattr(record, "figure_id", None))
            if fig_input is not None and fig_input.image_bytes:
                vision_calls += 1

    # #496: persist the generic vision classification/profile separately from
    # any teacher override.  Artifact persistence remains the source for old
    # runs; a DB write failure is therefore fail-soft and must not discard the
    # completed analysis result.
    try:
        from core.figure_presentation import persist_suggestions

        persist_suggestions(document_id, getattr(result, "apparatus_records", []) or [])
    except Exception:
        logger.warning(
            "apparatus_semantics: failed to persist figure presentation suggestions "
            "document=%s",
            document_id,
            exc_info=True,
        )

    seen_versions: set[tuple[str, int]] = set()
    referenced_versions: list[dict] = []
    for candidates in library_candidates.values():
        for c in candidates:
            key = (c.entry_id, c.version_no)
            if key in seen_versions:
                continue
            seen_versions.add(key)
            referenced_versions.append({"entry_id": c.entry_id, "version_no": c.version_no})

    done_payload = {
        "status": "completed",
        "apparatus_records": len(records),
        "vision_calls": vision_calls,
        "skipped_by_limit": skipped_by_limit,
        "referenced_library_versions": referenced_versions,
        "context_collected": context_collected,
        "presentation_modes": {
            mode: sum(
                1 for record in records
                if getattr(record, "suggested_mode", "unknown") == mode
            )
            for mode in (
                "functional_diagram", "data_plot", "descriptive_image", "mixed", "unknown"
            )
        },
        "iterative_mode": iterative_enabled,
    }
    if iterative_enabled:
        done_payload["convergence"] = convergence_counts
    return result, done_payload


def _ctxexpl_max_elements_per_document() -> int:
    try:
        return max(0, int(os.getenv("CTXEXPL_MAX_ELEMENTS_PER_DOCUMENT", "40")))
    except (TypeError, ValueError):
        return 40


def _ctxexpl_max_calls_per_day() -> int:
    try:
        return max(0, int(os.getenv("CTXEXPL_MAX_CALLS_PER_DAY", "20")))
    except (TypeError, ValueError):
        return 20


def _ctxexpl_model(effective_options: dict | None = None) -> str:
    """M層の正本（``core.llm_policy.resolve_scene_model``）でモデルを決める。

    ``ContextualExplanationAgent`` はモデル名を construction 時の明示引数
    (``llm_model=``) として受け取るため、``core.llm.py`` の呼び出し口で
    call-argument 扱いになり、M層設計書 §3 の解決順①（呼び出し側の明示引数）が
    最優先されてしまう。そのため、ランナーループが張る
    ``core.llm_policy.model_override`` の contextvar（解決順②）だけに任せられない。
    ``_discuss_opening_model`` と同じく **同じ正本関数**を呼んで解決してから渡す
    ことで、run override → user/system ポリシー →
    ``CTXEXPL_LLM_MODEL``（``llm_policy`` の ``_FEATURE_DIRECT_ENV``）→ fast tier
    の順が効く（M1: env をここで直読みしない）。

    ``effective_options["models"]`` の先読みは従来どおり維持する
    （``pipeline:contextual_explanation`` → ``pipeline`` の順）。ランナーループの
    ``model_override`` と同じ値になるが、この関数が override コンテキストの外から
    呼ばれても run 指定が効くようにしておく。
    """
    models = (effective_options or {}).get("models") if effective_options else None
    if isinstance(models, dict):
        override = models.get("pipeline:contextual_explanation") or models.get("pipeline")
        if isinstance(override, str) and override.strip():
            return override.strip()
    try:
        resolved = resolve_scene_model(f"{SCENE_PIPELINE}:contextual_explanation").model
    except Exception:
        logger.debug("failed to resolve contextual_explanation model", exc_info=True)
        resolved = ""
    if resolved:
        return resolved
    from core.config import get_settings

    return get_settings().llm_fast_model


def _build_contextual_explanation(
    *,
    document_id: str,
    cartridge_id: str | None,
    component_result: Any,
    claim_objects: Any,
    equations: Any,
    fig_tbl: Any,
    apparatus_result: Any,
    thesis: Any,
    material_id: str | None = None,
    derivations: Any = None,
    effective_options: dict | None = None,
) -> dict:
    """contextual_explanation ステージ本体（design doc §5.1）。

    入力構築は ``contextual_explanation_inputs.py`` に分離（Tier 3-19 方式）。
    ここでは (1) 優先順位付き・上限適用済みの要素リストを組み立て、
    (2) 日次コスト上限を CostGate で事前チェック（apparatus の
    ``_apparatus_daily_remaining`` と同じ「先に残数を見て、実行後に実測を計上する」
    流儀を、DB 集計クエリの代わりに再利用可能な CostGate プリミティブで行う。
    figure_reanalysis.py が既にこの組み合わせ方の前例）、(3) agent を実行し、
    (4) 結果を ``element_explanations`` に candidate として保存する。
    """
    from .contextual_explanation_inputs import build_contextual_explanation_inputs

    max_elements = _ctxexpl_max_elements_per_document()
    elements, meta = build_contextual_explanation_inputs(
        document_id=document_id,
        cartridge_id=cartridge_id,
        component_result=component_result,
        claim_objects=claim_objects,
        equations=equations,
        fig_tbl=fig_tbl,
        apparatus_result=apparatus_result,
        thesis=thesis,
        max_elements=max_elements,
        material_id=material_id,
        derivations=derivations,
    )

    payload: dict[str, Any] = {
        "status": "completed",
        "elements_considered": meta.get("considered", 0),
        "elements_selected": meta.get("selected", 0),
        "truncated": bool(meta.get("truncated", False)),
        "truncated_count": meta.get("truncated_count", 0),
        "counts_by_kind": meta.get("counts_by_kind", {}),
        "skipped": meta.get("skipped", []),
        # 指示書 §5.2: 教材提示対象の数式が何件あり、何件を入力化し、何件が
        # artifact 未解決だったか。CostGate 到達で生成を諦めた run でも残る。
        "required_equations_considered": meta.get("required_equations_considered", 0),
        "required_equations_selected": meta.get("required_equations_selected", 0),
        "required_equations_unresolved": meta.get("required_equations_unresolved", 0),
        "llm_calls": 0,
        "saved_candidates": 0,
        "agent_skipped": [],
    }

    if not elements:
        return payload

    daily_limit = _ctxexpl_max_calls_per_day()
    daily_key = today_str()
    remaining = _ctxexpl_cost_gate.daily_remaining(daily_limit=daily_limit, daily_key=daily_key)
    if remaining <= 0:
        payload["skipped_by_limit"] = True
        logger.info(
            "contextual_explanation: daily call limit reached (limit=%d), skipping "
            "LLM generation for document=%s (%d element(s) considered)",
            daily_limit, document_id, len(elements),
        )
        return payload

    from episteme_graph.agents.contextual_explanation.agent import ContextualExplanationAgent

    agent = ContextualExplanationAgent(llm_model=_ctxexpl_model(effective_options))
    result = agent.run(elements, cartridge_id=cartridge_id)
    # Real usage is only known after the batched+repaired run completes (a
    # single stage run may cost more than 1 LLM call); book it post-hoc
    # against today's counter, exactly like figure_reanalysis.py's
    # vision-call accounting via CostGate.count_extra_daily.
    _ctxexpl_cost_gate.count_extra_daily(daily_key=daily_key, amount=result.llm_call_count)

    payload["llm_calls"] = result.llm_call_count
    payload["truncated"] = bool(payload["truncated"] or result.truncated)

    items: list[dict] = []
    agent_skipped: list[dict] = []
    for element_result in result.elements:
        if element_result.skipped_reason:
            agent_skipped.append({
                "element_type": element_result.element_type,
                "element_id": element_result.element_id,
                "reason": element_result.skipped_reason,
            })
            continue
        evidence = {
            "evidence_quote": element_result.evidence_quote,
            "reason": element_result.reason,
            "confidence": element_result.confidence,
        }
        if element_result.contextual_explanation:
            items.append({
                "element_type": element_result.element_type,
                "element_id": element_result.element_id,
                "kind": "contextual",
                "body": element_result.contextual_explanation,
                "evidence": dict(evidence),
                "created_by": "pipeline",
            })
        if element_result.generic_explanation:
            items.append({
                "element_type": element_result.element_type,
                "element_id": element_result.element_id,
                "kind": "generic",
                "body": element_result.generic_explanation,
                "evidence": dict(evidence),
                "created_by": "pipeline",
            })
    payload["agent_skipped"] = agent_skipped

    if items:
        from core.postgres import get_session as _pg_session

        session = _pg_session()
        try:
            from core.element_explanations import insert_candidates

            saved = insert_candidates(session, document_id, items)
            session.commit()
            payload["saved_candidates"] = len(saved)
        except Exception:
            session.rollback()
            logger.warning(
                "contextual_explanation: failed to persist candidate explanations "
                "(non-fatal): document=%s",
                document_id, exc_info=True,
            )
        finally:
            session.close()

    return payload


def _discuss_opening_max_items_per_document() -> int:
    try:
        return max(0, int(os.getenv("DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT", "4")))
    except (TypeError, ValueError):
        return 4


def _discuss_opening_max_calls_per_day() -> int:
    try:
        return max(0, int(os.getenv("DISCUSS_OPENING_MAX_CALLS_PER_DAY", "20")))
    except (TypeError, ValueError):
        return 20


def _discuss_opening_language() -> str:
    """生成言語（設計書 §4.1）。``lecture_language`` は使わない — 同じ論文が言語設定の
    違うコースに載りうるため、document 単位の生成物は env 1つで決める。"""
    return (os.getenv("DISCUSS_OPENING_LANGUAGE", "") or "ja").strip() or "ja"


def _discuss_opening_model() -> str:
    """M層の正本（``core.llm_policy.resolve_scene_model``）でモデルを決める。

    ``DiscussOpeningAgent`` はモデル名を construction 引数で受けるため、ランナー
    ループが張る ``model_override`` の contextvar（解決順②）を素通ししてしまう。
    ここで **同じ正本関数**を呼んで解決してから渡すことで、run override →
    user/system ポリシー → ``DISCUSS_OPENING_LLM_MODEL``（``llm_policy`` の
    ``_FEATURE_DIRECT_ENV``）→ fast tier の順が効く。この関数は本ステージが
    ``model_override`` コンテキストの内側で実行されることを前提にしている
    （``run_document_pipeline`` のループがステージ単位で張る）。
    """
    try:
        return resolve_scene_model(f"{SCENE_PIPELINE}:discuss_opening").model
    except Exception:
        logger.debug("failed to resolve discuss_opening model", exc_info=True)
        return ""


def _build_discuss_opening(
    *,
    document_id: str,
    cartridge_id: str | None,
    artifacts: dict,
    derivations: Any,
    equations: Any,
) -> dict:
    """discuss_opening ステージ本体（``discuss_opening_authoring_design.md`` §4.1）。

    (1) 素材（D層の未検証前提 / derivation の operation 列 / thesis の合成文）を
    ``core.discuss.authoring`` で**解決済みテキスト**として組み立て、
    (2) 日次コスト上限を CostGate で事前チェックし（contextual_explanation と同じ
    「先に残数を見て、実行後に実測を計上する」流儀）、
    (3) agent を 1 document = 1 コールで実行し、
    (4) 結果を ``element_explanations`` に ``element_type='document'`` /
    ``kind='contextual'`` / ``role='discussion_seed'`` の candidate として保存する
    （再解析時、同じキーの既存 candidate は superseded・approved は不変）。

    素材が無い document は**生成しない**（``skipped_reason='no_source_material'`` を
    stage_outputs に正直に記録する。根拠の無い火種を創作させない — 設計書 §4.1）。

    未検証前提の出所は2系統ある（``assumption_source`` に記録する）:

    - ``"ledger"``: D層 ``epistemic_ledger``（再解析時。前 run の行がまだ残っている）
    - ``"artifact_fallback"``: 台帳が空のとき（初回解析）に in-run artifact から
      同じ保守的マッピングで導出したもの（[D-4]。台帳への記帳はしない）
    """
    from core.discuss.authoring import (
        build_discuss_opening_input,
        collect_untested_assumptions,
        compute_source_fingerprint,
        derive_untested_assumptions_from_artifacts,
    )

    max_items = _discuss_opening_max_items_per_document()
    language = _discuss_opening_language()

    payload: dict[str, Any] = {
        "status": "completed",
        "language": language,
        "max_items": max_items,
        "assumption_count": 0,
        "author_choice_count": 0,
        "llm_calls": 0,
        "seed_count": 0,
        "saved_candidates": 0,
        "truncated": False,
        "truncated_count": 0,
    }

    untested_assumptions: list[dict] = []
    session = None
    ledger_stats: dict = {}
    try:
        from core.postgres import get_session as _pg_session

        session = _pg_session()
        untested_assumptions = collect_untested_assumptions(
            session, document_id, stats=ledger_stats
        )
    except Exception:
        # 台帳未整備 / DB 不達でもステージは進める（operation だけで生成できる）。
        payload["assumption_lookup_failed"] = True
        logger.warning(
            "discuss_opening: failed to read epistemic_ledger (non-fatal): document=%s",
            document_id, exc_info=True,
        )
    finally:
        if session is not None:
            session.close()

    if untested_assumptions:
        payload["assumption_source"] = "ledger"
    if int(ledger_stats.get("opaque_skipped") or 0):
        # [D-1] 不透明 ID だけの台帳行は素材にしない（落とした件数は正直に残す, P4）。
        payload["assumption_rows_skipped_opaque"] = int(ledger_stats["opaque_skipped"])

    if not untested_assumptions:
        # [D-4] 台帳は pipeline **完了後**の backfill が書くため、初回解析では構造的に
        # 空になる。台帳と同じ保守的マッピングを in-run artifact に写像して素材を作る
        # （D層への記帳はしない）。
        fallback_stats: dict = {}
        try:
            untested_assumptions = derive_untested_assumptions_from_artifacts(
                artifacts, stats=fallback_stats
            )
        except Exception:
            logger.warning(
                "discuss_opening: artifact fallback for untested assumptions failed "
                "(non-fatal): document=%s",
                document_id, exc_info=True,
            )
            untested_assumptions = []
        if untested_assumptions:
            payload["assumption_source"] = "artifact_fallback"
        if int(fallback_stats.get("opaque_skipped") or 0):
            payload["assumption_rows_skipped_opaque"] = int(
                payload.get("assumption_rows_skipped_opaque") or 0
            ) + int(fallback_stats["opaque_skipped"])

    agent_input = build_discuss_opening_input(
        document_id=document_id,
        artifacts=artifacts,
        derivations=derivations,
        equations=equations,
        untested_assumptions=untested_assumptions,
        language=language,
        max_seeds=max_items,
    )
    payload["assumption_count"] = len(agent_input.get("untested_assumptions") or [])
    payload["author_choice_count"] = len(agent_input.get("author_choices") or [])

    if max_items <= 0:
        payload["skipped_reason"] = "item_limit_is_zero"
        return payload

    if not (payload["assumption_count"] or payload["author_choice_count"]):
        payload["skipped_reason"] = "no_source_material"
        logger.info(
            "discuss_opening: no untested assumption / author choice for document=%s; "
            "skipping generation",
            document_id,
        )
        return payload

    daily_limit = _discuss_opening_max_calls_per_day()
    daily_key = today_str()
    remaining = _discuss_opening_cost_gate.daily_remaining(
        daily_limit=daily_limit, daily_key=daily_key
    )
    if remaining <= 0:
        payload["skipped_by_limit"] = True
        payload["skipped_reason"] = "daily_call_limit_reached"
        logger.info(
            "discuss_opening: daily call limit reached (limit=%d), skipping generation "
            "for document=%s",
            daily_limit, document_id,
        )
        return payload

    from episteme_graph.agents.discuss_opening.agent import DiscussOpeningAgent

    agent = DiscussOpeningAgent(llm_model=_discuss_opening_model() or None)
    result = agent.run(agent_input, cartridge_id=cartridge_id)
    # 実測の事後計上（contextual_explanation / figure_reanalysis と同じ会計）。
    _discuss_opening_cost_gate.count_extra_daily(
        daily_key=daily_key, amount=result.llm_call_count
    )

    payload["llm_calls"] = result.llm_call_count
    payload["seed_count"] = len(result.seeds)
    payload["truncated"] = bool(result.truncated)
    payload["truncated_count"] = int(result.truncated_count or 0)
    if result.skipped_reason:
        payload["skipped_reason"] = result.skipped_reason
    if result.review_notes:
        payload["review_notes"] = list(result.review_notes)

    if not result.seeds:
        return payload

    fingerprint = compute_source_fingerprint(artifacts)
    payload["source_fingerprint"] = fingerprint

    from core.element_explanations import (
        ELEMENT_TYPE_DOCUMENT,
        KIND_CONTEXTUAL,
        PIPELINE_CREATED_BY,
        ROLE_DISCUSSION_SEED,
    )

    items = [
        {
            "element_type": ELEMENT_TYPE_DOCUMENT,
            "element_id": str(document_id),
            "kind": KIND_CONTEXTUAL,
            "role": ROLE_DISCUSSION_SEED,
            "body": seed.body,
            "evidence": {
                "evidence_quote": seed.evidence_quote,
                "reason": seed.reason,
                "confidence": seed.confidence,
                "grounded_in": seed.grounded_in,
                "language": result.language,
                "source_fingerprint": fingerprint,
                "opening_version": result.opening_version,
            },
            "created_by": PIPELINE_CREATED_BY,
        }
        for seed in result.seeds
    ]

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        from core.element_explanations import insert_candidates

        saved = insert_candidates(session, document_id, items)
        session.commit()
        payload["saved_candidates"] = len(saved)
    except Exception:
        session.rollback()
        logger.warning(
            "discuss_opening: failed to persist candidate seeds (non-fatal): document=%s",
            document_id, exc_info=True,
        )
    finally:
        session.close()

    return payload


def _build_landscape_placement(ctx: PipelineContext) -> dict:
    """landscape_placement ステージ本体（``knowledge_landscape_design.md`` §7.1）。

    実体は ``core.landscape.builder.build_and_store_placements`` への薄い委譲で、
    admin の手動再提案（``.../placements/propose``）と**同一コードパス・同一日次
    予算**を通る（§7.1）。コスト上限・モデル解決（M層）・骨格列挙・永続化はすべて
    builder 側に置き、ここでは in-run artifact を渡すだけにする（未指定なら builder が
    ``core.deliberation.refs.document_run_artifacts`` で DB から読む）。
    """
    from core.landscape.builder import build_and_store_placements

    return build_and_store_placements(
        document_id=ctx.document_id,
        artifacts=ctx.all_artifacts(),
        run_id=ctx.run_id,
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
