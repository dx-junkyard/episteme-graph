"""第 25 周 第 8 波の後の投影の同期を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave8.md``、同期の記録は ``projection_sync_wave8.md``。
純関数・偽の ctx と JS の逐語の照合だけ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

from uxsim.actions.registry import REGISTRY as ACTIONS_BY_ID
from uxsim.config import REPO_ROOT
from uxsim.oracles import share as share_oracle
from uxsim.runner import doubt_exec as dx, share_exec as sx, studio_exec as stx
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace, TranscriptStep

_JS = REPO_ROOT / "frontend" / "public" / "js"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


class _Ctx:
    """runner の行為が使う ctx の最小形（呼び出しを控え、応答は既定の 200）。"""

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

    def main(self, *, json=None, params=None, overrides=None):
        return self.call("POST", "/api/admin/library/relations", json=json, params=params, overrides=overrides)

    def missing(self, name):
        self.missing_names.append(name)


def _fact(s: PersonaSession) -> str:
    return str((s.last_body or {}).get("_ui_fact") or "")


# --- 面 A（share）---------------------------------------------------------------------------------
def test_restore_prompts_and_button_mirror_js():
    admin, dlb = _js("admin.js"), _js("deliberation.js")
    assert sx.LIBRARY_RESTORE_PROMPT in admin and sx.RESTORE_NEED_REASON in admin
    assert sx.DLB_RESTORE_PROMPT in dlb and sx.RESTORE_NEED_REASON in dlb
    assert ">" + sx.RESTORE_BUTTON + "</button>" in admin and ">" + sx.RESTORE_BUTTON + "</button>" in dlb
    assert sx.RELATION_CONFIRM_PROMPT in admin and sx.RELATION_REASON_PLACEHOLDER in admin
    for aid in ("admin.library.identity_link_restore", "admin.deliberation.identity_link_restore"):
        assert ACTIONS_BY_ID[aid].affordance == "deliberation.identity-link-restore" and aid in sx.HANDLERS


def _queue_with_rejected():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_identity"] = [{"entry": {"name": "TOV"}, "links": [
        {"link_id": "L1", "status": "rejected", "document_title": "論文A",
         "restore_note": "見送った判断は記録に残ります。"}]}]
    return s


def test_library_restore_blocks_without_reason_and_sends_reason():
    ctx = _Ctx({"candidate_hint": "TOV"}, _queue_with_rejected())
    sx._library_identity_link_restore(ctx)
    assert not ctx.calls and "restore_reason" in ctx.missing_names
    assert "見送った判断は記録に残ります。 — " + sx.LIBRARY_RESTORE_PROMPT in _fact(ctx.session)
    ctx2 = _Ctx({"candidate_hint": "TOV", "reason": "読み違えた"}, _queue_with_rejected(), responses={
        ("POST", "/api/admin/deliberation/identity-links/L1/restore"): (200, {"outcome_fact": "結び付きを候補に戻しました"}),
        ("GET", "/api/admin/library/identity-candidates"): (200, {"candidates": [{"entry": {"name": "TOV"}, "links": [
            {"link_id": "L1", "status": "candidate", "document_title": "論文A",
             "restore_line": "〔候補に戻した〕 戻した: 田中・2026-10-31（前の判断: 見送り）",
             "restore_reason_line": "候補に戻した理由: 読み違えた"}]}]})})
    sx._library_identity_link_restore(ctx2)
    assert ctx2.calls[0] == ("POST", "/api/admin/deliberation/identity-links/L1/restore", {"reason": "読み違えた"})
    text = _fact(ctx2.session)
    assert "（一覧の直前に表示: 結び付きを候補に戻しました）" in text
    assert text.index("〔候補に戻した〕") < text.index("候補に戻した理由: 読み違えた")
    assert f"［{sx.RESTORE_BUTTON}］" not in text  # 候補に戻った行にはボタンが無い


def test_restore_lines_in_bound_parts_and_deliberation_row():
    body = {"identity_links": [{"id": "B1", "status": "revoked", "instance_label": "TOV", "revoke_line": "取り消し: 田中",
                                "restore_note": "ホバーの説明"},
                               {"id": "B2", "status": "candidate", "instance_label": "EOS",
                                "restore_line": "〔候補に戻した〕 戻した: 佐藤", "restore_reason_line": "候補に戻した理由: x"}]}
    lines = sx.bound_parts_lines(body)
    text = "\n".join(lines)
    assert f"  ［{sx.RESTORE_BUTTON}］" in lines and "ホバーの説明" not in text  # title は行に写さない
    assert "  〔候補に戻した〕 戻した: 佐藤" in lines and "  候補に戻した理由: x" in lines
    row = sx.identity_link_row(1, {"status": "rejected", "restore_note": "前の判断は残ります", "decision_reason_line": "見送りの理由: y"})
    assert "   前の判断は残ります" in row and row.rstrip().endswith(f"［{sx.RESTORE_BUTTON}］")
    actors = sx.identity_link_actor_lines({"status": "candidate", "restore_line": "〔候補に戻した〕 戻した: A",
                                           "restore_reason_line": "候補に戻した理由: z"})
    assert actors == ["〔候補に戻した〕 戻した: A", "候補に戻した理由: z"]


def test_dlb_restore_blocks_then_posts():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_element_links"] = [{"id": "L9", "status": "rejected", "shared_part_name": "TOV"}]
    ctx = _Ctx({}, s)
    sx._dlb_link_restore(ctx)
    assert not ctx.calls and sx.RESTORE_NEED_REASON in _fact(s)
    ctx2 = _Ctx({"reason": "戻す"}, s)
    sx._dlb_link_restore(ctx2)
    assert ctx2.calls[0] == ("POST", "/api/admin/deliberation/identity-links/L9/restore", {"reason": "戻す"})


def test_relation_rows_record_and_decide_payload():
    rels = [{"id": "r1", "subject_entry_id": "e2", "object_entry_id": "e1", "subject_name": "X", "kind": "broader",
             "kind_label": "下位", "status": "confirmed", "direction_line": "「X」はこの概念の下位です（…）。",
             "added_line": "追加: 田中・2026-10-30（理由: 包含）", "decision_line": "〔確定〕 確定: 田中・2026-10-31",
             "decision_reason_line": "確定の理由: 定義から"}]
    lines = sx._relations_lines(rels, "e1")
    assert lines[0].startswith("1. 下位: X") and "［見送り］" in lines[0]
    assert [l.strip() for l in lines[1:]] == [rels[0]["direction_line"], rels[0]["added_line"], rels[0]["decision_line"],
                                             rels[0]["decision_reason_line"]]
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_entry"] = {"id": "e1", "name": "Y"}
    s.scratch["share_relations"] = [{"id": "r2", "subject_entry_id": "e1", "object_entry_id": "e3", "object_name": "Z",
                                     "kind": "related", "status": "candidate"}]
    ctx = _Ctx({"status": "confirmed", "cancel": True}, s)
    sx._relation_decide(ctx)
    assert not ctx.calls and "キャンセル" in _fact(s)
    ctx2 = _Ctx({"status": "confirmed"}, s, responses={
        ("POST", "/api/admin/library/relations/r2/decide"): (200, {"facts": ["〔確定〕 確定: 田中・2026-10-31"]})})
    sx._relation_decide(ctx2)
    assert ctx2.calls[0] == ("POST", "/api/admin/library/relations/r2/decide", {"status": "confirmed", "viewer_entry_id": "e1"})
    s.scratch["share_relations"] = [{"id": "r2", "subject_entry_id": "e1", "object_entry_id": "e3", "object_name": "Z",
                                     "kind": "related", "status": "candidate"}]  # 読み直しで一覧が入れ替わった
    ctx3 = _Ctx({"status": "dismissed"}, s)
    sx._relation_decide(ctx3)
    assert not ctx3.calls and sx.DISMISS_REASON_REQUIRED in _fact(s)


def test_entry_review_decision_line():
    text = sx.entry_detail_view({"name": "A", "review_status": "confirmed",
                                 "review_decision_line": "〔確定〕 確定: 田中・2026-10-31"}, None, None, None, "")
    assert "いまの扱い: 確定 〔確定〕 確定: 田中・2026-10-31" in text


def test_oracle_e2_reads_last_link_decision_only():
    def step(seq, path):
        return TranscriptStep(seq=seq, persona_id="te", action_id="admin.library.identity_link_decide", args={},
                              http=[HttpTrace(method="POST", path=path, status=200, response_excerpt="{}")])
    steps = [step(1, "/api/admin/deliberation/identity-links/L1/reject"),
             step(2, "/api/admin/deliberation/identity-links/L1/restore"),
             step(3, "/api/admin/deliberation/identity-links/L2/reject")]
    links = share_oracle.teacher_decisions(steps)["links"]
    assert [(l["id"], l["decision"]) for l in links] == [("L2", "reject")]


# --- 面 B（doubt）---------------------------------------------------------------------------------
def test_unplotted_points_load_note_and_targets():
    data = {"points": [], "empty_note": "", "unplotted_heading": "依存の広がりを計算できない前提（…）",
            "unplotted_points": [{"target_type": "assumption", "target_id": "a1", "label": "等分配", "load_level": "",
                                  "load_note": "依存の広がり: 計算できません（…）。"}],
            "population_note": "母集合の注記"}
    lines = dx.atlas_lines(data)
    assert lines.index("依存の広がりを計算できない前提（…）") < lines.index("- 前提1: ［等分配］（押すと台帳）") < lines.index("母集合の注記")
    js = _js("doubt-atlas.js")
    assert "unplotted_heading" in js and "data-doubt-unplotted-point" in js and "data-doubt-load-note" in js
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["doubt_atlas"] = data
    targets = dx._targets_from(type("C", (), {"session": s})(), "atlas")
    assert targets and targets[0]["target_id"] == "a1"
    rows = dx.open_lines([{"statement": "等分配", "load_level": "", "load_note": "依存の広がり: 計算できません（…）。",
                           "verification_status": "untested"}], only_reachable=False)
    assert "［依存の広がり: 計算できません（…）。 / " in rows[-1] and "依存の広がり: 　" not in rows[-1]
    view = dx.ledger_view({"label": "等分配", "target_type": "assumption"},
                          {"verification_status": "untested", "load_note": "依存の広がり: 計算できません（…）。"})
    assert "依存の広がり: 計算できません（…）。" in view


def test_withdrawn_record_line_and_learner_words():
    entry = {"challenges": [{"id": "c1", "status": "withdrawn", "challenge_type": "definitional", "challenger_name": "A",
                             "record_line": "記帳: A（2026-10-30 10:00） ／ 取り下げ: A（2026-10-31 09:00・記帳者本人）"}]}
    lines = dx.withdrawn_challenge_lines(entry, opened=True)
    i = next(n for n, l in enumerate(lines) if l.strip().startswith("状態:"))
    assert lines[i + 1].strip().startswith("記帳: A")
    app, pm = _js("app.js"), _js("personal-map-home.js")
    assert dx.LEARNER_NO_SCOPE in app and dx.LEARNER_HAS_SCOPE in app and "（記録された検証スコープ: " in app
    assert "検証の記録がある" in pm and "検証の記録がある" in dx.NEARBY_LEGEND_LEDGER


# --- 面 D（studio）--------------------------------------------------------------------------------
_REQ = {"label": "依頼の文をコピー", "recipient": "所有者（田中）", "request_line": "音声の生成をお願いします。",
        "section_labels": {"course": "コース", "request": "お願い", "language": "読み上げの言語",
                           "audio_state": "音声の状態", "topics": "音声の無いトピック"},
        "language_labels": {"ja": "日本語", "en": "英語"},
        "copy_notice": "音声の依頼の文をクリップボードに写しました。所有者（田中）に渡してください。"}


def _owner_only_session():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch.update({"studio_course_id": "c", "studio_loaded": True, "studio_settings": {"lecture_language": "ja"},
                      "studio_structure": {"can_save": False, "title": "ゼミ",
                                           "audio_generate_notice": "…音声の作り直しは所有者（田中）に依頼してください。",
                                           "audio_request": _REQ, "topic_audio_status": {"message": "音声は未生成です"},
                                           "chapters": [{"topics": [{"title": "T1", "has_topic_audio": False},
                                                                    {"title": "T2", "has_topic_audio": True}]}]}})
    return s


def test_owner_only_readiness_copy_button_and_text():
    s = _owner_only_session()
    lines = stx._readiness_lines("owner_only", s.scratch["studio_structure"])
    assert lines[1] == "  …音声の作り直しは所有者（田中）に依頼してください。 ［依頼の文をコピー］"
    text = stx.audio_request_text(s.scratch["studio_structure"], "ja")
    assert text == ("【コース】\nゼミ\n\n【お願い】\n音声の生成をお願いします。\n\n【読み上げの言語】\n日本語\n\n"
                    "【音声の状態】\n音声は未生成です\n\n【音声の無いトピック】\n- T1")
    js = _js("admin-lecture-studio.js")
    assert "lecture-studio.audio-request-copy" in js and 'add("topics", missing.join' in js
    owner = js.split('case "owner_only":', 1)[1].split("default:", 1)[0]
    assert 'action: ""' in owner and "audio_request" in owner  # 押せない「→ 所有者に依頼する」は出ない


def test_copy_audio_request_action():
    s = _owner_only_session()
    ctx = _Ctx({}, s)
    stx._copy_audio_request(ctx)
    assert not ctx.calls and s.scratch["studio_clipboard"].startswith("【コース】")
    shown = project_observation("admin.lecture_studio.audio_request_copy", s.last_status, s.last_body)
    assert _REQ["copy_notice"] in shown and "【お願い】" in shown
    s.scratch["studio_structure"]["can_save"] = True
    ctx2 = _Ctx({}, s)
    stx._copy_audio_request(ctx2)
    assert "audio_request_copy_button" in ctx2.missing_names
    act = ACTIONS_BY_ID["admin.lecture_studio.audio_request_copy"]
    assert act.affordance == "lecture-studio.audio-request-copy" and "admin.lecture_studio.audio_request_copy" in stx.HANDLERS


def test_component_context_original_marks_on_supports_and_dependencies():
    body = {"instance": {"component": {"label": "部品"},
                         "supports": {"preconditions": [{"text": "Static", "original_mark": "（原文）"}],
                                      "cautions": [{"text": "注意", "original_mark": ""}],
                                      "dependencies": [{"target_label": "EOS", "reason": "uses", "original_mark": "（原文）"}]}}}
    text = project_observation("learning.component.context", 200, body)
    assert "- Static（原文）" in text and "- 注意\n" in text + "\n" and "- EOS — uses（原文）" in text
    assert "it.original_mark" in _js("app.js") and "d.original_mark" in _js("app.js")
