"""第 25 周 面 C 第 1 周の偽所見 H1〜H5・H9 の是正（投影を画面と同じ形にする・審判の検査 4 種）。"""
from __future__ import annotations

from uxsim.oracles import atlas as oracle
from uxsim.runner import atlas_exec as ax
from uxsim.runner.state import OBSERVATION_CHARS, PersonaSession
from uxsim.schema import HttpTrace, TranscriptStep

from uxsim.tests.test_round25_atlas_harness import _factory, _s, _t  # noqa: E402  同じ組み立て


def _edge(i: int, status: str = "candidate") -> dict:
    return {"edge_key": f"edge|astrophysics|c_{i:02d}|c_{i + 1:02d}", "from_id": f"c_{i:02d}",
            "to_id": f"c_{i + 1:02d}", "from_label": f"概念{i}", "to_label": f"概念{i + 1}",
            "origins": ["vector"], "documents": [{"title": f"論文{i}"}], "decision": {"status": status}}


# H1 ---------------------------------------------------------------------------
def test_h1_edges_fold_into_titles_within_budget_and_repeat_explanations_once():
    s = PersonaSession(persona_id="te-03")
    s.scratch["atlas_edges"] = {"candidates": [_edge(i) for i in range(40)], "draft_exists": False}
    full = ax.edges_lines(s)
    budget = 2500
    lines = ax.edges_lines(s, budget=budget)
    text = "\n".join(lines)
    assert ax._size(lines) <= budget + 400 < ax._size(full)
    assert "（以下の関係の候補は画面の下に続く: " in text  # 切ったことを投影の側で言う（件数は書かない）
    assert ax.SAME_EXPLAIN_NOTE.strip() in text
    assert text.count(ax.EDGE_ACCEPT_NOTE) == 1


def test_h1_tab_keeps_vector_and_alias_sections_under_observation_limit():
    s = PersonaSession(persona_id="te-03")
    s.scratch["atlas_edges"] = {"candidates": [_edge(i) for i in range(60)], "draft_exists": False}

    class Ctx:
        session = s

    marks = {"v": ["ベクトル索引の区画"], "a": ["登録済みの別名の区画"]}

    def vector_lines(sess):
        return marks["v"]

    def aliases_lines(sess):
        return marks["a"]

    ax._tab_fact(Ctx, vector_lines, aliases_lines, ax.edges_lines, status=200)
    out = s.last_body["_ui_fact"]
    assert len(out) <= OBSERVATION_CHARS
    assert "ベクトル索引の区画" in out and "登録済みの別名の区画" in out and "画面の下に続く" in out


# H2 ---------------------------------------------------------------------------
def test_h2_card_numbers_are_fixed_per_key_and_not_reused():
    s = PersonaSession(persona_id="te-03")
    rows = [_edge(i) for i in range(4)]
    first = dict(ax.card_numbers(s, "edge", rows, "edge_key"))
    rest = rows[:2] + rows[3:]  # 3 番目を見送って一覧から消えた
    again = ax.card_numbers(s, "edge", rest + [_edge(9)], "edge_key")
    assert again[rows[3]["edge_key"]] == first[rows[3]["edge_key"]] == 4
    assert again[_edge(9)["edge_key"]] == 5  # 消えた番号（3）を再利用しない
    picked = ax._hint_pick(rest, "4", ("from_label",), numbers=again, key_field="edge_key")
    assert picked is rows[3]
    assert ax._hint_pick(rest, "3", ("from_label",), numbers=again, key_field="edge_key") is None


# H3 ---------------------------------------------------------------------------
def test_h3_sea_labels_from_skeleton_nodes_and_skips_anchors_without_point():
    skeleton = {"domain_name": "宇宙物理",
                "levels": {"1": {"regions": [{"id": "stellar", "label": "恒星", "kind": "region"}],
                                 "nodes": [{"id": "binary_stars", "region": "stellar"}]}},
                "nodes": {"binary_stars": {"label": "連星"}, "cmb": {"label": "宇宙背景放射"}}}
    landscape = {"placements": [{"anchor_node_id": "binary_stars", "document_id": "d1"},
                                {"anchor_node_id": "cmb", "document_id": "d2"}]}
    text = "\n".join(ax.sea_map_lines(landscape, skeleton, None))
    assert "○ 連星" in text and "binary_stars" not in text
    assert "宇宙背景放射" not in text and "cmb" not in text  # L1 に位置の無い概念の円は画面に無い


# H4 ---------------------------------------------------------------------------
def test_h4_domains_and_papers_are_projected_like_corpus_sea():
    dom = "\n".join(ax.domains_lines({"domains": [{"domain_key": "astrophysics", "domain_name": "宇宙物理",
                                                    "has_visible_papers": True}]}))
    assert "[宇宙物理]" in dom and "domain_name" not in dom and "has_visible_papers" not in dom
    papers = "\n".join(ax.papers_lines({"documents": [{"document_id": "d1", "title": "連星の論文", "placed": False,
                                                       "can_discuss": True}]}))
    assert "連星の論文" in papers and "placed" not in papers and "can_discuss" not in papers


