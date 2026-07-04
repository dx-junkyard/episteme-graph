"""ドメインカートリッジローダー (Issue #176)。

`backend/cartridges/<cartridge_id>/` 配下の JSON / Markdown 一式を読み込み、
抽出・正規化・検証・UI表示・接続検証で参照できる形に統合する。

設計上の重要事項:

- 本モジュールは FastAPI / SQLAlchemy / OpenAI など外部依存を持たないこと。
  単体テストおよび CI で外部サービスなしに動作する必要がある。
- カートリッジ定義は読み取り専用としてキャッシュする。書き込みは行わない。
- 将来的に他分野カートリッジを追加できるようディレクトリ走査でリストする。
- 「素粒子物理学に限定した有効性検証」を優先するため、現時点では
  particle_physics 以外のカートリッジは存在しなくてよい。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from core import atlas as atlas_module
from core.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_FALLBACK_CARTRIDGE_ID = "particle_physics"


def _default_cartridge_id() -> str:
    """Settings.default_cartridge_id を返す。未設定・空文字時は particle_physics にフォールバック。"""
    settings = get_settings()
    value = (getattr(settings, "default_cartridge_id", "") or "").strip()
    return value or _FALLBACK_CARTRIDGE_ID


def _cartridges_root() -> Path:
    """`backend/cartridges/` の絶対パスを返す。

    ``Settings.cartridges_dir`` で上書きできる。未設定時は ``backend/cartridges``。
    """
    settings = get_settings()
    override = (getattr(settings, "cartridges_dir", "") or "").strip()
    if override:
        return Path(override).resolve()
    # backend/core/cartridges.py → backend/
    return Path(__file__).resolve().parent.parent / "cartridges"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CartridgeSummary:
    cartridge_id: str
    name: str
    version: str
    description: str
    target_domain: tuple[str, ...]


@dataclass(frozen=True)
class Ontology:
    concept_types: tuple[dict[str, Any], ...]
    aliases: tuple[dict[str, Any], ...]
    notation_patterns: tuple[dict[str, Any], ...]
    extraction_hints: tuple[str, ...]
    normalization_rules: tuple[dict[str, Any], ...]

    @property
    def concept_type_ids(self) -> tuple[str, ...]:
        return tuple(str(c.get("id", "")) for c in self.concept_types if c.get("id"))


@dataclass(frozen=True)
class DomainCartridge:
    cartridge_id: str
    name: str
    version: str
    description: str
    target_domain: tuple[str, ...]
    default_language: str
    source_policy: dict[str, Any]
    ontology: Ontology
    component_types: tuple[dict[str, Any], ...]
    relation_types: tuple[dict[str, Any], ...]
    maturity_levels: tuple[dict[str, Any], ...]
    maturity_sources: tuple[dict[str, Any], ...]
    support_statuses: tuple[dict[str, Any], ...]
    validation_rules: tuple[dict[str, Any], ...]
    extraction_prompt: str
    review_prompt: str
    examples: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    raw_manifest: dict[str, Any] = field(default_factory=dict)
    # 分野の地図の骨格 (Issue A)。同梱されていないカートリッジでは None
    # (フォールバック: 地図機能を出さない)
    atlas_skeleton: atlas_module.AtlasSkeleton | None = None

    # Convenience accessors -------------------------------------------------

    @property
    def component_type_ids(self) -> tuple[str, ...]:
        return tuple(str(c.get("id", "")) for c in self.component_types if c.get("id"))

    @property
    def relation_type_ids(self) -> tuple[str, ...]:
        return tuple(str(r.get("id", "")) for r in self.relation_types if r.get("id"))

    @property
    def maturity_level_ids(self) -> tuple[str, ...]:
        return tuple(str(m.get("id", "")) for m in self.maturity_levels if m.get("id"))

    @property
    def maturity_source_ids(self) -> tuple[str, ...]:
        return tuple(str(m.get("id", "")) for m in self.maturity_sources if m.get("id"))

    @property
    def support_status_ids(self) -> tuple[str, ...]:
        return tuple(str(s.get("id", "")) for s in self.support_statuses if s.get("id"))

    def component_type(self, component_type_id: str) -> dict[str, Any] | None:
        for entry in self.component_types:
            if str(entry.get("id")) == component_type_id:
                return dict(entry)
        return None

    @property
    def learner_atlas_skeleton(self) -> atlas_module.AtlasSkeleton | None:
        """学習者向けに表示してよい骨格。draft / 未レビューは決して返さない。"""
        skeleton = self.atlas_skeleton
        if skeleton is not None and skeleton.is_learner_visible:
            return skeleton
        return None

    def default_maturity_for(self, component_type_id: str) -> str | None:
        entry = self.component_type(component_type_id)
        if not entry:
            return None
        value = entry.get("default_maturity_level")
        return str(value) if value else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cartridge_id": self.cartridge_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "target_domain": list(self.target_domain),
            "default_language": self.default_language,
            "source_policy": self.source_policy,
            "ontology": {
                "concept_types": list(self.ontology.concept_types),
                "aliases": list(self.ontology.aliases),
                "notation_patterns": list(self.ontology.notation_patterns),
                "extraction_hints": list(self.ontology.extraction_hints),
                "normalization_rules": list(self.ontology.normalization_rules),
            },
            "component_types": list(self.component_types),
            "relation_types": list(self.relation_types),
            "maturity_levels": list(self.maturity_levels),
            "maturity_sources": list(self.maturity_sources),
            "support_statuses": list(self.support_statuses),
            "validation_rules": list(self.validation_rules),
            "extraction_prompt": self.extraction_prompt,
            "review_prompt": self.review_prompt,
            "examples": list(self.examples),
            "atlas_skeleton": (
                atlas_module.skeleton_to_dict(self.atlas_skeleton)["atlas_skeleton"]
                if self.atlas_skeleton is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Cartridge file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _tuple_of_dicts(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in _ensure_list(value) if isinstance(item, dict))


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in _ensure_list(value) if isinstance(item, str))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_examples(directory: Path) -> tuple[dict[str, Any], ...]:
    examples_dir = directory / "examples"
    if not examples_dir.is_dir():
        return ()
    items: list[dict[str, Any]] = []
    for entry in sorted(examples_dir.glob("*.json")):
        try:
            data = _read_json(entry)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to parse example file: %s", entry, exc_info=True)
            continue
        if isinstance(data, dict):
            items.append(data)
    return tuple(items)


def _load_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "cartridge.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"cartridge.json must be a JSON object: {manifest_path}")
    return manifest


def _load_atlas_skeleton(directory: Path, files: dict[str, Any]) -> atlas_module.AtlasSkeleton | None:
    """同梱骨格 `atlas/skeleton.yaml` を読み込む (Issue A-1)。

    - ファイルが無ければ None (地図機能を出さないフォールバック)
    - 同梱できるのは凍結済み (frozen) の骨格のみ。draft の同梱や
      `generated_by` / `reviewed_by` の欠落はビルド失敗 (ValueError) にする
      (受け入れ条件2・3)
    """
    path = directory / str(files.get("atlas_skeleton", atlas_module.SKELETON_FILENAME))
    if not path.exists():
        return None
    try:
        skeleton = atlas_module.load_skeleton(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"atlas skeleton の読み込みに失敗しました: {path}: {exc}") from exc
    if skeleton.status != atlas_module.STATUS_FROZEN:
        raise ValueError(
            f"カートリッジに同梱できる骨格は凍結済みのみです (status={skeleton.status!r}): {path}"
        )
    report = atlas_module.validate_skeleton(skeleton)
    if not report.ok:
        raise ValueError(
            f"同梱骨格のバリデーションに失敗しました: {path}: " + "; ".join(report.errors)
        )
    for warning in report.warnings:
        logger.warning("atlas skeleton (%s): %s", path, warning)
    return skeleton


def _load_one(directory: Path) -> DomainCartridge:
    manifest = _load_manifest(directory)
    files = manifest.get("files") or {}

    def _resolve(filename_key: str, default: str) -> Path:
        return directory / str(files.get(filename_key, default))

    ontology_raw = _read_json(_resolve("ontology", "ontology.json"))
    component_types_raw = _read_json(_resolve("component_types", "component_types.json"))
    relation_types_raw = _read_json(_resolve("relation_types", "relation_types.json"))
    maturity_raw = _read_json(_resolve("maturity_levels", "maturity_levels.json"))
    support_raw = _read_json(_resolve("support_statuses", "support_statuses.json"))
    validation_raw = _read_json(_resolve("validation_rules", "validation_rules.json"))
    extraction_prompt = _read_text(_resolve("extraction_prompt", "extraction_prompt.md"))
    review_prompt = _read_text(_resolve("review_prompt", "review_prompt.md"))

    ontology = Ontology(
        concept_types=_tuple_of_dicts(ontology_raw.get("concept_types") if isinstance(ontology_raw, dict) else None),
        aliases=_tuple_of_dicts(ontology_raw.get("aliases") if isinstance(ontology_raw, dict) else None),
        notation_patterns=_tuple_of_dicts(
            ontology_raw.get("notation_patterns") if isinstance(ontology_raw, dict) else None
        ),
        extraction_hints=_tuple_of_strings(
            ontology_raw.get("extraction_hints") if isinstance(ontology_raw, dict) else None
        ),
        normalization_rules=_tuple_of_dicts(
            ontology_raw.get("normalization_rules") if isinstance(ontology_raw, dict) else None
        ),
    )

    cartridge = DomainCartridge(
        cartridge_id=str(manifest.get("cartridge_id") or directory.name),
        name=str(manifest.get("name") or directory.name),
        version=str(manifest.get("version") or "0.0.0"),
        description=str(manifest.get("description") or ""),
        target_domain=_tuple_of_strings(manifest.get("target_domain")),
        default_language=str(manifest.get("default_language") or "ja"),
        source_policy=dict(manifest.get("source_policy") or {}),
        ontology=ontology,
        component_types=_tuple_of_dicts(
            component_types_raw.get("component_types") if isinstance(component_types_raw, dict) else None
        ),
        relation_types=_tuple_of_dicts(
            relation_types_raw.get("relation_types") if isinstance(relation_types_raw, dict) else None
        ),
        maturity_levels=_tuple_of_dicts(
            maturity_raw.get("maturity_levels") if isinstance(maturity_raw, dict) else None
        ),
        maturity_sources=_tuple_of_dicts(
            maturity_raw.get("maturity_sources") if isinstance(maturity_raw, dict) else None
        ),
        support_statuses=_tuple_of_dicts(
            support_raw.get("support_statuses") if isinstance(support_raw, dict) else None
        ),
        validation_rules=_tuple_of_dicts(
            validation_raw.get("validation_rules") if isinstance(validation_raw, dict) else None
        ),
        extraction_prompt=extraction_prompt,
        review_prompt=review_prompt,
        examples=_load_examples(directory),
        raw_manifest=manifest,
        atlas_skeleton=_load_atlas_skeleton(directory, files if isinstance(files, dict) else {}),
    )

    _validate_internal_consistency(cartridge)
    return cartridge


def _validate_internal_consistency(cartridge: DomainCartridge) -> None:
    """カートリッジ内の ID 重複・不整合を最低限チェックする。"""
    seen_ids: dict[str, set[str]] = {}
    for label, ids in (
        ("component_types", cartridge.component_type_ids),
        ("relation_types", cartridge.relation_type_ids),
        ("maturity_levels", cartridge.maturity_level_ids),
        ("maturity_sources", cartridge.maturity_source_ids),
        ("support_statuses", cartridge.support_status_ids),
        ("concept_types", cartridge.ontology.concept_type_ids),
    ):
        bucket = seen_ids.setdefault(label, set())
        for entry_id in ids:
            if entry_id in bucket:
                raise ValueError(
                    f"Cartridge '{cartridge.cartridge_id}' has duplicate id '{entry_id}' in {label}"
                )
            bucket.add(entry_id)

    # component_type の default_maturity_level が maturity_levels に存在するか確認
    valid_maturity = set(cartridge.maturity_level_ids)
    for entry in cartridge.component_types:
        default = entry.get("default_maturity_level")
        if default and default not in valid_maturity:
            raise ValueError(
                f"Cartridge '{cartridge.cartridge_id}': component_type '{entry.get('id')}' "
                f"has unknown default_maturity_level '{default}'"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _cached_load(cartridge_id: str, root: str) -> DomainCartridge:
    directory = Path(root) / cartridge_id
    if not directory.is_dir():
        raise FileNotFoundError(f"Cartridge directory not found: {directory}")
    return _load_one(directory)


def load_cartridge(cartridge_id: str | None = None) -> DomainCartridge:
    """カートリッジを読み込む。

    ``cartridge_id`` 省略時は ``Settings.default_cartridge_id`` を使用する。
    ``Settings.default_cartridge_id`` が未設定・空文字の場合は particle_physics にフォールバックする。
    """
    target = (cartridge_id or "").strip() or _default_cartridge_id()
    root = str(_cartridges_root())
    return _cached_load(target, root)


def get_default_cartridge() -> DomainCartridge:
    return load_cartridge()


def cartridge_directory(cartridge_id: str) -> Path:
    """カートリッジのディレクトリを返す (atlas 骨格の draft 読み書きなどに使う)。"""
    directory = _cartridges_root() / cartridge_id
    if not directory.is_dir():
        raise FileNotFoundError(f"Cartridge directory not found: {directory}")
    return directory


def list_cartridges() -> list[CartridgeSummary]:
    """利用可能なカートリッジのサマリ一覧を返す。"""
    root = _cartridges_root()
    if not root.is_dir():
        return []
    summaries: list[CartridgeSummary] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "cartridge.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to parse cartridge manifest: %s", manifest_path, exc_info=True)
            continue
        if not isinstance(manifest, dict):
            continue
        summaries.append(
            CartridgeSummary(
                cartridge_id=str(manifest.get("cartridge_id") or entry.name),
                name=str(manifest.get("name") or entry.name),
                version=str(manifest.get("version") or "0.0.0"),
                description=str(manifest.get("description") or ""),
                target_domain=_tuple_of_strings(manifest.get("target_domain")),
            )
        )
    return summaries


def clear_cache() -> None:
    """テスト用キャッシュクリア。"""
    _cached_load.cache_clear()


# ---------------------------------------------------------------------------
# Maturity resolution helper
# ---------------------------------------------------------------------------


def resolve_maturity(
    component: dict[str, Any],
    cartridge: DomainCartridge,
    curated_registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """コンポーネントに maturity_level / maturity_source を付与する。

    決定順序 (Issue #176 §11):

    1. curated registry に同名コンポーネントがあれば、その値を採用 (curated_registry)
    2. component_type.default_maturity_level があれば採用 (cartridge_default)
    3. origin == "paper" なら paper_claim にフォールバック (cartridge_default)
    4. それ以外は unknown
    """
    updated = dict(component)
    canonical = str(updated.get("name") or updated.get("component_id") or "").strip()
    registry = curated_registry or {}

    if canonical and canonical in registry:
        ref = registry[canonical] or {}
        level = ref.get("maturity_level")
        if level:
            updated["maturity_level"] = str(level)
            updated["maturity_source"] = "curated_registry"
            return updated

    component_type = str(updated.get("component_type") or "")
    default = cartridge.default_maturity_for(component_type)
    if default:
        updated["maturity_level"] = default
        updated["maturity_source"] = "cartridge_default"
        return updated

    if str(updated.get("origin") or "") == "paper":
        updated["maturity_level"] = "paper_claim"
        updated["maturity_source"] = "cartridge_default"
        return updated

    updated["maturity_level"] = "unknown"
    updated["maturity_source"] = "unknown"
    return updated
