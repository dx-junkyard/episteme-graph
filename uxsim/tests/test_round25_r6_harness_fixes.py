"""第 25 周 第 6 周の偽所見・観察の欠けの是正（runs/round25/harness_fixes_{share,doubt,atlas,studio}.md の第 6 周の節）
と第 7 周の前提スクリプト（``r5_prereqs.py --round 7``）。

外部呼び出しなし（純関数・ファイルの静的検査・偽の頭脳）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

from uxsim.llm import TRANSCRIPT_SEQ, MailboxPersonaLLM
from uxsim.oracles import atlas as atlas_oracle
from uxsim.oracles import studio as so
from uxsim.oracles.findings import FindingFactory
from uxsim.persona.agent import NO_ACTION, StepDecision
from uxsim.runner import api as api_mod
from uxsim.runner import atlas_exec as ax
from uxsim.runner import doubt_exec as dx
from uxsim.runner import share_exec as sx
from uxsim.runner import studio_exec as stx
from uxsim.runner import state as state_mod
from uxsim.runner.api import Budget, RunWriter, ScenarioRunner
from uxsim.runner.scenario import ScenarioParams, load_scenario, parse_scenario
from uxsim.runner.state import PersonaSession
from uxsim.sandbox import brain
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

UXSIM = Path(__file__).resolve().parents[1]
ROUND = UXSIM / "runs" / "round25"
CART = "/api/admin/cartridges/astrophysics/atlas"


def _flat(scenario_id: str) -> list[tuple[str, list[str], bool]]:
    """場面の段（kind, 行為 id の列, optional）を上から順に。"""
    out = []

    def walk(steps):
        for st in steps:
            if getattr(st, "children", None):
                walk(st.children)
            else:
                out.append((st.kind, [o.action for o in st.options], bool(getattr(st, "optional", False))))
    walk(load_scenario(scenario_id).steps)
    return out


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------
def test_share_h39_revoke_and_reject_follow_entry_open():
    steps = _flat("t-share-identity-queue")
    i_open = next(i for i, (_, acts, _) in enumerate(steps) if acts == ["admin.library.entry_open"])
    assert steps[i_open][0] != "choose"  # 詳細を開く手は代替に並べない（必須の段）
    after = [acts for _, acts, _ in steps[i_open + 1:]]
    assert ["admin.library.identity_link_revoke"] in after
    assert any("admin.library.identity_link_decide" in a for a in after)
    # 取り消しの段は任意（確定の行が無ければ飛ばせる）
    revoke = next(s for s in steps[i_open + 1:] if s[1] == ["admin.library.identity_link_revoke"])
    assert revoke[2] is True


def test_share_h40_expertise_tag_is_a_field_not_a_stance():
    data = yaml.safe_load((UXSIM / "domains/astrophysics/scenario_params/t-share-explain-endorse-cite.yaml")
                          .read_text(encoding="utf-8"))
    tags = [data["params"]["expertise_tag"]] + [p.get("expertise_tag") for p in data["personas"].values()
                                               if p.get("expertise_tag")]
    assert not any("立場" in t or "担当" in t for t in tags), tags


# ---------------------------------------------------------------------------
# doubt
# ---------------------------------------------------------------------------
def test_doubt_h39_num_hint_reads_serial_names_one_source():
    assert sx._NUM_HINT is state_mod.NUM_HINT_RE and dx._NUM_HINT_RE is state_mod.NUM_HINT_RE
    items = [{"reason": "a"}, {"reason": "b"}, {"reason": "c"}]
    for hint, want in (("疑義2", "b"), ("提案1", "a"), ("前提3", "c"), ("主張 2", "b"), ("検証提案1", "a"),
                       ("リンク2", "b"), ("２", "b"), ("3つ目", "c")):
        assert (dx.hint_pick(items, hint, ("reason",)) or {}).get("reason") == want, hint
    assert dx.hint_pick(items, "疑義4", ("reason",)) is None
    assert sx.pick(items, "疑義2", ("reason",))["reason"] == "b"


def test_doubt_h40_popup_head_names_the_component():
    s = PersonaSession(persona_id="st-05")
    s.scratch["last_component_graph"] = {"focus": {"id": "c1", "label": "TOV residual loss"}}
    assert dx._center_component_label(s, "c1") == "TOV residual loss"
    assert dx._center_component_label(s, "c2") == ""


def test_doubt_h42_assumption_filter_shows_choices():
    head = dx.assumption_lines({"items": []}, "candidate")[0]
    assert "表示: 候補" in head and "候補 / 確定済み / 却下済み（保持） / すべて" in head
    js = (UXSIM.parent / "frontend/public/js/doubt-atlas.js").read_text(encoding="utf-8")
    for opt in dx.ASSUMPTION_FILTER_OPTIONS:
        assert f">{opt}</option>" in js, opt


# ---------------------------------------------------------------------------
# atlas
# ---------------------------------------------------------------------------
def test_atlas_h36_h39_h40_brain_header():
    head = brain.persona_request_header({"transcript_seq": 12})
    assert "seq 12" in head[0] and "自分で数えない" in head[0]
    assert "番号はハーネスが振った印" in brain.NUMBERING_NOTE and "画面に番号は無い" in brain.NUMBERING_NOTE
    assert "引用" in brain.GONE_NOTE
    assert "seq なし" in brain.persona_request_header({})[0]


def test_atlas_h39_mailbox_carries_transcript_seq(tmp_path, monkeypatch):
    llm = MailboxPersonaLLM(root=tmp_path)
    seen = {}

    def fake_ask(kind, payload):
        seen.update(payload)
        return {"output": {"action_id": "none"}}
    monkeypatch.setattr(llm.box, "ask", fake_ask)
    token = TRANSCRIPT_SEQ.set(7)
    try:
        llm.complete_json("sys", [{"role": "user", "content": "x"}], "{}")
    finally:
        TRANSCRIPT_SEQ.reset(token)
    assert seen["transcript_seq"] == 7 and seen["messages"] == [{"role": "user", "content": "x"}]
    seen.clear()
    llm.complete_json("sys", [], "{}")
    assert "transcript_seq" not in seen


def test_atlas_h37_index_note_counts_as_empty_reason():
    assert ax._edge_digest({"candidates": [], "index_note": "作っています"})["index_note"] is True
    t = HttpTrace(method="GET", path=CART + "/edge-candidates", status=200,
                  digest={"atlas": {"kind": "edge_candidates", "candidates": [], "empty_notes": False,
                                    "index_note": True, "orphaned_labels": []}})
    steps = [TranscriptStep(seq=1, persona_id="te-01", action_id="admin.atlas.edges.list", http=[t])]
    factory = FindingFactory(RunMeta(run_id="r", campaign_id="c", domain="astro", snapshot="s"))
    assert not [f for f in atlas_oracle._check_freeze_readiness(steps, factory) if "候補が無い理由" in f.hypothesis]


class _Agent:
    def __init__(self):
        self.calls = []

    def step(self, **kw):
        self.calls.append(kw)
        allowed = kw.get("allowed") or []
        only = kw.get("reaction_only") or kw.get("finishing")
        return StepDecision(action_id=NO_ACTION if only else (allowed[0].id if allowed else NO_ACTION))


def test_atlas_h38_fixed_step_without_target_is_skipped(tmp_path, monkeypatch):
    executed = []

    def fake_execute(action_id, args, session, client):
        executed.append(action_id)
        session.last_status, session.last_body = 200, {}
        return [HttpTrace(method="GET", path="/x", status=200)]
    monkeypatch.setattr(api_mod, "execute", fake_execute)
    monkeypatch.setattr(api_mod, "project_observation", lambda a, st, b: f"画面({a})")
    sess = PersonaSession(persona_id="te-01")
    sess.scratch["atlas_gaps"] = {"draft_exists": True, "candidates": []}  # 下書きあり・候補 0
    sc = parse_scenario({"id": "t-h38", "population": "teachers", "goal": "g",
                         "steps": [{"do": "admin.atlas.gaps.draft_from_frozen"}, {"do": "admin.atlas.open"}],
                         "stop_when": ["gave_up"]})
    agent = _Agent()
    r = ScenarioRunner(agent, sc, ScenarioParams({}), sess, client=None, writer=RunWriter(tmp_path),
                       budget=Budget(limits={}))
    r.run()
    assert executed == ["admin.atlas.open"]
    lines = [json.loads(x) for x in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("画面に対象が無いので飛ばした" in n for n in lines[0]["runner_notes"])
    # 頭脳への要求に「直前に見た画面」の seq を渡す（H39）
    assert any(c.get("screen_seq") == 1 for c in agent.calls)


# ---------------------------------------------------------------------------
# studio
# ---------------------------------------------------------------------------
def _st(seq, at, key, digest, pid="p"):
    t = HttpTrace(method="GET", path="/x", status=200)
    t.digest = {key: digest}
    return TranscriptStep(seq=seq, at=at, persona_id=pid, action_id="a", http=[t])


def test_studio_h31_cross_compares_saved_text_only():
    base = {"scope": "topic", "course_id": "c", "topic_id": "t0"}
    teacher = [_st(4, "2026-10-27T12:00:00Z", "studio_split", {**base, "display_count": 4, "text_sha": "s",
                                                                "matches_stored": True}),
               _st(6, "2026-10-27T12:05:00Z", "studio_split", {**base, "display_count": 3, "text_sha": "draft",
                                                                "matches_stored": False, "can_save": False})]
    student = [_st(3, "2026-10-27T12:10:00Z", "studio_student",
                   {"course_id": "c", "topic_id": "t0", "material": {"topic_id": "t0", "topic_based": True, "pages": 4}})]
    rows = so.check_campaign({"te": teacher, "st": student})
    drafts = [r for r in rows if r["draft_only"]]
    compared = [r for r in rows if not r["draft_only"]]
    assert drafts and drafts[0]["mismatch"] is False and drafts[0]["teacher_seq"] == 6
    assert compared and compared[0]["teacher_seq"] == 4 and compared[0]["mismatch"] is False


def test_studio_h32_saves_compared_with_student_api():
    save = {"course_id": "c", "topic_id": "t16", "status": 200, "text_sha": "x",
            "preview": {"delivered_count": 2, "mismatch": False}}
    runs = {"te": [_st(9, "2026-10-27T12:00:00Z", "studio_saved", save)]}
    rows = so.check_campaign_saves(runs, lambda c, t: 2)
    assert rows == [{**rows[0], "kind": "student_api", "student_total_slides": 2, "preview_slides": 2,
                     "mismatch": False, "unavailable": False}]
    assert so.check_campaign_saves(runs, lambda c, t: 3)[0]["mismatch"] is True
    assert so.check_campaign_saves(runs, lambda c, t: None)[0]["unavailable"] is True
    assert so.student_persona_of({"a": [_st(1, "x", "k", {}, pid="te-01")],
                                  "b": [_st(1, "x", "k", {}, pid="st-03-x")]}) == "st-03-x"


def test_studio_h33_lecture_pause_and_resume():
    s = PersonaSession(persona_id="st-07")
    s.topic_id = "t11"
    s.scratch.update({"lecture_deck": [{"display_text": "a"}, {"display_text": "b"}, {"display_text": "c"}],
                      "lecture_pos": 1, "lecture_topic": "t11", "lecture_paused": False})
    note = stx.lecture_paused_note("learning.chat.ask", s)
    assert note and stx.LECTURE_PAUSED_BAR in note and "スライド 2 / 3" in note
    assert stx.lecture_paused_note("learning.symbol.lookup", s)  # 一時停止中は他の観察にもモードバーの行
    assert stx.lecture_paused_note("learning.lecture.next", s) is None
    s.topic_id = "t0"
    assert stx.lecture_paused_note("learning.chat.ask", s) is None  # 別のトピックでは講義は動いていない
    steps = _flat("s-lecture-then-trace")
    assert any("learning.lecture.resume" in acts for _, acts, _ in steps)
    js = (UXSIM.parent / "frontend/public/js/app.js").read_text(encoding="utf-8")
    assert "🎙 講義を一時停止中" in js and "▶ 再開" in js


def test_studio_audio_generate_action_registered():
    from uxsim.actions.registry import REGISTRY

    a = REGISTRY["admin.lecture_studio.audio_generate"]
    assert a.llm_cost == "product" and "admin.lecture_studio.audio_generate" in stx.HANDLERS


# ---------------------------------------------------------------------------
# 第 7 周の前提スクリプト
# ---------------------------------------------------------------------------
def _prereqs():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_round7_items_and_defaults():
    m = _prereqs()
    names = m.ROUND_ITEMS[7]
    assert len(names) == len(set(names)) and all(n in m.ALL_ITEMS for n in names)
    for want in ("share_pair_frozen", "share_symbol_link_frozen", "pinn_editor", "pinn_editor_removed",
                 "doubt_load_recompute", "ledger_lines_unmoved_component", "atlas_footprint_student",
                 "atlas_added_items", "studio_course_release", "atlas_freeze_version", "studio_t16_no_split",
                 "studio_visibility", "persona_profile_names"):
        assert want in names, want
    a = m.parse_args(["--round", "7"])
    assert a.probe_teacher == m.TE01 and a.assumption_on_cepheus is True and a.freeze_teacher == "te-05-delegator-lab"
    assert a.expect_frozen == "2026.3" and m.next_version(a.expect_frozen) == "2026.4"
    assert m.parse_args(["--round", "6"]).assumption_on_cepheus in (None, False)
    # 第 5・6 周の並びは不変
    assert m.ROUND_ITEMS[5] == m.ITEM_NAMES and m.ROUND_ITEMS[6] == m.R6_ITEM_NAMES
    params = yaml.safe_load((UXSIM / "domains/astrophysics/scenario_params/t-atlas-draft-freeze.yaml")
                            .read_text(encoding="utf-8"))
    assert params["personas"]["te-05-delegator-lab"]["freeze_version"] == "2026.4"


def test_round7_pure_helpers():
    m = _prereqs()
    q = [{"entry": {"id": "e1"}, "links": [{"instance": {"document_id": "tex"}}]},
         {"entry": {"id": "e2"}, "links": [{"instance": {"document_id": "tex"}, "basis_label": "同じ論文の別の版での一致"},
                                         {"instance": {"document_id": "pdf"}, "basis_label": "同じ論文の別の版での一致"}]}]
    assert m.pair_candidate(q, {"tex"}, {"pdf"})["entry"]["id"] == "e2"
    assert m.unmoved_anchor("TOV network map", "TOV  network map") and not m.unmoved_anchor("TOV network map", "Radius")
    assert not m.unmoved_anchor("", "")


def test_round7_entry_is_thin():
    text = (ROUND / "r7_prereqs.py").read_text(encoding="utf-8")
    assert '"--round", "7"' in text and "def check_" not in text


def test_round7_claim_sequence_summary_and_recompute_courses():
    m = _prereqs()
    assert "doubt_claim_sequence_compare" in m.ROUND_ITEMS[7] and "doubt_load_recompute" in m.ROUND_ITEMS[7]
    assert set(m.RECOMPUTE_COURSES) == {"1bad7d9f", "6be229e1", "74ca9b48", "0d70b5d0"}
    rows = [{"target_type": "claim", "target_id": "intro", "with_sequence_edges": 40, "without_sequence_edges": 1},
            {"target_type": "claim", "target_id": "mid", "with_sequence_edges": 10, "without_sequence_edges": 10},
            {"target_type": "equation", "target_id": "e", "with_sequence_edges": None, "without_sequence_edges": 2}]
    sm = m.summarize_claim_sequence(rows, top=1)
    assert sm == {"rows": 3, "lowered": 1, "top": [{"target_type": "claim", "target_id": "intro", "lowered": True}],
                  "top_all_lowered": True}
    assert "仮説どおり" in m.claim_sequence_fact(sm, "abcdef0123")
    assert "合わない" in m.claim_sequence_fact(m.summarize_claim_sequence(rows, top=2), "abcdef0123")


def test_ik1031_view_carries_atlas_node_id():
    from uxsim.runner.actions_exec import atlas_last_node_view

    s = PersonaSession(persona_id="st-07")
    s.scratch["atlas_last_node"] = {"label": "中性子星", "status": "unvisited", "node_id": "c_ns"}
    v = atlas_last_node_view(s)
    assert v["atlas_node_id"] == "c_ns" and v["atlas_node_label"] == "中性子星"
    s.scratch["atlas_last_node"] = {"label": "中性子星"}
    assert "atlas_node_id" not in atlas_last_node_view(s)
