"""検証シミュレーション層（SB層）の審判 — 規則ごとに陽性 1・陰性 1（偽の transcript・DB はモック engine）。"""
from __future__ import annotations

import json
from typing import Any

from uxsim.oracles import simulation
from uxsim.oracles.findings import FindingFactory, layers_for_action
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

sb = simulation.sb

RUN_PATH = "/api/learning/courses/c/simulation/runs"


def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c-x", domain="astro", snapshot="s",
                                  started_at="2026-10-03T00:00:00+00:00", finished_at="2026-10-03T01:00:00+00:00"))


def _trace(body: Any, *, status=200, path=RUN_PATH, method="POST", raw: str | None = None):
    excerpt = raw if raw is not None else json.dumps(body, ensure_ascii=False)
    return HttpTrace(method=method, path=path, status=status, elapsed_ms=10, response_excerpt=excerpt)


def _step(seq, action, *traces, persona="st-1", screen=None):
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="s-sim", action_id=action,
                          screen=screen or ("admin" if action.startswith("admin.") else "learning"),
                          affordance="material.simulation-lab", http=list(traces))


def _run(**over):
    run = {
        "id": "r", "program_id": "p", "wiring_id": None, "status": "succeeded", "observation_claim_ids": [],
        "prediction": [{"symbol": "x", "value": 1.0}],
        "inputs": {"values": [{"symbol": "k", "value": 0.1}], "params": [], "reference": [], "seed": 1},
        "result": {"theory_outputs": [{"symbol": "x", "value": 1.0}], "observed": [{"symbol": "x", "value": 1.0}],
                   "comparison": {"agreement_level": "match", "separation_level": "clear", "reference_level":
                                  "unmeasured", "facts": [], "metrics": {"relative_difference": 0.0}},
                   "labels": {"agreement": "一致", "separation": "分離できる"},
                   "statement": "このモデルと配線のもとでは、理論の出力と模擬観測は一致しています。",
                   "facts": [sb.FACT_NOT_VERIFICATION]},
        "shared": False,
    }
    for k, v in over.items():
        if k in ("result_over",):
            run["result"].update(v)
        else:
            run[k] = v
    return run


def _check(*steps):
    return simulation.check(list(steps), _factory())


def _hyps(found):
    return [f.hypothesis for f in found]


# ---------------------------------------------------------------------------
# A 契約
# ---------------------------------------------------------------------------
def test_a1_run_lists_vs_mapping():
    bad = _run(prediction={"x": 1.0})
    assert any("写像" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    assert _check(_step(1, "learning.simlab.run", _trace({"run": _run()}))) == []


def test_a1_truncated_excerpt_mapping():
    raw = '{"run": {"id": "r", "program_id": "p", "prediction": {"x": 1.0}, "inputs": {"values": [' + "x" * 2100
    assert any("写像" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace(None, raw=raw)))))


