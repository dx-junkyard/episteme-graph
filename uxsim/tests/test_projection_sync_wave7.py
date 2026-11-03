"""第 25 周 第 7 波の後の投影の同期を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave7.md``、同期の記録は ``projection_sync_wave7.md``。
純関数・偽の ctx と JS の逐語の照合だけ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

from uxsim.actions.registry import REGISTRY as ACTIONS_BY_ID
from uxsim.config import REPO_ROOT
from uxsim.runner import atlas_exec as ax, doubt_exec as dx, share_exec as sx, studio_exec as stx, vision5_exec as vx
from uxsim.runner.state import PersonaSession, material_anchors, project_observation, render_material_text

_JS = REPO_ROOT / "frontend" / "public" / "js"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


class _Ctx:
    """share_exec の行為が使う ctx の最小形（呼び出しを控え、応答は既定の 200）。"""

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


# --- 面 A（share）---------------------------------------------------------------------------------
def _queue_session():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_identity"] = [{"entry": {"name": "TOV"}, "links": [
        {"link_id": "L1", "status": "candidate", "document_title": "論文A"}]}]
    return s


def test_library_link_reject_sends_reason_and_blocks_when_empty():
    assert sx.LIBRARY_LINK_REJECT_PROMPT.replace("「この結び付き」", "") in _js("admin.js").replace(
        '"「" + what + "」', "")  # _libraryPromptDismissReason の文
    ctx = _Ctx({"decision": "reject", "candidate_hint": "TOV", "link_hint": "1"}, _queue_session())
    sx._identity_link_decide(ctx)
    assert not ctx.calls and "reject_reason" in ctx.missing_names
    assert sx.DISMISS_REASON_REQUIRED in ctx.session.last_body["_ui_fact"]
    ctx2 = _Ctx({"decision": "reject", "candidate_hint": "TOV", "link_hint": "1", "reason": "根拠が読めない"},
                _queue_session())
    sx._identity_link_decide(ctx2)
    assert ctx2.calls[0] == ("POST", "/api/admin/deliberation/identity-links/L1/reject", {"reason": "根拠が読めない"})
    assert "に「根拠が読めない」と書いた" in ctx2.session.last_body["_ui_fact"]


def test_deliberation_reject_prompt_mirrors_js():
    js = _js("deliberation.js")
    assert sx.DLB_REJECT_PROMPT in js and sx.DLB_REJECT_NEED_REASON in js
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["share_element_links"] = [{"id": "L9", "status": "candidate", "shared_part_name": "TOV"}]
    ctx = _Ctx({"decision": "reject"}, s)
    sx._dlb_link_decide(ctx)
    assert not ctx.calls and sx.DLB_REJECT_NEED_REASON in s.last_body["_ui_fact"]


def test_identity_candidates_status_label_reason_and_dismissed_entry():
    body = {"candidates": [{"entry": {"name": "概念X", "review_status": "dismissed",
                                      "decision_line": "〔見送り〕 見送り: 田中・2026-10-28",
                                      "decision_reason_line": "見送りの理由: 別物",
                                      "restore_note": "この概念は見送り済みです（…）"},
                            "links": [{"status": "rejected", "status_label": "見送り", "document_title": "論文A",
                                       "decision_line": "〔見送り〕 見送り: 田中・2026-10-28",
                                       "decision_reason_line": "見送りの理由: 表記だけ"}]}]}
    text = sx.identity_candidates_view(body)
    assert "[見送り]" in text and "rejected" not in text
    assert "見送りの理由: 表記だけ" in text and "見送りの理由: 別物" in text and "この概念は見送り済みです" in text
    assert "概念: ［確定］［見送り］" not in text  # 候補のときだけ


def test_bound_parts_and_actor_lines_use_revoke_line_and_reason():
    links = {"identity_links": [
        {"status": "revoked", "status_label": "確定を取り消し", "instance_label": "部品",
         "revoke_line": "〔取り消し済み〕 取り消し: 佐藤・2026-10-28（元の確定: 田中・2026-10-20）", "revoked_by_label": "佐藤"},
        {"status": "rejected", "status_label": "見送り", "instance_label": "部品2",
         "decision_line": "〔見送り〕 見送り: 田中・2026-10-28", "decision_reason_line": "見送りの理由: 記録なし（…）"}]}
    text = "\n".join(sx.bound_parts_lines(links))
    assert "〔取り消し済み〕 取り消し: 佐藤・2026-10-28（元の確定" in text
    assert "〔見送り〕 〔見送り〕 見送り: 田中" in text and "見送りの理由: 記録なし" in text
    actors = sx.identity_link_actor_lines(links["identity_links"][0]) + sx.identity_link_actor_lines(links["identity_links"][1])
    assert any("元の確定" in a for a in actors) and "見送りの理由: 記録なし（…）" in actors


def test_explanations_review_line_and_stranded():
    card = "\n".join(sx.explanation_card_lines(1, {"kind": "personal", "title": "t", "body": "b",
                                                   "review_line": "査読: 田中・2026-10-28（著者）",
                                                   "delivery_note": "学生に届きます"}))
    assert card.index("査読: 田中") < card.index("学生に届きます")
    assert sx.STRANDED_FACT_MARK in _js("admin-lecture-studio.js") and sx.STRANDED_OPEN_LABEL in _js("admin-lecture-studio.js")
    lines = sx.stranded_explanations_view({"facts": ["再解析前の部品に付いたままの説明です（読み取り専用）。"],
                                           "items": [{"kind_label": "独自解釈", "title": "T", "body": "本文",
                                                      "origin_line": "再解析前の部品「X」に付いた説明（作成: A・2026-10-01）",
                                                      "rewrite_note": "書き直すには…"}]})
    assert lines[0].startswith("再解析前") and any("作成: A" in l for l in lines) and "書き直すには…" in lines[-1]
    assert sx.stranded_explanations_view({}) == [sx.STRANDED_EMPTY]
    act = ACTIONS_BY_ID["admin.explanations.stranded_open"]
    assert act.affordance == "lecture-studio.explanation-stranded-open" and "admin.explanations.stranded_open" in sx.HANDLERS


def test_learner_frozen_anchor_and_original_marks():
    gone = {"available": False, "note": "旧い文", "frozen_label": "旧い部品",
            "frozen_label_line": "教材を作ったときの部品『旧い部品』は、再解析で置き換わりました。出典の重なる新しい部品は見つかりません。"}
    text = project_observation("learning.component.context", 200, gone)
    assert "見出し: 論理要素 ・ 旧い部品" in text and "出典の重なる新しい部品は見つかりません" in text and "旧い文" not in text
    assert sx.explanations_view(gone).startswith("（見出し: 旧い部品）")
    ctx_body = {"instance": {"component": {"label": "Static solution", "label_original_mark": "（原文）"},
                             "in_paper": {"narrative_role": "The residual", "narrative_role_original_mark": "（原文）"},
                             "supports": {"claims": [{"excerpt": "We show", "original_mark": "（原文）"}]}},
                "_chip_display_label": "静的な解"}
    text2 = project_observation("learning.component.context", 200, ctx_body)
    assert "見出し: 論理要素 ・ 静的な解" in text2 and "The residual（原文）" in text2 and "We show（原文）" in text2
    chunk = {"text": "![[component:c1]]", "evidence_items": [
        {"kind": "component", "id": "c1", "title": "Static", "display_label": "静的な解", "title_original_mark": ""},
        ]}
    assert "〔⚓ 静的な解〕" in render_material_text(chunk)
    chunk["evidence_items"][0].update(display_label="", title_original_mark="（原文）")
    assert "〔⚓ Static（原文）〕" in render_material_text(chunk)
    assert material_anchors({"chunks": [chunk]})[0]["title"] == "Static（原文）"
    assert sx.symbol_mention_lines({"mentions": [{"text": "x is", "original_mark": "（原文）"}]}) == ["「x is」（原文）"]


# --- 面 B（doubt）---------------------------------------------------------------------------------
def test_assumption_ledger_document_line_and_question_note():
    data = {"items": [{"id": "a", "statement": "前提", "status": "confirmed", "ledger_document_id": "d",
                       "ledger_document_line": "結ばれた論文: 『Cepheus』（［台帳を開く］はこの論文の台帳の行を開きます）"}]}
    lines = dx.assumption_lines(data, "confirmed")
    assert lines.index("   ［台帳を開く］") + 1 == lines.index(
        "   結ばれた論文: 『Cepheus』（［台帳を開く］はこの論文の台帳の行を開きます）")
    assert "ledger_document_line" in _js("doubt-atlas.js") and "central_question_note" in _js("discuss.js")
    body = {"documents": [{"title": "P", "thesis": {"central_question": "", "paper_goal": "",
                                                     "central_question_note": "この論文の中心の問いはまだ整理されていません。"}}]}
    text = vx.discuss_opening_view(body)
    assert "この論文の中心の問いはまだ整理されていません。" in text and "この論文が答えようとした問い" not in text


# --- 面 C（atlas）---------------------------------------------------------------------------------
def test_edge_draft_create_row_and_direction_line():
    js = _js("admin.js")
    for t in (ax.EDGE_DRAFT_CREATE_NOTE, ax.EDGE_DRAFT_CREATE_RETIRED_NOTE, ax.EDGE_DRAFT_CREATE_BUTTON):
        assert t in js
    assert ax.edge_draft_create_lines({"draft_exists": True}) == []
    assert ax.edge_draft_create_lines({"draft_exists": False})[0].endswith(f"[{ax.EDGE_DRAFT_CREATE_BUTTON}]")
    assert ax.edge_draft_create_lines({}, retired=True)[0].endswith("（押せない）")
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["atlas_edges"] = {"draft_exists": False, "candidates": [], "orphaned_decisions": [
        {"edge_key": "edge|d|a|b", "from_label": "A", "to_label": "B", "decision": {"status": "accepted"}}]}
    text = "\n".join(ax.edges_lines(s))
    assert text.count(ax.EDGE_DRAFT_CREATE_NOTE) == 2  # 区画の先頭と「採用済み・未反映」の区画
    act = ACTIONS_BY_ID["admin.atlas.edges.draft_from_frozen"]
    assert act.affordance == "atlas.edge-draft-from-frozen" and "admin.atlas.edges.draft_from_frozen" in ax.HANDLERS
    assert ax.unavailable("admin.atlas.edges.draft_from_frozen", s) is None
    s.scratch["atlas_edges"]["draft_exists"] = True
    assert ax.unavailable("admin.atlas.edges.draft_from_frozen", s)


def test_sea_version_label_after_legend():
    land = {"placements": [], "skeleton_version_label": "地図 版 2026.4"}
    skel = {"levels": {"1": {"regions": [{"id": "r1", "label": "星"}], "nodes": []}}}
    lines = ax.sea_map_lines(land, skel, None)
    assert "地図 版 2026.4" in lines and lines.index("地図 版 2026.4") > lines.index("- 星: 霧")
    assert "skeleton_version_label" in _js("corpus-sea.js")
    assert ax.PANEL_VERIFY_FALLBACK in _js("atlas-panel.js")


# --- 面 D（studio）--------------------------------------------------------------------------------
def test_appendix_stale_list_and_frozen_anchor_notice():
    st = {"chapters": [{"title": "第1章", "topics": [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}]}],
          "appendix_stale_topics": [{"id": "t2", "title": "B"}], "appendix_stale_course_notice": "付録が古いトピックがあります（下の一覧）。",
          "appendix_stale_list_summary": "付録が古いトピックの一覧",
          "frozen_anchor_notice": "このコースの教材の部品の参照のうち…"}
    out = stx.appendix_stale_lines(st)
    assert out[0] == "付録が古いトピックがあります（下の一覧）。" and "  - 1.2 B" in out
    text = "\n".join(stx._course_lines(st, {}))
    assert "このコースの教材の部品の参照のうち…" in text
    js = _js("admin-lecture-studio.js")
    assert "lecture-studio.appendix-stale-list" in js and "data-frozen-anchor-notice" in js


def test_unpublished_changes_and_versions_entry():
    s = PersonaSession(persona_id="te", course_id="", topic_id="")
    s.scratch["studio_structure"] = {"delivery": {"message": "保存は次の発行で届きます。", "status": "release",
                                                  "unpublished_changes": True,
                                                  "unpublished_changes_note": "版 1 の発行の後に保存した変更があります。受講者はまだ版 1 を見ています。",
                                                  "versions_entry": {"label": "版の管理を開く", "object_type": "course"}}}
    s.scratch["studio_topic"] = {"topic": {"title": "T"}}
    s.scratch["studio_draft"] = {}
    text = "\n".join(stx._draft_lines(stx._topic_view(s)))
    assert text.index("保存は次の発行で届きます。") < text.index("版 1 の発行の後に") < text.index("［版の管理を開く］")
    s.scratch["studio_structure"]["delivery"].pop("versions_entry")  # editor
    assert "［版の管理を開く］" not in "\n".join(stx._draft_lines(stx._topic_view(s)))
    modal = stx.versions_modal_lines("コース", {"latest_version_no": 1, "can_publish": True, "active_release_id": "r1"},
                                     [{"id": "r1", "version_no": 1, "created_at": "2026-10-28T09:05:00"}])
    assert "現在の最新版: v1" in modal and any("〔現行〕 2026/10/28 09:05" in l for l in modal)
    # 第 10 波（IK-1127）: コースの冒頭の静的な 1 文は出さない（宛先ごとの届き方はサーバの audience_note）
    assert stx.VG_PUBLISH in _js("versioning.js") and stx.VG_INTRO_COURSE == ""
    assert "共有先は同意するまで自分の版に留まります" not in _js("versioning.js")
    for aid, anchor in (("admin.lecture_studio.open_versions", "lecture-studio.open-versions"),
                        ("admin.lecture_studio.publish_release", "versioning.publish")):
        assert ACTIONS_BY_ID[aid].affordance == anchor and aid in stx.HANDLERS