# H5 ---------------------------------------------------------------------------
def test_h5_zero_impact_projects_the_pre_publish_checklist():
    draft = {"validation": {"warnings": ["w"]}}
    lines = ax.freeze_check_lines("2026.2", draft, [{"status": "pending"}], {"facts": ["削除される概念はありません"]})
    assert lines[0] == "公開版: 2026.2" and "検証エラー: 0件" in lines and lines[-1] == ax.FREEZE_QUESTION
    assert "削除される概念はありません" in lines
    assert not ax._impact_modal_shown({"facts": ["x"]})
    assert ax._impact_modal_shown({"removed_node_ids": ["a"]})


# H9 ---------------------------------------------------------------------------
def _gap_dg(**cand) -> dict:
    base = {"cluster_key": "gap|a|r|x", "status": "candidate", "label": "星間磁場", "document_titles": [],
            "decided": False, "decided_by_in_api": False, "decider": ""}
    return {"kind": "gap_candidates", "candidates": [{**base, **cand}]}


def test_h9_duplicate_papers_by_arxiv_id_or_title():
    dg = _gap_dg(document_titles=["2407.01221v2.pdf", "2407.01221v2.tar.gz"])
    found = oracle.check([_s(1, "admin.atlas.gaps.list", _t("/x/gap-candidates", {}, atlas=dg))], _factory())
    assert any("同じ論文" in f.hypothesis for f in found)
    dg2 = _gap_dg(document_titles=["Magnetic fields in ISM", "magnetic  fields in ISM"])
    assert any("同じ論文" in f.hypothesis for f in oracle.check(
        [_s(1, "admin.atlas.gaps.list", _t("/x/gap-candidates", {}, atlas=dg2))], _factory()))
    ok = _gap_dg(document_titles=["論文A", "論文B"])
    assert not any("同じ論文" in f.hypothesis for f in oracle.check(
        [_s(1, "admin.atlas.gaps.list", _t("/x/gap-candidates", {}, atlas=ok))], _factory()))


def _obs_step(seq, aid, observation, *traces):
    st = _s(seq, aid, *traces)
    st.observation = observation
    return st


def test_h9_internal_ids_in_teacher_projection():
    skel = {"kind": "skeleton_state", "node_ids": ["binary_stars", "cmb"]}
    steps = [_obs_step(1, "admin.atlas.open", "関係の候補\n1. 連星 — 重力波", _t("/x/skeleton", {}, atlas=skel)),
             _obs_step(2, "admin.atlas.edges.list", "1. binary_stars — 重力波  AIによる推定（未確認）"),
             _obs_step(3, "admin.atlas.gaps.incorporate_preview", "概念として追加します（id: ism_star_formation_c5）。")]
    found = [f for f in oracle.check(steps, _factory()) if "内部 ID" in f.hypothesis]
    assert len(found) == 2
    clean = [_obs_step(1, "admin.atlas.open", "関係の候補\n1. 連星 — 重力波 cmb", _t("/x/skeleton", {}, atlas=skel))]
    assert not any("内部 ID" in f.hypothesis for f in oracle.check(clean, _factory()))


def test_h9_decided_card_without_attribution():
    dg = _gap_dg(status="accepted", decided=True, decided_by_in_api=True)
    obs = "1. 星間磁場  概念（親: 星間物質）  AIによる検出（未確認）  [採用済み]\n  ボタン: [採用]（押せない）"
    step = _obs_step(1, "admin.atlas.gaps.list", obs, _t("/x/gap-candidates", {}, atlas=dg))
    assert any("誰が判断した" in f.hypothesis for f in oracle.check([step], _factory()))
    step2 = _obs_step(1, "admin.atlas.gaps.list", obs + "\n  判断: 堀 先生", _t("/x/gap-candidates", {}, atlas=dg))
    assert not any("誰が判断した" in f.hypothesis for f in oracle.check([step2], _factory()))


def test_h9_sea_all_fog_with_placements():
    dg = {"kind": "sea_map", "has_placements": True, "regions_present": True, "all_regions_fog": True}
    st = _s(1, "learning.corpus.landscape", _t("/api/learning/corpus/landscape", {}, atlas=dg), persona="st-01")
    assert any("霧" in f.hypothesis for f in oracle.check([st], _factory()))
    dg["has_placements"] = False
    assert not any("霧" in f.hypothesis for f in oracle.check([st], _factory()))


def test_h9_new_findings_carry_no_numbers():
    dg = _gap_dg(document_titles=["2407.01221v2.pdf", "2407.01221v2.tar.gz"], status="dismissed", decided=True,
                 decided_by_in_api=True)
    step = _obs_step(1, "admin.atlas.gaps.list", "1. 星間磁場  AIによる検出（未確認）",
                     _t("/x/gap-candidates", {}, atlas=dg))
    for f in oracle.check([step], _factory()):
        assert not any(ch.isdigit() for ch in f.hypothesis)
