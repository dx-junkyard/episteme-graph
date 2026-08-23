"""ドキュメント解析パイプラインの事前トークン見積り（設計書 §4.5 / §7-2）。

``estimate_document_run()`` は解析パイプラインを**実行せず**、対象ドキュメントの
チャンク数・総文字数・図数（DB 読みのみ）から、主要 LLM ステージ別のトークン量を
ステージごとの粗い入力係数 + 固定オーバーヘッドで見積る。LLM を呼ばない・乱数や
時刻に依存しない（同一入力には常に同一出力。テスト可能性 / 事前見積り両方に必須）。

``STAGE_INPUT_PROFILES`` の係数は **v1 の粗い定数**であり、実測トークン分布
（``llm_usage_events``）が溜まった後の Phase 3 で較正する前提のプレースホルダーである
（捏造した精度を演出しない。設計書 §11 Phase 3）。

対象ステージは ``core/document_pipeline/orchestrator.py`` の ``PIPELINE_STAGES`` のうち
LLM-first な主要ステージ（paper_skeleton / rhetorical_role / claim_qualification /
equation_semantics / thesis_reconstruction / dsl_linking / component_assembly）。
非LLM ステージ（evidence_registry / claim_object_builder / symbol_registry /
derivation_chain / course_mapping 等、``_PIPELINE_STEPS`` で ``llm_kind="none"`` を
宣言しているステージ）はここでは見積らない。``component_graph`` は LLM を呼ぶ
（``llm_kind="text"``）が入力量の係数が定まっていないため v1 の見積り対象外で、
``narrative_annotator`` / ``contextual_explanation`` / ``discuss_opening`` /
``landscape_placement`` も同様に未収録（見積りは主要ステージのみの下限であって
全 LLM ステージの網羅ではない）。``analyze_images=True`` のときのみ vision ステージ
（apparatus_semantics）を別途加算する。

FastAPI には依存しない・LLM を呼ばない（core/llm_usage の他モジュールと同じ規律）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.llm_usage import estimator

# 事前見積りは常にヒューリスティック推計（tiktoken 実測ではなく DB 由来の集計文字数からの
# 見積りのため）。設計書 §7-2 のレスポンス例と一致させる。
USAGE_SOURCE = "estimated_heuristic"

APPARATUS_STAGE_KEY = "apparatus_semantics"

# apparatus_semantics の画像1枚あたりの追加テキストオーバーヘッド
# （caption・近傍本文・vision プロンプトの定型文相当）。FLAT_IMAGE_TOKENS（画像そのもの）
# とは別枠で加算する粗い定数（v1）。
APPARATUS_TEXT_OVERHEAD_TOKENS_PER_IMAGE = 300


@dataclass(frozen=True)
class StageInputProfile:
    """1 LLM ステージあたりの粗い入力見積りプロファイル。

    ``input_coefficient``: ドキュメント総文字数のうち、このステージの LLM 入力に
    含まれるとみなす割合（1.0 = 全文相当、0.3〜0.5 程度 = 既に前段で抽出された
    claim/equation 等の縮小テキストを主入力とするステージ）。
    ``fixed_overhead_tokens``: プロンプト定型文・JSON Schema（structured output）等、
    文書サイズに依存しない固定オーバーヘッドの概算トークン数。
    """

    input_coefficient: float
    fixed_overhead_tokens: int


# 正本の係数表（v1、粗い定数）。すべて domain-independent
# （特定分野・特定論文の用語には依存しない、CLAUDE.md の全般ルールと同型）。
#
# | stage                | input_coefficient | fixed_overhead_tokens | 根拠（v1 の粗い仮定） |
# |-----------------------|--------------------|------------------------|------------------------|
# | paper_skeleton        | 1.0                | 900                    | 文書全文を仮説化に使う |
# | rhetorical_role       | 1.0                | 700                    | 全 chunk/span を判定   |
# | claim_qualification   | 1.0                | 1100                   | 全文 + atomic rewrite の出力が長い |
# | equation_semantics    | 0.3                | 600                    | 数式ブロック周辺のみが主入力 |
# | thesis_reconstruction | 0.4                | 800                    | 抽出済み claim/equation が主入力（全文より小さい） |
# | dsl_linking           | 0.35               | 700                    | 抽出済み claim/equation/thesis が主入力 |
# | component_assembly    | 0.5                | 900                    | 組立対象の claim/equation/thesis + cartridge 語彙 |
STAGE_INPUT_PROFILES: dict[str, StageInputProfile] = {
    "paper_skeleton": StageInputProfile(input_coefficient=1.0, fixed_overhead_tokens=900),
    "rhetorical_role": StageInputProfile(input_coefficient=1.0, fixed_overhead_tokens=700),
    "claim_qualification": StageInputProfile(input_coefficient=1.0, fixed_overhead_tokens=1100),
    "equation_semantics": StageInputProfile(input_coefficient=0.3, fixed_overhead_tokens=600),
    "thesis_reconstruction": StageInputProfile(input_coefficient=0.4, fixed_overhead_tokens=800),
    "dsl_linking": StageInputProfile(input_coefficient=0.35, fixed_overhead_tokens=700),
    "component_assembly": StageInputProfile(input_coefficient=0.5, fixed_overhead_tokens=900),
}


def _margin_range(point_tokens: float) -> tuple[int, int]:
    """点推定を ``estimator.ESTIMATE_MARGIN``（±40%）でレンジ化する（点推定を見せない, U5）。"""
    point_tokens = max(0.0, float(point_tokens))
    low = math.floor(point_tokens * (1 - estimator.ESTIMATE_MARGIN))
    high = math.ceil(point_tokens * (1 + estimator.ESTIMATE_MARGIN))
    return max(0, low), max(0, high)


def _stage_tokens_range(total_chars: int, profile: StageInputProfile) -> tuple[int, int]:
    """1 ステージ分のトークンレンジを見積る。

    ``estimator.OTHER_CHARS_PER_TOKEN`` のヒューリスティック係数を再利用する
    （実文書内容が無く総文字数の集計値しか持たないため CJK 判定は行わず、
    安全側の非CJK換算係数をそのまま流用する。設計書 §4.2 と同じ思想）。
    """
    stage_chars = int(total_chars * profile.input_coefficient)
    body_tokens = math.ceil(stage_chars / estimator.OTHER_CHARS_PER_TOKEN)
    point = body_tokens + profile.fixed_overhead_tokens
    return _margin_range(point)


def _fetch_document_totals(session, document_id: str) -> tuple[int, int, int] | None:
    """(chunk_count, total_chars, figure_count) を返す。chunk 0件なら None（document 不在扱い）。

    ``document_id`` は canonical な documents.id（UUID 文字列）または material_id の
    どちらでも解決する（``_chunks_for_document`` と同じ ``document_id::text = :document_id
    OR material_id = :document_id`` パターン）。
    """
    chunk_row = session.execute(
        sa_text(
            """
            SELECT count(*), COALESCE(sum(length(text)), 0)
            FROM chunks
            WHERE document_id::text = :document_id OR material_id = :document_id
            """
        ),
        {"document_id": document_id},
    ).fetchone()

    chunk_count = int(chunk_row[0] or 0) if chunk_row else 0
    if chunk_count == 0:
        return None
    total_chars = int(chunk_row[1] or 0) if chunk_row else 0

    figure_row = session.execute(
        sa_text("SELECT count(*) FROM document_figures WHERE document_id = :document_id"),
        {"document_id": document_id},
    ).fetchone()
    figure_count = int(figure_row[0] or 0) if figure_row else 0

    return chunk_count, total_chars, figure_count


def estimate_document_run(session, document_id: str, *, analyze_images: bool = False) -> dict | None:
    """対象ドキュメントの解析パイプライン実行前トークン量を見積る（設計書 §4.5 / §7-2）。

    LLM を呼ばない・DB 読みのみ（``session.execute`` は呼び出し側が渡したセッションを
    そのまま使う。session の open/close は呼び出し側の責務）。同一入力には常に同一出力
    （乱数・時刻非依存）。

    Returns:
        document が見つからない（chunk 0 件）場合は ``None``（呼び出し側で 404 に変換する）。
        見つかった場合は設計書 §7-2 のレスポンス形そのもの（``document_id`` /
        ``total_tokens_range`` / ``stages`` / ``usage_source``）。**点推定・cost_usd は
        一切含めない**（U5: 事前見積りはレンジのみ）。
    """
    totals = _fetch_document_totals(session, document_id)
    if totals is None:
        return None
    _chunk_count, total_chars, figure_count = totals

    stages: list[dict] = []
    total_low = 0
    total_high = 0

    for stage_key, profile in STAGE_INPUT_PROFILES.items():
        low, high = _stage_tokens_range(total_chars, profile)
        stages.append({"stage": stage_key, "tokens_range": [low, high]})
        total_low += low
        total_high += high

    if analyze_images:
        settings = get_settings()
        cap = max(0, int(settings.apparatus_max_images_per_document))
        capped_count = min(figure_count, cap)
        point = capped_count * (estimator.FLAT_IMAGE_TOKENS + APPARATUS_TEXT_OVERHEAD_TOKENS_PER_IMAGE)
        low, high = _margin_range(point)

        note = f"figures={figure_count}"
        if figure_count > cap:
            note += ", capped_at=APPARATUS_MAX_IMAGES_PER_DOCUMENT"

        stages.append({"stage": APPARATUS_STAGE_KEY, "tokens_range": [low, high], "note": note})
        total_low += low
        total_high += high

    return {
        "document_id": document_id,
        "total_tokens_range": [total_low, total_high],
        "stages": stages,
        "usage_source": USAGE_SOURCE,
    }
