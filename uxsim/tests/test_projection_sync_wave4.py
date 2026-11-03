"""第 25 周 第 4 波（第 4 周の是正）の後の投影の同期 — 投影が製品の画面と同じ欄・同じ形を写すことを固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave4.md``、同期の記録は ``projection_sync_wave4.md``。
JS の文言の逐語ミラーは原文に現れることを固定する（PE6）。MockTransport のみ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.config import REPO_ROOT
from uxsim.runner import actions_exec, atlas_exec as ax, doubt_exec as dx, share_exec as sx, studio_exec as stx
from uxsim.runner import vision5_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation, render_material_text, shared_pane_lines

_JS = REPO_ROOT / "frontend" / "public" / "js"


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def _run(action_id: str, args: dict, routes: dict, session: PersonaSession, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = routes.get((request.method, request.url.path))
        if callable(body):
            body = body(request)
        if body is None:
            return httpx.Response(404, json={"detail": "見つかりません"})
        status, payload = body if isinstance(body, tuple) else (200, body)
        return httpx.Response(status, json=payload)

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    ctx = _Ctx(REGISTRY[action_id], args, session, client)
    assert sx.execute(action_id, ctx) is True
    return project_observation(action_id, session.last_status, session.last_body)


# --- 面 A（share）-------------------------------------------------------------------------------
def test_js_mirrors_are_verbatim():
    adm, dlb, ls = _js("admin.js"), _js("deliberation.js"), _js("admin-lecture-studio.js")
    assert sx.LS_ENDORSE_HEAD.split(" — ")[0] in ls and sx.LS_ENDORSE_SUB in ls and sx.LS_EVIDENCE_HEAD in ls
    assert sx.DLB_REVOKE_PROMPT in dlb and sx.DLB_REVOKE_NEED_REASON in dlb and sx.DLB_GRANULARITY_CONFIRM_TAIL in dlb
    assert sx.DLB_LINK_STATUS["revoked"] in dlb and sx.BOUND_PARTS_STATUS["revoked"] in adm
    assert sx.BOUND_PARTS_REVOKE_PROMPT in adm and sx.BOUND_PARTS_REVOKE_FAIL in adm


def test_identity_link_row_candidate_and_confirmed():
    cand = {"status": "candidate", "shared_part_id": "e1", "shared_part_name": "TOV 方程式",
            "instance_element_type": "theory_component", "instance_type_label": "理論", "instance_label": "静水圧平衡",
            "instance_document_title": "PINN", "instance_document_version_label": "TeX 版・a.tar.gz",
            "created_by_label": "岡村", "confirm_question": "部品「静水圧平衡」と共通部品「TOV 方程式」は、同じものですか。",
            "granularity_note": "種別が違います。", "confirm_note": "確定すると学生に出ます。"}
    row = sx.identity_link_row(1, cand)
    assert "（論文『PINN・TeX 版・a.tar.gz』）" in row and "作成: 岡村" in row
    assert row.index("同じものですか") < row.index("種別が違います") < row.index("確定すると") < row.index("［確定］［却下］")
    done = dict(cand, status="confirmed", decided_by_label="岡村", self_confirmed_note="作成者が確定しました。",
                revoke_note="取り消すと…理由は必須です。")
    row = sx.identity_link_row(1, done)
    assert "作成: 岡村 ／ 確定: 岡村" in row and "作成者が確定しました。" in row and "［確定を取り消す］" in row
    assert "同じものですか" not in row and "［確定］［却下］" not in row
    gone = dict(done, status="revoked", revoked_by_label="山本", revoked_at="2026-10-24T01:00:00", revoke_reason="別物")
    row = sx.identity_link_row(1, gone)
    assert "確定を取り消し" in row and "取り消し: 山本（2026-10-24）" in row and "取り消しの理由: 別物" in row
    assert "［確定を取り消す］" not in row


def test_bound_parts_and_entry_review_buttons():
    body = {"identity_links": [
        {"id": "l2", "status": "revoked", "instance_label": "B", "revoked_by_label": "山本", "revoke_reason": "別物"},
        {"id": "l1", "status": "confirmed", "instance_label": "A", "instance_document_title": "PINN",
         "instance_document_version_label": "PDF 版・p.pdf", "decided_by_label": "岡村"}]}
    lines = sx.bound_parts_lines(body)
    assert lines[0].startswith("- 論文『PINN・PDF 版・p.pdf』 「A」") and "確定: 岡村" in lines[0]
    assert lines[0].endswith("［確定を取り消す］") and "取り消し: 山本" in lines[1] and "取り消しの理由: 別物" in lines[2]
    assert sx.entry_review_buttons("candidate") == " ［確定にする］［見送る］"
    assert sx.entry_review_buttons("dismissed") == " ［候補に戻す］" and sx.entry_review_buttons("confirmed") == ""


def test_library_identity_link_revoke_prompt_and_reload():
    seen: list = []
    entry = {"id": "e1", "name": "TOV 方程式", "domain_key": "astrophysics", "review_status": "confirmed"}
    links = {"identity_links": [{"id": "l1", "status": "confirmed", "instance_label": "A", "revoke_note": "記録は残ります。"}]}
    routes = {("GET", "/api/admin/deliberation/shared-parts/e1/identity-links"): links,
              ("POST", "/api/admin/deliberation/identity-links/l1/revoke"): {"id": "l1", "status": "revoked"},
              ("GET", "/api/admin/library/entries/e1"): entry,
              ("GET", "/api/admin/library/entries/e1/labels"): {"labels": []},
              ("GET", "/api/admin/library/relations"): {"relations": []},
              ("GET", "/api/admin/library/atlas-links"): {"links": []}}
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch["share_entry"] = entry
    text = _run("admin.library.identity_link_revoke", {}, routes, s, seen)
    assert not any(r.method == "POST" for r in seen) and sx.DLB_REVOKE_NEED_REASON in text
    text = _run("admin.library.identity_link_revoke", {"reason": "使うだけの関係"}, routes, s, seen)
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content) == {"reason": "使うだけの関係"}
    assert "（記録は残ります。 — 取り消す理由を書いてください（必須）。）に「使うだけの関係」と書いた" in text
    assert "結びついた教材の部品" in text or "TOV 方程式" in text


def test_explanation_card_my_support_and_evidence():
    exp = {"id": "x1", "kind": "standard", "title": "要約", "body": "本文", "review_status": "teacher_approved",
           "evidence_claims": [{"claim_id": "c1", "text": "圧力は密度で決まる"}], "my_support": False}
    lines = "\n".join(sx.explanation_card_lines(1, exp))
    assert sx.LS_EVIDENCE_HEAD in lines and "- 圧力は密度で決まる ［根拠を開く］" in lines
    assert "［支持を取り消す］" not in lines
    lines = "\n".join(sx.explanation_card_lines(1, dict(exp, my_support=True, evidence_claims=[],
                                                         evidence_note="根拠の主張の記録が無い")))
    assert "［支持を取り消す］" in lines and "根拠の主張の記録が無い" in lines


def test_shared_pane_uses_standardization_note_only():
    lines = shared_pane_lines({"entry": {"name": "TOV", "standardization_status": "standard"}})
    assert not any("標準化状況" in ln for ln in lines)
    lines = shared_pane_lines({"entry": {"name": "TOV", "standardization_note": "教員は標準的と判断しています。"}})
    assert "教員は標準的と判断しています。" in lines


def test_copilot_anchor_links_render_as_heading_text():
    body = {"answer": "手順は [結びついた教材の部品](#bound-parts) を見てください。"}
    text = project_observation("admin.copilot.chat", 200, body)
    assert "「結びついた教材の部品」" in text and "](#" not in text


def test_reconstructed_mark_in_material_and_symbol_popover():
    text = render_material_text({"text": "式 [[FORMULA_0]] と [[FORMULA_1]]",
                                 "formulas": [{"latex": "P=K\\rho", "reconstructed": True, "reconstructed_mark": "AI復元"},
                                              {"latex": "r"}]})
    assert "$P=K\\rho$ 〔AI復元〕" in text and "$r$" in text
    pop = sx.symbol_popover_view(200, {"available": True, "symbol": "P", "definition": {
        "text": "圧力", "latex": "P=K\\rho", "reconstructed": True, "reconstructed_mark": "AI復元"}}, "P")
    assert "$P=K\\rho$ 〔AI復元〕" in pop


# --- 面 B（doubt）--------------------------------------------------------------------------------
def test_ledger_view_status_label_proposals_and_refresh_note():
    entry = {"target_label": "等分配", "verification_status": "untested", "falsification_refresh_note": "コース全体が対象です。",
             "challenges": [{"id": "ch1", "challenge_type": "scope_extrapolation", "status": "led_to_verification",
                             "status_label": "検証提案へ昇格",
                             "proposals": [{"id": "p1", "proposal": "取り直す", "status": "in_progress",
                                            "status_label": "検証に着手済み", "reachability_label": "未評価",
                                            "proposer_name": "岡村"}]}]}
    text = dx.ledger_view({"label": "等分配", "target_type": "assumption"}, entry)
    assert "状態: 検証提案へ昇格" in text and "検証提案1: 検証に着手済み" in text and "［完了を記録］［取り下げ］" in text
    assert text.index("［AIに候補を出してもらう］") < text.index("コース全体が対象です。")
    ro = dx.ledger_view({"label": "等分配", "target_type": "assumption"}, dict(entry, can_edit=False))
    assert "［完了を記録］［取り下げ］（押せない）" in ro
    none = dx.ledger_view({"label": "x", "target_type": "assumption"},
                          {"exists": False, "falsification_refresh_note": "コース全体が対象です。"})
    assert "コース全体が対象です。" in none
    src = _js("doubt-atlas.js")
    assert "検証提案: " in src and "コーパス外の確認: " in src and "提案者: " in src


def test_assumptions_mining_note_and_materials_role():
    lines = dx.assumption_lines({"items": [], "mining_note": "前提マイニングを実行中です。"}, "candidate")
    assert lines[1] == "前提マイニングを実行中です。" and lines[2] == dx.ASSUMPTIONS_EMPTY
    text = project_observation("admin.materials.list", 200, [
        {"title": "論文", "filename": "a.pdf", "status": "completed", "visibility": "private", "role_label": "閲覧のみ",
         "edit_request_note": "編集の共有は、所有者の岡村さんに頼めます。", "document_id": "d1"}])
    assert "・閲覧のみ" in text and "編集の共有は、所有者の岡村さんに頼めます。" in text


def test_studio_graph_node_label_is_japanese_stage():
    seen: list = []
    graph = {"graph": {"nodes": [{"id": "n1", "label": "Theory basis", "graph_layer": "main",
                                  "component_type": "TheoryOperationNode"}]}}

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/component-graph"):
            return httpx.Response(200, json=graph)
        return httpx.Response(200, json={"exists": False})

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    ctx = _Ctx(REGISTRY["admin.doubt.studio_node_ledger"], {"document_id": "d1", "node_hint": "Theory basis"}, s, client)
    assert dx.execute("admin.doubt.studio_node_ledger", ctx)
    assert "台帳の詳細: 理論の土台" in str(s.last_body.get("_ui_fact"))


def test_learner_sources_tab_folds_same_fact_line():
    seen: list = []
    s = PersonaSession(persona_id="st-06", course_id="c9", topic_id="t2")
    s.scratch["last_sources"] = {"1": "ch1"}
    fact = "この内容の検証スコープはまだ記帳されていません。"
    routes = {("GET", "/api/learning/courses/c9/source-chunk/ch1"): {"formulas": [{"id": "eq_1", "label": "式 1"}]},
              ("GET", "/api/learning/courses/c9/chunks/ch1/claim-refs"): {"claims": [{"id": "cl1", "label": "主張 A"}]},
              ("GET", "/api/learning/courses/c9/ledger/equation/eq_1"): {"exists": False, "fact_line": fact},
              ("GET", "/api/learning/courses/c9/ledger/claim/cl1"): {"exists": False, "fact_line": fact}}

    def handler(request):
        seen.append(request)
        body = routes.get((request.method, request.url.path))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={})

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    ctx = _Ctx(REGISTRY["learning.ledger.sources_tab"], {}, s, client)
    assert dx.execute("learning.ledger.sources_tab", ctx)
    text = str(s.last_body.get("_ui_fact"))
    assert f"数式 式 1・主張 主張 A — {fact}" in text and text.count(fact) == 1


def test_chat_screen_context_carries_atlas_last_node():
    s = PersonaSession(persona_id="st-01", course_id="c1", topic_id="t1")
    s.scratch["atlas_last_node"] = {"label": "宇宙膨張" * 10, "status": "unvisited", "ledger_status": ""}
    ctx_view = actions_exec.learning_screen_context(s, "t1")["view"]
    assert ctx_view["atlas_node_label"] == ("宇宙膨張" * 10)[:40] and ctx_view["atlas_node_status"] == "unvisited"
    assert "atlas_node_ledger_status" not in ctx_view and "personal_map_seen" not in ctx_view


# --- 面 C（atlas）--------------------------------------------------------------------------------
def test_placement_reason_label_note_and_source_label():
    p = {"reason": "Ia 型を扱う", "reason_label": "AIの要約", "reason_note": "理由に挙がる 「Ia」 は、引用には現れません。"}
    assert ax.placement_reason_lines(p, "  ") == ["  〔AIの要約〕Ia 型を扱う", "  理由に挙がる 「Ia」 は、引用には現れません。"]
    data = {"documents": [{"title": "論文", "placements": [dict(p, node_label="超新星", status="inferred",
                                                                 evidence=[{"quote": "We", "source_label": "論文の主張からの引用"}])]}]}
    lines = ax.paper_placement_section_lines(data)
    assert "      〔AIの要約〕Ia 型を扱う" in lines and "      論文の主張からの引用" in lines
    rr = vision5_exec.project_release_step2({"documents": [{"title": "論文", "placements": [dict(p, node_label="超新星")]}]})
    assert "〔AIの要約〕Ia 型を扱う" in rr and "引用には現れません" in rr


def test_threads_basis_lines_and_version_prefix():
    atlas = {"skeleton_version": "2026.1", "threads": {"available": True, "skeleton_version": "2026.1",
                                                       "items": [{"from_label": "A", "to_label": "B", "basis_line": "近いという推定です。"}]}}
    lines = ax.threads_lines(atlas)
    assert "  近いという推定です。" in lines and any("糸ごとの根拠" in ln for ln in lines)
    assert "糸ごとの根拠" in _js("atlas-threads-layer.js")
    head = ax.overlay_l1_lines({"domain_name": "宇宙物理", "skeleton_version": "2026.1", "levels": {}})[0]
    assert "地図 版 2026.1" in head and "骨格" not in head
    assert '"地図 版 "' in _js("atlas-overlay.js")


def test_edge_accept_sends_origin():
    seen: list = []
    cart = "/api/admin/cartridges/astrophysics/atlas"
    cand = {"edge_key": "edge|astrophysics|a|b", "from_id": "a", "to_id": "b", "from_label": "A", "to_label": "B",
            "origins": ["vector", "co_occurrence"], "decision": None}
    routes = {("GET", cart + "/edge-candidates"): {"candidates": [cand], "skeleton_version": "2026.1"},
              ("POST", cart + "/edge-candidates/decide"): {"ok": True}}

    def handler(request):
        seen.append(request)
        body = routes.get((request.method, request.url.path))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={"detail": "x"})

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["atlas_domain"] = "astrophysics"
    s.scratch["atlas_edges"] = {"candidates": [cand], "skeleton_version": "2026.1"}
    ctx = _Ctx(REGISTRY["admin.atlas.edges.accept"], {"pick": "1", "kind": "隣接"}, s, client)
    assert ax.execute("admin.atlas.edges.accept", ctx)
    post = [r for r in seen if r.method == "POST"]
    assert post and json.loads(post[-1].content)["origin"] == "co_occurrence"


# --- 面 D（studio）-------------------------------------------------------------------------------
def test_preview_split_payload_carries_topic_address():
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    s.scratch.update({"studio_course_id": "c1", "studio_draft": {"material": "本文", "spoken": ""},
                      "studio_topic": {"chapter_index": 0, "topic_index": 2, "topic": {"id": "t3"}}})
    payload = stx._draft_split_payload(s)
    assert payload["course_id"] == "c1" and payload["topic_id"] == "t3"
    assert payload["chapter_index"] == 0 and payload["topic_index"] == 2 and payload["auto_paginate"] is True


def test_adopt_notice_parts_and_owner_notice():
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    parts = {"lead": "図を入れました。", "audio": "音声は作り直しになります。", "delivery": "すぐ届きます。"}
    s.scratch.update({"studio_structure": {"figure_adopt_notice_parts": parts},
                      "studio_topic": {"topic": {"id": "t1", "has_topic_audio": False}}})
    assert stx.adopt_notice(s) == "図を入れました。 すぐ届きます。"
    s.scratch["studio_topic"]["topic"]["has_topic_audio"] = True
    assert stx.adopt_notice(s) == "図を入れました。 音声は作り直しになります。 すぐ届きます。"
    lines = stx._course_lines({"title": "ゼミ", "share_mark": "共有（編集可）", "owner_notice": "このコースの所有者: 岡村"}, {})
    assert "共有（編集可） このコースの所有者: 岡村" in lines
