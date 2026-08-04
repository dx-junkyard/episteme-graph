"""知識ランドスケープのガードレール（core 部分）。

正本: ``docs/features/knowledge_landscape_design.md`` §0（LS1〜LS10）・§11。

本ファイルは Wave A-1（migration 065 / ``core/landscape/`` / 骨格シード / 上限 /
監査定数）が担う構造的検査だけを持つ。agent ツリー・routes・フロント側の検査は
後続 Wave が同ファイルへ追記する（クラス単位で追記すること）。

検査観点:
  1. ``core/landscape/`` が FastAPI / routes / services を import しない（開発ルール2）
  2. ``store`` に DELETE 文が無い（P4: 行削除 API を作らない）
  3. ``store`` の INSERT は必ず ``status='inferred'``（LS3: AI は confirmed を書けない）
  4. migration 065 の CHECK 語彙 == ``core/landscape/schema.py`` の語彙（完全一致）
  5. DTO に ``weight`` / ``confidence`` の生値キーが無い（LS5・実データ試験）
  6. 宇宙物理骨格がパース・検証を通り、学習者に出せる凍結版である（§6.3・LS6）
  7. バンドルドメインのシード経路が atlas_store に存在する（§6.1）
  8. 監査 entity_type がカタログに登録されている（§9.3）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas as atlas_module  # noqa: E402
from core import atlas_store  # noqa: E402
from core import schema as core_schema  # noqa: E402
from core.landscape import projection, schema  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

LANDSCAPE_DIR = BACKEND / "core" / "landscape"
STORE_SRC = (LANDSCAPE_DIR / "store.py").read_text(encoding="utf-8")
MIGRATION_SQL = read_migration_sql(BACKEND, 65)
ASTRO_DIR = BACKEND / "atlas_domains" / "astrophysics"


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_core_landscape_does_not_import_fastapi_or_api_layer(self):
        assert_module_tree_does_not_import(
            LANDSCAPE_DIR, ["fastapi", "api.", "services", "starlette"]
        )

    def test_core_landscape_does_not_import_llm_sdk(self):
        """配置生成の LLM 呼び出しは agent 側（src/）+ builder 経由。
        schema / store / projection は非LLM（LS9: 読み取り API は非LLM）。"""
        for name in ("schema.py", "store.py", "projection.py"):
            assert_source_forbids(
                (LANDSCAPE_DIR / name).read_text(encoding="utf-8"),
                ["import openai", "from openai"],
                context=f"core/landscape/{name}",
            )

    def test_package_modules_exist(self):
        for name in ("__init__.py", "schema.py", "store.py", "projection.py"):
            assert (LANDSCAPE_DIR / name).is_file(), f"missing core/landscape/{name}"


# ---------------------------------------------------------------------------
# 2〜3. store の不変条項（P4 / LS3）
# ---------------------------------------------------------------------------


class TestStoreInvariants:
    def test_no_delete_statements(self):
        assert_source_forbids(
            STORE_SRC,
            ["DELETE FROM landscape_placements", "DELETE FROM", "delete("],
            context="core/landscape/store.py",
        )

    def test_no_delete_api_in_module_surface(self):
        import core.landscape.store as store_module

        offending = [
            name
            for name in dir(store_module)
            if not name.startswith("_") and ("delete" in name or "purge" in name)
        ]
        assert offending == [], f"行削除 API が生えている: {offending}"

    def test_insert_writes_inferred_status_only(self):
        src = extract_function_source(STORE_SRC, "supersede_and_insert_candidates")
        assert '"status": schema.STATUS_INFERRED' in src
        for forbidden in (
            '"status": schema.STATUS_CONFIRMED',
            "'status': schema.STATUS_CONFIRMED",
            '"status": "confirmed"',
        ):
            assert forbidden not in src

    def test_supersede_targets_only_inferred_rows(self):
        src = extract_function_source(STORE_SRC, "supersede_and_insert_candidates")
        assert '"supersedable": list(schema.SUPERSEDABLE_STATUSES)' in src
        assert schema.SUPERSEDABLE_STATUSES == (schema.STATUS_INFERRED,)

    def test_update_status_rejects_non_live_targets(self):
        src = extract_function_source(STORE_SRC, "update_status")
        assert "new_status not in schema.LIVE_STATUSES" in src

    def test_store_does_not_manage_transactions(self):
        assert_source_forbids(
            STORE_SRC,
            ["session.commit(", "session.rollback(", "session.close("],
            context="core/landscape/store.py",
        )


# ---------------------------------------------------------------------------
# 4. migration の CHECK 語彙 == schema.py の語彙
# ---------------------------------------------------------------------------


def _check_vocabulary(column: str) -> tuple[str, ...]:
    """migration 065 の ``CHECK (<column> IN ('a','b'))`` の語彙を抽出する。"""
    match = re.search(
        rf"CHECK \({column} IN\s*\(([^)]*)\)\)", MIGRATION_SQL, re.IGNORECASE | re.DOTALL
    )
    assert match, f"migration 065 に {column} の CHECK が無い"
    return tuple(v.strip().strip("'") for v in match.group(1).split(",") if v.strip())


class TestMigrationVocabularyMatchesSchema:
    def test_status_check_matches(self):
        assert set(_check_vocabulary("status")) == set(schema.STATUSES)

    def test_perspective_check_matches(self):
        assert set(_check_vocabulary("perspective")) == set(schema.PERSPECTIVES)

    def test_provenance_check_matches(self):
        assert set(_check_vocabulary("provenance")) == set(schema.PROVENANCES)

    def test_node_kind_check_matches(self):
        assert set(_check_vocabulary("node_kind")) == set(schema.NODE_KINDS)

    def test_document_fk_cascades(self):
        assert (
            "document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE"
            in MIGRATION_SQL
        )

    def test_weight_column_lives_in_db_only(self):
        """weight は DB に持つ（LS5 は「表示に出さない」＝列を持たないことではない）。"""
        assert "weight REAL NOT NULL DEFAULT 0.5" in MIGRATION_SQL

    def test_migration_is_idempotent_style(self):
        assert "CREATE TABLE IF NOT EXISTS landscape_placements" in MIGRATION_SQL
        assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", MIGRATION_SQL, re.MULTILINE)
        assert not re.search(r"\bADD CONSTRAINT\b", MIGRATION_SQL, re.IGNORECASE)
        assert "knowledge_landscape_design.md" in MIGRATION_SQL


# ---------------------------------------------------------------------------
# 5. DTO に生数値を出さない（LS5・実データ試験）
# ---------------------------------------------------------------------------


def _all_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            keys |= _all_keys(v)
    return keys


def _row(**kwargs) -> dict:
    row = {
        "id": "pl-1",
        "document_id": "11111111-1111-1111-1111-111111111111",
        "domain_key": "astrophysics",
        "skeleton_version": "2026.1",
        "node_id": "cosmology",
        "node_kind": schema.NODE_KIND_REGION,
        "perspective": schema.PERSPECTIVE_SUBJECT,
        "weight": 0.93,
        "reason": "理由",
        # 上流が confidence 付きの evidence を渡してきても DTO に漏らさない
        "evidence": [{"quote": "原文の逐語", "claim_id": "c1", "confidence": 0.88}],
        "status": schema.STATUS_INFERRED,
        "provenance": schema.PROVENANCE_LLM,
        "run_id": None,
        "created_by": "pipeline",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": "",
        "created_at": "2026-08-04T00:00:00+00:00",
        "updated_at": "2026-08-04T00:00:00+00:00",
    }
    row.update(kwargs)
    return row


def _index() -> dict:
    return projection.skeleton_node_index(
        {"astrophysics": atlas_module.load_skeleton(ASTRO_DIR / "skeleton.yaml")}
    )


class TestNoRawNumbersInDtos:
    def test_admin_dto_has_no_weight_or_confidence_keys(self):
        dto = projection.admin_placement_dto(_row(), _index())
        assert not _all_keys(dto) & set(schema.FORBIDDEN_NUMERIC_KEYS)
        assert dto["weight_label"] in schema.WEIGHT_LABELS.values()

    def test_learner_dto_has_no_weight_or_confidence_keys(self):
        dto = projection.learner_landscape_dto(
            [_row(), _row(status=schema.STATUS_CONFIRMED, node_id="cmb")],
            _index(),
            [{"domain_key": "astrophysics", "domain_name": "宇宙物理", "frozen_version": "2026.1"}],
            "astrophysics",
        )
        assert not _all_keys(dto) & set(schema.FORBIDDEN_NUMERIC_KEYS)

    def test_learner_dto_never_exposes_rejected_or_superseded(self):
        dto = projection.learner_landscape_dto(
            [
                _row(status=schema.STATUS_REJECTED),
                _row(status=schema.STATUS_SUPERSEDED, node_id="cmb"),
            ],
            _index(),
            [{"domain_key": "astrophysics"}],
            "astrophysics",
        )
        assert dto["documents"] == []

    def test_learner_dto_drops_claim_ids(self):
        dto = projection.learner_landscape_dto(
            [_row()], _index(), [{"domain_key": "astrophysics"}], "astrophysics"
        )
        evidence = dto["documents"][0]["placements"][0]["evidence"]
        assert evidence == [{"quote": "原文の逐語"}]

    def test_projection_module_has_no_raw_weight_key_literals(self):
        src = (LANDSCAPE_DIR / "projection.py").read_text(encoding="utf-8")
        assert '"weight":' not in src
        assert '"confidence":' not in src


# ---------------------------------------------------------------------------
# 6. 宇宙物理基準骨格（§6.3）
# ---------------------------------------------------------------------------


class TestAstrophysicsSkeletonSeed:
    @pytest.fixture(scope="class")
    def skeleton(self):
        return atlas_module.load_skeleton(ASTRO_DIR / "skeleton.yaml")

    def test_files_exist(self):
        assert (ASTRO_DIR / "skeleton.yaml").is_file()
        assert (ASTRO_DIR / "domain.json").is_file()

    def test_parses_validates_and_is_learner_visible(self, skeleton):
        report = atlas_module.validate_skeleton(skeleton)
        assert report.ok, [str(e) for e in report.errors]
        assert report.warnings == (), [str(w) for w in report.warnings]
        assert skeleton.is_learner_visible
        assert skeleton.cartridge == "astrophysics"
        assert skeleton.version == "2026.1"
        assert skeleton.reviewed_by == ("faculty:reference_import",)

    def test_ten_anchor_regions_within_limits(self, skeleton):
        assert [r.id for r in skeleton.regions] == [
            "cosmology",
            "galaxies",
            "ism_star_formation",
            "high_energy",
            "stars",
            "fundamental_physics",
            "sun_heliosphere",
            "planetary",
            "fundamental_astronomy",
            "observation_methods",
        ]
        assert len(skeleton.regions) <= atlas_module.MAX_REGIONS
        for region in skeleton.regions:
            assert region.concepts, f"{region.id} に代表概念が無い"
            assert len(region.concepts) <= atlas_module.MAX_CONCEPTS_PER_REGION

    def test_max_regions_admits_the_reference_map(self, skeleton):
        # §6.2 の上限引き上げ（7 → 12）が巻き戻ると 10 領域の骨格が invalid になる
        assert atlas_module.MAX_REGIONS >= len(skeleton.regions)

    def test_dark_components_are_marked_assumed_and_reviewed(self, skeleton):
        by_id = {c.id: c for r in skeleton.regions for c in r.concepts}
        assumed = {
            cid
            for cid, concept in by_id.items()
            if concept.seed_status and concept.seed_status.value == "assumed"
        }
        assert assumed == {
            "dark_matter",
            "dark_energy",
            "dark_sector_physics",
            "habitability",
        }
        for concept in by_id.values():
            assert concept.seed_status is not None
            # 未レビューの seed_status は学習者に出ないため、シードは reviewed で入れる
            assert concept.seed_status.reviewed is True

    def test_concept_layouts_are_region_relative(self, skeleton):
        for region in skeleton.regions:
            for concept in region.concepts:
                assert concept.layout is not None
                assert 0.0 <= concept.layout.x <= 1.0
                assert 0.0 <= concept.layout.y <= 1.0

    def test_cross_cutting_regions_are_connected(self, skeleton):
        pairs = {(e.from_id, e.to_id): e.kind for e in skeleton.edges}
        assert pairs[("cosmology", "galaxies")] == "adjacent"
        assert pairs[("cosmology", "fundamental_physics")] == "related"
        for target in (
            "cosmology",
            "galaxies",
            "ism_star_formation",
            "stars",
            "high_energy",
            "planetary",
            "sun_heliosphere",
        ):
            assert pairs[("observation_methods", target)] == "related"

    def test_skeleton_dump_is_deterministic(self, skeleton):
        reparsed = atlas_module.parse_skeleton(
            atlas_module.skeleton_to_dict(skeleton)
        )
        assert atlas_module.dump_skeleton(reparsed) == atlas_module.dump_skeleton(skeleton)


# ---------------------------------------------------------------------------
# 7. バンドルドメインのシード経路（§6.1）
# ---------------------------------------------------------------------------


class TestBundledDomainSeedPath:
    def test_constant_points_at_backend_atlas_domains(self):
        assert atlas_store.ATLAS_BUNDLED_DOMAINS_DIR == BACKEND / "atlas_domains"
        assert atlas_store.ATLAS_BUNDLED_DOMAINS_DIR.is_dir()

    def test_astrophysics_is_discoverable_as_a_bundled_domain(self):
        assert "astrophysics" in atlas_store._bundled_domain_keys()
        skeleton = atlas_store._bundled_skeleton("astrophysics")
        assert skeleton is not None and skeleton.version == "2026.1"

    def test_import_and_list_scan_bundled_domains(self):
        src = (BACKEND / "core" / "atlas_store.py").read_text(encoding="utf-8")
        for fn in ("import_bundled_skeletons", "list_domains"):
            assert "_bundled_domain_keys()" in extract_function_source(src, fn), (
                f"{fn} が atlas_domains/ を走査していない"
            )

    def test_domain_meta_seed_never_overwrites(self):
        src = (BACKEND / "core" / "atlas_store.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "_seed_bundled_domain_meta")
        assert "load_domain_meta(session, domain_key) is not None" in body
        assert "return" in body

    def test_dockerfile_ships_bundled_domains(self):
        dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
        assert "COPY backend/atlas_domains/ /app/atlas_domains/" in dockerfile


# ---------------------------------------------------------------------------
# 8. 監査 entity_type（§9.3）
# ---------------------------------------------------------------------------


class TestAuditCatalog:
    def test_landscape_placement_is_registered(self):
        assert core_schema.AUDIT_ENTITY_LANDSCAPE_PLACEMENT == "landscape_placement"
        assert (
            core_schema.AUDIT_ENTITY_LANDSCAPE_PLACEMENT
            in core_schema.AUDIT_ENTITY_TYPES
        )

    def test_catalog_has_no_duplicates(self):
        assert len(core_schema.AUDIT_ENTITY_TYPES) == len(
            set(core_schema.AUDIT_ENTITY_TYPES)
        )
