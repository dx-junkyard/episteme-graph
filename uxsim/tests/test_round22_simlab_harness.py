"""第 22 周（SB層「🧪 動かして確かめる」）のハーネス — 場面の読み込み・新しい行為・審判 B10/B11/E4/E5・投影の新キー・reset の選別。

外部呼び出しなし（製品は httpx.MockTransport、DB はモック engine / 偽の行）。正本は
docs/architecture/persona_enactment_testing_design.md §19。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import simulation
from uxsim.oracles.findings import FindingFactory
from uxsim.runner.actions_exec import execute, simulation_screen_context
from uxsim.runner.api import load_campaign
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_params, load_scenario
from uxsim.runner.simlab_exec import project, selected_module_digest, when
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.sandbox.reset_learner_state import (LEARNER_TABLES, LEDGER_CANDIDATES_STRIP_SQL, persona_email,
                                               select_targets)
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

UXSIM = Path(__file__).resolve().parents[1]
CAMPAIGNS = {
    "c-simulation-lab-r22.yaml": {
        "s-sim-first-open-io": ["st-01-m1-radio"],
        "s-sim-rewire-scaled": ["st-06-skeptic-bao"],
        "s-sim-share-measured": ["st-04-intl-exoplanet"],
        "s-sim-reference-value": ["st-10-d3-21cm-receiver", "st-03-d1-from-particle"],
        "s-sim-english-meaning": ["st-04-intl-exoplanet"],
        "s-sim-voice-result": ["st-07-m1-grb-voice"],
        "s-sim-stale-runs": ["st-06-skeptic-bao"],
        "s-sim-share-measured-only": ["st-04-intl-exoplanet"],
    },
    "c-simulation-lab-teacher-r22.yaml": {
        "t-sim-decide-candidates": ["te-03-meticulous-seminar", "te-04-copilot-intro"],
    },
}


def _archetype(persona_id: str) -> str:
    kind = "students" if persona_id.startswith("st-") else "teachers"
    data = yaml.safe_load((UXSIM / "domains" / "astrophysics" / "personas" / kind / f"{persona_id}.yaml").read_text())
    return str(data.get("archetype") or "")


# ---------------------------------------------------------------------------
# 場面の読み込み
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("campaign_file", sorted(CAMPAIGNS))
def test_campaign_scenarios_params_and_personas(campaign_file):
    camp = load_campaign(UXSIM / "campaigns" / "astrophysics" / campaign_file)
    personas = set((camp.get("personas") or {}).get("students") or []) | set((camp.get("personas") or {}).get("teachers") or [])
    listed = [s for group in (camp.get("scenarios") or {}).values() for s in group]
    assert sorted(listed) == sorted(CAMPAIGNS[campaign_file])
    # 第 21 周の 8 場面は新 campaign に含めない（再演で回す）
    assert not set(listed) & {"s-sim-first-open", "s-sim-rewire-revise", "s-sim-share-meaning", "t-sim-review-shared"}
    for sid, who in CAMPAIGNS[campaign_file].items():
        sc = load_scenario(sid)
        for action in sc.referenced_actions():
            assert action in REGISTRY, (sid, action)
        for pid in who:
            assert pid in personas, (campaign_file, pid)
            params = load_params("astrophysics", sid, pid, archetype_id=_archetype(pid))
            assert sc.missing_params(params) == [], (sid, pid)
            # UUID・module_key を台本に書かない
            text = json.dumps(params, ensure_ascii=False)
            assert "m2:" not in text and not any(len(w) == 36 and w.count("-") == 4 for w in text.split('"'))


def test_new_scenarios_point_to_existing_manual_sections():
    manual = UXSIM.parent / "docs" / "manual"
    for sid in [s for c in CAMPAIGNS.values() for s in c]:
        data = yaml.safe_load((UXSIM / "scenarios" / "goal" / ("teacher" if sid.startswith("t-") else "student")
                               / f"{sid}.yaml").read_text())
        anchors = [e["manual"] for e in data.get("expects") or [] if isinstance(e, dict) and e.get("manual")]
        assert anchors, sid
        for a in anchors:
            path, frag = a.split("#")
            assert "{#" + frag + "}" in (manual / path).read_text(encoding="utf-8"), a


def test_round22_campaign_declares_generation_budget():
    data = yaml.safe_load((UXSIM / "campaigns" / "astrophysics" / "c-simulation-lab-r22.yaml").read_text())
    gens = []
    for sid in data["scenarios"]["student"]:
        sc = yaml.safe_load((UXSIM / "scenarios" / "goal" / "student" / f"{sid}.yaml").read_text())
        gens += [sid for st in sc["steps"] if isinstance(st, dict) and st.get("do") == "learning.simlab.generate"]
    assert sorted(gens) == sorted(["s-sim-rewire-scaled", "s-sim-share-measured", "s-sim-reference-value",
                                   "s-sim-voice-result"])  # 学生 1 人 1 回


# ---------------------------------------------------------------------------
# 行為（MockTransport）
# ---------------------------------------------------------------------------
def _client(handler, seen):
    def wrap(request):
        seen.append(request)
        return handler(request)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _body(r):
    return json.loads(r.content.decode("utf-8")) if r.content else {}


TARGETS = [{"claim_id": "c-pol", "label": "This value represented the polarization angle dispersion at the central pixel."},
           {"claim_id": "c-vel", "label": "v denotes the velocity dispersion of the 13CO molecular line."}]


def test_run_sends_reference_and_two_claims_and_share_sends_both():
    seen = []
    run = {"id": "r1", "program_id": "p1", "status": "succeeded"}
    client = _client(lambda r: httpx.Response(200, json={"run": run}), seen)
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t2")
    s.scratch.update(sim_targets=TARGETS, sim_programs_dto=[{"id": "p1", "review_status": "confirmed"}])
    execute("learning.simlab.run", {"values": {"n": 1.0}, "claim_hints": ["polarization angle", "13CO molecular"],
                                    "reference": {"B_0": {"value": 1.53e-4, "unit": "G", "source_note": "論文"}},
                                    "wiring": "none"}, s, client)
    body = _body(seen[-1])
    assert body["observation_claim_ids"] == ["c-pol", "c-vel"]
    assert body["reference"] == [{"symbol": "B_0", "value": 1.53e-4, "unit": "G", "source_note": "論文"}]
    execute("learning.simlab.share", {"claim_hints": ["polarization angle", "13CO molecular"]}, s, client)
    assert seen[-1].url.path == "/api/learning/simulation/runs/r1/share"
    assert _body(seen[-1]) == {"claim_ids": ["c-pol", "c-vel"]}


def _teacher_session():
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch["sim_shared_runs_dto"] = [{"id": "r1", "observation_claim_ids": ["c-pol", "c-vel"], "shared": True}]
    s.scratch["sim_targets"] = TARGETS
    return s


def _ledger_handler(r):
    if r.method == "GET" and r.url.path.endswith("/claim/c-pol"):
        return httpx.Response(200, json={"simulation_candidates": [{"candidate_id": "k-pol", "run_id": "r1"}]})
    if r.method == "GET" and r.url.path.endswith("/claim/c-vel"):
        return httpx.Response(200, json={"simulation_candidates": [{"candidate_id": "k-vel", "run_id": "r1"}]})
    return httpx.Response(200, json={"candidate": {"status": "dismissed", "dismiss_reason": "形になっていない"},
                                     "verification_status": "untested"})


def test_ledger_dismiss_requires_reason_and_picks_claim_by_label():
    seen = []
    s = _teacher_session()
    traces = execute("admin.simulation.ledger_candidate_dismiss", {"claim_hint": "13CO"}, s, _client(_ledger_handler, seen))
    assert not seen and traces[-1].error == "precondition:reason"  # 画面は理由が空のあいだ押せない
    execute("admin.simulation.ledger_candidate_dismiss", {"claim_hint": "13CO", "reason": "形になっていない"}, s,
            _client(_ledger_handler, seen))
    assert seen[-1].method == "POST"
    assert seen[-1].url.path == "/api/admin/doubt/ledger/claim/c-vel/simulation-candidates/k-vel/dismiss"
    assert _body(seen[-1]) == {"reason": "形になっていない"}
    obs = project_observation("admin.simulation.ledger_candidate_dismiss", s.last_status, s.last_body)
    assert "見送りました" in obs and "見送りの理由: 形になっていない" in obs


def test_ledger_confirm_picks_claim_by_label():
    seen = []
    s = _teacher_session()
    execute("admin.simulation.ledger_candidate_confirm", {"claim_hint": "polarization"}, s, _client(_ledger_handler, seen))
    assert seen[-1].url.path.endswith("/claim/c-pol/simulation-candidates/k-pol/confirm")


def test_review_shared_run_projects_labels_and_decided_reason_without_claim_id():
    seen = []

    def handler(r):
        if r.url.path == "/api/admin/simulation/runs/r1":
            return httpx.Response(200, json={"run": {"id": "r1", "program_id": "p", "status": "succeeded", "shared": True,
                                                     "shared_by_label": "学生四", "observation_claim_ids": ["c-pol", "c-vel"]}})
        if r.url.path.endswith("/claim/c-pol"):
            return httpx.Response(200, json={"simulation_candidates": [{"candidate_id": "k1", "run_id": "r1",
                                                                         "statement": "このモデルと配線のもとでは…"}]})
        return httpx.Response(200, json={"simulation_candidates": [], "simulation_candidates_decided": [
            {"candidate_id": "k2", "run_id": "r1", "status": "dismissed", "dismiss_reason": "入力を与えただけ"}]})

    s = _teacher_session()
    execute("admin.simulation.review_shared_run", {}, s, _client(handler, seen))
    obs = project_observation("admin.simulation.review_shared_run", s.last_status, s.last_body)
    assert "polarization angle dispersion" in obs and "見送り（見送りの理由: 入力を与えただけ）" in obs
    assert "主張 c-pol" not in obs


def test_chat_carries_simulation_run_id_like_app_js():
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t2")
    assert simulation_screen_context(s, "latest", "t2") is None  # 記録が無ければキーごと送らない
    s.scratch["sim_runs_dto"] = [{"id": "r-new"}, {"id": "r-old"}]
    ctx = simulation_screen_context(s, "latest", "t2", mode="voice")
    assert ctx["selection"]["simulation_run_id"] == "r-new" and ctx["screen"] == "learning"
    assert ctx["view"]["mode"] == "voice" and ctx["selection"]["kind"] == "topic"
    seen = []
    client = _client(lambda r: httpx.Response(200, json={"answer": "…"}), seen)
    execute("learning.chat.ask", {"message": "What does unmeasured mean here?", "simulation_run_id": "latest"}, s, client)
    body = _body(seen[-1])
    assert body["screen_context"]["selection"]["simulation_run_id"] == "r-new"
    assert "simulation_run_id" not in body
    execute("learning.chat.ask", {"message": "ふつうの問い"}, s, client)
    assert "screen_context" not in _body(seen[-1])


# ---------------------------------------------------------------------------
# 投影の新キー
# ---------------------------------------------------------------------------
def _module(**kw):
    m = {"module_key": "m2:x", "level": "outer", "label": "定義: $P$", "theory_object": "$P$", "inputs": [], "outputs": [],
         "assumptions": [], "has_equation_text": True, "reconstructed_inputs": False, "program": None}
    m.update(kw)
    return m


def test_module_projection_shows_symbols_subtitle_and_simulability():
    body = {"document_id": "d", "modules": [
        _module(input_symbols=["\\delta\\theta", "\\sigma_v"], output_symbols=["\\sigma_\\xi"], subtitle="生む式: 式 (3)"),
        _module(label="制約を適用: $L$", input_symbols=[], output_symbols=[], simulable=False,
                simulability_note="このまとまりの式は、量の関係ではなく学習・最適化の手続（損失・目的関数）を表しているため…")],
        "parts_catalog": [], "observation_targets": []}
    obs = project("learning.simlab.open", 200, body)
    assert "受け取る記号: \\delta\\theta、\\sigma_v → 定める記号: \\sigma_\\xi" in obs
    assert "生む式: 式 (3)" in obs and "学習・最適化の手続" in obs
    assert "— → —" not in obs  # 空の入出力を「—」で埋めない


def test_run_projection_shows_date_and_ledger_decision_fact():
    run = {"id": "r", "program_id": "p", "status": "succeeded", "shared": True, "created_at": "2026-10-03T00:00:00Z",
           "result": {}, "ledger_decision": {"status": "candidate", "fact": "共有した実行は、台帳の候補として担当教員の判断を待っています。"}}
    obs = project("learning.simlab.share", 200, {"run": run, "facts": []})
    assert when("2026-10-03T00:00:00Z") in obs and "判断を待っています" in obs
    quiet = project("learning.simlab.run", 200, {"run": {**run, "shared": False, "ledger_decision": {"status": "none", "fact": ""}}})
    assert "判断" not in quiet
    ov = project("learning.simlab.open", 200, {"document_id": "d", "modules": [], "runs": [run]})
    assert "これまでの実行（あなたの記録）:\n  - " + when(run["created_at"]) + " 実行できました / 共有済み" in ov
    assert "    共有した実行は、台帳の候補として担当教員の判断を待っています。" in ov


def test_parts_projection_carries_unit_note():
    obs = project("learning.simlab.open", 200, {"document_id": "d", "modules": [], "parts_catalog": [
        {"entry_type_label": "ノイズ源", "name": "ガウス雑音", "params": [{"name": "sigma", "default": 0.1, "unit": ""}]}]})
    assert "（理論の出力と同じ単位の絶対値）" in obs


# ---------------------------------------------------------------------------
# 審判 B10 / B11 / E4 / E5
# ---------------------------------------------------------------------------
def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c", domain="astro", snapshot="s",
                                  started_at="2026-10-04T00:00:00+00:00", finished_at="2026-10-04T01:00:00+00:00"))


def _step(seq, action, *traces, persona="st-1"):
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="s", action_id=action,
                          screen="admin" if action.startswith("admin.") else "learning", http=list(traces))


def _sel_trace(module):
    return HttpTrace(method="GET", path="/api/learning/courses/c/simulation/overview", status=200,
                     response_excerpt='{"modules": [', digest={"sim_selected_module": selected_module_digest(module)})


def test_b10_selected_module_without_symbols():
    empty = _module(input_symbols=[], output_symbols=[])
    found = simulation.check([_step(1, "learning.simlab.select_module", _sel_trace(empty))], _factory())
    assert any("受け取る記号も定める記号も" in f.hypothesis for f in found)
    ok = _module(input_symbols=["n"], output_symbols=[])
    assert not simulation.check([_step(1, "learning.simlab.select_module", _sel_trace(ok))], _factory())
    not_sim = _module(input_symbols=[], output_symbols=[], simulable=False)
    assert not simulation.check([_step(1, "learning.simlab.select_module", _sel_trace(not_sim))], _factory())
    legacy = {k: v for k, v in empty.items() if k not in ("input_symbols", "output_symbols")}  # 旧 DTO は対象外
    assert not simulation.check([_step(1, "learning.simlab.select_module", _sel_trace(legacy))], _factory())


def _admin_run(wiring_id, by_symbol):
    return {"id": "r", "program_id": "p", "status": "succeeded", "wiring_id": wiring_id, "shared": True,
            "result": {"comparison": {"agreement_level": "match", "separation_level": "clear",
                                      "metrics": {"relative_difference": None, "by_symbol": by_symbol}}}}


def test_b11_unwired_run_with_numeric_relative_difference():
    path = "/api/admin/simulation/runs/r"
    bad = HttpTrace(method="GET", path=path, status=200,
                    response_excerpt=json.dumps({"run": _admin_run(None, {"x": {"relative_difference": 0.0}})}))
    found = simulation.check([_step(1, "admin.simulation.review_shared_run", bad, persona="te")], _factory())
    assert any("配線なしの実行" in f.hypothesis for f in found)
    wired = HttpTrace(method="GET", path=path, status=200,
                      response_excerpt=json.dumps({"run": _admin_run("w", {"x": {"relative_difference": 0.1}})}))
    nulled = HttpTrace(method="GET", path=path, status=200,
                       response_excerpt=json.dumps({"run": _admin_run(None, {"x": {"relative_difference": None,
                                                                                    "reference_relative_difference": 0.2}})}))
    for t in (wired, nulled):
        assert not [f for f in simulation.check([_step(1, "admin.simulation.review_shared_run", t, persona="te")], _factory())
                    if "配線なし" in f.hypothesis]
    # 抜粋が切れていても digest から読む
    from uxsim.runner.digest import response_digest

    cut = HttpTrace(method="GET", path=path, status=200, response_excerpt='{"run": {"id": "r", "pro',
                    digest=response_digest({"run": _admin_run(None, {"x": {"separation_ratio": 3.0}})}))
    assert any("配線なし" in f.hypothesis
               for f in simulation.check([_step(1, "admin.simulation.open", cut, persona="te")], _factory()))


def _decide_step(seq, action, cid, status=200):
    path = f"/api/admin/doubt/ledger/claim/c-x/simulation-candidates/{cid}/{action}"
    return _step(seq, f"admin.simulation.ledger_candidate_{action}",
                 HttpTrace(method="GET", path="/api/admin/doubt/ledger/claim/c-x", status=200),
                 HttpTrace(method="POST", path=path, status=status), persona="te")


def test_decided_candidates_from_transcript():
    got = simulation.decided_candidates([_decide_step(1, "dismiss", "k1"), _decide_step(2, "confirm", "k2"),
                                         _decide_step(3, "confirm", "k3", status=404)])
    assert [(d["cid"], d["action"], d["tid"]) for d in got] == [("k1", "dismiss", "c-x"), ("k2", "confirm", "c-x")]


def test_e4_e5_ledger_decisions():
    f = _factory()
    ok = [{"action": "dismiss", "ttype": "claim", "tid": "c", "cid": "k1", "seq": 1, "found": True,
           "verification_status": "untested", "candidate": {"status": "dismissed", "dismiss_reason": "理由"}},
          {"action": "confirm", "ttype": "claim", "tid": "c", "cid": "k2", "seq": 2, "found": True,
           "verification_status": "indirectly_supported", "candidate": {"status": "confirmed"}}]
    assert simulation.check_ledger_decisions(ok, f) == []
    bad = [{**ok[0], "candidate": {"status": "dismissed", "dismiss_reason": ""}},
           {**ok[0], "candidate": None},
           {**ok[1], "verification_status": "directly_verified"}]
    hyps = [x.hypothesis for x in simulation.check_ledger_decisions(bad, f)]
    assert any("理由が残っていない" in h for h in hyps)
    assert any("配列に残っていない" in h for h in hyps)
    assert any("直接検証" in h for h in hyps)
    assert all(x.oracle == "E" and "doubt_d" in x.suspected_layer for x in simulation.check_ledger_decisions(bad, f))


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        assert sql.lstrip().upper().startswith("SELECT")
        self.sql.append((sql, params))
        return _Rows(self.rows)


class _Engine:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def connect(self):
        return self.conn


def test_collect_ledger_reads_only():
    eng = _Engine([{"verification_status": "indirectly_supported", "candidate": {"candidate_id": "k1", "status": "confirmed"}}])
    rows = simulation.collect_ledger("", [{"ttype": "claim", "tid": "c", "cid": "k1", "action": "confirm", "seq": 1}], eng)
    assert rows[0]["verification_status"] == "indirectly_supported" and rows[0]["candidate"]["status"] == "confirmed"
    assert eng.conn.sql[0][1] == {"cid": "k1", "ttype": "claim", "tid": "c"}


# ---------------------------------------------------------------------------
# reset（運転側の手）
# ---------------------------------------------------------------------------
def test_reset_selects_and_excludes_personas():
    rows = [("u1", persona_email("st-01-m1-radio")), ("u6", persona_email("st-06-skeptic-bao"))]
    assert select_targets(rows, [], ["st-06-skeptic-bao"]) == [rows[0]]
    assert select_targets(rows, ["st-06-skeptic-bao"], []) == [rows[1]]
    assert select_targets(rows, [], []) == rows


def test_reset_covers_sb_tables_in_fk_order_and_strips_candidates_without_delete():
    tables = [t for t, _ in LEARNER_TABLES]
    assert tables.index("simulation_runs") < tables.index("instrument_wirings") < tables.index("simulation_programs")
    assert dict(LEARNER_TABLES)["simulation_runs"] == "owner_user_id"
    sql = LEDGER_CANDIDATES_STRIP_SQL.upper()
    assert sql.startswith("UPDATE EPISTEMIC_LEDGER") and "DELETE" not in sql and "CREATED_BY" in sql


# ---------------------------------------------------------------------------
# 第 22 周 第 2 波の UI に合わせた投影（REPORT_W2 §5 の 7 点 + 学生所見 (a)〜(e)）
# ---------------------------------------------------------------------------
def test_module_projection_output_symbols_note_and_to_sink_line_like_ui():
    m = _module(input_symbols=["l", "d"], output_symbols=[], output_symbols_note="このまとまりは条件を表し、値を定める記号がありません。",
                to_sink=[{"display_label": "式 (9)"}])
    obs = project("learning.simlab.open", 200, {"document_id": "d", "modules": [m]})
    lines = obs.split("\n")
    i = lines.index("   受け取る記号: l、d → 定める記号: ")
    assert lines[i + 1] == "   このまとまりは条件を表し、値を定める記号がありません。"  # 1: 記号行の直後
    assert "   → 論文全体のまとめへ" in lines and "— → —" not in obs  # 2: to_sink は別の 1 行
    with_io = project("learning.simlab.open", 200, {"document_id": "d", "modules": [
        _module(inputs=[{"display_label": "式 (1)"}], outputs=[{"display_label": "式 (2)"}], to_sink=[{"x": 1}])]})
    assert "   式 (1) → 式 (2)\n   → 論文全体のまとめへ" in with_io and "（→ 論文全体のまとめへ）" not in with_io


def test_overview_history_carries_ledger_decision_and_comes_before_modules():
    runs = [{"id": "r2", "status": "succeeded", "shared": True, "created_at": "2026-10-03T00:00:00Z",
             "result": {"labels": {"agreement": "理論の出力と模擬観測: 一致"}},
             "ledger_decision": {"status": "dismissed", "fact": "担当教員はこの候補を見送りました。"}},
            {"id": "r1", "status": "succeeded", "shared": False, "ledger_decision": {"status": "none", "fact": "x"}}]
    mods = [_module(label=f"定義: $X_{i}$", assumptions=[{"text": "長い前提 " * 80}]) for i in range(30)]
    obs = project("learning.simlab.open", 200, {"document_id": "d", "modules": mods, "runs": runs,
                                               "observation_targets": TARGETS})
    assert "    担当教員はこの候補を見送りました。" in obs  # 3
    assert "    x" not in obs.split("\n")  # status none は描かない
    # (e) 6000 字で切れても、自分の記録と関係する主張は一覧より前にあるので読める
    assert obs.index("これまでの実行（あなたの記録）") < obs.index("理論モジュールを選ぶ:")
    assert obs.index("関係する主張（観測）") < obs.index("理論モジュールを選ぶ:")


def test_program_projection_reads_parameters_like_render_predict_step():
    prog = {"id": "p", "review_status": "confirmed", "source_code": "def simulate(i, p): ...",
            "io_contract": {"inputs": [{"symbol": "n", "unit": "cm^-3", "default": 1000}],
                            "outputs": [{"symbol": "B_0", "unit": "G"}],
                            "parameters": [{"symbol": "Q", "default": 0.5}]}}
    obs = project("learning.simlab.adopt", 200, {"program": prog})
    assert "パラメータ: Q" in obs  # 4: params ではなく parameters
    # 画面の ④ の欄の並び: 予想 → 入力 → パラメータ
    assert obs.index("④ あなたの予想の欄") < obs.index("④ 入力の欄: n [cm^-3]（既定 1000）") < obs.index("④ パラメータの欄: Q（既定 0.5）")


def _review_handler(target_label=""):
    def handler(r):
        if r.url.path == "/api/admin/simulation/runs/r1":
            return httpx.Response(200, json={"run": {"id": "r1", "program_id": "p", "status": "succeeded", "shared": True,
                                                     "observation_claim_ids": ["c-x"]}})
        return httpx.Response(200, json={"target_label": target_label,
                                         "simulation_candidates": [{"candidate_id": "k", "run_id": "r1"}]})
    return handler


def test_review_without_permission_shows_buttons_not_pressable_and_target_label_fallback():
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch.update(sim_shared_runs_dto=[{"id": "r1", "observation_claim_ids": ["c-x"], "shared": True}],
                     sim_targets=[], sim_can_decide_ledger=False)
    execute("admin.simulation.review_shared_run", {}, s, _client(_review_handler("台帳の対象ラベル"), []))
    obs = project_observation("admin.simulation.review_shared_run", s.last_status, s.last_body)
    assert "［確定する］［見送る］（押せない: この教材の編集権限が無いため" in obs  # 5
    assert "台帳の対象ラベル" in obs and "（主張の本文を表示できません）" not in obs  # 6


def test_admin_open_remembers_can_decide_ledger():
    s = PersonaSession(persona_id="te", role="TEACHER")
    client = _client(lambda r: httpx.Response(200, json={"runs": [], "can_decide_ledger": False}), [])
    execute("admin.simulation.open", {"document_id": "d1"}, s, client)
    assert s.scratch["sim_can_decide_ledger"] is False


def test_level_chips_have_no_heading_like_ui():
    run = {"id": "r", "status": "succeeded", "result": {"labels": {"agreement": "理論の出力と模擬観測: 一致",
                                                                  "separation": "効果とばらつき: 分かれる"}}}
    obs = project("learning.simlab.run", 200, {"run": run})
    assert "\n理論の出力と模擬観測: 一致 / 効果とばらつき: 分かれる\n" in obs and "段階:" not in obs  # 7


def test_select_module_projection_lists_observation_targets_first_12():
    targets = [{"claim_id": f"c{i}", "label": f"観測の主張 {i}"} for i in range(15)]
    body = {"_sim_selected": _module(), "parts_catalog": [], "observation_targets": targets}
    obs = project("learning.simlab.select_module", 200, body)
    assert "関係する主張（観測）（④で実行の前に選ぶ）:" in obs  # (a)
    assert "  ☐ 観測の主張 11" in obs and "観測の主張 12" not in obs and "  …" in obs


def test_claim_hint_miss_is_noted_and_projected_and_run_shows_chosen_claims():
    seen = []
    run = {"id": "r1", "program_id": "p1", "status": "succeeded", "observation_claim_ids": ["c-pol"]}
    client = _client(lambda r: httpx.Response(200, json={"run": run}), seen)
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t2")
    s.scratch.update(sim_targets=TARGETS, sim_programs_dto=[{"id": "p1", "review_status": "confirmed"}])
    execute("learning.simlab.run", {"values": {"n": 1.0}, "claim_hints": ["polarization angle", "my own made-up claim"]},
            s, client)
    assert _body(seen[-1])["observation_claim_ids"] == ["c-pol"]
    assert any("主張『my own made-up claim』は観測系の主張の一覧に無い" in n for n in s.scratch["runner_notes"])  # (b)
    obs = project_observation("learning.simlab.run", s.last_status, s.last_body)
    assert "（主張『my own made-up claim』は観測系の主張の一覧に無いため、選ばれていません）" in obs
    assert "選んだ観測系の主張: This value represented the polarization angle dispersion" in obs and "c-pol" not in obs  # (c)


def test_find_module_matches_subtitle():
    from uxsim.runner.simlab_exec import find_module
    mods = [_module(label="定義: $P$"), _module(label="結果: $B$", subtitle="生む式: 式 (eq:dcf)")]
    assert find_module(mods, "eq:dcf") is mods[1]  # (d)


def test_share_measured_only_path_skips_generation_and_reuses_wiring():
    sc = load_scenario("s-sim-share-measured-only")
    actions = [st.get("do") for st in yaml.safe_load(
        (UXSIM / "scenarios" / "goal" / "student" / "s-sim-share-measured-only.yaml").read_text())["steps"]]
    assert "learning.simlab.generate" not in actions and "learning.simlab.adopt" not in actions
    assert actions[-3:] == ["learning.simlab.wiring_save", "learning.simlab.run", "learning.simlab.share"]
    base = load_params("astrophysics", "s-sim-share-measured", "st-04-intl-exoplanet", archetype_id="non_native")
    only = load_params("astrophysics", "s-sim-share-measured-only", "st-04-intl-exoplanet", archetype_id="non_native")
    assert only == base and sc.missing_params(only) == []
    raw = yaml.safe_load((UXSIM / "scenarios" / "goal" / "student" / "s-sim-share-measured-only.yaml").read_text())
    assert any("候補が 2 件立つ" in w for w in raw["watch_for"])
    # 保存済みの同名の配線があれば保存し直さない
    seen = []
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t2")
    s.scratch["sim_wirings_dto"] = [{"id": "w-old", "name": "雑音と平均化", "origin": "learner"}]
    execute("learning.simlab.wiring_save", {"name": "雑音と平均化", "parts": ["ガウス雑音"], "reuse_existing": True}, s,
            _client(lambda r: httpx.Response(200, json={}), seen))
    assert not seen and s.latest("sim_wirings") == "w-old"
    assert "保存済みの配線を選んだ" in project_observation("learning.simlab.wiring_save", s.last_status, s.last_body)
