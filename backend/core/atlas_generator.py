"""分野の地図 — 骨格のLLMバッチ生成 (Issue A-2)。

カートリッジ単位で一度だけ実行するバッチ。出力は `status: draft` の骨格で、
`generated_by: "model:<id> batch:<id>"` を必ず記録する。

- draft はいかなる画面にも学習者向け表示しない (core.atlas.learner_view が担保)
- リアルタイムの LLM 生成は行わない。本モジュールは明示的なバッチ操作
  (管理APIの生成エンドポイント / scripts.generate_atlas_skeleton) からのみ呼ぶ
- LLM 出力は後処理で上限 (領域 ≤ 7・領域内代表概念 ≤ 6, §13) と座標範囲に収める
- LLM が seed_status を提案しても `reviewed` は必ず False に留める
  (maturity・review情報の最終確定禁止 — 確定は教員レビュー A-3 のみ)
"""

from __future__ import annotations

import logging
import re
import uuid

from pydantic import BaseModel, Field

from core import atlas
from core.cartridges import DomainCartridge, load_cartridge
from core.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM structured output モデル
# ---------------------------------------------------------------------------


class GeneratedConcept(BaseModel):
    id: str = Field(description="安定したスラッグID (小文字英数と_)。版を跨いで不変になる前提で付ける")
    label: str = Field(description="学習者向けの短い概念名 (日本語)")
    x: float = Field(description="領域内の正規化x座標 (0.0-1.0)")
    y: float = Field(description="領域内の正規化y座標 (0.0-1.0)")
    seed_status: str | None = Field(
        default=None,
        description="初期状態ヒント。verified / contested / assumed のいずれか。不明なら null",
    )


class GeneratedRegion(BaseModel):
    id: str = Field(description="安定したスラッグID (小文字英数と_)")
    label: str = Field(description="領域名 (日本語)")
    x: float = Field(description="キャンバス上の正規化x座標 (0.0-1.0)")
    y: float = Field(description="キャンバス上の正規化y座標 (0.0-1.0)")
    w: float = Field(description="正規化幅 (0.0-1.0)")
    h: float = Field(description="正規化高さ (0.0-1.0)")
    concepts: list[GeneratedConcept] = Field(
        default_factory=list, description="領域の代表概念 (最大6件)"
    )


class GeneratedEdge(BaseModel):
    from_id: str = Field(description="始点の領域または概念のID")
    to_id: str = Field(description="終点の領域または概念のID")
    kind: str = Field(default="adjacent", description="adjacent / depends / related")


class GeneratedSkeleton(BaseModel):
    regions: list[GeneratedRegion] = Field(description="分野の主要領域 (最大7件)")
    edges: list[GeneratedEdge] = Field(default_factory=list, description="領域間の関係")


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------


def build_generation_prompt(cartridge: DomainCartridge) -> str:
    concept_types = ", ".join(
        str(c.get("label") or c.get("id") or "") for c in cartridge.ontology.concept_types[:20]
    )
    domains = ", ".join(cartridge.target_domain)
    return (
        "あなたは大学院教育のカリキュラム設計者です。"
        "以下の分野について、学習者に見せる「分野の地図」の骨格 (領域と代表概念の配置) を設計してください。\n\n"
        f"分野: {cartridge.name}\n"
        f"対象ドメイン: {domains}\n"
        f"説明: {cartridge.description}\n"
        f"分野の概念タイプ語彙: {concept_types}\n\n"
        "制約:\n"
        f"- 領域 (regions) は最大 {atlas.MAX_REGIONS} 件。分野を俯瞰する主要領域に絞る\n"
        f"- 各領域の代表概念 (concepts) は最大 {atlas.MAX_CONCEPTS_PER_REGION} 件\n"
        "- id は英小文字・数字・アンダースコアのスラッグ。改版を跨いで安定に使える普遍的な名前にする\n"
        "- layout は正規化座標 (0.0〜1.0)。領域同士は重ならないように配置する\n"
        "- 概念の x/y は領域内の相対位置 (0.0〜1.0)\n"
        "- seed_status は確信がある場合のみ: verified (実験で確認) / contested (解釈が分かれる) / "
        "assumed (暗黙の前提)。不明なら null\n"
        "- edges は領域間の隣接・依存関係。from_id / to_id には領域IDを使う\n"
        "- 推薦文言・評価語は含めない。地図は事実の投影であり誘導ではない\n"
    )


# ---------------------------------------------------------------------------
# 後処理 (上限・座標・語彙を非LLMで整える)
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9_]+")


