"""第 25 周 第 2 周の後の投影の同期（H13〜H16 ほか）— 投影が製品の画面と同じ欄・同じ形を写すことを固定する。

各面の正本の JS 関数名は投影関数の docstring にある。MockTransport のみ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

from uxsim.runner import atlas_exec as ax
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.tests.test_round25_atlas_harness import CART, GAP, _opened, _routes, _run  # noqa: E402


# ---------------------------------------------------------------------------
# 面 C（分野の地図）
# ---------------------------------------------------------------------------
def test_draft_from_frozen_is_not_executed_when_button_absent():
    """H13: [現在の版から次版の下書きを作る] は採用済み ∧ 下書きなしのときだけカードに出る。"""
    seen: list = []
    s, c = _opened(_routes(), seen)  # 候補は未判断
    seen.clear()
    traces = _run("admin.atlas.gaps.draft_from_frozen", {}, s, c)
    assert not any(r.method == "POST" for r in seen)
    assert traces and traces[-1].error == "precondition:draft_from_frozen_button"
    out = project_observation("admin.atlas.gaps.draft_from_frozen", s.last_status, s.last_body)
    assert "行為は行われていない" in out


def test_draft_from_frozen_runs_when_accepted_and_no_draft():
    accepted = dict(GAP, decision={"status": "accepted"})
    routes = _routes({("GET", CART + "/gap-candidates"): {"candidates": [accepted], "skeleton_version": "2026.1",
                                                         "draft_exists": False},
                      ("POST", CART + "/skeleton/draft/from-frozen"): {"ok": True}})
    seen: list = []
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.gaps.draft_from_frozen", {}, s, c)
    assert any(r.method == "POST" and r.url.path.endswith("/draft/from-frozen") for r in seen)


def test_fold_cards_never_lists_empty_titles():
    """H14: 字数が尽きても畳んだ候補の題名を最低 1 件は出す。"""
    cards = [("A" * 10, ["x" * 50]), ("長い題名の候補", ["y" * 50])]
    out = ax.fold_cards(cards, frozenset(), 40, "関係の候補")
    assert out[-1].startswith(ax.FOLD_HEAD.format(what="関係の候補") + "長い題名の候補")
    assert ": 、" not in out[-1] and ": …" not in out[-1]


def test_learner_atlas_view_is_overlay_l1_not_dump():
    """H15: 学習者の分野の地図は L1（領域 → 概念・pill）で、キー名をダンプしない。"""
    atlas = {"domain_name": "宇宙物理", "skeleton_version": "2026.1", "provenance": "AI生成・教員レビュー済",
             "nodes": {"c1": {"label": "降着", "status": "unvisited", "pill": "未記帳",
                              "pill_note": "この場所について…"},
                       "r2": {"label": "重力波", "status": "fog", "pill": "霧", "pill_note": "まだ…"}},
             "levels": {"1": {"svgDesc": "灯りの領域と霧の領域、状態つきノード",
                              "regions": [{"id": "r1", "label": "降着円盤", "kind": "lit"},
                                          {"id": "r2", "label": "重力波", "kind": "fog"}],
                              "nodes": [{"id": "c1", "label": "降着", "region": "r1", "x": 1, "y": 2}]}}}
    lines = "\n".join(ax.overlay_l1_lines(atlas))
    assert "分野の地図（宇宙物理・地図 版 2026.1・AI生成・教員レビュー済）" in lines
    assert "- 降着円盤:" in lines and "○ 降着（未記帳）" in lines
    assert "- 重力波（霧のハッチ — 論文未投入の地形）" in lines
    assert "未記帳: この場所について…" in lines and "skeleton_version" not in lines and "x:" not in lines


def test_learner_atlas_view_handler_projects_overlay():
    from uxsim.tests.test_round25_atlas_harness import _client
    atlas = {"domain_name": "宇宙物理", "skeleton_version": "1", "nodes": {},
             "levels": {"1": {"regions": [{"id": "r1", "label": "降着円盤", "kind": "lit"}], "nodes": []}}}
    seen: list = []
    c = _client({("GET", "/api/atlas"): atlas}, seen)
    s = PersonaSession(persona_id="st-04", role="STUDENT", course_id="c1", topic_id="t1")
    _run("learning.atlas.view", {}, s, c)
    out = project_observation("learning.atlas.view", s.last_status, s.last_body)
    assert "- 降着円盤:" in out and "levels" not in out


def test_language_oracle_ignores_atlas_content_labels():
    """H16: 骨格の label（内容）は言語一致の対象にしない。"""
    from uxsim.oracles import principle
    from uxsim.schema import HttpTrace, TranscriptStep
    import json
    body = json.dumps({"nodes": {"c1": {"label": "降着円盤の物理"}}}, ensure_ascii=False)
    step = TranscriptStep(seq=1, persona_id="st-en", screen="learning", action_id="learning.atlas.view",
                          http=[HttpTrace(method="GET", path="/api/atlas", status=200, response_excerpt=body)])
    from uxsim.tests.test_round25_atlas_harness import _factory
    orig = principle._persona_language
    principle._persona_language = lambda d, p: "en"  # type: ignore[assignment]
    try:
        found = principle.check([step], _factory())
    finally:
        principle._persona_language = orig  # type: ignore[assignment]
    assert not any("言語不一致" in f.hypothesis for f in found)


# ---------------------------------------------------------------------------
# 面 B（D層・SL層）
# ---------------------------------------------------------------------------
from uxsim.config import REPO_ROOT  # noqa: E402
from uxsim.runner import doubt_exec  # noqa: E402
from uxsim.tests import test_r25_doubt_harness as dh  # noqa: E402

_JS = REPO_ROOT / "frontend" / "public" / "js"


def test_doubt_mirrored_texts_exist_in_js():
    doubt_js = (_JS / "doubt-atlas.js").read_text(encoding="utf-8")
    app_js = (_JS / "app.js").read_text(encoding="utf-8")
    assert doubt_exec.BATCH_FAIL in doubt_js and "仮に偽にしている前提: " in doubt_js
    assert doubt_exec.LEARNER_OPEN_SUMMARY in app_js and doubt_exec.LEARNER_OPEN_FAIL in app_js


def test_doubt_empty_atlas_and_open_use_server_notes():
    atlas = {"course_id": "c1", "points": [], "empty_note": "範囲が空です。", "empty_hint": "スタジオからも開けます。"}
    opened = {"course_id": "c1", "items": [], "empty_note": "対象はありません（範囲あり）。"}
    seen: list = []
    s = dh._session()
    h = dh._admin_handler({("GET", "/api/admin/doubt/courses/c1/assumption-atlas"): atlas,
                           ("GET", "/api/admin/doubt/courses/c1/open-assumptions"): opened})
    dh._run("admin.doubt.tab_open", {}, s, dh._client(h, seen))
    obs = dh._obs(s)
    assert "範囲が空です。" in obs and "スタジオからも開けます。" in obs and "対象はありません（範囲あり）。" in obs


def test_doubt_batch_shows_server_note_in_status_line():
    seen: list = []
    s = dh._session()
    h = dh._admin_handler({("POST", "/api/admin/doubt/courses/c1/corpus-audit/run"):
                           {"ok": True, "skipped": True, "note": "論文が少なく実行しませんでした。"}})
    c = dh._client(h, seen)
    dh._run("admin.doubt.tab_open", {}, s, c)
    dh._run("admin.doubt.audit_run", {}, s, c)
    assert "（ボタンの下の状態行: 論文が少なく実行しませんでした。）" in dh._obs(s)


def test_cf_view_lists_names_and_warnings():
    cf = {"toggled": ["n1"], "observations": [], "labels": {"n1": "等分配が成り立つ\n(前提)"}}
    out = doubt_exec.cf_view(cf, {"collapsed_node_ids": [], "indeterminate_node_ids": [], "node_labels": {},
                                  "warnings": ["グラフ上で見つからなかったため、依存する範囲を試算できなかった対象: X"]})
    assert "仮に偽にしている前提: 等分配が成り立つ［すべて解除］" in out and "件" not in out.split("\n")[1]
    assert "試算できなかった対象: X" in out
    cf2 = {"toggled": ["n9"], "observations": []}
    assert "仮に偽にしている前提: 結果の名前" in doubt_exec.cf_view(cf2, {"toggled_labels": ["結果の名前"]})
    assert "保存しました（共有範囲: 非公開）" in doubt_exec.cf_view(cf, None, status_text="保存しました（共有範囲: 非公開）")


def test_seminar_brief_empty_notes_and_shared_scope_label():
    out = doubt_exec.seminar_brief_view({"available": True, "fragile_assumptions": [],
                                         "empty_notes": {"fragile_assumptions": "このコーパスの中では該当がありません。"}})
    assert "〔脆い前提〕\nこのコーパスの中では該当がありません。" in out and "〔晴れ間〕" not in out
    lines = doubt_exec.cf_session_lines({"items": [{"shared_scope": "private", "shared_scope_label": "非公開"}]})
    assert "［非公開］" in lines[1] and "private" not in lines[1]


def test_learner_open_assumptions_fail_vs_empty_and_target_note():
    import httpx
    s = PersonaSession(persona_id="st-06", course_id="c9", topic_id="t2")
    seen: list = []

    def h(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/open-assumptions"):
            return httpx.Response(200, json={"items": [], "empty_note": "まだ一覧に載っている前提がありません。"})
        if r.url.path.endswith("/ledger/component/u1"):
            return httpx.Response(200, json={"fact_line": "記帳なし。", "scopes": [], "target_note": "（段の記録）"})
        return httpx.Response(500, json={})
    c = dh._client(h, seen)
    dh._run("learning.ledger.open_assumptions", {}, s, c)
    assert "まだ一覧に載っている前提がありません。" in dh._obs(s)

    from uxsim.actions.registry import REGISTRY
    from uxsim.runner.actions_exec import _Ctx
    ctx = _Ctx(REGISTRY["learning.ledger.open_assumptions"], {}, s, c)
    assert doubt_exec._learner_line(ctx, "component", "u1") == "記帳なし。 （段の記録）"
    s2 = PersonaSession(persona_id="st-06", course_id="c9", topic_id="t2")
    dh._run("learning.ledger.open_assumptions", {}, s2, dh._client(lambda r: httpx.Response(500, json={}), []))
    assert doubt_exec.LEARNER_OPEN_FAIL in dh._obs(s2)


# ---------------------------------------------------------------------------
# 面 A（共有・ライブラリ・学生の部品と記号）
# ---------------------------------------------------------------------------
from uxsim.runner import share_exec  # noqa: E402


def test_share_mirrored_texts_exist_in_js():
    admin_js = (_JS / "admin.js").read_text(encoding="utf-8")
    for text in (share_exec.RELATION_OBJECT_PLACEHOLDER, share_exec.RELATION_SELECT_EMPTY,
                 share_exec.RELATION_SEARCH_NONE, share_exec.RELATION_SEARCH_FAIL, share_exec.ALIASES_MOVED_NOTE,
                 share_exec.DS_SHARE_NOTE):
        assert text in admin_js, text


def test_library_detail_uses_display_labels():
    out = share_exec.entry_detail_view({"id": "e1", "name": "TOV", "domain_key": "astrophysics", "domain_label": "宇宙物理",
                                        "entry_type": "concept", "version_line": "凍結版 2", "updated_by_label": "堀",
                                        "aliases": ["x"], "review_status": "candidate",
                                        "review_status_note": "候補のままでは届きません。", "revision": 7},
                                       None, None, None, "")
    assert out.startswith("宇宙物理 ・ ") and "凍結版 2 ・ 更新: 堀" in out and "revision" not in out
    assert share_exec.ALIASES_MOVED_NOTE in out and "カンマ区切り" not in out and "候補のままでは届きません。" in out
    old = share_exec.entry_detail_view({"id": "e1", "name": "TOV", "domain_key": "astrophysics",
                                        "latest_version_no": 3}, None, None, None, "")
    assert old.startswith("astrophysics ・ ") and "版 3" in old  # 旧サーバのフォールバック


def test_symbol_popover_equations_and_definition_latex():
    out = share_exec.symbol_popover_view(200, {"available": True, "symbol": "P",
                                               "definition": {"text": "式で定義", "latex": "P=K\\rho^2"},
                                               "facts": ["事実"], "equations": [{"name": "式 (3)", "latex": "a=b"}]}, "P")
    assert "$P=K\\rho^2$" in out and "式 (3)\n$a=b$" in out


def test_symbol_text_drops_superscripts():
    assert share_exec.symbol_text("r^2") == "r" and share_exec.symbol_text("c_s^2") == "c_s"


def test_shared_part_note_and_explanation_note():
    body = {"instance": {"component": {"label": "TOV"}}, "shared_part": None, "shared_part_note": "まだ共通部品ではありません。"}
    assert "まだ共通部品ではありません。" in share_exec.shared_pane_view(body)
    assert "まだ共通部品ではありません。" in project_observation("learning.component.context", 200, body)
    assert share_exec.explanations_view({"explanations": [], "note": "承認された説明がありません（理由）。"}) \
        == "承認された説明がありません（理由）。"


# ---------------------------------------------------------------------------
# 面 D（原稿スタジオ・講義）
# ---------------------------------------------------------------------------
import copy  # noqa: E402

from uxsim.runner import studio_exec as sx  # noqa: E402
from uxsim.tests import test_r25_studio as st  # noqa: E402


def test_studio_mirrored_texts_exist_in_js():
    js = (_JS / "admin-lecture-studio.js").read_text(encoding="utf-8")
    assert sx.REWRITE_SKIPPED in js and sx.STUMBLE_TOPIC_UNRESOLVED in js


def _new_structure():
    structure = copy.deepcopy(st.STRUCTURE)
    structure.update({"topic_audio_status": {"status": "some", "message": "一部のトピックに音声があります。"},
                      "topic_audio_save_notice": "保存するとこのトピックの音声は消えます。",
                      "documents": [{"document_id": "d1", "title": "論文"}],
                      "generated_appendix_headings": ["## 式の一覧"]})
    t2 = structure["chapters"][0]["topics"][1]
    t2.update({"evidence_state": {"status": "none", "label": "根拠なし", "message": "根拠候補はありません（前）。"},
               "has_topic_audio": True, "document_ids": ["d1"]})
    return structure


def test_studio_course_and_topic_use_server_states():
    seen: list = []
    routes = st._teacher_routes()
    import copy

    before, after = _new_structure(), copy.deepcopy(_new_structure())
    for ch in after["chapters"]:
        for t in ch["topics"]:
            t["has_topic_audio"] = False  # 保存で音声が消えた後の読み直し（第 2 波 lsRefreshTopicServerFacts）
    saved = {"done": False}

    def structure(_req):
        return after if saved["done"] else before

    def put(_req):
        saved["done"] = True
        return {"status": "edited", "audio_invalidated": True, "notice": "音声を消しました。"}

    routes[("GET", "/lecture-studio/course-structure")] = structure
    routes[("PUT", "/lecture-studio/course-topics/")] = put
    c = st._client(seen, routes)
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    st._run("admin.lecture_studio.open_course", {}, s, c)
    shown = sx.project("admin.lecture_studio.open_course", s.last_status, s.last_body)
    assert "コース: 論文ゼミ（一部のトピックに音声があります。）" in shown
    st._run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, c)
    shown = sx.project("admin.lecture_studio.select_topic", s.last_status, s.last_body)
    assert "根拠リンク: [根拠なし] 根拠候補はありません（前）。" in shown and "[none]" not in shown
    assert "保存するとこのトピックの音声は消えます。" in shown
    traces = st._run("admin.lecture_studio.save_topic", {}, s, c)
    shown = sx.project("admin.lecture_studio.save_topic", s.last_status, s.last_body)
    assert "授業用ドラフトを保存しました。音声を消しました。" in shown
    dig = next(t.digest["studio_saved"] for t in traces if "studio_saved" in t.digest)  # 第 2 波: 保存後に構造を読み直す
    assert dig["notice_shown_before"] and dig["notice_shown_after"]
    assert s.scratch["studio_topic"]["topic"]["has_topic_audio"] is False


def test_rewrite_skipped_and_unchanged():
    seen: list = []
    routes = st._teacher_routes()
    routes[("POST", "/rewrite")] = {"skipped": True, "changed": False, "notice": "根拠の材料がありません。"}
    c = st._client(seen, routes)
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    st._run("admin.lecture_studio.open_course", {}, s, c)
    st._run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, c)
    before = dict(s.scratch["studio_draft"])
    traces = st._run("admin.lecture_studio.rewrite_topic", {"prompt": "短く"}, s, c)
    shown = sx.project("admin.lecture_studio.rewrite_topic", s.last_status, s.last_body)
    assert "根拠の材料がありません。" in shown and s.scratch["studio_draft"] == before
    rw = next(t.digest["studio_rewrite"] for t in traces if "studio_rewrite" in t.digest)
    assert rw["llm_called"] is False and rw["reported_applied"] is False


def test_indicator_notes_and_paragraph_insert():
    split = {"display_count": 2, "spoken_count": 2, "auto_paginate_suppressed": True, "notes": ["図があるので分けません。"]}
    assert sx.indicator_text(split).endswith(" — 図があるので分けません。")
    assert sx.indicator_text({"display_count": 2, "spoken_count": 1, "mismatch": True, "notes": ["理由。"]}) \
        .endswith("1枚に統合して配信されます。理由。")
    mat = "本文\n\n## 式の一覧\n- 式"
    assert sx.appendix_start(mat, ["## 式の一覧"]) == mat.index("## 式の一覧")
    assert sx.as_paragraph("前", 1, "X") == "\n\nX\n"


def test_lecture_toggle_with_slides_but_no_audio():
    seen: list = []
    c = st._client(seen, {("GET", "/audio-status"): {"has_audio": False, "total_slides": 2,
                                                       "lecture_note": "音声はありません。スライドは自動で送られます。"},
                          ("GET", "/sequence"): {"total_slides": 2, "segments": [{"chunk_id": "topic:t2", "slides": [
                              {"slide_index": 0, "display_text": "一", "has_audio": False}]}]}})
    s = st._student_session()
    st._run("learning.lecture.audio_status", {}, s, c)
    shown = sx.project("learning.lecture.audio_status", s.last_status, s.last_body)
    assert "「🎙 講義」は押せる（音声なし" in shown and "音声はありません。スライドは自動で送られます。" in shown
    st._run("learning.lecture.sequence", {}, s, c)
    assert any("/sequence" in r.url.path for r in seen)
    s2 = st._student_session()
    st._run("learning.lecture.audio_status", {}, s2, st._client([], {("GET", "/audio-status"): {
        "has_audio": False, "total_slides": 0, "lecture_note": "このトピックには講義がありません。"}}))
    shown = sx.project("learning.lecture.audio_status", s2.last_status, s2.last_body)
    assert "押せない" in shown and "講義なし" in shown and sx.LECTURE_DISABLED_TITLE not in shown
