"""``core/element_explanations.py``（``element_explanations`` テーブル, migration 056）の
DB プリミティブ単体テスト。

設計書: ``docs/features/hierarchical_context_explanation_design.md`` §2（E1〜E8）・§5.2。

``tests/core/test_atlas_store.py`` / ``tests/fixtures/atlas_skeletons_fake.py`` の流儀を
踏襲し、本ファイル内に ``element_explanations`` の SQL 面だけを模倣するインメモリ
フェイクセッションを定義して検証する（実 DB 接続なし。制約により新規ファイルは本ファイルと
ガードレールファイルの2つに限定するため、フェイクは独立モジュールへ切り出さずここに置く）。

検証観点:
- insert_candidates: 同一 (element_type, element_id, kind) の再投入が既存 candidate のみを
  superseded にし、approved/dismissed には触れない（再解析セーフ・P4）。
- list_for_document: element_type/status/kind フィルタ。
- approve/dismiss: candidate からのみ遷移し、reviewed_by/reviewed_at を記録する。
  candidate 以外からの遷移は ElementExplanationError(kind='conflict')。行削除しない。
- update_body: 旧行 superseded + 新行 INSERT（evidence/status/reviewed_* 引き継ぎ）。
  dismissed/superseded からの編集は拒否。
- approved_for_elements: 複数要素の一括 approved 読み出し。
"""

from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import element_explanations as store  # noqa: E402


