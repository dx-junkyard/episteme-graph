"""第 25 周・面 B 第 1 周のハーネスの偽所見 H1〜H10 の是正（``uxsim/runs/round25/harness_fixes_doubt.md``）。

外部呼び出しなし（MockTransport）。画面の正本は app.js（showComponentExplanations / renderComponentContextPanel）・
admin.js（renderSeminarBrief）・personal-map-home.js（renderNearby）。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import behavior
from uxsim.oracles import doubt as oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import actions_exec, doubt_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_scenario
from uxsim.runner.state import (HARNESS_CLIPPED_MARK, HARNESS_PRECONDITION_TEXT, OBSERVATION_CHARS, PersonaSession,
                                _clip, _component_context_lines, project_observation)
from uxsim.schema import HttpTrace, RunMeta, ThinkAloud, TranscriptStep


def _client(handler, seen):
    def wrap(r):
        seen.append(r)
        return handler(r)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _run(action_id, args, s, client):
    client.take_traces()
    ctx = _Ctx(REGISTRY[action_id], args, s, client)
    assert actions_exec._HANDLERS[action_id] is not None
    actions_exec._HANDLERS[action_id](ctx)
    s.last_action = action_id
    return client.take_traces()


def _obs(s):
    return project_observation(s.last_action, s.last_status, s.last_body)


def _factory():
    return FindingFactory(RunMeta(run_id="r25b", campaign_id="c-r25-doubt-r2", domain="astrophysics", snapshot="s"))


def _trace(body, *, path, status=200, method="GET"):
    return HttpTrace(method=method, path=path, status=status, response_excerpt=json.dumps(body, ensure_ascii=False))


# --- H1 ----------------------------------------------------------------------
def test_h1_precondition_text_hides_runner_state_name_and_is_marked_harness():
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    client = _client(lambda r: httpx.Response(200, json=[]), [])
    traces = _run("admin.doubt.ledger_open", {}, s, client)
    obs = _obs(s)
    assert obs.startswith("（ハーネス: ") and "ledger_target" not in obs and "course_id" not in obs
    assert any((t.error or "") == "precondition:course_id" for t in traces)  # 状態名は trace に残る
    generic = project_observation("learning.chat.ask", None, {"_precondition": "topic_id"})
    assert generic == HARNESS_PRECONDITION_TEXT and "topic_id" not in generic


def test_h1_behavior_judge_marks_precondition_only_friction_as_harness():
    step = TranscriptStep(seq=3, persona_id="te-03", action_id="admin.doubt.ledger_open", screen="admin",
                          http=[HttpTrace(method="GET", path="/x", status=None, error="precondition:ledger_target")],
                          think=ThinkAloud(reaction="内部語 ledger_target が出た", friction="confused"))
    found, _ = behavior.check([step], _factory())
    assert found and all(f.hypothesis.startswith("ハーネス:") for f in found)


# --- H2 ----------------------------------------------------------------------
MATERIAL = {"chunks": [{"id": "ch1", "text": "本文", "graph_mentions": [
    {"element_type": "component", "element_id": "comp-uuid", "label": "DCF 法"}]}]}


def _learner_handler(expl):
    def h(r):
        p = r.url.path
        if p.endswith("/explanations"):
            return httpx.Response(200, json={"explanations": expl})
        if "/ledger/" in p:
            return httpx.Response(200, json={"fact_line": "このコーパスの中では検証記録がありません。", "scopes": []})
        return httpx.Response(404, json={"detail": "x"})
    return h


def test_h2_component_line_uses_explain_button_and_is_gated_by_approved_explanations():
    seen = []
    s = PersonaSession(persona_id="st-02", role="STUDENT", course_id="c1", topic_id="t1")
    s.material = MATERIAL
    s.remember("components", "lower-node-uuid")  # 直前の文脈で見えた下位ノード — これを引かない
    client = _client(_learner_handler([]), seen)
    _run("learning.ledger.component_line", {}, s, client)
    assert "承認済みの説明はまだありません。" in _obs(s)
    # 第 25 周 第 2 波: 説明ゼロでも空の文の下に一行を添える（app.js appendLearnerLedgerLine）。下位ノードは引かない
    assert any(r.url.path.endswith("/ledger/component/comp-uuid") for r in seen)
    assert not any("lower-node" in r.url.path for r in seen)
    seen.clear()
    client = _client(_learner_handler([{"kind": "standard", "body": "標準の説明文"}]), seen)
    s.scratch.pop("ledger_line_cache", None)
    _run("learning.ledger.component_line", {}, s, client)
    obs = _obs(s)
    assert "「DCF 法の説明」" in obs and "説明の下の一行: このコーパスの中では検証記録がありません。" in obs
    assert any(r.url.path.endswith("/ledger/component/comp-uuid") for r in seen)


def test_h2_component_line_falls_back_to_context_center_not_lower_node():
    seen = []
    s = PersonaSession(persona_id="st-02", role="STUDENT", course_id="c1", topic_id="t1")
    s.scratch["last_component_graph"] = {"focus": {"id": "center-uuid"}, "lower": [{"id": "lower-uuid"}]}
    s.remember("components", "lower-uuid")
    client = _client(_learner_handler([{"kind": "standard", "body": "b"}]), seen)
    _run("learning.ledger.component_line", {}, s, client)
    assert any("center-uuid" in r.url.path for r in seen) and not any("lower-uuid" in r.url.path for r in seen)


# --- H3 ----------------------------------------------------------------------
def test_h3_seminar_brief_mirrors_render_sections_without_dto_keys():
    brief = {"available": True, "document_id": "d1", "course_id": "", "fragile_assumptions": [],
             "single_support_lines": [{"target_id": "eq_op_1", "target_type": "component", "statement": "結論 A",
                                       "fact_line": "この対象は単一の支持線に立っています。"}],
             "clear_skies": [], "learner_handoff": {"items": [], "note": ""}}
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.remember("documents", "d1")
    client = _client(lambda r: httpx.Response(200, json=brief), [])
    _run("admin.seminar_brief.view", {}, s, client)
    obs = _obs(s)
    assert "〔一点吊りの支持線〕" in obs and "〔学習者からの問い〕" in obs
    assert "〔脆い前提〕" not in obs and "〔晴れ間〕" not in obs  # 行の無い区画は画面どおり見出しごと出ない
    assert "このコースの学習者の詰まりの記録はまだありません。" in obs
    for leak in ("available", "target_type", "eq_op_1", "document_id"):
        assert leak not in obs


# --- H4 ----------------------------------------------------------------------
def test_h4_component_context_draws_only_panel_fields():
    body = {"component_id": "x", "instance": {
        "component": {"label": "DCF 法", "component_type": "AssumptionComponent"}, "provenance": "course_freeze",
        "in_paper": {"narrative_role": "磁場の推定法", "document": {"title": "Cepheus B"}},
        "supports": {"preconditions": [{"text": "等分配が成り立つ"}], "equations": [{"id": "eq_1", "role": "output", "label": "式 (1)"}]}},
        "graph": {"focus": {"id": "x", "label": "DCF 法"}, "upper": [], "lower": []}}
    text = "\n".join(_component_context_lines(body))
    assert "見出し: 論理要素 ・ DCF 法" in text and "この論文での位置づけ: 磁場の推定法" in text
    assert "前提:\n- 等分配が成り立つ" in text and "出力 式 (1)" in text and "出典論文: Cepheus B" in text
    assert "AssumptionComponent" not in text and "course_freeze" not in text and "component_type" not in text


# --- H5 ----------------------------------------------------------------------
def test_h5_nearby_projects_range_view_without_edge_ids():
    dto = {"mode": "range", "available": True, "ledger_available": True, "range_fallback": False,
           "range_documents": [{"title": "Cepheus B", "nodes": [
               {"component_id": "theory_op_0001", "label": "理論の基礎", "touched": True,
                "verification": {"label": "間接的な支持あり"}}],
               "edges": [{"from": "theory_op_0001", "to": "theory_op_0002"}]}],
           "facts": ["この話題は第 2 節に触れています。"]}
    s = PersonaSession(persona_id="st-02", role="STUDENT", course_id="c1")
    s.remember("nodes", "n1")
    client = _client(lambda r: httpx.Response(200, json=dto), [])
    _run("learning.personal_network.nearby", {}, s, client)
    obs = _obs(s)
    assert "この話題が触れている範囲" in obs and "理論の基礎 ［間接的な支持あり］" in obs and "〔Cepheus B〕" in obs
    assert "theory_op" not in obs and "edges" not in obs


# --- H6 ----------------------------------------------------------------------
def test_h6_pin_course_overrides_persona_choice_and_notes_it():
    seen = []
    s = PersonaSession(persona_id="st-06", role="STUDENT")
    s.scratch.update(pinned_course_id="1bad7d9f", pin_course_strict=True)

    def h(r):
        return httpx.Response(200, json={"enrolled": True} if r.method == "POST" else {"topics": []})
    client = _client(h, seen)
    _run("learning.course.enroll", {"course_id": "6be229e1"}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/1bad7d9f/enroll" and s.course_id == "1bad7d9f"
    assert any(n.startswith("runner_fixed:") for n in s.scratch.get("runner_notes", []))
    s2 = PersonaSession(persona_id="st-06", role="STUDENT")
    s2.scratch["pinned_course_id"] = "1bad7d9f"  # strict でなければ差し替えない（取り違えを記録するだけ）
    client = _client(h, seen)
    _run("learning.course.enroll", {"course_id": "6be229e1"}, s2, client)
    assert seen[-1].url.path == "/api/learning/courses/6be229e1/enroll"


def test_h6_h8_scenarios_declare_pin_and_studio_entry_first():
    assert load_scenario("s-db-ledger-lines").raw.get("pin_course") is True
    for sid in ("t-db-scope-promote", "t-db-challenge-propose"):
        acts = [a for st in load_scenario(sid).steps for a in st.actions]
        assert acts.index("admin.doubt.studio_node_ledger") < acts.index("admin.doubt.ledger_open")


# --- H9 ----------------------------------------------------------------------
def test_h9_audit_documents_zero_cf_warnings_and_visibility_raw():
    steps = [
        TranscriptStep(seq=1, persona_id="te-03", action_id="admin.doubt.tab_open", screen="admin", http=[
            _trace([{"id": "c1", "title": "ゼミ", "topic_count": 18}], path="/api/admin/courses")]),
        TranscriptStep(seq=2, persona_id="te-03", action_id="admin.doubt.audit_run", screen="admin", http=[
            _trace({"ok": True, "skipped": True, "documents": 0, "registered": 0},
                   path="/api/admin/doubt/courses/c1/corpus-audit/run", method="POST")]),
        TranscriptStep(seq=3, persona_id="te-03", action_id="admin.doubt.counterfactual_toggle", screen="admin", http=[
            _trace({"collapsed_node_ids": [], "warnings": ["グラフ上に対応するノードが見つからなかった前提: x"]},
                   path="/api/admin/doubt/counterfactual/compute", method="POST")],
            observation="グラフ上で薄く点線になったノード（この前提に依存する範囲）: なし"),
        TranscriptStep(seq=4, persona_id="te-03", action_id="admin.doubt.counterfactual_save", screen="admin", http=[],
                       observation="（保存しました（共有範囲: private））"),
    ]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("教材を一つも読まなかった" in h for h in hyps)
    assert any("警告を返したのに" in h for h in hyps)
    assert any("共有範囲の内部値" in h for h in hyps)
    assert all(not any(ch.isdigit() for ch in h) for h in hyps)
    ok = [steps[0], TranscriptStep(seq=2, persona_id="te-03", action_id="admin.doubt.audit_run", screen="admin", http=[
        _trace({"ok": True, "skipped": False, "documents": 4, "registered": 2},
               path="/api/admin/doubt/courses/c1/corpus-audit/run", method="POST")])]
    assert oracle.check(ok, _factory()) == []


# --- H10 ---------------------------------------------------------------------
def test_h10_observation_clip_says_it_is_the_harness():
    out = _clip("あ" * (OBSERVATION_CHARS * 2))
    assert out.endswith(HARNESS_CLIPPED_MARK) and len(out) <= OBSERVATION_CHARS
    assert _clip("abcdef", 4) == "abc…"
