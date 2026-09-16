"""学ぶ単位のコース側ヘルパー（``core/course_units.py``）のテスト。

正本: ``docs/features/learning_units_design.md`` §6.2（P2-3）。ここで固定するのは:

1. 候補の並びと handle が **決定論**（DB の返す行順に依存しない）。
2. 候補提示は ``LEARNING_UNIT_KINDS_FOR_COURSE`` に限り、``dismissed`` を外す。
3. 候補に無い handle は**捨てる**（捏造ガード = LU3）。
4. 読むのは ``learning_units_live`` ビューだけ（基表を SELECT しない = KO5）。
5. 候補区画に数値を書かない（LU5）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# routes.admin（コースビルダーへの配線）を import できるようにする
# （test_course_builder_context.py と同じ作法）。
_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core import course_units
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED
from core.schema import LEARNING_UNIT_KINDS_FOR_COURSE


DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"


def _candidate_row(
    unit_id: str,
    document_id: str,
    stable_key: str,
    unit_kind: str,
    label: str,
    summary: str = "",
    order_index: int = 0,
):
    """``list_unit_candidates`` の SELECT 列順のタプル。"""
    return (unit_id, document_id, stable_key, unit_kind, label, summary, order_index)


def _session_returning(rows):
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    return session


# ---------------------------------------------------------------------------
# 1. 候補の決定論
# ---------------------------------------------------------------------------

def test_candidate_order_is_deterministic_regardless_of_row_order():
    """DB の返す行順を入れ替えても handle の割り当ては変わらない。"""
    rows = [
        _candidate_row("u-3", DOC_B, "k1:b-sec", "section_block", "B の章", order_index=0),
        _candidate_row("u-1", DOC_A, "k1:a-sec1", "section_block", "A の章1", order_index=0),
        _candidate_row("u-2", DOC_A, "k1:a-thesis", "thesis_support", "A の主張", order_index=0),
        _candidate_row("u-4", DOC_A, "k1:a-sec2", "section_block", "A の章2", order_index=1),
    ]
    forward = course_units.list_unit_candidates(_session_returning(rows), [DOC_A, DOC_B])
    reverse = course_units.list_unit_candidates(_session_returning(list(reversed(rows))), [DOC_A, DOC_B])

    assert [c.handle for c in forward] == ["U1", "U2", "U3", "U4"]
    assert [c.stable_key for c in forward] == [c.stable_key for c in reverse]
    # document_ids の順 → kind の宣言順 → order_index の順
    assert [c.stable_key for c in forward] == [
        "k1:a-sec1", "k1:a-sec2", "k1:a-thesis", "k1:b-sec",
    ]


def test_candidates_follow_document_id_argument_order():
    """document の並びは引数の順（DB 行順ではない）。"""
    rows = [
        _candidate_row("u-1", DOC_A, "k1:a", "section_block", "A"),
        _candidate_row("u-2", DOC_B, "k1:b", "section_block", "B"),
    ]
    candidates = course_units.list_unit_candidates(_session_returning(rows), [DOC_B, DOC_A])
    assert [c.stable_key for c in candidates] == ["k1:b", "k1:a"]


def test_no_documents_issues_no_sql():
    session = MagicMock()
    assert course_units.list_unit_candidates(session, []) == []
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 提示の絞り込み（種別 / dismissed / 上限）
# ---------------------------------------------------------------------------

def test_query_reads_the_live_view_and_excludes_dismissed():
    """``learning_units_live`` だけを読み、``dismissed`` を SQL で外す（KO5 / LU2）。"""
    session = _session_returning([])
    course_units.list_unit_candidates(session, [DOC_A])

    sql = str(session.execute.call_args.args[0])
    assert "learning_units_live" in sql
    # 基表を直接 SELECT しない（``FROM learning_units`` 単体が現れない）。
    assert "FROM learning_units\n" not in sql
    assert "review_status <> 'dismissed'" in sql
    # document_id は uuid 列なのでキャストして束縛する（migration 080 以降）。
    assert "CAST(:did_0 AS uuid)" in sql


def test_only_course_facing_kinds_are_offered():
    """``dsl_node`` はコースビルダーへ出さない（LEARNING_UNIT_KINDS_FOR_COURSE）。"""
    session = _session_returning([
        _candidate_row("u-1", DOC_A, "k1:node", "dsl_node", "概念ノード"),
    ])
    # SQL の IN 句に dsl_node が入らない（サーバ側で絞る）。
    assert course_units.list_unit_candidates(session, [DOC_A]) == []
    params = session.execute.call_args.args[1]
    offered = {value for key, value in params.items() if key.startswith("kind_")}
    assert offered == set(LEARNING_UNIT_KINDS_FOR_COURSE)
    assert "dsl_node" not in offered


def test_figure_candidates_are_capped_per_document():
    """figure は document ごとに上限まで（設計書 §6.2）。他の種別は全件。"""
    rows = [
        _candidate_row(f"u-{i}", DOC_A, f"k1:fig{i}", "figure", f"図{i}", order_index=i)
        for i in range(12)
    ] + [
        _candidate_row(f"s-{i}", DOC_A, f"k1:sec{i}", "section_block", f"章{i}", order_index=i)
        for i in range(12)
    ]
    candidates = course_units.list_unit_candidates(_session_returning(rows), [DOC_A])
    figures = [c for c in candidates if c.unit_kind == "figure"]
    sections = [c for c in candidates if c.unit_kind == "section_block"]
    assert len(figures) == course_units.UNIT_KIND_CANDIDATE_LIMITS["figure"]
    assert len(sections) == 12


# ---------------------------------------------------------------------------
# 3. handle の解決（捏造ガード）
# ---------------------------------------------------------------------------

def _candidates():
    return course_units.list_unit_candidates(
        _session_returning([
            _candidate_row("u-1", DOC_A, "k1:a", "section_block", "章A"),
            _candidate_row("u-2", DOC_A, "k1:b", "thesis_support", "主張B"),
        ]),
        [DOC_A],
    )


def test_resolve_unit_handles_maps_to_topic_units():
    resolved = course_units.resolve_unit_handles(_candidates(), ["U2"])
    assert resolved == [{
        "kind": "thesis_support",
        "stable_key": "k1:b",
        "unit_id": "u-2",
        "label": "主張B",
        "source": UNIT_SOURCE_TEACHER_SELECTED,
    }]


def test_resolve_unit_handles_drops_invented_handles():
    """候補に無い handle は捨てる（LU3 の捏造ガード）。"""
    resolved = course_units.resolve_unit_handles(_candidates(), ["U9", "U1", "存在しない"])
    assert [item["stable_key"] for item in resolved] == ["k1:a"]


def test_resolve_unit_handles_dedups_and_keeps_input_order():
    resolved = course_units.resolve_unit_handles(_candidates(), ["U2", "u2", "U1"])
    assert [item["stable_key"] for item in resolved] == ["k1:b", "k1:a"]


def test_resolve_unit_handles_accepts_resolved_dicts_only_via_the_same_gate():
    """解決済み dict でも候補表に無い stable_key は通さない。"""
    resolved = course_units.resolve_unit_handles(
        _candidates(),
        [{"stable_key": "k1:a"}, {"stable_key": "k1:missing"}],
    )
    assert [item["stable_key"] for item in resolved] == ["k1:a"]


@pytest.mark.parametrize("handles", [None, "U1", 3, {"U1": True}])
def test_resolve_unit_handles_rejects_non_list_input(handles):
    assert course_units.resolve_unit_handles(_candidates(), handles) == []


# ---------------------------------------------------------------------------
# 4. 候補区画（数値を書かない）
# ---------------------------------------------------------------------------

def test_render_block_lists_handles_without_numbers():
    block = course_units.render_unit_candidates_block(_candidates())
    assert "U1" in block and "U2" in block
    assert "章A" in block and "主張B" in block
    # 件数・confidence・order_index のような数値は書かない（LU5）。
    for forbidden in ("件", "confidence", "order_index", "スコア"):
        assert forbidden not in block


def test_render_block_is_empty_without_candidates():
    assert course_units.render_unit_candidates_block([]) == ""


def test_unit_kind_label_falls_back_to_the_kind_itself(monkeypatch):
    """語彙表が未整備でも落ちない（fail-soft）。"""
    from core import label_vocab

    monkeypatch.delattr(label_vocab, "LEARNING_UNIT_KIND_LABELS", raising=False)
    assert course_units.unit_kind_label("section_block") == "section_block"


# ---------------------------------------------------------------------------
# 5. freeze 向けの読み
# ---------------------------------------------------------------------------

def _unit_row(stable_key: str, *, agent_payload=None, claims=None, equations=None):
    return (
        "u-1", DOC_A, stable_key, "parent_component", "親ラベル", "要約",
        claims or [], equations or [], ["db-uuid"], [], agent_payload or {}, 0,
    )


def test_load_units_by_keys_scopes_by_document_and_skips_empty():
    session = MagicMock()
    assert course_units.load_units_by_keys(session, [DOC_A], []) == {}
    assert course_units.load_units_by_keys(session, [], ["k1:a"]) == {}
    session.execute.assert_not_called()

    session = _session_returning([_unit_row("k1:a")])
    units = course_units.load_units_by_keys(session, [DOC_A], ["k1:a"])
    assert set(units) == {"k1:a"}
    sql = str(session.execute.call_args.args[0])
    assert "learning_units_live" in sql
    assert "stable_key IN" in sql


def test_load_units_for_documents_reads_the_live_view():
    session = _session_returning([_unit_row("k1:a")])
    units = course_units.load_units_for_documents(session, [DOC_A])
    assert units["k1:a"]["label"] == "親ラベル"
    assert "learning_units_live" in str(session.execute.call_args.args[0])

    session = MagicMock()
    assert course_units.load_units_for_documents(session, []) == {}
    session.execute.assert_not_called()


def test_component_agent_ids_come_from_agent_payload_only():
    """DB UUID（linked_component_ids）を artifact 名前空間へ混ぜない（§4.1）。"""
    row = course_units.load_units_for_documents(
        _session_returning([
            _unit_row("k1:a", agent_payload={"linked_component_agent_ids": ["comp_001", "comp_002"]}),
        ]),
        [DOC_A],
    )["k1:a"]
    assert course_units.unit_component_agent_ids(row) == ["comp_001", "comp_002"]
    # DB UUID 側は component の agent ID として返さない。
    assert "db-uuid" not in course_units.unit_component_agent_ids(row)


def test_equation_and_claim_agent_id_helpers():
    row = course_units.load_units_for_documents(
        _session_returning([
            _unit_row(
                "k1:a",
                agent_payload={"claim_ids": ["claim_1"]},
                claims=["550e8400-e29b-41d4-a716-446655440000"],
                equations=["eq_2_7", "eq_2_7"],
            ),
        ]),
        [DOC_A],
    )["k1:a"]
    assert course_units.unit_equation_agent_ids(row) == ["eq_2_7"]
    # agent 側 ID を先に返し、UUID も候補には残る（呼び出し側が artifact 索引で絞る）。
    assert course_units.unit_claim_agent_ids(row)[0] == "claim_1"


def test_helpers_are_safe_on_non_dict_rows():
    for helper in (
        course_units.unit_component_agent_ids,
        course_units.unit_equation_agent_ids,
        course_units.unit_claim_agent_ids,
    ):
        assert helper(None) == []
        assert helper("not a row") == []


# ---------------------------------------------------------------------------
# 6. ガードレール
# ---------------------------------------------------------------------------

def test_core_module_does_not_import_fastapi_or_llm():
    """``core/`` 共通ルール + LU3（LLM 0 回）を構造的に固定する。"""
    from pathlib import Path

    from tests.guardrail_helpers import assert_source_forbids

    src = (Path(__file__).resolve().parents[1] / "core" / "course_units.py").read_text(encoding="utf-8")
    assert_source_forbids(
        src,
        [
            "import fastapi", "from fastapi",
            "import core.llm", "from core.llm",
            "import openai", "from openai",
        ],
        context="core/course_units.py",
    )


# ---------------------------------------------------------------------------
# 7. コースビルダーへの配線（_build_material_context / システムプロンプト）
# ---------------------------------------------------------------------------

def test_material_context_appends_the_candidate_block(monkeypatch):
    """``_build_material_context`` の末尾に「学ぶ単位の候補」区画が付く。

    handle は document を跨いだ通し番号なので、教材ごとではなく末尾に1区画。
    """
    import routes.admin as routes_admin

    session = MagicMock()
    session.execute.return_value.fetchall.side_effect = [
        [("mat-1", "論文A", "a.pdf", DOC_A, "completed", {})],  # documents
        [],  # theory_components_live
        [],  # theory_component_graphs
        [],  # theory_claims_live
        [],  # chunks
    ]
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})
    monkeypatch.setattr(
        routes_admin,
        "list_unit_candidates",
        lambda *a, **k: course_units.list_unit_candidates(
            _session_returning([
                _candidate_row("u-1", DOC_A, "k1:a", "section_block", "観測量の構成"),
            ]),
            [DOC_A],
        ),
    )

    ctx = routes_admin._build_material_context(["mat-1"])
    assert ctx is not None
    assert "学ぶ単位の候補" in ctx
    assert "U1" in ctx and "観測量の構成" in ctx


def test_material_context_is_fail_soft_when_units_are_unavailable(monkeypatch):
    """表が無い / 読めない環境では区画ごと消えるだけ（コース設計は止めない）。"""
    import routes.admin as routes_admin

    session = MagicMock()
    session.execute.return_value.fetchall.side_effect = [
        [("mat-1", "論文A", "a.pdf", DOC_A, "completed", {})],
        [], [], [], [],
    ]
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})

    def _boom(*a, **k):
        raise RuntimeError("relation \"learning_units_live\" does not exist")

    monkeypatch.setattr(routes_admin, "list_unit_candidates", _boom)

    ctx = routes_admin._build_material_context(["mat-1"])
    assert ctx is not None
    assert "学ぶ単位の候補" not in ctx
    session.rollback.assert_called_once()


def test_course_builder_prompt_declares_the_units_rule():
    """topic スキーマに units を足し、候補の handle だけを使う規則を明示する（LU3）。"""
    from routes.admin import _COURSE_BUILDER_SYSTEM_PROMPT as prompt

    assert '"units": ["U3"]' in prompt
    assert "学ぶ単位の候補" in prompt
    assert "候補に無い handle を書いてはならない" in prompt
    # 候補が無ければ空配列（発明させない）。
    assert "空配列 []" in prompt


# ---------------------------------------------------------------------------
# 8. 可視性ゲートと信頼境界（P2-R5 / P2-R6）
# ---------------------------------------------------------------------------

def _material_context_session(rows):
    session = MagicMock()
    session.execute.return_value.fetchall.side_effect = [rows, [], [], [], []]
    return session


def test_material_context_drops_documents_the_user_cannot_see(monkeypatch):
    """sources に material_id が書いてあるだけでは読んでよい根拠にならない（P2-R5）。"""
    import routes.admin as routes_admin

    session = _material_context_session([
        ("mat-1", "見える論文", "a.pdf", DOC_A, "completed", {}),
        ("mat-2", "見えない論文", "b.pdf", DOC_B, "completed", {}),
    ])
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})
    monkeypatch.setattr(routes_admin, "list_unit_candidates", lambda *a, **k: [])
    monkeypatch.setattr(routes_admin, "list_visible_document_ids", lambda _u: [DOC_A])

    ctx = routes_admin._build_material_context(["mat-1", "mat-2"], user_id="u1")
    assert ctx is not None
    assert "見える論文" in ctx
    assert "見えない論文" not in ctx


def test_material_context_is_none_when_nothing_is_visible(monkeypatch):
    import routes.admin as routes_admin

    session = _material_context_session([("mat-1", "論文A", "a.pdf", DOC_A, "completed", {})])
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})
    monkeypatch.setattr(routes_admin, "list_unit_candidates", lambda *a, **k: [])
    monkeypatch.setattr(routes_admin, "list_visible_document_ids", lambda _u: [])

    assert routes_admin._build_material_context(["mat-1"], user_id="u1") is None


def test_material_context_opens_with_the_untrusted_source_notice(monkeypatch):
    """資料本文は第三者が書いた untrusted 入力（開発ルール4・TB2）。"""
    import routes.admin as routes_admin
    from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE

    session = _material_context_session([("mat-1", "論文A", "a.pdf", DOC_A, "completed", {})])
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})
    monkeypatch.setattr(routes_admin, "list_unit_candidates", lambda *a, **k: [])

    ctx = routes_admin._build_material_context(["mat-1"])
    assert ctx is not None
    assert ctx.startswith(UNTRUSTED_SOURCE_NOTICE)


def test_candidate_block_strips_control_sequences_from_pdf_text():
    """PDF 由来の label / summary に混ざった ANSI 残骸をプロンプトへ流さない。"""
    candidate = course_units.UnitCandidate(
        handle="U1",
        unit_id="u-1",
        stable_key="k1:a",
        unit_kind="section_block",
        label="\x1b[0m観測量の構成[0m",
        summary="[1;32m要約\x1b[0m",
        document_id=DOC_A,
    )
    block = course_units.render_unit_candidates_block([candidate])
    assert "観測量の構成" in block and "要約" in block
    assert "\x1b" not in block and "[0m" not in block and "[1;32m" not in block


def test_material_context_reports_the_candidate_table_to_the_caller(monkeypatch):
    """P2-R10: プレビューが handle ではなく単位の名前を出せる材料を返す。"""
    import routes.admin as routes_admin

    session = _material_context_session([("mat-1", "論文A", "a.pdf", DOC_A, "completed", {})])
    monkeypatch.setattr(routes_admin, "_pg_session", lambda: session)
    monkeypatch.setattr(routes_admin, "resolve_artifact_runs", lambda *a, **k: {})
    monkeypatch.setattr(
        routes_admin,
        "list_unit_candidates",
        lambda *a, **k: course_units.list_unit_candidates(
            _session_returning([
                _candidate_row("u-1", DOC_A, "k1:a", "section_block", "観測量の構成"),
            ]),
            [DOC_A],
        ),
    )

    out: list[dict] = []
    routes_admin._build_material_context(["mat-1"], unit_candidates_out=out)
    assert out and out[0]["handle"] == "U1" and out[0]["label"] == "観測量の構成"
    # 参照キー・件数などの内部情報は載せない（LU5 / KO10）。
    assert set(out[0]) == {"handle", "kind", "kind_label", "label"}


# ---------------------------------------------------------------------------
# 9. コースビルダーのプレビュー表示（P2-R10・静的契約）
# ---------------------------------------------------------------------------

_ADMIN_JS = (
    Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js"
).read_text(encoding="utf-8")


def test_preview_shows_unit_names_not_internal_handles():
    """``U3`` のような内部 handle をそのまま学ぶ単位の表示名にしない。"""
    assert "function cbUnitDisplayName(" in _ADMIN_JS
    assert 'unitHandles.map(cbUnitDisplayName).map(escHtml).join(", ")' in _ADMIN_JS
    # 旧実装（handle をそのまま並べる）が残っていないこと。
    assert 'unitHandles.map(escHtml).join(", ")' not in _ADMIN_JS


def test_preview_falls_back_to_the_handle_instead_of_inventing_a_name():
    """候補表に無い handle は handle のまま出す（存在しない名前を作らない）。"""
    block = _ADMIN_JS.split("function cbUnitDisplayName(")[1].split("\n  }")[0]
    assert "if (!found || !found.label) return String(handle || \"\");" in block


def test_candidate_table_comes_from_the_server_response():
    assert "state.unitCandidatesByHandle" in _ADMIN_JS
    assert "data.unit_candidates" in _ADMIN_JS
