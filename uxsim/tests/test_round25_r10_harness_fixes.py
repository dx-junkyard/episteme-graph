"""第 25 周 第 10 周の偽所見・場面の欠けの是正（runs/round25/judge_*_r10.md の「ハーネス」節と harness_fixes_*.md の
第 10 周の項 = 第 10 波）と第 11 周の前提スクリプト（``r5_prereqs.py --round 11``）。

外部呼び出しなし（MockTransport・純関数・偽の ctx・偽の python）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import studio as ost
from uxsim.runner import actions_exec as ax
from uxsim.runner import doubt_exec as dx
from uxsim.runner import outcome as oc
from uxsim.runner import share_exec as sx
from uxsim.runner import state as rstate
from uxsim.runner import studio_exec as stx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_params, load_scenario
from uxsim.runner.state import OBSERVATION_CHARS, PersonaSession
from uxsim.schema import HttpTrace, TranscriptStep

UXSIM = Path(__file__).resolve().parents[1]
REPO = UXSIM.parent
ROUND = UXSIM / "runs" / "round25"
PARAMS = UXSIM / "domains" / "astrophysics" / "scenario_params"


def _client(handler):
    return EpistemeClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _actions(scenario_id: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    def walk(steps):
        for st in steps:
            if getattr(st, "children", None):
                walk(st.children)
            out.extend((o.action, o.with_) for o in st.options)
    walk(load_scenario(scenario_id).steps)
    return out


def _ids(scenario_id: str) -> list[str]:
    return [a for a, _ in _actions(scenario_id)]


def _prereqs():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# share H59: 材料のエントリを id の頭で指す（「TOV」は 2 つのエントリに当たった）
# ---------------------------------------------------------------------------
def test_share_h59_entry_hint_by_id():
    p = load_params("astrophysics", "t-share-labels-atlas", "te-03-meticulous-seminar")
    assert p["entry_hint"] == "67727cb0"
    pool = [{"id": "f55ad8ca-0000", "name": "TOV 方程式（静水圧平衡）"},
            {"id": "67727cb0-1111", "name": "TOV residual loss at collocation points"}]
    assert sx.pick_entry_by_id(pool, "67727cb0")["name"].startswith("TOV residual")
    assert sx.pick_entry_by_id(pool, "TOV") is None  # 名前の hint は従来の部分一致へ
    assert sx.pick_entry_by_id(pool + [{"id": "67727cb0-2222"}], "67727cb0") is None  # 複数なら選ばない
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    s.scratch["share_entries"] = pool
    ctx = _Ctx(REGISTRY["admin.library.entry_open"], {"entry_hint": "67727cb0"}, s, _client(lambda r: httpx.Response(500)))
    assert sx._resolve_entry(ctx)["id"] == "67727cb0-1111"
    ctx2 = _Ctx(REGISTRY["admin.library.entry_open"], {"entry_hint": "TOV"}, s, _client(lambda r: httpx.Response(500)))
    assert sx._resolve_entry(ctx2)["id"] == "f55ad8ca-0000"  # 名前の部分一致は先頭（偽所見の再現 — 材料を id にした理由）


# share H60: 既定の topic_id = t1（全学生に効く）・st-05 に 3 問・コースに無い topic_id は題名の語で選び直す
def test_share_h60_default_topic_and_fallback():
    for pid in ("st-05-night-solarwind", "st-01-m1-radio", "st-08-m1-rprocess-lecture"):
        assert load_params("astrophysics", "s-share-part-chip", pid)["topic_id"] == "t1", pid
    assert len(load_params("astrophysics", "s-share-part-chip", "st-05-night-solarwind")["seed_questions"]) == 3
    seen: list[str] = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        return httpx.Response(200, json={"chunks": []})

    s = PersonaSession(persona_id="st-05", role="STUDENT", course_id="c1")
    s.topics = {"t3": {"title": "二重ネットワーク"}, "t4": {"title": "別"}}
    s.topic_ids = ["t3", "t4"]
    ax._topic_open(_Ctx(REGISTRY["learning.topic.open"], {"topic_id": "t1", "topic_hint_terms": ["二重"]}, s, _client(handler)))
    assert seen and "/topics/t3/" in seen[-1] and s.topic_id == "t3"
    assert any("topic_id=t1 はこのコースのトピックに無い" in n for n in s.scratch.get("runner_notes") or [])


# share H61: ボタンの文字を行為の語彙に読む
def test_share_h61_explanation_what_vocab():
    assert sx.explanation_what("学生に公開してよいと査読する") == "approve"
    assert sx.explanation_what("［もう一人の査読者として査読する］") == "second_review"
    assert sx.explanation_what("共有ON") == "share_on" and sx.explanation_what("共有 OFF") == "share_off"
    assert sx.explanation_what("approve") == "approve" and sx.explanation_what("謎") == "謎"


# share H62: st-04 の材料に N_TOV・5 つ目の記号の任意の手
def test_share_h62_ntov_hint_and_fifth_lookup():
    hints = load_params("astrophysics", "s-share-symbol-concept", "st-04-intl-exoplanet")["symbol_hints"]
    assert any("mathcal{N}" in h and "TOV" in h for h in hints) and len(hints) == 5
    assert _ids("s-share-symbol-concept").count("learning.symbol.lookup") == 5


# share H63: 地図の概念を選んで［対応を付ける］まで（node_hint の手）
def test_share_h63_manual_atlas_link_with_node_hint():
    manual = [w for a, w in _actions("t-share-labels-atlas") if a == "admin.library.atlas_link_manual"]
    assert len(manual) == 2 and "node_hint" in manual[1] and "node_hint" not in manual[0]
    assert load_params("astrophysics", "t-share-labels-atlas", "te-03-meticulous-seminar")["atlas_manual_node_hint"]


# share (b2): 自分の説明を書く → 共有 ON → その説明を引用
def test_share_b2_write_share_cite_steps():
    ids = _ids("t-share-explain-endorse-cite")
    i_add, i_cite = ids.index("admin.explanations.add"), ids.index("admin.explanations.cite")
    assert i_add < i_cite and "admin.explanations.update" in ids[i_add:i_cite]
    cite = next(w for a, w in _actions("t-share-explain-endorse-cite") if a == "admin.explanations.cite")
    assert "explanation_hint" in cite
    p = load_params("astrophysics", "t-share-explain-endorse-cite", "te-02-veteran-intro")
    assert p["own_explanation_title"] and p["own_explanation_body"] and p["cite_explanation_hint"] == p["own_explanation_title"]
    assert load_params("astrophysics", "t-share-explain-endorse-cite", "te-04-copilot-intro")["own_explanation_title"] == ""


# ---------------------------------------------------------------------------
# share H65: 到達点の記録と lane.sh の前提の状態による依存
# ---------------------------------------------------------------------------
def _step(seq, action, http, args=None, persona="te-03"):
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="t-share-labels-atlas", action_id=action,
                          args=args or {}, http=[HttpTrace(method=m, path="/x", status=st) for m, st in http])


def test_share_h65_outcome_points():
    steps = [_step(1, "admin.library.entries", [("GET", 200)]),
             _step(2, "admin.library.identity_link_restore", [("POST", 200), ("GET", 200)]),
             _step(3, "admin.library.bound_link_decide", [("POST", 200)], {"decision": "reject", "section": "symbols"}),
             _step(4, "admin.library.entry_freeze", [("POST", 409)]),
             _step(5, "unsupported:x", [("POST", 200)])]
    out = oc.build_outcome(steps, run_id="r1")
    row = out["personas"]["te-03"]
    assert row["reached"] == ["admin.library.identity_link_restore", "admin.library.bound_link_decide",
                              "admin.library.bound_link_decide=reject"]
    assert row["seen"] == ["admin.library.entries"]
    need = oc.parse_points("admin.library.bound_link_decide=confirm,admin.library.entry_freeze")
    assert oc.missing_points(out, "te-03-meticulous-seminar", need) == need  # id の頭で引ける・確定と凍結に届いていない
    assert oc.missing_points(out, "te-03", ["admin.library.identity_link_restore"]) == []


def test_share_h65_write_outcome_and_cli(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    (run / "transcript.jsonl").write_text(
        _step(1, "admin.library.entry_freeze", [("POST", 200)]).model_dump_json() + "\n", encoding="utf-8")
    extra = tmp_path / "mb" / "OUTCOME_te-03.json"
    oc.write_outcome(run, extra_path=str(extra))
    assert json.loads((run / oc.OUTCOME_FILE).read_text(encoding="utf-8"))["personas"]["te-03"]["reached"] == \
        ["admin.library.entry_freeze"]
    assert oc.main(["check", str(extra), "te-03", "admin.library.entry_freeze"]) == 0
    assert oc.main(["check", str(extra), "te-03", "admin.library.bound_link_decide=confirm"]) == 1
    assert oc.main(["check", str(tmp_path / "none.json"), "te-03", "a"]) == 2
    src = (UXSIM / "runner" / "api.py").read_text(encoding="utf-8")
    assert "write_outcome(run_dir)" in src


@pytest.mark.skipif(os.name == "nt", reason="sh が要る")
def test_share_h65_lane_deps_by_points(tmp_path):
    src = (ROUND / "lane.sh").read_text(encoding="utf-8")
    src = re.sub(r"^cd /\S+$", f"cd {tmp_path}", src, count=1, flags=re.MULTILINE)
    lane = tmp_path / "lane.sh"
    lane.write_text(src, encoding="utf-8")
    (tmp_path / "uxsim" / "sandbox").mkdir(parents=True)
    (tmp_path / "uxsim" / "sandbox" / ".env.uxsim").write_text("", encoding="utf-8")
    (tmp_path / "uxsim" / "runs" / "round25").mkdir(parents=True)
    py = tmp_path / "backend" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    # 到達点の check だけ本物の python で、runner は偽（FAKE_RC）
    py.write_text(f'#!/bin/sh\nif [ "$2" = "uxsim.runner.outcome" ]; then exec "{sys.executable}" "$@"; fi\n'
                  'exit ${FAKE_RC:-0}\n', encoding="utf-8")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    mb = tmp_path / "uxsim" / "runs" / "round25" / "mailbox_share"
    mb.mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": str(REPO)}

    def run(*args, deps=""):
        r = subprocess.run(["sh", str(lane), *args], env={**env, "LANE_DEPS": deps}, capture_output=True)
        return r.returncode

    deps = "te-03@admin.library.bound_link_decide=confirm,admin.library.entry_freeze"
    # 依存先の記録が無い → 走らせない
    assert run("share", "c.yaml", "st-04", "s-e", deps=deps) == 3
    assert "reason=到達点の記録が無い" in (mb / "LANE_SKIP_st-04").read_text(encoding="utf-8")
    (mb / "OUTCOME_te-03.json").write_text(json.dumps(
        {"personas": {"te-03": {"reached": ["admin.library.bound_link_decide=reject"]}}}), encoding="utf-8")
    assert run("share", "c.yaml", "st-04", "s-e", deps=deps) == 3
    assert "admin.library.entry_freeze" in (mb / "LANE_SKIP_st-04").read_text(encoding="utf-8")
    (mb / "OUTCOME_te-03.json").write_text(json.dumps(
        {"personas": {"te-03": {"reached": ["admin.library.bound_link_decide=confirm", "admin.library.entry_freeze"]}}}),
        encoding="utf-8")
    assert run("share", "c.yaml", "st-04", "s-e", deps=deps) == 0 and not (mb / "LANE_SKIP_st-04").exists()
    # 自分の run の前に古い自分の記録を消す・--clear-marks が OUTCOME_* も消す
    assert not (mb / "OUTCOME_st-04.json").exists()
    assert run("--clear-marks", "share") == 0 and not list(mb.glob("OUTCOME_*"))


# ---------------------------------------------------------------------------
# doubt H59: 手の前提の行は属性ごと上限の外側（末尾）に
# ---------------------------------------------------------------------------
def test_doubt_h59_pinned_rows_survive_observation_limit():
    items = [{"statement": f"主張{i}" + "あ" * 120, "load_level": "low", "verification_status": "untested"}
             for i in range(60)]
    items.append({"statement": "手の前提", "load_note": "依存の広がりを計算できません（結ばれていないため）。",
                  "verification_status": "untested", "scope_count_is_zero": True})
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch.update({"doubt_courses": [{"id": "c1", "title": "コース"}], "doubt_course_id": "c1",
                      "doubt_open": items, "doubt_atlas": {"points": [{"label": "点" + "い" * 200, "target_type": "claim"}
                                                                       for _ in range(30)]},
                      "doubt_cf_sessions": {"items": [{"title": "反実仮想" + "う" * 300} for _ in range(5)]}})
    text = dx.tab_view(s)
    row = dx.pinned_open_rows(items)[0]
    assert len(text) <= OBSERVATION_CHARS - 200 and row in text and "スコープの記帳なし" in row
    assert dx.OPEN_PINNED_TAIL_NOTE in text or text.endswith(row) or row in text
    assert dx.fit_with_pinned_tail("短い", [row], 6000) == "短い"


# doubt H60: 既存の疑義の写し（同じ型・同じ理由）で送る・材料の目印は値との一致
def test_doubt_h60_reason_copy_and_material_match():
    rows = [{"challenge_type": "definitional", "reason": "定義が曖昧", "status": "open"},
            {"challenge_type": "scope_extrapolation", "reason": "外挿の理由", "status": "open"}]
    assert dx.copy_source_challenge(rows, "scope_extrapolation")["reason"] == "外挿の理由"
    assert dx.copy_source_challenge(rows, "hidden_lemma")["reason"] == "定義が曖昧"
    assert dx.copy_source_challenge([], "x") is None
    sent: list[dict] = []

    def handler(req: httpx.Request):
        if req.method == "POST":
            sent.append(json.loads(req.content))
            return httpx.Response(201, json={"challenge_id": "c9", "duplicate_note": "同じ型・同じ理由の疑義があります。"})
        return httpx.Response(200, json={"challenges": rows})

    s = PersonaSession(persona_id="te-03", role="TEACHER")
    s.scratch.update({"doubt_target": {"target_type": "claim", "target_id": "x1", "label": "主張"},
                      "doubt_ledger": {"challenges": rows}})
    dx.challenge_record(_Ctx(REGISTRY["admin.doubt.challenge_record"],
                             {"challenge_type": "外挿", "reason_from": "open_challenge", "reason": "無視される"}, s,
                             _client(handler)))
    assert sent and sent[0]["reason"] == "外挿の理由" and sent[0]["challenge_type"] == "scope_extrapolation"
    assert any("写し" in n for n in s.scratch.get("observation_notes") or [])
    # 材料の文は頭書きなしでも値の一致で「場面の材料」と注記する
    s2 = PersonaSession(persona_id="te-03", role="TEACHER")
    s2.scratch[dx.SCENARIO_MATERIAL_TEXTS_KEY] = dx.scenario_material_texts({"challenge_reason": "定義が曖昧。", "n": 1})
    dx.note_scenario_material(s2, {"reason": "定義が曖昧。"})
    assert "「理由」の欄" in s2.scratch["observation_notes"][0] and "周の番号入り" not in dx.SCENARIO_MATERIAL_NOTE
    for sid in ("t-db-challenge-from-node", "t-db-assumption-review"):
        assert "（場面の材料・第" not in (PARAMS / f"{sid}.yaml").read_text(encoding="utf-8").split("scenario:", 1)[1]
    assert any(w.get("reason_from") == "open_challenge" for a, w in _actions("t-db-challenge-from-node")
               if a == "admin.doubt.challenge_record")
    assert "SCENARIO_MATERIAL_TEXTS_KEY" in (UXSIM / "runner" / "api.py").read_text(encoding="utf-8")


# doubt H61: 前提スクリプトが書いた理由を観察で「前提の材料」と注記
def test_doubt_h61_prereq_reason_note():
    m = _prereqs()
    assert m.ledger_reason(type("A", (), {"round": 11})()).startswith("（前提の材料）第 11 周の前提")
    assert dx.is_prereq_reason("第 4 周の前提: 行のある対象を 1 件用意") and dx.is_prereq_reason("（前提の材料）x")
    assert not dx.is_prereq_reason("本人の言葉")
    entry = {"verification_status": "untested",
             "verification_scopes": [{"condition": "c", "recorded_by_label": "堀 正和", "reason": "第 4 周の前提: 行"}]}
    text = dx.ledger_view({"label": "対象"}, entry)
    assert "記帳者: 堀 正和 — 第 4 周の前提: 行" in text and dx.PREREQ_REASON_NOTE.strip() in text


# ---------------------------------------------------------------------------
# atlas H51 / H52: freeze_note に周番号なし・watch_for に名前の行
# ---------------------------------------------------------------------------
def test_atlas_h51_h52_materials():
    data = yaml.safe_load((PARAMS / "t-atlas-draft-freeze.yaml").read_text(encoding="utf-8"))
    notes = [data["params"]["freeze_note"]] + [v.get("freeze_note") for v in data["personas"].values() if v.get("freeze_note")]
    assert notes and not any(re.search(r"第\s*\d+\s*周", n) for n in notes)
    assert load_params("astrophysics", "t-atlas-draft-freeze", "te-01-rookie-seminar")["freeze_version"] == "2026.8"
    watch = "\n".join(load_scenario("s-atlas-threads-and-placement").raw.get("watch_for") or [])
    assert "名前の行" in watch and "同じ句" in watch


# ---------------------------------------------------------------------------
# studio H43 / H44 / H45
# ---------------------------------------------------------------------------
def _reattach_step(at="2026-10-31T05:00:00Z"):
    t = HttpTrace(method="POST", path="/reattach-all", status=200,
                  digest={"studio_reattach_all": {"course_id": "c1", "applied_topic_ids": ["t3"],
                                                  "outcome_topic_ids": ["t3", "t4"], "changed": True}})
    return TranscriptStep(seq=4, at=at, persona_id="te-01", action_id="admin.lecture_studio.reattach_appendix_all", http=[t])


def test_studio_h43_reattach_topics_feed_prepublish(tmp_path, monkeypatch):
    runs = {"r1": [_reattach_step()]}
    assert set(ost.reattached_topics(runs)) == {("c1", "t3"), ("c1", "t4")}
    rows = ost.check_campaign_reattach(runs, lambda c, t: 5, exclude={("c1", "t4")})
    assert [r["topic_id"] for r in rows] == ["t3"] and rows[0]["source"] == "reattach_all" and rows[0]["student_total_slides"] == 5
    (tmp_path / "transcript.jsonl").write_text(_reattach_step().model_dump_json() + "\n", encoding="utf-8")

    class Fake:
        calls: list = []

        def login(self, u, p):
            return True, None

        def call(self, method, path, **kw):
            Fake.calls.append(path)
            return 200, {"total_slides": 4}, None

        def close(self):
            pass

    s = PersonaSession(persona_id="te-01", role="TEACHER", password="pw")
    s.scratch.update({"run_dir": str(tmp_path), "campaign_students": ["st-01-m1-radio"]})
    ctx = _Ctx(REGISTRY["admin.lecture_studio.publish_release"], {}, s, _client(lambda r: httpx.Response(500)))
    got = stx.prepublish_student_reads(ctx, "c1", client_factory=Fake)
    assert {r["topic_id"] for r in got} == {"t3", "t4"} and all(r["read_at"] for r in got)  # H44: 時刻が空でない
    src = (UXSIM / "runner" / "studio_exec.py").read_text(encoding="utf-8")
    assert "prepublish_student_reads(ctx, cid, now=_utc_now())" in src and '"outcome_topic_ids"' in src


def test_studio_h45_figure_card_says_image():
    assert rstate._figure_card({"image_url": "/x", "caption": "図1"}).startswith("〔図（画像あり）〕")
    assert rstate._figure_card({"caption": "図1"}).startswith("〔図: この図の画像を取得できませんでした。〕")


# ---------------------------------------------------------------------------
# 第 11 周の前提（r5_prereqs.py --round 11）— 確認だけ・表と並びが同じ
# ---------------------------------------------------------------------------
def _doc_items(name: str) -> list[str]:
    text = (ROUND / name).read_text(encoding="utf-8")
    return re.findall(r"^\| `([a-z0-9_]+)` \|", text.split("## 周の前の一括実行", 1)[1], re.MULTILINE)


NEW_R11 = ("lane_outcomes_clear", "share_symbol_entry_unique", "share_b2_own_explanation", "share_e_symbol_hints",
           "share_part_chip_students", "doubt_ik1120_same_claim", "doubt_proposal_completion",
           "doubt_open_tail_projection", "atlas_browser_run_ready", "studio_work_note_topics")


def test_round11_items_match_doc_and_defaults():
    m = _prereqs()
    assert list(m.ROUND_ITEMS[11]) == _doc_items("r11_prereqs.md")
    assert set(m.ROUND_ITEMS[11]) <= set(m.ALL_ITEMS)
    for name in NEW_R11:
        assert name in m.ROUND_ITEMS[11] and m.ALL_ITEMS[name].do is None, name  # 確認だけ
    a = m.parse_args(["--round", "11"])
    assert a.expect_frozen == "2026.7" and a.freeze_teacher == m.TE01 and a.expect_release == "7"
    assert a.split_topic == "t18" and a.part_chip_student == "st-05" and a.allow_auto_confirm is False
    assert '"--round", "11"' in (ROUND / "r11_prereqs.py").read_text(encoding="utf-8")
    p = load_params("astrophysics", "t-studio-split-save", "te-01-rookie-seminar")
    assert "CMB" in p["split_topic_hint"]


def test_round11_pure_functions():
    m = _prereqs()
    assert [e["id"] for e in m.entries_with_word([{"id": "a", "name": "TOV x"}, {"id": "b", "name": "y"}], "tov")] == ["a"]
    assert len(m.own_explanations([{"kind": "personal", "author_name": "堀"}, {"kind": "standard", "author_name": "堀"},
                                   {"kind": "personal", "author_name": "他"}], "堀")) == 1
    entry = {"challenges": [{"proposals": [{"status": "completed", "proposal": "p", "completed_by_name": "岡村",
                                            "reachability_label": "可能"}, {"status": "in_progress"}]}]}
    assert m.completion_attribution(entry) == [{"proposal": "p", "attribution": {"completed_by_name": "岡村"}}]
    assert m.ledger_reachabilities(entry) == ["可能"]
    topics = [{"id": "t1", "title": "A", "student_material": "…取り込まれた…載せていません"}, {"id": "t2", "student_material": "x"}]
    assert m.work_note_topics(topics) == [("t1", "A", ["取り込まれ", "載せていません"])]
