"""既存行への stable_key バックフィル（knowledge_objects_design.md §5.6）。

``core/knowledge_objects/backfill.py`` が
  ①``stable_key IS NULL`` の live 行だけを対象にすること
  ②同一 document 内の衝突を決定論順（id 昇順）で ``#2`` … に送ること
  ③既に使われているキー（別の live 行が持つ）を避けること
  ④``agent_component_id`` を ``source_scope.legacy_ids[0]`` から補うこと
を、SQL をディスパッチする fake session（test_persist_claims_legacy_ids.py 同型）で
UPDATE のパラメータを捕捉して検証する。DB も LLM も使わない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects import backfill as backfill_mod  # noqa: E402
from core.knowledge_objects.backfill import backfill_stable_keys  # noqa: E402
from core.knowledge_objects.stable_key import (  # noqa: E402
    claim_stable_key,
    component_stable_key,
)

DOC = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# fake session
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """SELECT は用意した行を返し、UPDATE はパラメータを種別ごとに貯める。"""

    def __init__(
        self,
        *,
        pending_claims=(),
        taken_claims=(),
        pending_components=(),
        taken_components=(),
        component_blocks=(),
    ):
        self.pending_claims = list(pending_claims)
        self.taken_claims = list(taken_claims)
        self.pending_components = list(pending_components)
        self.taken_components = list(taken_components)
        self.component_blocks = list(component_blocks)
        self.claim_updates: list[dict] = []
        self.component_updates: list[dict] = []
        self.statements: list[str] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        params = dict(params or {})
        if sql.startswith("UPDATE theory_claims"):
            self.claim_updates.append(params)
            return _FakeResult([])
        if sql.startswith("UPDATE theory_components"):
            self.component_updates.append(params)
            return _FakeResult([])
        # migration 080 で document_id が uuid になり、投影が
        # ``SELECT document_id::text AS document_id, stable_key`` になった。
        if re.search(r"SELECT document_id\S* (AS document_id, )?stable_key FROM theory_claims", sql):
            return _FakeResult(self.taken_claims)
        if re.search(r"SELECT document_id\S* (AS document_id, )?stable_key FROM theory_components", sql):
            return _FakeResult(self.taken_components)
        if "FROM theory_claims" in sql and "claim_text" in sql:
            return _FakeResult(self.pending_claims)
        if "jsonb_array_elements_text" in sql:
            return _FakeResult(self.component_blocks)
        if "FROM theory_components" in sql:
            return _FakeResult(self.pending_components)
        raise AssertionError(f"unexpected statement: {sql}")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _claim_row(row_id, text="a claim", block_id="b1", document_id=DOC):
    return {
        "id": row_id,
        "document_id": document_id,
        "claim_text": text,
        "block_id": block_id,
    }


def _component_row(row_id, name="Comp", document_id=DOC, agent_component_id=None, legacy_id=None):
    return {
        "id": row_id,
        "document_id": document_id,
        "name": name,
        "agent_component_id": agent_component_id,
        "legacy_id": legacy_id,
    }


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


class TestClaimBackfill:
    def test_only_null_rows_are_updated_and_key_matches_pure_function(self):
        session = _FakeSession(pending_claims=[_claim_row("c-1", "The mass is one.", "b1")])

        counts = backfill_stable_keys(session)

        assert counts == {"claims": 1, "components": 0}
        assert len(session.claim_updates) == 1
        update = session.claim_updates[0]
        assert update["id"] == "c-1"
        assert update["stable_key"] == claim_stable_key(DOC, "The mass is one.", ["b1"])

    def test_update_is_guarded_by_stable_key_is_null(self):
        session = _FakeSession(pending_claims=[_claim_row("c-1")])
        backfill_stable_keys(session)
        update_sql = [s for s in session.statements if s.startswith("UPDATE theory_claims")]
        assert update_sql and "stable_key IS NULL" in update_sql[0]

    def test_empty_block_id_yields_empty_block_set(self):
        session = _FakeSession(pending_claims=[_claim_row("c-1", "t", block_id=None)])
        backfill_stable_keys(session)
        assert session.claim_updates[0]["stable_key"] == claim_stable_key(DOC, "t", [])

    def test_nothing_to_do_issues_no_update(self):
        session = _FakeSession()
        assert backfill_stable_keys(session) == {"claims": 0, "components": 0}
        assert session.claim_updates == []
        assert session.component_updates == []

    def test_collision_inside_document_is_numbered_by_id_order(self):
        session = _FakeSession(
            pending_claims=[
                _claim_row("c-3", "same text", "b1"),
                _claim_row("c-1", "same text", "b1"),
                _claim_row("c-2", "same text", "b1"),
            ]
        )
        backfill_stable_keys(session)

        by_id = {u["id"]: u["stable_key"] for u in session.claim_updates}
        base = claim_stable_key(DOC, "same text", ["b1"])
        assert by_id == {"c-1": base, "c-2": f"{base}#2", "c-3": f"{base}#3"}

    def test_same_text_in_other_document_does_not_collide(self):
        other = "33333333-3333-3333-3333-333333333333"
        session = _FakeSession(
            pending_claims=[
                _claim_row("c-1", "same text", "b1"),
                _claim_row("c-2", "same text", "b1", document_id=other),
            ]
        )
        backfill_stable_keys(session)
        keys = {u["id"]: u["stable_key"] for u in session.claim_updates}
        assert "#" not in keys["c-1"] and "#" not in keys["c-2"]
        assert keys["c-1"] != keys["c-2"]

    def test_key_already_taken_by_a_live_row_is_bumped(self):
        base = claim_stable_key(DOC, "same text", ["b1"])
        session = _FakeSession(
            pending_claims=[_claim_row("c-1", "same text", "b1")],
            taken_claims=[{"document_id": DOC, "stable_key": base}],
        )
        backfill_stable_keys(session)
        assert session.claim_updates[0]["stable_key"] == f"{base}#2"


# ---------------------------------------------------------------------------
# component
# ---------------------------------------------------------------------------


class TestComponentBackfill:
    def test_block_ids_come_from_evidence_claims(self):
        session = _FakeSession(
            pending_components=[_component_row("k-1", "Mass model")],
            component_blocks=[
                {"id": "k-1", "block_id": "b2"},
                {"id": "k-1", "block_id": "b1"},
            ],
        )
        counts = backfill_stable_keys(session)

        assert counts["components"] == 1
        assert session.component_updates[0]["stable_key"] == component_stable_key(
            DOC, "Mass model", "", ["b1", "b2"]
        )

    def test_unresolvable_evidence_yields_empty_block_set(self):
        session = _FakeSession(pending_components=[_component_row("k-1", "Mass model")])
        backfill_stable_keys(session)
        assert session.component_updates[0]["stable_key"] == component_stable_key(
            DOC, "Mass model", "", []
        )

    def test_agent_component_id_filled_from_legacy_ids(self):
        session = _FakeSession(
            pending_components=[_component_row("k-1", legacy_id="comp_001")]
        )
        backfill_stable_keys(session)
        assert session.component_updates[0]["agent_id"] == "comp_001"

    def test_existing_agent_component_id_is_not_overwritten(self):
        session = _FakeSession(
            pending_components=[
                _component_row("k-1", agent_component_id="already", legacy_id="comp_001")
            ]
        )
        backfill_stable_keys(session)
        # 呼び出し側は NULL を渡し、SQL 側の COALESCE(NULLIF(...)) が既存値を守る。
        assert session.component_updates[0]["agent_id"] is None
        update_sql = [s for s in session.statements if s.startswith("UPDATE theory_components")]
        assert "COALESCE(NULLIF(agent_component_id, ''), :agent_id)" in update_sql[0]

    def test_collision_inside_document_is_numbered(self):
        session = _FakeSession(
            pending_components=[_component_row("k-2"), _component_row("k-1")]
        )
        backfill_stable_keys(session)
        by_id = {u["id"]: u["stable_key"] for u in session.component_updates}
        base = component_stable_key(DOC, "Comp", "", [])
        assert by_id == {"k-1": base, "k-2": f"{base}#2"}


# ---------------------------------------------------------------------------
# モジュールの規律
# ---------------------------------------------------------------------------


class TestModuleDiscipline:
    def test_does_not_import_fastapi_or_llm(self):
        source = Path(backfill_mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("fastapi", "core.llm", "openai"):
            assert forbidden not in source

    def test_has_no_delete_statement(self):
        source = Path(backfill_mod.__file__).read_text(encoding="utf-8")
        assert not re.search(r"\bDELETE\s+FROM\b", source, re.IGNORECASE)

    def test_selects_only_live_rows(self):
        source = Path(backfill_mod.__file__).read_text(encoding="utf-8")
        assert source.count("superseded_at IS NULL") >= 4
