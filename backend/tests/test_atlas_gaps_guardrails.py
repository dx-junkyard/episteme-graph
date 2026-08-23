"""カテゴリギャップ候補のガードレール（core / migration 部分）。

正本: ``docs/features/category_gap_candidates_design.md`` §5.7（§2 の不変条項の写像）。

本ファイルは データ層 Wave（migration 066 / ``core/atlas_gaps/`` / builder 配線 /
監査定数 / 設定）と 管理 API Wave（``api/routes/atlas_gaps.py`` / freeze ゲート /
from-frozen）が担う構造的検査を持つ。フロント側の検査は後続 Wave が同ファイルへ
追記する（クラス単位で追記すること）。

検査観点:
  1. ``core/atlas_gaps/`` が FastAPI / LLM SDK / API 層を import しない（開発ルール2）
  2. ``store`` に DELETE 文が無い・公開面に delete / purge 名が無い（P4 / AB3）
  3. migration 066 の CHECK 語彙 == ``core/atlas_gaps/schema.py`` の語彙（完全一致）+
     冪等スタイル + 設計書への参照
  4. **``atlas_skeletons`` への INSERT / UPDATE が gap 系コード・gap ルートに無い**
     （配置層から骨格を書き換える経路の不在証明 — LS7 の構造的保証）
  5. 監査 entity_type がカタログに登録されている・重複が無い（§5.7）
  6. 見送り（dismiss）は理由必須（§5.4。store と route の両方）
  7. 禁止語彙（欠陥語彙・督促語彙）が利用者向け文言に無い（LS1 / AB1 / G6）
  8. patch の op は ``add`` のみ（§5.5 / 合意事項8）
  9. builder 配線: gap の保存が配置と同一トランザクションで、上限・監査を通る（§3-3）
 10. 学習者向け DTO にカテゴリギャップ候補の語彙が漏れない（§5.6）
 11. 生成プロンプトの捏造ガード文言が存在する（§5.1）
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import schema as core_schema  # noqa: E402
from core.atlas_gaps import patching, schema  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

GAPS_DIR = BACKEND / "core" / "atlas_gaps"
STORE_SRC = (GAPS_DIR / "store.py").read_text(encoding="utf-8")
PATCHING_SRC = (GAPS_DIR / "patching.py").read_text(encoding="utf-8")
BUILDER_SRC = (BACKEND / "core" / "landscape" / "builder.py").read_text(encoding="utf-8")
MIGRATION_SQL = read_migration_sql(BACKEND, 66)

ROUTES_DIR = BACKEND / "api" / "routes"
GAP_ROUTE_SRC = (ROUTES_DIR / "atlas_gaps.py").read_text(encoding="utf-8")
ATLAS_ROUTE_SRC = (ROUTES_DIR / "atlas.py").read_text(encoding="utf-8")
LANDSCAPE_ROUTE_SRC = (ROUTES_DIR / "landscape.py").read_text(encoding="utf-8")
ATLAS_VIEW_ROUTE_SRC = (ROUTES_DIR / "atlas_view.py").read_text(encoding="utf-8")
PLACEMENT_PROMPT_SRC = (
    ROOT / "src" / "episteme_graph" / "agents" / "landscape_placement" / "prompt.py"
).read_text(encoding="utf-8")

_HAS_FASTAPI = True
try:  # pragma: no cover - 環境差の分岐
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (route guardrails need the app)"
)


def _function_code(src: str, name: str) -> str:
    """関数の**実コード**（docstring・コメントを除く）を文字列で返す。

    語彙リントを「説明文にその語を書いてはいけない」ではなく「実装がその語を
    扱っていない」の検査にするために使う（docstring には「gap の語彙は載せない」と
    書けるべきである）。
    """
    tree = ast.parse(src)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_does_not_import_fastapi_or_api_layer(self):
        assert_module_tree_does_not_import(
            GAPS_DIR, ["fastapi", "api.", "services", "starlette"]
        )

    def test_does_not_import_llm_sdk(self):
        """候補生成の LLM は landscape_placement の同一コールに相乗り（追加コール
        なし）。信号の保存・導出・patch 生成はすべて非LLM（LS9）。"""
        assert_module_tree_does_not_import(
            GAPS_DIR, ["openai", "google.generativeai", "anthropic", "vertexai"]
        )

    def test_package_modules_exist(self):
        for name in ("__init__.py", "schema.py", "store.py", "patching.py"):
            assert (GAPS_DIR / name).is_file(), f"missing core/atlas_gaps/{name}"

    def test_store_does_not_manage_transactions(self):
        assert_source_forbids(
            STORE_SRC,
            ["session.commit(", "session.rollback(", "session.close("],
            context="core/atlas_gaps/store.py",
        )

    def test_no_bind_param_direct_cast(self):
        """``:name::type`` は SQLAlchemy がバインドを検出できない既知の罠。

        正しくは ``CAST(:name AS type)``（D層の同名ガードレールと同じ検査）。
        """
        pattern = re.compile(r":[a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z_]")
        match = pattern.search(STORE_SRC)
        assert match is None, f"found `:name::type` bind-cast anti-pattern: {match}"


# ---------------------------------------------------------------------------
# 2. 行を消さない（P4 / AB3）
# ---------------------------------------------------------------------------


class TestNothingIsDeleted:
    def test_no_delete_statements(self):
        assert_source_forbids(
            STORE_SRC,
            ["DELETE FROM", "delete(", "DROP TABLE", "TRUNCATE"],
            context="core/atlas_gaps/store.py",
        )

    def test_no_delete_api_in_module_surface(self):
        import core.atlas_gaps.store as store_module

        offending = [
            name
            for name in dir(store_module)
            if not name.startswith("_") and ("delete" in name or "purge" in name)
        ]
        assert offending == [], f"行削除 API が生えている: {offending}"

    def test_dismissal_is_a_status_transition(self):
        """見送りも復帰も行削除ではなく status 遷移で表す。"""
        restore = extract_function_source(STORE_SRC, "restore_decision")
        assert "UPDATE atlas_gap_decisions" in restore
        assert "DELETE" not in restore.upper()
        assert "schema.DECISION_STATUS_CANDIDATE" in restore

    def test_reanalysis_supersedes_instead_of_deleting(self):
        src = extract_function_source(STORE_SRC, "record_signals")
        assert "SET status = :superseded" in src
        assert '"supersedable": list(schema.SUPERSEDABLE_SIGNAL_STATUSES)' in src
        assert schema.SUPERSEDABLE_SIGNAL_STATUSES == (schema.SIGNAL_STATUS_ACTIVE,)


# ---------------------------------------------------------------------------
# 3. migration の CHECK 語彙 == schema.py の語彙
# ---------------------------------------------------------------------------


def _check_vocabulary(column: str) -> tuple[str, ...]:
    """migration 066 の ``CHECK (<column> IN ('a','b'))`` の語彙を抽出する。"""
    match = re.search(
        rf"CHECK \({column} IN\s*\(([^)]*)\)\)", MIGRATION_SQL, re.IGNORECASE | re.DOTALL
    )
    assert match, f"migration 066 に {column} の CHECK が無い"
    return tuple(v.strip().strip("'") for v in match.group(1).split(",") if v.strip())


class TestMigrationVocabularyMatchesSchema:
    def test_layer_check_matches(self):
        assert set(_check_vocabulary("layer")) == set(schema.GAP_LAYERS)

    def test_signal_status_check_matches(self):
        assert set(_check_vocabulary("status")) == set(schema.SIGNAL_STATUSES)

    def test_decision_status_check_matches(self):
        match = re.search(
            r"CONSTRAINT atlas_gap_decisions_status_check CHECK \(status IN\s*\(([^)]*)\)\)",
            MIGRATION_SQL,
            re.IGNORECASE | re.DOTALL,
        )
        assert match, "migration 066 に atlas_gap_decisions.status の CHECK が無い"
        vocabulary = {v.strip().strip("'") for v in match.group(1).split(",") if v.strip()}
        assert vocabulary == set(schema.DECISION_STATUSES)

    def test_signals_cascade_on_document_delete(self):
        assert (
            "document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE"
            in MIGRATION_SQL
        )

    def test_decisions_have_no_document_fk(self):
        """判断はコーパス横断の共同財行（1論文が消えても残る・§5.2）。"""
        decisions = MIGRATION_SQL.split("CREATE TABLE IF NOT EXISTS atlas_gap_decisions")[1]
        assert "REFERENCES documents" not in decisions

    def test_cluster_key_is_unique(self):
        assert re.search(r"cluster_key\s+TEXT\s+NOT NULL\s+UNIQUE", MIGRATION_SQL)

    def test_confidence_lives_in_db_only(self):
        """LS5 は「表示に出さない」であって「列を持たない」ではない。"""
        assert "confidence DOUBLE PRECISION" in MIGRATION_SQL

    def test_acceptance_and_application_are_separate_columns(self):
        assert "draft_node_id TEXT NOT NULL DEFAULT ''" in MIGRATION_SQL
        assert "applied_version TEXT NOT NULL DEFAULT ''" in MIGRATION_SQL

    def test_migration_is_idempotent_style(self):
        assert "CREATE TABLE IF NOT EXISTS landscape_gap_signals" in MIGRATION_SQL
        assert "CREATE TABLE IF NOT EXISTS atlas_gap_decisions" in MIGRATION_SQL
        assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", MIGRATION_SQL, re.MULTILINE)
        assert not re.search(r"\bADD CONSTRAINT\b", MIGRATION_SQL, re.IGNORECASE)
        assert not re.search(
            r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!IF NOT EXISTS)", MIGRATION_SQL
        )

    def test_migration_references_the_design_document(self):
        assert "category_gap_candidates_design.md" in MIGRATION_SQL


# ---------------------------------------------------------------------------
# 4. 骨格を書き換える経路の不在証明（LS7）
# ---------------------------------------------------------------------------


class TestNoSkeletonWrites:
    _WRITE = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+atlas_skeletons\b", re.IGNORECASE
    )

    def test_no_write_statements_against_atlas_skeletons(self):
        offending = []
        for path in sorted(GAPS_DIR.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            if self._WRITE.search(src):
                offending.append(str(path))
        assert offending == [], f"gap 系コードから骨格へ書き込んでいる: {offending}"

    def test_gap_modules_do_not_call_the_skeleton_writers(self):
        """``atlas_store`` の draft 保存・凍結 API を呼ばない（適用は教員の PUT）。"""
        for path in sorted(GAPS_DIR.rglob("*.py")):
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["save_draft(", "insert_frozen(", "freeze_skeleton(", "delete_draft("],
                context=str(path),
            )

    def test_patching_returns_a_patch_and_touches_no_db(self):
        assert_source_forbids(
            PATCHING_SRC,
            ["sqlalchemy", "get_session", "session.execute"],
            context="core/atlas_gaps/patching.py",
        )

    def test_gap_route_never_writes_the_skeleton_table(self):
        """gap ルートは骨格を書かない（適用は教員の既存 PUT draft のみ）。"""
        assert self._WRITE.search(GAP_ROUTE_SRC) is None
        assert_source_forbids(
            GAP_ROUTE_SRC,
            ["save_draft(", "insert_frozen(", "delete_draft(", "freeze_skeleton("],
            context="api/routes/atlas_gaps.py",
        )

    def test_gap_route_has_no_delete_or_purge_surface(self):
        """P4: 行を消す API を生やさない（見送りは status 遷移）。"""
        assert_source_forbids(
            GAP_ROUTE_SRC,
            ["DELETE FROM", "@router.delete", "purge"],
            context="api/routes/atlas_gaps.py",
        )


# ---------------------------------------------------------------------------
# 5. 監査（§5.7）
# ---------------------------------------------------------------------------


class TestAuditCatalog:
    def test_category_gap_is_registered(self):
        assert core_schema.AUDIT_ENTITY_CATEGORY_GAP == "category_gap"
        assert core_schema.AUDIT_ENTITY_CATEGORY_GAP in core_schema.AUDIT_ENTITY_TYPES

    def test_catalog_has_no_duplicates(self):
        assert len(core_schema.AUDIT_ENTITY_TYPES) == len(
            set(core_schema.AUDIT_ENTITY_TYPES)
        )

    def test_detect_audit_uses_the_catalog_constant(self):
        assert "AUDIT_ENTITY_CATEGORY_GAP" in STORE_SRC
        src = extract_function_source(STORE_SRC, "record_detect_audit")
        assert "theory_review_events" in src
        assert "schema.AUDIT_ACTION_DETECT" in src
        # AI 由来の記帳に人間の帰属を偽装しない
        assert '"changed_by": None' in src

    def test_action_vocabulary_matches_the_design(self):
        assert set(schema.AUDIT_ACTIONS) == {
            "detect",
            "accept",
            "dismiss",
            "restore",
            "merge",
            "incorporate",
        }


# ---------------------------------------------------------------------------
# 6. 見送りは理由必須（§5.4）
# ---------------------------------------------------------------------------


class TestDismissRequiresAReason:
    def test_store_raises_without_a_note(self):
        from core.atlas_gaps import store

        class _Session:
            def __init__(self):
                self.calls = 0

            def execute(self, *_a, **_k):  # pragma: no cover - must not be reached
                self.calls += 1
                raise AssertionError("検証前に SQL を発行してはならない")

        session = _Session()
        with pytest.raises(ValueError):
            store.upsert_decision(
                session,
                cluster_key="gap|astrophysics|cosmology|cosmic web",
                status=schema.DECISION_STATUS_DISMISSED,
                decided_by="99999999-9999-9999-9999-999999999999",
            )
        assert session.calls == 0

    def test_vocabulary_declares_the_requirement(self):
        assert schema.DECISION_STATUS_DISMISSED in schema.REVIEW_NOTE_REQUIRED_STATUSES
        assert schema.DECISION_STATUS_ACCEPTED not in schema.REVIEW_NOTE_REQUIRED_STATUSES

    @_skip_no_fastapi
    def test_route_maps_the_missing_reason_to_422_without_writing(self):
        """route も同じ規則を守る（ValueError → 422・書き込み SQL を発行しない）。"""
        from fastapi import HTTPException

        import routes.atlas_gaps as gap_routes

        class _Result:
            def fetchone(self):
                return None

            def fetchall(self):
                return []

        class _Session:
            def __init__(self):
                self.writes: list[str] = []
                self.rollbacks = 0
                self.commits = 0

            def execute(self, stmt, params=None):
                sql = " ".join(str(stmt).split())
                if sql.startswith(("INSERT", "UPDATE", "DELETE")):
                    self.writes.append(sql)
                return _Result()

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                pass

        session = _Session()
        original = gap_routes._session
        gap_routes._session = lambda: session
        try:
            with pytest.raises(HTTPException) as excinfo:
                gap_routes.decide_atlas_gap_candidate(
                    "astrophysics",
                    gap_routes.DecideGapCandidateRequest(
                        cluster_key=schema.build_cluster_key(
                            "astrophysics", "cosmology", "Cosmic Web"
                        ),
                        action=gap_routes.ACTION_DISMISS,
                    ),
                    current_user={"id": "99999999-9999-9999-9999-999999999999"},
                )
        finally:
            gap_routes._session = original
        assert excinfo.value.status_code == 422
        assert session.writes == []
        assert session.commits == 0


# ---------------------------------------------------------------------------
# 7. 文言リント（LS1 / AB1 / G6）
# ---------------------------------------------------------------------------


class TestNoDefectOrUrgingLanguage:
    #: コメント・docstring を含めどこにも現れてはいけない語（KN-1 / LS5 / AB1）。
    HARD_BANNED = (
        "カバー率",
        "網羅率",
        "踏破率",
        "地図の穴",
        "空白ダッシュボード",
        "埋めましょう",
        "埋めてください",
        "疑え",
        "早急",
        "至急",
    )

    #: 利用者向け文言（ラベル・事実文・エラーメッセージ）に現れてはいけない語。
    BANNED_IN_USER_TEXT = HARD_BANNED + (
        "穴",
        "不足",
        "未整備",
        "欠落",
        "べきです",
        "しなければなりません",
        "警告",
        "注意",
        "残念",
    )

    def test_source_has_no_hard_banned_terms(self):
        for path in sorted(GAPS_DIR.rglob("*.py")):
            assert_source_forbids(
                path.read_text(encoding="utf-8"), self.HARD_BANNED, context=str(path)
            )

    def test_migration_has_no_hard_banned_terms(self):
        assert_source_forbids(
            MIGRATION_SQL, self.HARD_BANNED, context="backend/db/066_*.sql"
        )

    def _user_facing_strings(self) -> list[str]:
        texts = list(schema.DECISION_STATUS_LABELS.values())
        texts += list(schema.GAP_LAYER_LABELS.values())
        texts += list(schema.CONFIDENCE_LABELS)
        draft = {
            "regions": [
                {"id": "cosmology", "label": "宇宙論", "layout": {"x": 0.02, "y": 0.03,
                 "w": 0.23, "h": 0.28}, "concepts": []}
            ]
        }
        texts.append(
            patching.build_gap_patch(
                draft, layer="concept", parent_region_id="cosmology",
                proposed_label="Cosmic Web",
            )["summary"]
        )
        texts.append(
            patching.build_gap_patch(draft, layer="region", proposed_label="重力波")[
                "summary"
            ]
        )
        full = {
            "regions": [
                {
                    "id": "cosmology",
                    "label": "宇宙論",
                    "concepts": [{"id": f"c{i}", "label": f"概念{i}"} for i in range(6)],
                }
            ]
        }
        try:
            patching.build_gap_patch(
                full, layer="concept", parent_region_id="cosmology",
                proposed_label="Cosmic Web",
            )
        except patching.SkeletonCapacityError as exc:
            texts.append(str(exc))
        return texts

    def test_user_facing_strings_are_factual(self):
        for text in self._user_facing_strings():
            assert_source_forbids(text, self.BANNED_IN_USER_TEXT, context=repr(text))

    def test_gap_route_source_has_no_hard_banned_terms(self):
        assert_source_forbids(
            GAP_ROUTE_SRC, self.HARD_BANNED, context="api/routes/atlas_gaps.py"
        )

    @_skip_no_fastapi
    def test_gap_route_detail_sentences_are_factual(self):
        """route が返す事実文（``_DETAIL_*``）に欠陥語彙・督促語彙を入れない。"""
        import routes.atlas_gaps as gap_routes

        details = [
            value
            for name, value in vars(gap_routes).items()
            if name.startswith("_DETAIL_") and isinstance(value, str)
        ]
        assert details, "route の事実文定数が見つからない（定数化を崩さないこと）"
        for text in details:
            assert_source_forbids(text, self.BANNED_IN_USER_TEXT, context=repr(text))
            # LS5: 事実文に件数・上限値を書かない（骨格の上限文言は patching が持つ）。
            assert not any(ch.isdigit() for ch in text), text

    def test_freeze_impact_facts_are_factual_and_have_no_numbers(self):
        from core import atlas_lifecycle

        for text in (
            atlas_lifecycle.FACT_REMOVED_NODES_HIDE_LEARNER_TRACES,
            atlas_lifecycle.FACT_ADDED_NODES_NEED_REANALYSIS,
        ):
            assert_source_forbids(text, self.BANNED_IN_USER_TEXT, context=repr(text))
            assert not any(ch.isdigit() for ch in text), text


# ---------------------------------------------------------------------------
# 8. patch は add のみ（§5.5）
# ---------------------------------------------------------------------------


class TestAdditiveOnlyPatches:
    def test_no_remove_or_replace_op_literals(self):
        assert_source_forbids(
            PATCHING_SRC,
            ['"remove"', "'remove'", '"replace"', "'replace'", '"move"', '"copy"'],
            context="core/atlas_gaps/patching.py",
        )

    def test_generated_ops_are_adds(self):
        draft = {
            "regions": [
                {"id": "cosmology", "label": "宇宙論", "layout": {"x": 0.02, "y": 0.03,
                 "w": 0.23, "h": 0.28}, "concepts": []}
            ]
        }
        for kwargs in (
            {"layer": "region", "proposed_label": "重力波天文学"},
            {
                "layer": "concept",
                "parent_region_id": "cosmology",
                "proposed_label": "Cosmic Web",
            },
        ):
            result = patching.build_gap_patch(draft, **kwargs)
            assert {op["op"] for op in result["patch"]} == {"add"}


# ---------------------------------------------------------------------------
# 9. builder 配線（§3-3: 保存は _persist と同一トランザクション）
# ---------------------------------------------------------------------------


class TestBuilderWiring:
    def test_gaps_are_read_defensively_from_the_agent_result(self):
        src = extract_function_source(BUILDER_SRC, "build_and_store_placements")
        assert 'getattr(result, "category_gaps", None)' in src

    def test_gaps_are_persisted_in_the_placement_transaction(self):
        # "_persist(" と書くのは `_persist_gaps` を先に拾わせないため（素朴抽出の制約）
        persist = extract_function_source(BUILDER_SRC, "_persist(")
        assert "_persist_gaps(" in persist
        assert "session.commit()" in persist
        # gap 用に別セッションを開かない（同一トランザクション）
        gaps = extract_function_source(BUILDER_SRC, "_persist_gaps")
        assert "get_session" not in gaps
        assert "record_signals(" in gaps
        assert "record_detect_audit(" in gaps

    def test_limit_comes_from_settings(self):
        from core.config import get_settings

        assert get_settings().landscape_gap_max_per_document >= 0
        assert "landscape_gap_max_per_document" in BUILDER_SRC
        assert "max_gaps_per_document" in BUILDER_SRC

    def test_builder_still_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(
            BACKEND / "core" / "landscape", ["fastapi", "api.", "services", "starlette"]
        )

    def test_persist_writes_signals_and_audit_in_one_transaction(self, monkeypatch):
        """実際に ``_persist`` を通し、信号と監査が**同じセッション**で1回 commit
        されることを確かめる（配置ゼロでも gap は保存される — LS10）。"""
        from core.landscape import builder
        from tests.test_atlas_gaps_store import FakeGapSession, _Gap

        session = FakeGapSession()
        monkeypatch.setattr("core.postgres.get_session", lambda: session)

        out = builder._persist(
            "11111111-1111-1111-1111-111111111111",
            "44444444-4444-4444-4444-444444444444",
            [],  # 配置ゼロ
            gaps=[_Gap()],
            skeleton_versions={"astrophysics": "2026.1"},
            max_gaps=3,
        )

        assert out["gap_signals_created"] == 1
        assert len(session.signals) == 1
        assert session.signals[0]["status"] == schema.SIGNAL_STATUS_ACTIVE
        assert len(session.audits) == 1
        assert session.audits[0]["entity_type"] == core_schema.AUDIT_ENTITY_CATEGORY_GAP
        assert (session.commits, session.rollbacks) == (1, 0)
        assert session.closed is True

    def test_persist_skips_the_audit_when_nothing_was_stored(self, monkeypatch):
        from core.landscape import builder
        from tests.test_atlas_gaps_store import FakeGapSession, _Gap

        session = FakeGapSession()
        monkeypatch.setattr("core.postgres.get_session", lambda: session)

        out = builder._persist(
            "11111111-1111-1111-1111-111111111111",
            None,
            [],
            gaps=[_Gap(layer="galaxy")],  # 語彙外 → drop
            skeleton_versions={},
            max_gaps=3,
        )

        assert out["gap_signals_created"] == 0
        assert session.signals == [] and session.audits == []
        assert session.calls == []


# ---------------------------------------------------------------------------
# 10. 学習者側への非漏洩（§5.6）
# ---------------------------------------------------------------------------


class TestLearnerSurfaceHasNoCandidateVocabulary:
    """共有候補・教員の判断・集約は学習者に出さない（§5.6 / KN-1）。

    「gap」という語そのものではなく、**カテゴリギャップ候補の語彙**（判断テーブル・
    信号テーブル・cluster キー・候補ラベル）を検査する — ``atlas_view.py`` の
    ``gap2`` / ``gap3`` のような過去の設計課題番号は本機能とは無関係のため。
    """

    CANDIDATE_VOCABULARY = (
        "atlas_gaps",
        "atlas_gap_decisions",
        "landscape_gap_signals",
        "gap_signals",
        "gap-candidates",
        "cluster_key",
        "proposed_label",
    )

    def test_learner_landscape_dto_builder_has_no_candidate_vocabulary(self):
        code = _function_code(LANDSCAPE_ROUTE_SRC, "get_course_landscape")
        assert_source_forbids(
            code, self.CANDIDATE_VOCABULARY, context="get_course_landscape (実コード)"
        )

    def test_learner_atlas_view_has_no_candidate_vocabulary(self):
        assert_source_forbids(
            ATLAS_VIEW_ROUTE_SRC,
            self.CANDIDATE_VOCABULARY,
            context="api/routes/atlas_view.py",
        )

    def test_only_the_admin_route_reads_gap_signals_in_landscape(self):
        """landscape 側で gap 信号を読むのは教員向けの案内一行のためだけ。"""
        helper = _function_code(LANDSCAPE_ROUTE_SRC, "_gap_signal_domains")
        assert "list_active_signals" in helper
        # 件数を返さない（真偽値の材料として domain_key の集合だけを返す — LS5）。
        assert "len(" not in helper
        admin = _function_code(LANDSCAPE_ROUTE_SRC, "list_document_landscape_placements")
        assert "_gap_signal_domains(" in admin
        assert "gap_signals_recorded" in admin

    def test_gap_route_is_admin_only(self):
        """gap ルートは教員以上のみ（学習者ルーターに生やさない）。"""
        assert "_require_teacher" in GAP_ROUTE_SRC
        assert_source_forbids(
            GAP_ROUTE_SRC,
            ["_get_current_user", "learning_router", "/api/learning"],
            context="api/routes/atlas_gaps.py",
        )


# ---------------------------------------------------------------------------
# 11. 生成プロンプトの捏造ガード（§5.1）
# ---------------------------------------------------------------------------


class TestPromptFabricationGuards:
    def test_prompt_forbids_relabelling_existing_concepts(self):
        assert (
            "既存の概念の言い換えを新しい概念として申告しないでください"
            in PLACEMENT_PROMPT_SRC
        )

    def test_prompt_keeps_placements_as_the_primary_task(self):
        assert "配置（placements）の判定を最優先" in PLACEMENT_PROMPT_SRC


# ---------------------------------------------------------------------------
# 12. 骨格への反映の配線（§5.5: from-frozen → 教員の PUT → 凍結の刻印）
# ---------------------------------------------------------------------------


class TestSkeletonApplicationWiring:
    """「地図が育つ」経路が決定論・人間主導のままであることを構造的に固定する。"""

    def test_freeze_checks_pending_candidates_before_freezing(self):
        freeze = _function_code(ATLAS_ROUTE_SRC, "freeze_atlas_skeleton")
        assert "_pending_gap_candidates(" in freeze
        assert "pending_labels" in freeze
        # 件数ではなくラベルの列挙で示す（LS5）。
        assert "len(pending_gaps" not in freeze

    def test_pending_check_is_fail_open(self):
        """候補機構の照会失敗で凍結という主要操作を止めない。"""
        helper = _function_code(ATLAS_ROUTE_SRC, "_pending_gap_candidates")
        assert "list_pending_for_freeze" in helper
        assert "return []" in helper

    def test_applied_version_is_stamped_inside_the_freeze_transaction(self):
        freeze = _function_code(ATLAS_ROUTE_SRC, "freeze_atlas_skeleton")
        insert_at = freeze.index("insert_frozen")
        stamp_at = freeze.index("stamp_applied_versions")
        commit_at = freeze.index("session.commit()", insert_at)
        assert insert_at < stamp_at < commit_at, (
            "刻印は凍結と同一トランザクション内で行う（採用と反映の分離を破らない）"
        )

    def test_from_frozen_is_deterministic_and_serialised(self):
        fn = _function_code(
            ATLAS_ROUTE_SRC, "create_atlas_skeleton_draft_from_frozen"
        )
        # retire/restore と直列化し、トランザクション内で lifecycle を再確認する。
        assert "lock_domain_for_write" in fn
        assert "domain_lifecycle" in fn
        # 複製元は現行凍結版（同梱のみのドメインでも解決できる経路）。
        assert "load_learner_skeleton" in fn
        assert "save_draft" in fn
        # LLM を呼ばない（node id を振り直さないことが本 API の存在理由）。
        for term in ("generate_skeleton_draft", "propose_skeleton_edit", "interpret_"):
            assert term not in fn

    def test_gap_route_delegates_the_draft_write_to_the_existing_put(self):
        """取り込みは「プレビュー → 教員の PUT → 刻印」の3手（AI は書かない）。"""
        preview = _function_code(GAP_ROUTE_SRC, "preview_atlas_gap_incorporation")
        assert "build_gap_patch" in preview
        assert "session.commit()" not in preview
        mark = _function_code(GAP_ROUTE_SRC, "mark_atlas_gap_incorporated")
        assert "mark_incorporated" in mark
        assert "_draft_node_ids(" in mark
