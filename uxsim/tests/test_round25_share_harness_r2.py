"""第 25 周 share 第 1 周の偽所見 H1〜H9 の是正（runs/round25/harness_fixes_share.md）。MockTransport・外部呼び出しなし。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import share as share_oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import actions_exec, share_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.runner.vision5_exec import course_visibility_label
from uxsim.schema import HttpTrace, TranscriptStep

ROOT = Path(__file__).resolve().parents[2]
DOC = "11111111-1111-1111-1111-111111111111"


def _client(handler, seen):
    def wrap(req):
        seen.append(req)
        return handler(req)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _share(action_id, args, handler, session):
    seen: list[httpx.Request] = []
    client = _client(handler, seen)
    client.take_traces()
    assert share_exec.execute(action_id, _Ctx(REGISTRY[action_id], args, session, client)) is True
    return seen, client.take_traces()


# H1 -------------------------------------------------------------------------

def test_topic_open_lists_only_embedded_anchors_and_topic_components():
    body = {"chunks": [{"text": "本文 ![[component:k1]] と ![[claim:c1]]。", "evidence_items": [
        {"kind": "component", "id": "k1", "title": "TOV network map", "summary": "TOV を課す網"},
        {"kind": "component", "id": "k2", "title": "EOS network map"},
        {"kind": "claim", "id": "c1", "title": "主張いち"},
        {"kind": "claim", "id": "c9", "title": "埋め込まれていない主張"},
        {"kind": "equation", "id": "eq_9", "title": "埋め込まれていない式"}]}]}
    obs = project_observation("learning.topic.open", 200, body)
    anchors = obs[obs.index("本文の ⚓"):obs.index("出典タブ")]
    assert "⚓1" in anchors and "⚓2" in anchors and "⚓3" not in anchors
    assert "埋め込まれていない" not in obs
    tab = obs[obs.index("出典タブ"):]
    assert "TOV network map — TOV を課す網 ［教材の該当箇所へ］" in tab
    assert "EOS network map" in tab and "EOS network map ［" not in tab  # 埋め込みの無い部品はボタンなし


# H2 -------------------------------------------------------------------------

def test_component_context_hop_by_number_without_ids_in_observation():
    graph = {"focus": {"id": "a"}, "upper": [{"id": "b", "element_type": "theory_component", "navigable": True,
                                              "label": "隣", "relation_label": "を前提にする"}],
             "lower": [{"id": "c", "element_type": "theory_component", "navigable": True, "label": "下"}]}
    obs = project_observation("learning.component.context", 200, {"instance": {}, "graph": graph})
    assert "番号 1" in obs and "番号 2" in obs and "id=" not in obs
    s = PersonaSession(persona_id="st", course_id="C", topic_id="t1")
    s.scratch["last_component_graph"] = graph
    seen: list[httpx.Request] = []
    actions_exec.execute("learning.component.context_hop", {"component_id": "2"}, s,
                         _client(lambda r: httpx.Response(200, json={"graph": {"focus": {"id": "c"}}}), seen))
    assert seen[-1].url.path.endswith("/components/c/context")


# H3 -------------------------------------------------------------------------

def test_symbol_text_matches_katex_token_text():
    assert share_exec.symbol_text(r"\mathbf{F}_{\!\mathrm{EOS}}") == "F_EOS"
    assert share_exec.symbol_text(r"\rho") == "ρ"
    assert share_exec.symbol_text("m(r)") == "m"
    assert share_exec.symbol_text("P") == "P"


MATERIAL = {"chunks": [{"id": "ch1", "text": "定義 ![[equation:eq_tov]] と [[FORMULA_0]]。",
                        "formulas": [{"id": "eq_tov", "latex": r"\mathbf{F}_{\!\mathrm{EOS}} = \frac{dP}{dr}"},
                                     {"id": "FORMULA_0", "latex": r"m(r) = 4\pi r^2"}]}]}


def _lookup(args, body, status=200):
    s = PersonaSession(persona_id="st-06", course_id="C", topic_id="t1")
    s.material = MATERIAL
    seen: list[httpx.Request] = []
    actions_exec.execute("learning.symbol.lookup", args, s,
                         _client(lambda r: httpx.Response(status, json=body), seen))
    return s, seen


def test_symbol_lookup_sends_screen_form_and_projects_popover():
    body = {"available": True, "symbol": "F_EOS", "registered": False, "scope_label": "この論文",
            "facts": ["この論文には定義の記述が見つかりませんでした。"]}
    s, seen = _lookup({"symbol": r"\mathbf{F}_{\!\mathrm{EOS}}"}, body)
    q = dict(seen[-1].url.params)
    assert q["symbol"] == "F_EOS" and q["equation_id"] == "eq_tov" and q["chunk_id"] == "ch1" and q["topic_id"] == "t1"
    obs = project_observation("learning.symbol.lookup", s.last_status, s.last_body)
    assert "registered" not in obs and "available" not in obs and "定義の記述が見つかりませんでした" in obs
    assert any(n.startswith(share_exec.SYMBOL_LHS_NOTE) for n in s.scratch.get("runner_notes") or [])


def test_symbol_lookup_inline_formula_has_no_equation_id_and_unknown_symbol_is_not_sent():
    s, seen = _lookup({"symbol": "m"}, {"available": False, "facts": []})
    assert "equation_id" not in dict(seen[-1].url.params)
    assert share_exec.SYMBOL_NOT_FOUND in project_observation("learning.symbol.lookup", s.last_status, s.last_body)
    s, seen = _lookup({"symbol": r"\Sigma"}, {})
    assert seen == [] and "見当たらない" in project_observation("learning.symbol.lookup", s.last_status, s.last_body)


# H4 -------------------------------------------------------------------------

def test_graph_node_label_is_single_screen_label():
    assert share_exec.graph_node_label({"label": "Theory basis", "visual_label": "Theory basis",
                                        "display_label": "理論の土台: define"}) == "理論の土台"
    assert share_exec.graph_node_label({"operation": "derive_result", "theory_object": "TOV"}) == "結果を導出: TOV"


def test_operation_verb_table_mirrors_js():
    js = (ROOT / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
    block = js[js.index("var LS_OPERATION_VERB_JA = {"):]
    block = block[:block.index("};")]
    assert dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block)) == share_exec.LS_OPERATION_VERB_JA


def test_identity_open_falls_back_to_inventory_component():
    comp = "33333333-3333-3333-3333-333333333333"

    def h(req):
        p = req.url.path
        if p == "/api/admin/materials":
            return httpx.Response(200, json=[{"document_id": DOC, "filename": "x.tar.gz", "title": "PINN"}])
        if p.endswith("/component-graph"):
            return httpx.Response(200, json={"graph": {"nodes": [
                {"id": "theory_op_0001", "label": "Theory basis", "visual_label": "Theory basis",
                 "representative_component_id": "comp_001"}]}})
        if p.endswith(f"/documents/{DOC}/elements"):
            return httpx.Response(200, json={"elements": [
                {"element_type": "theory_component", "element_id": comp, "label": "TOV network map"}]})
        if p.endswith("/overview"):
            return httpx.Response(200, json={"ref": {"element_type": "theory_component", "element_id": comp,
                                                     "document_id": DOC}, "decomposition": {"label": "TOV network map"}})
        return httpx.Response(200, json={"identity_links": []})

    s = PersonaSession(persona_id="te-04", role="TEACHER")
    seen, _ = _share("admin.deliberation.identity_open",
                     {"material_hint": "PINN", "format_hint": "tex", "component_hint": "TOV network map"}, h, s)
    assert any(r.url.path.endswith(f"/theory_component/{comp}/overview") for r in seen)
    assert s.scratch["share_element"]["element_id"] == comp


# H5 -------------------------------------------------------------------------

def test_studio_course_must_be_in_teacher_course_list():
    s = PersonaSession(persona_id="te-03", role="TEACHER", course_id="1bad7d9f")
    seen, traces = _share("admin.explanations.open", {}, lambda r: httpx.Response(200, json=[]), s)
    assert [r.url.path for r in seen] == ["/api/admin/courses"]
    assert traces[-1].error == "precondition:studio_course"
    assert "コースを選択してください" in project_observation("admin.explanations.open", s.last_status, s.last_body)

    def h(req):
        if req.url.path == "/api/admin/courses":
            return httpx.Response(200, json=[{"id": "own-course", "title": "自分のコース", "role": "owner"}])
        return httpx.Response(200, json=[])

    s = PersonaSession(persona_id="te-03", role="TEACHER", course_id="1bad7d9f")
    seen, _ = _share("admin.explanations.open", {}, h, s)
    assert seen[-1].url.path == "/api/admin/courses/own-course/theory-components"


# H6 / H7 ------------------------------------------------------------------------

def test_relation_object_missing_is_explained_in_japanese():
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV 方程式"}
    s.scratch["share_entries"] = [{"id": "e1", "name": "TOV 方程式"}]
    seen, traces = _share("admin.library.relation_add", {"kind": "related", "object_hint": "状態方程式"},
                          lambda r: httpx.Response(200, json={}), s)
    obs = project_observation("admin.library.relation_add", s.last_status, s.last_body)
    # IK-0866 の是正後は画面が名前で検索する。結果が無ければ選択欄は「（該当なし）」で、追加は送られない
    assert "状態方程式" in obs and "（該当なし）" in obs and "relation_object" not in obs
    assert not any(r.method == "POST" for r in seen)


def test_course_visibility_label():
    assert [course_visibility_label(v) for v in ("public", "group", "private", None)] == \
        ["公開中", "グループ限定", "非公開", "非公開"]
    obs = project_observation("admin.courses.list", 200, [{"title": "T", "role": "owner", "visibility": "public"}])
    assert "開示範囲: 公開中" in obs and "public" not in obs


# H9 -------------------------------------------------------------------------

def _t(method, path, status, body):
    return HttpTrace(method=method, path=path, status=status, response_excerpt=json.dumps(body, ensure_ascii=False))


def _step(action_id, traces, seq=1, observation="", notes=None):
    return TranscriptStep(seq=seq, persona_id="p", action_id=action_id, screen="learning", http=traces,
                          observation=observation, runner_notes=notes or [])


def test_oracle_learner_explanations_404():
    step = _step("learning.component.explanations",
                 [_t("GET", "/api/learning/courses/c/components/x/explanations", 404, {"detail": "Not found"})])
    found = share_oracle.check([step], FindingFactory())
    assert [f.severity_label for f in found] == ["blocked"]


def test_oracle_derive_facts_dropped():
    fact = "この分野にはアンカーの索引がありません。"
    tr = [_t("POST", "/api/admin/library/atlas-links/derive", 200, {"candidates": [], "facts": [fact]})]
    assert share_oracle.check([_step("admin.library.atlas_links_derive", tr, observation="一覧")], FindingFactory())
    assert not share_oracle.check([_step("admin.library.atlas_links_derive", tr, observation=fact)], FindingFactory())


def test_oracle_symbol_definitions():
    assert share_oracle.SYMBOL_LHS_NOTE == share_exec.SYMBOL_LHS_NOTE
    none = {"available": True, "facts": ["この論文には定義の記述が見つかりませんでした。"]}
    steps = [_step("learning.symbol.lookup", [_t("GET", "/api/learning/courses/c/symbols/lookup", 200, none)], seq=i,
                   notes=["symbol_lhs:F_EOS（教材の式の左辺に現れる記号）"] if i == 1 else None) for i in (1, 2)]
    keys = {f.hypothesis[:12] for f in share_oracle.check(steps, FindingFactory())}
    assert len(keys) == 2
    ok = {"available": True, "definition": {"text": "半径"}}
    steps[1] = _step("learning.symbol.lookup", [_t("GET", "/api/learning/courses/c/symbols/lookup", 200, ok)], seq=2)
    assert len(share_oracle.check(steps, FindingFactory())) == 1  # 左辺の 1 件だけ
