"""第 25 周 第 10 波の後の投影の同期を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave10.md``、同期の記録は ``projection_sync_wave10.md``。
ハーネス波（fix-harness-r10）が同期済みのもの（LANE_DEPS・share の材料・doubt の末尾優先・atlas 2026.8・studio prepublish）は
``test_round25_r10_harness_fixes.py`` 側。純関数・偽の ctx と JS の逐語の照合だけ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

from uxsim.config import REPO_ROOT
from uxsim.actions.registry import REGISTRY as ACTIONS_BY_ID
from uxsim.runner import actions_exec as ax, atlas_exec as ag, doubt_exec as dx, share_exec as sx, studio_exec as stx
from uxsim.runner.state import PersonaSession, _component_instance_lines

_JS = REPO_ROOT / "frontend" / "public" / "js"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


class _Ctx:
    client = None

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

    def main(self, *, json=None, params=None, overrides=None):  # 行為の主 API（テストでは呼ばれたことだけ控える）
        self.calls.append(("MAIN", str(overrides or params or ""), json))
        self.session.last_status, self.session.last_body = 200, {}
        return 200, {}

    def missing(self, name):
        self.missing_names.append(name)


def _fact(s: PersonaSession) -> str:
    return str((s.last_body or {}).get("_ui_fact") or "")


# --- 面 A（share）---------------------------------------------------------------------------------------
def test_decision_actor_line_replaces_marked_line_in_bound_parts():
    assert "link.decision_actor_line || link.decision_line" in _js("admin.js")
    link = {"status": "rejected", "status_label": "見送り", "instance_label": "部品X",
            "decision_line": "〔見送り〕 見送り: 田中・2026-10-30", "decision_actor_line": "見送り: 田中・2026-10-30"}
    row = sx.bound_parts_lines({"identity_links": [link]})[0]
    assert "〔見送り〕 見送り: 田中・2026-10-30" in row and row.count("〔見送り〕") == 1
    # 旧サーバ（印の無い形が無い）は従来の decision_line
    old = sx.bound_parts_lines({"identity_links": [{**link, "decision_actor_line": None}]})[0]
    assert "〔見送り〕 〔見送り〕 見送り: 田中" in old


def test_prior_decision_line_after_restore_reason():
    for js in ("admin.js", "deliberation.js"):
        assert "data-identity-prior-decision" in _js(js)
    link = {"status": "candidate", "restore_line": "候補に戻した: 佐藤・2026-10-31", "restore_reason_line": "戻した理由: 再検討",
            "prior_decision_line": "前の判断の記録: 見送り: 田中・2026-10-30（見送りの理由: 粒度が違う）"}
    out = sx.restore_lines(link)
    assert out.index("戻した理由: 再検討") + 1 == out.index(link["prior_decision_line"])
    assert link["prior_decision_line"] in sx.identity_link_actor_lines(link)


def test_entry_freeze_confirm_mentions_learner_effect():
    assert sx.LIBRARY_FREEZE_LEARNER_EFFECT_NOTE in _js("admin.js")
    c = sx.ENTRY_FREEZE_CONFIRM
    assert c.index("パイプラインの解析") < c.index("学生の画面") < c.index("よろしいですか")


def test_share_modal_heading_has_format_label():
    assert "data-doc-share-heading" in _js("admin.js") and "m.format_label" in _js("admin.js")
    assert "教材「論文A」（TeX 版）の設定です。" in sx._share_modal_view("論文A", [], [], format_label="TeX 版")
    assert "教材「論文A」の設定です。" in sx._share_modal_view("論文A", [], [])


def test_relation_decision_reason_button_and_action():
    assert "knowledge-library.relation-decision-reason" in _js("admin.js")
    a = ACTIONS_BY_ID["admin.library.relation_decision_reason"]
    assert a.affordance == "knowledge-library.relation-decision-reason"
    assert "admin.library.relation_decision_reason" in sx.HANDLERS
    row = {"id": "R1", "status": "confirmed", "kind": "broader", "kind_label": "上位", "object_entry_id": "E2",
           "object_name": "重力", "subject_entry_id": "E1", "can_add_decision_reason": True,
           "decision_reason_add_note": "理由の無い確定です。"}
    assert f"［{sx.RELATION_DECISION_REASON_BUTTON}］" in sx._relations_lines([row], "E1")[0]
    assert f"［{sx.RELATION_DECISION_REASON_BUTTON}］" not in sx._relations_lines([{**row, "can_add_decision_reason": None}], "E1")[0]
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_entry"] = {"id": "E1"}
    s.scratch["share_relations"] = [{**row, "can_add_decision_reason": None}]
    ctx = _Ctx({"reason": "r", "relation_hint": "重力"}, s)
    sx._relation_decision_reason(ctx)
    assert not ctx.calls and "relation_decision_reason" in ctx.missing_names  # ボタンが無い行は押さない
    s.scratch["share_relations"] = [row]
    ctx = _Ctx({"reason": "", "relation_hint": "重力"}, s)
    sx._relation_decision_reason(ctx)
    assert not ctx.calls and sx.RELATION_DECISION_REASON_REQUIRED in _fact(s)  # 空は送らない


# --- 面 B（doubt）---------------------------------------------------------------------------------------
_ITEM = {"statement": "等分配", "load_level": "high", "verification_status": "untested", "has_verification_proposal": True,
         "verification_proposal_label": "検証提案あり（完了）", "reachability_summary": "reachable"}


def test_open_list_reachability_label_and_support_scope_note():
    fs = {"support_scope_note": "観測からの支持線は、各行の対象が載っている論文の中で読んでいます（台帳と同じ範囲です）。",
          "field_labels": {"falsification_reachability": "覆る条件の到達可能性"}}
    text = "\n".join(dx.open_lines([_ITEM], only_reachable=False, field_source=fs))
    assert fs["support_scope_note"] in text and "覆る条件の到達可能性: " in text and "／ 到達可能性:" not in text
    assert "検証提案あり（完了）" in text
    assert text.index(fs["support_scope_note"]) < text.index("1. 等分配")
    assert "data-doubt-support-scope-note" in _js("doubt-atlas.js")


def test_proposal_card_completion_lines_and_label():
    js = _js("doubt-atlas.js")
    assert "検証提案の到達可能性: " in js and "data-doubt-proposal-completed" in js and "data-doubt-proposal-note" in js
    done = {"id": "P1", "status": "completed", "status_label": "完了", "reachability_label": "現在の観測で検証可能",
            "status_line": "完了: 田中（2026-10-31 09:00）", "completion_note_line": "確かめたこと: 観測値を照合",
            "status_note": "提案の完了を記録しても、検証状態は変わりません（…）。"}
    lines = dx.proposal_card_lines([done])
    text = "\n".join(lines)
    assert "検証提案の到達可能性: 現在の観測で検証可能" in text
    assert text.index("完了: 田中") < text.index("確かめたこと: 観測値を照合") < text.index("提案の完了を記録しても")
    assert "［完了を記録］" not in text
    prog = "\n".join(dx.proposal_card_lines([{"id": "P2", "status": "in_progress"}]))
    assert dx.PROPOSAL_NOTE_FIELD in prog and "［完了を記録］" in prog


def test_proposal_complete_sends_completion_note():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    t = {"target_type": "claim", "target_id": "X1", "label": "主張"}
    s.scratch["doubt_target"] = t
    s.scratch["doubt_ledger"] = {"exists": True, "challenges": [{"id": "C1", "status": "open",
                                                                  "proposals": [{"id": "P1", "status": "in_progress"}]}]}
    ctx = _Ctx({"status": "完了を記録", "completion_note": "観測値を照合した"}, s)
    dx.proposal_status(ctx)
    patch = next(c for c in ctx.calls if c[0] == "PATCH")
    assert patch[2] == {"status": "completed", "completion_note": "観測値を照合した"}
    assert "completion_note" in ACTIONS_BY_ID["admin.doubt.proposal_status"].args


def test_learner_open_list_words_follow_app_js():
    app = _js("app.js")
    assert dx.LEARNER_NO_SCOPE == "検証の記録なし" and dx.LEARNER_NO_SCOPE in app and dx.LEARNER_HAS_SCOPE in app
    assert "item.verification_proposal_label" in app


def test_runner_never_sends_position_anchor_segment():
    """IK-1122: app.js はレクチャー外で ``position_anchor.segment_id`` を送らない。runner はそもそも position_anchor を送らない
    （送れば「報告あり」と読まれる）— 写しが紛れ込んでいないことを固定する。"""
    assert "anchor.segment_id = lectureState.currentSegmentIndex" in _js("app.js")
    for mod in (ax, dx, ag, sx, stx):
        assert "position_anchor" not in open(mod.__file__, encoding="utf-8").read()


# --- 面 C（atlas）---------------------------------------------------------------------------------------
def test_dismiss_locked_card_has_no_dismiss_button():
    js = _js("admin.js")
    assert "candidate.dismiss_locked" in js and "atlas-edge-dismiss-locked" in js
    note = "この関係は次版の下書きに入っているため、見送れません。"
    c = {"edge_key": "e1", "from_label": "A", "to_label": "B", "decision": {"status": "accepted", "edge_kind": "depends_on"},
         "in_draft": True, "dismiss_locked": True, "dismiss_locked_note": note}
    text = "\n".join(ag.edge_card_lines(c, 1, True))
    assert "[見送り…]" not in text and note in text
    assert "[見送り…]" in "\n".join(ag.edge_card_lines({**c, "dismiss_locked": None, "dismiss_locked_note": None}, 1, True))
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["atlas_edges"] = {"candidates": [], "orphaned_decisions": [c], "draft_exists": True}
    orphan = "\n".join(ag.edge_orphaned_lines(s, s.scratch["atlas_edges"]))
    assert "[見送り…]" not in orphan and note in orphan


def test_dismiss_locked_card_is_not_pressed():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["atlas_domain"] = "astro"
    locked = {"edge_key": "e1", "from_label": "A", "to_label": "B", "decision": {"status": "accepted"},
              "dismiss_locked": True, "dismiss_locked_note": "見送れません。"}
    s.scratch["atlas_edges"] = {"candidates": [locked], "draft_exists": True}
    ctx = _Ctx({"edge_hint": "1", "reason": "理由"}, s)
    ctx.args["domain_key"] = "astro"
    ag._decide_edge("dismiss", ("candidate", "accepted"))(ctx)
    assert not [c for c in ctx.calls if c[1].endswith("/edge-candidates/decide")]
    assert "edge_dismiss_locked" in ctx.missing_names
    # 対照: 見送れるカードは送る
    s.scratch["atlas_edges"] = {"candidates": [{**locked, "dismiss_locked": None}], "draft_exists": True}
    ctx = _Ctx({"edge_hint": "1", "reason": "理由", "domain_key": "astro"}, s)
    ag._decide_edge("dismiss", ("candidate", "accepted"))(ctx)
    assert [c for c in ctx.calls if c[1].endswith("/edge-candidates/decide")]


def test_panel_papers_line_after_relations():
    assert "atlas-panel-papers" in _js("atlas-panel.js")
    atlas = {"nodes": {"n1": {"label": "星形成", "relations": "関係の行", "papers": "ここに置かれているこのコースの論文: 『X』（教員確認済み）。"}}}
    lines = ag.panel_lines(atlas, "n1")
    assert lines.index("関係の行") + 1 == lines.index(atlas["nodes"]["n1"]["papers"])
    assert not any("ここに置かれている" in l for l in ag.panel_lines({"nodes": {"n1": {"label": "星形成"}}}, "n1"))


# --- 面 D（studio）---------------------------------------------------------------------------------------
def test_versions_modal_audience_learner_version_changed_topics():
    js = _js("versioning.js")
    for attr in ("data-vg-audience-note", "data-vg-learner-version", "data-vg-changed-topics"):
        assert attr in js
    state = {"is_owner": True, "latest_version_no": 2, "audience_note": "受講者は発行の時点から新しい版を見ます。",
             "learner_version_note": "受講者は発行済みの版（版 2）を見ています。",
             "unpublished_changes_note": "版 2 の発行の後に保存した変更があります。",
             "changed_topics_label": "発行の後に変わったトピック", "changed_topic_titles": ["降着", "ジェット"]}
    lines = stx.versions_modal_lines("C", state, [])
    order = [lines.index(state[k]) for k in ("audience_note", "learner_version_note", "unpublished_changes_note")]
    assert order == sorted(order) and lines.index("現在の最新版: v2") < order[0]
    assert "  - 降着" in lines and any("発行の後に変わったトピック" in l for l in lines)
    viewer = stx.versions_modal_lines("C", {**state, "is_owner": False}, [])
    assert state["audience_note"] in viewer and state["learner_version_note"] not in viewer and "  - 降着" not in viewer
    assert "同意するまで自分の版" not in "\n".join(lines)


def test_work_note_lists_split_by_place_and_embed_conflict():
    js = _js("admin-lecture-studio.js")
    assert "work_note_list_summaries" in js and 'data-work-note-embed-conflict="1"' in js
    structure = {"title": "C", "work_note_course_notice": "注意点は受講者の画面に出ない欄です。",
                 "work_note_list_summaries": {"body": "本文の「注」の段落に作業メモが残るトピック",
                                              "cautions": "注意点だけに作業メモが残るトピック"},
                 "work_note_topics": [{"id": "t1", "title": "序", "places": ["body", "cautions"]},
                                      {"id": "t2", "title": "降着", "places": ["cautions"]}],
                 "chapters": [{"topics": [{"id": "t1", "title": "序"}, {"id": "t2", "title": "降着"}]}]}
    lines = stx.work_note_course_lines(structure)
    i_body = next(i for i, l in enumerate(lines) if "本文の「注」" in l)
    i_caut = next(i for i, l in enumerate(lines) if "注意点だけに" in l)
    assert lines[i_body + 1] == "  - 1.1 序" and lines[i_caut + 1] == "  - 1.2 降着" and i_body < i_caut
    # 旧サーバ（summaries 無し）は 1 つの一覧
    old = stx.work_note_course_lines({**structure, "work_note_list_summaries": None, "work_note_list_summary": "一覧"})
    assert sum(1 for l in old if l.startswith("▸ ")) == 1
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["studio_structure"] = {"delivery": {}}
    s.scratch["studio_topic"] = {"topic": {"title": "T", "work_note_state": {
        "message": "作業メモが残っています。", "embed_conflict_note": "注意点の「埋め込んでいません」は本文の埋め込みと食い違います。"}}}
    s.scratch["studio_draft"] = {}
    text = "\n".join(stx._draft_lines(stx._topic_view(s)))
    assert text.index("作業メモが残っています。") < text.index("本文の埋め込みと食い違います")


def test_component_supports_summary_line_has_no_counts():
    js = _js("app.js")
    assert "parts.push(pair[0])" in js
    body = {"instance": {"component": {"label": "部品"},
                         "supports": {"preconditions": [{"text": "a"}, {"text": "b"}], "claims": [{"text": "c"}]}}}
    _head, details, _shared = _component_instance_lines(body)
    assert "前提 · 主張" in details and not any("件" in d for d in details)


# --- 横断（チャット）------------------------------------------------------------------------------------
def _chat_session() -> PersonaSession:
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t1")
    s.topic_ids = ["t1", "t2"]
    return s


def _sent_screen(s: PersonaSession, message: str = "ここは？") -> dict:
    ctx = _Ctx({"message": message}, s)
    ax._chat(ctx, intent_mode="on_path")
    body = ctx.calls[-1][2]
    return (body or {}).get("screen_context") or {}


def test_atlas_node_selection_only_on_the_next_turn():
    assert "state.atlasNodeFreshForTurn = true" in _js("app.js") and "selection.atlas_node_id" in _js("app.js")
    s = _chat_session()
    s.scratch["atlas_last_node"] = {"label": "星形成", "node_id": "n9", "status": "", "ledger_status": ""}
    s.scratch["atlas_node_fresh"] = True
    first = _sent_screen(s)
    assert first["selection"].get("atlas_node_id") == "n9" and first["view"].get("atlas_node_id") == "n9"
    second = _sent_screen(s)
    assert "atlas_node_id" not in second["selection"] and second["view"].get("atlas_node_id") == "n9"


def test_personal_map_selection_while_nearby_open():
    pm = _js("personal-map-home.js")
    assert "personal_map_node_id" in pm and "personal_map_center_component_id" in pm
    s = _chat_session()
    ax.remember_personal_map_view(s, "trace-1", "comp-7")
    for _ in range(2):  # 開いている間は続けて載る
        sel = _sent_screen(s)["selection"]
        assert sel.get("personal_map_node_id") == "trace-1" and sel.get("personal_map_center_component_id") == "comp-7"
    s.scratch.pop("personal_map_view")  # 閉じた（トピックを開く・別のタブ・地図を開く）
    assert "personal_map_node_id" not in (_sent_screen(s).get("selection") or {})
    # nearby 200 で控える
    s2 = _chat_session()
    ctx = _Ctx({"node_id": "trace-2"}, s2)
    ax._network_node(("mode", "center_component_id"))(ctx)
    assert s2.scratch["personal_map_view"] == {"personal_map_node_id": "trace-2"}
    ax._network_node(())(_Ctx({"node_id": "trace-2"}, s2))  # 問いからの旅 = 別のタブ
    assert "personal_map_view" not in s2.scratch