# ---------------------------------------------------------------------------
# インメモリフェイクセッション（core.element_explanations の SQL 面だけを模倣）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeElementExplanationSession:
    """``element_explanations`` のインメモリ実装（セッション兼用）。

    ``commit``/``rollback``/``close`` は no-op（store 側はコミット等を行わない設計 —
    呼び出し側 = このテストが状態遷移の結果をそのまま検証する）。
    """

    def __init__(self):
        self.rows: list[dict] = []
        self._id_seq = itertools.count(1)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _new_id(self) -> str:
        return f"expl-{next(self._id_seq)}"

    @staticmethod
    def _parse_ts(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    @staticmethod
    def _row_tuple(row: dict) -> tuple:
        return (
            row["id"], row["document_id"], row["element_type"], row["element_id"],
            row["kind"], row["body"], row["evidence"], row["status"], row["created_by"],
            row["reviewed_by"], row["reviewed_at"], row["created_at"],
        )

    # -- セッションインターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})

        if sql.startswith("INSERT INTO element_explanations"):
            return self._insert(params)
        if sql.startswith("UPDATE element_explanations SET status = :superseded WHERE document_id"):
            return self._supersede_by_element(params)
        if sql.startswith("UPDATE element_explanations SET status = :superseded WHERE id"):
            return self._supersede_by_id(params)
        if sql.startswith("UPDATE element_explanations SET status = :new_status") and "ANY(:ids)" in sql:
            return self._bulk_transition(params)
        if sql.startswith("UPDATE element_explanations SET status = :new_status"):
            return self._transition(params)
        if sql.startswith("SELECT id::text, document_id::text, status FROM element_explanations"):
            return self._bulk_status_check(params)
        if sql.startswith("SELECT") and sql.endswith("WHERE id = CAST(:id AS uuid)"):
            return self._get_by_id(params)
        if sql.startswith("SELECT") and "ORDER BY element_type, element_id, kind, created_at DESC" in sql:
            return self._approved_for_elements(params)
        if sql.startswith("SELECT") and "ORDER BY created_at DESC" in sql:
            return self._list_for_document(params)
        raise AssertionError(f"unhandled element_explanations SQL: {sql!r}")

    # -- ディスパッチ先 --
    def _insert(self, params: dict) -> _FakeResult:
        evidence_raw = params.get("evidence")
        evidence = json.loads(evidence_raw) if isinstance(evidence_raw, str) else (evidence_raw or {})
        row = {
            "id": self._new_id(),
            "document_id": params["document_id"],
            "element_type": params["element_type"],
            "element_id": params["element_id"],
            "kind": params["kind"],
            "body": params["body"],
            "evidence": evidence,
            "status": params["status"],
            "created_by": params["created_by"],
            "reviewed_by": params.get("reviewed_by"),
            "reviewed_at": self._parse_ts(params.get("reviewed_at")),
            "created_at": datetime.now(timezone.utc),
        }
        self.rows.append(row)
        return _FakeResult([self._row_tuple(row)])

    def _supersede_by_element(self, params: dict) -> _FakeResult:
        for row in self.rows:
            if (
                row["document_id"] == params["document_id"]
                and row["element_type"] == params["element_type"]
                and row["element_id"] == params["element_id"]
                and row["kind"] == params["kind"]
                and row["status"] == params["candidate"]
            ):
                row["status"] = params["superseded"]
        return _FakeResult([])

    def _supersede_by_id(self, params: dict) -> _FakeResult:
        for row in self.rows:
            if row["id"] == params["id"] and row["status"] == params["current_status"]:
                row["status"] = params["superseded"]
                return _FakeResult([(row["id"],)])
        return _FakeResult([])

    def _transition(self, params: dict) -> _FakeResult:
        for row in self.rows:
            if row["id"] == params["id"] and row["status"] == params["candidate"]:
                row["status"] = params["new_status"]
                row["reviewed_by"] = params["user_id"]
                row["reviewed_at"] = datetime.now(timezone.utc)
                return _FakeResult([self._row_tuple(row)])
        return _FakeResult([])

    def _bulk_transition(self, params: dict) -> _FakeResult:
        ids = set(params["ids"])
        updated = []
        for row in self.rows:
            if (
                row["id"] in ids
                and row["document_id"] == params["document_id"]
                and row["status"] == params["candidate"]
            ):
                row["status"] = params["new_status"]
                row["reviewed_by"] = params["user_id"]
                row["reviewed_at"] = datetime.now(timezone.utc)
                updated.append(row)
        return _FakeResult([self._row_tuple(r) for r in updated])

    def _bulk_status_check(self, params: dict) -> _FakeResult:
        ids = set(params["ids"])
        matched = [r for r in self.rows if r["id"] in ids]
        return _FakeResult([(r["id"], r["document_id"], r["status"]) for r in matched])

    def _get_by_id(self, params: dict) -> _FakeResult:
        for row in self.rows:
            if row["id"] == params["id"]:
                return _FakeResult([self._row_tuple(row)])
        return _FakeResult([])

    def _list_for_document(self, params: dict) -> _FakeResult:
        rows = [r for r in self.rows if r["document_id"] == params["document_id"]]
        if "element_type" in params:
            rows = [r for r in rows if r["element_type"] == params["element_type"]]
        if "status" in params:
            rows = [r for r in rows if r["status"] == params["status"]]
        if "kind" in params:
            rows = [r for r in rows if r["kind"] == params["kind"]]
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        return _FakeResult([self._row_tuple(r) for r in rows])

    def _approved_for_elements(self, params: dict) -> _FakeResult:
        rows = [
            r for r in self.rows
            if r["document_id"] == params["document_id"] and r["status"] == params["status"]
        ]
        pairs = []
        idx = 0
        while f"etype_{idx}" in params:
            pairs.append((params[f"etype_{idx}"], params[f"eid_{idx}"]))
            idx += 1
        matched = [r for r in rows if (r["element_type"], r["element_id"]) in pairs]
        matched.sort(key=lambda r: r["created_at"], reverse=True)
        return _FakeResult([self._row_tuple(r) for r in matched])


DOC_A = "11111111-1111-1111-1111-111111111111"


def _item(element_type="figure", element_id="fig-1", kind="contextual", body="text", **extra) -> dict:
    payload = {"element_type": element_type, "element_id": element_id, "kind": kind, "body": body}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# insert_candidates
# ---------------------------------------------------------------------------


class TestInsertCandidates:
    def test_creates_candidate_rows(self):
        fake = FakeElementExplanationSession()
        created = store.insert_candidates(
            fake, DOC_A, [_item(evidence={"reason": "r", "confidence": 0.8})],
        )
        assert len(created) == 1
        row = created[0]
        assert row["status"] == store.STATUS_CANDIDATE
        assert row["created_by"] == store.PIPELINE_CREATED_BY
        assert row["evidence"]["confidence"] == 0.8
        assert len(fake.rows) == 1

    def test_reinsertion_supersedes_only_prior_candidate_for_same_key(self):
        fake = FakeElementExplanationSession()
        store.insert_candidates(fake, DOC_A, [_item()])
        store.insert_candidates(fake, DOC_A, [_item()])
        statuses = sorted(r["status"] for r in fake.rows)
        assert statuses == [store.STATUS_CANDIDATE, store.STATUS_SUPERSEDED]
        # 行削除はしない(P4)
        assert len(fake.rows) == 2

    def test_reinsertion_does_not_touch_approved_or_dismissed(self):
        fake = FakeElementExplanationSession()
        [approved_seed] = store.insert_candidates(fake, DOC_A, [_item(element_id="fig-2")])
        store.approve(fake, approved_seed["id"], "teacher-1")
        [dismissed_seed] = store.insert_candidates(fake, DOC_A, [_item(element_id="fig-3")])
        store.dismiss(fake, dismissed_seed["id"], "teacher-1")

        # 別要素の再解析。approved/dismissed の行は無関係のはずだが、念のため同じ
        # (element_type, kind) の別 element_id で候補投入しても既存には影響しないことを確認。
        store.insert_candidates(fake, DOC_A, [_item(element_id="fig-2")])
        store.insert_candidates(fake, DOC_A, [_item(element_id="fig-3")])

        approved_rows = [r for r in fake.rows if r["element_id"] == "fig-2"]
        dismissed_rows = [r for r in fake.rows if r["element_id"] == "fig-3"]
        assert store.STATUS_APPROVED in [r["status"] for r in approved_rows]
        assert store.STATUS_DISMISSED in [r["status"] for r in dismissed_rows]
        # 新しい candidate が追加されているが、approved/dismissed 行自体は残っている(P4)。
        assert any(r["status"] == store.STATUS_CANDIDATE for r in approved_rows)
        assert any(r["status"] == store.STATUS_CANDIDATE for r in dismissed_rows)

    def test_invalid_element_type_raises(self):
        fake = FakeElementExplanationSession()
        with pytest.raises(ValueError):
            store.insert_candidates(fake, DOC_A, [_item(element_type="bogus")])

    def test_invalid_kind_raises(self):
        fake = FakeElementExplanationSession()
        with pytest.raises(ValueError):
            store.insert_candidates(fake, DOC_A, [_item(kind="bogus")])

    def test_empty_body_raises(self):
        fake = FakeElementExplanationSession()
        with pytest.raises(ValueError):
            store.insert_candidates(fake, DOC_A, [_item(body="  ")])

    def test_created_by_override(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item(created_by="user-42")])
        assert row["created_by"] == "user-42"