def _slugify(value: str, fallback: str) -> str:
    slug = _SLUG_STRIP.sub("_", str(value).strip().lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{fallback}_{slug}" if slug else fallback
    return slug


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _unique_id(slug: str, seen: set[str]) -> str:
    candidate = slug
    suffix = 2
    while candidate in seen:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def normalize_generated(
    generated: GeneratedSkeleton,
    *,
    cartridge_id: str,
    generated_by: str,
) -> atlas.AtlasSkeleton:
    """LLM出力を骨格スキーマに正規化する。上限超過は切り詰め、座標はクランプする。"""
    seen_ids: set[str] = set()
    regions: list[atlas.SkeletonRegion] = []

    dropped_regions = len(generated.regions) - atlas.MAX_REGIONS
    if dropped_regions > 0:
        logger.warning(
            "atlas generation: %d region(s) over the limit were dropped (cartridge=%s)",
            dropped_regions,
            cartridge_id,
        )

    for i, region in enumerate(generated.regions[: atlas.MAX_REGIONS]):
        region_id = _unique_id(_slugify(region.id, f"region{i + 1}"), seen_ids)
        dropped_concepts = len(region.concepts) - atlas.MAX_CONCEPTS_PER_REGION
        if dropped_concepts > 0:
            logger.warning(
                "atlas generation: %d concept(s) over the limit were dropped (region=%s)",
                dropped_concepts,
                region_id,
            )
        concepts: list[atlas.SkeletonConcept] = []
        for j, concept in enumerate(region.concepts[: atlas.MAX_CONCEPTS_PER_REGION]):
            concept_id = _unique_id(_slugify(concept.id, f"{region_id}_c{j + 1}"), seen_ids)
            seed = None
            if concept.seed_status in atlas.SEED_STATUS_VALUES:
                # LLM 提案は provisional。reviewed は教員レビュー (A-3) まで必ず False
                seed = atlas.SeedStatus(value=concept.seed_status, reviewed=False)
            concepts.append(
                atlas.SkeletonConcept(
                    id=concept_id,
                    label=concept.label.strip() or concept_id,
                    layout=atlas.ConceptLayout(
                        x=_clamp(concept.x, 0.0, 1.0), y=_clamp(concept.y, 0.0, 1.0)
                    ),
                    seed_status=seed,
                )
            )
        x = _clamp(region.x, 0.0, 1.0)
        y = _clamp(region.y, 0.0, 1.0)
        w = _clamp(region.w, 0.01, 1.0 - x) if x < 1.0 else 0.01
        h = _clamp(region.h, 0.01, 1.0 - y) if y < 1.0 else 0.01
        regions.append(
            atlas.SkeletonRegion(
                id=region_id,
                label=region.label.strip() or region_id,
                layout=atlas.RegionLayout(x=x, y=y, w=w, h=h),
                concepts=tuple(concepts),
            )
        )

    edges: list[atlas.SkeletonEdge] = []
    for edge in generated.edges:
        from_id = _slugify(edge.from_id, "")
        to_id = _slugify(edge.to_id, "")
        if from_id not in seen_ids or to_id not in seen_ids:
            logger.warning(
                "atlas generation: edge references unknown id and was dropped: %s -> %s",
                edge.from_id,
                edge.to_id,
            )
            continue
        kind = edge.kind if edge.kind in atlas.EDGE_KINDS else "adjacent"
        edges.append(atlas.SkeletonEdge(from_id=from_id, to_id=to_id, kind=kind))

    skeleton = atlas.AtlasSkeleton(
        cartridge=cartridge_id,
        status=atlas.STATUS_DRAFT,
        version="",
        generated_by=generated_by,
        reviewed_by=(),
        changelog=(),
        regions=tuple(regions),
        edges=tuple(edges),
        concept_bindings=(),
    )
    report = atlas.validate_skeleton(skeleton)
    if not report.ok:
        raise ValueError(
            "生成骨格が後処理後もスキーマに適合しません: " + "; ".join(report.errors)
        )
    return skeleton


# ---------------------------------------------------------------------------
# バッチ生成 (一度だけ実行。再実行は明示操作)
# ---------------------------------------------------------------------------


def generate_skeleton_draft(
    cartridge_id: str,
    *,
    batch_id: str | None = None,
    model: str | None = None,
) -> atlas.AtlasSkeleton:
    """カートリッジのメタデータ + モデル知識から draft 骨格を生成する。"""
    # 遅延インポート: core.atlas / cartridges の単体テストを LLM SDK なしで動かすため
    from core.llm import generate_text_with_structured_output

    cartridge = load_cartridge(cartridge_id)
    settings = get_settings()
    model_name = model or settings.llm_analysis_model
    batch = batch_id or uuid.uuid4().hex[:12]

    generated = generate_text_with_structured_output(
        messages=[{"role": "user", "content": build_generation_prompt(cartridge)}],
        response_format=GeneratedSkeleton,
        model=model_name,
    )
    return normalize_generated(
        generated,
        cartridge_id=cartridge.cartridge_id,
        generated_by=f"model:{model_name} batch:{batch}",
    )
