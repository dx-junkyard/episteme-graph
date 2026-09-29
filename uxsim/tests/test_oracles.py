"""審判 A・B・C の決定論部分（偽の transcript から発見が出ること）。"""
from __future__ import annotations

import json

from uxsim.oracles import behavior, contract, principle
from uxsim.oracles.findings import FindingFactory
from uxsim.oracles.run_all import run_oracles
from uxsim.schema import HttpTrace, RunMeta, ThinkAloud, TranscriptStep


def _step(seq, action, *, status=200, body=None, observation="", screen="learning", friction="none", path=None,
          persona="st-1", error=""):
    excerpt = json.dumps(body, ensure_ascii=False) if body is not None else ""
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="s-x", action_id=action, screen=screen,
                          affordance="composer.send", observation=observation,
                          think=ThinkAloud(friction=friction),
                          http=[HttpTrace(method="POST", path=path or "/api/learning/courses/c/topics/t/chat",
                                          status=status, elapsed_ms=10, response_excerpt=excerpt, error=error)])


def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c-x", domain="astro", snapshot="s"))


def test_contract_500_and_connection():
    steps = [_step(1, "learning.chat.ask", status=500, body={"detail": "Internal"}),
             _step(2, "learning.chat.ask", status=None, error="ConnectError: x")]
    found = contract.check(steps, _factory())
    assert {f.severity_label for f in found} == {"blocked"}
    assert len(found) == 2


def test_contract_skips_precondition():
    steps = [_step(1, "learning.symbol.lookup", status=None, error="precondition:course_id")]
    assert contract.check(steps, _factory()) == []


def test_contract_429_hidden():
    hidden = [_step(1, "learning.chat.ask", status=429, body={"detail": "エラー"}, observation="エラーが表示された")]
    shown = [_step(1, "learning.chat.ask", status=429, body={"detail": "本日の上限に達しました"},
                   observation="本日の上限に達しました")]
    assert any("429" in f.hypothesis for f in contract.check(hidden, _factory()))
    assert contract.check(shown, _factory()) == []


def test_contract_stance_shape():
    bad = [_step(1, "learning.chat.ask", body={"answer": "x", "stance": {"stance": "tutor", "source": "inferred",
                                                                         "label": "l", "confidence": 0.9}})]
    ok = [_step(1, "learning.chat.ask", body={"answer": "x", "stance": {"stance": "tutor", "source": "inferred",
                                                                        "label": "l"}})]
    assert any("stance" in f.hypothesis for f in contract.check(bad, _factory()))
    assert contract.check(ok, _factory()) == []


def test_principle_denylist_internal_id_numbers_control():
    steps = [
        _step(1, "learning.chat.ask", body={"answer": "設定は ADMIN_PASSWORD で行います"}),
        _step(2, "learning.chat.ask", body={"answer": "式 eq_op_12 を見てください"}),
        _step(3, "learning.chat.ask", body={"answer": "あなたの正答率は 80% です"}),
        _step(4, "learning.chat.ask", body={"answer": "色付き\x1b[0m文字"}),
        _step(5, "learning.chat.ask", body={"answer": "式 (12) と 2026年 と arXiv 2407.01221 の話"}),
        _step(6, "admin.materials.list", screen="admin", body={"answer": "ADMIN_PASSWORD"}),
        # 論文の内容の百分率（68% CL・1% 精度）は製品の指標ではない（第 11 周の誤検出）
        _step(7, "learning.chat.ask", body={"answer": "68% 信頼区間で w0 が制約され、距離は 1% 精度です"}),
    ]
    found = principle.check(steps, _factory())
    by_seq = {f.evidence.transcript_steps[0]: f.hypothesis for f in found}
    assert "禁止語彙" in by_seq[1]
    assert "内部 ID" in by_seq[2]
    assert "数値" in by_seq[3]
    assert "制御文字" in by_seq[4]
    assert 5 not in by_seq and 6 not in by_seq  # 除外パターン・管理画面は対象外
    assert 7 not in by_seq  # 裸の百分率は当てない
    assert all(f.oracle == "B" and f.severity_label == "principle" for f in found)


def test_principle_reads_truncated_excerpt():
    raw = '{"answer": "uuid 123e4567-e89b-12d3-a456-426614174000 が見える", "sources": [{"quote": "' + "x" * 3000
    step = _step(1, "learning.chat.ask")
    step.http[0].response_excerpt = raw[:2000]
    assert any("内部 ID" in f.hypothesis for f in principle.check([step], _factory()))


def test_behavior_prefilter():
    steps = [_step(i, "learning.symbol.lookup", status=404, path="/api/x") for i in range(1, 4)]
    steps.append(_step(4, "learning.chat.ask", friction="gave_up"))
    steps.append(TranscriptStep(seq=5, persona_id="st-1", action_id="unsupported:learning.fly"))
    found, notes = behavior.check(steps, _factory())
    reasons = [f.hypothesis for f in found]
    assert any("続けて失敗" in r for r in reasons)
    assert any("諦めた" in r for r in reasons)
    assert notes["unsupported_actions"] == ["learning.fly"]


def test_run_all_writes_findings(tmp_path):
    meta = RunMeta(run_id="r1", campaign_id="c-x", domain="astro", snapshot="s")
    (tmp_path / "meta.json").write_text(meta.model_dump_json())
    steps = [_step(1, "learning.chat.ask", status=500, body={"detail": "x"}),
             _step(2, "learning.chat.ask", status=500, body={"detail": "x"})]
    (tmp_path / "transcript.jsonl").write_text("\n".join(s.model_dump_json() for s in steps) + "\n")
    findings = run_oracles(tmp_path, database_url="")
    assert len(findings) == 1  # 同じ指紋は束ねる
    assert findings[0].evidence.transcript_steps == [1, 2]
    notes = json.loads((tmp_path / "oracle_notes.json").read_text())
    assert "未実施" in notes["observation"]["note"]
