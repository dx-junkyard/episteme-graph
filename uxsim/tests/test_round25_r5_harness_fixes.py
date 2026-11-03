"""第 25 周 第 5 周の偽所見・観察の欠けの是正（runs/round25/harness_fixes_{share,doubt,atlas,studio}.md の第 5 周の節）。

外部呼び出しなし（純関数・ファイルの静的検査）。各テストの見出しは面と H の番号。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from uxsim.runner import atlas_exec as ax
from uxsim.runner import doubt_exec as dx
from uxsim.runner import share_exec as sx
from uxsim.runner import studio_exec as stx
from uxsim.runner.actions_exec import unavailable_reason
from uxsim.runner.scenario import load_params, load_scenario
from uxsim.runner.state import PersonaSession, render_material_text, reconstructed_mark_text
from uxsim.oracles import studio as so

UXSIM = Path(__file__).resolve().parents[1]
ROUND = UXSIM / "runs" / "round25"


def _actions(scenario_id: str) -> list[str]:
    sc = load_scenario(scenario_id)
    out = []

    def walk(steps):
        for st in steps:
            out.extend(o.action for o in st.options)
            walk(getattr(st, "children", []) or [])
    walk(sc.steps)
    return out


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------
def test_share_h35_pick_reads_projected_labels():
    rows = [{"document_title": "A"}, {"document_title": "B"}]
    for hint in ("リンク1", "リンク 2", "2", "#1", "番号 2", "⚓2", "候補1", "2本目"):
        assert sx.pick(rows, hint, ("document_title",)) is not None, hint
    assert sx.pick(rows, "リンク2", ("document_title",))["document_title"] == "B"
    assert sx.pick(rows, "リンク3", ("document_title",)) is None


def test_share_h35_link_miss_lists_choices():
    cand = {"entry": {"name": "Boundary constraints"}}
    links = [{"document_title": "PINN", "local_expression": {"name": "Radius estimate"}}]
    note = sx.link_choices_note(cand, links, "リンクX")
    assert "リンク1: PINN「Radius estimate」" in note and note.startswith("（ハーネス:")


def test_share_h36_same_title_courses_and_param():
    rows = [{"id": "6be229e1-x", "title": "T", "role": "editor", "role_label": "共同編集"},
            {"id": "1bad7d9f-y", "title": "T", "role": "editor", "atlas_domain_label": "宇宙物理"},
            {"id": "aaaaaaaa-z", "title": "U", "role": "owner"}]
    lines = sx.same_title_course_lines(rows, "6be229e1-x")
    assert len(lines) == 1 and "course_id=1bad7d9f" in lines[0]
    text = (UXSIM / "scenarios/goal/teacher/t-share-explain-endorse-cite.yaml").read_text("utf-8")
    assert text.count("course_id: '{{studio_course_id}}'") == 2
    assert load_params("astrophysics", "t-share-explain-endorse-cite").get("studio_course_id") == "1bad7d9f"


def test_share_h37_reconstructed_mark_like_screen():
    f = {"latex": "P=K\\rho", "reconstructed": True, "reconstructed_mark": "AI復元", "reconstructed_note": "PDF から復元した式"}
    assert reconstructed_mark_text(f) == " 〔AI復元（ホバーで: PDF から復元した式）〕"
    text = render_material_text({"text": "[[FORMULA_0]]", "formulas": [f]})
    assert "$P=K\\rho$ 〔AI復元（ホバーで: PDF から復元した式）〕" in text
    pop = sx.symbol_popover_view(200, {"available": True, "symbol": "P", "definition": {"text": "圧力", **f}}, "P")
    assert "（ホバーで: PDF から復元した式）" in pop
    assert reconstructed_mark_text({"latex": "x"}) == ""


# ---------------------------------------------------------------------------
# doubt
# ---------------------------------------------------------------------------
def test_doubt_h34_raw_probe_body_is_valid():
    body = dx.raw_probe_scope_body({})
    assert body["condition"] and body["evidence_quote"] and body["reason"] and body["evidence_ids"] == []
    body2 = dx.raw_probe_scope_body({"domain": "宇宙", "reason": "r"})
    assert body2["domain"] == "宇宙" and body2["condition"] == "" and body2["reason"] == "r"


def test_doubt_h36_select_node_before_map_question():
    for sid in ("s-db-map-ask-en", "s-db-ledger-lines"):
        acts = _actions(sid)
        assert "learning.atlas.select_node" in acts, sid
        i = acts.index("learning.atlas.select_node")
        assert acts.index("learning.atlas.view") < i < len(acts) - 1 and "learning.chat.ask" in acts[i + 1:]
        assert load_params("astrophysics", sid).get("map_node_hint")


def test_doubt_h38_no_line_notes_split():
    assert "教材の外" in dx.LEARNER_NO_LINE_OUTSIDE
    assert "何も出さない" not in dx.LEARNER_NO_LINE


# ---------------------------------------------------------------------------
# atlas
# ---------------------------------------------------------------------------
def _atlas():
    return {"nodes": {"n1": {"label": "中性子星"}, "n2": {"label": "暗黒エネルギー"}, "n3": {"label": "重力波"}},
            "levels": {"2": {"nodes": [{"id": "n1"}, {"id": "n2"}], "edges": [["n1", "n2"], ["n1", "n3"]],
                             "omitted_note": "この画面では、分野の地図の項目の一部だけを描いています。"}},
            "threads": {"available": True, "skeleton_version": "2026.2", "undrawn_note": "糸の一部は線にしていません。",
                        "items": [{"from": "n1", "to": "n2", "from_label": "中性子星", "to_label": "暗黒エネルギー"},
                                  {"from": "n1", "to": "n3", "from_label": "中性子星", "to_label": "重力波"}]}}


def test_atlas_h32_l2_projection_reads_dto_range():
    atlas = _atlas()
    lines = ax.l2_lines(atlas)
    assert "関係（実線）: 中性子星 — 暗黒エネルギー" in lines
    assert not any("重力波" in l for l in lines if l.startswith("関係"))
    assert atlas["levels"]["2"]["omitted_note"] in lines
    _n, drawn, names = ax.l2_drawn(atlas)
    t = ax.threads_lines(atlas, drawn=drawn, names=names)
    i = t.index("糸の一部は線にしていません。")
    assert any("暗黒エネルギー" in l for l in t[:i]) and any("重力波" in l for l in t[i:])
    # JS の描画上限をミラーしない（サーバの DTO が描く範囲そのもの）
    assert "l2Nodes" not in (UXSIM / "runner" / "atlas_exec.py").read_text("utf-8")


def test_atlas_h33_vector_building_does_not_urge_rebuild():
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    s.scratch["atlas_vectors"] = {"stale": True, "building": True, "skeleton_version": "2026.2"}
    assert ax.VECTOR_STALE_TEXT not in ax.vector_lines(s)
    s.scratch["atlas_vectors"]["building"] = False
    assert ax.VECTOR_STALE_TEXT in ax.vector_lines(s)
    assert ax.vector_refresh_fact({"status": "skipped", "skipped_reason": "building_in_progress",
                                   "fact": "いま新しい版の索引を作っています。"}) == "いま新しい版の索引を作っています。"
    src = (UXSIM / "runner" / "atlas_exec.py").read_text("utf-8")
    freeze = src[src.index("def _freeze(ctx)"):src.index("def _freeze_blocked_goto")]
    assert "_tab_fact(ctx, overview_lines, vector_lines, aliases_lines, gaps_lines, edges_lines" in freeze


def test_atlas_edge_result_line_and_index_note():
    s = PersonaSession(persona_id="te-04", role="TEACHER")
    s.scratch["atlas_edges"] = {"candidates": [], "index_note": "新しい版の索引を作っているため…"}
    s.scratch["atlas_edge_result_line"] = "「A」と「B」の関係の候補を見送りました（理由を記録しました）。"
    lines = ax.edges_lines(s)
    assert "新しい版の索引を作っているため…" in lines and any(l.startswith("「A」と「B」") for l in lines)
    assert "atlas_edge_result_line" not in s.scratch  # 1 回だけ写す


def test_atlas_h34_draft_from_frozen_dropped_when_draft_exists():
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    s.scratch["atlas_state"] = {"draft": {"revision": 1}}
    assert unavailable_reason("admin.atlas.gaps.draft_from_frozen", s)
    s2 = PersonaSession(persona_id="te-05", role="TEACHER")
    s2.scratch["atlas_edges"] = {"draft_exists": True, "candidates": []}
    assert unavailable_reason("admin.atlas.gaps.draft_from_frozen", s2)


def test_atlas_r6_freeze_version_param():
    """te-01 の freeze_version は「現行凍結版の次の版」（周ごとに campaign が上げる — 第 8 周で 2026.5）。値を固定せず、
    最新の atlas campaign の前提（atlas_frozen_and_pending の現行版・atlas_freeze_version の版）と整合するかを見る。"""
    import re as _re
    from pathlib import Path as _Path

    value = str(load_params("astrophysics", "t-atlas-draft-freeze", "te-01-rookie-seminar").get("freeze_version"))
    assert _re.fullmatch(r"20\d\d\.\d+", value), value
    camps = sorted(_Path(__file__).resolve().parents[1].glob("campaigns/astrophysics/c-r25-atlas-r*.yaml"),
                   key=lambda p: int(_re.search(r"-r(\d+)\.yaml$", p.name).group(1)))
    text = camps[-1].read_text(encoding="utf-8")
    current = _re.search(r"現行の凍結版\s*(20\d\d\.\d+)", text)
    declared = _re.search(r"freeze_version\s*が\s*(20\d\d\.\d+)", text)
    assert current and declared, camps[-1].name
    major, minor = current.group(1).split(".")
    assert declared.group(1) == f"{major}.{int(minor) + 1}"  # r5_prereqs.next_version と同じ規則
    # 第 9 波: 次の周の前提（r{N+1}_prereqs）で param を先に上げ、campaign を後で作るので、最新の campaign の値か、その次の版
    nxt = f"{major}.{int(declared.group(1).split('.')[1]) + 1}"
    assert value in (declared.group(1), nxt)


# ---------------------------------------------------------------------------
# studio
# ---------------------------------------------------------------------------
def test_studio_h27_delivered_count():
    split = stx._split_summary(200, {"display_segment_count": 3, "spoken_segment_count": 1, "mismatch": True,
                                     "slides": [{"display_text": "x"}]})
    assert split["display_count"] == 3 and split["delivered_count"] == 1
    assert so.delivered_count({"display_count": 3, "mismatch": True}) == 1  # 旧い digest
    assert so.delivered_count({"display_count": 3, "delivered_count": 3}) == 3


def test_studio_h28_evidence_chips_not_truncated():
    items = [{"kind": "claim", "title": f"主張{i}"} for i in range(27)] + \
        [{"kind": "figure", "title": "教員の図", "extra": {"teaching": True}}]
    assert stx.teaching_evidence_titles(items) == ["教員の図"]
    lines = stx._evidence_lines({"evidence_items": items})
    assert any("教員の図" in l for l in lines[1:-1]) and "…ほか" not in "\n".join(lines)


def test_studio_h29_anchor_is_conditional():
    sc = load_scenario("s-lecture-then-trace")
    step = next(st for st in sc.steps if any(o.action == "learning.component.context" for o in st.options))
    assert step.kind == "choose" and step.optional


def test_studio_h30_x1_material_beyond_merge():
    assert "learning" not in "".join(_actions("t-studio-split-save"))
    assert load_params("astrophysics", "t-studio-split-save").get("x1_topic_hint")
    assert _actions("t-studio-split-save").count("admin.lecture_studio.save_topic") == 2


# ---------------------------------------------------------------------------
# 運転: brain pending --after
# ---------------------------------------------------------------------------
def test_brain_pending_after(tmp_path):
    from uxsim.sandbox import brain

    for seq in (3, 250, 251):
        (tmp_path / f"req-{seq:05d}.json").write_text('{"kind": "persona"}', encoding="utf-8")
    (tmp_path / "res-00250.json").write_text("{}", encoding="utf-8")
    assert brain._pending(tmp_path) == [3, 251]
    assert brain._pending(tmp_path, after=247) == [251]


# ---------------------------------------------------------------------------
# 周の前の前提（r5_prereqs.py の一般化・--round 6）
# ---------------------------------------------------------------------------
def _load_prereqs():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod
    spec.loader.exec_module(mod)
    return mod


def _doc_items(name: str) -> list[str]:
    text = (ROUND / name).read_text(encoding="utf-8")
    return re.findall(r"^\| `([a-z0-9_]+)` \|", text.split("## 周の前の一括実行", 1)[1], re.MULTILINE)


def test_prereqs_round6_items_match_doc_and_defaults():
    mod = _load_prereqs()
    assert list(mod.ROUND_ITEMS[6]) == _doc_items("r6_prereqs.md")
    assert set(mod.ROUND_ITEMS[6]) <= set(mod.ALL_ITEMS)
    a = mod.parse_args(["--round", "6", "--dry-run"])
    assert a.probe_teacher == "te-05-delegator-lab" and a.expect_frozen == "2026.2" and a.residual
    a5 = mod.parse_args(["--dry-run", "--probe-teacher", "te-01-rookie-seminar"])
    assert a5.round == 5 and a5.probe_teacher == "te-01-rookie-seminar" and a5.assumption_teacher == mod.TE03
    assert mod.state_file(5) == mod.STATE_FILE and mod.state_file(6).name == "r6_prereqs_state.json"


def test_prereqs_pure_helpers_round6():
    mod = _load_prereqs()
    assert mod.next_version("2026.2") == "2026.3"
    assert "===" not in mod.strip_split_markers("前\n===\n後\n")
    rows = [{"id": "6be229e1-x", "role": "editor", "title": "A"}, {"id": "74ca9b48-y", "role": "owner", "title": "B"}]
    assert mod.assumption_course_pick(rows, "", "")[0]["id"].startswith("74ca9b48")  # 先頭（rows[0]）ではない
    assert mod.assumption_course_pick(rows, "", "6be229e1")[0]["id"].startswith("6be229e1")
    body = {"component_id": "uuid-1", "instance": {"component": {"label": "Radius estimate", "document": {"id": "doc-tex"}}}}
    assert mod.context_target(body) == ("uuid-1", "doc-tex", "Radius estimate")
    topics = [{"id": "t12", "title": "x"}, {"id": "t13", "title": "主結果：R1.4と音速の軟化"}]
    assert mod.pick_topic(topics, "主結果", "t13")["id"] == "t13"


def test_r6_prereqs_shim_is_not_a_copy():
    src = (ROUND / "r6_prereqs.py").read_text(encoding="utf-8")
    assert "r5_prereqs.py" in src and "--round" in src and len(src.splitlines()) < 60
    for bad in ("psycopg2", "sqlalchemy", "subprocess", "docker "):
        assert bad not in src and bad not in (ROUND / "r5_prereqs.py").read_text(encoding="utf-8")


def test_prereqs_round6_profile_names_item():
    mod = _load_prereqs()
    assert mod.ROUND_ITEMS[6][0] == "persona_profile_names" and "persona_profile_names" not in mod.ITEM_NAMES
    names = mod.persona_display_names()
    assert len([k for k in names if k.startswith("te-")]) == 5 and len([k for k in names if k.startswith("st-")]) == 10
    assert all(v and not v.startswith("uxsim_") for v in names.values())
    src = (ROUND / "r5_prereqs.py").read_text(encoding="utf-8")
    assert '"PATCH", "/api/me/profile"' in src