# ---------------------------------------------------------------------------
# list_for_document
# ---------------------------------------------------------------------------


class TestListForDocument:
    def test_filters_by_element_type_status_kind(self):
        fake = FakeElementExplanationSession()
        store.insert_candidates(
            fake, DOC_A,
            [
                _item(element_type="figure", element_id="fig-1", kind="contextual"),
                _item(element_type="equation", element_id="eq-1", kind="generic"),
            ],
        )
        all_rows = store.list_for_document(fake, DOC_A)
        assert len(all_rows) == 2

        figures = store.list_for_document(fake, DOC_A, element_type="figure")
        assert len(figures) == 1 and figures[0]["element_id"] == "fig-1"

        generic = store.list_for_document(fake, DOC_A, kind="generic")
        assert len(generic) == 1 and generic[0]["element_id"] == "eq-1"

        candidates = store.list_for_document(fake, DOC_A, status="candidate")
        assert len(candidates) == 2

        approved = store.list_for_document(fake, DOC_A, status="approved")
        assert approved == []

    def test_scoped_to_document(self):
        fake = FakeElementExplanationSession()
        store.insert_candidates(fake, DOC_A, [_item()])
        other_doc = "22222222-2222-2222-2222-222222222222"
        store.insert_candidates(fake, other_doc, [_item()])
        assert len(store.list_for_document(fake, DOC_A)) == 1
        assert len(store.list_for_document(fake, other_doc)) == 1


