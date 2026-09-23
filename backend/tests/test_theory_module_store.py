"""理論モジュール層 Phase 1 — 保存（knowledge_theory_modules）のテスト。

正本: ``docs/features/theory_module_layer_design.md`` §13.2〜§13.6 / §13.10 / §13.11（TM11・TM12・TM14）。

固定するもの:

- 基表 ``knowledge_theory_modules`` を SQL で触るのは ``core/document_pipeline/persistence.py``
  だけ（読み手は ``knowledge_theory_modules_live``）。migration 086 の列・部分一意索引・live ビュー・
  DELETE 文なし。
- ``theory_module_stable_key`` の決定性（位置・run_id・agent ID を材料にしない・集合の順序に
  依存しない・規則の版で変わる）。
- ``persist_theory_modules`` の同期（stable_key 一致で同 UUID・``records: []`` で全 superseded・
  ``persistable: false`` で SQL 非発行・``parent_stable_key`` は確定済みキー）。
- ステージ ``theory_modules`` の登録位置・skip 条件・非致命・resume。
- route 経路とステージ経路の ``module_key`` 集合の一致（fixture 2 本）。

DB・LLM には触らない（fake session と monkeypatch）。
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.document_pipeline import orchestrator as orch  # noqa: E402
from core.document_pipeline import persistence  # noqa: E402
from core.knowledge_objects import stable_key as ko_keys  # noqa: E402
from core.knowledge_objects import theory_modules as ko_modules  # noqa: E402
from core.knowledge_objects.schema import TABLE_THEORY_MODULES, VIEW_THEORY_MODULES_LIVE  # noqa: E402
from core.theory_modules import build_theory_module_records, build_theory_modules  # noqa: E402
from core.theory_modules import schema as tm_schema  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402

CORE_DIR = BACKEND / "core"
API_DIR = BACKEND / "api"
KO_DIR = CORE_DIR / "knowledge_objects"
MIGRATION = BACKEND / "db" / "086_theory_modules.sql"
FIXTURES = BACKEND / "tests" / "fixtures" / "theory_modules"
STAGE = "theory_modules"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _equation_artifact(fixture: dict) -> dict:
    """fixture の equation_semantics は ``records`` キーで持つ（切り詰め版）。実 artifact と同じ
    ``equations`` キーに載せ替えて ``equation_stable_key_map`` が読める形にする。"""
    payload = fixture["artifacts"].get("equation_semantics") or {}
    return {"equations": list(payload.get("records") or payload.get("equations") or [])}


def _key_map(fixture: dict) -> dict[str, str]:
    return persistence.equation_stable_key_map(fixture["document_id"], _equation_artifact(fixture))


def _records(fixture: dict) -> dict:
    return build_theory_module_records(
        document_id=fixture["document_id"],
        artifacts=fixture["artifacts"],
        graph_json=fixture["graph_json"],
        equation_stable_keys=_key_map(fixture),
    )


# ===========================================================================
# 基表の規律と migration 086
# ===========================================================================

#: 基表 ``knowledge_theory_modules`` を SQL で触ってよいファイルと、その理由。
BASE_TABLE_ALLOWLIST: dict[str, str] = {
    "backend/core/document_pipeline/persistence.py": (
        "書き手。sync_live_rows 経由で同期（stable_key 一致で UPDATE / 不一致で supersede・INSERT）。"
    ),
}

_BASE_TABLE_SQL_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+knowledge_theory_modules\b(?!_live)", re.IGNORECASE
)


def _scanned_sources() -> list[Path]:
    paths = sorted(CORE_DIR.rglob("*.py")) + sorted(API_DIR.rglob("*.py"))
    return [p for p in paths if KO_DIR not in p.parents and p.parent != KO_DIR]


class TestBaseTableDiscipline:
    def test_only_persistence_touches_the_base_table(self):
        offending: dict[str, list[str]] = {}
        for path in _scanned_sources():
            rel = path.relative_to(ROOT).as_posix()
            if rel in BASE_TABLE_ALLOWLIST:
                continue
            hits = [
                f"{lineno}: {line.strip()}"
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
                if _BASE_TABLE_SQL_RE.search(line)
            ]
            if hits:
                offending[rel] = hits
        assert offending == {}, f"読み手は knowledge_theory_modules_live を読むこと: {offending}"

    def test_table_constant_is_used_only_by_the_writer(self):
        users = []
        for path in _scanned_sources():
            if "TABLE_THEORY_MODULES" in path.read_text(encoding="utf-8"):
                users.append(path.relative_to(ROOT).as_posix())
        assert users == ["backend/core/document_pipeline/persistence.py"]

    def test_constants_match_migration(self):
        assert TABLE_THEORY_MODULES == "knowledge_theory_modules"
        assert VIEW_THEORY_MODULES_LIVE == "knowledge_theory_modules_live"


class TestMigration086:
    def setup_method(self):
        self.sql = MIGRATION.read_text(encoding="utf-8")

    def _table_block(self) -> str:
        start = self.sql.index("CREATE TABLE IF NOT EXISTS knowledge_theory_modules")
        return self.sql[start:self.sql.index(");", start)]

    def test_all_written_columns_exist(self):
        block = self._table_block()
        columns = set(re.findall(r"^\s{4}([a-z_]+)\s", block, re.M))
        written = set(ko_modules.THEORY_MODULE_CONTENT_COLUMNS) | {
            "document_id", "stable_key", ko_modules.AGENT_ID_COLUMN, "produced_by_run_id",
            "superseded_at", "superseded_by_run_id", "updated_at",
        }
        assert written <= columns, written - columns

    def test_document_fk_cascade_and_level_check(self):
        block = self._table_block()
        assert "document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE" in block
        assert "knowledge_theory_modules_level_check CHECK (level IN ('outer', 'inner'))" in block
        assert (tm_schema.LEVEL_OUTER, tm_schema.LEVEL_INNER) == ("outer", "inner")

    def test_partial_unique_index_on_live_stable_key(self):
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_theory_modules_stable_key_live\s+"
            r"ON knowledge_theory_modules \(document_id, stable_key\)\s+"
            r"WHERE superseded_at IS NULL AND stable_key IS NOT NULL",
            self.sql,
        )

    def test_live_view(self):
        assert re.search(
            r"CREATE OR REPLACE VIEW knowledge_theory_modules_live AS\s+"
            r"SELECT \* FROM knowledge_theory_modules WHERE superseded_at IS NULL",
            self.sql,
        )

    def test_no_delete_statement(self):
        body = "\n".join(line for line in self.sql.splitlines() if not line.strip().startswith("--"))
        assert "DELETE FROM" not in body.upper()

    def test_no_numeric_columns(self):
        """TM6: 件数・本数を列にしない（指紋の文字列と JSONB の中にだけある）。"""
        block = self._table_block()
        assert not re.search(r"\b(?:INTEGER|INT|BIGINT|NUMERIC|REAL|DOUBLE)\b", block)


# ===========================================================================
# stable_key
# ===========================================================================


class TestTheoryModuleStableKey:
    def test_deterministic_and_prefixed(self):
        a = ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a", "k1:b"])
        b = ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a", "k1:b"])
        assert a == b and a.startswith("k1:")

    def test_independent_of_order(self):
        assert ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:b", "k1:a"]) == \
            ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a", "k1:b"])

    def test_changes_with_rule_version_level_document_and_equations(self):
        base = ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a"])
        assert ko_keys.theory_module_stable_key("doc", "m3", "outer", ["k1:a"]) != base
        assert ko_keys.theory_module_stable_key("doc", "m2", "inner", ["k1:a"]) != base
        assert ko_keys.theory_module_stable_key("doc2", "m2", "outer", ["k1:a"]) != base
        assert ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:b"]) != base

    def test_material_has_no_position_run_or_agent_id(self):
        params = set(inspect.signature(ko_keys.theory_module_stable_key).parameters)
        assert params == {"document_id", "rule_version", "level", "produced_equation_keys"}


# ===========================================================================
# 保存用 incoming の組み立て（純関数）
# ===========================================================================


def _record(module_key, level, produced_keys, parent=None, **extra):
    base = {
        "module_key": module_key,
        "level": level,
        "parent_module_key": parent,
        "produced_equation_ids": [k.replace("k1:", "eq_") for k in produced_keys],
        "produced_equation_keys": list(produced_keys),
        "label": "定義: x", "visual_label": "定義: x", "theory_object": "x",
        "process_verbs": ["定義"], "stage_keys": ["theory_basis"], "dominant_stage": "theory_basis",
        "source_backing_status": "source_backed", "isolated_reason": None,
        "input_equation_ids": [], "output_equation_ids": [], "foundation_equation_ids": [],
        "sink_equation_ids": [], "required_claim_ids": [], "assumptions": [],
        "members": [{"step_refs": ["d1:s1"], "node_ids": ["n1"], "operation": "define_x",
                     "edge_type": "defines", "generic": False, "stage_key": "theory_basis",
                     "input_equation_ids": ["eq_0"], "output_equation_ids": ["eq_a"]}],
        "structure_fingerprint": f"m2|{level}|ops=defines:1|in=0|out=0|premise=0",
        "identity_eligible": False,
        "components_for_comparison": ["部品"],
    }
    base.update(extra)
    return base


class TestBuildItems:
    def test_parent_stable_key_is_the_parents_final_key(self):
        records = [
            _record("m2:outer", "outer", ["k1:a", "k1:b"]),
            _record("m2:inner", "inner", ["k1:a"], parent="m2:outer"),
        ]
        items = ko_modules.build_theory_module_items("doc", records, rule_version="m2")
        outer, inner = items
        assert outer["values"]["parent_stable_key"] is None
        assert inner["values"]["parent_stable_key"] == outer["stable_key"]
        assert outer["stable_key"] == ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a", "k1:b"])
        assert outer["agent_id"] == "m2:outer"

    def test_collision_gets_numbered_and_parent_follows_the_final_key(self):
        records = [
            _record("m2:b", "outer", ["k1:a"]),
            _record("m2:a", "outer", ["k1:a"]),
            _record("m2:child", "inner", ["k1:z"], parent="m2:b"),
        ]
        items = ko_modules.build_theory_module_items("doc", records, rule_version="m2")
        keys = {item["agent_id"]: item["stable_key"] for item in items}
        raw = ko_keys.theory_module_stable_key("doc", "m2", "outer", ["k1:a"])
        assert keys["m2:a"] == raw and keys["m2:b"] == raw + "#2"
        child = next(item for item in items if item["agent_id"] == "m2:child")
        assert child["values"]["parent_stable_key"] == raw + "#2"

    def test_outer_items_come_first(self):
        records = [
            _record("m2:inner", "inner", ["k1:a"], parent="m2:outer"),
            _record("m2:outer", "outer", ["k1:a", "k1:b"]),
        ]
        items = ko_modules.build_theory_module_items("doc", records, rule_version="m2")
        assert [item["values"]["level"] for item in items] == ["outer", "inner"]

    def test_values_cover_exactly_the_content_columns(self):
        items = ko_modules.build_theory_module_items("doc", [_record("m2:o", "outer", ["k1:a"])], rule_version="m2")
        assert set(items[0]["values"]) == set(ko_modules.THEORY_MODULE_CONTENT_COLUMNS)
        assert items[0]["values"]["agent_payload"] == {"components_for_comparison": ["部品"]}

    def test_empty_records_give_no_items(self):
        assert ko_modules.build_theory_module_items("doc", [], rule_version="m2") == []


# ===========================================================================
# 同期（fake session）
# ===========================================================================


class TestSyncTheoryModules:
    def test_match_updates_in_place_and_never_deletes(self):
        records = [_record("m2:o", "outer", ["k1:a"])]
        key = ko_keys.theory_module_stable_key("doc-1", "m2", "outer", ["k1:a"])
        session = FakeKnowledgeSession(live_rows=[
            {"id": "uuid-1", "stable_key": key, "agent_id": "m2:old"},
        ])
        summary = persistence.sync_theory_modules(
            session, document_id="doc-1", run_id="run-1", records=records, rule_version="m2",
        )
        assert summary == {"updated": 1, "inserted": 0, "superseded": 0, "outer": 1, "inner": 0,
                           "rule_version": "m2"}
        assert session.inserted_into("knowledge_theory_modules") == []
        assert not any("DELETE" in sql.upper() for sql in session.sql)
        values = session.updated_in("knowledge_theory_modules")[0]
        assert values["agent_module_key"] == "m2:o"
        assert values["produced_by_run_id"] == "run-1"
        assert session.json_of(values, "member_steps")[0]["edge_type"] == "defines"

    def test_new_rows_are_inserted_and_stale_rows_superseded(self):
        records = [_record("m2:o", "outer", ["k1:new"])]
        session = FakeKnowledgeSession(live_rows=[
            {"id": "uuid-old", "stable_key": "k1:gone", "agent_id": "m2:old"},
        ])
        summary = persistence.sync_theory_modules(
            session, document_id="doc-1", run_id="run-1", records=records, rule_version="m2",
        )
        assert summary["inserted"] == 1 and summary["superseded"] == 1
        assert session.superseded == ["uuid-old"]
        row = session.inserted_into("knowledge_theory_modules")[0]
        assert row["rule_version"] == "m2" and row["level"] == "outer"

    def test_empty_records_supersede_every_live_row(self):
        """TM14 の後半: 式の手順が 0 だった = 今回の結果。旧行を superseded にする。"""
        session = FakeKnowledgeSession(live_rows=[
            {"id": "uuid-1", "stable_key": "k1:a", "agent_id": "m2:a"},
            {"id": "uuid-2", "stable_key": "k1:b", "agent_id": "m2:b"},
        ])
        summary = persistence.sync_theory_modules(
            session, document_id="doc-1", run_id="run-1", records=[], rule_version="m2",
        )
        assert summary["superseded"] == 2 and summary["outer"] == 0
        assert sorted(session.superseded) == ["uuid-1", "uuid-2"]

    def test_sync_uses_no_preserved_columns_and_no_fallback(self):
        src = inspect.getsource(persistence.sync_theory_modules)
        assert "preserved_columns=()" in src
        assert "fallback_match_column" not in src.split('"""')[-1]


