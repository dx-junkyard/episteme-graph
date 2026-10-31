"""第 25 周 第 9 周の偽所見・運転の欠けの是正（runs/round25/judge_*_r9.md の「ハーネス」節と harness_fixes_*.md の第 9 周の項）と
第 10 周の前提スクリプト（``r5_prereqs.py --round 10``）・レーンの失敗の印（LANE_FAIL）・第 9 波の投影（sync_keys_wave9.md 面 C）。

外部呼び出しなし（MockTransport・純関数・偽の ctx・一時ディレクトリの lane.sh）。各テストの見出しは面と H の番号。
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

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import run_all
from uxsim.runner import atlas_exec as atx
from uxsim.runner import doubt_exec as dx
from uxsim.runner import share_exec as sx
from uxsim.runner import studio_exec as stx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_params, load_scenario
from uxsim.runner.state import PersonaSession

UXSIM = Path(__file__).resolve().parents[1]
ROUND = UXSIM / "runs" / "round25"


def _client(handler):
    return EpistemeClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _actions(scenario_id: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    def walk(steps):
        for st in steps:
            if getattr(st, "children", None):
                walk(st.children)
            out.extend((o.action, dict(o.with_ or {})) for o in st.options)
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
# share H53: (a) は［候補に戻す］の後に、戻した同じリンクの［確認］/［見送り］
# ---------------------------------------------------------------------------
def test_share_h53_restore_then_same_link_decide():
    ids = _ids("t-share-identity-queue")
    restore = ids.index("admin.library.identity_link_restore")
    assert "admin.library.bound_link_decide" in ids[restore + 1:]
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    rows = [{"id": "l1", "status": "candidate"}, {"id": "l2", "status": "candidate"}]
    assert sx.restored_first(s, rows, "", "id") is None
    s.scratch["share_restored_link_id"] = "l2"
    assert sx.restored_first(s, rows, "", "id")["id"] == "l2"
    assert sx.restored_first(s, rows, "リンク1", "id") is None  # hint があれば従来どおり
    src = (UXSIM / "runner" / "share_exec.py").read_text(encoding="utf-8")
    assert 's.scratch["share_restored_link_id"] = link_id' in src


# share H54: (c) は記号の［候補に戻す］→ 確認 → エントリの凍結（新しい行為）
def test_share_h54_symbol_restore_and_freeze_steps():
    ids = _ids("t-share-labels-atlas")
    i_restore = ids.index("admin.library.identity_link_restore")
    assert ids.index("admin.library.symbol_derive") < i_restore < ids.index("admin.library.bound_link_decide")
    assert ids.index("admin.library.bound_link_decide") < ids.index("admin.library.entry_freeze")
    p = load_params("astrophysics", "t-share-labels-atlas", "te-03-meticulous-seminar")
    for k in ("symbol_restore_reason", "freeze_note"):
        assert p[k] and not re.search(r"judge_|IK-| H\d|前提スクリプト", str(p[k])), k
    act = REGISTRY["admin.library.entry_freeze"]
    assert act.api == ("POST", "/api/admin/library/entries/{entry_id}/freeze")
    assert act.affordance == "knowledge-library.detail-freeze"


def test_share_h54_entry_freeze_posts_and_reloads():
    sent: list[tuple[str, str, bytes]] = []

    def handler(req: httpx.Request):
        sent.append((req.method, req.url.path, req.content))
        if req.method == "POST":
            return httpx.Response(200, json={"version_no": 2, "embedding_status": "ok"})
        if req.url.path.endswith("/entries/e1"):
            return httpx.Response(200, json={"id": "e1", "name": "TOV", "review_status": "confirmed"})
        return httpx.Response(200, json={})

    s = PersonaSession(persona_id="te-03", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV", "review_status": "confirmed"}
    sx._entry_freeze(_Ctx(REGISTRY["admin.library.entry_freeze"], {"note": "メモ"}, s, _client(handler)))
    posts = [x for x in sent if x[0] == "POST"]
    assert posts and posts[0][1] == "/api/admin/library/entries/e1/freeze" and json.loads(posts[0][2]) == {"note": "メモ"}
    text = s.last_body["_ui_fact"]
    assert sx.ENTRY_FREEZE_DONE in text and "取り消せません" in text and "［保存］［凍結（版発行）］［廃止］" in text
    # キャンセルなら送らない
    sent.clear()
    sx._entry_freeze(_Ctx(REGISTRY["admin.library.entry_freeze"], {"cancel": True}, s, _client(handler)))
    assert not [x for x in sent if x[0] == "POST"] and "キャンセル" in s.last_body["_ui_fact"]
    assert "［復元］" in sx.entry_detail_buttons({"status": "retired"})


# share H55: st-07 の材料は ⚓ の開く t1・選択質問の枠は 3 つ
def test_share_h55_part_chip_topic_and_three_questions():
    p = load_params("astrophysics", "s-share-part-chip", "st-07-m1-grb-voice")
    assert p["topic_id"] == "t1" and len(p["seed_questions"]) == 3
    # 第 10 波（H60）: 既定が t1 になった（全学生に効く）。題名の語で選ぶ材料は st-10 だけが空で残す
    assert load_params("astrophysics", "s-share-part-chip", "st-01-m1-radio")["topic_id"] == "t1"
    assert load_params("astrophysics", "s-share-part-chip", "st-10-d3-21cm-receiver")["topic_id"] == ""
    acts = _actions("s-share-part-chip")
    assert [a for a, _ in acts].count("learning.chat.ask_selection") == 3
    assert any(a == "learning.topic.open" and "topic_id" in w for a, w in acts)


# share H57: 「共通部品として」の投影は available:false なら事実文 1 行（タブも部品名も描かない）
def test_share_h57_shared_pane_unavailable():
    body = {"available": False, "frozen_label": "旧部品", "frozen_label_line": "…置き換わり、後継が決まっていません。",
            "instance": {"component": {"label": ""}}}
    out = sx.shared_pane_view(body)
    assert "部品: " not in out and "タブ" not in out and out.endswith("後継が決まっていません。") and "旧部品" in out
    assert sx.shared_pane_view({"available": False, "note": "N"}) == "N"


# ---------------------------------------------------------------------------
# doubt H52: 「計算できない前提」区画の行を開く（番号は区画の中で数える）
# ---------------------------------------------------------------------------
def test_doubt_h52_unplotted_sources_and_action():
    assert dx.ledger_from_source("計算できない前提") == "unplotted"
    assert dx.ledger_sources("確定した前提", "前提1") == (["unplotted"], "1")
    assert dx.ledger_sources("散布図", "点2") == (["atlas"], "点2")
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch["doubt_atlas"] = {"points": [{"target_type": "claim", "target_id": "p1", "label": "点の主張"}],
                                "unplotted_points": [{"target_type": "assumption", "target_id": "a1", "label": "前提A"},
                                                     {"target_type": "assumption", "target_id": "a2", "label": "前提B"}]}
    ctx = _Ctx(REGISTRY["admin.doubt.unplotted_open"], {"pick": "前提2"}, s, _client(lambda r: httpx.Response(500)))
    assert dx.hint_pick(dx._targets_from(ctx, "unplotted"), "2", ("label",))["target_id"] == "a2"
    assert "admin.doubt.unplotted_open" in dx.HANDLERS
    first_choose = [a for a in _ids("t-db-assumption-review") if a in ("admin.doubt.unplotted_open",
                                                                         "admin.doubt.assumption_open_ledger")]
    assert first_choose[0] == "admin.doubt.unplotted_open"


# doubt H53: 合意リストを予算で間引くとき、load_note の行は末尾に必ず写す
def test_doubt_h53_open_lines_pin_load_note():
    items = [{"statement": f"主張{i}" + "あ" * 80, "load_level": "low", "verification_status": "untested"} for i in range(40)]
    items.append({"statement": "手の前提", "load_note": "依存の広がりを計算できません。", "verification_status": "untested"})
    lines = dx.open_lines(items, only_reachable=False, budget=1500)
    text = "\n".join(lines)
    assert dx.OPEN_ROWS_CLIPPED in lines and "41. 手の前提" in text and "依存の広がりを計算できません。" in text
    assert sum(len(x) + 1 for x in lines) < 1500 + 400
    assert dx.OPEN_ROWS_CLIPPED not in dx.open_lines(items[:3], only_reachable=False, budget=6000)


# doubt H54: te-03 の場面に［完了を記録］・course_hint 1bad7d9f
def test_doubt_h54_complete_proposal_and_course_hint():
    acts = _actions("t-db-challenge-from-node")
    statuses = [w.get("status") for a, w in acts if a == "admin.doubt.proposal_status"]
    assert "検証に着手" in statuses and "完了を記録" in statuses
    assert load_params("astrophysics", "t-db-challenge-from-node", "te-03-meticulous-seminar")["course_hint"] == "1bad7d9f"


# ---------------------------------------------------------------------------
# atlas H48: level 2 の select_node は L2 のキャンバスのまま / H49: 説明の畳みは状態ごと
# ---------------------------------------------------------------------------
def _atlas_l2() -> dict:
    return {"domain_name": "宇宙物理", "skeleton_version": "2026.6",
            "nodes": {"a": {"label": "A"}, "b": {"label": "B"}, "c": {"label": "C"}},
            "levels": {"1": {"regions": [], "nodes": [], "now_note": "いまここは表示していません。"},
                       "2": {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}],
                             "edges": [["a", "b"], ["b", "c"]], "directed_edges": [["a", "b"]],
                             "omitted_note": "O", "relations_note": "R", "now_note": "N"}}}


def test_atlas_h48_select_node_level2_draws_l2():
    def handler(req):
        return httpx.Response(200, json=_atlas_l2())

    s = PersonaSession(persona_id="st-03", role="STUDENT", course_id="c1", topic_id="t1")
    atx._atlas_select(_Ctx(REGISTRY["learning.atlas.select_node"], {"level": 2, "node_hint": "A"}, s, _client(handler)))
    text = s.last_body["_ui_fact"]
    assert "関係（実線）: A → B（依存）" in text and "関係（実線）: B — C" in text and "右のパネル:" in text
    s2 = PersonaSession(persona_id="st-03", role="STUDENT", course_id="c1", topic_id="t1")
    atx._atlas_select(_Ctx(REGISTRY["learning.atlas.select_node"], {"level": 1, "node_hint": "A"}, s2, _client(handler)))
    assert "関係（実線）" not in s2.last_body["_ui_fact"]


def test_atlas_h49_fold_by_state():
    ex = frozenset({"採用の説明", "見送りの説明"})
    cards = [("1", ["カード1", "見送りの説明"]), ("2", ["カード2", "採用の説明"]), ("3", ["カード3", "採用の説明"]),
             ("4", ["カード4", "採用の説明"])]
    out = atx.fold_cards(cards, ex, None, "関係の候補")
    assert out.count("採用の説明") == 1 and out.count(atx.SAME_EXPLAIN_NOTE) == 2 and out.count("見送りの説明") == 1


# atlas 第 9 波（sync_keys_wave9 面 C）: 事実文の行の順・L1 の now_note・パネルの relations
def test_atlas_wave9_level_note_and_panel_relations():
    l2 = _atlas_l2()["levels"]["2"]
    assert atx.level_note_text(l2) == "N O R"
    assert atx.level_note_text({}) == ""
    assert "いまここは表示していません。" in "\n".join(atx.overlay_l1_lines(_atlas_l2()))
    lines = atx.panel_lines({"nodes": {"a": {"label": "A", "relations": "地図の関係: 「A」が「B」に依存する。"}}}, "a")
    assert "地図の関係: 「A」が「B」に依存する。" in lines
    assert not any("地図の関係" in x for x in atx.panel_lines({"nodes": {"a": {"label": "A"}}}, "a"))


# ---------------------------------------------------------------------------
# studio H40: 発行の直前に学生の読みを run の中に残す
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def login(self, user, pw):
        return True, None

    def call(self, method, path, **kw):
        self.calls.append((method, path))
        return 200, {"total_slides": 3}, None

    def close(self):
        pass


def test_studio_h40_prepublish_reads(tmp_path, monkeypatch):
    from uxsim.oracles import studio as ost

    def fake_saves(runs, fetch):
        assert list(runs) == [tmp_path.name]
        return [{"kind": "student_api", "course_id": "c1", "topic_id": "t17", "preview_slides": 2,
                 "student_total_slides": fetch("c1", "t17"), "mismatch": True},
                {"kind": "student_api", "course_id": "other", "topic_id": "t1", "student_total_slides": None}]

    monkeypatch.setattr(ost, "check_campaign_saves", fake_saves)
    fake = _FakeClient()
    s = PersonaSession(persona_id="te-01", role="TEACHER", password="pw")
    s.scratch.update({"run_dir": str(tmp_path), "campaign_students": ["st-01-m1-radio"]})
    ctx = _Ctx(REGISTRY["admin.lecture_studio.publish_release"], {}, s, _client(lambda r: httpx.Response(500)))
    rows = stx.prepublish_student_reads(ctx, "c1", client_factory=lambda: fake)
    assert len(rows) == 1 and rows[0]["phase"] == "before_publish" and rows[0]["student_total_slides"] == 3
    assert fake.calls == [("GET", "/api/learning/lecture/courses/c1/topics/t17/sequence")]
    written = [json.loads(x) for x in (tmp_path / stx.PREPUBLISH_FILE).read_text(encoding="utf-8").splitlines()]
    assert written[0]["topic_id"] == "t17" and "prepublish_reads" in s.scratch["runner_notes"][-1]
    # 学生・書き先が無ければ何もしない
    s2 = PersonaSession(persona_id="te-01", role="TEACHER")
    assert stx.prepublish_student_reads(_Ctx(REGISTRY["admin.lecture_studio.publish_release"], {}, s2,
                                             _client(lambda r: httpx.Response(500))), "c1") == []
    src = (UXSIM / "runner" / "studio_exec.py").read_text(encoding="utf-8")
    pub = src.split("def _publish_release", 1)[1]
    assert pub.index("prepublish_student_reads(ctx, cid") < pub.index('"/api/admin/shared/{object_type}/{object_id}/releases"')
    api_src = (UXSIM / "runner" / "api.py").read_text(encoding="utf-8")
    assert 'session.scratch["run_dir"]' in api_src and 'session.scratch["campaign_students"]' in api_src


def test_studio_h40_run_all_collects_prepublish(tmp_path):
    camp = tmp_path / "c-x"
    (camp / "r1").mkdir(parents=True)
    (camp / "r1" / "campaign_studio_cross.before.jsonl").write_text(json.dumps({"topic_id": "t17"}) + "\n", encoding="utf-8")
    (camp / "r2").mkdir()
    rows = run_all.collect_prepublish_rows(camp)
    assert rows == [{"topic_id": "t17", "source_run": "r1"}] and (camp / run_all.PREPUBLISH_COLLECTED).is_file()
    assert run_all.collect_prepublish_rows(tmp_path / "c-x" / "r2") == []


# ---------------------------------------------------------------------------
# share H58 / studio H41 / atlas H50: lane.sh は exit を返し、失敗の印と依存の止めを持つ
# ---------------------------------------------------------------------------
def _lane_sandbox(tmp_path: Path) -> Path:
    src = (ROUND / "lane.sh").read_text(encoding="utf-8")
    src = re.sub(r"^cd /\S+$", f"cd {tmp_path}", src, count=1, flags=re.MULTILINE)
    lane = tmp_path / "lane.sh"
    lane.write_text(src, encoding="utf-8")
    (tmp_path / "uxsim" / "sandbox").mkdir(parents=True)
    (tmp_path / "uxsim" / "sandbox" / ".env.uxsim").write_text("", encoding="utf-8")
    (tmp_path / "uxsim" / "runs" / "round25").mkdir(parents=True)
    py = tmp_path / "backend" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nexit ${FAKE_RC:-0}\n", encoding="utf-8")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    return lane


@pytest.mark.skipif(os.name == "nt", reason="sh が要る")
def test_lane_fail_marks_and_deps(tmp_path):
    lane = _lane_sandbox(tmp_path)
    mb = tmp_path / "uxsim" / "runs" / "round25" / "mailbox_share"

    def run(*args, rc=0, deps=""):
        env = {**os.environ, "FAKE_RC": str(rc), "LANE_DEPS": deps}
        return subprocess.run(["sh", str(lane), *args], env=env, capture_output=True, text=True).returncode

    assert run("share", "camp.yaml", "te-03", "t-a", rc=1) == 1
    mark = mb / "LANE_FAIL_te-03"
    assert mark.is_file() and "scenarios=t-a" in mark.read_text(encoding="utf-8")
    # 依存先に印 → 走らせず exit 3・LANE_SKIP（連鎖する）
    assert run("share", "camp.yaml", "st-04", "s-e", deps="te-03") == 3 and (mb / "LANE_SKIP_st-04").is_file()
    assert run("share", "camp.yaml", "st-07", "s-d", deps="st-04") == 3
    # 依存の無い段は走る
    assert run("share", "camp.yaml", "te-05", "t-q") == 0
    # 同じペルソナの別の場面の成功では印を消さない・同じ場面の走らせ直しの成功で消える
    assert run("share", "camp.yaml", "te-03", "t-other") == 0 and mark.is_file()
    assert run("share", "camp.yaml", "te-03", "t-a") == 0 and not mark.is_file()
    log = (tmp_path / "uxsim" / "runs" / "round25" / "lane_share.log").read_text(encoding="utf-8")
    assert "LANE_END share te-03 exit 1" in log and "LANE_SKIP share st-04" in log
    assert run("--clear-marks", "share") == 0 and not list(mb.glob("LANE_SKIP_*"))


# ---------------------------------------------------------------------------
# 第 10 周の前提（r5_prereqs.py --round 10）— 確認だけ・表と並びが同じ・読み方の見直し
# ---------------------------------------------------------------------------
def _doc_items(name: str) -> list[str]:
    text = (ROUND / name).read_text(encoding="utf-8")
    return re.findall(r"^\| `([a-z0-9_]+)` \|", text.split("## 周の前の一括実行", 1)[1], re.MULTILINE)


NEW_R10 = ("share_restore_confirmable", "share_symbol_restorable", "share_part_chip_anchor_topic", "share_cite_pair",
           "doubt_unplotted_ledger_path", "doubt_te03_course_hint", "studio_prepublish_student", "lane_marks_clear")


def test_round10_items_match_doc_and_defaults():
    m = _prereqs()
    assert list(m.ROUND_ITEMS[10]) == _doc_items("r10_prereqs.md")
    assert set(m.ROUND_ITEMS[10]) <= set(m.ALL_ITEMS)
    for name in NEW_R10:
        assert name in m.ROUND_ITEMS[10] and m.ALL_ITEMS[name].do is None, name  # 確認だけ
    a = m.parse_args(["--round", "10"])
    assert a.freeze_teacher == m.TE01 and a.expect_frozen == "2026.6" and a.expect_release == "5"
    assert a.split_topic == "t17" and a.cepheus_teacher == m.TE03 and a.allow_auto_confirm is False
    assert m.parse_args(["--round", "9"]).split_topic in (None, "")  # 第 9 周までは check の既定 t16
    assert '"--round", "10"' in (ROUND / "r10_prereqs.py").read_text(encoding="utf-8")
    p = load_params("astrophysics", "t-atlas-draft-freeze", "te-01-rookie-seminar")
    assert p["freeze_version"] in ("2026.7", "2026.8")  # 第 10 波: r11 の前提で 2026.8 に上げた


def test_round10_pure_functions_and_reading_fix(tmp_path):
    m = _prereqs()
    # 未登録の公開テンプレート（is_enrollable）を受講中と読まない（第 9 波の読み方の見直し）
    rows = [{"id": "1bad7d9f-x", "is_enrollable": True}]
    assert m.learner_course_row(rows, "1bad7d9f") is None
    assert m.learner_course_row([{"id": "1bad7d9f-y", "is_enrollable": False}], "1bad7d9f")["id"] == "1bad7d9f-y"
    q = [{"entry": {"name": "A"}, "links": [{"instance": {"document_id": "p1"}, "status": "candidate"},
                                             {"instance_document_id": "p2", "status": "revoked"}]},
         {"entry": {"name": "B"}, "links": [{"instance": {"document_id": "p1"}, "status": "rejected"},
                                             {"instance": {"document_id": "z"}, "status": "candidate"}]}]
    sm = m.classify_identity_candidates(q, {"p1", "p2"})
    assert sm["same_paper"] == ["A"] and sm["different_papers"] == ["B"] and sm["restorable_links"] == 2
    assert m.summarize_symbol_links([{"status": "revoked"}, {"status": "candidate"}])["restorable"] == 1
    pool = m.cite_pool([{"kind": "personal", "shared": True, "author_name": "江口 大地"},
                        {"kind": "personal", "shared": True, "author_name": "堀 正和"},
                        {"kind": "standard", "shared": True, "author_name": ""},
                        {"kind": "personal", "shared": False, "author_name": "堀 正和"}], "江口 大地")
    assert [e["author_name"] for e in pool] == ["堀 正和"]
    (tmp_path / "mailbox_share").mkdir()
    (tmp_path / "mailbox_share" / "LANE_FAIL_te-03").write_text("x", encoding="utf-8")
    (tmp_path / "mailbox_share" / "LANE_DONE").write_text("x", encoding="utf-8")
    assert m.lane_marks(tmp_path) == ["mailbox_share/LANE_FAIL_te-03"]
