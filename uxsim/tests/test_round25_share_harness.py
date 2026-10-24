"""第 25 周 share（共有・転用の通し）の行為・投影・審判（MockTransport・外部呼び出しなし）。

行為 = ``uxsim/actions/share_actions.py`` / 実行と投影 = ``uxsim/runner/share_exec.py`` / 審判 = ``uxsim/oracles/share.py``。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions import share_actions
from uxsim.actions.registry import REGISTRY
from uxsim.oracles import share as share_oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import share_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace, TranscriptStep

DOC_TEX = "11111111-1111-1111-1111-111111111111"
DOC_PDF = "22222222-2222-2222-2222-222222222222"
MATERIALS = [
    {"material_id": "m1", "document_id": DOC_TEX, "filename": "2605.31198.tar.gz", "title": "Neutron Star EoS via PINN",
     "status": "completed"},
    {"material_id": "m2", "document_id": DOC_PDF, "filename": "2605.31198v1.pdf", "title": "Neutron Star EoS via PINN",
     "status": "completed"},
]


def _run(action_id: str, args: dict, handler, session: PersonaSession | None = None):
    seen: list[httpx.Request] = []

    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))
    s = session or PersonaSession(persona_id="te-03", role="TEACHER")
    client.take_traces()
    ctx = _Ctx(REGISTRY[action_id], args, s, client)
    assert share_exec.execute(action_id, ctx) is True
    return s, seen, client.take_traces()


def test_all_actions_registered_with_handlers():
    ids = {a.id for a in share_actions.ACTIONS}
    assert ids <= set(REGISTRY)
    assert ids == set(share_exec.HANDLERS)
    assert share_exec.execute("admin.no.such", None) is False


def test_identity_candidates_empty_projects_both_lines():
    """候補ゼロ: 画面（IK-0864 是正後）はサーバの事実文だけを描く — 固定の空状態文を重ねない（第 25 周 H22）。"""
    def h(req):
        return httpx.Response(200, json={"candidates": [], "facts": ["確認をお待ちしている同一性の候補はありません。"]})

    s, seen, _ = _run("admin.library.identity_candidates", {}, h)
    text = project_observation("admin.library.identity_candidates", s.last_status, s.last_body)
    assert "確認をお待ちしている同一性の候補はありません。" in text
    assert share_exec.IDENTITY_EMPTY not in text


def test_entry_review_dismiss_without_reason_is_not_sent():
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV 方程式"}
    s, seen, traces = _run("admin.library.entry_review", {"status": "dismissed"},
                           lambda r: httpx.Response(500), session=s)
    assert seen == []
    assert traces and traces[0].error == "precondition:dismiss_reason"
    text = project_observation("admin.library.entry_review", s.last_status, s.last_body)
    assert share_exec.DISMISS_REASON_REQUIRED in text


def test_identity_open_picks_pdf_version_and_resolves_node():
    def h(req):
        p = req.url.path
        if p == "/api/admin/materials":
            return httpx.Response(200, json=MATERIALS)
        if p.endswith("/component-graph"):
            return httpx.Response(200, json={"graph": {"nodes": [
                {"id": "theory_op_0001", "label": "Equation system", "representative_component_id": "comp_003"}]}})
        if p.endswith("/overview"):
            return httpx.Response(200, json={"ref": {"element_type": "theory_component",
                                                     "element_id": "33333333-3333-3333-3333-333333333333",
                                                     "document_id": DOC_PDF}, "decomposition": {"label": "TOV"}})
        if p.endswith("/identity-links"):
            return httpx.Response(200, json={"identity_links": [
                {"id": "l1", "status": "candidate", "shared_part_id": "e1", "shared_part_name": "TOV 方程式",
                 "instance_element_type": "theory_component", "instance_document_id": DOC_PDF}]})
        return httpx.Response(404)

    s, seen, _ = _run("admin.deliberation.identity_open",
                      {"material_hint": "PINN", "format_hint": "pdf", "component_hint": "式の体系"}, h)
    paths = [r.url.path for r in seen]
    assert f"/api/admin/documents/{DOC_PDF}/component-graph" in paths
    assert any(p.endswith("/theory_component/comp_003/overview") for p in paths)
    assert s.scratch["share_element"]["element_id"].startswith("3333")
    text = project_observation("admin.deliberation.identity_open", s.last_status, s.last_body)
    # 画面（_identityLinkRowHtml）は英語の要素型と論文の UUID をそのまま出す — 投影でも隠さない
    # 第 3 波（IK-0916）: 部品側の document UUID は本文に出ない（title 属性だけ）
    assert DOC_PDF not in text and "［確定］［却下］" in text


def test_relation_add_by_name_records_runner_note():
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV 方程式", "domain_key": "astrophysics"}
    s.scratch["share_entries"] = [{"id": "e1", "name": "TOV 方程式"}, {"id": "e2", "name": "状態方程式"}]
    bodies = []

    def h(req):
        if req.method == "POST":
            bodies.append(json.loads(req.content))
            return httpx.Response(201, json={"relation": {"id": "r1"}})
        if req.url.path.endswith("/entries/e1"):
            return httpx.Response(200, json={"id": "e1", "name": "TOV 方程式", "domain_key": "astrophysics"})
        if req.url.path.endswith("/library/entries") and req.url.params.get("q") == "状態方程式":
            # 画面の［検索］（searchLibraryRelationCounterparts — IK-0866 の是正後は名前で検索して選ぶ）
            return httpx.Response(200, json={"entries": [{"id": "e1", "name": "TOV 方程式"},
                                                         {"id": "e2", "name": "状態方程式", "entry_type": "concept"}]})
        return httpx.Response(200, json={"labels": [], "relations": [], "links": []})

    s, _, _ = _run("admin.library.relation_add", {"kind": "related", "object_hint": "状態方程式"}, h, session=s)
    assert bodies == [{"subject_entry_id": "e1", "object_entry_id": "e2", "kind": "related"}]
    assert "相手の選択: 状態方程式" in str(s.last_body)


def test_cite_without_target_course_is_blocked():
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    s.scratch["share_component"] = {"id": "c1", "name": "TOV", "course_id": "course-a"}
    s.scratch["share_explanations"] = [{"id": "x1", "title": "私ならこう説明する", "shared": True}]

    def h(req):
        return httpx.Response(200, json=[{"id": "course-a", "role": "owner", "title": "自分のコース"}])

    s, seen, traces = _run("admin.explanations.cite", {}, h, session=s)
    assert all(r.method == "GET" for r in seen)
    assert share_exec.LS_NO_CITE_TARGET in project_observation("admin.explanations.cite", s.last_status, s.last_body)


def test_shared_pane_lists_titles_not_count_as_the_screen_does():
    # 第 3 波（IK-0920）: 画面は件数を描かず、他の論文の題名を列挙する
    body = {"instance": {"component": {"label": "TOV"}},
            "shared_part": {"entry": {"name": "TOV 方程式", "aliases": ["TOV eq."], "standardization_status": "unknown",
                                      "other_documents_count": 1}}}
    text = share_exec.shared_pane_view(body)
    assert "［共通部品として］" in text and "論文でも使用" not in text
    assert "（「共通部品として」のタブは出ていない）" in share_exec.shared_pane_view({"instance": {}})


def _step(action_id: str, traces: list[HttpTrace], seq: int = 1, screen: str = "learning") -> TranscriptStep:
    return TranscriptStep(seq=seq, persona_id="p", action_id=action_id, screen=screen, http=traces)


def _t(method: str, path: str, status: int, body) -> HttpTrace:
    return HttpTrace(method=method, path=path, status=status, response_excerpt=json.dumps(body, ensure_ascii=False))


def test_oracle_learner_counts_and_keys():
    body = {"instance": {}, "shared_part": {"entry": {"name": "TOV", "other_documents_count": 2, "entry_id": "e1"}}}
    step = _step("learning.component.shared_part", [_t("GET", "/api/learning/courses/c/components/x/context", 200, body)])
    keys = {f.fingerprint for f in share_oracle.check([step], FindingFactory())}
    found = share_oracle.check([step], FindingFactory())
    assert len(keys) == 2 and {f.oracle for f in found} == {"B"}


def test_oracle_identity_shape_and_blank_title():
    body = {"candidates": [{"entry": {"name": "TOV"}, "links": [{"link_id": "l", "document_title": ""}],
                            "hidden_count": True, "hidden_unresolved": False}], "facts": []}
    step = _step("admin.library.identity_candidates", [_t("GET", "/api/admin/library/identity-candidates", 200, body)],
                 screen="admin")
    hyps = " ".join(f.hypothesis for f in share_oracle.check([step], FindingFactory()))
    assert "契約" in hyps and "内部 ID" in hyps


def test_oracle_explanations_unreachable():
    step = _step("admin.explanations.open", [
        _t("GET", "/api/admin/courses/c/theory-components", 200, [{"id": "x"}]),
        _t("GET", "/api/admin/theory-components/x/explanations", 404, {"detail": "Course not found"})], screen="admin")
    found = share_oracle.check([step], FindingFactory())
    assert [f.severity_label for f in found] == ["blocked"]


def test_oracle_e_candidate_visible_and_reject_lost():
    rows = {"names": [{"name": "TOV", "via": "shared_part", "seq": 3,
                       "rows": [{"review_status": "candidate", "status": "active"}]}],
            "links": [{"id": "l1", "decision": "reject", "seq": 2, "status": "candidate", "found": True}],
            "entries": []}
    found = share_oracle.check_rows(rows, FindingFactory())
    assert {f.severity_label for f in found} == {"principle", "inconsistent"}
    steps = [_step("admin.deliberation.identity_link_decide",
                   [_t("POST", "/api/admin/deliberation/identity-links/l1/reject", 200, {"id": "l1"})], screen="admin")]
    assert share_oracle.teacher_decisions(steps)["links"][0]["decision"] == "reject"
