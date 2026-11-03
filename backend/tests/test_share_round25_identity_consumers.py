"""第 25 周 面 A 第 3 周: 確定した同一性リンクの読み手（IK-0920）と学生側の見出し（IK-0917）。

① 管理画面ナレッジライブラリのエントリ詳細「結びついた教材の部品」（admin.js・表示のみ）
② 学生の「共通部品として」: 他の論文は確定リンクから導いた**題名**（件数をやめる）・
   読める論文の集合の中だけ（fail-closed）・見出しはサーバ固定文
③ 学習チャットの element 解決器: 部品の確定リンクを事実 1 行で添える（新しい kind なし）
④ 旅: 区画に帰属した問いでも、区画に埋め込まれた部品が一意なら [2] の共通部品 hop へ渡す
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core import component_context  # noqa: E402
from core.assistant_context.resolvers import learning as learning_resolvers  # noqa: E402
from core.library import schema as library_schema  # noqa: E402
from core.personal_graph import journey  # noqa: E402
from core.personal_graph import queries as pg_queries  # noqa: E402
from core.personal_graph.schema import (  # noqa: E402
    PersonalAnchor,
    PersonalNetwork,
    PersonalNode,
)

_REPO = _BACKEND_DIR.parent
_ADMIN_JS = _REPO / "frontend" / "public" / "js" / "admin.js"
_APP_JS = _REPO / "frontend" / "public" / "js" / "app.js"

_COMP = "11111111-1111-1111-1111-111111111111"
_DOC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DOC_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_DOC_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_SP = "99999999-9999-9999-9999-999999999999"


# ---------------------------------------------------------------------------
# ② 学生の「共通部品として」
# ---------------------------------------------------------------------------


def _patch_shared_part(monkeypatch, *, shared_links):
    monkeypatch.setattr(
        component_context.identity_links_mod,
        "list_for_instance",
        lambda *a, **k: [{"status": "confirmed", "shared_part_id": _SP, "decided_by": ""}],
    )
    monkeypatch.setattr(
        component_context.identity_links_mod, "list_for_shared_part", lambda _sp: shared_links
    )
    monkeypatch.setattr(
        component_context.library_store_mod,
        "get_entry",
        lambda _eid: {
            "status": library_schema.STATUS_ACTIVE,
            "name": "TOV 方程式",
            "summary": "静水圧平衡",
            # source_document_ids は読まない（IK-0920: 手で作ったエントリは空になり得る）
            "source_document_ids": [_DOC_A, _DOC_D],
            "latest_version_no": 1,
        },
    )
    # IK-0954: 学生は凍結版の content を読む。
    monkeypatch.setattr(
        component_context.library_store_mod,
        "get_version",
        lambda _eid, _no: {"content": {"name": "TOV 方程式", "summary": "静水圧平衡"}},
    )
    monkeypatch.setattr(component_context, "_user_display_name", lambda _uid: "")
    monkeypatch.setattr(
        component_context,
        "_document_titles",
        lambda ids: {_DOC_B: "論文B", _DOC_C: "論文C", _DOC_D: "論文D"},
    )


class TestSharedPartOtherDocuments:
    def test_titles_from_confirmed_links_within_readable_set(self, monkeypatch):
        _patch_shared_part(
            monkeypatch,
            shared_links=[
                {"status": "confirmed", "instance_document_id": _DOC_A},  # 自分の論文
                {"status": "confirmed", "instance_document_id": _DOC_B},  # 読める
                {"status": "confirmed", "instance_document_id": _DOC_C},  # 読めない
                {"status": "candidate", "instance_document_id": _DOC_D},  # 確定でない
            ],
        )
        result = component_context._build_shared_part(
            _COMP, _DOC_A, {_COMP}, readable_document_ids={_DOC_A, _DOC_B, _DOC_D}
        )
        entry = result["entry"]
        assert entry["other_documents"] == [{"title": "論文B"}]
        assert entry["other_documents_lead"] == component_context.SHARED_PART_OTHER_DOCUMENTS_LEAD
        assert "other_documents_count" not in entry
        assert result["heading"] == "教員がこの部品と同じものとして確かめた共通部品"
        # 読めない論文の題名・存在は DTO のどこにも出ない
        assert "論文C" not in repr(result)

    def test_no_readable_set_does_not_read_links(self, monkeypatch):
        called = []
        _patch_shared_part(monkeypatch, shared_links=[])
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_shared_part",
            lambda sp: called.append(sp) or [],
        )
        result = component_context._build_shared_part(_COMP, _DOC_A, {_COMP})
        assert called == []
        assert result["entry"]["other_documents"] == []
        assert result["entry"]["other_documents_note"] == component_context.SHARED_PART_NO_OTHER_DOCUMENTS_NOTE

    def test_listing_failure_keeps_shared_part(self, monkeypatch):
        _patch_shared_part(monkeypatch, shared_links=[])

        def _boom(_sp):
            raise RuntimeError("db down")

        monkeypatch.setattr(component_context.identity_links_mod, "list_for_shared_part", _boom)
        result = component_context._build_shared_part(
            _COMP, _DOC_A, {_COMP}, readable_document_ids={_DOC_B}
        )
        assert result is not None
        assert result["entry"]["name"] == "TOV 方程式"
        assert result["entry"]["other_documents"] == []

    def test_no_numeric_keys_in_entry(self, monkeypatch):
        _patch_shared_part(
            monkeypatch, shared_links=[{"status": "confirmed", "instance_document_id": _DOC_B}]
        )
        result = component_context._build_shared_part(
            _COMP, _DOC_A, {_COMP}, readable_document_ids={_DOC_B}
        )
        for value in result["entry"].values():
            assert not isinstance(value, (int, float)) or isinstance(value, bool)


class TestAppJsSharedPane:
    def test_heading_and_titles_are_server_passthrough(self):
        src = _APP_JS.read_text(encoding="utf-8")
        start = src.index("function renderContextSharedPane(")
        block = src[start:start + 3000]
        assert "sharedPart.heading" in block
        assert "entry.other_documents" in block
        assert "other_documents_note" in block
        # 件数の表示をやめた（数値非表示）
        assert "other_documents_count" not in block
        assert "論文でも使用" not in block
        # 見出し文言は JS にミラーしない
        assert "同じものとして確かめた共通部品" not in src


# ---------------------------------------------------------------------------
# ③ 学習チャットの element 解決器
# ---------------------------------------------------------------------------


class TestElementResolverSharedPartFact:
    def _context(self, shared_part):
        return {
            "instance": {
                "component": {"label": "TOV network map", "summary": "構造ネットワーク"},
                "in_paper": {"document": {"title": "論文A"}},
            },
            "shared_part": shared_part,
        }

    def test_confirmed_shared_part_adds_fact(self):
        facts = learning_resolvers._component_facts(
            self._context(
                {"entry": {"name": "TOV 方程式", "other_documents": [{"title": "論文B"}]}}
            )
        )
        assert "教員がこの部品を共通部品「TOV 方程式」と同じものとして確かめています" in facts
        assert any("論文『論文B』" in f for f in facts)

    def test_no_shared_part_no_fact(self):
        facts = learning_resolvers._component_facts(self._context(None))
        assert not any("共通部品" in f for f in facts)

    def test_fact_survives_the_cap(self):
        context = self._context({"entry": {"name": "TOV 方程式"}})
        context["instance"]["supports"] = {
            "preconditions": [{"text": f"前提{i}"} for i in range(3)],
            "claims": [{"excerpt": f"主張の本文その{i}です"} for i in range(5)],
        }
        from core.assistant_context.schema import ScreenContext

        ctx = ScreenContext(
            screen="learning",
            selection={"element_type": "component", "element_id": _COMP},
            view={},
            visible_entities=[],
        )
        facts = learning_resolvers.resolve_element(
            ctx, {"element": {"element_type": "component", "context": context}}
        )
        assert any("共通部品「TOV 方程式」" in f for f in facts)


# ---------------------------------------------------------------------------
# ④ 旅: 区画に帰属した問い
# ---------------------------------------------------------------------------


def _segment_node(anchor_id="seg_1"):
    return PersonalNode(
        id="trace-1",
        node_kind="question",
        label="この式は何？",
        anchor=PersonalAnchor(anchor_type="segment", anchor_id=anchor_id, anchor_label="選択"),
        topic_id="t1",
        course_id="c1",
        created_at="2026-10-23T00:00:00",
        facts=[],
        source={},
    )


class TestSegmentComponentRefs:
    def test_unique_embed_in_segment(self):
        topic = {
            "id": "t1",
            "content": (
                "はじめの区画。![[component:comp_001]]\n\n===\n\n"
                "次の区画。![[component:comp_002]] と ![[component:comp_002]]\n\n===\n\n"
                "三つ目。![[component:comp_003]] ![[component:comp_004]]"
            ),
        }
        assert pg_queries.segment_component_refs(topic, 0) == ["comp_001"]
        assert pg_queries.segment_component_refs(topic, 1) == ["comp_002"]
        assert pg_queries.segment_component_refs(topic, 2) == ["comp_003", "comp_004"]
        assert pg_queries.segment_component_refs(topic, 9) == []
        assert pg_queries.segment_component_refs(None, 0) == []


class _FakeQueries:
    def __init__(self, refs):
        self.refs = refs

    def fetch_topic(self, course_id, topic_id):
        return {"id": topic_id}

    def segment_component_refs(self, topic, index):
        return self.refs

    def fetch_course_document_ids(self, course_id):
        return {_DOC_A, _DOC_B}

    def resolve_course_component(self, ref, doc_ids):
        return _COMP

    def fetch_component_document_id(self, component_id):
        return _DOC_A

    def fetch_confirmed_identity_links(self, document_id):
        return [
            {
                "id": "l1", "instance_element_type": "theory_component",
                "instance_element_id": _COMP, "shared_part_id": _SP, "created_at": "x",
            }
        ]

    def fetch_library_entry_names(self, ids):
        return {_SP: "TOV 方程式"}

    def fetch_confirmed_links_for_shared_part(self, sp):
        return [{"instance_document_id": _DOC_A}, {"instance_document_id": _DOC_B}]

    def fetch_document_titles(self, ids):
        return {_DOC_B: "論文B"}

    def fetch_component_name(self, component_id):
        return "TOV network map"


class TestSegmentJourney:
    def test_unique_embed_feeds_shared_part_hop(self):
        seg, links, entries, others = journey._segment_link_inputs(
            _FakeQueries(["comp_001"]), "u1", _segment_node()
        )
        assert seg == {"component_id": _COMP, "label": "TOV network map"}
        assert [l["shared_part_id"] for l in links] == [_SP]
        assert entries[_SP]["other_documents"] == [{"document_id": _DOC_B, "title": "論文B"}]
        assert others == {_DOC_B}

        result = journey.build_person_journey(
            _segment_node(), PersonalNetwork(nodes=[_segment_node()]), {}, links, entries,
            {}, {}, {_DOC_B}, segment_component=seg,
        )
        facts = [s["fact"] for s in result["steps"]]
        assert facts[0] == "教材の選んだ箇所に埋め込まれた部品『TOV network map』からたどります。"
        assert "部品『TOV network map』は共通部品『TOV 方程式』の教材での表れとして教員が確かめています" in facts
        assert "『TOV 方程式』は論文Bにも現れます" in facts
        assert result["frontier_note"] is None

    def test_two_embeds_do_not_guess(self):
        seg, links, entries, _others = journey._segment_link_inputs(
            _FakeQueries(["comp_001", "comp_002"]), "u1", _segment_node()
        )
        assert seg is None and links == [] and entries == {}

    def test_unresolved_segment_index_does_not_guess(self):
        seg, *_ = journey._segment_link_inputs(_FakeQueries(["comp_001"]), "u1", _segment_node(""))
        assert seg is None

    def test_unviewable_document_is_fail_closed(self):
        seg, *_ = journey._segment_link_inputs(
            _FakeQueries(["comp_001"]), "u1", _segment_node(),
            can_view_document=lambda _u, _d: False,
        )
        assert seg is None

    def test_segment_without_component_keeps_previous_behaviour(self):
        result = journey.build_person_journey(
            _segment_node(), PersonalNetwork(nodes=[_segment_node()]), {}, [], {}, {}, {}, set(),
        )
        assert result["steps"] == []
        assert result["frontier_note"] is None

    def test_course_scoped_builder_accepts_segment_component(self):
        seg = {"component_id": _COMP, "label": "TOV network map"}
        links = [{"id": "l1", "shared_part_id": _SP, "created_at": "x"}]
        entries = {_SP: {"name": "TOV 方程式", "other_documents": []}}
        result = journey.build_journey(
            _segment_node(), PersonalNetwork(nodes=[_segment_node()]), {}, links, entries,
            {}, set(), segment_component=seg,
        )
        assert any("共通部品『TOV 方程式』" in s["fact"] for s in result["steps"])


# ---------------------------------------------------------------------------
# ① 管理画面: 結びついた教材の部品
# ---------------------------------------------------------------------------


class TestAdminBoundParts:
    def test_section_reads_existing_identity_links_api(self):
        src = _ADMIN_JS.read_text(encoding="utf-8")
        assert 'data-ui-anchor="knowledge-library.bound-parts"' in src or '"knowledge-library.bound-parts"' in src
        start = src.index("function loadLibraryEntryBoundParts(")
        # 第 5 波（IK-0982 / IK-0983）で行の描画を _libraryBoundLinksHtml に分けたので、両方を見る。
        block = src[start:src.index("\n  function ", start + 10)]
        rows_start = src.index("function _libraryBoundLinksHtml(")
        rows = src[rows_start:src.index("\n  function ", rows_start + 10)]
        assert '"/admin/deliberation/shared-parts/"' in block
        assert "/identity-links" in block
        assert "hidden_count" in block
        assert "instance_document_title" in rows
        assert "instance_label" in rows
        assert "library-bound-part-revoke" in rows
        assert "instance_document_version_label" in rows
        # IK-0982: 候補の行に［確認］［見送り］（エントリを確定しても未決のリンクの置き場を残す）。
        assert "library-bound-part-confirm" in rows and "library-bound-part-reject" in rows
        assert 'data-ui-anchor="knowledge-library.bound-part-decide"' in rows
        # IK-0983: 記号の結び付きは別の区画。
        assert 'instance_element_type === "symbol"' in block
        assert "library-bound-symbols-list" in block


# ---------------------------------------------------------------------------
# 第 4 波（IK-0954 / IK-0959）: 学生は凍結版だけを読む・未評価は出さない
# ---------------------------------------------------------------------------


class TestLearnerReadsFrozenOnly:
    def _links(self, monkeypatch):
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_instance",
            lambda *a, **k: [{"status": "confirmed", "shared_part_id": _SP, "decided_by": ""}],
        )
        monkeypatch.setattr(component_context.identity_links_mod, "list_for_shared_part", lambda _sp: [])
        monkeypatch.setattr(component_context, "_user_display_name", lambda _uid: "")

    def test_unfrozen_entry_is_not_shown(self, monkeypatch):
        self._links(monkeypatch)
        monkeypatch.setattr(
            component_context.library_store_mod,
            "get_entry",
            lambda _eid: {"status": library_schema.STATUS_ACTIVE, "name": "下書きの名前", "latest_version_no": 0},
        )
        called = []
        monkeypatch.setattr(
            component_context.library_store_mod, "get_version", lambda *a: called.append(a) or None
        )
        assert component_context._build_shared_part(_COMP, _DOC_A, {_COMP}) is None
        assert called == []

    def test_frozen_content_wins_over_draft(self, monkeypatch):
        self._links(monkeypatch)
        monkeypatch.setattr(
            component_context.library_store_mod,
            "get_entry",
            lambda _eid: {
                "status": library_schema.STATUS_ACTIVE,
                "name": "下書きで直した名前",
                "summary": "下書きの要約",
                "latest_version_no": 3,
                "standardization_status": library_schema.STANDARDIZATION_STATUS_UNKNOWN,
            },
        )
        monkeypatch.setattr(
            component_context.library_store_mod,
            "get_version",
            lambda _eid, no: {"content": {"name": "凍結した名前", "summary": "凍結した要約"}} if no == 3 else None,
        )
        result = component_context._build_shared_part(_COMP, _DOC_A, {_COMP})
        assert result["entry"]["name"] == "凍結した名前"
        assert result["entry"]["summary"] == "凍結した要約"
        assert "下書き" not in repr(result)
        # 未評価は学生に何も出さない（キー自体が無い）
        assert "standardization_note" not in result["entry"]
        assert "standardization_status" not in result["entry"]
        assert "未評価" not in repr(result)
