"""第 18 周のハーネス是正（入れ子の宛先キー・部品文脈の exits・鏡の枠・ノード対話の解決・降下路の ⚓ 番号）。"""
from pathlib import Path

from uxsim.runner import actions_exec
from uxsim.runner.state import (MIRROR_LABEL, PersonaSession, _component_context_lines, _generic,
                                project_observation, strip_address_keys)
from uxsim.schema import HttpTrace

ROOT = Path(__file__).resolve().parents[2]
MIRROR_MOVED_NOTE = "（言い直しは、この回答の「AIによる言い直し」の枠に示しています。）"

MATERIAL = {"chunks": [
    {"text": "式を見る ![[claim:c1]] と ![[equation:eq_3]] と ![[figure:f1]]", "evidence_items": [
        {"kind": "claim", "id": "c1", "title": "主張いち"},
        {"kind": "equation", "id": "eq_3", "title": "式さん", "latex": "a=b"},
        {"kind": "figure", "id": "f1", "title": "図いち"}]},
]}


class FakeClient:
    def __init__(self, responses=None, statuses=None):
        self.traces, self.calls = [], []
        self.responses, self.statuses = responses or {}, statuses or {}

    def call(self, method, path, json=None, params=None):
        self.calls.append((method, path, json, params))
        status = next((v for k, v in self.statuses.items() if k == path), 200)
        self.traces.append(HttpTrace(method=method, path=path, status=status))
        if status >= 400:
            return status, {"detail": "Theory component not found"}, None
        for k, v in self.responses.items():
            if k in path:
                return status, v, None
        return status, {}, None

    def take_traces(self):
        t, self.traces = self.traces, []
        return t


def _session(role="STUDENT"):
    s = PersonaSession(persona_id="p", course_id="C", topic_id="t1")
    s.material = MATERIAL
    return s


# --- 1. 入れ子の宛先キー -------------------------------------------------------

def test_strip_address_keys_recursive():
    v = {"label": "L", "node_ids": ["a"], "inner": [{"module_key": "m2:x", "section_id": "s1", "title": "章"}]}
    assert strip_address_keys(v) == {"label": "L", "inner": [{"title": "章"}]}


def test_generic_deep_json_has_no_address_keys():
    body = {"modules": [{"label": "工程", "detail": {"steps": [
        {"evidence_claim_ids": ["c9"], "node_ids": ["eq_op_1"], "module_key": "m2|x", "label": "式の変形"}]}}]}
    text = "\n".join(_generic(body))
    for key in ("evidence_claim_ids", "node_ids", "module_key", "eq_op_1", "m2|x"):
        assert key not in text
    assert "式の変形" in text


def test_teacher_projection_no_nested_address_keys():
    body = {"available": True, "sections": [{"section_id": "sec_1", "heading": "序論",
                                              "nodes": [{"node_id": "theory_op_1", "label": "理論の土台",
                                                         "detail": {"step_refs": ["s1"], "claim": "主張"}}]}]}
    for aid in ("admin.paper_layer.view", "admin.theory_modules.view", "admin.seminar_brief.view",
                "admin.graph_review.open"):
        obs = project_observation(aid, 200, body)
        assert "sec_1" not in obs and "theory_op_1" not in obs and "step_refs" not in obs
        assert "序論" in obs


# --- 2. 部品文脈の exits -------------------------------------------------------

def test_component_context_renders_exits_once_and_hides_idless_handles():
    exit_fact = "議論の開幕画面で中心命題と支持構造を読めます"
    body = {"instance": {}, "graph": {
        "focus": {"id": "k1", "label": "中心"},
        "upper": [{"label": "中心命題", "relation_label": "支える", "element_type": "thesis", "navigable": True},
                  {"id": "k2", "label": "親部品", "relation_label": "の一部", "element_type": "theory_component",
                   "navigable": True}],
        "lower": [{"label": "支持構造", "element_type": "theory_component", "navigable": True}],
        "exits": {"upper": exit_fact, "lower": exit_fact}}}
    text = "\n".join(_component_context_lines(body))
    assert text.count(exit_fact) == 1
    assert "中心命題" in text and "id=None" not in text
    assert "親部品（移動できる） id=k2" in text
    assert "支持構造（移動できる）" not in text  # id の無い行は移動先にしない


# --- 3. 鏡の枠 ------------------------------------------------------------------

def test_chat_renders_mirror_block_with_ui_heading_before_answer():
    body = {"answer": "本文です。" + MIRROR_MOVED_NOTE, "mirror": {"text": "あなたは「音速」と言いました"}}
    obs = project_observation("learning.discuss.ask", 200, body)
    assert f"〔{MIRROR_LABEL}〕あなたは「音速」と言いました" in obs
    assert obs.index(MIRROR_LABEL) < obs.index("AI の回答")
    assert MIRROR_MOVED_NOTE in obs  # 製品の文言は落とさない（指す枠が観察にある）
    assert "〔鏡〕" not in obs
    assert "そのとおり" in obs and "少し違う" in obs


def test_chat_without_mirror_text_draws_no_block():
    obs = project_observation("learning.chat.ask", 200, {"answer": "a", "mirror": {"text": ""}})
    assert MIRROR_LABEL not in obs