class TestPersistTheoryModules:
    def test_not_persistable_issues_no_sql(self, monkeypatch):
        monkeypatch.setattr(persistence, "_pg_session", lambda: pytest.fail("SQL を発行してはならない"))
        for reason in (tm_schema.RECORDS_SKIP_NO_DERIVATIONS, tm_schema.RECORDS_SKIP_BUILD_FAILED):
            result = persistence.persist_theory_modules(
                document_id="doc-1", run_id="run-1",
                module_records={"persistable": False, "skip_reason": reason, "rule_version": "m2", "records": []},
            )
            assert result == {"skipped_reason": reason, "rule_version": "m2"}

    def test_persistable_commits_with_audit_and_stage_output(self, monkeypatch):
        session = FakeKnowledgeSession()
        monkeypatch.setattr(persistence, "_pg_session", lambda: session)
        result = persistence.persist_theory_modules(
            document_id="doc-1", run_id="run-1",
            module_records={"persistable": True, "rule_version": "m2",
                            "records": [_record("m2:o", "outer", ["k1:a"])]},
        )
        assert result["inserted"] == 1 and session.committed
        audit = session.inserted_into("theory_review_events")[0]
        metadata = json.loads(audit["metadata"])
        assert metadata["theory_modules"]["outer"] == 1
        assert "structure_fingerprint" not in audit["metadata"]  # TM12: 監査に指紋を出さない
        assert any("document_analysis_runs" in sql for sql in session.sql)

    def test_failure_rolls_back_records_and_raises(self, monkeypatch):
        session = FakeKnowledgeSession()
        monkeypatch.setattr(persistence, "_pg_session", lambda: session)
        recorded = {}
        monkeypatch.setattr(
            persistence, "record_knowledge_stage_output_failure",
            lambda **kwargs: recorded.update(kwargs),
        )

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(persistence, "sync_live_rows", _boom)
        with pytest.raises(RuntimeError):
            persistence.persist_theory_modules(
                document_id="doc-1", run_id="run-1",
                module_records={"persistable": True, "rule_version": "m2", "records": []},
            )
        assert session.rolled_back and not session.committed
        assert recorded["kind"] == "theory_modules"


