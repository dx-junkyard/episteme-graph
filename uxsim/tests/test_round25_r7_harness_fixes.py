"""第 25 周 第 7 周の偽所見・運転の欠けの是正（runs/round25/judge_*_r7.md の「偽所見・ハーネス」節と
harness_fixes_{share,doubt,atlas,studio}.md の第 7 周の項）と第 8 周の前提スクリプト（``r5_prereqs.py --round 8``）。

外部呼び出しなし（MockTransport・純関数・偽の ctx・tmp のファイル）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import httpx
import yaml

from uxsim.actions.registry import REGISTRY
from uxsim.oracles import run_all
from uxsim.oracles import studio as so
from uxsim.runner import actions_exec
from uxsim.runner import doubt_exec as dx
from uxsim.runner import share_exec as sx
from uxsim.runner import studio_exec as stx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import STATE_FLAGS, evaluate_until, load_scenario, state_flags_in
from uxsim.runner.state import PersonaSession, render_material_text
from uxsim.sandbox import brain

UXSIM = Path(__file__).resolve().parents[1]
ROUND = UXSIM / "runs" / "round25"


def _client(handler):
    return EpistemeClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _actions(scenario_id: str) -> list[str]:
    out: list[str] = []

    def walk(steps):
        for st in steps:
            if getattr(st, "children", None):
                walk(st.children)
            out.extend(o.action for o in st.options)
    walk(load_scenario(scenario_id).steps)
    return out


# ---------------------------------------------------------------------------
# share H42: ラベルの選択は完全一致 → 前方一致（双方向の部分一致にしない）
# ---------------------------------------------------------------------------
def test_share_h42_label_pick_is_not_bidirectional():
    rows = [{"label": "TOV"}, {"label": "TOV eq."}, {"label": "TOV equation"}]
    assert sx.pick_strict(rows, "TOV eq.", ("label",))[0]["label"] == "TOV eq."
    assert sx.pick_strict(rows, "tov", ("label",))[0]["label"] == "TOV"
    hit, many = sx.pick_strict(rows, "TOV eq", ("label",))  # 前方一致が 2 件 → 選ばずに列挙
    assert hit is None and [r["label"] for r in many] == ["TOV eq.", "TOV equation"]
    assert sx.pick_strict([{"label": "TOV"}], "TOV residual", ("label",)) == (None, [])  # 短い語が長い語を飲まない


def test_share_h42_label_dismiss_does_not_hit_shorter_alias():
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch["share_entry"] = {"id": "e1", "name": "TOV residual loss"}
    s.scratch["share_entry_labels"] = [{"id": "l1", "label": "TOV", "status": "active"},
                                       {"id": "l2", "label": "TOV eq.", "status": "active"}]
    sent = []

    def handler(req):
        sent.append(str(req.url.path))
        return httpx.Response(200, json={"facts": [], "labels": []})
    sx._label_dismiss(_Ctx(REGISTRY["admin.library.label_dismiss"], {"label": "TOV eq.", "review_note": "r"}, s,
                           _client(handler)))
    assert any(p.endswith("/labels/l2/dismiss") for p in sent) and not any("/labels/l1/" in p for p in sent)


# ---------------------------------------------------------------------------
# share H43: 引用先は id の頭で引く・止めたら「送っていない」
# ---------------------------------------------------------------------------
def test_share_h43_cite_target_by_course_id():
    opts = [{"id": "1bad7d9f-aaaa", "title": "宇宙物理 論文紹介ゼミ"}, {"id": "6be229e1-bbbb", "title": "宇宙物理 論文紹介ゼミ"}]
    assert sx.pick_strict(opts, "6be229e1", ("id", "title"))[0]["id"].startswith("6be229e1")
    hit, many = sx.pick_strict(opts, "宇宙物理 論文紹介ゼミ", ("id", "title"))
    assert hit is not None  # 完全一致は先頭（同題名は製品の課題 IK-0407 — id で引くのが是正）
    assert "送っていない" in sx.CITE_NOT_SENT
    sc = yaml.safe_load((UXSIM / "scenarios/goal/teacher/t-share-explain-endorse-cite.yaml").read_text(encoding="utf-8"))
    cite = next(st for st in sc["steps"] if st.get("do") == "admin.explanations.cite")
    assert "cite_course_id" in cite["with"]
    assert "cite_course_id" in REGISTRY["admin.explanations.cite"].args


# ---------------------------------------------------------------------------
# share H45: 前提の対の確定リンクは取り消さない（revoke_target_hint で指す）
# ---------------------------------------------------------------------------
def test_share_h45_revoke_protects_prerequisite_pair():
    assert sx.revoke_protected_entries({"revoke_protect_entry_ids": "67727cb0 abc"}) == ["67727cb0", "abc"]
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    s.scratch["share_entry"] = {"id": "67727cb0-9986", "name": "TOV residual loss"}
    sent = []

    def handler(req):
        sent.append((req.method, str(req.url.path)))
        return httpx.Response(200, json={"identity_links": [{"id": "k1", "status": "confirmed", "instance_label": "x"}]})
    args = {"reason": "r", "revoke_protect_entry_ids": "67727cb0"}
    sx._library_identity_link_revoke(_Ctx(REGISTRY["admin.library.identity_link_revoke"], args, s, _client(handler)))
    assert not any(m == "POST" for m, _ in sent)
    assert "取り消さない" in s.last_body["_ui_fact"]
    sent.clear()
    sx._library_identity_link_revoke(_Ctx(REGISTRY["admin.library.identity_link_revoke"],
                                          {**args, "revoke_target_hint": "x"}, s, _client(handler)))
    assert ("POST", "/api/admin/deliberation/identity-links/k1/revoke") in sent
    params = yaml.safe_load((UXSIM / "domains/astrophysics/scenario_params/t-share-identity-queue.yaml")
                            .read_text(encoding="utf-8"))["params"]
    assert params["revoke_protect_entry_ids"] == "67727cb0" and "revoke_target_hint" in params


# ---------------------------------------------------------------------------
# share H46: 文字の送信でも ⚓ のポップオーバー表示中は selection.element_* を送る（1 往復だけ）
# ---------------------------------------------------------------------------
def _chat_bodies(seen):
    return [json.loads(b) for m, p, b in seen if m == "POST" and p.endswith("/chat")]


def test_share_h46_text_chat_declares_open_component():
    seen: list = []

    def handler(req):
        seen.append((req.method, str(req.url.path), req.content.decode() if req.content else ""))
        if str(req.url.path).endswith("/context"):
            return httpx.Response(200, json={"instance": {"component": {"label": "TOV residual loss"}}})
        return httpx.Response(200, json={"answer": "はい", "sources": []})
    s = PersonaSession(persona_id="st-08", course_id="C", topic_id="t1", uses_voice=False)
    s.remember("components", "comp-1")
    client = _client(handler)
    actions_exec.execute("learning.component.context", {"component_id": "comp-1"}, s, client)
    actions_exec.execute("learning.chat.ask", {"message": "この部品の主張は？"}, s, client)
    sel = _chat_bodies(seen)[-1]["screen_context"]["selection"]
    assert sel["kind"] == "element" and sel["element_type"] == "component" and sel["element_id"] == "comp-1"
    assert sel["element_label"] == "TOV residual loss"
    actions_exec.execute("learning.chat.ask", {"message": "次は？"}, s, client)
    assert "screen_context" not in _chat_bodies(seen)[-1]


# ---------------------------------------------------------------------------
# share H44 / 第 8 周の前提: 記号リンクは探すまで・確定は人の判断
# ---------------------------------------------------------------------------
def _prereqs():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeCtx:
    def __init__(self, responses=None):
        self.calls: list[tuple[str, str]] = []
        self.responses = responses or {}

    def call(self, who, method, path, **kw):
        self.calls.append((method, path))
        return self.responses.get((method, path), (200, {}))


def test_share_h44_symbol_prereq_does_not_auto_confirm(tmp_path, monkeypatch):
    m = _prereqs()
    monkeypatch.setattr(m, "state_file", lambda n: tmp_path / f"r{n}.json")
    tex, pdf = {"tex1"}, {"pdf1"}
    rows = [m.symbol_candidate_row({"id": "a", "instance_label": "F_EOS", "instance_document_id": "pdf1",
                                    "reason": "定義の文", "status": "candidate"}, tex, pdf),
            m.symbol_candidate_row({"id": "b", "instance_label": "L_TOV", "instance_document_id": "tex1",
                                    "reason": "TOV の残差", "status": "candidate"}, tex, pdf)]
    assert [r["version"] for r in rows] == ["PDF 版", "TeX 版"]
    ctx = _FakeCtx()
    r = m.Result(False, [], data={"who": "te-03", "entry_id": "E", "rows": rows, "tex_ids": ["tex1"]})
    a = argparse.Namespace(round=8, allow_auto_confirm=False, symbol_hint="L_TOV", symbol_links=1)
    log = m.do_share_symbol(ctx, a, r)
    assert not any("/confirm" in p or "/freeze" in p for _, p in ctx.calls)
    assert any("確定は人の判断" in line for line in log)
    assert json.loads((tmp_path / "r8.json").read_text())["share_symbol_candidates"][1]["symbol"] == "L_TOV"
    picked = m.pick_symbol_candidates(rows, hint="L_TOV", course_docs={"tex1"})
    assert [p["link_id"] for p in picked] == ["b"]  # 自動確定を許しても教材の TeX 版・hint に合うものだけ
    a2 = argparse.Namespace(round=8, allow_auto_confirm=True, symbol_hint="L_TOV", symbol_links=1)
    m.do_share_symbol(ctx, a2, r)
    assert ("POST", "/api/admin/deliberation/identity-links/b/confirm") in ctx.calls
    assert ("POST", "/api/admin/deliberation/identity-links/a/confirm") not in ctx.calls
    assert m.misconfirmed_symbol_links([{**rows[0], "status": "confirmed"}, {**rows[1], "status": "confirmed"}]) \
        == [{**rows[0], "status": "confirmed"}]
    assert "--allow-auto-confirm" in (ROUND / "r7_prereqs.md").read_text(encoding="utf-8")


def test_round8_items_and_defaults():
    m = _prereqs()
    names = m.ROUND_ITEMS[8]
    assert len(names) == len(set(names)) and all(n in m.ALL_ITEMS for n in names)
    for want in ("share_fixed_course_anchors", "share_pair_frozen", "share_symbol_link_frozen",
                 "share_symbol_misconfirm_revoked", "share_alias_tov", "share_revoke_target",
                 "doubt_support_line_compare", "doubt_no_view_teacher", "pinn_editor_removed",
                 "atlas_no_draft", "atlas_footprint_student", "atlas_footprintless_student", "atlas_freeze_version",
                 "studio_course_release", "studio_course_editor"):
        assert want in names, want
    a = m.parse_args(["--round", "8"])
    assert a.freeze_teacher == m.TE01 and a.expect_frozen == "2026.4" and a.symbol_hint == "L_TOV"
    assert a.allow_auto_confirm is False and a.footprintless_student == "st-01"
    assert m.alias_state([{"label": "TOV", "status": "dismissed"}], "tov") == "dismissed"
    assert m.alias_state([{"label": "TOV eq.", "status": "active"}], "TOV") == "absent"
    q = [{"entry": {"id": "67727cb0-x", "name": "対"}, "links": [{"status": "confirmed", "instance_label": "p"}]},
         {"entry": {"id": "zz", "name": "別"}, "links": [{"status": "confirmed", "instance_label": "q", "id": "L"}]}]
    assert [r["link_label"] for r in m.revoke_targets(q, "67727cb0-9986")] == ["q"]
    sm = m.summarize_support_lines([{"target_type": "claim", "target_id": "c1", "with_sequence_edges": "several",
                                     "without_sequence_edges": "single"},
                                    {"target_type": "claim", "target_id": "c2", "with_sequence_edges": "none",
                                     "without_sequence_edges": "none"}])
    assert sm["rows"] == 2 and sm["changed"] == 1 and "several → single" in sm["examples"][0]
    assert m.anchor_open_summary([("t1", "A", True), ("t2", "B", False)])["closed_examples"] == ["t2「B」"]
    assert (ROUND / "r8_prereqs.md").is_file() and (ROUND / "r8_prereqs.py").is_file()


# ---------------------------------------------------------------------------
# atlas H41 / H42 / H43
# ---------------------------------------------------------------------------
def test_atlas_h41_bound_topics_from_owner_propose():
    m = _prereqs()
    body = {"proposals": [{"bindings": [{"topic_id": "t1", "topic_title": "A", "current": "n1"},
                                        {"topic_id": "t2", "topic_title": "B", "current": ""}]},
                          {"bindings": [{"topic_id": "t1", "topic_title": "A", "current": "n1"}]}]}
    assert m.bound_topics_from_propose(body) == [{"id": "t1", "title": "A", "atlas_node_id": "n1"}]
    assert m.bound_topics_from_propose(None) == []


def test_atlas_h42_binding_body_uses_topic_bindings():
    m = _prereqs()
    body = m.atlas_binding_put_body("astrophysics", [{"topic_id": "t1", "atlas_node_id": "n1"},
                                                     {"topic_id": "t2", "atlas_node_id": ""}])
    assert body == {"cartridge_id": "astrophysics", "topic_bindings": [{"topic_id": "t1", "atlas_node_id": "n1"}]}


def test_atlas_h43_student_scenes_pin_the_campaign_course():
    for sid in ("s-atlas-threads-and-placement", "s-share-part-chip", "s-share-symbol-concept",
                "s-db-ledger-lines", "s-db-map-ask-en", "s-studio-lecture-pages", "s-lecture-then-trace"):
        assert load_scenario(sid).raw.get("pin_course") is True, sid


# ---------------------------------------------------------------------------
# doubt H43〜H47
# ---------------------------------------------------------------------------
def test_doubt_h43_incomplete_body_sent_explicitly():
    body = dx.raw_probe_incomplete_body({"condition": "c", "reason": "r"})
    assert body["evidence_quote"] == "" and body["condition"] == "c"
    assert "既定値で埋めた" in dx.RAW_PROBE_HEAD
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    s.scratch["doubt_target"] = {"target_type": "claim", "target_id": "c1", "label": "主張", "document_id": "d1"}
    s.scratch["doubt_ledger"] = {"exists": True, "can_edit": False}
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(403, json={"detail": "権限がありません。"})
    dx.scope_record_raw_incomplete(_Ctx(REGISTRY["admin.doubt.scope_record_raw_incomplete"], {"condition": "c"}, s,
                                        _client(handler)))
    assert sent and sent[0]["evidence_quote"] == ""
    assert s.last_body["_ui_fact"].startswith(dx.RAW_PROBE_INCOMPLETE_HEAD)
    assert "admin.doubt.scope_record_raw_incomplete" in _actions("t-db-ledger-readonly-probe")


def test_doubt_h44_scenes_wire_withdrawn_and_card_ledger():
    acts = _actions("t-db-challenge-from-node")
    assert acts.index("admin.doubt.withdrawn_challenges_open") > acts.index("admin.doubt.challenge_withdraw")
    acts = _actions("t-db-assumption-review")
    assert acts.index("admin.doubt.assumption_open_ledger") > acts.index("admin.doubt.load_recompute")
    assert dx.ledger_from_source("確定した前提") == "assumptions" and dx.ledger_from_source("") == ""
    assert dx.ledger_from_source("散布図の点") == "atlas" and dx.ledger_from_source("open") == "open"

    class C:
        session = PersonaSession(persona_id="te-02", role="TEACHER")
    C.session.scratch["doubt_assumptions"] = {"items": [
        {"id": "a1", "status": "confirmed", "statement": "前提", "ledger_document_id": "d9"},
        {"id": "a2", "status": "candidate", "statement": "候補", "ledger_document_id": "d9"}]}
    rows = dx._targets_from(C, "assumptions")
    assert rows == [{"target_type": "assumption", "target_id": "a1", "label": "前提", "document_id": "d9"}]


def test_doubt_h46_question_after_component_and_line_follows_hint():
    acts = _actions("s-db-ledger-lines")
    assert acts.index("learning.component.context") < acts.index("learning.chat.ask")
    s = PersonaSession(persona_id="st-08", course_id="C", topic_id="t1")
    s.material = {"chunks": [{"text": "![[component:comp_002]] と ![[component:comp_001]]",
                              "evidence_items": [{"kind": "component", "id": "comp_001", "title": "EOS network map"},
                                                 {"kind": "component", "id": "comp_002", "title": "TOV network map"}]}]}
    s.scratch["component_center"] = "comp_001"
    assert dx.component_line_target(s, "TOV network map") == ("comp_002", "TOV network map")
    assert dx.component_line_target(s, "存在しない部品") == ("", "")  # 当たらないときだけ中心へ倒す（呼び出し側）


def test_doubt_h47_map_question_note_is_language_neutral():
    sc = (UXSIM / "scenarios/goal/student/s-db-map-ask-en.yaml").read_text(encoding="utf-8")
    assert "英語で尋ねる" not in sc
    params = yaml.safe_load((UXSIM / "domains/astrophysics/scenario_params/s-db-map-ask-en.yaml").read_text(encoding="utf-8"))
    st07 = params["personas"]["st-07-m1-grb-voice"]["map_question"]
    camp = (UXSIM / "campaigns/astrophysics/c-r25-doubt-r7.yaml").read_text(encoding="utf-8")
    assert any("぀" <= ch <= "ヿ" for ch in st07) and "map_question の日本語の問い" in camp


# ---------------------------------------------------------------------------
# studio H34 / H35 / 投影の $…$（judge_studio_r7 §8）
# ---------------------------------------------------------------------------
def test_studio_h34_student_from_campaign_and_phase_files(tmp_path):
    camp_dir = tmp_path / "c-r25-studio-r7"
    camp_dir.mkdir()
    want = yaml.safe_load((UXSIM / "campaigns/astrophysics/c-r25-studio-r7.yaml").read_text(encoding="utf-8"))
    assert run_all.campaign_first_student(camp_dir) == want["personas"]["students"][0]
    assert run_all.cross_file_name("before") == "campaign_studio_cross.before.jsonl"
    assert run_all.cross_file_name("") == "campaign_studio_cross.jsonl"
    so.check_campaign_dir(camp_dir, out_name=run_all.cross_file_name("after"))
    assert (camp_dir / "campaign_studio_cross.after.jsonl").is_file()


def test_studio_h35_missing_counts_only_mapped_analysis_items():
    topic = {"id": "t1", "evidence_state": {"status": "missing", "unmapped_heading": "対応付けられていない候補（同じ節から拾った式・根拠）"},
             "evidence_items": [{"kind": "equation", "id": "e1"}, {"kind": "figure", "id": "f1", "teaching": True}]}
    sec = stx.evidence_sections(topic, topic["evidence_items"])
    assert sec == {"teacher": 1, "unmapped": 1, "analysis": 0}
    assert so.missing_has_mapped_items({"right_pane_sections": sec}) is False
    assert so.missing_has_mapped_items({"right_pane_sections": {"analysis": 2}}) is True
    assert so.missing_has_mapped_items({}) is True  # 古い digest は従来どおり


def test_studio_formula_placeholder_projects_dollar_latex_and_brain_show_keeps_it(tmp_path):
    latex = r"B_{\mathrm{DCF}} = Q\sqrt{4\pi\rho}\,\sigma_v/\sigma_\theta"
    text = render_material_text({"text": "その関係は次の式で表されます。[[FORMULA_0]]",
                                 "formulas": [{"id": "eq_1", "latex": latex}]})
    assert f"${latex}$" in text
    (tmp_path / "req-00001.json").write_text(json.dumps({"seq": 1, "kind": "persona", "system": "s",
                                                         "messages": [{"role": "user", "content": text}]},
                                                        ensure_ascii=False), encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        brain.cmd_show(argparse.Namespace(mailbox=str(tmp_path), seq="1", system_chars=100, msg_chars=1000, full=False))
    assert f"${latex}$" in out.getvalue()


# ---------------------------------------------------------------------------
# 運転: 講義の最後の枚の ▶ / brain pending --claim
# ---------------------------------------------------------------------------
def test_lecture_at_end_flag_ends_the_loop():
    s = PersonaSession(persona_id="st-01", course_id="C", topic_id="t1")
    s.scratch.update({"lecture_deck": [{"display_text": "1"}, {"display_text": "2"}], "lecture_pos": 0,
                      "lecture_topic": "t1"})

    class C:
        session = s
        args: dict = {}
        client = type("Client", (), {"traces": []})()

        @staticmethod
        def missing(name):
            raise AssertionError(name)
    stx._lecture_next(C)
    assert s.scratch["until_flags"]["lecture_at_end"] is False  # 最後の枚に着いただけ
    stx._lecture_next(C)
    assert s.scratch["until_flags"]["lecture_at_end"] is True   # 最後の枚でもう一度 ▶
    assert "lecture_at_end" in STATE_FLAGS and state_flags_in("persona_decides_to_stop or lecture_at_end") == ["lecture_at_end"]
    assert evaluate_until("persona_decides_to_stop or steps > 8 or lecture_at_end", {"lecture_at_end": True})
    for sid in ("s-studio-lecture-pages", "s-lecture-then-trace"):
        loops = [st for st in load_scenario(sid).steps if st.kind == "repeat"]
        nexts = [st for st in loops if any(o.action == "learning.lecture.next"
                                           for c in (st.children or [st]) for o in c.options)]
        assert nexts and all("lecture_at_end" in st.until for st in nexts), sid


def test_brain_pending_claim_lets_second_brain_skip(tmp_path):
    for seq in (1, 2):
        (tmp_path / f"req-{seq:05d}.json").write_text(json.dumps({"seq": seq, "kind": "persona"}), encoding="utf-8")
    assert brain.claim_next(tmp_path, owner="A") == [1]
    assert brain.claim_next(tmp_path, owner="B") == [2]   # 2 体目は予約済みを飛ばす
    assert brain.claim_next(tmp_path, owner="C") == []
    assert brain._pending(tmp_path) == [1, 2]              # 予約は未応答の列挙を変えない（--claim なし）
