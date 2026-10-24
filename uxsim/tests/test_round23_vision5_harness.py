"""第 23 周（vision 5 機能）のハーネス — 場面の読み込み・新しい行為・投影・前提 owns_course・審判 E6/E7/E8・B（督促）。

外部呼び出しなし（製品は httpx.MockTransport、DB は偽の行）。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import decision, differential, principle, retraction
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import vision5_exec
from uxsim.runner.actions_exec import _HANDLERS, execute
from uxsim.runner.api import check_preconditions, load_campaign
from uxsim.runner.client import EpistemeClient
from uxsim.runner.digest import response_digest
from uxsim.runner.scenario import ScenarioError, load_params, load_scenario, parse_scenario
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

UXSIM = Path(__file__).resolve().parents[1]
MANUAL = UXSIM.parent / "docs" / "manual"
STUDENTS = ["st-02-m2-lensing-theory", "st-05-night-solarwind", "st-06-skeptic-bao"]
TEACHERS = ["te-02-veteran-intro", "te-03-meticulous-seminar"]
NEW_ACTIONS = ["learning.chat.delete_message", "learning.traces.list", "learning.traces.map_exclude",
               "learning.traces.map_restore", "learning.records.export", "learning.disclosure.view",
               "learning.cycle.landing_candidates", "learning.reconstruction.revise", "admin.courses.list",
               "admin.release_review.row_status", "admin.release_review.read_evidence"]


def _archetype(persona_id: str) -> str:
    kind = "students" if persona_id.startswith("st-") else "teachers"
    data = yaml.safe_load((UXSIM / "domains" / "astrophysics" / "personas" / kind / f"{persona_id}.yaml").read_text())
    return str(data.get("archetype") or "")


# ---------------------------------------------------------------------------
# 場面の読み込み
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("campaign_file,who", [("c-vision5-students.yaml", STUDENTS), ("c-vision5-teachers.yaml", TEACHERS)])
def test_campaign_scenarios_load_for_every_persona(campaign_file, who):
    camp = load_campaign(UXSIM / "campaigns" / "astrophysics" / campaign_file)
    personas = (camp.get("personas") or {})
    assert sorted((personas.get("students") or []) + (personas.get("teachers") or [])) == sorted(who)
    ids = []
    for group in (camp.get("scenarios") or {}).values():
        ids += [e["id"] if isinstance(e, dict) else e for e in group]
    assert ids and all(i.startswith(("s-v5-", "t-v5-")) for i in ids)
    for sid in ids:
        sc = load_scenario(sid)
        for action in sc.referenced_actions():
            assert action in REGISTRY, (sid, action)
        for pid in who:
            params = load_params("astrophysics", sid, pid, archetype_id=_archetype(pid))
            assert sc.missing_params(params) == [], (sid, pid)
        for seed in sc.seed_scenarios:
            load_scenario(seed)
        data = yaml.safe_load((UXSIM / "scenarios" / "goal" / ("teacher" if sid.startswith("t-") else "student")
                               / f"{sid}.yaml").read_text())
        anchors = [e["manual"] for e in data.get("expects") or [] if isinstance(e, dict)]
        assert anchors and data.get("watch_for"), sid
        for a in anchors:
            path, frag = a.split("#")
            assert "{#" + frag + "}" in (MANUAL / path).read_text(encoding="utf-8"), a


def test_campaign_notes_carry_lane_dependencies_and_permission_difference():
    s = (UXSIM / "campaigns" / "astrophysics" / "c-vision5-students.yaml").read_text(encoding="utf-8")
    t = (UXSIM / "campaigns" / "astrophysics" / "c-vision5-teachers.yaml").read_text(encoding="utf-8")
    assert "s-v5-landscape-check" in s and "t-v5-release-skip-step2" in s  # (5c) の前後比較
    assert "6be229e1" in t and "te-01" in t  # 代替案
    assert "te-02 は閲覧のみ" in t and "te-03 は editor あり" in t
    assert (UXSIM / "runs" / "run23_lane.sh").is_file()


@pytest.mark.parametrize("action_id", NEW_ACTIONS)
def test_new_actions_have_handlers(action_id):
    assert action_id in _HANDLERS and not REGISTRY[action_id].unsupported


def test_owns_course_precondition_vocabulary():
    sc = load_scenario("t-v5-release-meticulous")
    assert sc.precondition_checks[0]["check"] == "owns_course" and sc.seed_scenarios == ["t-v5-register-own-course"]
    with pytest.raises(ScenarioError):
        parse_scenario({"id": "t-x", "population": "teachers", "goal": "g", "steps": [{"do": "admin.courses.list"}],
                        "preconditions": [{"text": "x", "check": "owns_course", "requires": ["published"]}]})


# ---------------------------------------------------------------------------
# 行為（MockTransport）
# ---------------------------------------------------------------------------
def _client(handler, seen):
    def wrap(request):
        seen.append(request)
        return handler(request)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _body(r):
    return json.loads(r.content.decode("utf-8")) if r.content else {}


def _ok(payload=None):
    return lambda r: httpx.Response(200, json=payload if payload is not None else {"ok": True})


def test_precision_toggle_is_ui_only_and_leaves_runner_note():
    s = PersonaSession(persona_id="st", course_id="c1")
    seen = []
    traces = execute("learning.cycle.precision_toggle", {}, s, _client(_ok(), seen))
    assert not seen and traces[0].path == "unsupported:learning.cycle.precision_toggle"
    obs = project_observation("learning.cycle.precision_toggle", s.last_status, s.last_body)
    assert "論文と議論" in obs and "押せない" in obs
    assert any(n.startswith("unsupported_ui_only:") for n in s.scratch["runner_notes"])


def test_delete_message_targets_last_user_message_and_truncates():
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t1")
    h = s.history_for("t1")
    h += [{"role": "user", "content": "q1", "id": "m1"}, {"role": "assistant", "content": "a1"},
          {"role": "user", "content": "q2", "id": "m2"}, {"role": "assistant", "content": "a2"}]
    seen = []
    execute("learning.chat.delete_message", {"nth_from_last": 1}, s, _client(_ok({"status": "deleted"}), seen))
    assert seen[-1].method == "DELETE" and seen[-1].url.path == "/api/learning/courses/c1/topics/t1/chat/messages/m2"
    assert [m.get("id") for m in h if m["role"] == "user"] == ["m1"]
    assert "このメッセージ以降の会話を削除します" in project_observation("learning.chat.delete_message", s.last_status, s.last_body)


def test_rewrite_in_discussion_uses_discussion_topic_and_notes_target():
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t1")
    s.history_for("_discussion").extend([{"role": "user", "content": "d1", "id": "dm1"}, {"role": "assistant", "content": "x"}])
    seen = []
    execute("learning.chat.rewrite", {"message": "書き直し", "topic": "discussion"}, s,
            _client(_ok({"answer": "ok"}), seen))
    body = _body(seen[-1])
    assert seen[-1].url.path == "/api/learning/courses/c1/topics/_discussion/chat"
    assert body["replace_message_id"] == "dm1" and body["intent_mode"] == "discuss"
    assert "rewrite_target: dm1" in s.scratch["runner_notes"]


def test_carryover_pick_and_revisit_default_source():
    s = PersonaSession(persona_id="st", course_id="c1")
    seen = []
    handler = lambda r: httpx.Response(200, json=(
        {"candidates": [{"trace_id": "tq1", "label": "問い A", "revisit": True}]} if r.url.path.endswith("landing-candidates")
        else {"empty": False, "carryover": {"trace_id": "tc1", "text": "問い A"}} if r.url.path.endswith("return-door")
        else {"trace_id": "new"}))
    c = _client(handler, seen)
    execute("learning.cycle.landing_candidates", {}, s, c)
    assert "選択肢1: 問い A〔あとで戻る〕" in project_observation("learning.cycle.landing_candidates", s.last_status, s.last_body)
    execute("learning.cycle.intention", {"role": "carryover_question", "pick": "1"}, s, c)
    assert _body(seen[-1]) == {"role": "carryover_question", "text": "問い A", "source_trace_id": "tq1"}
    execute("learning.cycle.return_door", {}, s, c)
    execute("learning.cycle.intention", {"role": "revisit_answer", "text": "答え"}, s, c)
    assert _body(seen[-1])["source_trace_id"] == "tc1"


def test_map_exclude_picks_question_node_and_restore_uses_it():
    s = PersonaSession(persona_id="st", course_id="c1")
    seen = []

    def handler(r):
        if r.url.path == "/api/me/personal-network":
            return httpx.Response(200, json={"nodes": [{"id": "rc", "node_kind": "reconstruction", "label": "R"},
                                                       {"id": "tr1", "node_kind": "question", "label": "準静的近似の問い"}]})
        return httpx.Response(200, json={"ok": True})

    c = _client(handler, seen)
    execute("learning.traces.map_exclude", {"node_hint": "準静的"}, s, c)
    assert seen[-1].url.path == "/api/learning/traces/tr1/map-exclude"
    assert project_observation("learning.traces.map_exclude", s.last_status, s.last_body) == vision5_exec.MAP_EXCLUDED_NOTE
    execute("learning.traces.map_restore", {}, s, c)
    assert seen[-1].url.path == "/api/learning/traces/tr1/map-restore"


def test_recon_revise_sends_revision_of_previous_answer():
    s = PersonaSession(persona_id="st", course_id="c1")
    s.remember("recon_items", "it1")
    s.remember("recons", "rc1")
    seen = []
    execute("learning.reconstruction.revise", {"response": {"text": "答え直し"}}, s, _client(_ok({"recon_id": "rc2"}), seen))
    assert seen[-1].url.path == "/api/learning/reconstruction/it1/revise"
    assert _body(seen[-1])["revision_of"] == "rc1" and s.latest("recons") == "rc2"


LANDSCAPE = {"course_id": "c9", "pending_count": 2, "hidden_document_count": 0,
             "domains": [{"domain_name": "宇宙物理", "frozen_version": "2026.1"}],
             "documents": [{"title": "論文 A", "placements": [
                 {"id": "p1", "node_label": "暗黒エネルギー", "domain_name": "宇宙物理", "perspective_label": "主題",
                  "status": "inferred", "status_label": "AIによる推定（未確認）", "evidence": [{"quote": "原文の一節"}]},
                 {"id": "p2", "node_label": "修正重力", "domain_name": "宇宙物理", "status": "inferred", "evidence": []},
                 {"id": "p3", "node_label": "重力レンズ", "domain_name": "宇宙物理", "status": "rejected"}],
                 "unplaced_domains": []}]}


def test_release_step2_projection_mirrors_screen():
    obs = project_observation("admin.release_review.placements", 200, LANDSCAPE)
    assert vision5_exec.RR_NEXT_MEANING in obs and vision5_exec.RR_REOPEN in obs
    assert "1. 暗黒エネルギー（宇宙物理 ・ 主題）［AIによる推定（未確認）］" in obs  # サーバの status_label が優先
    assert "2. 修正重力（宇宙物理）［AI推定（未確認）］" in obs  # 無ければ label_vocab のミラー
    assert "▶ 根拠を見る（折りたたみ・閉じている）" in obs and "原文の一節" not in obs  # 既定は閉じている
    assert obs.count("[却下] / [再検討]") == 2  # 却下済みの行にはボタンが無い
    assert "未確認 2件" in obs and "使用した地図: 宇宙物理 地図 版 2026.1" in obs


def test_release_accept_mirrors_screen_body_and_skips_when_nothing_pending():
    s = PersonaSession(persona_id="te", role="TEACHER", course_id="c9")
    seen = []
    c = _client(lambda r: httpx.Response(200, json=LANDSCAPE if r.method == "GET" else
                                         {"confirmed": 2, "decision_context": {"presented_matches_applied": True},
                                          "skipped_note": "編集権限の無い論文は対象外にしました。"}), seen)
    execute("admin.release_review.placements", {}, s, c)
    execute("admin.release_review.read_evidence", {"placement_hint": "暗黒"}, s, c)
    assert "原文の一節" in project_observation("admin.release_review.read_evidence", s.last_status, s.last_body)
    execute("admin.release_review.accept", {}, s, c)
    assert seen[-2].method == "POST"
    assert _body(seen[-2]) == {"presented_placement_ids": ["p1", "p2"], "evidence_expanded_placement_ids": ["p1"]}
    assert seen[-1].method == "GET"  # 画面は記録の後に配置を取り直す（fetchLandscape）
    obs = project_observation("admin.release_review.accept", s.last_status, s.last_body)
    assert vision5_exec.RR_ACCEPT_MATCHED in obs and "編集権限の無い論文" in obs
    n = len(seen)
    s.scratch["release_landscape"] = dict(LANDSCAPE, pending_count=0)
    execute("admin.release_review.accept", {}, s, c)
    assert len(seen) == n  # 未確認が無ければ POST しない（画面と同じ）
    assert "記録せずに" in project_observation("admin.release_review.accept", s.last_status, s.last_body)


def test_row_status_patches_then_reloads():
    s = PersonaSession(persona_id="te", role="TEACHER", course_id="c9")
    s.scratch["release_landscape"] = LANDSCAPE
    seen = []
    execute("admin.release_review.row_status", {"status": "再検討", "placement_hint": "2"}, s,
            _client(lambda r: httpx.Response(200, json=LANDSCAPE if r.method == "GET" else {"id": "p2"}), seen))
    assert seen[-2].method == "PATCH" and seen[-2].url.path == "/api/admin/landscape/placements/p2"
    assert _body(seen[-2]) == {"status": "review_required"}
    assert seen[-1].url.path == "/api/admin/landscape/courses/c9/placements"


def test_courses_list_prefers_unpublished_owned_course():
    s = PersonaSession(persona_id="te", role="TEACHER")
    rows = [{"id": "x", "title": "他人", "role": "viewer"}, {"id": "a", "title": "公開済み", "role": "owner", "is_published": True},
            {"id": "b", "title": "未公開", "role": "owner", "is_published": False}]
    execute("admin.courses.list", {"prefer": "unpublished"}, s, _client(_ok(rows), []))
    assert s.course_id == "b"
    assert "公開済み 〔所有〕" in project_observation("admin.courses.list", 200, rows)


def test_check_preconditions_owns_course():
    sc = load_scenario("t-v5-release-skip-step2")
    s = PersonaSession(persona_id="te", role="TEACHER")
    unmet = check_preconditions(sc, s, _client(_ok([{"id": "a", "role": "owner", "is_published": True}]), []))
    assert unmet and unmet[0]["missing"] == ["owned_course"]
    unmet = check_preconditions(sc, s, _client(_ok([{"id": "b", "role": "owner", "is_published": False}]), []))
    assert unmet == [] and s.course_id == "b"


# ---------------------------------------------------------------------------
# 投影（画面と同じ欄）
# ---------------------------------------------------------------------------
def test_return_door_projection():
    assert project_observation("learning.cycle.return_door", 200, {"empty": True, "fact": "f", "hint": "h"}) == \
        vision5_exec.RETURN_DOOR_HIDDEN  # 画面は空の扉を描かない（fact・hint も出さない）
    obs = project_observation("learning.cycle.return_door", 200, {
        "empty": False, "leave_note": {"text": "書き置き"}, "carryover": {"text": "問い", "trace_id": "t"}})
    assert "あなたの書き置き: 書き置き" in obs and "持ち越しの問い: 問い" in obs and "最後に確定" not in obs


def test_records_and_trajectory_and_disclosure_projection():
    records = {"systems": [{"label": "問い", "publicity_note": "「問いの軌跡」に表示されます。", "items": [
        {"id": "r1", "kind": "question", "status": "superseded", "status_label": "取り消し", "text": "q",
         "created_at": "2026-10-09T01:00:00", "flags": {"map_excluded": True}, "publicity": "あなた以外には表示されません。"}]}],
        "truncated": False, "provenance_note": "来歴の文"}
    obs = project_observation("learning.records.mine", 200, records)
    assert "［取り消し］q" in obs and vision5_exec.RECORDS_MAP_EXCLUDED in obs and "来歴の文" in obs
    assert "r1" not in obs and "superseded" not in obs
    traj = project_observation("learning.traces.list", 200, {"traces": [
        {"id": "t1", "kind": "question", "status": "open", "text": "q", "map_excluded": True}]})
    assert "〔問い〕〔未解決〕q" in traj and "地図に戻す" in traj
    disc = project_observation("learning.disclosure.view", 200, {"data_kind": {"note": "外部の AI に送られます。"}})
    assert disc == "入力欄の下の一行: 外部の AI に送られます。"


def test_digest_structure_and_records():
    d = response_digest({"title": "C", "topics": [{"id": "t1", "title": "T", "status": "in_progress"}],
                         "updated_at": "x"}, path="/api/learning/courses/c1")
    assert "title" in d["structure"] and not any("status" in k or "updated_at" in k or k.endswith(".id")
                                                  for k in d["structure"])
    assert "structure" not in response_digest({"a": 1}, path="/api/learning/courses/c1/progress")
    rec = response_digest({"systems": [{"items": [{"id": "r1", "kind": "tension", "status": "dismissed",
                                                   "flags": {"map_excluded": False}}]}]}, path="/api/me/records")
    assert rec["records"] == [{"id": "r1", "kind": "tension", "status": "dismissed", "map_excluded": False}]


# ---------------------------------------------------------------------------
# 審判
# ---------------------------------------------------------------------------
def _meta():
    return RunMeta(run_id="r", campaign_id="c", domain="astrophysics", snapshot="s",
                   started_at="2026-10-09T00:00:00+00:00", finished_at="2026-10-09T01:00:00+00:00")


GOOD_CTX = {"basis": "release_review.placements", "presented": {"count": 2, "ids": ["p1", "p2"], "truncated": False},
            "applied": {"count": 2, "ids": ["p1", "p2"], "truncated": False}, "presented_matches_applied": True,
            "alternatives_available": ["reject"], "decline_possible": True,
            "reopen": {"path": "PATCH /x", "statuses": ["rejected"], "actor": "teacher"}, "evidence_shown": None,
            "client_reported": {"presented_placement_ids": ["p1", "p2"], "evidence_expanded_placement_ids": ["p1"]}}


def test_e6_context_rules():
    assert decision.context_problems(GOOD_CTX, entity_id="p1") == []
    assert decision.context_problems(None) == ["確定の記帳に decision_context が無い"]
    bad = dict(GOOD_CTX, presented_matches_applied=False, alternatives_available=[],
               applied={"count": 3, "ids": ["p1", "p2"], "truncated": False})
    problems = decision.context_problems(bad, entity_id="p9")
    assert any("truncated" in p for p in problems) and any("代替" in p for p in problems)
    weird = dict(GOOD_CTX, client_reported={"presented_placement_ids": ["p1"], "evidence_expanded_placement_ids": ["p7"]})
    assert any("来歴申告" in p for p in decision.context_problems(weird))


def test_e6_rows_skip_non_public_visibility_and_flag_missing_context():
    f = FindingFactory(_meta())
    rows = [{"entity_id": "p1", "new_status": "confirmed", "metadata": {"action": "accept_on_release",
                                                                         "decision_context": GOOD_CTX}},
            {"entity_id": "c1", "new_status": "private", "metadata": {"action": "course_visibility"}},
            {"entity_id": "c1", "new_status": "public", "metadata": {"action": "course_visibility"}}]
    found = decision.check_rows(rows, f)
    assert len(found) == 1 and "course_visibility" in found[0].hypothesis


def _step(seq, persona, action, http, notes=None, screen="learning"):
    return TranscriptStep(seq=seq, persona_id=persona, action_id=action, screen=screen, http=http, runner_notes=notes or [])


def test_e7_flags_structure_difference_between_students():
    a = HttpTrace(method="GET", path="/api/learning/courses/c1", status=200, digest={"structure": {"title": "aa", "x": "1"}})
    b = HttpTrace(method="GET", path="/api/learning/courses/c1", status=200, digest={"structure": {"title": "aa", "x": "2"}})
    same = HttpTrace(method="GET", path="/api/learning/courses/c1", status=200, digest={"structure": {"title": "aa", "x": "1"}})
    f = FindingFactory(_meta())
    found = differential.check([_step(1, "st-a", "learning.course.open", [a]), _step(2, "st-b", "learning.course.open", [b])], f)
    assert len(found) == 1 and found[0].evidence.server_rows["differing_fields"] == ["x"]
    assert differential.check([_step(1, "st-a", "learning.course.open", [a]),
                               _step(2, "st-b", "learning.course.open", [same])], f) == []


def test_e8_transcript_and_db():
    steps = [
        _step(1, "st", "learning.chat.delete_message",
              [HttpTrace(method="DELETE", path="/api/learning/courses/c/topics/t/chat/messages/m2", status=200)]),
        _step(2, "st", "learning.chat.rewrite", [], notes=["rewrite_target: m1"]),
        _step(3, "st", "learning.traces.map_exclude",
              [HttpTrace(method="POST", path="/api/learning/traces/tr1/map-exclude", status=200)]),
        _step(4, "st", "learning.tension.dismiss",
              [HttpTrace(method="POST", path="/api/learning/tension/tt/dismiss", status=200)]),
        _step(5, "st", "learning.records.mine", [HttpTrace(method="GET", path="/api/me/records", status=200, digest={
            "records": [{"id": "tr1", "kind": "question", "status": "open", "map_excluded": False}]})]),
    ]
    f = FindingFactory(_meta())
    ops = retraction.collect_ops(steps)
    assert {m["message_id"] for m in ops["messages"]} == {"m1", "m2"}
    tr = retraction.check_transcript(steps, ops, f)
    assert {x.evidence.server_rows.get("trace_id") for x in tr} == {"tr1", "tt"}  # フラグの不一致・外した候補が無い
    rows = {"messages": [{"id": "x1", "kind": "question", "status": "superseded", "message_id": "m2"},
                         {"id": "x2", "kind": "question", "status": "open", "message_id": "m1"}],
            "traces": [{"id": "tr1", "status": "open", "payload": {"map_excluded": True}},
                       {"id": "tt", "status": "dismissed", "payload": {}}]}
    db, notes = retraction.check_db(ops, rows, f)
    assert len(db) == 1 and db[0].evidence.server_rows["message_id"] == "m1"


def test_b_nag_vocab_comes_from_product_tests_and_skips_own_words():
    assert "日ぶり" in principle.nag_vocab() and "連続日数" in principle.nag_vocab()
    f = FindingFactory(_meta())
    own = HttpTrace(method="GET", path="/x", status=200, response_excerpt=json.dumps(
        {"empty": False, "leave_note": {"text": "毎日 3 日連続で読む"}}, ensure_ascii=False))
    assert principle.check_nag([_step(1, "st", "learning.cycle.return_door", [own])], f) == []
    nag = HttpTrace(method="GET", path="/x", status=200, response_excerpt=json.dumps(
        {"fact": "3日ぶりですね"}, ensure_ascii=False))
    assert len(principle.check_nag([_step(1, "st", "learning.cycle.return_door", [nag])], f)) == 1


# ---------------------------------------------------------------------------
# 第 23 周の偽所見の是正（uxsim/runs/round23/harness_fixes.md）
# ---------------------------------------------------------------------------
def test_intention_text_wins_over_pick_and_zero_candidates_still_posts():
    s = PersonaSession(persona_id="st", course_id="c1")
    s.scratch["landing_candidates"] = [{"trace_id": "tq1", "label": "候補の問い"}]
    seen = []
    c = _client(_ok({"ok": True, "trace_id": "n1"}), seen)
    execute("learning.cycle.intention", {"role": "carryover_question", "pick": "1", "text": "自分の問い"}, s, c)
    assert _body(seen[-1]) == {"role": "carryover_question", "text": "自分の問い"}
    assert any(n.startswith("runner_choice:") for n in s.scratch["runner_notes"])
    obs = project_observation("learning.cycle.intention", s.last_status, s.last_body)
    assert obs == f"{vision5_exec.LEAVE_HEAD}\n{vision5_exec.LEAVE_SAVED}" and "ok" not in obs
    s.scratch["landing_candidates"] = []
    execute("learning.cycle.intention", {"role": "carryover_question", "text": "候補なしでも書く"}, s, c)
    assert _body(seen[-1])["text"] == "候補なしでも書く"
    execute("learning.cycle.intention", {"role": "leave_note", "text": "書き置き"}, s, c)
    assert project_observation("learning.cycle.intention", s.last_status, s.last_body) == vision5_exec.LEAVE_NOTE_SAVED


def test_row_status_numbering_keeps_rejected_rows():
    s = PersonaSession(persona_id="te", role="TEACHER", course_id="c9")
    land = {"documents": [{"title": "A", "placements": [
        {"id": "p1", "node_label": "一", "status": "rejected"}, {"id": "p2", "node_label": "二", "status": "inferred"},
        {"id": "p3", "node_label": "三", "status": "inferred"}]}]}
    s.scratch["release_landscape"] = land
    seen = []
    c = _client(lambda r: httpx.Response(200, json=land if r.method == "GET" else {"id": "x"}), seen)
    execute("admin.release_review.row_status", {"status": "再検討", "placement_hint": "3"}, s, c)
    assert seen[-2].url.path == "/api/admin/landscape/placements/p3"  # 画面の 3 番 = p3（却下済みも数える）
    n = len(seen)
    traces = execute("admin.release_review.row_status", {"status": "却下", "placement_hint": "1"}, s, c)
    assert len(seen) == n and traces[-1].error == "precondition:placement_row_rejected"
    assert "1. 一" in project_observation("admin.release_review.placements", 200, land)


def test_anchor_digest_card_numbered_and_dismiss_targets_shown_card_then_reloads():
    s = PersonaSession(persona_id="st", course_id="c1")
    items = [{"trace_id": "a1", "anchor_label": "式 (3)", "doubt_type_label": "定義", "question_text": "q1"},
             {"trace_id": "a2", "anchor_label": "主張", "doubt_type_label": "前提", "question_text": "q2"}]
    seen = []

    def handler(r):
        if r.method == "GET":
            return httpx.Response(200, json={"items": items if not seen or seen[-1].method == "GET" else items[1:]})
        return httpx.Response(200, json={"ok": True, "status": "dismissed", "notice": "この候補を外しました。問いそのものは残っています。"})

    c = _client(handler, seen)
    execute("learning.anchors.digest", {}, s, c)
    card = project_observation("learning.anchors.digest", s.last_status, s.last_body)
    assert "候補1（いま表示されている候補）" in card and "候補2（「あとで」を 1 回押すと出る候補）" in card
    assert "a1" not in card
    execute("learning.anchors.dismiss", {}, s, c)
    assert seen[-2].url.path == "/api/learning/anchors/a1/dismiss"  # いま表示されている 1 枚目
    obs = project_observation("learning.anchors.dismiss", s.last_status, s.last_body)
    assert obs.startswith("この候補を外しました") and "ok:" not in obs
    execute("learning.anchors.digest", {}, s, c)
    execute("learning.anchors.dismiss", {"pick": 2}, s, c)
    assert seen[-2].url.path == "/api/learning/anchors/a2/dismiss"


def test_publish_and_registration_project_screen_sentences():
    s = PersonaSession(persona_id="te", role="TEACHER", course_id="c9")
    s.scratch.update(release_landscape={"placement_count": 3, "pending_count": 1, "documents": []},
                     accept_record={"confirmed": 2, "evidence_opened": 1}, rows_decided=["p1"])
    execute("admin.course.visibility", {"visibility": "public"}, s, _client(_ok({"visibility": "public"}), []))
    obs = project_observation("admin.course.visibility", s.last_status, s.last_body)
    assert "公開しました。" in obs and "確認済みにした配置: 2件" in obs and "visibility:" not in obs
    reg = project_observation("admin.course_builder.register", 200, {"title": "T", "is_template": True})
    assert "登録完了!" in reg and "is_template" not in reg


def test_discuss_opening_and_course_tree_use_screen_labels():
    body = {"intention": {"has_motive": True},
            "documents": [{"document_id": "d1", "title": "P", "thesis": {"central_question": "問い"},
                           "backbone": [{"label": "theory_basis", "stage_label": "理論の基礎", "backed": True},
                                        {"label": "x", "stage_label": "観測量の構成", "backed": False}]}]}
    obs = project_observation("learning.discuss.opening", 200, body)
    assert "has_motive" not in obs and "1. 理論の基礎 › 2. 観測量の構成（点線）" in obs
    assert "点線は、根拠がまだ確認されていない段階です。" in obs
    tree = project_observation("learning.course.open", 200, {"title": "C", "is_template": True, "topics": [
        {"id": "t0", "title": "題", "status": "in_progress", "status_label": "学習中"}]})
    assert "題（学習中）" in tree and "in_progress" not in tree and "is_template" not in tree


def test_accept_mismatch_shows_server_decision_note():
    obs = project_observation("admin.release_review.accept", 200, {
        "decision_context": {"presented_matches_applied": False}, "decision_note": "表示されていた 3 件のうち 1 件を確認しました。"})
    assert "表示されていた 3 件のうち 1 件" in obs and vision5_exec.RR_BACK_TO_LANDSCAPE in obs


def test_e6_skipped_block():
    ctx = dict(GOOD_CTX, applied={"count": 1, "ids": ["p1"], "truncated": False}, presented_matches_applied=False,
               skipped={"count": 1, "ids": ["p2"], "truncated": False, "document_ids": ["d"], "reason": "not_editable"})
    assert decision.context_problems(ctx) == []
    bad = dict(ctx, skipped={"count": 1, "ids": ["p1"], "truncated": False, "document_ids": [], "reason": ""})
    probs = decision.context_problems(bad)
    assert any("両方" in p for p in probs) and any("理由" in p for p in probs)