# ===========================================================================
# builder の保存用出力（fixture）
# ===========================================================================


class TestRecordsOnFixtures:
    @pytest.mark.parametrize(
        ("name", "outer", "inner"),
        [("arxiv_2407_01221v2_tex.json", 8, 5), ("arxiv_2609_15375v1_tex.json", 10, 4)],
    )
    def test_record_counts_match_the_dto(self, name, outer, inner):
        fixture = _load(name)
        records = _records(fixture)
        assert records["persistable"] is True and records["rule_version"] == "m2"
        levels = [r["level"] for r in records["records"]]
        assert levels.count("outer") == outer and levels.count("inner") == inner
        dto = build_theory_modules(
            document_id=fixture["document_id"], artifacts=fixture["artifacts"],
            graph_json=fixture["graph_json"], equation_stable_keys=_key_map(fixture),
        )
        assert [r["module_key"] for r in records["records"]] == [m["module_key"] for m in dto["modules"]]
        assert records["equations_without_stable_key"] == []

    def test_claim_chains_only_is_persistable_with_no_records(self):
        records = _records(_load("arxiv_2609_15375v1_pdf.json"))
        assert records["persistable"] is True and records["records"] == []

    def test_items_from_fixture_have_unique_stable_keys(self):
        fixture = _load("arxiv_2609_15375v1_tex.json")
        records = _records(fixture)
        items = ko_modules.build_theory_module_items(
            fixture["document_id"], records["records"], rule_version=records["rule_version"],
        )
        keys = [item["stable_key"] for item in items]
        assert len(keys) == len(set(keys))
        outer_keys = {item["stable_key"] for item in items if item["values"]["level"] == "outer"}
        for item in items:
            if item["values"]["level"] == "inner":
                assert item["values"]["parent_stable_key"] in outer_keys


