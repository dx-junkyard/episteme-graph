"""対話の往復をまたぐ審判（dialogue.py）・原因で束ねる審判 C・応答の要約（digest）。"""
from __future__ import annotations

import json

from uxsim.oracles import behavior, dialogue
from uxsim.oracles.findings import FindingFactory, dedupe
from uxsim.runner.digest import chat_record_from_excerpt, response_digest
from uxsim.schema import HttpTrace, RunMeta, ThinkAloud, TranscriptStep

CHAT = "/api/learning/courses/c/topics/t/chat"


def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c-x", domain="astro", snapshot="s"))


def _chat_step(seq, body, *, message="Q", persona="st-1", action="learning.chat.ask", path=CHAT, digest=True,
               session=1):
    excerpt = json.dumps(body, ensure_ascii=False)
    trace = HttpTrace(method="POST", path=path, status=200, elapsed_ms=10, response_excerpt=excerpt[:2000],
                      digest=response_digest(body) if digest else {})
    return TranscriptStep(seq=seq, persona_id=persona, session=session, scenario_id="s-x", action_id=action,
                          screen="learning", affordance="composer.send", args={"message": message}, http=[trace])


def _src(index, chunk, title="論文A", **extra):
    return {"index": index, "chunk_id": chunk, "source_title": title, "tier": "source", "score": 0.8, **extra}


# ----------------------------------------------------------------------------
# a. 出典番号の振り直し
# ----------------------------------------------------------------------------

def test_renumbering_flagged_once_per_persona():
    steps = [
        _chat_step(1, {"answer": "磁場は弓状 [出典1]、圧力は [出典2]", "sources": [_src(1, "ch-a"), _src(2, "ch-b")]}),
        _chat_step(2, {"answer": "次の話 [出典1]", "sources": [_src(1, "ch-c"), _src(2, "ch-a")]}, message="Q2"),
        _chat_step(3, {"answer": "さらに [出典1]", "sources": [_src(1, "ch-b"), _src(2, "ch-c")]}, message="Q3"),
    ]
    found = [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_RENUMBER]
    assert len(found) == 1
    f = found[0]
    assert f.oracle == "A" and f.evidence.transcript_steps == [1, 2]
    assert "[出典1]" in f.evidence.quote and "ch-a" in f.evidence.quote
    assert f.evidence.server_rows["renumbered[st-1]"][0] == {"chunk_id": "ch-a", "before": 1, "after": 2}


def test_renumbering_needs_markers_in_earlier_answer_and_same_conversation():
    no_markers = [
        _chat_step(1, {"answer": "出典の番号なし", "sources": [_src(1, "ch-a"), _src(2, "ch-b")]}),
        _chat_step(2, {"answer": "x [出典1]", "sources": [_src(1, "ch-b"), _src(2, "ch-a")]}, message="Q2"),
    ]
    other_topic = [
        _chat_step(1, {"answer": "a [出典1]", "sources": [_src(1, "ch-a")]}),
        _chat_step(2, {"answer": "b [出典2]", "sources": [_src(1, "ch-z"), _src(2, "ch-a")]}, message="Q2",
                   path="/api/learning/courses/c/topics/t2/chat"),
    ]
    stable = [
        _chat_step(1, {"answer": "a [出典1]", "sources": [_src(1, "ch-a")]}),
        _chat_step(2, {"answer": "b [出典1]", "sources": [_src(1, "ch-a"), _src(2, "ch-b")]}, message="Q2"),
    ]
    for steps in (no_markers, other_topic, stable):
        assert not [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_RENUMBER]


def test_renumbering_skipped_when_sources_not_in_excerpt():
    long = "x" * 2500 + " [出典1]"
    steps = [_chat_step(1, {"answer": long, "sources": [_src(1, "ch-a")]}, digest=False),
             _chat_step(2, {"answer": long + "y", "sources": [_src(1, "ch-b"), _src(2, "ch-a")]}, message="Q2",
                        digest=False)]
    assert not [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_RENUMBER]


# ----------------------------------------------------------------------------
# b. 区別できない出典
# ----------------------------------------------------------------------------

