"""第 25 周 第 4 周の偽所見の是正（runs/round25/harness_fixes_{share,doubt,atlas,studio}.md の第 4 周の節）。

外部呼び出しなし（MockTransport・純関数）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

from uxsim.runner import atlas_exec as ax
from uxsim.runner.actions_exec import unavailable_reason
from uxsim.runner.scenario import STATE_FLAGS, evaluate_until, load_scenario, state_flags_in
from uxsim.runner.state import PersonaSession


def _edge(key, status="candidate", in_draft=False, applied=None, a="A", b="B"):
    dec = {"status": status}
    if applied:
        dec["applied_version"] = applied
    return {"edge_key": key, "from_label": a, "to_label": b, "decision": dec, "in_draft": in_draft}


# ---------------------------------------------------------------------------
# atlas
# ---------------------------------------------------------------------------
def test_atlas_h25_freeze_ready_flag_from_lists():
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    s.scratch["atlas_gaps"] = {"draft_exists": True, "candidates": []}
    s.scratch["atlas_edges"] = {"draft_exists": True, "candidates": [_edge("e1", "accepted"), _edge("e2", "accepted")]}
    ax.refresh_freeze_ready(s)
    assert s.scratch["until_flags"]["freeze_ready"] is False
    assert [r["edge_key"] for r in ax.pending_unreflected(s, "edge", where="candidates")] == ["e1", "e2"]
    s.scratch["atlas_edges"]["candidates"] = [_edge("e1", "accepted", in_draft=True), _edge("e2", "accepted", applied=3)]
    ax.refresh_freeze_ready(s)
    assert s.scratch["until_flags"]["freeze_ready"] is True
    assert "freeze_ready" in STATE_FLAGS and state_flags_in("freeze_ready or steps > 3") == ["freeze_ready"]
    assert evaluate_until("freeze_ready or persona_decides_to_stop", {"freeze_ready": True})


def test_atlas_h25_orphan_incorporate_routes_to_normal_list():
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    s.scratch["atlas_edges"] = {"draft_exists": True, "orphaned_decisions": [],
                                "candidates": [_edge("e9", "candidate"), _edge("e1", "accepted", a="ブラックホール",
                                                                                 b="重力波")]}
    ax.display_numbers(s, "edge", s.scratch["atlas_edges"]["candidates"], "edge_key")

    class C:
        session = s
        args = {"edge_hint": "2"}
    assert ax._route_to_normal(C, "edge") is True
    assert C.args["edge_hint"] == "2"  # 表示の番号でその候補を指し直す
    assert "ブラックホール" in "".join(s.scratch.get("runner_notes") or [])


def test_atlas_h25_scenario_has_normal_preview_and_loop():
    sc = load_scenario("t-atlas-draft-freeze")
    actions = [o.action for st in sc.steps for o in st.options]
    loop = [st for st in sc.steps if st.kind == "repeat"]
    assert "admin.atlas.edges.incorporate_preview" in actions[actions.index("admin.atlas.freeze_blocked_goto"):]
    assert loop and "freeze_ready" in loop[0].until and loop[0].max >= 2
    inner = [o.action for c in loop[0].children for o in c.options]
    assert {"admin.atlas.edges.incorporate_preview", "admin.atlas.edges.incorporate_apply"} <= set(inner)


def test_atlas_h26_unavailable_actions_dropped():
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    assert unavailable_reason("admin.atlas.gaps.accept", s) is None  # 一覧を読む前は外さない
    s.scratch["atlas_gaps"] = {"draft_exists": False, "candidates": []}
    assert unavailable_reason("admin.atlas.gaps.accept", s)
    assert unavailable_reason("admin.atlas.gaps.dismiss", s)
    assert unavailable_reason("admin.atlas.gaps.incorporate_apply", s)
    assert unavailable_reason("admin.atlas.freeze_blocked_goto", s)
    s.scratch["atlas_gaps"]["candidates"] = [{"cluster_key": "g1", "decision": {"status": "candidate"}}]
    assert unavailable_reason("admin.atlas.gaps.accept", s) is None


def test_atlas_h27_numbers_follow_display_order():
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    rows = [_edge("a"), _edge("b"), _edge("c")]
    assert ax.display_numbers(s, "edge", rows, "edge_key") == {"a": 1, "b": 2, "c": 3}
    now = ax.display_numbers(s, "edge", [rows[1], rows[0], rows[2]], "edge_key")
    assert now == {"b": 1, "a": 2, "c": 3}
    assert ax.previous_number_note(s, "edge", "b", 1) == "（前の表示の番号 2）"
    assert ax.previous_number_note(s, "edge", "c", 3) == ""
    assert "ハーネス" in ax.CARD_NUMBER_NOTE and "ハーネス" in ax.SAME_EXPLAIN_NOTE


def test_atlas_h28_fold_head_is_marked():
    out = ax.fold_cards([("A" * 10, ["x" * 50]), ("題名B", ["y" * 50])], frozenset(), 40, "関係の候補")
    assert out[-1].startswith("（ハーネス: 表示の都合で畳んだ — 画面には全カードがある。")


def test_atlas_h29_sea_circles_nested_by_region():
    skeleton = {"levels": {"1": {"regions": [{"id": "r1", "label": "コンパクト天体"}, {"id": "r2", "label": "宇宙論"}],
                                 "nodes": [{"id": "n1", "label": "中性子星", "region": "r1"},
                                           {"id": "n2", "label": "暗黒エネルギー", "region": "r2"}]}}}
    landscape = {"placements": [{"anchor_node_id": "n1", "document_id": "d1"},
                                {"anchor_node_id": "r2", "document_id": "d2"},
                                {"anchor_node_id": "n2", "document_id": "d3"}]}
    lines = ax.sea_map_lines(landscape, skeleton, None)
    i1 = next(i for i, l in enumerate(lines) if l.startswith("- コンパクト天体"))
    i2 = next(i for i, l in enumerate(lines) if l.startswith("- 宇宙論"))
    assert "中性子星" in lines[i1 + 1]
    block = "\n".join(lines[i2 + 1:i2 + 3])
    assert "暗黒エネルギー" in block and "領域そのもの" in block


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------
class _FakeCtx:
    def __init__(self, session, args):
        self.session, self.args = session, dict(args)
        self.missed = []

    def missing(self, name):
        self.missed.append(name)
        self.session.last_status, self.session.last_body = None, {"_precondition": name}


def test_share_h30_explanation_hint_by_kind_word():
    from uxsim.runner import share_exec as sx

    s = PersonaSession(persona_id="te-03", role="TEACHER")
    s.scratch["share_explanations"] = [
        {"id": "p1", "kind": "personal", "title": "私の読み", "author_name": "堀"},
        {"id": "s1", "kind": "standard", "title": "TOV の要約", "author_name": ""}]
    assert sx._explanation_target(_FakeCtx(s, {"explanation_hint": "標準の説明"}))["id"] == "s1"
    assert sx._explanation_target(_FakeCtx(s, {"explanation_hint": "[標準の説明]"}))["id"] == "s1"
    assert sx._explanation_target(_FakeCtx(s, {"explanation_hint": "独自解釈"}))["id"] == "p1"
    assert sx._explanation_target(_FakeCtx(s, {"explanation_hint": "2"}))["id"] == "s1"
    ctx = _FakeCtx(s, {"explanation_hint": "存在しない説明"})
    assert sx._explanation_target(ctx) is None
    sx._explanation_missing(ctx)
    from uxsim.runner.state import project_observation
    text = project_observation("admin.explanations.cite", None, s.last_body)
    assert "1. [教員: 堀] 私の読み" in text and "2. [標準の説明] TOV の要約" in text and "ハーネス" in text


def test_share_h31_h32_name_list_keeps_listing_and_says_clipped():
    from uxsim.runner import share_exec as sx

    rows = [{"name": f"部品{i}"} for i in range(25)]
    out = sx.name_list(rows, 20)
    assert out.count("\n- ") == 19 and "先頭 20 件" in out and "25" not in out
    assert "先頭" not in sx.name_list(rows[:3], 20)


def test_share_h33_part_chip_records_anchor_before_journey():
    sc = load_scenario("s-share-part-chip")
    acts = [o.action for st in sc.steps for o in st.options]
    j = acts.index("learning.personal_network.journey")
    assert "learning.anchors.digest" in acts[:j] and acts[:j].count("learning.chat.ask_selection") >= 2


# ---------------------------------------------------------------------------
# doubt
# ---------------------------------------------------------------------------
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

from uxsim.actions.registry import REGISTRY  # noqa: E402
from uxsim.runner import doubt_exec as dx  # noqa: E402
from uxsim.runner import vision5_exec  # noqa: E402
from uxsim.runner.actions_exec import _Ctx  # noqa: E402
from uxsim.runner.client import EpistemeClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _doubt_session(entry):
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["doubt_target"] = {"target_type": "claim", "target_id": "c1", "label": "主張", "document_id": "d1"}
    s.scratch["doubt_ledger"] = entry
    return s


def _client(handler):
    return EpistemeClient(transport=httpx.MockTransport(handler), base_url="http://test")


def test_doubt_h28_challenge_numbers_match_screen():
    entry = {"exists": True, "verification_status": "untested", "challenges": [
        {"id": "x1", "status": "promoted", "challenge_type": "definitional", "reason": "一"},
        {"id": "x2", "status": "open", "challenge_type": "definitional", "reason": "二"}]}
    sent = []
    s = _doubt_session(entry)

    def handler(req):
        sent.append(str(req.url.path))
        return httpx.Response(200, json={})
    c = _client(handler)
    dx.challenge_withdraw(_Ctx(REGISTRY["admin.doubt.challenge_withdraw"], {"pick": "2"}, s, c))
    assert any(p.endswith("/challenges/x2/withdraw") for p in sent)
    sent.clear()
    s.scratch["doubt_ledger"] = entry  # 取り下げ後の読み直し（モックは空）を戻す
    dx.challenge_withdraw(_Ctx(REGISTRY["admin.doubt.challenge_withdraw"], {"pick": "1"}, s, c))
    assert not sent and "疑義1" in s.last_body["_ui_fact"] and "［取り下げ］" in s.last_body["_ui_fact"]


def test_doubt_h29_read_only_marks_and_js_mirror():
    view = dx.ledger_view({"label": "主張", "target_type": "claim"},
                          {"exists": True, "can_edit": False, "verification_status": "untested",
                           "falsification_candidates": [{"statement": "s"}]})
    assert "［覆る条件を記帳する］［AIに候補を出してもらう］（押せない）" in view
    assert "［根拠の線を追加］（押せない）" in view and "［確認して記帳］［見送る］（押せない）" in view
    js = (ROOT / "frontend/public/js/doubt-atlas.js").read_text(encoding="utf-8")
    i = js.index("function disableLedgerWrites")
    body = js[i:js.index("}", js.index("forEach", i))]
    found = tuple(t for t in (x.strip() for x in "".join(
        part for part in body.split('"')[1::2]).split(",")) if t)
    assert found == dx.READ_ONLY_DISABLED_SELECTORS


def test_doubt_h30_raw_probe_bypasses_ui_guard():
    s = _doubt_session({"exists": True, "can_edit": False})
    sent = []

    def handler(req):
        sent.append((req.method, str(req.url.path)))
        return httpx.Response(403, json={"detail": "この論文の台帳を編集する権限がありません。"})
    dx.scope_record_raw(_Ctx(REGISTRY["admin.doubt.scope_record_raw"], {"condition": "c"}, s, _client(handler)))
    assert sent == [("POST", "/api/admin/doubt/ledger/claim/c1/scopes")]
    assert s.last_body["_ui_fact"].startswith(dx.RAW_PROBE_HEAD) and "HTTP 403" in s.last_body["_ui_fact"]
    sc = load_scenario("t-db-ledger-readonly-probe")
    assert "admin.doubt.scope_record_raw" in [o.action for st in sc.steps for o in st.options]


def test_doubt_h31_expected_row_records_unmet():
    s = _doubt_session({"exists": False})
    s.last_status = 200

    class C:
        session = s
        args = {"expect_row": "true"}
    dx.check_expected_row(C, {"label": "TOV"})
    assert s.scratch["inrun_unmet"][0]["check"] == "ledger_row"
    from uxsim.runner.api import record_inrun_unmet
    from uxsim.schema import RunMeta
    meta = RunMeta(run_id="r", campaign_id="c", domain="astrophysics", snapshot="s")
    assert record_inrun_unmet(meta, s) and meta.blocked_precondition[0]["in_run"] is True


def test_doubt_h33_opening_unverified_moves_first():
    part = ["■ 論文A", "この論文が答えようとした問い", "  問い", "▶ 論文の骨格（章の流れ）（畳まれている・開くと）:",
            "  1. 序", "▶ この論文が確かめていないこと（畳まれている・開くと）:", "  - 前提X",
            "▶ この論文の組み立てをくわしく見る（畳まれている・開くと）:"]
    out = vision5_exec.opening_unverified_first(part)
    assert out[0] == "■ 論文A" and out[2].startswith("▶ この論文が確かめていないこと") and out[3] == "  - 前提X"
    assert len(out) == len(part) + 1


# ---------------------------------------------------------------------------
# studio
# ---------------------------------------------------------------------------
from uxsim.runner import actions_exec as ax_exec  # noqa: E402
from uxsim.schema import HttpTrace, TranscriptStep  # noqa: E402


def test_studio_h23_pinned_swap_is_visible_to_brain():
    s = PersonaSession(persona_id="st-06", role="STUDENT")
    s.scratch.update({"pinned_course_id": "P", "pin_course_strict": True})
    ax_exec.remember_course_rows(s, {"courses": [{"id": "M", "title": "宇宙論入門", "is_enrollable": False},
                                                 {"id": "P", "title": "宇宙論入門", "is_enrollable": True}]})
    assert ax_exec.pinned_course_override(s, "M") == "P"
    note = s.scratch["observation_notes"][-1]
    assert "新しく受講可能なコース" in note and "2. 「宇宙論入門」" in note and "ハーネス" in note and "P" not in note.replace("「", "")


def test_studio_h25_claim_anchor_opens_element_context():
    s = PersonaSession(persona_id="st-06", role="STUDENT")
    s.course_id, s.topic_id = "c1", "t1"
    s.material = {"chunks": [{"text": "本文 ![[claim:cl1]]", "evidence_items": [{"kind": "claim", "id": "cl1", "title": "主張"}]}]}
    seen = []

    def handler(req):
        seen.append(str(req.url.path))
        return httpx.Response(200, json={"focus": {"label": "主張"}})
    ctx = _Ctx(REGISTRY["learning.component.context"], {"element_ref": "1"}, s, _client(handler))
    ax_exec._component_context(ctx)
    assert seen and seen[0].endswith("/elements/claim/cl1/context")
    assert "要素の文脈として開いた" in s.last_body["_ui_fact"]


def test_studio_h25_precondition_is_not_a_trace_break():
    from uxsim.oracles import behavior

    st = TranscriptStep(seq=1, persona_id="st", action_id="learning.component.context",
                        http=[HttpTrace(method="GET", path="/x", status=None, error="precondition:component_anchor")])
    assert behavior._trace_break(st) == ""


def test_studio_h24_campaign_cross_check():
    from uxsim.oracles import studio

    def step(seq, at, key, digest):
        t = HttpTrace(method="GET", path="/x", status=200)
        t.digest = {key: digest}
        return TranscriptStep(seq=seq, at=at, persona_id="p", action_id="a", http=[t])
    teacher = [step(3, "2026-10-24T23:34:00Z", "studio_split",
                    {"scope": "topic", "course_id": "c", "topic_id": "t", "display_count": 3, "text_sha": "x"})]
    student = [step(7, "2026-10-24T23:40:00Z", "studio_student",
                    {"course_id": "c", "topic_id": "t", "material": {"topic_id": "t", "topic_based": True, "pages": 4}})]
    rows = studio.check_campaign({"te": teacher, "st": student})
    assert rows and rows[0]["mismatch"] is True and rows[0]["teacher_run"] == "te"
    assert studio.check_campaign({"te": teacher + student}) == []  # 同じ run の組は _cross_checks の担当