# ===========================================================================
# ステージ theory_modules
# ===========================================================================


class _FakeCtx:
    def __init__(self, *, artifacts=None, use_artifact=False, skip_graph=False, skip_components=False):
        self.document_id = "doc-1"
        self.material_id = "mat-1"
        self.run_id = "run-1"
        self._artifacts = dict(artifacts or {})
        self._use_artifact = use_artifact
        self.skip_graph_persist = skip_graph
        self.skip_component_persist = skip_components
        self.saved: dict = {}
        self.done: dict = {}
        self.started: list = []

    def artifact(self, name):
        return self._artifacts.get(name)

    def should_use_artifact(self, name):
        return self._use_artifact

    def report_start(self, name, total=None, unit=None):
        self.started.append((name, unit))

    def save_artifact(self, name, payload):
        self.saved[name] = payload

    def report_done(self, name, payload):
        self.done[name] = payload

    def finish_target_stage(self, name, payload):
        return False


class TestStageRegistration:
    def test_between_persist_and_identity_candidates(self):
        named = [step.name for step in orch._PIPELINE_STEPS if step.name]
        index = named.index(STAGE)
        assert named[index - 1] == "persist_claims_components_graph"
        assert named[index + 1] == "identity_candidates"
        stages = orch.PIPELINE_STAGES
        assert stages.index(STAGE) == stages.index("persist_claims_components_graph") + 1

    def test_declared_deterministic(self):
        step = next(s for s in orch._PIPELINE_STEPS if s.name == STAGE)
        assert step.llm_kind == orch.LLM_KIND_NONE
        assert step.model_policy is False
        assert step.progress_unit == "builder"
        assert STAGE not in orch.LLM_STAGE_NAMES | orch.LLM_CALLING_STAGE_NAMES | orch.VISION_STAGE_NAMES

    def test_label_and_usage_feature(self):
        from core.llm_usage.schema import KNOWN_FEATURES
        from routes.lecture_studio import pipeline as ls_pipeline

        assert ls_pipeline.DOCUMENT_PIPELINE_STAGE_LABELS[STAGE] == "理論モジュールの保存"
        assert STAGE in ls_pipeline.DOCUMENT_PIPELINE_STAGES
        assert f"pipeline:{STAGE}" in KNOWN_FEATURES


