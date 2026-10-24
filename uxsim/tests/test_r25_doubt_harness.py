"""第 25 周・面 B（D層・SL層の教員側 ⇄ 学生の検証状態の一行）の行為・投影・審判（MockTransport・外部呼び出しなし）。

行為 = uxsim/actions/doubt_actions.py / 実行と投影 = uxsim/runner/doubt_exec.py / 審判 = uxsim/oracles/doubt.py。
画面の正本 = frontend/public/js/doubt-atlas.js・app.js（D3-6）、契約の正本 = backend/api/routes/doubt.py。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import doubt as oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import doubt_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

UUID = "0b0e4c9a-1111-4222-8333-944455556666"
ATLAS = {"course_id": "c1", "axes": {}, "points": [
    {"target_id": "a1", "target_type": "assumption", "label": "等分配が成り立つ", "verification_status": "untested",
     "scope_coverage": "none", "unscoped": True, "load_level": "high", "consensus_label": "", "has_naive_signal": False}]}
ASSUMPTIONS = {"course_id": "c1", "status": "candidate", "items": [
    {"id": "a1", "statement": "等分配が成り立つ", "origin": "corpus_audit", "status": "candidate", "created_from": {},
     "evidence_quote": "", "has_naive_signal": False},
    {"id": "a2", "statement": "乱流は等方的", "origin": "corpus_audit", "status": "candidate", "created_from": {}}]}
OPEN = {"course_id": "c1", "items": []}
LEDGER = {"target_id": "a1", "target_type": "assumption", "target_label": "等分配が成り立つ", "verification_status": "untested",
          "verification_scopes": [], "scope_candidates": [], "consensus_explicit_label": "", "consensus_behavioral_dependents": 0,
          "load_level": "high", "naive_signal": {"present": False},
          "challenges": [{"id": "ch1", "challenge_type": "scope_extrapolation", "reason": "外挿", "status": "open",
                          "challenger_name": "岡村", "challenge_mode": "direct"}],
          "evidence_lines": [], "falsification_conditions": [], "falsification_candidates": []}


def _client(handler, seen):
    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _admin_handler(overrides: dict[tuple[str, str], Any] | None = None):
    over = overrides or {}

    def h(r: httpx.Request) -> httpx.Response:
        key = (r.method, r.url.path)
        if key in over:
            v = over[key]
            return v if isinstance(v, httpx.Response) else httpx.Response(200, json=v)
        p = r.url.path
        if p == "/api/admin/courses":
            return httpx.Response(200, json=[{"id": "c1", "title": "宇宙論ゼミ", "role": "owner"}])
        if p.endswith("/assumption-atlas"):
            return httpx.Response(200, json=ATLAS)
        if p.endswith("/assumptions"):
            return httpx.Response(200, json=ASSUMPTIONS)
        if p.endswith("/open-assumptions"):
            return httpx.Response(200, json=OPEN)
        if p.endswith("/counterfactual/sessions"):
            return httpx.Response(200, json={"items": []})
        if p.startswith("/api/admin/doubt/ledger/") and r.method == "GET":
            return httpx.Response(200, json=LEDGER)
        return httpx.Response(200, json={"ok": True})
    return h


def _run(action_id: str, args: dict, session: PersonaSession, client: EpistemeClient) -> list[HttpTrace]:
    client.take_traces()
    ctx = _Ctx(REGISTRY[action_id], args, session, client)
    assert doubt_exec.execute(action_id, ctx)
    session.last_action = action_id
    return client.take_traces()


def _session(**kw) -> PersonaSession:
    s = PersonaSession(persona_id="te-03-meticulous-seminar", role="TEACHER", course_id="", topic_id="")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _obs(s: PersonaSession) -> str:
    return project_observation(s.last_action, s.last_status, s.last_body)


# ---------------------------------------------------------------------------
# 行為の登録と実行
# ---------------------------------------------------------------------------
def test_all_face_actions_registered_and_handled():
    ids = [a for a in REGISTRY if a.startswith(("admin.doubt.", "learning.ledger."))]
    # 面の行為 + 面 B が投影を引き受けた既存の行為（第 25 周 H3 ゼミ前ブリーフ / H5 いまここの周り）
    assert ids and set(ids) | {"admin.seminar_brief.view", "learning.personal_network.nearby"} == set(doubt_exec.HANDLERS)
    assert REGISTRY["admin.doubt.mining_run"].llm_cost == "product"
    assert REGISTRY["admin.doubt.audit_run"].llm_cost == "none"


def test_tab_open_selects_course_and_projects_four_sections():
    seen: list[httpx.Request] = []
    s = _session()
    _run("admin.doubt.tab_open", {}, s, _client(_admin_handler(), seen))
    paths = [r.url.path for r in seen]
    assert paths[0] == "/api/admin/courses" and "/api/admin/doubt/courses/c1/assumption-atlas" in paths
    assert seen[paths.index("/api/admin/doubt/courses/c1/assumptions")].url.params.get("status") == "candidate"
    obs = _obs(s)
    assert doubt_exec.TAB_HEAD in obs and "コース: 宇宙論ゼミ" in obs
    assert "点1: 等分配が成り立つ" in obs and "塗りなし・点線の輪郭" in obs
    assert "1. 等分配が成り立つ［corpus_audit / candidate］" in obs  # 画面は origin / status を生の語で出す
    assert doubt_exec.OPEN_EMPTY in obs and doubt_exec.CF_SESSIONS_EMPTY in obs


def test_assumption_confirm_with_empty_reason_sends_nothing():
    seen: list[httpx.Request] = []
    s = _session()
    client = _client(_admin_handler(), seen)
    _run("admin.doubt.tab_open", {}, s, client)
    n = len(seen)
    traces = _run("admin.doubt.assumption_confirm", {"pick": "1", "reason": ""}, s, client)
    assert len(seen) == n and not traces and "何も起きなかった" in _obs(s)
    _run("admin.doubt.assumption_confirm", {"pick": "1", "reason": "出所を読んだ"}, s, client)
    post = next(r for r in seen[n:] if r.method == "POST")
    assert post.url.path == "/api/admin/doubt/assumptions/a1/confirm" and json.loads(post.content)["reason"] == "出所を読んだ"


def test_direct_verification_blocked_without_scope_and_scope_422_shows_detail():
    seen: list[httpx.Request] = []
    s = _session()
    detail = "condition / domain / precision / system のうち 1 つ以上が必要です"
    client = _client(_admin_handler({("POST", "/api/admin/doubt/ledger/assumption/a1/scopes"):
                                     httpx.Response(422, json={"detail": detail})}), seen)
    _run("admin.doubt.tab_open", {}, s, client)
    _run("admin.doubt.ledger_open", {}, s, client)
    assert "台帳の詳細: 等分配が成り立つ" in _obs(s) and doubt_exec.NO_SCOPE in _obs(s)
    n = len(seen)
    _run("admin.doubt.verification_status", {"status": "直接検証の記帳あり", "reason": "確かめた"}, s, client)
    assert len(seen) == n and doubt_exec.VSTATUS_DIRECT_NEEDS_SCOPE in _obs(s)
    _run("admin.doubt.scope_record", {"reason": "理由"}, s, client)
    assert detail in _obs(s)


def test_challenge_422_fixed_text_withdraw_403_and_withdrawn_hidden():
    seen: list[httpx.Request] = []
    s = _session()
    withdrawn = dict(LEDGER, challenges=[dict(LEDGER["challenges"][0], status="withdrawn")])
    client = _client(_admin_handler({
        ("POST", "/api/admin/doubt/targets/assumption/a1/challenges"): httpx.Response(422, json={"detail": "reason（本人の言葉）が必要です"}),
        ("POST", "/api/admin/doubt/challenges/ch1/withdraw"): httpx.Response(403, json={"detail": "x"})}), seen)
    _run("admin.doubt.tab_open", {}, s, client)
    _run("admin.doubt.ledger_open", {}, s, client)
    _run("admin.doubt.challenge_record", {"challenge_type": "外挿"}, s, client)
    assert f"（フォームの下に表示: {doubt_exec.CHALLENGE_422}）" in _obs(s)
    _run("admin.doubt.challenge_withdraw", {}, s, client)
    assert doubt_exec.WITHDRAW_403 in _obs(s)
    assert doubt_exec.NO_CHALLENGE in doubt_exec.ledger_view({"label": "x", "target_type": "assumption"}, withdrawn)


def test_proposal_requires_external_check_on_screen_then_card():
    seen: list[httpx.Request] = []
    s = _session()
    client = _client(_admin_handler({("POST", "/api/admin/doubt/challenges/ch1/proposals"):
                                     {"ok": True, "proposal_id": "pp1", "challenge_status": "led_to_verification"}}), seen)
    _run("admin.doubt.tab_open", {}, s, client)
    _run("admin.doubt.ledger_open", {}, s, client)
    n = len(seen)
    _run("admin.doubt.challenge_propose", {"proposal": "取り直す"}, s, client)
    assert len(seen) == n and doubt_exec.PROPOSAL_EXTERNAL_EMPTY in _obs(s)
    _run("admin.doubt.challenge_propose", {"proposal": "取り直す", "external_check": "レビューを読んだ"}, s, client)
    assert "検証提案（コーパス外の確認あり）: 検証に着手可能［検証に着手］［取り下げ］" in _obs(s)
    _run("admin.doubt.proposal_status", {"status": "完了を記録"}, s, client)
    assert "そのボタンはこのカードに無い" in _obs(s)
    _run("admin.doubt.proposal_status", {"status": "検証に着手"}, s, client)
    assert seen[-1].method == "PATCH" and json.loads(seen[-1].content) == {"status": "in_progress"}


def test_studio_node_ledger_404_projects_empty_section_and_counterfactual():
    seen: list[httpx.Request] = []
    s = _session()
    s.scratch["doubt_course_id"] = "c1"
    graph = {"graph": {"nodes": [{"id": "theory_op_1", "label": "等分配の仮定", "graph_layer": "main"}]}}
    client = _client(_admin_handler({
        ("GET", "/api/admin/documents/d1/component-graph"): graph,
        ("GET", "/api/admin/doubt/ledger/component/theory_op_1"): httpx.Response(404, json={"detail": "Ledger entry not found"}),
        ("POST", "/api/admin/doubt/counterfactual/compute"): {
            "collapsed_node_ids": ["n2"], "indeterminate_node_ids": [], "surviving_node_ids": ["n3"],
            "node_labels": {"n2": "磁場強度の推定"}, "warnings": []}}), seen)
    _run("admin.doubt.studio_node_ledger", {"document_id": "d1"}, s, client)
    assert doubt_exec.NO_SCOPE in _obs(s) and "台帳の詳細: 等分配の仮定" in _obs(s)
    assert s.latest("graph_documents") == "d1"
    _run("admin.doubt.counterfactual_toggle", {"node_hint": "等分配"}, s, client)
    body = json.loads(seen[-1].content)
    assert body["toggled_assumption_ids"] == ["theory_op_1"] and body["course_id"] == "c1"
    assert "この前提に依存する範囲）: 磁場強度の推定" in _obs(s)


def test_learner_sources_tab_and_open_assumptions():
    seen: list[httpx.Request] = []
    s = PersonaSession(persona_id="st-06-skeptic-bao", course_id="c9", topic_id="t2")
    s.scratch["last_sources"] = {"1": "ch-1"}

    def h(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if p.endswith("/source-chunk/ch-1"):
            return httpx.Response(200, json={"formulas": [{"id": "[[FORMULA_1]]"}, {"id": "eq_3", "label": "(3)"}]})
        if p.endswith("/claim-refs"):
            return httpx.Response(200, json={"claims": [{"id": "cl1", "label": "磁場は 30 µG"}]})
        if p.endswith("/ledger/equation/eq_3"):
            return httpx.Response(404, json={"detail": "Ledger entry not found"})
        if p.endswith("/ledger/claim/cl1"):
            return httpx.Response(200, json={"fact_line": "この内容の検証スコープはまだ記帳されていません。", "scopes": []})
        if p.endswith("/open-assumptions"):
            return httpx.Response(200, json={"items": [{"statement": "等分配", "scope_count_is_zero": True}]})
        return httpx.Response(404, json={})
    client = _client(h, seen)
    _run("learning.ledger.sources_tab", {}, s, client)
    assert "主張 磁場は 30 µG — この内容の検証スコープはまだ記帳されていません。" in _obs(s)
    assert not any(r.url.path.endswith("FORMULA_1]]") for r in seen)
    _run("learning.ledger.open_assumptions", {}, s, client)
    assert doubt_exec.LEARNER_OPEN_SUMMARY in _obs(s) and "等分配（検証スコープの記帳なし）" in _obs(s)


# ---------------------------------------------------------------------------
# 審判
# ---------------------------------------------------------------------------
def _factory():
    return FindingFactory(RunMeta(run_id="r25", campaign_id="c-r25-doubt-r1", domain="astrophysics", snapshot="s"))


def _trace(body: Any, *, path: str, status=200, method="GET") -> HttpTrace:
    return HttpTrace(method=method, path=path, status=status, response_excerpt=json.dumps(body, ensure_ascii=False))


def _step(seq, action, *traces, persona="te-03", observation="", args=None):
    return TranscriptStep(seq=seq, persona_id=persona, action_id=action, screen="admin" if action.startswith("admin") else "learning",
                          http=list(traces), observation=observation, args=args or {})


def test_vocab_is_read_from_product_guardrail_tests():
    assert "この分野では未検証" in oracle.closed_world_banned() and "ノーベル賞" in oracle.closed_world_banned()
    assert {"cut_members", "recorded_by", "dependent_count"} <= oracle.learner_forbidden_keys()
    assert {"confidence", "load_score"} <= oracle.teacher_forbidden_keys()


def test_closed_world_field_sentence_on_screen_and_learner_keys():
    step = _step(1, "learning.ledger.open_assumptions",
                 _trace({"items": [{"statement": "x", "cut_members": []}]}, path="/api/learning/courses/c/open-assumptions"),
                 observation="「この分野の未検証の前提を見る」を開いた:")
    found = oracle.check([step], _factory())
    keys = {f.fingerprint for f in found}
    assert any("この分野" in f.hypothesis for f in found)
    assert any("記帳者・根拠の生データ" in f.hypothesis for f in found)
    assert len(keys) == len(found)
    clean = _step(2, "learning.ledger.open_assumptions",
                  _trace({"items": [{"statement": "x"}]}, path="/api/learning/courses/c/open-assumptions"),
                  observation="このコーパスの中では検証記録がありません。")
    assert oracle.check([clean], _factory()) == []


def test_recorder_uuid_universal_verification_and_english_detail():
    led = dict(LEDGER, verification_status="directly_verified", verification_scopes=[])
    led2 = dict(LEDGER, verification_scopes=[{"condition": "a", "recorded_by": UUID}])
    steps = [
        _step(1, "admin.doubt.ledger_open", _trace(led, path="/api/admin/doubt/ledger/assumption/a1")),
        _step(2, "admin.doubt.ledger_open", _trace(led2, path="/api/admin/doubt/ledger/assumption/a1")),
        _step(3, "admin.doubt.verification_status",
              _trace({"detail": "invalid verification_status: x"}, path="/api/admin/doubt/ledger/assumption/a1/verification-status",
                     status=422, method="PUT")),
    ]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("全称検証" in h for h in hyps) and any("内部 ID" in h for h in hyps) and any("日本語" in h for h in hyps)


def test_retention_and_permission_lines():
    gone = dict(LEDGER, challenges=[])
    steps = [
        _step(1, "admin.doubt.challenge_withdraw",
              _trace({"ok": True}, path="/api/admin/doubt/challenges/ch1/withdraw", method="POST"),
              _trace(gone, path="/api/admin/doubt/ledger/assumption/a1")),
        _step(2, "admin.doubt.counterfactual_observation",
              _trace({"detail": "Course not found"}, path="/api/admin/doubt/courses/c7/observation-targets", status=404)),
        _step(3, "admin.doubt.load_recompute",
              _trace({"ok": True, "updated": 0}, path="/api/admin/doubt/courses/c7/load/recompute", method="POST")),
    ]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("行ごと消えている" in h for h in hyps) and any("権限ゲートが揃っていない" in h for h in hyps)
    kept = dict(LEDGER, challenges=[dict(LEDGER["challenges"][0], status="withdrawn")])
    ok = [_step(1, "admin.doubt.challenge_withdraw",
                _trace({"ok": True}, path="/api/admin/doubt/challenges/ch1/withdraw", method="POST"),
                _trace(kept, path="/api/admin/doubt/ledger/assumption/a1"))]
    assert oracle.check(ok, _factory()) == []


def test_written_targets_for_observation():
    steps = [_step(1, "admin.doubt.scope_record",
                   _trace({"ok": True}, path="/api/admin/doubt/ledger/component/n1/scopes", method="POST"))]
    assert oracle.written_ledger_targets(steps) == [{"target_type": "component", "target_id": "n1", "what": ["scopes"], "seq": 1}]
    assert oracle.check_observation(None, _factory(), "", steps=steps) == []
