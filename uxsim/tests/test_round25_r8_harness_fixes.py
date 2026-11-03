"""第 25 周 第 8 周の偽所見・運転の欠けの是正（runs/round25/judge_*_r8.md の「ハーネス」節と harness_fixes_*.md の第 8 周の項）と
第 9 周の前提スクリプト（``r5_prereqs.py --round 9``）・第 8 波の投影の runner 側の文字列（sync_keys_wave8.md 面 C）。

外部呼び出しなし（MockTransport・純関数・偽の ctx）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import httpx
import yaml

from uxsim.actions.registry import REGISTRY
from uxsim.runner import actions_exec as ax
from uxsim.runner import api as rapi
from uxsim.runner import atlas_exec as atx
from uxsim.runner import doubt_exec as dx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import ScenarioParams, load_params, load_scenario
from uxsim.runner.state import PersonaSession

UXSIM = Path(__file__).resolve().parents[1]
ROUND = UXSIM / "runs" / "round25"
PARAMS = UXSIM / "domains" / "astrophysics" / "scenario_params"


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


def _prereqs():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# atlas H45: 下書きを作る手（関係の区画のボタン）が両場面にあり、論文由来の候補のボタンより先
# ---------------------------------------------------------------------------
def test_atlas_h45_edge_draft_from_frozen_in_both_scenarios():
    edge = _actions("t-atlas-edge-review")
    assert "admin.atlas.edges.draft_from_frozen" in edge
    assert edge.index("admin.atlas.edges.draft_from_frozen") < edge.index("admin.atlas.edges.incorporate_preview")
    assert "admin.atlas.edges.incorporate_apply" in edge
    freeze = _actions("t-atlas-draft-freeze")
    assert freeze.index("admin.atlas.edges.draft_from_frozen") < freeze.index("admin.atlas.gaps.draft_from_frozen")
    # 下書きなし・候補 0 件では関係の区画のボタンは出る（runner が飛ばさない）、論文由来の候補のボタンは出ない
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    s.scratch["atlas_edges"] = {"draft_exists": False, "candidates": []}
    s.scratch["atlas_gaps"] = {"draft_exists": False, "candidates": []}
    assert atx.unavailable("admin.atlas.edges.draft_from_frozen", s) is None
    assert atx.unavailable("admin.atlas.gaps.draft_from_frozen", s)
    p = yaml.safe_load((PARAMS / "t-atlas-draft-freeze.yaml").read_text(encoding="utf-8"))
    assert p["personas"]["te-03-meticulous-seminar"]["freeze_version"] == "2026.6"
    e = yaml.safe_load((PARAMS / "t-atlas-edge-review.yaml").read_text(encoding="utf-8"))
    assert e["personas"]["te-04-copilot-intro"]["accept_kind"] == "依存"


# atlas H46: 送らなかった手は runner_notes に理由が残る（HTTP 行は precondition のまま）
def test_atlas_h46_not_sent_note():
    s = PersonaSession(persona_id="te-01", role="TEACHER")
    ctx = _Ctx(REGISTRY["admin.atlas.freeze_impact"], {}, s, _client(lambda r: httpx.Response(500)))
    ctx.missing("freeze_ready")
    assert ctx.client.traces[-1].error == "precondition:freeze_ready" and ctx.client.traces[-1].status is None
    note = s.scratch["runner_notes"][-1]
    assert note.startswith("（ハーネス: 送らなかった") and "freeze_ready" in note and "{cartridge_id}" in note


# atlas H47: 領域どうしと概念どうしの関係が混ざる一覧には注記
def test_atlas_h47_relations_mix_note():
    sk = {"regions": [{"id": "r1", "label": "R1", "concepts": [{"id": "c1", "label": "C1"}, {"id": "c2", "label": "C2"}]},
                      {"id": "r2", "label": "R2", "concepts": []}],
          "edges": [{"from": "r1", "to": "r2", "kind": "adjacent"}, {"from": "c1", "to": "c2", "kind": "related"}]}
    lines = atx.draft_relations_lines(sk, None)
    assert atx.RELATIONS_MIX_NOTE in lines and len([x for x in lines if x.startswith("- ")]) == 2
    only_regions = {**sk, "edges": [sk["edges"][0]]}
    assert atx.RELATIONS_MIX_NOTE not in atx.draft_relations_lines(only_regions, None)


# atlas 第 8 波（sync_keys_wave8 面 C）: 見送った判断は「見送りの理由」・下書きなしの未採用カードの案内
def test_atlas_wave8_dismissed_note_head_and_no_draft_text():
    assert atx.decision_note_head({"status": "dismissed"}) == "見送りの理由"
    assert atx.decision_note_head({"status": "accepted"}) == "判断のメモ"
    src = (UXSIM / "runner" / "atlas_exec.py").read_text(encoding="utf-8")
    assert "判断のメモ: {decision" not in src and src.count("decision_note_head(decision)") == 4
    admin = (UXSIM.parent / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
    assert atx.EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT in admin and atx.EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT in atx.EDGE_EXPLAIN_LINES


# ---------------------------------------------------------------------------
# doubt H48: ⚓ の無いトピックで部品の文脈を開く手 → inrun_unmet（meta の blocked_precondition）
# ---------------------------------------------------------------------------
def test_doubt_h48_no_anchor_goes_to_inrun_unmet():
    s = PersonaSession(persona_id="st-05", role="STUDENT", course_id="c1", topic_id="t13")
    s.material = {"chunks": [{"content": "本文だけ", "evidence_items": []}]}
    ax._component_context(_Ctx(REGISTRY["learning.component.context"], {}, s, _client(lambda r: httpx.Response(500))))
    unmet = s.scratch["inrun_unmet"]
    assert unmet and unmet[0]["check"] == "component_anchor" and "t13" in unmet[0]["reason"]
    sc = load_scenario("s-db-ledger-lines")
    assert any("⚓" in str(p) for p in sc.raw.get("preconditions") or [])
    params = load_params("astrophysics", "s-db-ledger-lines", "st-05-night-solarwind")
    # 第 8 波は t11（開かない ⚓）、第 11 周から t1（後継の決まる ⚓ — judge_doubt_r10 §4）。⚓ のあるトピックが固定されていればよい
    assert params["topic_id"] in ("t11", "t1")
    assert load_params("astrophysics", "s-db-ledger-lines", "st-02-m2-lensing-theory")["topic_id"] == ""


# doubt H50 / H51: 場面の材料の文は目印付き・送ると観察に注記
def test_doubt_h50_h51_material_marked_and_noted():
    for sid, keys in (("t-db-challenge-from-node", ("challenge_reason", "second_reason")),
                      ("t-db-assumption-review", ("premise_scope_quote",))):
        data = yaml.safe_load((PARAMS / f"{sid}.yaml").read_text(encoding="utf-8"))
        layers = [data.get("params") or {}] + list((data.get("personas") or {}).values()) \
            + list((data.get("per_archetype") or {}).values())
        for layer in layers:
            for k in keys:
                if k in (layer or {}):
                    # 第 10 波（doubt H60）: 文の頭の目印（周番号）は外した — 目印は材料の値との一致で付く
                    assert not re.match(r"（場面の材料・第 \d+ 周）", str(layer[k])), (sid, k)
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    dx.note_scenario_material(s, {"reason": "（場面の材料・第 9 周）理由", "evidence_quote": "原文"})
    assert s.scratch["observation_notes"] and "「理由」の欄" in s.scratch["observation_notes"][0]
    s2 = PersonaSession(persona_id="te-03", role="TEACHER")
    dx.note_scenario_material(s2, {"reason": "本人の言葉"})
    assert "observation_notes" not in s2.scratch


# ---------------------------------------------------------------------------
# share H50: voice: false の材料でこの経路だけ文字で送る
# ---------------------------------------------------------------------------
def test_share_h50_scenario_voice_off():
    s = PersonaSession(persona_id="st-07", role="STUDENT", uses_voice=True)
    saved = rapi.apply_scenario_voice(s, ScenarioParams({"voice": False}))
    assert saved is True and s.uses_voice is False and rapi.VOICE_OFF_NOTE in s.scratch["runner_notes"]
    s2 = PersonaSession(persona_id="st-07", role="STUDENT", uses_voice=True)
    assert rapi.apply_scenario_voice(s2, ScenarioParams({})) is None and s2.uses_voice is True
    assert load_params("astrophysics", "s-share-part-chip", "st-07-m1-grb-voice")["voice"] is False


# share H51: 移った先の部品の名前も控える（app.js と同じ instance.component.label || frozen_label）
def test_share_h51_hop_remembers_label():
    assert ax.opened_component_label({"instance": {"component": {"label": "TOV map"}}}) == "TOV map"
    assert ax.opened_component_label({"frozen_label": "旧名"}) == "旧名"
    src = (UXSIM / "runner" / "actions_exec.py").read_text(encoding="utf-8")
    hop = src.split("def _component_context_hop", 1)[1].split("\ndef ", 1)[0]
    assert "remember_open_context(s, target, label)" in hop


# share H52: (c) に記号の候補・記号の確定・地図の手付け、(b2) に 2 人目の査読
def test_share_h52_scenario_steps():
    c = _actions("t-share-labels-atlas")
    for a in ("admin.library.symbol_derive", "admin.library.bound_link_decide", "admin.library.atlas_link_manual"):
        assert a in c, a
    assert "admin.explanations.update" in _actions("t-share-explain-endorse-cite")
    text = (UXSIM / "scenarios" / "goal" / "teacher" / "t-share-explain-endorse-cite.yaml").read_text(encoding="utf-8")
    assert "what: second_review" in text


# ---------------------------------------------------------------------------
# studio H38 / H39
# ---------------------------------------------------------------------------
def test_studio_h38_next_topic_has_lecture_next():
    sc = load_scenario("s-studio-lecture-pages")
    outer = [st for st in sc.steps if st.kind == "repeat" and st.children][-1]
    inner = [st for st in outer.children if st.kind == "repeat"]
    assert inner and any(o.action == "learning.lecture.next" for st in inner[0].children for o in st.options)
    assert "lecture_at_end" in inner[0].until


def test_studio_h39_versions_reattach_audio_steps():
    split = _actions("t-studio-split-save")
    assert split.index("admin.lecture_studio.open_versions") < split.index("admin.lecture_studio.publish_release")
    # 第 8 波の投影の同期（IK-1071）: 音声の準備を読んだ後に［依頼の文をコピー］の任意の段が続く
    rs = _actions("t-studio-rewrite-shared")
    assert rs[-2:] == ["admin.lecture_studio.audio_readiness", "admin.lecture_studio.audio_request_copy"]
    ra = _actions("t-studio-reattach-all")
    assert "admin.lecture_studio.reattach_appendix_all" in ra and "admin.lecture_studio.publish_release" in ra
    assert load_params("astrophysics", "t-studio-reattach-all", "te-01-rookie-seminar")["studio_course_id"] == "6be229e1"


# ---------------------------------------------------------------------------
# 第 9 周の前提（r5_prereqs.py --round 9）
# ---------------------------------------------------------------------------
def _doc_items(name: str) -> list[str]:
    text = (ROUND / name).read_text(encoding="utf-8")
    return re.findall(r"^\| `([a-z0-9_]+)` \|", text.split("## 周の前の一括実行", 1)[1], re.MULTILINE)


def test_round9_items_match_doc_and_defaults():
    m = _prereqs()
    assert list(m.ROUND_ITEMS[9]) == _doc_items("r9_prereqs.md")
    assert set(m.ROUND_ITEMS[9]) <= set(m.ALL_ITEMS)
    a = m.parse_args(["--round", "9"])
    assert a.freeze_teacher == m.TE03 and a.expect_frozen == "2026.5" and a.ledger_student == "st-05"
    assert a.expect_release == "3" and a.allow_auto_confirm is False
    for name in ("share_fixed_course_sources", "share_tex_course_anchor_reason", "share_viewer_teacher",
                 "doubt_ik1057_read", "doubt_ledger_anchor_topic", "doubt_same_course_compare",
                 "studio_release_copy_no_split", "studio_head_equals_release"):
        item = m.ALL_ITEMS[name]
        assert item.do is None or name == "doubt_ik1057_read", name  # 確認だけ（ik1057 は記録の書き込みだけ）
    wrapper = (ROUND / "r9_prereqs.py").read_text(encoding="utf-8")
    assert '"--round", "9"' in wrapper


def test_round9_pure_functions():
    m = _prereqs()
    # 学習者の API の master_course の包みを剥がす（第 8 周の「⚓ 0 個」の読み違い）
    assert [t["id"] for t in m.course_topics({"master_course": {"topics": [{"id": "t1"}]}})] == ["t1"]
    assert m.course_source_document_ids({"sources": [{"document_id": "a"}, {"document_id": "a"}, {"x": 1}]}) == ["a"]
    assert m.reach_summary(["d1"], {"x": {"d1"}, "y": {"d2"}, "z": set()}) == {"x": True, "y": False, "z": False}
    topics = [{"id": "t11", "title": "二重ネットワーク"}, {"id": "t13", "title": "主結果"}]
    assert m.ledger_topic_choice({"topic_id": "t13", "topic_hint_terms": ["二重"]}, topics)["id"] == "t13"
    assert m.ledger_topic_choice({"topic_id": "", "topic_hint_terms": ["二重"]}, topics)["id"] == "t11"
    assert m.editable_paths("viewer", [("c1", "viewer")]) == []
    assert m.editable_paths("viewer", [("c1234567", "editor")]) == ["コース c1234567 の editor"]
    sm = m.summarize_ik1057([{"target_id": "a", "variant": "default", "level": "several"},
                             {"target_id": "a", "variant": "no_stage_roots", "level": "single"},
                             {"target_id": "b", "variant": "default", "level": "none"}])
    assert sm["targets"] == 2 and sm["changed"] == 1
    assert m.topic_split_fields({"student_material": {"source_text": "a\n===\nb"}, "spoken_script": "x"}) == ["教材欄"]
    facts = m.anchor_reason_facts([{"topic_id": "t0", "anchors": 0, "opened": 0, "component_evidence": 3},
                                   {"topic_id": "t11", "anchors": 2, "opened": 0, "component_evidence": 5}])
    assert any("t0" in f and "埋め込み" in f for f in facts) and any("t11（0/2" in f for f in facts)


def test_round9_share_pair_does_not_auto_confirm():
    m = _prereqs()

    class _Never:
        def call(self, *a, **k):  # pragma: no cover — 呼ばれたら失敗
            raise AssertionError("書き込みを送った")

    r = m.Result(False, data={"who": m.TE03, "entry_id": "e1", "links": [{"id": "l1", "status": "candidate"}]})
    a = argparse.Namespace(round=9, allow_auto_confirm=False)
    assert m.do_share_pair(_Never(), a, r) == [m.PAIR_NO_AUTO_CONFIRM_NOTE]
    for bad in ("judge_", "IK-", " H4", "前提スクリプト"):
        assert bad not in m.SYMBOL_REVOKE_REASON_DEFAULT
