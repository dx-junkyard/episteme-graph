"""IK-0371: 学ぶ単位の handle を位置ではなく参照キー（stable_key）で登録時に解決する。

handle（``U3``）は「草案を出したターンの教材集合を material_ids 順に並べた候補表の位置」で、
登録時の sources が違うと別の単位を指す。固定するのは:

1. 同じ単位でも教材集合が違えば handle は変わる（位置であることの確認）。
2. コースビルダーが草案に handle → 参照キーを同梱し、units を ``{handle, stable_key}`` に直す。
3. 登録は参照キーで解決する — 別の候補表でも同じ単位に解決され、handle だけなら別の単位になる。
4. 参照キーが登録時の候補表に無い単位は handle へフォールバックせず捨て、その事実を記帳する。
5. admin.js::cbDraftUnitHandles が参照キーを落とさない（ES5 静的契約）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core import course_units  # noqa: E402

DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"

_ROWS = [
    # (unit_id, document_id, stable_key, unit_kind, label, summary, order_index, section_ids)
    ("ua1", DOC_A, "k1:a-1", "section_block", "中性子星の音速", "", 0, ["s1"]),
    ("ua2", DOC_A, "k1:a-2", "section_block", "TOV 方程式", "", 1, ["s2"]),
    ("ub1", DOC_B, "k1:b-1", "section_block", "Cepheus B の星形成", "", 0, ["s1"]),
    ("ub2", DOC_B, "k1:b-2", "section_block", "分子雲の電離", "", 1, ["s2"]),
]


def _table(document_ids: list[str]) -> list[course_units.UnitCandidate]:
    rows = [r for r in _ROWS if r[1] in document_ids]
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    return course_units.list_unit_candidates(session, document_ids)


# ---------------------------------------------------------------------------
# 1〜4. core
# ---------------------------------------------------------------------------

def test_handle_is_a_position_that_changes_with_the_material_set():
    two = _table([DOC_A, DOC_B])
    one = _table([DOC_B])
    assert {c.handle: c.stable_key for c in two}["U3"] == "k1:b-1"
    assert {c.handle: c.stable_key for c in one}["U1"] == "k1:b-1"


def test_stable_key_resolves_to_the_same_unit_across_tables_but_bare_handle_does_not():
    builder_turn = _table([DOC_A, DOC_B])   # 草案を出したターン（2 論文）
    registration = _table([DOC_B])          # 登録時の sources（1 論文）

    draft_units = course_units.normalize_draft_unit_refs(builder_turn, ["U3"])
    assert draft_units == [{"handle": "U3", "stable_key": "k1:b-1"}]

    by_key = course_units.resolve_unit_handles(registration, draft_units)
    assert [u["stable_key"] for u in by_key] == ["k1:b-1"]
    assert by_key[0]["label"] == "Cepheus B の星形成"

    # handle だけでは登録時の候補表の位置で引く（別の単位になる / 候補に無ければ落ちる）。
    by_handle = course_units.resolve_unit_handles(registration, ["U1"])
    builder_u1 = course_units.normalize_draft_unit_refs(builder_turn, ["U1"])[0]["stable_key"]
    assert builder_u1 == "k1:a-1"
    assert by_handle[0]["stable_key"] != builder_u1
    assert course_units.resolve_unit_handles(registration, ["U3"]) == []


def test_key_missing_is_dropped_without_falling_back_to_the_handle():
    registration = _table([DOC_B])
    # 草案では U1 = 論文 A の単位。登録時の候補表では U1 は論文 B の単位 → 付けてはならない。
    resolved, stats = course_units.resolve_unit_refs(
        registration, [{"handle": "U1", "stable_key": "k1:a-1"}]
    )
    assert resolved == []
    assert stats["key_missing"] is True and stats["dropped"] is True


def test_normalize_keeps_unknown_handles_as_strings_and_does_not_invent_keys():
    table = _table([DOC_A])
    out = course_units.normalize_draft_unit_refs(table, ["U2", "U9", "u2", 3, {"handle": "U1"}])
    assert out == [
        {"handle": "U2", "stable_key": "k1:a-2"},
        "U9",
        {"handle": "U1", "stable_key": "k1:a-1"},
    ]
    # 前のターンから持ち越した参照キー（このターンの表に無い）はそのまま残す。
    carried = course_units.normalize_draft_unit_refs(table, [{"handle": "U7", "stable_key": "k1:b-2"}])
    assert carried == [{"handle": "U7", "stable_key": "k1:b-2"}]
    # 表にある参照キーは handle をこのターンの handle に揃える。
    realigned = course_units.normalize_draft_unit_refs(table, [{"handle": "U9", "stable_key": "k1:a-1"}])
    assert realigned == [{"handle": "U1", "stable_key": "k1:a-1"}]


def test_candidate_key_table():
    assert course_units.candidate_key_table(_table([DOC_A, DOC_B])) == {
        "U1": "k1:a-1", "U2": "k1:a-2", "U3": "k1:b-1", "U4": "k1:b-2",
    }


# ---------------------------------------------------------------------------
# 登録（routes.learning._bind_topic_units）
# ---------------------------------------------------------------------------

class _NoSqlSession:
    def execute(self, *_a, **_k):  # pragma: no cover - 呼ばれない経路の保険
        raise AssertionError("SQL must not be issued in this test")

    def close(self):
        pass


def test_registration_resolves_by_key_and_records_the_dropped_fact(monkeypatch):
    import routes.learning as learning_routes

    registration = _table([DOC_B])
    monkeypatch.setattr(learning_routes, "_ordered_source_document_ids", lambda _s, _m, **_k: [DOC_B])
    monkeypatch.setattr(learning_routes, "list_unit_candidates", lambda _s, _d: list(registration))
    data = {
        "sources": [{"material_id": "m-b"}],
        "topics": [
            {"id": "t0", "title": "星形成", "units": [{"handle": "U3", "stable_key": "k1:b-1"}]},
            {"id": "t1", "title": "中性子星", "units": [{"handle": "U1", "stable_key": "k1:a-1"}]},
        ],
    }
    topics, info = learning_routes._bind_topic_units(_NoSqlSession(), data, user_id="u1")
    assert [u["label"] for u in topics[0]["units"]] == ["Cepheus B の星形成"]
    # 論文 A は sources から外れたので、U1 が論文 B の単位に黙って化けない。
    assert topics[1]["units"] == []
    assert info["outside_sources"] is True and info["unresolved"] is True

    recorded: list = []
    monkeypatch.setattr(learning_routes, "record_review_event", lambda *a, **k: recorded.append(a))
    learning_routes._record_course_registration("c1", "u1", info)
    assert recorded and recorded[0][5]["unit_refs_outside_sources"] is True


def test_registration_string_handles_keep_the_old_behaviour(monkeypatch):
    import routes.learning as learning_routes

    registration = _table([DOC_B])
    monkeypatch.setattr(learning_routes, "_ordered_source_document_ids", lambda _s, _m, **_k: [DOC_B])
    monkeypatch.setattr(learning_routes, "list_unit_candidates", lambda _s, _d: list(registration))
    data = {"sources": [{"material_id": "m-b"}], "topics": [{"id": "t0", "units": ["U1"]}]}
    topics, info = learning_routes._bind_topic_units(_NoSqlSession(), data, user_id="u1")
    assert [u["stable_key"] for u in topics[0]["units"]] == ["k1:b-1"]
    assert info["outside_sources"] is False


# ---------------------------------------------------------------------------
# 2. コースビルダーの草案に対応表を同梱する
# ---------------------------------------------------------------------------

def test_attach_unit_identity_to_draft_overrides_llm_supplied_table():
    import routes.admin as routes_admin

    table = _table([DOC_A, DOC_B])
    draft = {
        "unit_candidate_keys": {"U1": "k1:invented"},  # LLM が履歴から写したもの（信用しない）
        "chapters": [
            {"title": "c", "topics": [{"title": "a", "units": ["U3", "U99"]}, "文字列のトピック"]},
        ],
    }
    routes_admin._attach_unit_identity_to_draft(draft, table)
    assert draft["unit_candidate_keys"]["U1"] == "k1:a-1"
    assert draft["unit_candidate_keys"]["U3"] == "k1:b-1"
    assert draft["chapters"][0]["topics"][0]["units"] == [{"handle": "U3", "stable_key": "k1:b-1"}, "U99"]

    no_table = {"unit_candidate_keys": {"U1": "k1:x"}, "chapters": []}
    routes_admin._attach_unit_identity_to_draft(no_table, [])
    assert "unit_candidate_keys" not in no_table


try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except (ImportError, RuntimeError):  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
def test_chat_returns_and_saves_the_identity_table(monkeypatch):
    import routes.admin as routes_admin
    from api.main import app
    from core.llm_worker.cost_gate import CostGate
    from dependencies import ROLE_TEACHER, _create_token

    table = _table([DOC_A, DOC_B])

    def fake_context(material_ids, **kwargs):
        kwargs["unit_candidate_objects_out"].extend(table)
        return "## 教材"

    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = []
    saved: dict = {}

    def fake_save(user_id, session_id, hist, draft):
        saved["draft"] = draft
        return True

    monkeypatch.setattr(routes_admin, "_course_builder_cost_gate", CostGate())
    monkeypatch.setattr(routes_admin, "_build_material_context", fake_context)
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: fake_session)
    monkeypatch.setattr(routes_admin, "save_cb_session", fake_save)
    monkeypatch.setattr(
        routes_admin, "generate_text",
        lambda *a, **k: 'はい。\n---COURSE_DRAFT_JSON---\n{"title": "T", "chapters": '
                        '[{"title": "c", "topics": [{"title": "a", "units": ["U3"]}]}]}',
    )
    token = _create_token("33333333-3333-3333-3333-333333333333", "teacher1", "t@test.com", ROLE_TEACHER)
    resp = TestClient(app).post(
        "/api/admin/course-builder/chat",
        json={"message": "作って", "session_id": "s1", "selected_material_ids": ["m-a", "m-b"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    draft = resp.json()["course_draft"]
    assert draft["unit_candidate_keys"]["U3"] == "k1:b-1"
    assert draft["chapters"][0]["topics"][0]["units"] == [{"handle": "U3", "stable_key": "k1:b-1"}]
    assert saved["draft"]["unit_candidate_keys"] == draft["unit_candidate_keys"]


# ---------------------------------------------------------------------------
# 5. admin.js の静的契約
# ---------------------------------------------------------------------------

_ADMIN_JS = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = _ADMIN_JS.index(f"function {name}(")
    tail = _ADMIN_JS[start + 1:]
    rel_end = tail.find("\n  function ")
    return _ADMIN_JS[start: start + 1 + rel_end if rel_end != -1 else len(_ADMIN_JS)]


def test_cb_draft_unit_handles_keeps_stable_key():
    body = _function("cbDraftUnitHandles")
    assert "item.stable_key" in body
    assert "unit_candidate_keys" in body
    assert "{ handle: handle, stable_key: stableKey }" in body
    # ES5（admin.js の規律）
    for token in ("=>", "const ", "let ", "`"):
        assert token not in body


def test_cb_unit_display_name_accepts_identity_dicts():
    body = _function("cbUnitDisplayName")
    assert 'typeof handle === "object"' in body
