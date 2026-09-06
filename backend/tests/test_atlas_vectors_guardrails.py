"""分野マップのベクトル係留層（VA層）のガードレール。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §10（不変条項 VA1〜VA9 は §2）。

ここで**構造的に**守るもの:

1. core が FastAPI / routes を import しない（開発ルール2）
2. VA9 骨格（``atlas_skeletons``）への書き込み経路が存在しない
3. VA6 行削除なし（``replace_domain_embeddings`` の全置換 DELETE のみ設計明示の例外）
4. VA2 閾値・ラベルの正本は ``core/label_vocab.py`` のみ（重複定義の検出）
5. migration 074 が冪等・2表を含み、CHECK 語彙が ``schema.py`` の定数と一致する
6. 監査 entity_type / U層 feature の登録（``embedding:`` は scene 対象外）
7. VA3 学習者向けルートから本層への参照ゼロ
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_BACKEND = Path(__file__).resolve().parents[1]
_PKG = _BACKEND / "core" / "atlas_vectors"
_LABEL_VOCAB = _BACKEND / "core" / "label_vocab.py"

_PKG_FILES = sorted(_PKG.glob("*.py"))
_PKG_SOURCES = {p.name: p.read_text(encoding="utf-8") for p in _PKG_FILES}
_ALL_PKG_SOURCE = "\n".join(_PKG_SOURCES.values())

#: VA3 — 学習者向けルート（ここから本層に触れたら学習者起点の embedding 経路になる）。
_LEARNER_ROUTES = (
    "learning.py",
    "corpus.py",
    "cycle.py",
    "personal_map.py",
    "my_records.py",
)


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCoreIsFrameworkFree:
    def test_package_files_exist(self):
        names = set(_PKG_SOURCES)
        assert {"__init__.py", "schema.py", "store.py", "builder.py", "query.py",
                "annotate.py"} <= names

    def test_does_not_import_fastapi_or_routes(self):
        assert_module_tree_does_not_import(
            _PKG, ["fastapi", "api.routes", "api.main", "services"]
        )

    def test_query_and_schema_are_db_free(self):
        """純計算・語彙の2モジュールは DB にも触れない（テスト可能性の担保）。"""
        for name in ("schema.py", "query.py"):
            assert_source_forbids(
                _PKG_SOURCES[name],
                ["sqlalchemy", "get_session", "core.postgres"],
                context=f"core/atlas_vectors/{name}",
            )

    def test_query_does_not_touch_llm(self):
        """``query.py`` は純計算（設計書 §4 — LLM 非接触）。"""
        assert_source_forbids(
            _PKG_SOURCES["query.py"],
            ["core.llm", "generate_embeddings", "usage_context"],
            context="core/atlas_vectors/query.py",
        )


# ---------------------------------------------------------------------------
# 2. VA9 骨格へ書き込まない
# ---------------------------------------------------------------------------


class TestNoSkeletonWrites:
    def test_no_insert_or_update_targeting_atlas_skeletons(self):
        """``atlas_skeletons`` を対象にした INSERT/UPDATE が存在しない（VA9 / KN-3 / AB4）。"""
        offending: list[str] = []
        pattern = re.compile(
            r"(INSERT\s+INTO|UPDATE)\s+atlas_skeletons\b", re.IGNORECASE
        )
        for name, src in _PKG_SOURCES.items():
            for match in pattern.finditer(src):
                offending.append(f"{name}: {match.group(0)}")
        assert offending == [], f"VA9 violation — skeleton writes found: {offending}"

    def test_atlas_skeletons_appears_only_in_prose(self):
        """テーブル名が現れるのは説明文（コメント / docstring）だけで、SQL には無い。"""
        offending: list[str] = []
        for name, src in _PKG_SOURCES.items():
            for lineno, line in enumerate(src.splitlines(), start=1):
                if "atlas_skeletons" not in line:
                    continue
                stripped = line.strip()
                is_prose = stripped.startswith("#") or "``atlas_skeletons``" in line
                if not is_prose:
                    offending.append(f"{name}:{lineno}: {stripped}")
        assert offending == [], f"VA9 — non-prose mention of atlas_skeletons: {offending}"

    def test_skeleton_is_read_through_atlas_store(self):
        """骨格の読みは ``atlas_store.load_frozen_skeleton``（直読み禁止の規約）。"""
        src = _PKG_SOURCES["builder.py"]
        assert "atlas_store.load_frozen_skeleton" in src
        assert "learner_atlas_skeleton" not in src


# ---------------------------------------------------------------------------
# 3. VA6 行削除なし（全置換のみ例外）
# ---------------------------------------------------------------------------


class TestNoRowDeletion:
    def test_delete_from_only_inside_replace_domain_embeddings(self):
        """``DELETE FROM`` は ``store.replace_domain_embeddings`` の中だけ（設計書 §3 要点4）。

        アンカーベクトルは骨格と確定情報からの**導出データ**であり正本ではないので、
        (domain_key, skeleton_version) 単位の全置換だけが例外として許される
        （help_kb ``vector.py::sync_manual_vectors`` と同じ扱い）。
        """
        store_src = _PKG_SOURCES["store.py"]
        allowed = extract_function_source(store_src, "replace_domain_embeddings")
        assert "DELETE FROM atlas_anchor_embeddings" in allowed

        # 例外を宣言しているモジュール docstring（説明文）は検査対象から外す。
        module_doc = ast.get_docstring(ast.parse(store_src)) or ""
        rest = store_src.replace(allowed, "").replace(module_doc, "")
        assert_source_forbids(
            rest, ["DELETE FROM"], context="core/atlas_vectors/store.py (outside replace)"
        )

    def test_no_delete_in_other_modules(self):
        others = [p for p in _PKG_FILES if p.name != "store.py"]
        for path in others:
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["DELETE FROM", "DELETE  FROM"],
                context=f"core/atlas_vectors/{path.name}",
            )

    def test_no_delete_of_alias_rows(self):
        """別名は status 遷移のみ（``atlas_anchor_aliases`` の行を消さない）。"""
        assert "DELETE FROM atlas_anchor_aliases" not in _ALL_PKG_SOURCE

    def test_dismiss_is_a_status_transition(self):
        dismiss = extract_function_source(_PKG_SOURCES["store.py"], "dismiss_alias")
        assert "UPDATE atlas_anchor_aliases" in dismiss
        assert "status = 'dismissed'" in dismiss
        assert "DELETE" not in dismiss


# ---------------------------------------------------------------------------
# 4. VA2 閾値・ラベルの一元管理
# ---------------------------------------------------------------------------


class TestLabelsDeclaredOnce:
    def test_label_literals_absent_from_layer_source(self):
        """段階ラベルの文字列が ``core/atlas_vectors/`` に現れない（正本は label_vocab）。"""
        assert_module_tree_forbids(_PKG, ["かなり近い", "近い可能性"])
        assert_source_forbids(
            _ALL_PKG_SOURCE,
            ["かなり近い", "近い可能性"],
            context="core/atlas_vectors/*.py",
        )

    def test_thresholds_declared_once_in_label_vocab(self):
        src = _LABEL_VOCAB.read_text(encoding="utf-8")
        assert src.count("ANCHOR_NEARNESS_THRESHOLD_NEAR = ") == 1
        assert src.count("ANCHOR_NEARNESS_THRESHOLD_MID = ") == 1

    def test_layer_does_not_redeclare_thresholds(self):
        for name, src in _PKG_SOURCES.items():
            assert "ANCHOR_NEARNESS_THRESHOLD_NEAR =" not in src, name
            assert "ANCHOR_NEARNESS_THRESHOLD_MID =" not in src, name

    def test_scale_values_match_design(self):
        from core.label_vocab import (
            ANCHOR_NEARNESS_SCALE,
            ANCHOR_NEARNESS_THRESHOLD_MID,
            ANCHOR_NEARNESS_THRESHOLD_NEAR,
        )

        assert ANCHOR_NEARNESS_THRESHOLD_NEAR == 0.55
        assert ANCHOR_NEARNESS_THRESHOLD_MID == 0.40
        assert ANCHOR_NEARNESS_SCALE.thresholds == (0.55, 0.40)
        assert ANCHOR_NEARNESS_SCALE.labels == ("かなり近い", "近い可能性", "遠い")

    def test_scale_falls_back_to_most_cautious_label(self):
        """未測定・非数値は最も慎重な段階へ倒れる（GradedScale の不変条項）。"""
        from core.label_vocab import ANCHOR_NEARNESS_SCALE

        assert ANCHOR_NEARNESS_SCALE.label_for(None) == "遠い"

    def test_scale_exported(self):
        from core import label_vocab

        for name in (
            "ANCHOR_NEARNESS_SCALE",
            "ANCHOR_NEARNESS_THRESHOLD_NEAR",
            "ANCHOR_NEARNESS_THRESHOLD_MID",
        ):
            assert name in label_vocab.__all__


# ---------------------------------------------------------------------------
# 5. migration 074
# ---------------------------------------------------------------------------


_MIGRATION_SQL = read_migration_sql(_BACKEND, 74)


class TestMigration074:
    def test_creates_both_tables_idempotently(self):
        assert "CREATE TABLE IF NOT EXISTS atlas_anchor_embeddings" in _MIGRATION_SQL
        assert "CREATE TABLE IF NOT EXISTS atlas_anchor_aliases" in _MIGRATION_SQL

    def test_no_bare_create_table(self):
        bare = re.findall(
            r"CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)", _MIGRATION_SQL, re.IGNORECASE
        )
        assert bare == []

    def test_references_the_design_document(self):
        assert "atlas_vector_anchoring_design.md" in _MIGRATION_SQL

    def test_vector_dimension_matches_chunks(self):
        """VA5 — chunks と同じ 3072 次元（モデル切替は非対応）。"""
        assert "vector(3072)" in _MIGRATION_SQL

    def test_no_index_created(self):
        """行数が小さいので ANN/通常 index を作らない（設計書 §3 要点1）。"""
        assert "CREATE INDEX" not in _MIGRATION_SQL.upper()

    def test_no_foreign_keys(self):
        """骨格は JSONB スナップショットなので node_id に FK を張らない（要点2）。"""
        assert "REFERENCES" not in _MIGRATION_SQL.upper()

    def test_no_seed_inserts(self):
        assert "INSERT INTO" not in _MIGRATION_SQL.upper()

    def test_unique_constraints(self):
        assert "UNIQUE (domain_key, skeleton_version, node_id)" in _MIGRATION_SQL
        assert "UNIQUE (domain_key, node_id, normalized_alias)" in _MIGRATION_SQL

    @staticmethod
    def _check_vocab(column: str) -> tuple[str, ...]:
        match = re.search(
            rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)",
            _MIGRATION_SQL,
            re.IGNORECASE,
        )
        assert match, f"no CHECK vocabulary found for column {column}"
        return tuple(
            part.strip().strip("'") for part in match.group(1).split(",") if part.strip()
        )

    def test_node_kind_vocab_matches_schema(self):
        from core.atlas_vectors import schema

        assert set(self._check_vocab("node_kind")) == set(schema.NODE_KINDS)

    def test_alias_status_vocab_matches_schema(self):
        from core.atlas_vectors import schema

        assert set(self._check_vocab("status")) == set(schema.ALIAS_STATUSES)

    def test_alias_source_vocab_matches_schema(self):
        from core.atlas_vectors import schema

        assert set(self._check_vocab("source")) == set(schema.ALIAS_SOURCES)


# ---------------------------------------------------------------------------
# 6. 監査語彙 / U層 feature
# ---------------------------------------------------------------------------


class TestAuditAndUsageRegistration:
    def test_audit_entity_in_catalog(self):
        from core.schema import AUDIT_ENTITY_ATLAS_VECTOR, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_ATLAS_VECTOR == "atlas_vector"
        assert AUDIT_ENTITY_ATLAS_VECTOR in AUDIT_ENTITY_TYPES

    def test_audit_actions_are_fixed(self):
        from core.atlas_vectors import schema

        assert schema.AUDIT_ACTIONS == (
            "vectors_refresh", "alias_register", "alias_dismiss"
        )

    def test_usage_feature_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "embedding:atlas_anchors" in KNOWN_FEATURES

    def test_usage_feature_has_no_model_scene(self):
        """VA5 — ``embedding:`` プレフィックスにより scene 対象外（モデル切替なし）。"""
        from core.llm_policy import scene_for_feature

        assert scene_for_feature("embedding:atlas_anchors") is None

    def test_embedding_call_is_attributed(self):
        """埋め込みは必ず ``usage_context("embedding:atlas_anchors")`` 配下で行う（U3）。"""
        src = _PKG_SOURCES["builder.py"]
        embed = extract_function_source(src, "embed_texts")
        assert 'usage_context("embedding:atlas_anchors")' in embed
        assert "generate_embeddings" in embed

    def test_only_builder_touches_llm(self):
        """LLM（embedding）接触点は ``builder.py`` の1箇所だけ。"""
        for name, src in _PKG_SOURCES.items():
            if name == "builder.py":
                continue
            assert "generate_embeddings" not in src, name


# ---------------------------------------------------------------------------
# 7. VA3 学習者経路からの参照ゼロ
# ---------------------------------------------------------------------------


class TestLearnerRoutesNeverTouchThisLayer:
    def test_learner_routes_do_not_reference_atlas_vectors(self):
        routes_dir = _BACKEND / "api" / "routes"
        offending: list[str] = []
        for name in _LEARNER_ROUTES:
            path = routes_dir / name
            if not path.is_file():
                continue
            if "atlas_vectors" in path.read_text(encoding="utf-8"):
                offending.append(name)
        assert offending == [], (
            "VA3 violation — learner-facing routes must not reach the vector layer: "
            f"{offending}"
        )

    def test_learner_route_files_exist(self):
        """参照ゼロ検査が空振りしていないことの確認（ファイルの実在）。"""
        routes_dir = _BACKEND / "api" / "routes"
        present = [n for n in _LEARNER_ROUTES if (routes_dir / n).is_file()]
        assert len(present) >= 3, f"learner routes unexpectedly missing: {present}"


# ---------------------------------------------------------------------------
# 8. VA1 / VA4 の構造（候補生成器であること・fail-soft）
# ---------------------------------------------------------------------------


class TestCandidateOnlyAndFailSoft:
    def test_annotate_never_writes(self):
        """近傍注記は読み時導出で保存しない（v1 に alias candidate 行は無い — VA1）。"""
        src = _PKG_SOURCES["annotate.py"]
        assert_source_forbids(
            src,
            ["INSERT INTO", "UPDATE ", "DELETE FROM", "commit()"],
            context="core/atlas_vectors/annotate.py",
        )

    def test_annotate_declares_fail_soft_returns(self):
        """例外を送出せず入力をそのまま返す構造（``raise`` を持たない）。"""
        annotate_fn = extract_function_source(
            _PKG_SOURCES["annotate.py"], "annotate_gap_clusters"
        )
        assert "raise" not in annotate_fn

    def test_alias_status_default_is_confirmed(self):
        """登録＝教員の確定操作（candidate 状態は v1 に存在しない — 設計書 §3 要点5）。"""
        squashed = " ".join(_MIGRATION_SQL.split())
        assert "status TEXT NOT NULL DEFAULT 'confirmed'" in squashed
        assert "'candidate'" not in _MIGRATION_SQL

    def test_alias_registration_requires_attribution(self):
        upsert = extract_function_source(_PKG_SOURCES["store.py"], "upsert_alias")
        assert "user_id is required" in upsert
