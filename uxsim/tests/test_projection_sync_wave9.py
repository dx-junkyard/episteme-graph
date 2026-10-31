"""第 25 周 第 9 波の後の投影の同期を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave9.md``、同期の記録は ``projection_sync_wave9.md``。
面 C の runner 側（directed_edges / now_note / relations）と面 B の unplotted は ハーネス波（fix-harness-r9）の
``test_round25_r9_harness_fixes.py`` が固定する。純関数・偽の ctx と JS の逐語の照合だけ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

import json

from uxsim.config import REPO_ROOT
from uxsim.oracles import dialogue as dialogue_oracle, share as share_oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import doubt_exec as dx, share_exec as sx, studio_exec as stx
from uxsim.runner.state import PersonaSession
from uxsim.schema import HttpTrace, TranscriptStep

_JS = REPO_ROOT / "frontend" / "public" / "js"
UNDECIDABLE = "この結び付きの部品は再解析で置き換わったため、判断の対象ではありません。"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


class _Ctx:
    client = None  # doubt_exec._mark_ledger_digest は client.traces を読む（無ければ何もしない）

    def __init__(self, args, session=None, responses=None):
        self.args = args
        self.session = session or PersonaSession(persona_id="te", course_id="", topic_id="")
        self.calls: list[tuple[str, str, object]] = []
        self.responses = responses or {}
        self.missing_names: list[str] = []

    def call(self, method, template, *, json=None, params=None, overrides=None):
        path = template
        for k, v in (overrides or {}).items():
            path = path.replace("{" + k + "}", str(v))
        self.calls.append((method, path, json))
        status, body = self.responses.get((method, path), (200, {}))
        self.session.last_status, self.session.last_body = status, body
        return status, body

    def missing(self, name):
        self.missing_names.append(name)


def _fact(s: PersonaSession) -> str:
    return str((s.last_body or {}).get("_ui_fact") or "")


def _undecidable_link(**extra) -> dict:
    return {"link_id": "L9", "id": "L9", "status": "candidate", "decidable": False, "undecidable_note": UNDECIDABLE,
            "document_title": "論文A", "instance_label": "古い部品", "instance_summary": "要約X", "reason": "理由Y",
            **extra}


# --- 面 A（share）IK-1076 ----------------------------------------------------------------------------
def test_js_uses_decidable_predicate():
    admin, dlb = _js("admin.js"), _js("deliberation.js")
    assert 'link.decidable !== false' in admin and "undecidable_note" in admin
    assert "link.decidable !== false" in dlb and "undecidable_note" in dlb
    assert sx.link_decidable({}) and sx.link_decidable({"decidable": True}) and not sx.link_decidable({"decidable": False})


def test_undecidable_rows_have_no_buttons_and_show_note():
    body = {"candidates": [{"entry": {"name": "TOV", "review_status": "confirmed"},
                            "links": [_undecidable_link()]}]}
    text = sx.identity_candidates_view(body)
    assert "［確認］［見送り］" not in text and UNDECIDABLE in text
    assert text.index(UNDECIDABLE) < text.index("要約X")  # 行の直後・要約の前（admin.js の順）
    bound = "\n".join(sx.bound_parts_lines({"identity_links": [_undecidable_link()]}))
    assert "［確認］［見送り］" not in bound and UNDECIDABLE in bound and "要約X" not in bound  # 要約・理由は候補の行だけ
    row = sx.identity_link_row(1, _undecidable_link(created_by_label="田中"))
    assert "［確定］［却下］" not in row and UNDECIDABLE in row
    rejected = _undecidable_link(status="rejected")
    assert f"［{sx.RESTORE_BUTTON}］" not in "\n".join(sx.restore_lines(rejected))
    assert f"［{sx.RESTORE_BUTTON}］" not in sx.identity_link_row(1, rejected)
    # 判断できる行はキーを持たない — 従来どおりボタンが出る
    ok = {k: v for k, v in _undecidable_link().items() if k not in ("decidable", "undecidable_note")}
    assert "［確認］［見送り］" in sx.identity_candidates_view({"candidates": [{"entry": {"name": "TOV"}, "links": [ok]}]})


def test_undecidable_rows_are_not_pressed():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_identity"] = [{"entry": {"name": "TOV"}, "links": [_undecidable_link()]}]
    ctx = _Ctx({"candidate_hint": "TOV", "link_hint": "論文A", "decision": "confirm"}, s)
    sx._identity_link_decide(ctx)
    assert not ctx.calls and "identity_link" in ctx.missing_names and UNDECIDABLE in _fact(s)
    s2 = PersonaSession(persona_id="te", course_id="", topic_id="")
    s2.scratch["share_identity"] = [{"entry": {"name": "TOV"}, "links": [_undecidable_link(status="rejected")]}]
    ctx2 = _Ctx({"candidate_hint": "TOV", "reason": "戻す"}, s2)
    sx._library_identity_link_restore(ctx2)
    assert not ctx2.calls and "identity_link" in ctx2.missing_names
    s3 = PersonaSession(persona_id="te", course_id="", topic_id="")
    s3.scratch["share_element_links"] = [_undecidable_link()]
    ctx3 = _Ctx({"decision": "confirm"}, s3)
    sx._dlb_link_decide(ctx3)
    assert not ctx3.calls and "identity_link" in ctx3.missing_names


# --- 面 A（share）IK-1077 ----------------------------------------------------------------------------
def test_cite_button_follows_can_cite():
    js = _js("admin-lecture-studio.js")
    assert "exp.can_cite === false" in js and "cite_note" in js
    note = "この説明は共有されていないため、別のコースで引用できません。"
    lines = "\n".join(sx.explanation_card_lines(1, {"id": "E1", "kind": "personal", "can_cite": False, "cite_note": note}))
    assert "［別コースで引用］" not in lines and note in lines
    assert "［別コースで引用］" in "\n".join(sx.explanation_card_lines(1, {"id": "E1", "kind": "personal"}))
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_explanations"] = [{"id": "E1", "title": "説明", "can_cite": False, "cite_note": note}]
    ctx = _Ctx({"explanation_hint": "説明"}, s)
    sx._explanations_cite(ctx)
    assert not ctx.calls and "cite_unavailable" in ctx.missing_names and note in _fact(s)


def test_symbol_definition_original_mark():
    assert "definition.original_mark" in _js("app.js")
    text = sx.symbol_popover_view(200, {"available": True, "symbol": "M", "definition": {
        "text": "the total mass", "original_mark": "（原文）"}}, "M")
    assert "定義: the total mass（原文）" in text


# --- 面 A 審判 ------------------------------------------------------------------------------------------
def _teacher_step(path, body, method="GET", status=200, action="admin.library.identity_candidates"):
    return TranscriptStep(seq=1, persona_id="te", action_id=action, args={},
                          http=[HttpTrace(method=method, path=path, status=status,
                                          response_excerpt=json.dumps(body, ensure_ascii=False))])


def _keys(found):
    return [f.fingerprint for f in found]


def test_oracle_a13_undecidable_shape():
    f = FindingFactory()
    good = {"candidates": [{"entry": {"name": "TOV"}, "hidden_count": 0, "hidden_unresolved": False,
                            "links": [_undecidable_link()]}], "facts": []}
    assert not [x for x in share_oracle.check([_teacher_step("/api/admin/library/identity-candidates", good)], f)
                if "undecidable" in x.hypothesis or "判断の対象でない" in x.hypothesis]
    bad = {"identity_links": [_undecidable_link(undecidable_note="", confirm_question="同じものですか")]}
    hyps = [x.hypothesis for x in share_oracle.check(
        [_teacher_step("/api/admin/deliberation/shared-parts/E1/identity-links", bad,
                       action="admin.library.entry_detail")], FindingFactory())]
    assert any("理由の事実文が無い" in h for h in hyps) and any("確定の問い" in h for h in hyps)


def test_oracle_a14_and_cite_403_detail():
    hyps = [x.hypothesis for x in share_oracle.check([_teacher_step(
        "/api/admin/theory-components/C1/explanations", [{"id": "E1", "can_cite": False}],
        action="admin.explanations.open")], FindingFactory())]
    assert any("引用できない理由" in h for h in hyps)
    hyps2 = [x.hypothesis for x in share_oracle.check([_teacher_step(
        "/api/admin/explanations/E1/cite", {"detail": "Forbidden"}, method="POST", status=403,
        action="admin.explanations.cite")], FindingFactory())]
    assert any("日本語" in h for h in hyps2)
    ok = [x for x in share_oracle.check([_teacher_step(
        "/api/admin/explanations/E1/cite", {"detail": "この説明は共有されていないため、別のコースで引用できません。"},
        method="POST", status=403, action="admin.explanations.cite")], FindingFactory())]
    assert not ok


def test_oracle_b6_tex_label_key():
    step = TranscriptStep(seq=1, persona_id="st", action_id="learning.symbol.lookup", args={},
                          http=[HttpTrace(method="GET", path="/api/learning/courses/c/symbols/lookup", status=200,
                                          response_excerpt=json.dumps({"facts": ["式 eq:tov の中に現れます"]},
                                                                      ensure_ascii=False))])
    assert any("eq:" in x.hypothesis for x in share_oracle.check([step], FindingFactory()))
    clean = TranscriptStep(seq=1, persona_id="st", action_id="learning.symbol.lookup", args={},
                           http=[HttpTrace(method="GET", path="/api/learning/courses/c/symbols/lookup", status=200,
                                           response_excerpt=json.dumps({"facts": ["番号の無い式の中に現れます"]},
                                                                       ensure_ascii=False))])
    assert not [x for x in share_oracle.check([clean], FindingFactory()) if "eq:" in x.hypothesis]


# --- 面 B（doubt）----------------------------------------------------------------------------------------
def test_challenge_duplicate_note_is_shown():
    assert "data.duplicate_note" in _js("doubt-atlas.js")
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    t = {"target_type": "claim", "target_id": "X1", "label": "主張"}
    s.scratch["doubt_target"] = t
    s.scratch["doubt_ledger"] = {"exists": True, "challenges": []}
    dup = "記帳しました。この対象には、同じ型・同じ理由の疑義がすでに残っています（取り下げていないもの）。"
    ctx = _Ctx({"challenge_type": "外挿", "reason": "同じ理由"}, s, responses={
        ("POST", "/api/admin/doubt/targets/claim/X1/challenges"): (201, {"challenge_id": "C1", "duplicate_note": dup}),
        ("GET", "/api/admin/doubt/ledger/claim/X1"): (200, {"exists": True, "challenges": []})})
    dx.challenge_record(ctx)
    obs = str((s.last_body or {}).get("_ui_fact") or s.last_body)
    assert dup in json.dumps(s.last_body, ensure_ascii=False) or dup in obs


def test_nearby_chip_learner_wording():
    chip = dx._nearby_chip({"label": "宇宙膨張"}, excerpt=False)
    assert "記帳" not in chip and "検証の記録なし" in chip


# --- 面 D（studio）---------------------------------------------------------------------------------------
_NOTE = "発行した版の後に保存した変更があります。受講者には発行するまで届きません。"


def test_course_header_unpublished_and_work_notes():
    js = _js("admin-lecture-studio.js")
    assert 'data-course-unpublished-changes="1"' in js and 'data-work-note-course-notice="1"' in js
    structure = {"title": "C", "can_save": True,
                 "delivery": {"unpublished_changes": True, "unpublished_changes_note": _NOTE,
                              "versions_entry": {"label": "版の管理を開く"}},
                 "work_note_topics": [{"id": "t2", "title": "降着"}], "work_note_course_notice": "作業メモが残っています。",
                 "work_note_list_summary": "作業メモの残るトピックの一覧",
                 "chapters": [{"title": "第1章", "topics": [{"id": "t1", "title": "序"}, {"id": "t2", "title": "降着"}]}]}
    lines = stx._course_lines(structure, {})
    text = "\n".join(lines)
    assert _NOTE in text and "［版の管理を開く］" in text and "作業メモが残っています。" in text and "  - 1.2 降着" in text
    assert text.index(_NOTE) < text.index("作業メモが残っています。")
    # 変更なし・editor では見出しに出さない
    assert _NOTE not in "\n".join(stx._course_lines({**structure, "delivery": {**structure["delivery"],
                                                                                  "unpublished_changes": False}}, {}))
    assert _NOTE not in "\n".join(stx._course_lines({**structure, "can_save": False}, {}))


def test_versions_modal_owner_note_and_topic_work_note_key():
    lines = stx.versions_modal_lines("C", {"is_owner": True, "unpublished_changes": True,
                                           "unpublished_changes_note": _NOTE, "latest_version_no": 2}, [])
    assert lines.index("現在の最新版: v2") + 1 == lines.index(_NOTE)
    assert _NOTE not in stx.versions_modal_lines("C", {"unpublished_changes_note": _NOTE}, [])
    assert "work_note_state" in stx.TOPIC_SERVER_KEYS
    assert "data-vg-unpublished-changes" in _js("versioning.js")


# --- 横断（チャット）IK-1078 ------------------------------------------------------------------------------
def _chat_step(answer: str, indices: list[int]) -> TranscriptStep:
    body = {"answer": answer, "sources": [{"index": i, "chunk_id": f"c{i}"} for i in indices]}
    return TranscriptStep(seq=1, persona_id="st", action_id="learning.chat.ask", args={"message": "q"}, session=1,
                          http=[HttpTrace(method="POST", path="/api/learning/courses/c/topics/t/chat", status=200,
                                          response_excerpt=json.dumps(body, ensure_ascii=False))])


def test_dialogue_dropped_marker_mirrors_marker_level_negation():
    assert dialogue_oracle.dropped_markers("…を使わない点が違う、と書かれています [出典9]。", {1}) == [9]
    assert dialogue_oracle.dropped_markers("[出典3] は今回の問いには使っていません。[出典1] によれば…", {1}) == []
    assert dialogue_oracle.dropped_markers("[出典2] によれば成り立たない [出典1]。", {1}) == [2]  # 助詞で始まらない
    hits = dialogue_oracle.check([_chat_step("と書かれています [出典9]。", [1])], FindingFactory())
    assert any(f.hypothesis == dialogue_oracle.HYP_DROPPED_MARKER for f in hits)
    ok = dialogue_oracle.check([_chat_step("と書かれています [出典9]。", [1, 9])], FindingFactory())
    assert not any(f.hypothesis == dialogue_oracle.HYP_DROPPED_MARKER for f in ok)