def test_indistinct_sources():
    same = _chat_step(1, {"answer": "a", "sources": [_src(i, f"ch-{i}", meta="2605.26810v1.pdf") for i in (1, 2, 3)]})
    distinct = _chat_step(2, {"answer": "a", "sources": [_src(i, f"ch-{i}", meta=f"§{i}") for i in (1, 2, 3)]})
    two = _chat_step(3, {"answer": "a", "sources": [_src(i, f"ch-{i}") for i in (1, 2)]})
    found = [f for f in dialogue.check([same], _factory()) if f.hypothesis == dialogue.HYP_INDISTINCT]
    assert len(found) == 1 and "論文A" in found[0].evidence.quote and found[0].severity_label == "confused"
    assert not [f for f in dialogue.check([distinct, two], _factory()) if f.hypothesis == dialogue.HYP_INDISTINCT]


# ----------------------------------------------------------------------------
# c. 古い回答の再送
# ----------------------------------------------------------------------------

def test_stale_answer_for_different_question():
    body = {"answer": "同じ回答の本文です", "sources": []}
    steps = [_chat_step(1, body, message="Q1"), _chat_step(2, body, message="Q2")]
    found = [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_STALE]
    assert len(found) == 1 and found[0].evidence.transcript_steps == [1, 2]


def test_stale_answer_not_flagged_for_same_question_degraded_or_different_answer():
    body = {"answer": "同じ回答", "sources": []}
    degraded = {"answer": "いまは回答できません", "degraded": True}
    cases = [
        [_chat_step(1, body, message="Q1"), _chat_step(2, body, message="Q1")],
        [_chat_step(1, degraded, message="Q1"), _chat_step(2, degraded, message="Q2")],
        [_chat_step(1, body, message="Q1"), _chat_step(2, {"answer": "別"}, message="Q2")],
        [_chat_step(1, body, message="Q1"), _chat_step(2, body, message="Q2", persona="st-2")],
    ]
    for steps in cases:
        assert not [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_STALE]


def test_stale_answer_from_truncated_excerpt_without_digest():
    body = {"answer": "長い回答 " * 600, "sources": []}
    steps = [_chat_step(1, body, message="Q1", digest=False), _chat_step(2, body, message="Q2", digest=False)]
    assert chat_record_from_excerpt(steps[0].http[0].response_excerpt)["answer_complete"] is False
    assert [f for f in dialogue.check(steps, _factory()) if f.hypothesis == dialogue.HYP_STALE]


# ----------------------------------------------------------------------------
# d. 学習者向け応答の数値の欄
# ----------------------------------------------------------------------------

def test_numeric_keys_one_finding_per_persona():
    s1 = _chat_step(1, {"answer": "本文に 80% と書いてあっても欄ではない", "sources": [_src(1, "ch-a")],
                        "origin": {"segment_id": 0, "scroll_offset": 0}})
    s2 = _chat_step(2, {"answer": "b", "draft_errors": 2, "chapter_index": 0, "progress_pct": 10,
                        "created_at": 1700000000}, message="Q2")
    admin = TranscriptStep(seq=3, persona_id="st-1", action_id="admin.materials.list", screen="admin",
                           http=[HttpTrace(method="GET", path="/api/admin/materials", status=200,
                                           digest=response_digest({"weight": 3}))])
    found = [f for f in dialogue.check([s1, s2, admin], _factory()) if f.hypothesis == dialogue.HYP_NUMERIC]
    assert len(found) == 1
    f = found[0]
    assert f.oracle == "B" and f.severity_label == "principle"
    assert f.evidence.quote == "数値の欄: draft_errors, score"
    assert f.evidence.transcript_steps == [1, 2]
    rows = f.evidence.server_rows["numeric_keys[st-1]"]
    assert rows == {"learning.chat.ask": ["draft_errors", "score"]}


def test_numeric_keys_from_truncated_excerpt_ignores_quoted_text():
    raw = '{"answer": "引用 \\"score\\": 5 は本文", "confidence": 0.7, "sources": [{"quote": "' + "x" * 3000
    step = _chat_step(1, {"answer": "x"}, digest=False)
    step.http[0].response_excerpt = raw[:2000]
    found = [f for f in dialogue.check([step], _factory()) if f.hypothesis == dialogue.HYP_NUMERIC]
    assert found and found[0].evidence.quote == "数値の欄: confidence"


# ----------------------------------------------------------------------------
# e. precondition の連鎖
# ----------------------------------------------------------------------------

def _pre(seq, action, name, persona="st-1"):
    return TranscriptStep(seq=seq, persona_id=persona, action_id=action, screen="learning",
                          http=[HttpTrace(method="GET", path="/api/x/{%s}" % name, error=f"precondition:{name}")])


