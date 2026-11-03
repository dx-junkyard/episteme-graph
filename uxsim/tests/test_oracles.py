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


# --- §18 の拡張 ---------------------------------------------------------------

def _get(seq, action, body, *, path="/api/learning/x", screen="learning", persona="st-1", args=None, status=200):
    st = _step(seq, action, body=body, path=path, screen=screen, persona=persona, status=status)
    st.http[0].method = "GET"
    if args:
        st.args = args
    return st


def test_principle_internal_id_new_prefixes():
    for token in ("eq_tex_4", "fig_3", "sec_runin_2", "ev_abc1", "m2|outer"):
        steps = [_get(1, "learning.element.context", {"facts": [f"関係 {token} を見る"]})]
        assert any("内部 ID" in f.hypothesis for f in principle.check(steps, _factory())), token
    ok = [_get(1, "learning.element.context", {"facts": ["式 (12) は z = 0.5 で成り立つ"]})]
    assert not any("内部 ID" in f.hypothesis for f in principle.check(ok, _factory()))


def test_principle_language_mismatch_ja_default():
    steps = [_get(1, "learning.personal_network.nearby", {"facts": ["This node is derived from the theory basis."]})]
    assert any("英語" in f.hypothesis for f in principle.check(steps, _factory()))
    ok = [_get(1, "learning.personal_network.nearby", {"facts": ["式 (12) は理論の基礎から導かれます。"],
                                                        "label": "ΛCDM"})]
    assert not any("英語" in f.hypothesis for f in principle.check(ok, _factory()))


def test_principle_language_english_persona(monkeypatch):
    monkeypatch.setattr(principle, "_persona_language", lambda d, p: "en")
    steps = [_get(1, "learning.atlas.view", {"facts": ["このコーパスの中では検証記録がありません。"]})]
    assert any("言語不一致" in f.hypothesis for f in principle.check(steps, _factory()))


def test_principle_scoring_vocab_read_from_product_guardrails():
    vocab = principle.scoring_vocab()
    assert "正答率" in vocab and "不正解" in vocab
    steps = [_get(1, "learning.reconstruction.submit", {"statements": ["あなたの答えは不正解です"]})]
    assert any("採点" in f.hypothesis for f in principle.check(steps, _factory()))
    quoted = [_get(1, "learning.reconstruction.submit", {"quote": "68% CL で正解に近い"})]
    assert not any("採点" in f.hypothesis for f in principle.check(quoted, _factory()))
    chat = [_get(1, "learning.chat.ask", {"answer": "1% 精度", "facts": ["一致度は高めです"]},
                 args={"cycle_mode": "diff"})]
    assert any("採点" in f.hypothesis for f in principle.check(chat, _factory()))


def test_principle_teacher_structure_forbidden_keys():
    bad = [_get(1, "admin.graph_review.open", {"modules": [{"label": "定義", "member_count": 3,
                                                             "id": "m2|outer|ops=x"}]},
                path="/api/admin/documents/d/theory-modules", screen="admin")]
    found = principle.check(bad, _factory())
    assert any("教員向け" in f.hypothesis and "member_count" in f.evidence.quote for f in found)
    ok = [_get(1, "admin.graph_review.open", {"modules": [{"label": "定義"}]},
               path="/api/admin/documents/d/paper-layer", screen="admin")]
    assert principle.check(ok, _factory()) == []


def test_contract_graph_chat_stance_prefix():
    bad = [_step(1, "admin.graph_review.chat", screen="admin",
                 body={"reply": "AIの読み（未確認）：この論文は…", "stance_label": "AIの読み（未確認）"})]
    ok = [_step(1, "admin.graph_review.chat", screen="admin",
                body={"reply": "この論文は…", "stance_label": "AIの読み（未確認）"})]
    assert any("重複" in f.hypothesis for f in contract.check(bad, _factory()))
    assert contract.check(ok, _factory()) == []


