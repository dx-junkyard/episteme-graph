"""第 14 周のハーネス是正（⚓ の番号選択・出典番号・ノード解決・受講投影・選択文の実在・頭脳遅延の印）。"""
from uxsim.runner import actions_exec
from uxsim.runner.state import PersonaSession, material_anchors, project_observation
from uxsim.schema import HttpTrace

MATERIAL = {"chunks": [
    {"text": "音速は小さい。 ![[claim:c1]] 次の文です。", "evidence_items": [
        {"kind": "claim", "id": "c1", "title": "主張いち"}, {"kind": "component", "id": "k2", "title": "部品に"}]},
    {"text": "現象論的なパラメータ化どうしは互いに整合し、値は1.1です。", "evidence_items": [
        {"kind": "equation", "id": "eq_3", "title": "式さん"}]},
]}


class FakeClient:
    def __init__(self, responses=None):
        self.traces, self.calls, self.responses = [], [], responses or {}

    def call(self, method, path, json=None, params=None):
        self.calls.append((method, path, json, params))
        self.traces.append(HttpTrace(method=method, path=path, status=200))
        for k, v in self.responses.items():
            if k in path:
                return 200, v, None
        return 200, {}, None

    def take_traces(self):
        t, self.traces = self.traces, []
        return t


def _session():
    s = PersonaSession(persona_id="p", course_id="C", topic_id="t1")
    s.material = MATERIAL
    return s


def test_anchors_numbered_in_projection():
    anchors = material_anchors(MATERIAL)
    assert [a["no"] for a in anchors] == [1, 2, 3]
    obs = project_observation("learning.topic.open", 200, MATERIAL)
    assert "⚓2" in obs and "部品に" in obs


def test_element_ref_selects_anchor():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.element.context", {"element_ref": "⚓3"}, s, c)
    assert c.calls[-1][1].endswith("/elements/equation/eq_3/context")
    actions_exec.execute("learning.element.context", {"element_ref": "2"}, s, c)
    assert "/components/k2/context" in c.calls[-1][1]
    n = len(c.calls)
    actions_exec.execute("learning.element.context", {"element_ref": "9"}, s, c)
    assert len(c.calls) == n  # 無い番号は製品を叩かない


def test_source_no_requires_previous_sources():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.source_chunk.open", {"source_no": 1}, s, c)
    assert c.calls == []
    s.scratch["last_sources"] = {"2": "chunkX"}
    actions_exec.execute("learning.source_chunk.open", {"source_no": 2}, s, c)
    assert c.calls[-1][1].endswith("/source-chunk/chunkX")


def test_nearby_loads_map_first():
    s = _session()
    c = FakeClient({"/api/me/personal-network": {"nodes": [{"id": "n1"}]}})
    actions_exec.execute("learning.personal_network.nearby", {}, s, c)
    assert c.calls[0][1] == "/api/me/personal-network"
    assert c.calls[-1][3]["node_id"] == "n1"


def test_enroll_projection_hides_flags():
    obs = project_observation("learning.course.enroll", 201, {
        "id": "x", "title": "T", "is_template": True, "is_enrollable": False, "visibility": "public",
        "enrolled": True, "notice": "受講を開始しました。"})
    assert "is_template" not in obs and "visibility" not in obs and "受講を開始しました" in obs


def test_selection_must_exist_in_material():
    s, c = _session(), FakeClient()
    actions_exec.execute("learning.chat.ask_selection", {"message": "?", "selection_text": "教材に無い文"}, s, c)
    assert c.calls == []
    actions_exec.execute("learning.chat.ask_selection", {"message": "?", "selection_ref": "1:1"}, s, c)
    body = c.calls[-1][2]
    assert body["selection_text"].startswith("現象論的") and body["selection_segment_id"] == 1


def test_node_chat_resolves_main_node_like_ui():
    node = {"id": "theory_op_0001", "representative_component_id": "comp_agent_1"}
    assert actions_exec.deliberation_target_id(node) == "comp_agent_1"
    assert actions_exec.deliberation_target_id({"id": "theory_op_0002"}) == ""
    s = _session()
    s.maps["node_targets"] = {"theory_op_0001": "comp_agent_1", "theory_op_0002": ""}
    c = FakeClient({"/api/admin/deliberation/sessions": {"session": {"id": "S"}}})
    actions_exec.execute("admin.graph_review.node_chat", {"node_id": "theory_op_0001", "message": "m"}, s, c)
    assert c.calls[0][2]["element_id"] == "comp_agent_1"
    n = len(c.calls)
    actions_exec.execute("admin.graph_review.node_chat", {"node_id": "theory_op_0002", "message": "m"}, s, c)
    assert len(c.calls) == n


def test_brain_latency_marked_harness():
    from uxsim.oracles import contract
    from uxsim.oracles.findings import FindingFactory
    from uxsim.schema import TranscriptStep
    import inspect
    fields = TranscriptStep.model_fields
    kw = {"persona_id": "p", "action_id": "learning.chat.ask", "observation": "",
          "http": [HttpTrace(method="POST", path="/x", status=200, elapsed_ms=130_000)]}
    for name, f in fields.items():
        if name not in kw and f.is_required():
            kw[name] = 0 if f.annotation in (int,) else ""
    step = TranscriptStep(**kw)
    f1 = contract.check([step], FindingFactory(None), brain_backed=True)
    f2 = contract.check([step], FindingFactory(None))
    assert any(x.hypothesis.startswith("ハーネス:") for x in f1)
    assert not any(x.hypothesis.startswith("ハーネス:") for x in f2)


def test_graph_chat_sends_screen_context():
    s = _session()
    s.maps["graph_sessions"] = {"D": "S"}
    c = FakeClient()
    actions_exec.execute("admin.graph_review.chat", {"document_id": "D", "message": "m"}, s, c)
    sc = c.calls[-1][2]["screen_context"]
    assert sc["screen"] == "graph_review" and sc["selection"]["document_id"] == "D" and "view" in sc


def test_materials_list_projects_health_chip():
    obs = project_observation("admin.materials.list", 200, [
        {"title": "T", "filename": "a.pdf", "status": "completed", "visibility": "public", "document_id": "d",
         "reference_health": {"status": "ok", "checked_at": "x"}}])
    assert "参照: 問題なし" in obs


def test_related_hides_module_key():
    obs = project_observation("admin.theory_modules.related", 200, {
        "available": True, "facts": [], "modules": [{"module_key": "m2:abc", "documents": [{"title": "P"}]}]})
    assert "m2:" not in obs and "module_key" not in obs and "P" in obs


def test_recon_options_by_label_and_hidden_ids():
    body = {"item": {"item_id": "I", "prompt": "q", "response_space": [
        {"id": "op_a", "label": "近似"}, {"id": "op_b", "label": "比較・検証"}]}}
    obs = project_observation("learning.reconstruction.next", 200, body)
    assert "op_a" not in obs and "近似" in obs
    s = _session()
    c = FakeClient({"reconstruction/next": body})
    actions_exec.execute("learning.reconstruction.next", {}, s, c)
    actions_exec.execute("learning.reconstruction.submit", {"response": {"text": "比較・検証"}}, s, c)
    assert c.calls[-1][2]["response"] == {"option_id": "op_b"}


def test_check_take_pairs_question_with_its_check_question():
    s = _session()
    s.topics["t1"] = {"check_questions": [{"question": "問1"}, {"question": "問2"}]}
    c = FakeClient()
    actions_exec.execute("learning.check.take", {"answer": "a", "question": "言い換えた問い"}, s, c)
    body = c.calls[-1][2]
    assert body["question"] == body["check_question"]["question"]
