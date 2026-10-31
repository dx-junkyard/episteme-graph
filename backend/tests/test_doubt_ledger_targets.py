"""台帳行の document_id 解決（core/doubt/ledger_targets.py）— 実機 2026-10-03 の NotNullViolation の是正。"""

from __future__ import annotations

import pytest

from core.doubt import ledger_targets as lt


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeSession:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or {}
        self.fail = fail
        self.sql: list[tuple[str, dict]] = []
        self.rolled_back = 0

    def execute(self, clause, params=None):
        text = str(getattr(clause, "text", clause))
        self.sql.append((text, dict(params or {})))
        if self.fail:
            raise RuntimeError("boom")
        for key, row in self.rows.items():
            if key in text:
                return _Result(row)
        return _Result(None)

    def rollback(self):
        self.rolled_back += 1


def test_resolve_claim_document_id_uses_live_view():
    s = FakeSession(rows={"theory_claims_live": ("11111111-1111-1111-1111-111111111111",)})
    assert lt.resolve_target_document_id(s, "claim", "c1") == "11111111-1111-1111-1111-111111111111"
    assert "theory_claims_live" in s.sql[0][0]


def test_resolve_unknown_target_type_is_none_without_sql():
    s = FakeSession()
    assert lt.resolve_target_document_id(s, "figure", "x") is None
    assert s.sql == []


def test_resolve_failure_rolls_back_and_returns_none():
    s = FakeSession(fail=True)
    assert lt.resolve_target_document_id(s, "claim", "c1") is None
    assert s.rolled_back == 1


def test_ensure_ledger_row_inserts_document_id():
    s = FakeSession()
    doc = lt.ensure_ledger_row(s, target_type="claim", target_id="c1", document_id="22222222-2222-2222-2222-222222222222")
    assert doc == "22222222-2222-2222-2222-222222222222"
    insert = [q for q in s.sql if "INSERT INTO epistemic_ledger" in q[0]]
    assert len(insert) == 1
    assert "document_id" in insert[0][0] and insert[0][1]["doc"] == doc
    # IK-0899（migration 092）: 行キーは (document_id, target_type, target_id)。
    assert "ON CONFLICT ON CONSTRAINT epistemic_ledger_document_target_key DO NOTHING" in insert[0][0]


def test_ensure_ledger_row_resolves_when_missing_and_raises_when_unknown():
    s = FakeSession(rows={"theory_components_live": ("33333333-3333-3333-3333-333333333333",)})
    # 部品の UUID は全体で一意なので、部品から論文を引いてよい。
    assert lt.ensure_ledger_row(
        s, target_type="component", target_id="44444444-4444-4444-4444-444444444444"
    ).startswith("3333")
    with pytest.raises(ValueError):
        lt.ensure_ledger_row(FakeSession(), target_type="claim", target_id="nope")


def test_scoped_target_without_document_is_refused():
    """IK-0899: グラフのノード id・式の agent ID は論文の中でしか一意でない — 推測で論文を埋めない。"""
    for target_type, target_id in (("component", "theory_op_0001"), ("equation", "eq_dcf")):
        with pytest.raises(lt.LedgerDocumentRequired):
            lt.ensure_ledger_row(FakeSession(), target_type=target_type, target_id=target_id)
        with pytest.raises(lt.LedgerDocumentRequired):
            lt.ledger_row_filter(target_type=target_type, target_id=target_id, document_id=None)


def test_row_filter_includes_document_only_for_scoped_targets():
    doc = "55555555-5555-5555-5555-555555555555"
    where, params = lt.ledger_row_filter(target_type="component", target_id="theory_op_0001", document_id=doc)
    assert "document_id = CAST(:doc AS uuid)" in where and params["doc"] == doc
    where, params = lt.ledger_row_filter(target_type="claim", target_id="c1", document_id=doc)
    assert "document_id" not in where and "doc" not in params


def test_every_ledger_target_type_has_a_lookup():
    from core.doubt.schema import TargetType

    assert set(lt.LEDGER_TARGET_TYPES) == {t.value for t in TargetType}