def test_precondition_chain_is_harness_finding():
    steps = [_pre(1, "learning.course.open", "course_id"), _pre(2, "learning.topic.open", "course_id"),
             _pre(3, "learning.chat.ask", "topic_id"), _pre(4, "learning.chat.ask", "topic_id"),
             _pre(10, "learning.course.open", "course_id", persona="st-2"),
             _pre(11, "learning.course.open", "course_id", persona="st-2")]
    found = [f for f in dialogue.check(steps, _factory()) if f.hypothesis.startswith("ハーネス:")]
    assert len(found) == 1  # st-2 は 2 手だけなので出ない
    f = found[0]
    assert f.severity_label == "blocked" and f.suspected_layer == ["cycle_verification"]
    assert f.evidence.transcript_steps == [1, 2, 3, 4]
    assert "course_id" in f.hypothesis and "topic_id" in f.hypothesis


def test_precondition_only_steps_are_not_product_failures_in_behavior():
    steps = [_pre(i, "learning.symbol.lookup", "symbol") for i in (1, 2, 3)]
    found, _ = behavior.check(steps, _factory())
    assert not [f for f in found if "続けて失敗" in f.hypothesis]


# ----------------------------------------------------------------------------
# 審判 C — 原因で束ねる
# ----------------------------------------------------------------------------

def _friction(seq, persona, action="learning.topic.open", friction="confused", reaction="分からない"):
    return TranscriptStep(seq=seq, persona_id=persona, action_id=action, screen="learning",
                          affordance="sidebar.topic", think=ThinkAloud(friction=friction, reaction=reaction))


class _VaryingJudge:
    """ペルソナごとに言い回しの違う仮説を返す偽の LLM 審判。"""

    def __init__(self):
        self.n = 0

    def complete_json(self, system, messages, schema):
        self.n += 1
        return {"verdict": "product_defect", "hypothesis": f"言い回し {'甲乙丙'[self.n % 3]}", "evidence_steps": []}


def test_behavior_merges_same_friction_across_personas():
    steps = [_friction(1, "st-1"), _friction(5, "st-2", reaction="何の画面？"), _friction(9, "st-3", friction="blocked"),
             _friction(12, "st-4", action="learning.chat.ask")]
    found, _ = behavior.check(steps, _factory(), llm=_VaryingJudge())
    merged = dedupe(found)
    assert len(merged) == 3  # topic.open×confused（2 人）/ topic.open×blocked / chat.ask×confused
    f = next(x for x in merged if set(x.persona_id.split(",")) == {"st-1", "st-2"})
    assert f.evidence.transcript_steps == [1, 5]
    occ = f.evidence.server_rows["occurrences"]
    assert [(o["persona_id"], o["steps"]) for o in occ] == [("st-1", [1]), ("st-2", [5])]
    assert len(f.evidence.server_rows["hypotheses"]) == 2


# ----------------------------------------------------------------------------
# digest
# ----------------------------------------------------------------------------

def test_response_digest_shapes():
    d = response_digest({"answer": "a [出典2] b [出典1]", "degraded": False, "ok": True, "_bytes": 10,
                         "sources": [_src(1, "c1", meta="m"), _src(2, "c2")]})
    assert d["numeric_keys"] == ["index", "score"]  # bool・_ で始まる欄は除く
    chat = d["chat"]
    assert chat["answer_markers"] == [1, 2] and chat["sources_known"] is True
    assert chat["sources"][0] == {"index": 1, "position": 1, "chunk_id": "c1", "source_title": "論文A", "meta": "m"}
    assert "score" not in chat["sources"][0]  # 値は残さない
    assert response_digest([{"id": "c1", "title": "t"}]) == {}


def test_structure_answer_hypothesis():
    from uxsim.oracles import dialogue
    ask = _chat_step(1, {"answer": "それは一般的な話です。", "sources": []}, message="この式はどこから来たの？")
    found = [f for f in dialogue.check([ask], _factory()) if "構造で答えていない" in f.hypothesis]
    assert len(found) == 1 and found[0].hypothesis.startswith("仮説")
    grounded = _chat_step(1, {"answer": "理論の前提 [出典1] から導かれます。", "sources": []},
                          message="根拠は？")
    assert not [f for f in dialogue.check([grounded], _factory()) if "構造で答えていない" in f.hypothesis]
    other = _chat_step(1, {"answer": "こんにちは。", "sources": []}, message="こんにちは")
    assert not [f for f in dialogue.check([other], _factory()) if "構造で答えていない" in f.hypothesis]
