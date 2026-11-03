"""第 25 周 第 3 周の偽所見の是正（runs/round25/harness_fixes_{share,doubt,atlas,studio}.md の第 3 周の節）。

外部呼び出しなし（MockTransport・純関数）。各テストの見出しは H の番号。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import doubt as doubt_oracle
from uxsim.oracles import observation as obs_oracle
from uxsim.oracles import share as share_oracle
from uxsim.oracles import studio as studio_oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import actions_exec, atlas_exec as ax, doubt_exec, share_exec, studio_exec, vision5_exec
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import OBSERVATION_CHARS, PersonaSession, journey_lines, project_observation
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

ROOT = Path(__file__).resolve().parents[2]


def _factory():
    return FindingFactory(RunMeta(run_id="r", campaign_id="c", domain="astrophysics", snapshot="s"))


def _t(method, path, status, body):
    return HttpTrace(method=method, path=path, status=status, response_excerpt=json.dumps(body, ensure_ascii=False))


# ---------------------------------------------------------------------------
# atlas
# ---------------------------------------------------------------------------
def test_atlas_h18_decided_cards_are_not_folded():
    cards = [(f"c{i}", [f"{i}. c{i}", "x" * 200]) for i in range(1, 6)]
    keep = [False, False, False, True, False]
    out = ax.fold_cards(cards, frozenset(), 300, "関係の候補", keep=keep)
    text = "\n".join(out)
    assert "4. c4" in text  # 判断済みは予算を越えても全文
    assert "5. c5" not in text and ax.FOLD_HEAD.format(what="関係の候補") in text  # 未判断は題名の列挙に畳む


def test_atlas_h18_decided_card_predicate():
    assert ax.decided_card({"decision": {"status": "accepted"}})
    assert ax.decided_card({"in_draft": True})
    assert not ax.decided_card({"decision": {"status": "candidate"}})


def test_atlas_h19_show_all_renumbers_in_display_order_once():
    s = PersonaSession(persona_id="te", role="TEACHER")
    first = [{"edge_key": k} for k in ("a", "b", "c")]
    ax.card_numbers(s, "edge", first, "edge_key")
    ax.card_numbers(s, "edge", [{"edge_key": "z"}], "edge_key")  # 末尾の番号 4 を受け取る
    ax.renumber_cards(s, "edge", [{"edge_key": k} for k in ("a", "z", "b", "c")], "edge_key")
    table = s.scratch["atlas_edge_numbers"]
    assert [table[k] for k in ("a", "z", "b", "c")] == [1, 2, 3, 4]
    assert s.scratch["atlas_edge_numbers_previous"]["z"] == 4


def test_atlas_h20_runner_notes_carry_harness_mark():
    note = ax.harness_note("（指した候補が一覧に無い）")
    assert note.startswith("（ハーネス: 指した候補が一覧に無い") and note.endswith("これは画面の文ではない）")
    src = (ROOT / "uxsim" / "runner" / "atlas_exec.py").read_text(encoding="utf-8")
    assert 'head=["（指した候補が一覧に無い）"]' not in src


def test_atlas_h20_incorporate_preview_falls_back_to_orphaned():
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch["atlas_domain"] = "astrophysics"
    s.scratch["atlas_gaps"] = {"candidates": [], "draft_exists": True,
                               "orphaned_decisions": [{"cluster_key": "k1", "proposed_label": "星間磁場",
                                                       "decision": {"status": "accepted"}}]}
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"patched_draft": {"x": 1}, "summary": "追加します", "timing_line": ""})

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    called = {}
    orig = ax._gap_orphan_incorporate
    ax._gap_orphan_incorporate = lambda ctx: called.setdefault("ok", ctx.args.get("gap_hint"))
    try:
        ctx = _Ctx(REGISTRY["admin.atlas.gaps.incorporate_preview"], {}, s, client)
        ctx.session.scratch["atlas_domain"] = "astrophysics"
        orig_need = ax._need_domain
        ax._need_domain = lambda c: True
        try:
            ax._gap_incorporate_preview(ctx)
        finally:
            ax._need_domain = orig_need
    finally:
        ax._gap_orphan_incorporate = orig
    assert called.get("ok")  # 別枠の判断へ回った（番号で指して）


def test_atlas_h22_threads_projection_has_no_key_dump():
    body = {"threads": {"skeleton_version": "2026.1", "items": [{"from_label": "A", "to_label": "B",
                                                                  "nearness_label": "近い可能性"}]},
            "skeleton_version": "2026.1", "cartridge": "astrophysics", "nodes": {"n1": {"label": "x", "pill_note": "y"}}}
    obs = project_observation("learning.atlas.threads", 200, body)
    assert "A ⋯ B" in obs and "cartridge" not in obs and "pill_note" not in obs and "nodes" not in obs


def test_atlas_h23_course_list_has_no_ids_and_hint_resolves_by_number_or_title():
    rows = [{"id": "1bad7d9f-0000", "title": "宇宙物理 論文紹介ゼミ", "is_enrollable": False},
            {"id": "6be229e1-0000", "title": "宇宙物理 論文紹介ゼミ（PDF）", "is_enrollable": True}]
    obs = project_observation("learning.course.list", 200, rows)
    assert "id=" not in obs and "1bad7d9f" not in obs and "1. 宇宙物理 論文紹介ゼミ" in obs
    s = PersonaSession(persona_id="st")
    actions_exec.remember_course_rows(s, rows)
    assert actions_exec.resolve_course_hint(s, "2") == "6be229e1-0000"
    assert actions_exec.resolve_course_hint(s, "宇宙物理 論文紹介ゼミ") == "1bad7d9f-0000"
    assert actions_exec.resolve_course_hint(s, "1bad7d9f") == "1bad7d9f-0000"
    assert actions_exec.resolve_course_hint(s, "無い題名") == "無い題名"


def test_atlas_h24_observation_e_filters_by_run_users():
    steps = [TranscriptStep(seq=1, persona_id="te-05-delegator-lab", action_id="auth.login")]
    assert obs_oracle.run_usernames(steps) == ["uxsim_te_05_delegator_lab"]
    assert obs_oracle.run_usernames([]) == []


# ---------------------------------------------------------------------------
# doubt
# ---------------------------------------------------------------------------
def test_doubt_h19_scatter_folds_same_text_and_respects_budget():
    pts = [{"label": "同じ前提", "scope_coverage": "none", "load_level": "low", "target_type": "assumption"}] * 30
    pts += [{"label": f"別の点{i}", "scope_coverage": "none", "load_level": "low"} for i in range(60)]
    lines = doubt_exec.atlas_lines({"points": pts}, budget=1200)
    text = "\n".join(lines)
    assert "（ほか、同じ文面の点: 点2・点3" in text
    assert lines[-1] == doubt_exec.ATLAS_POINTS_CLIPPED and len(text) <= 1200 + len(doubt_exec.ATLAS_POINTS_CLIPPED)


def test_doubt_h19_tab_view_keeps_sessions_inside_the_observation():
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch.update({"doubt_courses": [{"id": "c1", "title": "コース"}], "doubt_course_id": "c1",
                      "doubt_atlas": {"points": [{"label": f"点の名前{i}" * 4, "scope_coverage": "none",
                                                  "load_level": "low"} for i in range(200)]},
                      "doubt_cf_sessions": {"items": [{"shared_scope_label": "非公開（自分のみ）", "owner_name": "浦川",
                                                       "notes": "保存した反実仮想"}]}})
    text = doubt_exec.tab_view(s)
    assert "保存した反実仮想" in text and len(text) < OBSERVATION_CHARS


def test_doubt_h23_opening_keeps_paper_unverified_section():
    docs = [{"title": f"P{i}", "document_id": str(i), "thesis": {"central_thesis_text": "T" * 1500},
             "chapter_skeleton": [{"label": "章", "summary": "s" * 200}] * 12} for i in range(4)]
    view = vision5_exec.discuss_opening_view({"documents": docs, "paper_unverified_empty_note": "閉世界の一文"})
    assert view.endswith("閉世界の一文") and vision5_exec.OPENING_DOC_CLIPPED in view
    assert len(view) < OBSERVATION_CHARS


def test_doubt_h25_recorder_rule_reads_label():
    uuid = "2b1f0c3e-1111-2222-3333-444455556666"
    path = "/api/admin/doubt/ledger/claim/c1"
    ok = TranscriptStep(seq=1, persona_id="te-02", action_id="admin.doubt.ledger_open", screen="admin",
                        http=[_t("GET", path, 200, {"verification_scopes": [
                            {"recorded_by": uuid, "recorded_by_label": "浦川"}]})])
    bad = TranscriptStep(seq=2, persona_id="te-02", action_id="admin.doubt.ledger_open", screen="admin",
                         http=[_t("GET", path, 200, {"verification_scopes": [{"recorded_by": uuid}]})])
    hyps = lambda steps: [f.hypothesis for f in doubt_oracle.check(steps, _factory())]  # noqa: E731
    assert not any("利用者の内部 ID" in h for h in hyps([ok]))
    assert any("利用者の内部 ID" in h for h in hyps([bad]))


def test_doubt_h26_e_rule_uses_course_documents_helpers():
    steps = [TranscriptStep(seq=1, persona_id="te", action_id="admin.doubt.tab_open", screen="admin",
                            http=[_t("GET", "/api/admin/doubt/courses/c1/atlas", 200, {})]),
             TranscriptStep(seq=2, persona_id="te", action_id="admin.doubt.ledger_open", screen="admin",
                            http=[HttpTrace(method="GET", path="/api/admin/doubt/ledger/claim/x", status=200,
                                            digest={"doubt_ledger": {"requested_document_id": "d1",
                                                                     "response_document_id": "d1", "target_id": "x"}})])]
    assert doubt_oracle.run_doubt_courses(steps) == ["c1"]
    assert doubt_oracle.ledger_documents_by_target(steps) == {"x": "d1"}
    src = (ROOT / "uxsim" / "oracles" / "doubt.py").read_text(encoding="utf-8")
    assert "COALESCE(course_id" not in src  # course_id 列で範囲を見ない


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------
def test_share_h22_identity_empty_shows_only_facts():
    view = share_exec.identity_candidates_view({"candidates": [], "facts": ["事実の一文。"]})
    assert view.count("事実の一文。") == 1 and share_exec.IDENTITY_EMPTY not in view


def test_share_h25_journey_projects_screen_only():
    body = {"steps": [{"fact": "主張から理論構成へ", "ref": {"kind": "graph_node"}}], "truncated": False}
    text = "\n".join(journey_lines(body))
    assert "truncated" not in text and "主張から理論構成へ［理論構成］" in text and "（途中まで）" not in text
    assert "（途中まで）" in "\n".join(journey_lines({**body, "truncated": True}))
    obs = project_observation("learning.personal_network.journey", 200, {"steps": [], "notice": "まだ旅に出られません",
                                                                          "facts": ["範囲の事実"]})
    assert "まだ旅に出られません" in obs and "範囲の事実" in obs and "facts" not in obs


def test_share_h26_unbraced_subscript_command():
    assert share_exec.symbol_text(r"\mathcal{L}_\mathrm{BC}") == "L_BC"
    assert share_exec.symbol_text(r"\mathcal{L}_{\mathrm{BC}}") == "L_BC"
    assert share_exec.symbol_text(r"L_\rm X") == "L_X"


def test_share_h27_copilot_navigate_button():
    obs = project_observation("admin.copilot.chat", 200, {"answer": "ライブラリのタブです。",
                                                           "next_actions": [{"type": "navigate", "screen": "library"}]})
    assert "ボタン: そのタブを開く" in obs and "ボタン: \n" not in obs + "\n"


def test_share_h28_endorsers_view():
    body = {"endorsements": [{"endorser_name": "浦川", "expertise_tag": "天体", "level": "strong",
                              "role_label": "（著者自身）"}], "support_note": "著者自身の支持は数えません。"}
    view = share_exec.endorsers_view("説明", body)
    assert "浦川（専門: 天体） [強い支持] （著者自身）" in view and "著者自身の支持は数えません。" in view
    assert share_exec.ENDORSERS_EMPTY in share_exec.endorsers_view("説明", {"endorsements": []})
    assert "admin.explanations.endorsers" in REGISTRY
    card = "\n".join(share_exec.explanation_card_lines(1, {"title": "t"}))
    assert "支持した教員の一覧（開くと読み込みます）" in card


def test_share_h29_pick_component_exact_then_ambiguous():
    comps = [{"id": "1", "name": "Define: TOV residual loss"}, {"id": "2", "name": "TOV network map"},
             {"id": "3", "name": "TOV"}]
    assert share_exec.pick_component(comps, "TOV")[0]["id"] == "3"
    hit, many = share_exec.pick_component(comps[:2], "TOV")
    assert hit is None and {c["id"] for c in many} == {"1", "2"}
    assert share_exec.pick_component(comps, "network")[0]["id"] == "2"


def test_share_a12_identity_recreate_oracle():
    path = "/api/admin/deliberation/identity-links"
    s1 = TranscriptStep(seq=1, persona_id="te", action_id="admin.deliberation.identity_link_create", screen="admin",
                        http=[_t("POST", path, 201, {"id": "L1"})], observation=share_exec.DLB_CREATED)
    s2 = TranscriptStep(seq=2, persona_id="te", action_id="admin.deliberation.identity_link_create", screen="admin",
                        http=[_t("POST", path, 200, {"id": "L1"})], observation=share_exec.DLB_CREATED)
    found = share_oracle.check([s1, s2], _factory())
    assert any("新しく作成したと言っている" in f.hypothesis for f in found)
    assert not any("新しく作成したと言っている" in f.hypothesis for f in share_oracle.check([s1], _factory()))


def test_share_label_dismiss_action_registered_and_requires_reason():
    s = PersonaSession(persona_id="te", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV 方程式"}
    s.scratch["share_entries"] = [s.scratch["share_entry"]]
    s.scratch["share_entry_labels"] = [{"id": "l1", "label": "TOV", "kind": "hidden", "status": "confirmed"}]
    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    ctx = _Ctx(REGISTRY["admin.library.label_dismiss"], {"label": "TOV", "entry_hint": "TOV 方程式"}, s, client)
    share_exec._label_dismiss(ctx)
    assert s.last_body.get("_precondition") == "dismiss_reason"


# ---------------------------------------------------------------------------
# studio
# ---------------------------------------------------------------------------
def test_studio_h18_readiness_uses_course_meta():
    st = {"title": "コース", "course_status": "no_chunks",
          "topic_audio_status": {"status": "none", "message": "このコースのトピック音声はまだ生成されていません。"}}
    assert studio_exec.course_meta(st) == "このコースのトピック音声はまだ生成されていません。"
    shown = studio_exec.project("admin.lecture_studio.audio_readiness", 200,
                                {"_studio": {"kind": "readiness", "state": "ready", "structure": st, "language": "ja"}})
    assert "チャンクなし" not in shown and "まだ生成されていません" in shown


def test_studio_h19_chunk_miss_never_prints_empty_quotes():
    shown = studio_exec.project("admin.lecture_studio.chunk_slides", 200, {"_studio": {"kind": "chunk_miss", "hint": ""}})
    assert "「」" not in shown and "ハーネス:" in shown
    shown = studio_exec.project("admin.lecture_studio.chunk_slides", 200, {"_studio": {"kind": "chunk_list_empty"}})
    assert "教材のチャンクが見つかりません。" in shown


def test_studio_h22_units_only_message_mirrors_backend():
    src = (ROOT / "backend" / "core" / "lecture_studio_state.py").read_text(encoding="utf-8")
    assert studio_exec.EVIDENCE_UNITS_ONLY_HEAD in src
    dg = studio_exec.coverage_digest({"id": "t1", "evidence_state": {"status": "weak",
                                                                     "message": studio_exec.EVIDENCE_UNITS_ONLY_HEAD + "…"}})
    assert dg["center_units_only"] is True
    step = TranscriptStep(seq=1, persona_id="te", action_id="admin.lecture_studio.topic_open", screen="admin",
                          http=[HttpTrace(method="GET", path="/x", status=200,
                                          digest={"studio_coverage": {"center_status": "weak",
                                                                      "center_units_only": True,
                                                                      "right_pane_has_items": False}})])
    assert not any("右ペインの根拠リンクの一覧は空" in f.hypothesis for f in studio_oracle.check([step], _factory()))
    step.http[0].digest["studio_coverage"]["center_units_only"] = False
    assert any("右ペインの根拠リンクの一覧は空" in f.hypothesis for f in studio_oracle.check([step], _factory()))