def test_mirror_label_matches_app_js():
    js = (ROOT / "frontend/public/js/app.js").read_text(encoding="utf-8")
    assert f'<div class="mirror-label">{MIRROR_LABEL}</div>' in js


# --- 4. ノード対話の解決 --------------------------------------------------------

GRAPH = {"nodes": [
    {"id": "theory_op_0001", "label": "理論の土台", "graph_layer": "main", "component_id": "stale-x",
     "representative_component_id": "comp_001"},
    {"id": "theory_op_0002", "label": "方程式系", "graph_layer": "main", "linked_component_ids": ["", "comp_007"]},
    {"id": "theory_op_0003", "label": "診断と応用", "graph_layer": "main"},
    {"id": "eq_op_0001", "label": "式の変形", "graph_layer": "equation_detail", "component_id": "stale-y"},
]}


def _graph_session():
    s = _session()
    s.remember("documents", "d1")
    c = FakeClient({"/component-graph": GRAPH, "/api/admin/deliberation/sessions": {"session": {"id": "S"}}})
    actions_exec.execute("admin.graph_review.open", {}, s, c)
    return s, c


def _created_element(c):
    return [x[2]["element_id"] for x in c.calls if x[1] == "/api/admin/deliberation/sessions"]


def test_node_chat_without_node_uses_first_main_representative_not_stale_id():
    s, c = _graph_session()
    assert s.latest("components") == "stale-y"
    actions_exec.execute("admin.graph_review.node_chat", {"message": "m"}, s, c)
    assert _created_element(c) == ["comp_001"]
    sel = c.calls[-1][2]["screen_context"]["selection"]
    assert sel["node_id"] == "theory_op_0001" and sel["component_id"] == "comp_001"


def test_node_chat_matches_label_and_linked_component():
    s, c = _graph_session()
    actions_exec.execute("admin.graph_review.node_chat", {"node_id": "方程式系", "message": "m"}, s, c)
    assert _created_element(c) == ["comp_007"]
    actions_exec.execute("admin.graph_review.node_chat", {"component_id": "理論の土台", "message": "m"}, s, c)
    assert _created_element(c)[-1] == "comp_001"


def test_node_chat_unknown_label_does_not_send_stale_id():
    s, c = _graph_session()
    n = len(c.calls)
    actions_exec.execute("admin.graph_review.node_chat", {"node_id": "存在しない段階", "message": "m"}, s, c)
    assert len(c.calls) == n
    assert s.last_body == {"_precondition": "graph_node"}


def test_node_chat_unresolvable_node_projects_ui_fact():
    s, c = _graph_session()
    n = len(c.calls)
    actions_exec.execute("admin.graph_review.node_chat", {"node_id": "診断と応用", "message": "m"}, s, c)
    assert len(c.calls) == n
    obs = project_observation("admin.graph_review.node_chat", s.last_status, s.last_body)
    assert obs == actions_exec.NODE_UNRESOLVABLE_FACT


def test_node_chat_404_and_422_project_ui_fact_not_detail():
    for code in (404, 422):
        s, c = _graph_session()
        c.statuses = {"/api/admin/deliberation/sessions": code}
        actions_exec.execute("admin.graph_review.node_chat", {"message": "m"}, s, c)
        obs = project_observation("admin.graph_review.node_chat", s.last_status, s.last_body)
        assert "グラフ全体との対話をご利用ください" in obs and "not found" not in obs
        assert not any(x[1].endswith("/messages") for x in c.calls)


def test_node_chat_direct_component_id_still_opens():
    s, c = _graph_session()
    actions_exec.execute("admin.graph_review.node_chat", {"component_id": "comp_042", "message": "m"}, s, c)
    assert _created_element(c) == ["comp_042"]


def test_node_unresolvable_fact_matches_js():
    js = (ROOT / "frontend/public/js/admin-graph-review.js").read_text(encoding="utf-8")
    assert actions_exec.NODE_UNRESOLVABLE_FACT in js


# --- 5. 降下路の ⚓ 番号 ---------------------------------------------------------

def test_descent_ladder_accepts_anchor_numbers():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.descent.ladder", {"element_ref": "⚓2"}, s, c)
    assert c.calls[-1][3] == {"element_type": "equation", "element_id": "eq_3"}
    actions_exec.execute("learning.descent.ladder", {"element_id": "1"}, s, c)
    assert c.calls[-1][3] == {"element_type": "claim", "element_id": "c1"}
    actions_exec.execute("learning.descent.ladder", {"element_id": "⚓2"}, s, c)
    assert c.calls[-1][3]["element_id"] == "eq_3"


def test_descent_ladder_rejects_unknown_or_unsupported_anchor():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.descent.ladder", {"element_ref": "9"}, s, c)
    assert c.calls == [] and s.last_body == {"_precondition": "element_ref"}
    s.material = {"chunks": [{"text": "x", "evidence_items": [{"kind": "component", "id": "k1", "title": "部品"}]}]}
    actions_exec.execute("learning.descent.ladder", {"element_ref": "1"}, s, c)
    assert c.calls[-1][3] == {"element_type": "component", "element_id": "k1"}


def test_descent_ladder_plain_id_unchanged():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.descent.ladder", {"element_type": "equation", "element_id": "eq_9"}, s, c)
    assert c.calls[-1][3] == {"element_type": "equation", "element_id": "eq_9"}
