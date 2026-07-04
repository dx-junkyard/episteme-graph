"""分野の地図(Field Atlas)— 骨格(skeleton)スキーマ・バリデータ・凍結処理 (Issue A)。

仕様書 `field_atlas_overlay_spec.md` §9 に基づく。骨格はモデル知識から
カートリッジ単位で一度だけバッチ生成し、教員レビューを経て版として凍結し、
カートリッジに同梱する静的アセット (`atlas/skeleton.yaml`)。

設計上の重要事項:

- 本モジュールは FastAPI / SQLAlchemy / OpenAI など外部依存を持たないこと
  (core/cartridges.py と同じ方針。CI で外部サービスなしに検証できる)。
- 骨格が持てる状態情報は `seed_status`(初期ヒント)までで、
  `reviewed: true` のもののみ学習者向けに表示できる。
  検証状態・承認・灯りは生きた台帳・C層から実行時に導出する(骨格に焼き込まない)。
- draft はいかなる学習者向け画面にも出さない (`is_learner_visible` で担保)。
- 凍結版は不変。修正は次版で行い、changelog に帰属を残す。
- concept id の永続性 (§16-4): id は版を跨いで不変。改版で概念を統合・分割した
  場合は `id_migrations` に旧→新の対応を残し、足跡・修正報告のマイグレーションに使う。
  id の再利用(別概念への転用)は禁止。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATUS_DRAFT = "draft"
STATUS_FROZEN = "frozen"
SKELETON_STATUSES = (STATUS_DRAFT, STATUS_FROZEN)

# 骨格に持てる seed_status の語彙(認識的状態のみ。個人層の状態は持たない)
SEED_STATUS_VALUES = ("verified", "contested", "assumed")

# ノード数上限 (仕様書 §13)
MAX_REGIONS = 7
MAX_CONCEPTS_PER_REGION = 6

# エッジ種別
EDGE_KINDS = ("adjacent", "depends", "related")

# カートリッジ内の配置
SKELETON_FILENAME = "atlas/skeleton.yaml"
DRAFT_FILENAME = "atlas/skeleton.draft.yaml"

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedStatus:
    """概念の初期状態ヒント。reviewed=True のもののみ表示可 (§9.2)。"""

    value: str
    reviewed: bool = False


@dataclass(frozen=True)
class ConceptLayout:
    x: float
    y: float


@dataclass(frozen=True)
class RegionLayout:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class SkeletonConcept:
    id: str
    label: str
    layout: ConceptLayout | None = None
    seed_status: SeedStatus | None = None

    @property
    def display_seed_status(self) -> str | None:
        """学習者向けに表示してよい seed_status。reviewed でなければ None。"""
        if self.seed_status is not None and self.seed_status.reviewed:
            return self.seed_status.value
        return None


@dataclass(frozen=True)
class SkeletonRegion:
    id: str
    label: str
    layout: RegionLayout | None = None
    concepts: tuple[SkeletonConcept, ...] = ()


@dataclass(frozen=True)
class SkeletonEdge:
    from_id: str
    to_id: str
    kind: str = "adjacent"


@dataclass(frozen=True)
class ConceptBinding:
    """骨格概念 ↔ コーパス概念の対応。レビュー済みのもののみ同梱可。"""

    skeleton: str
    graph_concept_id: str
    reviewed: bool = False


@dataclass(frozen=True)
class ChangelogEntry:
    version: str
    note: str
    credits: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdMigration:
    """改版時の concept id 移行規則 (§16-4)。足跡・修正報告の追跡に使う。"""

    from_id: str
    to_id: str
    version: str


@dataclass(frozen=True)
class AtlasSkeleton:
    cartridge: str
    status: str = STATUS_DRAFT
    version: str = ""
    generated_by: str = ""
    reviewed_by: tuple[str, ...] = ()
    changelog: tuple[ChangelogEntry, ...] = ()
    regions: tuple[SkeletonRegion, ...] = ()
    edges: tuple[SkeletonEdge, ...] = ()
    concept_bindings: tuple[ConceptBinding, ...] = ()
    id_migrations: tuple[IdMigration, ...] = ()

    @property
    def is_learner_visible(self) -> bool:
        """学習者向けに描画してよい骨格か。draft は禁止 (Issue A-2)。"""
        return (
            self.status == STATUS_FROZEN
            and bool(self.version)
            and len(self.reviewed_by) > 0
        )

    def concept_ids(self) -> tuple[str, ...]:
        return tuple(c.id for r in self.regions for c in r.concepts)

    def region_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.regions)


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------


class SkeletonParseError(ValueError):
    pass


def _as_float(value: Any, context: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SkeletonParseError(f"{context}: 数値が必要です: {value!r}")


def _parse_seed_status(raw: Any, context: str) -> SeedStatus | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SkeletonParseError(f"{context}: seed_status はオブジェクトが必要です")
    return SeedStatus(
        value=str(raw.get("value") or ""),
        reviewed=bool(raw.get("reviewed", False)),
    )


def _parse_concept(raw: Any, context: str) -> SkeletonConcept:
    if not isinstance(raw, dict):
        raise SkeletonParseError(f"{context}: concept はオブジェクトが必要です")
    concept_id = str(raw.get("id") or "")
    layout_raw = raw.get("layout")
    layout = None
    if isinstance(layout_raw, dict):
        layout = ConceptLayout(
            x=_as_float(layout_raw.get("x"), f"{context}.layout.x"),
            y=_as_float(layout_raw.get("y"), f"{context}.layout.y"),
        )
    return SkeletonConcept(
        id=concept_id,
        label=str(raw.get("label") or ""),
        layout=layout,
        seed_status=_parse_seed_status(raw.get("seed_status"), f"{context}({concept_id})"),
    )


def _parse_region(raw: Any, context: str) -> SkeletonRegion:
    if not isinstance(raw, dict):
        raise SkeletonParseError(f"{context}: region はオブジェクトが必要です")
    region_id = str(raw.get("id") or "")
    layout_raw = raw.get("layout")
    layout = None
    if isinstance(layout_raw, dict):
        layout = RegionLayout(
            x=_as_float(layout_raw.get("x"), f"{context}.layout.x"),
            y=_as_float(layout_raw.get("y"), f"{context}.layout.y"),
            w=_as_float(layout_raw.get("w"), f"{context}.layout.w"),
            h=_as_float(layout_raw.get("h"), f"{context}.layout.h"),
        )
    concepts = tuple(
        _parse_concept(c, f"{context}.concepts[{i}]")
        for i, c in enumerate(raw.get("concepts") or [])
    )
    return SkeletonRegion(
        id=region_id,
        label=str(raw.get("label") or ""),
        layout=layout,
        concepts=concepts,
    )


def parse_skeleton(data: Any) -> AtlasSkeleton:
    """dict (yaml.safe_load 結果) から AtlasSkeleton を構築する。

    トップレベルが ``atlas_skeleton`` キーで包まれていてもよい (§9.4)。
    """
    if isinstance(data, dict) and isinstance(data.get("atlas_skeleton"), dict):
        data = data["atlas_skeleton"]
    if not isinstance(data, dict):
        raise SkeletonParseError("skeleton.yaml のトップレベルはオブジェクトが必要です")

    changelog = []
    for i, entry in enumerate(data.get("changelog") or []):
        if not isinstance(entry, dict):
            raise SkeletonParseError(f"changelog[{i}] はオブジェクトが必要です")
        changelog.append(
            ChangelogEntry(
                version=str(entry.get("version") or ""),
                note=str(entry.get("note") or ""),
                credits=tuple(str(c) for c in entry.get("credits") or []),
            )
        )

    edges = []
    for i, entry in enumerate(data.get("edges") or []):
        if not isinstance(entry, dict):
            raise SkeletonParseError(f"edges[{i}] はオブジェクトが必要です")
        edges.append(
            SkeletonEdge(
                from_id=str(entry.get("from") or ""),
                to_id=str(entry.get("to") or ""),
                kind=str(entry.get("kind") or "adjacent"),
            )
        )

    bindings = []
    for i, entry in enumerate(data.get("concept_bindings") or []):
        if not isinstance(entry, dict):
            raise SkeletonParseError(f"concept_bindings[{i}] はオブジェクトが必要です")
        bindings.append(
            ConceptBinding(
                skeleton=str(entry.get("skeleton") or ""),
                graph_concept_id=str(entry.get("graph_concept_id") or ""),
                reviewed=bool(entry.get("reviewed", False)),
            )
        )

    migrations = []
    for i, entry in enumerate(data.get("id_migrations") or []):
        if not isinstance(entry, dict):
            raise SkeletonParseError(f"id_migrations[{i}] はオブジェクトが必要です")
        migrations.append(
            IdMigration(
                from_id=str(entry.get("from") or ""),
                to_id=str(entry.get("to") or ""),
                version=str(entry.get("version") or ""),
            )
        )

    return AtlasSkeleton(
        cartridge=str(data.get("cartridge") or ""),
        status=str(data.get("status") or STATUS_DRAFT),
        version=str(data.get("version") or ""),
        generated_by=str(data.get("generated_by") or ""),
        reviewed_by=tuple(str(r) for r in data.get("reviewed_by") or []),
        changelog=tuple(changelog),
        regions=tuple(
            _parse_region(r, f"regions[{i}]") for i, r in enumerate(data.get("regions") or [])
        ),
        edges=tuple(edges),
        concept_bindings=tuple(bindings),
        id_migrations=tuple(migrations),
    )


def load_skeleton(path: Path | str) -> AtlasSkeleton:
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_skeleton(data)


# ---------------------------------------------------------------------------
# シリアライズ (決定論的 — 凍結版の再ビルドはバイト同一であること)
# ---------------------------------------------------------------------------


def skeleton_to_dict(skeleton: AtlasSkeleton) -> dict[str, Any]:
    """YAML/JSON へ書き出せる dict。キー順は固定 (再現性のため)。"""

    def _concept(c: SkeletonConcept) -> dict[str, Any]:
        out: dict[str, Any] = {"id": c.id, "label": c.label}
        if c.layout is not None:
            out["layout"] = {"x": c.layout.x, "y": c.layout.y}
        if c.seed_status is not None:
            out["seed_status"] = {
                "value": c.seed_status.value,
                "reviewed": c.seed_status.reviewed,
            }
        return out

    def _region(r: SkeletonRegion) -> dict[str, Any]:
        out: dict[str, Any] = {"id": r.id, "label": r.label}
        if r.layout is not None:
            out["layout"] = {
                "x": r.layout.x,
                "y": r.layout.y,
                "w": r.layout.w,
                "h": r.layout.h,
            }
        out["concepts"] = [_concept(c) for c in r.concepts]
        return out

    data: dict[str, Any] = {
        "version": skeleton.version,
        "cartridge": skeleton.cartridge,
        "status": skeleton.status,
        "generated_by": skeleton.generated_by,
        "reviewed_by": list(skeleton.reviewed_by),
        "changelog": [
            {"version": e.version, "note": e.note, "credits": list(e.credits)}
            for e in skeleton.changelog
        ],
        "regions": [_region(r) for r in skeleton.regions],
        "edges": [
            {"from": e.from_id, "to": e.to_id, "kind": e.kind} for e in skeleton.edges
        ],
        "concept_bindings": [
            {
                "skeleton": b.skeleton,
                "graph_concept_id": b.graph_concept_id,
                "reviewed": b.reviewed,
            }
            for b in skeleton.concept_bindings
        ],
    }
    if skeleton.id_migrations:
        data["id_migrations"] = [
            {"from": m.from_id, "to": m.to_id, "version": m.version}
            for m in skeleton.id_migrations
        ]
    return {"atlas_skeleton": data}


def dump_skeleton(skeleton: AtlasSkeleton) -> str:
    """決定論的 YAML 文字列。同一内容なら常にバイト同一 (受け入れ条件4)。"""
    buf = io.StringIO()
    yaml.safe_dump(
        skeleton_to_dict(skeleton),
        buf,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    return buf.getvalue()


def write_skeleton(skeleton: AtlasSkeleton, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_skeleton(skeleton), encoding="utf-8")


# ---------------------------------------------------------------------------
# バリデーション (CI で検証可能)
# ---------------------------------------------------------------------------


def _rects_overlap(a: RegionLayout, b: RegionLayout) -> bool:
    return not (
        a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y
    )


def validate_skeleton(skeleton: AtlasSkeleton) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    if not skeleton.cartridge:
        errors.append("cartridge が未設定です")
    if skeleton.status not in SKELETON_STATUSES:
        errors.append(f"status が不正です: {skeleton.status!r}")
    if not skeleton.generated_by:
        errors.append("generated_by (AI生成の来歴) が未記録です")

    is_frozen = skeleton.status == STATUS_FROZEN
    if skeleton.status != STATUS_DRAFT and not skeleton.reviewed_by:
        errors.append("reviewed_by が空のまま status が draft 以外になっています")
    if is_frozen:
        if not skeleton.version:
            errors.append("凍結済み骨格に version がありません")
        elif not any(e.version == skeleton.version for e in skeleton.changelog):
            errors.append(f"changelog に version {skeleton.version} のエントリがありません")

    # 上限 (§13)
    if len(skeleton.regions) > MAX_REGIONS:
        errors.append(f"領域数 {len(skeleton.regions)} が上限 {MAX_REGIONS} を超えています")
    for region in skeleton.regions:
        if len(region.concepts) > MAX_CONCEPTS_PER_REGION:
            errors.append(
                f"領域 '{region.id}' の代表概念数 {len(region.concepts)} が"
                f"上限 {MAX_CONCEPTS_PER_REGION} を超えています"
            )

    # id の一意性・形式
    seen_region_ids: set[str] = set()
    seen_concept_ids: set[str] = set()
    for region in skeleton.regions:
        if not region.id:
            errors.append("id が空の領域があります")
            continue
        if not _ID_PATTERN.match(region.id):
            errors.append(f"領域 id が不正です (小文字英数と_のみ): {region.id!r}")
        if region.id in seen_region_ids:
            errors.append(f"領域 id が重複しています: {region.id}")
        seen_region_ids.add(region.id)
        if not region.label:
            errors.append(f"領域 '{region.id}' に label がありません")
        for concept in region.concepts:
            if not concept.id:
                errors.append(f"領域 '{region.id}' に id が空の概念があります")
                continue
            if not _ID_PATTERN.match(concept.id):
                errors.append(f"概念 id が不正です (小文字英数と_のみ): {concept.id!r}")
            if concept.id in seen_concept_ids or concept.id in seen_region_ids:
                errors.append(f"概念 id が重複しています: {concept.id}")
            seen_concept_ids.add(concept.id)
            if not concept.label:
                errors.append(f"概念 '{concept.id}' に label がありません")

    # 正規化座標の範囲
    for region in skeleton.regions:
        if region.layout is not None:
            lo = region.layout
            if not (0.0 <= lo.x <= 1.0 and 0.0 <= lo.y <= 1.0):
                errors.append(f"領域 '{region.id}' の layout 座標が [0,1] を外れています")
            if not (0.0 < lo.w <= 1.0 and 0.0 < lo.h <= 1.0):
                errors.append(f"領域 '{region.id}' の layout サイズが (0,1] を外れています")
            elif lo.x + lo.w > 1.0 + 1e-9 or lo.y + lo.h > 1.0 + 1e-9:
                errors.append(f"領域 '{region.id}' が正規化キャンバスからはみ出しています")
        for concept in region.concepts:
            if concept.layout is not None:
                if not (0.0 <= concept.layout.x <= 1.0 and 0.0 <= concept.layout.y <= 1.0):
                    errors.append(f"概念 '{concept.id}' の layout 座標が [0,1] を外れています")

    # 領域の重なり (警告)
    laid_out = [r for r in skeleton.regions if r.layout is not None]
    for i, a in enumerate(laid_out):
        for b in laid_out[i + 1 :]:
            if _rects_overlap(a.layout, b.layout):  # type: ignore[arg-type]
                warnings.append(f"領域 '{a.id}' と '{b.id}' の配置が重なっています")

    # seed_status
    for region in skeleton.regions:
        for concept in region.concepts:
            ss = concept.seed_status
            if ss is None:
                continue
            if ss.value not in SEED_STATUS_VALUES:
                errors.append(
                    f"概念 '{concept.id}' の seed_status.value が不正です: {ss.value!r}"
                )
            if is_frozen and not ss.reviewed:
                warnings.append(
                    f"概念 '{concept.id}' の seed_status が未レビューのため表示されません"
                )

    # エッジの参照整合
    known_ids = seen_region_ids | seen_concept_ids
    for edge in skeleton.edges:
        if edge.kind not in EDGE_KINDS:
            errors.append(f"エッジ kind が不正です: {edge.kind!r}")
        for endpoint in (edge.from_id, edge.to_id):
            if endpoint not in known_ids:
                errors.append(f"エッジが未知の id を参照しています: {endpoint!r}")

    # concept_bindings (凍結版に同梱できるのはレビュー済みのみ)
    for binding in skeleton.concept_bindings:
        if binding.skeleton not in seen_concept_ids:
            errors.append(
                f"concept_bindings が未知の骨格概念を参照しています: {binding.skeleton!r}"
            )
        if not binding.graph_concept_id:
            errors.append(f"concept_bindings ('{binding.skeleton}') に graph_concept_id がありません")
        if is_frozen and not binding.reviewed:
            errors.append(
                f"凍結済み骨格に未レビューの concept_binding があります: {binding.skeleton!r}"
            )

    # id_migrations
    for migration in skeleton.id_migrations:
        if not migration.from_id or not migration.to_id or not migration.version:
            errors.append("id_migrations に from/to/version が欠けたエントリがあります")

    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# 学習者向けビュー (draft 非公開・未レビュー seed_status の除去を型で担保)
# ---------------------------------------------------------------------------


class SkeletonNotVisibleError(PermissionError):
    """draft / 未レビューの骨格を学習者向けに要求した場合に送出。"""


def learner_view(skeleton: AtlasSkeleton) -> dict[str, Any]:
    """学習者向けに公開してよい形の dict を返す。

    - draft はいかなる場合も返さない (SkeletonNotVisibleError)
    - `seed_status.reviewed != true` の seed_status は落とす
    - 未レビューの concept_bindings は落とす
    """
    if not skeleton.is_learner_visible:
        raise SkeletonNotVisibleError(
            "draft または未レビューの骨格は学習者向けに表示できません"
        )

    def _concept(c: SkeletonConcept) -> dict[str, Any]:
        out: dict[str, Any] = {"id": c.id, "label": c.label}
        if c.layout is not None:
            out["layout"] = {"x": c.layout.x, "y": c.layout.y}
        display = c.display_seed_status
        if display is not None:
            out["seed_status"] = display
        return out

    return {
        "skeleton_version": skeleton.version,
        "cartridge": skeleton.cartridge,
        "provenance": "AI生成・教員レビュー済",
        "regions": [
            {
                "id": r.id,
                "label": r.label,
                "layout": (
                    {"x": r.layout.x, "y": r.layout.y, "w": r.layout.w, "h": r.layout.h}
                    if r.layout is not None
                    else None
                ),
                "concepts": [_concept(c) for c in r.concepts],
            }
            for r in skeleton.regions
        ],
        "edges": [
            {"from": e.from_id, "to": e.to_id, "kind": e.kind} for e in skeleton.edges
        ],
        "concept_bindings": [
            {"skeleton": b.skeleton, "graph_concept_id": b.graph_concept_id}
            for b in skeleton.concept_bindings
            if b.reviewed
        ],
    }


# ---------------------------------------------------------------------------
# 凍結
# ---------------------------------------------------------------------------


class SkeletonFreezeError(ValueError):
    pass


def freeze_skeleton(
    skeleton: AtlasSkeleton,
    *,
    version: str,
    reviewed_by: list[str] | tuple[str, ...],
    note: str = "",
    credits: list[str] | tuple[str, ...] = (),
) -> AtlasSkeleton:
    """draft を凍結して版を付与する。以後は不変 (修正は次版で)。

    - `reviewed_by` は必須 (レビュー帰属。受け入れ条件2)
    - changelog に version のエントリを追記する
    - バリデーションエラーがあれば凍結しない
    """
    if skeleton.status == STATUS_FROZEN:
        raise SkeletonFreezeError("凍結済みの骨格は変更できません。修正は次版で行ってください")
    if not version:
        raise SkeletonFreezeError("version は必須です (例: 2026.1)")
    reviewers = tuple(str(r) for r in reviewed_by if str(r).strip())
    if not reviewers:
        raise SkeletonFreezeError("reviewed_by (レビュー帰属) は必須です")
    if any(e.version == version for e in skeleton.changelog):
        raise SkeletonFreezeError(f"version {version} は changelog に既に存在します")

    frozen = AtlasSkeleton(
        cartridge=skeleton.cartridge,
        status=STATUS_FROZEN,
        version=version,
        generated_by=skeleton.generated_by,
        reviewed_by=reviewers,
        changelog=skeleton.changelog
        + (ChangelogEntry(version=version, note=note or "改版", credits=tuple(str(c) for c in credits)),),
        regions=skeleton.regions,
        edges=skeleton.edges,
        concept_bindings=skeleton.concept_bindings,
        id_migrations=skeleton.id_migrations,
    )
    report = validate_skeleton(frozen)
    if not report.ok:
        raise SkeletonFreezeError(
            "凍結前バリデーションに失敗しました: " + "; ".join(report.errors)
        )
    return frozen