class TestStageExecution:
    def _install(self, monkeypatch, *, records=None, persisted=None):
        calls: dict = {"persist": [], "build": []}
        monkeypatch.setattr(orch, "load_stored_component_graph_json", lambda doc: {"nodes": []})

        def _build(**kwargs):
            calls["build"].append(kwargs)
            return records if records is not None else {"persistable": True, "rule_version": "m2", "records": []}

        def _persist(**kwargs):
            calls["persist"].append(kwargs)
            return persisted or {"updated": 0, "inserted": 1, "superseded": 0, "outer": 1, "inner": 0,
                                 "rule_version": "m2"}

        monkeypatch.setattr(orch, "build_theory_module_records", _build)
        monkeypatch.setattr(orch, "persist_theory_modules", _persist)
        return calls

    def test_graph_not_persisted_skips_without_sql(self, monkeypatch):
        calls = self._install(monkeypatch)
        for flags in ({"skip_graph": True}, {"skip_components": True}):
            ctx = _FakeCtx(**flags)
            assert orch._stage_theory_modules(ctx) is False
            assert ctx.saved[STAGE]["skipped_reason"] == orch.THEORY_MODULES_SKIP_GRAPH_NOT_PERSISTED
        assert calls["build"] == [] and calls["persist"] == []

    def test_not_persistable_records_the_reason_and_does_not_persist(self, monkeypatch):
        calls = self._install(monkeypatch, records={"persistable": False, "skip_reason": "no_derivations",
                                                     "rule_version": "m2", "records": []})
        ctx = _FakeCtx(artifacts={"derivation_chain": {"chains": []}})
        orch._stage_theory_modules(ctx)
        assert ctx.saved[STAGE]["skipped_reason"] == "no_derivations"
        assert calls["persist"] == []

    def test_success_passes_the_run_artifacts_and_shared_key_map(self, monkeypatch):
        calls = self._install(monkeypatch)
        equations = {"equations": [{"equation_id": "eq_1", "source_extraction": {"latex": "a=b"}}]}
        ctx = _FakeCtx(artifacts={"derivation_chain": {"chains": [{"x": 1}]}, "equation_semantics": equations,
                                  "unrelated": {"y": 1}})
        orch._stage_theory_modules(ctx)
        build = calls["build"][0]
        assert set(build["artifacts"]) == {"derivation_chain", "equation_semantics"}
        assert build["equation_stable_keys"] == persistence.equation_stable_key_map("doc-1", equations)
        assert build["graph_json"] == {"nodes": []}
        assert calls["persist"][0]["run_id"] == "run-1"
        payload = ctx.saved[STAGE]
        assert payload["status"] == "completed" and payload["outer"] == 1
        assert ctx.started == [(STAGE, "builder")]
        assert ctx.done[STAGE] == payload

    def test_stage_payload_has_no_fingerprint(self, monkeypatch):
        self._install(monkeypatch)
        ctx = _FakeCtx(artifacts={"derivation_chain": {"chains": [{"x": 1}]}})
        orch._stage_theory_modules(ctx)
        assert "fingerprint" not in json.dumps(ctx.saved[STAGE])

    def test_failure_is_non_fatal(self, monkeypatch):
        self._install(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(orch, "persist_theory_modules", _boom)
        ctx = _FakeCtx(artifacts={"derivation_chain": {"chains": [{"x": 1}]}})
        assert orch._stage_theory_modules(ctx) is False
        payload = ctx.saved[STAGE]
        assert payload["status"] == "completed" and payload["failed"] is True
        assert "db down" in payload["error"]

    def test_resume_reuses_the_artifact(self, monkeypatch):
        calls = self._install(monkeypatch)
        ctx = _FakeCtx(artifacts={STAGE: {"status": "completed", "outer": 3}}, use_artifact=True)
        orch._stage_theory_modules(ctx)
        assert calls["build"] == [] and calls["persist"] == []
        assert ctx.saved == {} and ctx.done[STAGE]["outer"] == 3


# ===========================================================================
# route 経路とステージ経路の module_key 集合の一致（§13.5）
# ===========================================================================


class TestRouteAndStageAgree:
    @pytest.mark.parametrize("name", ["arxiv_2407_01221v2_tex.json", "arxiv_2609_15375v1_tex.json"])
    def test_same_module_keys(self, monkeypatch, name):
        import routes.theory_components as tc

        fixture = _load(name)
        artifacts = dict(fixture["artifacts"])
        artifacts["equation_semantics"] = {
            **_equation_artifact(fixture), "records": fixture["artifacts"]["equation_semantics"]["records"],
        }
        # route 経路: 保存済みグラフ → 実際の正規化（component 一覧は空）→ builder。
        monkeypatch.setattr(tc, "_components_for_document", lambda doc: [])
        monkeypatch.setattr(tc, "_stored_component_graph", lambda doc: json.loads(json.dumps(fixture["graph_json"])))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda doc: artifacts)
        route_dto = tc.build_theory_modules_for_document(fixture["document_id"])
        # ステージ経路: 保存済みグラフをそのまま builder の保存用出力へ。
        monkeypatch.setattr(orch, "load_stored_component_graph_json", lambda doc: fixture["graph_json"])
        captured = {}
        monkeypatch.setattr(orch, "persist_theory_modules", lambda **kwargs: captured.update(kwargs) or {})
        ctx = _FakeCtx(artifacts=artifacts)
        ctx.document_id = fixture["document_id"]
        orch._stage_theory_modules(ctx)
        stage_records = captured["module_records"]["records"]
        assert route_dto["available"] is True
        route_keys = {m["module_key"] for m in route_dto["modules"]}
        stage_keys = {r["module_key"] for r in stage_records}
        assert route_keys == stage_keys and route_keys
        # 写像が効いている（equation_id ではなく equation stable_key を材料にしている）。
        assert all(key.startswith("k1:") for r in stage_records for key in r["produced_equation_keys"])
