"""§18.2 の論理構造を辿る行為が、正しい製品ルート・パラメータを呼ぶこと（httpx.MockTransport・外部呼び出しなし）。"""
from __future__ import annotations

import json

import httpx

from uxsim.runner.actions_exec import execute
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation


def _client(handler, seen):
    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _session(**kw):
    s = PersonaSession(persona_id="p1", course_id="c1", topic_id="t1")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


_GRAPH = {"graph": {"focus": {"id": "comp-a", "label": "中心"},
                    "upper": [{"id": "clm-1", "element_type": "theory_claim", "label": "主張", "navigable": True},
                              {"id": "comp-b", "element_type": "theory_component", "label": "隣の部品",
                               "relation_label": "を前提にする", "navigable": True}],
                    "lower": []}}


def test_component_context_then_hop_recenters_on_adjacent_component():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json=_GRAPH if r.url.path.endswith("comp-a/context")
                                              else {"graph": {"focus": {"id": "comp-b"}, "upper": [], "lower": []}}),
                     seen)
    s = _session()
    s.remember("components", "comp-a")
    execute("learning.component.context", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/components/comp-a/context"
    obs = project_observation("learning.component.context", s.last_status, s.last_body)
    assert "隣の部品（移動できる）" in obs and "文脈の図の中心: 中心" in obs
    execute("learning.component.context_hop", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/components/comp-b/context"  # claim ではなく部品へ
    # 次の hop は隣接が無い → HTTP を出さず precondition
    traces = execute("learning.component.context_hop", {}, s, client)
    assert len(seen) == 2 and traces[0].error == "precondition:adjacent_component"


def test_claim_refs_uses_known_chunk():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={"claims": [{"id": "x", "claim_type": "result", "label": "L"}]}), seen)
    s = _session()
    s.remember("chunks", "ch9")
    execute("learning.chunk.claim_refs", {}, s, client)
    assert seen[-1].method == "GET" and seen[-1].url.path == "/api/learning/courses/c1/chunks/ch9/claim-refs"
    assert s.latest("elements") == "x"


def test_ask_selection_and_cycle_bodies():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={"answer": "a"}), seen)
    s = _session()
    execute("learning.chat.ask_selection", {"message": "ここは?", "selection_text": "選んだ文", "selection_segment_id": 2},
            s, client)
    body = json.loads(seen[-1].content)
    assert seen[-1].url.path == "/api/learning/courses/c1/topics/t1/chat"
    assert body["selection_text"] == "選んだ文" and body["selection_segment_id"] == 2
    execute("learning.chat.cycle", {"message": "問いをください", "cycle_mode": "elicit"}, s, client)
    body = json.loads(seen[-1].content)
    assert seen[-1].url.path == "/api/learning/courses/c1/topics/_discussion/chat"
    assert body["cycle_mode"] == "elicit" and body["intent_mode"] == "discuss"
    traces = execute("learning.chat.ask_selection", {"message": "x"}, s, client)
    assert traces[0].error == "precondition:selection_text"


def test_atlas_threads_records_presence_and_neighbors_params():
    seen: list[httpx.Request] = []

    def handler(r):
        if r.url.path == "/api/atlas":
            return httpx.Response(200, json={"level": 2, "threads": {"available": True, "skeleton_version": "3",
                                                                     "items": [{"from_label": "A", "to_label": "B",
                                                                                "nearness_label": "近い可能性"}]}})
        return httpx.Response(200, json={"available": True, "here": {"label": "H", "region_label": "R"},
                                         "neighbors": [{"label": "N", "region_label": "R", "relation": "edge"}]})

    client = _client(handler, seen)
    s = _session()
    execute("learning.atlas.threads", {}, s, client)
    assert seen[-1].url.params["level"] == "2" and s.scratch["atlas_threads_present"] is True
    assert "A ⋯ B" in project_observation("learning.atlas.threads", s.last_status, s.last_body)
    s.remember("nodes", "n1")
    execute("learning.atlas.neighbors", {}, s, client)
    assert seen[-1].url.path == "/api/me/personal-network/atlas-neighbors" and seen[-1].url.params["node_id"] == "n1"
    assert "N（R・直接つながる）" in project_observation("learning.atlas.neighbors", s.last_status, s.last_body)


def test_admin_document_views_and_seminar_brief():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={"available": True}), seen)
    s = _session(role="TEACHER")
    s.remember("documents", "d1")
    for aid, suffix in (("admin.paper_layer.view", "paper-layer"), ("admin.theory_modules.view", "theory-modules"),
                        ("admin.theory_modules.related", "theory-modules/related"),
                        ("admin.seminar_brief.view", "seminar-brief")):
        execute(aid, {}, s, client)
        assert seen[-1].method == "GET" and seen[-1].url.path == f"/api/admin/documents/d1/{suffix}"


def test_deliberation_overview_passes_document_id():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={}), seen)
    s = _session()
    s.remember("documents", "d1")
    s.remember("components", "comp-a")
    execute("admin.deliberation.overview", {}, s, client)
    assert seen[-1].url.path == "/api/admin/deliberation/elements/theory_component/comp-a/overview"
    assert seen[-1].url.params["document_id"] == "d1"


def test_node_chat_creates_session_then_posts_with_screen_context():
    seen: list[httpx.Request] = []

    def handler(r):
        if r.url.path == "/api/admin/deliberation/sessions":
            return httpx.Response(200, json={"session": {"id": "s1"}})
        return httpx.Response(200, json={"reply": "r"})

    client = _client(handler, seen)
    s = _session()
    s.remember("graph_documents", "d1")
    s.remember("components", "comp-a")
    execute("admin.graph_review.node_chat", {"message": "この式はどこから?"}, s, client)
    create, msg = json.loads(seen[0].content), json.loads(seen[1].content)
    assert create == {"scope": "document", "element_type": "theory_component", "element_id": "comp-a",
                      "document_id": "d1", "title": ""}
    assert seen[1].url.path == "/api/admin/deliberation/sessions/s1/messages"
    assert msg["screen_context"]["screen"] == "graph_review"
    assert msg["screen_context"]["selection"]["document_id"] == "d1"
    execute("admin.graph_review.node_chat", {"message": "続き"}, s, client)
    assert len(seen) == 3  # セッションは再利用


def test_graph_open_remembers_claims_and_approve_claim_body():
    seen: list[httpx.Request] = []

    def handler(r):
        if r.url.path.endswith("/component-graph"):
            return httpx.Response(200, json={"nodes": [], "reference_index": {"claims": {
                "claim:x": {"claim_id": "uuid-1", "review_status": "teacher_review_required"},
                "synth": {"claim_id": "", "parent_claim_id": "uuid-2"}}}})
        return httpx.Response(200, json={"review_status": "teacher_approved"})

    client = _client(handler, seen)
    s = _session()
    s.remember("documents", "d1")
    execute("admin.graph_review.open", {}, s, client)
    assert set(s.all("claims")) == {"uuid-1", "uuid-2"}
    execute("admin.graph_review.approve_claim", {"claim_id": "uuid-1"}, s, client)
    assert seen[-1].method == "POST" and seen[-1].url.path == "/api/admin/claims/uuid-1/review"
    assert json.loads(seen[-1].content) == {"review_status": "teacher_approved"}