def test_contract_hop_degraded_is_fact_not_violation():
    steps = [_get(1, "learning.component.context_hop", {"instance": {"component": {"label": "x"}}, "graph": None})]
    found = contract.check(steps, _factory())
    assert len(found) == 1 and found[0].hypothesis.startswith("縮退の事実") and found[0].severity_label == "confused"


def test_contract_symbol_lookup_outside_course():
    course = _get(1, "learning.course.open", {"course": {"data": {"sources": [{"document_id": "doc-a"}]}}})
    inside = _get(2, "learning.symbol.lookup", {"definition": {"document_id": "doc-a"}})
    outside = _get(3, "learning.symbol.lookup", {"definition": {"document_id": "doc-b"}})
    assert contract.check([course, inside], _factory()) == []
    assert any("別論文" in f.hypothesis for f in contract.check([course, outside], _factory()))
    assert contract.check([outside], _factory()) == []  # sources を知らなければ判定しない


def test_behavior_trace_break_grouped_by_cause():
    steps = [_get(1, "learning.element.context", {"x": 1}),
             _get(2, "learning.component.context_hop", {"detail": "missing"}, status=404),
             _get(3, "learning.element.context", {"x": 1}, persona="st-2"),
             _get(4, "learning.component.context_hop", {"detail": "missing"}, status=404, persona="st-2")]
    found, _ = behavior.check(steps, _factory())
    breaks = [f for f in found if "辿り" in f.hypothesis]
    assert len({f.fingerprint for f in breaks}) == 1 and breaks[0].severity_label == "blocked"


def test_principle_enumeration_ten_is_not_score():
    """第 15 周の誤検出: 「次の 3 点」「1点」は列挙。点数は採点の文脈だけで当てる。"""
    quiet = [_step(i, "learning.chat.ask", body={k: t}) for i, (k, t) in enumerate(
        [("answer", "要点は次の 3点 です"), ("answer", "1点だけ補足します"), ("facts", ["注意は 1点 あります"])], 1)]
    assert not any("数値" in f.hypothesis for f in principle.check(quiet, _factory()))
    for t in ("10点満点で 7点満点", "評価は 7/10 です", "点数は 80"):
        found = principle.check([_step(1, "learning.chat.ask", body={"answer": t})], _factory())
        assert any("数値" in f.hypothesis for f in found), t


def test_principle_delimited_tex_label_is_not_raw():
    ok = [_get(1, "learning.chunk.claim_refs",
               {"label": r"In an equation of this paper, $d$ depends on $\chi_{\mathrm{eff}}$ and $\frac{a}{b}$"}),
          _get(2, "learning.chunk.claim_refs", {"label": r"cut: $d$ depends on $\chi_{\mathrm{…"})]
    assert not any("生の TeX" in f.hypothesis for f in principle.check(ok, _factory()))
    for lab in (r"\frac{a}{b} の比", r"残骸 \( x \) がある"):
        found = principle.check([_get(1, "learning.chunk.claim_refs", {"label": lab})], _factory())
        assert any("生の TeX" in f.hypothesis for f in found), lab


def test_dialogue_viewbox_and_lecture_contract_not_findings():
    from uxsim.oracles import dialogue
    steps = [
        _get(1, "learning.atlas.open", {"viewBox": 100, "w": 3}),
        _get(2, "learning.lecture.audio_status", {"ready_chunks": 2, "total_chunks": 3, "total_duration_ms": 9},
             path="/api/learning/lecture/courses/c/topics/t/audio-status"),
        _get(3, "learning.lecture.sequence", {"duration_ms": 5, "total_slides": 4},
             path="/api/learning/lecture/courses/c/topics/t/sequence"),
    ]
    assert not any("数値の項目" in f.hypothesis for f in dialogue.check(steps, _factory()))
    notes = dialogue.ui_contract_notes(steps)
    assert set(notes) == {"learning.lecture.audio_status", "learning.lecture.sequence"}
    # 真の検出（confidence）は残る
    bad = [_get(1, "learning.element.context", {"confidence": 0.8})]
    assert any("数値の項目" in f.hypothesis for f in dialogue.check(bad, _factory()))