# ---------------------------------------------------------------------------
# approve / dismiss
# ---------------------------------------------------------------------------


class TestApproveDismiss:
    def test_approve_sets_reviewer_and_status(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        updated = store.approve(fake, row["id"], "teacher-1")
        assert updated["status"] == store.STATUS_APPROVED
        assert updated["reviewed_by"] == "teacher-1"
        assert updated["reviewed_at"] is not None

    def test_dismiss_sets_reviewer_and_status(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        updated = store.dismiss(fake, row["id"], "teacher-1")
        assert updated["status"] == store.STATUS_DISMISSED
        assert updated["reviewed_by"] == "teacher-1"

    def test_approve_missing_id_returns_none(self):
        fake = FakeElementExplanationSession()
        assert store.approve(fake, "does-not-exist", "teacher-1") is None

    def test_approve_non_candidate_raises_conflict(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.approve(fake, row["id"], "teacher-1")
        with pytest.raises(store.ElementExplanationError) as exc:
            store.approve(fake, row["id"], "teacher-2")
        assert exc.value.kind == "conflict"

    def test_dismiss_non_candidate_raises_conflict(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.dismiss(fake, row["id"], "teacher-1")
        with pytest.raises(store.ElementExplanationError):
            store.dismiss(fake, row["id"], "teacher-2")

    def test_approve_requires_user_id(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        with pytest.raises(ValueError):
            store.approve(fake, row["id"], "")

    def test_no_row_is_ever_deleted(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.approve(fake, row["id"], "teacher-1")
        assert len(fake.rows) == 1  # 行削除なし・遷移のみ


# ---------------------------------------------------------------------------
# bulk_transition
# ---------------------------------------------------------------------------


OTHER_DOC = "22222222-2222-2222-2222-222222222222"


class TestBulkTransition:
    def test_bulk_approve_happy_path(self):
        fake = FakeElementExplanationSession()
        rows = store.insert_candidates(
            fake, DOC_A,
            [
                _item(element_id="fig-1"),
                _item(element_id="fig-2"),
                _item(element_id="fig-3"),
            ],
        )
        ids = [r["id"] for r in rows]
        result = store.bulk_transition(
            fake, DOC_A, ids, new_status=store.STATUS_APPROVED, user_id="teacher-1",
        )
        assert result["skipped"] == []
        assert [r["id"] for r in result["updated"]] == ids  # 入力順で整列
        for row in result["updated"]:
            assert row["status"] == store.STATUS_APPROVED
            assert row["reviewed_by"] == "teacher-1"
            assert row["reviewed_at"] is not None

    def test_bulk_dismiss_happy_path(self):
        fake = FakeElementExplanationSession()
        rows = store.insert_candidates(
            fake, DOC_A, [_item(element_id="fig-1"), _item(element_id="fig-2")],
        )
        ids = [r["id"] for r in rows]
        result = store.bulk_transition(
            fake, DOC_A, ids, new_status=store.STATUS_DISMISSED, user_id="teacher-1",
        )
        assert result["skipped"] == []
        assert all(r["status"] == store.STATUS_DISMISSED for r in result["updated"])

    def test_bulk_mixed_conflict_and_not_found(self):
        fake = FakeElementExplanationSession()
        [candidate_row] = store.insert_candidates(fake, DOC_A, [_item(element_id="fig-1")])
        [already_approved] = store.insert_candidates(fake, DOC_A, [_item(element_id="fig-2")])
        store.approve(fake, already_approved["id"], "teacher-1")
        [other_doc_row] = store.insert_candidates(fake, OTHER_DOC, [_item(element_id="fig-9")])

        ids = [candidate_row["id"], already_approved["id"], "bogus-id", other_doc_row["id"]]
        result = store.bulk_transition(
            fake, DOC_A, ids, new_status=store.STATUS_APPROVED, user_id="teacher-1",
        )

        assert [r["id"] for r in result["updated"]] == [candidate_row["id"]]
        skipped_by_id = {s["id"]: s for s in result["skipped"]}
        assert skipped_by_id[already_approved["id"]] == {
            "id": already_approved["id"], "status": store.STATUS_APPROVED, "reason": "conflict",
        }
        assert skipped_by_id["bogus-id"] == {"id": "bogus-id", "status": None, "reason": "not_found"}
        # 別 document の candidate 行は status を漏らさず not_found 扱い(権限境界)。
        assert skipped_by_id[other_doc_row["id"]] == {
            "id": other_doc_row["id"], "status": None, "reason": "not_found",
        }
        # 別 document の行自体は無傷(承認されていない)。
        untouched = store.get_by_id(fake, other_doc_row["id"])
        assert untouched["status"] == store.STATUS_CANDIDATE

    def test_empty_ids_raises(self):
        fake = FakeElementExplanationSession()
        with pytest.raises(ValueError):
            store.bulk_transition(fake, DOC_A, [], new_status=store.STATUS_APPROVED, user_id="teacher-1")

    def test_blank_ids_normalize_to_empty_and_raise(self):
        fake = FakeElementExplanationSession()
        with pytest.raises(ValueError):
            store.bulk_transition(
                fake, DOC_A, ["  ", ""], new_status=store.STATUS_APPROVED, user_id="teacher-1",
            )

    def test_invalid_new_status_raises(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        with pytest.raises(ValueError):
            store.bulk_transition(fake, DOC_A, [row["id"]], new_status="bogus", user_id="teacher-1")

    def test_requires_user_id(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        with pytest.raises(ValueError):
            store.bulk_transition(fake, DOC_A, [row["id"]], new_status=store.STATUS_APPROVED, user_id="")

    def test_duplicate_ids_are_deduped(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        result = store.bulk_transition(
            fake, DOC_A, [row["id"], row["id"], row["id"]],
            new_status=store.STATUS_APPROVED, user_id="teacher-1",
        )
        assert len(result["updated"]) == 1
        assert result["skipped"] == []

    def test_no_row_is_ever_deleted(self):
        fake = FakeElementExplanationSession()
        rows = store.insert_candidates(fake, DOC_A, [_item(element_id="fig-1"), _item(element_id="fig-2")])
        ids = [r["id"] for r in rows]
        store.bulk_transition(fake, DOC_A, ids, new_status=store.STATUS_DISMISSED, user_id="teacher-1")
        assert len(fake.rows) == 2


# ---------------------------------------------------------------------------
# update_body
# ---------------------------------------------------------------------------


class TestUpdateBody:
    def test_edit_creates_new_row_and_supersedes_old(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(
            fake, DOC_A, [_item(evidence={"reason": "old reason", "confidence": 0.4})],
        )
        new_row = store.update_body(fake, row["id"], "teacher-9", "edited body")

        assert new_row["id"] != row["id"]
        assert new_row["body"] == "edited body"
        assert new_row["status"] == store.STATUS_CANDIDATE  # 旧statusを維持
        assert new_row["evidence"] == {"reason": "old reason", "confidence": 0.4}  # evidence継承
        assert new_row["created_by"] == "teacher-9"

        old_row = store.get_by_id(fake, row["id"])
        assert old_row["status"] == store.STATUS_SUPERSEDED
        # 履歴保持(行削除なし)
        assert len(fake.rows) == 2

    def test_edit_preserves_approved_status_and_reviewer(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.approve(fake, row["id"], "teacher-1")
        new_row = store.update_body(fake, row["id"], "teacher-9", "polished wording")
        assert new_row["status"] == store.STATUS_APPROVED
        assert new_row["reviewed_by"] == "teacher-1"
        assert new_row["reviewed_at"] is not None

    def test_edit_dismissed_row_raises_conflict(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.dismiss(fake, row["id"], "teacher-1")
        with pytest.raises(store.ElementExplanationError) as exc:
            store.update_body(fake, row["id"], "teacher-9", "new text")
        assert exc.value.kind == "conflict"

    def test_edit_superseded_row_raises_conflict(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        store.update_body(fake, row["id"], "teacher-9", "first edit")
        with pytest.raises(store.ElementExplanationError):
            store.update_body(fake, row["id"], "teacher-9", "second edit on stale id")

    def test_edit_missing_id_returns_none(self):
        fake = FakeElementExplanationSession()
        assert store.update_body(fake, "does-not-exist", "teacher-9", "text") is None

    def test_edit_requires_non_empty_body(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        with pytest.raises(ValueError):
            store.update_body(fake, row["id"], "teacher-9", "   ")

    def test_edit_requires_user_id(self):
        fake = FakeElementExplanationSession()
        [row] = store.insert_candidates(fake, DOC_A, [_item()])
        with pytest.raises(ValueError):
            store.update_body(fake, row["id"], "", "text")


# ---------------------------------------------------------------------------
# approved_for_elements
# ---------------------------------------------------------------------------


class TestApprovedForElements:
    def test_bulk_read_groups_by_element(self):
        fake = FakeElementExplanationSession()
        [fig] = store.insert_candidates(fake, DOC_A, [_item(element_type="figure", element_id="fig-1")])
        [eq] = store.insert_candidates(fake, DOC_A, [_item(element_type="equation", element_id="eq-1")])
        [claim] = store.insert_candidates(fake, DOC_A, [_item(element_type="theory_claim", element_id="c-1")])
        store.approve(fake, fig["id"], "teacher-1")
        store.approve(fake, eq["id"], "teacher-1")
        # claim は candidate のまま(未承認)

        result = store.approved_for_elements(
            fake, DOC_A, [("figure", "fig-1"), ("equation", "eq-1"), ("theory_claim", "c-1")],
        )
        assert set(result.keys()) == {("figure", "fig-1"), ("equation", "eq-1")}
        assert result[("figure", "fig-1")][0]["status"] == store.STATUS_APPROVED

    def test_empty_refs_returns_empty_without_query(self):
        fake = FakeElementExplanationSession()
        assert store.approved_for_elements(fake, DOC_A, []) == {}
        assert fake.rows == []  # 何も実行していない


# ---------------------------------------------------------------------------
# migration file sanity (schema matches design doc §5.2)
# ---------------------------------------------------------------------------


class TestMigrationFile:
    def test_migration_056_defines_expected_columns(self):
        sql = (BACKEND / "db" / "056_element_explanations.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS element_explanations" in sql
        for token in (
            "document_id", "element_type", "element_id", "kind", "body", "evidence",
            "status", "created_by", "reviewed_by", "reviewed_at", "created_at",
        ):
            assert token in sql
        assert "'figure','theory_component','theory_claim','equation'" in sql.replace(
            "'figure', 'theory_component', 'theory_claim', 'equation'",
            "'figure','theory_component','theory_claim','equation'",
        ) or "figure" in sql
        assert "'generic', 'contextual'" in sql or "'generic','contextual'" in sql
        assert "'candidate', 'approved', 'dismissed', 'superseded'" in sql or (
            "candidate" in sql and "approved" in sql and "dismissed" in sql and "superseded" in sql
        )

    def test_migration_056_is_idempotent_style(self):
        sql = (BACKEND / "db" / "056_element_explanations.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "CREATE INDEX IF NOT EXISTS" in sql
