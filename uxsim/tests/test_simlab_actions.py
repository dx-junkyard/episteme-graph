"""SB層（検証シミュレーション）の行為が正しい製品ルート・本文を呼び、画面を投影すること（MockTransport・外部呼び出しなし）。
契約 = docs/features/simulation_bridge_design.md §14.3 / 行為 = uxsim/actions/registry.py の _SIMLAB / _ADMIN_SIM。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.runner.actions_exec import _HANDLERS, execute
from uxsim.runner.client import EpistemeClient
from uxsim.runner.simlab_exec import find_module, parse_value
from uxsim.runner.state import PersonaSession, project_observation

OVERVIEW = {
    "course_id": "c1", "document_id": "doc-1", "document_title": "磁場の論文", "sandbox_configured": True,
    "documents": [{"document_id": "doc-1", "title": "磁場の論文"}],
    "available": True, "facts": ["論文全体をまとめる手順は、モジュールではなく結果の吸い込み口として別に示します。"],
    "parts_catalog": [
        {"entry_id": "e-amp", "version_no": 1, "entry_type": "instrument_model", "entry_type_label": "装置モデル",
         "name": "線形増幅器", "summary": "", "params": [{"name": "gain", "default": 10.0, "unit": ""}]},
        {"entry_id": "e-gauss", "version_no": 1, "entry_type": "noise_source", "entry_type_label": "ノイズ源",
         "name": "ガウス雑音", "summary": "", "params": [{"name": "sigma", "default": 0.1, "unit": ""}], "noise_kind": "gaussian"},
        {"entry_id": "e-avg", "version_no": 1, "entry_type": "signal_processing", "entry_type_label": "信号処理",
         "name": "平均化（N 回）", "summary": "", "params": [{"name": "n", "default": 10, "unit": "回"}]},
    ],
    "modules": [
        {"module_key": "m2:aaa", "level": "outer", "label": "定義を適用・定義・制約を適用: $P_\\mathrm{B}$",
         "theory_object": "$P_\\mathrm{B}$", "inputs": [{"equation_id": "eq_1", "display_label": "式 (eq:b_correction)"}],
         "outputs": [{"equation_id": "eq_2", "display_label": "式 (equlibrium_pressure)"}],
         "assumptions": [{"text": "等方乱流", "origin_label": "論文"}], "has_equation_text": True,
         "reconstructed_inputs": True, "program": None},
        {"module_key": "m2:bbb", "level": "inner", "label": "定義: $C_{\\mathrm{eff}}$", "theory_object": "$C_{\\mathrm{eff}}$",
         "inputs": [], "outputs": [], "assumptions": [], "has_equation_text": True, "reconstructed_inputs": False,
         "program": None},
    ],
    "programs": [], "wirings": [], "runs": [],
    "observation_targets": [{"claim_id": "clm-9", "label": "観測された偏光角分散は 10 度以下である。"}],
}
PROGRAM = {"id": "p1", "module_key": "m2:aaa", "review_status": "candidate", "source_code": "def simulate(inputs, params):\n    return {'P_B': inputs['B']**2}\n",
           "io_contract": {"inputs": ["B"], "outputs": ["P_B"], "params": []},
           "provenance": {"reconstructed_inputs": True, "facts": ["入力の式に PDF から復元された式が含まれています。元の式と一致するかは確認されていません。"], "ast_ok": True, "ast_reasons": []},
           "facts": ["入力の式に PDF から復元された式が含まれています。元の式と一致するかは確認されていません。"]}
RUN = {"id": "r1", "program_id": "p1", "wiring_id": "w1", "status": "succeeded", "observation_claim_ids": ["clm-9"],
       "prediction": [{"symbol": "P_B", "value": 4.0}],
       "inputs": {"values": [{"symbol": "B", "value": 2.0}], "params": [], "reference": [], "seed": None},
       "result": {"theory_outputs": [{"symbol": "P_B", "value": 4.0}], "observed": [{"symbol": "P_B", "mean": 4.1, "std": 0.2}],
                  "comparison": {"agreement_level": "match", "separation_level": "clear", "reference_level": "unmeasured",
                                 "facts": [], "metrics": {"relative_difference": 0.025, "separation_ratio": 3.0,
                                                          "reference_relative_difference": None, "by_symbol": []}},
                  "labels": {"agreement": "一致", "separation": "はっきり分かれる"}, "statement": "このモデルと配線のもとでは、理論の出力と模擬観測はおおむね一致しました。",
                  "facts": ["シミュレーションの一致は検証ではなく、分析の妥当性の候補にとどまります。"]},
       "shared": False, "created_at": "2026-10-03T00:00:00Z"}


def _client(handler, seen):
    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _session(**kw):
    s = PersonaSession(persona_id="p1", course_id="c1", topic_id="t2")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8")) if request.content else {}


def test_registry_has_all_sb_actions_with_handlers():
    ids = [a for a in REGISTRY if a.startswith(("learning.simlab.", "admin.simulation."))]
    assert len(ids) == 14  # 第 22 周: ledger_candidate_dismiss
    for aid in ids:
        assert aid in _HANDLERS, aid
        assert REGISTRY[aid].affordance in ("material.simulation-lab", "materials.row-simulation", "simulation.run",
                                            "simulation.ledger-candidate", "simulation.ledger-candidate-decide")
    assert REGISTRY["learning.simlab.generate"].llm_cost == "product"
    assert REGISTRY["learning.simlab.propose_revision"].llm_cost == "product"


def test_open_remembers_modules_parts_targets_and_projects_panel():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json=OVERVIEW), seen)
    s = _session()
    execute("learning.simlab.open", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/simulation/overview"
    assert seen[-1].url.params.get("topic_id") == "t2"  # 表示中トピックを焦点論文の手がかりとして渡す
    assert s.scratch["sim_document_id"] == "doc-1" and len(s.scratch["sim_modules"]) == 2
    assert s.scratch["sim_targets"][0]["claim_id"] == "clm-9" and len(s.scratch["sim_parts"]) == 3
    obs = project_observation("learning.simlab.open", s.last_status, s.last_body)
    assert "理論モジュールを選ぶ" in obs and "式 (eq:b_correction) → 式 (equlibrium_pressure)" in obs
    assert "PDF から復元された式" in obs and "部品 — 装置モデル: 線形増幅器" in obs
    assert "シミュレーションの一致は検証ではなく" in obs and "成績評価には使用されません" in obs


def test_select_module_by_number_and_by_label_and_miss():
    assert find_module(OVERVIEW["modules"], "2")["module_key"] == "m2:bbb"
    assert find_module(OVERVIEW["modules"], "P_B")["module_key"] == "m2:aaa"
    assert find_module(OVERVIEW["modules"], "C_eff")["module_key"] == "m2:bbb"
    assert find_module(OVERVIEW["modules"], "存在しない") is None
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json=OVERVIEW), seen)
    s = _session()
    execute("learning.simlab.select_module", {"module_hint": "P_B"}, s, client)
    assert s.scratch["sim_module_key"] == "m2:aaa"
    obs = project_observation("learning.simlab.select_module", s.last_status, s.last_body)
    assert "選んだ理論モジュール" in obs and "前提: 等方乱流" in obs and "まだ無い" in obs
    execute("learning.simlab.select_module", {"module_hint": "存在しない"}, s, client)
    assert "合う理論モジュールが一覧に無い" in project_observation("learning.simlab.select_module", s.last_status, s.last_body)
    assert s.scratch["sim_module_key"] == "m2:aaa"  # 外れた指定で選択は変わらない


def test_generate_edit_adopt_chain_uses_latest_program():
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/programs/generate"):
            return httpx.Response(200, json={"program": PROGRAM, "facts": PROGRAM["facts"]})
        if r.url.path.endswith("/programs/p1/edit"):
            return httpx.Response(200, json={"program": {**PROGRAM, "id": "p2", "provenance": {**PROGRAM["provenance"], "facts": ["あなたが書き直したコードです。"]}}, "facts": ["あなたが書き直したコードです。"]})
        if r.url.path.endswith("/programs/p2/adopt"):
            return httpx.Response(200, json={"program": {**PROGRAM, "id": "p2", "review_status": "confirmed"}})
        return httpx.Response(200, json=OVERVIEW)

    client = _client(handler, seen)
    s = _session()
    # 選んでいなければ HTTP を出さない
    traces = execute("learning.simlab.generate", {}, s, client)
    assert traces[0].error == "precondition:module_key" and not seen
    execute("learning.simlab.select_module", {"module_hint": "1"}, s, client)
    execute("learning.simlab.generate", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/simulation/programs/generate"
    assert _body(seen[-1]) == {"module_key": "m2:aaa", "document_id": "doc-1", "topic_id": "t2"}
    obs = project_observation("learning.simlab.generate", s.last_status, s.last_body)
    assert "コード（候補）" in obs and "def simulate" in obs and "復元された式" in obs
    execute("learning.simlab.edit", {"source_code": "def simulate(inputs, params):\n    return {'P_B': 2 * inputs['B']}\n"}, s, client)
    assert seen[-1].url.path == "/api/learning/simulation/programs/p1/edit"
    assert s.latest("sim_programs") == "p2"
    execute("learning.simlab.adopt", {}, s, client)
    assert seen[-1].url.path == "/api/learning/simulation/programs/p2/adopt"
    assert s.scratch["sim_programs_dto"][0]["review_status"] == "confirmed"
    assert "採用済み" in project_observation("learning.simlab.adopt", s.last_status, s.last_body)


def test_wiring_save_maps_part_names_to_catalog_and_chains_slots():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={"wiring": {"id": "w1", "name": "雑音と平均", "parts": [
        {"slot": "s1", "entry_id": "e-gauss", "name": "ガウス雑音", "params": [{"name": "sigma", "value": 0.2}]},
        {"slot": "s2", "entry_id": "e-avg", "name": "平均化（N 回）", "params": []}],
        "edges": [{"from_slot": "s1", "to_slot": "s2"}], "origin": "learner", "review_status": "confirmed"}}), seen)
    s = _session()
    traces = execute("learning.simlab.wiring_save", {"name": "x", "parts": ["ガウス雑音"]}, s, client)
    assert traces[0].error == "precondition:parts_catalog" and not seen
    s.scratch["sim_parts"] = OVERVIEW["parts_catalog"]
    s.scratch["sim_document_id"] = "doc-1"
    execute("learning.simlab.wiring_save", {"name": "雑音と平均", "parts": ["ガウス雑音", "平均化"],
                                           "params": {"ガウス雑音": {"sigma": "0.2"}}}, s, client)
    body = _body(seen[-1])
    assert seen[-1].url.path == "/api/learning/courses/c1/simulation/wirings"
    assert [p["entry_id"] for p in body["parts"]] == ["e-gauss", "e-avg"]
    assert body["parts"][0]["params"] == {"sigma": 0.2} and body["edges"] == [{"from_slot": "s1", "to_slot": "s2"}]
    assert s.latest("sim_wirings") == "w1"
    assert "ガウス雑音（sigma=0.2） → 平均化（N 回）" in project_observation("learning.simlab.wiring_save", s.last_status, s.last_body)
    # 目録に無い名前は HTTP を出さず画面に目録を見せる
    traces = execute("learning.simlab.wiring_save", {"name": "y", "parts": ["ロックイン"]}, s, client)
    assert traces[-1].error == "precondition:part_name"
    assert "という名前の部品は無い" in project_observation("learning.simlab.wiring_save", s.last_status, s.last_body)


def test_parse_value_accepts_scalars_vectors_and_comma_strings():
    assert parse_value("3") == 3.0 and parse_value(2) == 2.0
    assert parse_value("0.1, 0.2, 0.3") == [0.1, 0.2, 0.3] and parse_value([1, "2"]) == [1.0, 2.0]
    assert parse_value("abc") == "abc"  # 製品側の 422 を見せるためそのまま


def test_predict_then_run_sends_prediction_values_claim_and_wiring():
    seen: list[httpx.Request] = []

    overview = {**OVERVIEW, "programs": [{"id": "p2", "review_status": "confirmed"}, {"id": "p1", "review_status": "candidate"}],
                "wirings": [{"id": "w1", "name": "雑音と平均"}]}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/simulation/runs") and r.method == "POST":
            return httpx.Response(200, json={"run": RUN})
        return httpx.Response(200, json=overview)

    client = _client(handler, seen)
    s = _session()
    execute("learning.simlab.open", {}, s, client)  # 予想の前に画面を開いている（programs / wirings はサーバの値で更新される）
    execute("learning.simlab.predict", {"prediction": {"P_B": "4"}}, s, client)
    assert s.scratch["sim_prediction"] == {"P_B": 4.0}
    assert "予想を書いてから条件を決める: P_B=4" in project_observation("learning.simlab.predict", s.last_status, s.last_body)
    execute("learning.simlab.run", {"values": {"B": "2", "k": "0.1, 0.2, 0.3"}, "claim_hint": "偏光角分散", "seed": "7"}, s, client)
    body = _body(seen[-1])
    assert body["program_id"] == "p2" and body["wiring_id"] == "w1" and body["seed"] == 7
    assert body["values"] == {"B": 2.0, "k": [0.1, 0.2, 0.3]}
    assert body["prediction"] == [{"symbol": "P_B", "value": 4.0}] and body["observation_claim_ids"] == ["clm-9"]
    assert s.latest("sim_runs") == "r1"
    obs = project_observation("learning.simlab.run", s.last_status, s.last_body)
    assert "実行できました" in obs and "P_B | 4 | 4 | 4.1 ± 0.2" in obs and "\n一致 / はっきり分かれる\n" in obs and "段階:" not in obs  # 画面と同じ見出しなし（軸名はサーバのラベル値に含まれる）
    assert "相対差: 0.025" in obs and "分析の妥当性の候補" in obs
    # wiring="none" なら配線なし
    execute("learning.simlab.run", {"values": {"B": 2}, "wiring": "none"}, s, client)
    assert "wiring_id" not in _body(seen[-1])


def test_share_and_propose_revision_target_latest_run():
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/share"):
            return httpx.Response(200, json={"run": {**RUN, "shared": True}, "facts": ["この実行記録を担当教員と共有しました。", "観測系の主張の台帳に、確かめた結果の候補として記帳しました。確定するのは教員です。"]})
        if r.url.path.endswith("/propose-revision"):
            return httpx.Response(200, json={"wiring": None, "proposal": {"changes": [{"op": "add", "slot": "s3", "entry_id": "e-avg", "param": "n", "value": 100, "reason": "ばらつきを下げる"}], "dropped": [], "facts": []}})
        return httpx.Response(200, json=OVERVIEW)

    client = _client(handler, seen)
    s = _session()
    traces = execute("learning.simlab.share", {}, s, client)
    assert traces[0].error == "precondition:run_id"
    s.scratch["sim_runs_dto"] = [RUN]
    s.scratch["sim_targets"] = OVERVIEW["observation_targets"]
    execute("learning.simlab.share", {"claim_hint": "偏光角"}, s, client)
    assert seen[-1].url.path == "/api/learning/simulation/runs/r1/share" and _body(seen[-1]) == {"claim_ids": ["clm-9"]}
    obs = project_observation("learning.simlab.share", s.last_status, s.last_body)
    assert "共有済み" in obs and "確定するのは教員です" in obs
    execute("learning.simlab.propose_revision", {}, s, client)
    assert seen[-1].url.path == "/api/learning/simulation/runs/r1/propose-revision"
    obs = project_observation("learning.simlab.propose_revision", s.last_status, s.last_body)
    assert "AI の提案（未確認）" in obs and "ばらつきを下げる" in obs and "新しい配線は作られなかった" in obs


def test_teacher_open_review_and_ledger_confirm_flow():
    seen: list[httpx.Request] = []
    shared_run = {**RUN, "shared": True, "shared_by_label": "佐伯 悠斗"}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/documents/doc-1/overview"):
            return httpx.Response(200, json={"document_id": "doc-1", "document_title": "磁場の論文", "facts": [],
                                             "modules": [], "programs": [], "wirings": [], "runs": [RUN, shared_run], "parts_catalog": []})
        if r.url.path == "/api/admin/simulation/runs/r1":
            return httpx.Response(200, json={"run": shared_run})
        if r.url.path == "/api/admin/doubt/ledger/claim/clm-9" and r.method == "GET":
            return httpx.Response(200, json={"simulation_candidates": [{"candidate_id": "cand-1", "run_id": "r1", "status": "candidate"}]})
        if r.url.path.endswith("/simulation-candidates/cand-1/confirm"):
            return httpx.Response(200, json={"status": "confirmed"})
        return httpx.Response(404, json={"detail": "nf"})

    client = _client(handler, seen)
    s = PersonaSession(persona_id="te", role="TEACHER")
    traces = execute("admin.simulation.open", {}, s, client)
    assert traces[0].error == "precondition:document_id"
    s.remember("documents", "doc-1")
    execute("admin.simulation.open", {}, s, client)
    assert seen[-1].url.path == "/api/admin/simulation/documents/doc-1/overview"
    obs = project_observation("admin.simulation.open", s.last_status, s.last_body)
    assert "学習者が共有 佐伯 悠斗" in obs and s.latest("sim_runs") == "r1"
    execute("admin.simulation.review_shared_run", {}, s, client)
    # 画面は共有 run を選ぶと主張の台帳も読む（区画 4「台帳の候補」）
    assert [r.url.path for r in seen[-2:]] == ["/api/admin/simulation/runs/r1", "/api/admin/doubt/ledger/claim/clm-9"]
    obs = project_observation("admin.simulation.review_shared_run", s.last_status, s.last_body)
    assert "台帳の候補（学習者が共有した実行）" in obs and "［確定する］［見送る理由（必須）を書いてから見送る］" in obs
    execute("admin.simulation.ledger_candidate_confirm", {}, s, client)
    assert [r.url.path for r in seen[-2:]] == ["/api/admin/doubt/ledger/claim/clm-9",
                                               "/api/admin/doubt/ledger/claim/clm-9/simulation-candidates/cand-1/confirm"]
    assert seen[-1].method == "POST"


def test_ledger_confirm_without_candidate_is_precondition_not_http():
    seen: list[httpx.Request] = []
    client = _client(lambda r: httpx.Response(200, json={"simulation_candidates": []}), seen)
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch["sim_shared_runs_dto"] = [{"id": "r9", "observation_claim_ids": ["clm-1"], "shared": True}]
    traces = execute("admin.simulation.ledger_candidate_confirm", {}, s, client)
    assert len(seen) == 1 and seen[0].method == "GET"
    assert traces[-1].error == "precondition:candidate_id"
    assert "候補は記帳されていない" in project_observation("admin.simulation.ledger_candidate_confirm", s.last_status, s.last_body)