def test_a2_level_vocab():
    bad = _run(result_over={"comparison": {"agreement_level": "good", "separation_level": "clear"}})
    assert any("語彙の外" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    assert not any("語彙の外" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": _run()})))))


def test_a3_failed_run_needs_fact():
    bad = _run(status="failed", result_over={"facts": [], "statement": "", "comparison": None})
    good = _run(status="failed", result_over={"facts": ["入力 k が足りません。"], "statement": "", "comparison": None})
    assert any("失敗した実行" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    assert not any("失敗した実行" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": good})))))


def test_a4_422_detail():
    eng = _step(1, "learning.simlab.wiring_save", _trace({"detail": "KeyError: 'slot'"}, status=422))
    english = _step(2, "learning.simlab.wiring_save", _trace({"detail": "invalid wiring"}, status=422))
    empty = _step(3, "learning.simlab.adopt", _trace({"detail": ""}, status=422))
    ok = _step(4, "learning.simlab.wiring_save", _trace({"detail": "部品を一つ以上選んでください。"}, status=422))
    found = _check(eng, english, empty, ok)
    by_seq = {f.evidence.transcript_steps[0]: f.hypothesis for f in found}
    assert "内部の例外文" in by_seq[1]
    assert "日本語" in by_seq[2]
    assert "detail" in by_seq[3]
    assert 4 not in by_seq


def test_a5_generate_shape():
    path = "/api/learning/courses/c/simulation/programs/generate"
    bad = {"program": {"id": "p", "module_key": "m", "source_code": "", "io_contract": {}, "facts": []}, "facts": []}
    good = {"program": {"id": "p", "module_key": "m", "source_code": "def simulate(i, p):\n    return {}",
                        "io_contract": {"inputs": ["k"]}, "facts": []}, "facts": []}
    assert any("入力の契約" in h for h in _hyps(_check(_step(1, "learning.simlab.generate", _trace(bad, path=path)))))
    assert _check(_step(1, "learning.simlab.generate", _trace(good, path=path))) == []


# ---------------------------------------------------------------------------
# B 原則
# ---------------------------------------------------------------------------
def test_b1_closed_world():
    bad = _run(result_over={"facts": ["この結果で理論は検証済みです。"]})
    assert any("検証済み" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    good = _run(result_over={"facts": ["この結果で理論は検証済みです。", sb.FACT_NOT_VERIFICATION]})
    assert not any("検証済み" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": good})))))


def test_b2_prediction_verdict_and_label_keys():
    bad = _run(prediction_match=True, result_over={"labels": {"agreement": "一致", "prediction": "的中"}})
    hyps = _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad}))))
    assert any("予想の当否" in h for h in hyps)
    assert any("段階ラベル" in h for h in hyps)
    hyps_ok = _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": _run()}))))
    assert not any("予想の当否" in h or "段階ラベル" in h for h in hyps_ok)


def test_b3_scoring_vocab():
    word = next(iter(simulation.principle.scoring_vocab()))
    bad = _run(result_over={"facts": [f"予想は{word}でした。", sb.FACT_NOT_VERIFICATION]})
    assert any("点数の語彙" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    assert not any("点数の語彙" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": _run()})))))


def test_b4_admin_personal_keys():
    path = "/api/admin/simulation/runs/r"
    bad = {"run": dict(_run(), shared=True, owner_user_id="u-1")}
    good = {"run": dict(_run(), shared=True, shared_by_label="学生A")}
    assert any("識別子" in h for h in _hyps(_check(_step(1, "admin.simulation.review_shared_run",
                                                          _trace(bad, path=path, method="GET")))))
    assert not any("識別子" in h for h in _hyps(_check(_step(1, "admin.simulation.review_shared_run",
                                                              _trace(good, path=path, method="GET")))))


def _overview(recon: bool):
    return {"modules": [{"module_key": "m1", "label": "定義: L_2", "theory_object": "L_2", "facts": [],
                         "inputs": [{"equation_id": "e", "display_label": "式 (3)"}], "outputs": [],
                         "reconstructed_inputs": recon, "program": None}],
            "parts_catalog": [{"entry_id": "part-1", "name": "ガウス雑音"}], "facts": [], "observation_targets": []}


def test_b5_reconstructed_mark():
    ov = _trace(_overview(True), method="GET", path="/api/learning/courses/c/simulation/overview")
    gen_path = "/api/learning/courses/c/simulation/programs/generate"
    prog = {"id": "p", "module_key": "m1", "source_code": "def simulate(i, p): return {}",
            "io_contract": {"inputs": ["k"]}, "provenance": {"reconstructed_inputs": False, "facts": []}}
    bad = [_step(1, "learning.simlab.open", ov),
           _step(2, "learning.simlab.generate", _trace({"program": dict(prog, facts=[]), "facts": []}, path=gen_path))]
    good = [_step(1, "learning.simlab.open", ov),
            _step(2, "learning.simlab.generate",
                  _trace({"program": dict(prog, facts=[sb.FACT_RECONSTRUCTED_INPUTS]), "facts": []}, path=gen_path))]
    assert any("復元式の印" in h for h in _hyps(_check(*bad)))
    assert not any("復元式の印" in h for h in _hyps(_check(*good)))


def test_b6_revision_catalog():
    ov = _trace(_overview(False), method="GET", path="/api/learning/courses/c/simulation/overview")
    path = "/api/learning/simulation/runs/r/propose-revision"

    def rev(entry_id):
        return _trace({"wiring": None, "proposal": {"changes": [{"op": "add", "slot": "s2", "entry_id": entry_id}],
                                                    "dropped": [], "facts": []}}, path=path)

    assert any("目録に無い" in h for h in _hyps(_check(_step(1, "learning.simlab.open", ov),
                                                         _step(2, "learning.simlab.propose_revision", rev("x-9")))))
    assert not any("目録に無い" in h for h in _hyps(_check(_step(1, "learning.simlab.open", ov),
                                                             _step(2, "learning.simlab.propose_revision", rev("part-1")))))


def test_b7_language():
    bad = _run(result_over={"statement": "The theory output agrees with the simulated observation."})
    assert any("英語" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": bad})))))
    assert not any("英語" in h for h in _hyps(_check(_step(1, "learning.simlab.run", _trace({"run": _run()})))))


def test_b8_internal_ids():
    ov = _overview(False)
    ov["observation_targets"] = [{"claim_id": "c", "label": "claim:（本文を特定できない要素）:（本文を特定できない要素）"}]
    path = "/api/learning/courses/c/simulation/overview"
    assert any("内部 ID" in h for h in _hyps(_check(_step(1, "learning.simlab.open", _trace(ov, method="GET",
                                                                                              path=path)))))
    ok = _overview(False)
    ok["observation_targets"] = [{"claim_id": "c", "label": "磁場の強さは約 100 μG と見積もられる"}]
    assert not any("内部 ID" in h for h in _hyps(_check(_step(1, "learning.simlab.open", _trace(ok, method="GET",
                                                                                                  path=path)))))


def test_b9_counts():
    path = "/api/learning/courses/c/simulation/overview"
    bad = _overview(False)
    bad["facts"] = ["この論文には動かせる理論モジュールが 29 個あります。"]
    good = _overview(False)
    good["facts"] = ["動かす理論モジュールを選んでください。"]
    assert any("件数" in h for h in _hyps(_check(_step(1, "learning.simlab.open", _trace(bad, method="GET", path=path)))))
    assert not any("件数" in h for h in _hyps(_check(_step(1, "learning.simlab.open",
                                                          _trace(good, method="GET", path=path)))))


def test_non_sb_steps_are_ignored_and_layers():
    bad = _run(prediction={"x": 1.0})
    assert _check(_step(1, "learning.chat.ask", _trace({"run": bad}))) == []
    assert layers_for_action("learning.simlab.run", "learning") == ["simulation_bridge_sb", "frontend_learning_ui"]
    assert layers_for_action("admin.simulation.open", "admin") == ["simulation_bridge_sb", "frontend_admin_ui"]


# ---------------------------------------------------------------------------
# E 観測（モック engine — 書き込みは無い）
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _Conn:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        assert sql.lstrip().upper().startswith("SELECT")
        if "FROM interest_traces" in sql:
            return _Result(self.data["traces"])
        if "count(*) FROM simulation_runs" in sql:
            return _Result(scalar=self.data["succeeded"])
        if "FROM llm_usage_events" in sql:
            return _Result(self.data["llm"])
        return _Result(self.data["no_owner"])


class _Engine:
    def __init__(self, **data):
        self.data = data

    def connect(self):
        return _Conn(self.data)


def _obs(engine, steps=None):
    f = _factory()
    return simulation.check_observation(f.meta, f, "", engine, steps=steps)


def test_e_skip_without_db():
    f = _factory()
    assert simulation.check_observation(f.meta, f, "") == []


def test_e1_traces():
    clean = [{"id": "t1", "payload": {"agreement_level": "match", "separation_level": "clear", "shared": False,
                                      "run_id": "r"}}]
    bad = [{"id": "t1", "payload": {"agreement_level": "great", "relative_difference": 0.1}}]
    found = _obs(_Engine(traces=bad, succeeded=1, llm=[], no_owner=[]))
    hyps = _hyps(found)
    assert any("数値の欄" in h for h in hyps) and any("語彙の外" in h for h in hyps)
    assert any("記録されていない" in h for h in _hyps(_obs(_Engine(traces=[], succeeded=2, llm=[], no_owner=[]))))
    assert _obs(_Engine(traces=clean, succeeded=1, llm=[], no_owner=[])) == []


def test_e2_llm_budget():
    steps = [_step(1, "learning.simlab.generate", persona="st-01"), _step(2, "learning.simlab.generate", persona="st-03")]
    over = _obs(_Engine(traces=[], succeeded=0, llm=[{"feature": sb.FEATURE_LEARNER_CODEGEN, "n": 3}], no_owner=[]),
                steps)
    assert any("予算" in h for h in _hyps(over))
    assert all(not any(ch.isdigit() for ch in f.hypothesis) for f in over)  # 数は server_rows にだけ置く
    ok = _obs(_Engine(traces=[], succeeded=0, llm=[{"feature": sb.FEATURE_LEARNER_CODEGEN, "n": 2}], no_owner=[]),
              steps)
    assert ok == []


def test_e3_runs_without_owner():
    bad = _obs(_Engine(traces=[], succeeded=0, llm=[], no_owner=[{"id": "r", "status": "succeeded",
                                                                   "created_at": "x"}]))
    assert any("所有者" in h for h in _hyps(bad))
    assert _obs(_Engine(traces=[], succeeded=0, llm=[], no_owner=[])) == []


def test_b7_english_fragment_in_module_labels_is_reported_for_japanese_persona():
    """40 字で切れた英語の生成文（ピリオド無し）がまとまりの入力・前提に出たら拾う（第 21 周の見逃し）。"""
    from uxsim.oracles import simulation as sim

    assert sim._english_fragment("番号なし: Defines the DCF correction factor as Q =") == "Defines the DCF correction factor as Q"
    assert sim._english_fragment("定義: $\\mathcal{L}_2$") == ""  # TeX は数式として除く
    assert sim._english_fragment("近似: 式 (eq:acf)") == ""
    body = {"modules": [{"module_key": "m2:x", "level": "outer", "label": "定義: $P$",
                         "inputs": [{"equation_id": "e1", "display_label": "番号なし: Defines the debiased polarization fracti"}],
                         "outputs": [], "assumptions": [], "has_equation_text": True, "reconstructed_inputs": False}]}
    step = _step(1, "learning.simlab.open")
    found = sim._check_language(step, body, "ja", FindingFactory(None))
    assert found and "断片" in found[0].hypothesis and "modules.inputs.display_label" in found[0].evidence.quote
    clean = {"modules": [{"module_key": "m2:x", "level": "outer", "label": "定義: $P$",
                          "inputs": [{"equation_id": "e1", "display_label": "式 (12)"}], "outputs": [], "assumptions": [], "reconstructed_inputs": False,
                          "has_equation_text": True}]}
    assert sim._check_language(_step(2, "learning.simlab.open"), clean, "ja", FindingFactory(None)) == []
