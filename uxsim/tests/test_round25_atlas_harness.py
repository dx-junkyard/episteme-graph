"""第 25 周 面 C（分野の地図の運用）の行為・投影・審判（MockTransport・外部呼び出しなし）。

行為 = ``uxsim/actions/atlas_actions.py`` / 実行と投影 = ``uxsim/runner/atlas_exec.py`` / 審判 = ``uxsim/oracles/atlas.py``。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from uxsim.actions.atlas_actions import ACTIONS
from uxsim.actions.registry import REGISTRY
from uxsim.config import REPO_ROOT
from uxsim.oracles import atlas as oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import atlas_exec as ax
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

JS = REPO_ROOT / "frontend" / "public" / "js"
CART = "/api/admin/cartridges/astrophysics/atlas"

FROZEN = {"version": "2026.1", "generated_by": "seed", "reviewed_by": ["堀"], "changelog": [{"version": "2026.1", "note": "初版"}],
          "regions": [{"id": "r_cmb", "label": "宇宙背景放射", "concepts": [{"id": "c_cmb_t", "label": "温度揺らぎ"}]}],
          "edges": []}
GAP = {"cluster_key": "gap|astrophysics|r_cmb|偏光", "layer": "concept", "layer_label": "概念", "parent_region_id": "r_cmb",
       "parent_region_label": "宇宙背景放射", "parent_region_known": True, "parent_region_at_capacity": False,
       "proposed_label": "偏光", "normalized_label": "偏光", "version_mismatch": False,
       "documents": [{"document_id": "d1", "title": "CMB 偏光の論文", "reason": "偏光を主題にしている",
                      "evidence_quote": "We measure the polarization", "skeleton_version": "2026.1",
                      "version_mismatch": False, "confidence_label": "中", "created_at": "x"}],
       "near_anchor": {"node_id": "c_cmb_t", "node_label": "温度揺らぎ", "region_label": "宇宙背景放射",
                       "nearness_label": "近い可能性", "skeleton_version": "2026.1"},
       "decision": None}
GAP2 = dict(GAP, cluster_key="gap|astrophysics|r_cmb|再電離", proposed_label="再電離", normalized_label="再電離", near_anchor=None)
EDGE = {"edge_key": "edge|astrophysics|c_a|c_b", "from_id": "c_a", "to_id": "c_b", "from_label": "A", "to_label": "B",
        "origins": ["co_occurrence"], "documents": [{"title": "論文 X"}], "decision": None}


def _client(routes: dict, seen: list) -> EpistemeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.method, request.url.path)
        body = routes.get(key)
        if callable(body):
            body = body(request)
        if body is None:
            return httpx.Response(404, json={"detail": "見つかりません"})
        status, payload = body if isinstance(body, tuple) else (200, body)
        return httpx.Response(status, json=payload)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))


def _run(aid: str, args: dict, session: PersonaSession, client: EpistemeClient) -> list[HttpTrace]:
    client.take_traces()
    ctx = _Ctx(REGISTRY[aid], args, session, client)
    assert ax.execute(aid, ctx)
    return client.take_traces()


def _body(req: httpx.Request) -> dict:
    return json.loads(req.content.decode("utf-8")) if req.content else {}


def _routes(over: dict | None = None) -> dict:
    routes = {
        ("GET", "/api/admin/atlas/domains"): {"domains": [{"domain_key": "astrophysics", "domain_name": "宇宙物理"}]},
        ("GET", CART + "/skeleton"): {"cartridge_id": "astrophysics", "draft": None,
                                      "frozen": {"skeleton": FROZEN, "validation": {"errors": [], "warnings": []}}},
        ("GET", CART + "/reports"): {"reports": []},
        ("GET", CART + "/gap-candidates"): {"candidates": [GAP, GAP2], "skeleton_version": "2026.1", "draft_exists": False},
        ("GET", CART + "/edge-candidates"): {"candidates": [EDGE], "skeleton_version": "2026.1", "draft_exists": False},
        ("GET", CART + "/vectors/status"): {"available": True, "skeleton_version": "2026.1", "total_nodes": 2,
                                            "embedded_nodes": 0, "stale": False},
        ("GET", CART + "/aliases"): {"aliases": []},
    }
    routes.update(over or {})
    return routes


def _opened(routes: dict, seen: list) -> tuple[PersonaSession, EpistemeClient]:
    s = PersonaSession(persona_id="te-03", role="TEACHER", course_id="c1", topic_id="t1")
    c = _client(routes, seen)
    _run("admin.atlas.open", {"domain_hint": "astrophysics"}, s, c)
    return s, c


# ---------------------------------------------------------------------------
# 行為のレジストリ
# ---------------------------------------------------------------------------
def test_all_actions_registered_and_handled():
    # learning.corpus.domains / documents は既存の行為（registry.py）の投影をこの面で差し替える（H4）。
    # learning.atlas.view も同じ（第 2 周 H15: オーバーレイ L1 の形）
    assert {a.id for a in ACTIONS} | {"learning.corpus.domains", "learning.corpus.documents",
                                      "learning.atlas.view"} == set(ax.HANDLERS)
    for a in ACTIONS:
        assert REGISTRY[a.id] is a or REGISTRY[a.id].id == a.id
    assert sorted(a.id for a in ACTIONS if a.llm_cost == "product") == [
        "admin.atlas.freeze", "admin.atlas.gaps.alias_register", "admin.atlas.vectors.refresh"]
    assert not any(a.id.startswith("admin.atlas_binding") for a in ACTIONS)


# ---------------------------------------------------------------------------
# 画面の文言のミラー（JS の原文に逐語で現れる）
# ---------------------------------------------------------------------------
MIRRORS = {
    "admin.js": [ax.GAP_GROUP_TITLE, ax.GAP_GROUP_INTRO, ax.GAP_ORIGIN_LABEL, ax.GAP_ACCEPT_NOTE, ax.GAP_DISMISS_NOTE,
                 ax.GAP_AT_CAPACITY_TEXT, ax.GAP_NO_DRAFT_TEXT, ax.GAP_NOT_ACCEPTED_TEXT, ax.GAP_EMPTY_TEXT,
                 ax.GAP_DISMISS_REASON_REQUIRED, ax.GAP_DOCUMENTS_HEAD, ax.GAP_ALIAS_NOTE_HEAD, ax.GAP_ALIAS_NOTE_TAIL,
                 ax.GAP_FREEZE_PENDING_TEXT, ax.GAP_FREEZE_REANALYSIS_TEXT, ax.GAP_SAVE_TO_DRAFT,
                 ax.EDGE_GROUP_TITLE, ax.EDGE_GROUP_INTRO, ax.EDGE_ORIGIN_VECTOR_LABEL, ax.EDGE_ORIGIN_CO_OCCURRENCE_LABEL,
                 ax.EDGE_ORIGIN_NOTE, ax.EDGE_KIND_REQUIRED, ax.EDGE_DISMISS_REASON_REQUIRED, ax.EDGE_ACCEPT_NOTE,
                 ax.EDGE_DISMISS_NOTE, ax.EDGE_NO_DRAFT_TEXT, ax.EDGE_NOT_ACCEPTED_TEXT, ax.EDGE_EMPTY_TEXT,
                 ax.EDGE_DOCUMENTS_HEAD, ax.EDGE_FREEZE_PENDING_TEXT, ax.VECTOR_GROUP_TITLE, ax.VECTOR_NO_SKELETON_TEXT,
                 ax.VECTOR_STALE_TEXT, ax.VECTOR_LIMIT_TEXT, ax.VECTOR_DONE_TEXT, ax.ALIAS_GROUP_TITLE, ax.ALIAS_EMPTY_TEXT,
                 ax.ALIAS_DISMISS_NOTE, ax.FREEZE_CORRESPONDENCE_TITLE, ax.FREEZE_CORRESPONDENCE_NOTE,
                 ax.FREEZE_CORRESPONDENCE_UNMATCHED_TITLE, ax.FREEZE_CORRESPONDENCE_ALREADY_NOTE, ax.FREEZE_QUESTION,
                 ax.NO_FROZEN_TEXT, *ax.EDGE_KIND_LABELS.values()],
    "atlas-threads-layer.js": [ax.THREADS_PROVENANCE_TEXT, ax.ATLAS_VERSION_PREFIX],
    "landscape-layer.js": [ax.LANDSCAPE_TOGGLE_LABEL, "登録済み論文 ", "AI推定を含む"],
    "corpus-sea.js": [ax.DENSITY_LEGEND_TITLE, *ax.DENSITY_LEGEND_LABELS],
}


@pytest.mark.parametrize("fname", sorted(MIRRORS))
def test_mirrored_strings_appear_verbatim_in_js(fname):
    text = (JS / fname).read_text(encoding="utf-8")
    missing = [s for s in MIRRORS[fname] if s not in text]
    assert not missing, missing


def test_density_thresholds_mirror_corpus_sea():
    text = (JS / "corpus-sea.js").read_text(encoding="utf-8")
    assert f"REGION_DENSITY_THRESHOLDS = [{', '.join(map(str, ax.REGION_DENSITY_THRESHOLDS))}]" in text
    assert f"ANCHOR_RADIUS_THRESHOLDS = [{', '.join(map(str, ax.ANCHOR_RADIUS_THRESHOLDS))}]" in text


# ---------------------------------------------------------------------------
# 教員の行為
# ---------------------------------------------------------------------------
def test_open_loads_tab_like_load_state_and_projects_cards():
    seen: list = []
    s, _ = _opened(_routes(), seen)
    paths = [r.url.path for r in seen]
    assert paths == ["/api/admin/atlas/domains", CART + "/skeleton", CART + "/reports", CART + "/gap-candidates",
                     CART + "/edge-candidates", CART + "/vectors/status", CART + "/aliases"]
    text = project_observation("admin.atlas.open", s.last_status, s.last_body)
    assert "学習者に公開中: 地図 版 2026.1" in text and "\n作成方法: " in text and ax.GAP_GROUP_TITLE in text and "CMB 偏光の論文" in text
    assert "既存の『温度揺らぎ』の別表記の可能性があります（近い可能性・地図 版 2026.1）" in text
    assert "[別名として登録]" in text and ax.EDGE_ORIGIN_CO_OCCURRENCE_LABEL in text
    # 第 2 波（IK-0889）: 件数は描かない。facts の無い旧サーバの応答は版の行だけ
    assert "地図 版 2026.1" in text and "索引済み" not in text and ax.ALIAS_EMPTY_TEXT in text


def test_gap_dismiss_without_reason_is_not_sent():
    seen: list = []
    s, c = _opened(_routes(), seen)
    seen.clear()
    traces = _run("admin.atlas.gaps.dismiss", {"gap_hint": "1", "reason": "  "}, s, c)
    assert not seen and traces[0].error == "precondition:reason"
    assert ax.GAP_DISMISS_REASON_REQUIRED in project_observation("admin.atlas.gaps.dismiss", s.last_status, s.last_body)


def test_gap_accept_and_dismiss_send_decide_and_reload():
    seen: list = []
    decided = dict(GAP, decision={"status": "dismissed", "status_label": "見送り", "review_note": "重なる"})
    s, c = _opened(_routes({("POST", CART + "/gap-candidates/decide"):
                              lambda r: {"decision": {"cluster_key": _body(r)["cluster_key"], "status": "accepted",
                                                      "review_note": ""}}}), seen)
    seen.clear()
    _run("admin.atlas.gaps.accept", {"gap_hint": "偏光"}, s, c)
    assert _body(seen[0]) == {"cluster_key": GAP["cluster_key"], "action": "accept", "review_note": ""}
    assert seen[1].url.path == CART + "/gap-candidates"
    seen.clear()
    _run("admin.atlas.gaps.dismiss", {"gap_hint": "2", "reason": "既存の言い換え"}, s, c)
    assert _body(seen[0])["action"] == "dismiss" and _body(seen[0])["review_note"] == "既存の言い換え"
    assert _body(seen[0])["cluster_key"] == GAP2["cluster_key"]
    # 戻す: 見送り済みの表示 → restore
    s.scratch["atlas_gaps"] = {"candidates": [decided], "draft_exists": False}
    seen.clear()
    _run("admin.atlas.gaps.restore", {"gap_hint": "1"}, s, c)
    assert _body(seen[0])["action"] == "restore"


def test_alias_register_two_step_with_auto_reason():
    seen: list = []
    routes = _routes({("POST", CART + "/aliases"): {"id": "a1"},
                        ("POST", CART + "/gap-candidates/decide"): {"decision": {"status": "dismissed"}}})
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.gaps.alias_register", {"gap_hint": "auto"}, s, c)
    assert _body(seen[0]) == {"node_id": "c_cmb_t", "alias": "偏光", "source": "gap_signal",
                              "evidence": {"cluster_key": GAP["cluster_key"]}}
    decide = next(r for r in seen if r.url.path.endswith("/decide"))
    assert _body(decide)["review_note"] == "既存概念『温度揺らぎ』の別名として登録"


def test_alias_register_not_sent_when_registration_fails():
    seen: list = []
    routes = _routes({("POST", CART + "/aliases"): (422, {"detail": "この項目は現行の版にありません"})})
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.gaps.alias_register", {}, s, c)
    assert not any(r.url.path.endswith("/decide") for r in seen)
    assert "登録できませんでした: この項目は現行の版にありません" in s.last_body["_ui_fact"]


def test_edge_accept_requires_kind_and_maps_japanese():
    seen: list = []
    routes = _routes({("POST", CART + "/edge-candidates/decide"): {"decision": {"status": "accepted"}}})
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.edges.accept", {"edge_hint": "1"}, s, c)
    assert not seen and ax.EDGE_KIND_REQUIRED in s.last_body["_ui_fact"]
    _run("admin.atlas.edges.accept", {"edge_hint": "1", "kind": "依存"}, s, c)
    assert _body(seen[0])["kind"] == "depends"


def test_incorporate_preview_then_apply_uses_draft_revision():
    seen: list = []
    draft = {"skeleton": FROZEN, "revision": 3, "validation": {"errors": [], "warnings": []}}
    accepted = dict(GAP, decision={"status": "accepted", "status_label": "採用"})
    routes = _routes({
        ("GET", CART + "/skeleton"): {"draft": draft, "frozen": {"skeleton": FROZEN}},
        ("GET", CART + "/gap-candidates"): {"candidates": [accepted], "skeleton_version": "2026.1", "draft_exists": True},
        ("POST", CART + "/gap-candidates/incorporate-preview"): {"node_id": "c_new", "proposed_label": "偏光",
                                                                 "layer": "concept", "parent_region_id": "r_cmb",
                                                                 "parent_region_label": "宇宙背景放射",
                                                                 "summary": "概念を 1 つ追加", "patched_draft": {"regions": []},
                                                                 "validation": {"errors": [], "warnings": []}, "revision": 3},
        ("PUT", CART + "/skeleton/draft"): {"draft": {"skeleton": {"regions": []}, "revision": 4}},
        ("POST", CART + "/gap-candidates/mark-incorporated"): {"decision": {"status": "accepted"}},
    })
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.gaps.incorporate_preview", {}, s, c)
    # admin.js openGapIncorporateConfirm: id は出さず、親領域は名前（IK-0850 の是正後）
    fact = s.last_body["_ui_fact"]
    assert "c_new" not in fact and "r_cmb" not in fact and "親領域: 宇宙背景放射" in fact
    assert ax.GAP_SAVE_TO_DRAFT in fact
    assert [r.method for r in seen] == ["POST"]  # 確認までは下書きを書かない
    seen.clear()
    traces = _run("admin.atlas.gaps.incorporate_apply", {}, s, c)
    assert _body(seen[0]) == {"skeleton": {"atlas_skeleton": {"regions": []}}, "revision": 3}
    assert _body(seen[1]) == {"cluster_key": GAP["cluster_key"], "draft_node_id": "c_new"}
    assert traces[0].digest["atlas"]["kind"] == "draft_saved"
    assert "次版の下書きに追加しました" in s.last_body["_ui_fact"]


def test_freeze_sends_only_checked_correspondence_and_reloads():
    seen: list = []
    draft = {"skeleton": FROZEN, "revision": 3, "validation": {"errors": [], "warnings": []}}
    impact = {"removed_node_ids": ["c_old"], "added_node_ids": ["c_new"], "affected_courses": [], "facts": [],
              "correspondence": {"candidates": [{"from_id": "c_old", "to_id": "c_new", "from_label": "旧", "to_label": "新",
                                                 "justification": "lexical_match"}], "unmatched_removed": [], "facts": []}}
    routes = _routes({("GET", CART + "/skeleton"): {"draft": draft, "frozen": {"skeleton": FROZEN}},
                        ("GET", CART + "/freeze-impact"): impact,
                        ("POST", CART + "/skeleton/freeze"): {"frozen": {"version": "2026.2"}}})
    s, c = _opened(routes, seen)
    seen.clear()
    _run("admin.atlas.freeze_impact", {"version": "2026.2"}, s, c)
    assert "□ 旧 → 新（根拠: 名前の一致）" in s.last_body["_ui_fact"]
    assert not any(r.method == "POST" for r in seen)
    seen.clear()
    _run("admin.atlas.freeze", {"version": "2026.2", "correspondence": "none"}, s, c)
    post = next(r for r in seen if r.method == "POST")
    assert _body(post) == {"version": "2026.2", "note": ""}
    seen.clear()
    _run("admin.atlas.freeze", {"version": "2026.2", "correspondence": "旧"}, s, c)
    post = next(r for r in seen if r.method == "POST")
    assert _body(post)["id_migrations"] == [{"from": "c_old", "to": "c_new"}]


def test_freeze_409_pending_labels_are_listed():
    seen: list = []
    draft = {"skeleton": FROZEN, "revision": 3, "validation": {"errors": [], "warnings": []}}
    routes = _routes({("GET", CART + "/skeleton"): {"draft": draft, "frozen": {"skeleton": FROZEN}},
                        ("GET", CART + "/freeze-impact"): {"facts": []},
                        ("POST", CART + "/skeleton/freeze"): (409, {"detail": {"message": ax.GAP_FREEZE_PENDING_TEXT,
                                                                               "pending_labels": ["偏光"]}})})
    s, c = _opened(routes, seen)
    _run("admin.atlas.freeze", {"version": "2026.2"}, s, c)
    assert f"公開に失敗しました: {ax.GAP_FREEZE_PENDING_TEXT}: 偏光" in s.last_body["_ui_fact"]
    assert s.last_status == 409


# ---------------------------------------------------------------------------
# 学生の行為
# ---------------------------------------------------------------------------
ATLAS = {"skeleton_version": "2026.1", "cartridge": "astrophysics", "domain_name": "宇宙物理", "provenance": "AI生成・教員レビュー済",
         "regions": [{"id": "r_cmb"}], "levels": {"2": {"nodes": [{"label": "温度揺らぎ"}]}},
         "threads": {"available": True, "skeleton_version": "2026.1",
                     "items": [{"from": "c_a", "to": "c_b", "from_label": "A", "to_label": "B", "nearness_label": "近い可能性"}]}}
LANDSCAPE = {"domains": [{"frozen_version": "2026.1", "is_course_map": True,
                          "facts": ["前の版（版 2025.1）の地図で確認された位置づけで、現行版（版 2026.1）に対応する場所がありません"]}],
             "corpus": {"source_document_count": 4},
             "documents": [{"title": "論文 X", "placements": [
                 {"node_id": "c_a", "current_node_id": "c_a", "node_label": "A", "status": "inferred",
                  "provenance_label": "AIによる推定（未確認）", "perspective_label": "主題", "reason": "主題が近い",
                  "evidence": [{"quote": "we study A"}]},
                 {"node_id": "c_old", "node_status": "unmapped", "node_label": "旧", "status": "confirmed",
                  "provenance_label": "教員確認済み"}]}]}


def test_atlas_layers_projects_threads_and_placements():
    seen: list = []
    c = _client({("GET", "/api/atlas"): ATLAS, ("GET", "/api/learning/courses/c1/landscape"): LANDSCAPE}, seen)
    s = PersonaSession(persona_id="st-05", course_id="c1", topic_id="t1")
    traces = _run("learning.atlas.layers", {}, s, c)
    text = project_observation("learning.atlas.layers", s.last_status, s.last_body)
    assert "点線の糸（AIによる推定（未確認）・地図 版 2026.1）" in text and "A ⋯ B（近い可能性）" in text
    assert "論文 X（主題）〔AIによる推定（未確認）〕" in text and "旧" not in text.split("登録済み論文")[0].split("前の版")[0]
    assert "現行版（版 2026.1）に対応する場所がありません" in text
    assert "登録済み論文 4 件の解析から生成（地図 版 2026.1・AI推定を含む）" in text
    assert traces[0].digest["atlas"]["thread_pairs"] == [["c_a", "c_b"]]
    assert traces[1].digest["atlas"]["unmapped"] is True


def test_atlas_layers_hides_threads_when_unavailable():
    seen: list = []
    atlas = dict(ATLAS, threads={"available": False, "note": "推定の糸を、いまは表示できません。"})
    c = _client({("GET", "/api/atlas"): atlas, ("GET", "/api/learning/courses/c1/landscape"): None}, seen)
    s = PersonaSession(persona_id="st-05", course_id="c1")
    _run("learning.atlas.layers", {}, s, c)
    text = s.last_body["_ui_fact"]
    assert "推定の糸: （切り替えが出ていない）" in text and "点線の糸" not in text
    assert f"{ax.LANDSCAPE_TOGGLE_LABEL}: （切り替えが出ていない）" in text


def test_sea_map_density_bins_without_counts():
    seen: list = []
    skel = {"skeleton_version": "2026.1", "cartridge": "astrophysics",
            "levels": {"1": {"regions": [{"id": "r1", "label": "領域1"}, {"id": "r2", "label": "霧の領域", "kind": "fog"}],
                             "nodes": [{"id": "n1", "label": "概念1", "region": "r1"}]}}}
    land = {"placements": [{"anchor_node_id": "n1", "document_id": d} for d in ("d1", "d2", "d3")],
            "fringe": [{"region_id": "r1", "region_label": "領域1", "fact_line": "この地図の縁です", "paper_titles": ["論文 Y"]}],
            "outer": {"fact_line": "外の事実"}}
    c = _client({("GET", "/api/learning/corpus/landscape"): land, ("GET", "/api/atlas"): skel,
                 ("GET", "/api/learning/corpus/documents"): {"documents": [{"document_id": "d1", "title": "論文 Z"}]}}, seen)
    s = PersonaSession(persona_id="st-01")
    _run("learning.atlas.sea_map", {"domain_key": "astrophysics"}, s, c)
    text = s.last_body["_ui_fact"]
    assert "- 領域1: 蓄積あり" in text and "- 霧の領域: 霧" in text and "○ 概念1（円の大きさ: 中）" in text
    assert "縁: 領域1 — この地図の縁です" in text and "外: 外の事実" in text
    assert "3" not in text.split("論文の一覧")[0].replace("2026", "")


# ---------------------------------------------------------------------------
# 審判
# ---------------------------------------------------------------------------
def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c-x", domain="astrophysics", snapshot="s",
                                  started_at="2026-10-22T00:00:00+00:00", finished_at="2026-10-22T01:00:00+00:00"))


def _t(path: str, body: Any = None, *, status: int = 200, method: str = "GET", atlas: dict | None = None,
       numeric: list | None = None) -> HttpTrace:
    digest: dict = {}
    if atlas is not None:
        digest["atlas"] = atlas
    if numeric is not None:
        digest["numeric_keys"] = numeric
    return HttpTrace(method=method, path=path, status=status,
                     response_excerpt=json.dumps(body, ensure_ascii=False) if body is not None else "", digest=digest)


def _s(seq: int, aid: str, *traces: HttpTrace, persona: str = "te-03") -> TranscriptStep:
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="x", action_id=aid,
                          screen="admin" if aid.startswith("admin.") else "learning", affordance="atlas.gap-candidates",
                          http=list(traces))


def _keys(found) -> set[str]:
    return {f.fingerprint for f in found}


def test_oracle_dismissed_edge_must_not_appear_in_threads():
    learner = {"kind": "learner_atlas", "skeleton_version": "2026.1", "cartridge": "astrophysics", "region_ids": ["r_cmb"],
               "threads_available": True, "threads_version": "2026.1", "thread_pairs": [["c_b", "c_a"]]}
    steps = [
        _s(1, "admin.atlas.edges.dismiss", _t(CART + "/edge-candidates/decide", {"decision": {
            "edge_key": "edge|astrophysics|c_a|c_b", "status": "dismissed", "review_note": "結ばない", "edge_kind": ""}},
            method="POST")),
        _s(2, "learning.atlas.layers", _t("/api/atlas", atlas=learner), persona="st-05"),
    ]
    found = oracle.check(steps, _factory())
    assert any("見送った関係" in f.hypothesis for f in found)
    restored = steps[:1] + [_s(2, "admin.atlas.edges.restore", _t(CART + "/edge-candidates/decide", {"decision": {
        "edge_key": "edge|astrophysics|c_a|c_b", "status": "candidate", "review_note": "", "edge_kind": ""}}, method="POST")),
        _s(3, "learning.atlas.layers", _t("/api/atlas", atlas=learner), persona="st-05")]
    assert not any("見送った関係" in f.hypothesis for f in oracle.check(restored, _factory()))


def test_oracle_threads_region_endpoint_version_and_limit():
    learner = {"kind": "learner_atlas", "skeleton_version": "2026.1", "region_ids": ["r_cmb"], "threads_available": True,
               "threads_version": "", "thread_pairs": [["r_cmb", "c_a"], ["c_a", "c_b"], ["c_a", "c_c"]]}
    found = oracle.check([_s(1, "learning.atlas.layers", _t("/api/atlas", atlas=learner), persona="st-05")], _factory())
    hyps = " / ".join(f.hypothesis for f in found)
    assert "領域を端点" in hyps and "骨格の版が付いていない" in hyps and "上限" in hyps
    for f in found:
        assert not any(ch.isdigit() for ch in f.hypothesis)


def test_oracle_contract_dismiss_without_reason_and_accept_without_kind():
    steps = [_s(1, "admin.atlas.gaps.list", _t(CART + "/gap-candidates", {"candidates": [{"cluster_key": "gap|a|b|c",
                "decision": {"cluster_key": "gap|a|b|c", "status": "dismissed", "review_note": ""}}]})),
             _s(2, "admin.atlas.edges.accept", _t(CART + "/edge-candidates/decide", {"decision": {
                 "edge_key": "edge|a|x|y", "status": "accepted", "edge_kind": "", "review_note": ""}}, method="POST"))]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("理由が残っていない" in h for h in hyps) and any("関係の種類" in h for h in hyps)


def test_oracle_raw_values_and_counts():
    steps = [_s(1, "learning.atlas.view", _t("/api/atlas", {}, numeric=["confidence", "x", "y", "source_document_count"]),
                persona="st-01"),
             _s(2, "admin.atlas.gaps.list", _t(CART + "/gap-candidates", {}, numeric=["document_count"]))]
    # 第 25 周 H23: 件数キーは画面の投影に漏れたときだけ所見（DTO の欄だけなら鳴らない）
    steps[1].observation = "document_count: 3"
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("生の値" in h for h in hyps) and any("件数の欄" in h for h in hyps)
    dto_only = [_s(2, "admin.atlas.gaps.list", _t(CART + "/gap-candidates", {}, numeric=["document_count"]))]
    assert not any("件数の欄" in f.hypothesis for f in oracle.check(dto_only, _factory()))
    clean = [_s(1, "learning.atlas.view", _t("/api/atlas", {}, numeric=["x", "source_document_count"]), persona="st-01")]
    assert oracle.check(clean, _factory()) == []


def test_oracle_skeleton_writes_outside_freeze():
    st = lambda fp, rev, dfp: {"kind": "skeleton_state", "frozen_version": "2026.1", "frozen_fingerprint": fp,  # noqa: E731
                               "draft_exists": True, "draft_revision": rev, "draft_fingerprint": dfp}
    steps = [_s(1, "admin.atlas.open", _t(CART + "/skeleton", atlas=st("a", 3, "d1"))),
             _s(2, "admin.atlas.gaps.accept", _t(CART + "/gap-candidates/decide", {}, method="POST")),
             _s(3, "admin.atlas.open", _t(CART + "/skeleton", atlas=st("b", 4, "d2")))]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("凍結済みの骨格の中身が変わった" in h for h in hyps) and any("次版の下書きが変わった" in h for h in hyps)
    saved = [steps[0], _s(2, "admin.atlas.gaps.incorporate_apply",
                          _t(CART + "/skeleton/draft", {}, method="PUT",
                             atlas={"kind": "draft_saved", "draft_revision": 4, "draft_fingerprint": "d2"})),
             _s(3, "admin.atlas.open", _t(CART + "/skeleton", atlas=st("a", 4, "d2")))]
    assert oracle.check(saved, _factory()) == []


def test_oracle_learner_version_change_needs_freeze():
    a = lambda v: {"kind": "learner_atlas", "skeleton_version": v, "cartridge": "astrophysics",  # noqa: E731
                   "threads_available": False}
    no_freeze = [_s(1, "learning.atlas.layers", _t("/api/atlas", atlas=a("2026.1")), persona="st-05"),
                 _s(2, "learning.atlas.layers", _t("/api/atlas", atlas=a("2026.2")), persona="st-04")]
    assert any("学生の地図の骨格の版が変わった" in f.hypothesis for f in oracle.check(no_freeze, _factory()))
    frozen = [no_freeze[0], _s(2, "admin.atlas.freeze", _t(CART + "/skeleton/freeze", {}, method="POST")),
              _s(3, "learning.atlas.layers", _t("/api/atlas", atlas=a("2026.2")), persona="st-04")]
    assert not any("版が変わった" in f.hypothesis for f in oracle.check(frozen, _factory()))


def test_oracle_static_fixed_phrase_present_in_product_js():
    assert all(oracle._js_has_fixed_phrase().values())


# ---------------------------------------------------------------------------
# 第 25 周 第 2 周の投影の同期（第 1 周の是正波で変わった応答の欄 — projection_sync_r2.md）
# ---------------------------------------------------------------------------
def test_sync_r2_decision_lines_notes_endpoints_and_provenance():
    seen: list = []
    decided = dict(GAP, decision={"status": "accepted", "status_label": "採用", "decided_by_label": "堀",
                                  "decision_line": "堀が 2026-10-22 に採用しました",
                                  "provenance_line": "AIによる検出 → 堀が 2026-10-22 に採用しました"})
    restored = dict(GAP2, decision={"status": "candidate", "decided_by_label": "堀",
                                    "decision_line": "堀が 2026-10-22 に見送りから戻しました"})
    edge = dict(EDGE, documents=[{"title": "論文 X", "endpoints": [
        {"node_id": "c_a", "node_label": "A", "reason": "A を扱う", "evidence_quote": "we study A"}]}],
        decision={"status": "accepted", "edge_kind": "related",
                  "provenance_line": "AIによる推定 → 堀が 2026-10-22 に採用しました"})
    routes = _routes({
        ("GET", CART + "/skeleton"): {"draft": None, "frozen": {"skeleton": FROZEN, "provenance": {
            "generated_by_label": "同梱の基準地図から取り込み", "reviewed_by_labels": ["基準地図の取り込み時の確認"]}}},
        ("GET", CART + "/gap-candidates"): {"candidates": [decided, restored], "skeleton_version": "2026.1",
                                            "draft_exists": False,
                                            "near_anchor_check": {"state": "no_index", "note": "索引がないので照合していない"}},
        ("GET", CART + "/edge-candidates"): {"candidates": [edge], "skeleton_version": "2026.1", "draft_exists": False,
                                             "dismissed_note": "見送り済みの候補はありません。"},
    })
    s, _c = _opened(routes, seen)
    text = s.last_body["_ui_fact"]
    assert "作成方法: 同梱の基準地図から取り込み" in text and "確認者: 基準地図の取り込み時の確認" in text
    assert "seed" not in text
    assert "AIによる検出 → 堀が 2026-10-22 に採用しました" in text
    assert "堀が 2026-10-22 に見送りから戻しました" in text
    assert "索引がないので照合していない" in text and "見送り済みの候補はありません。" in text
    assert "A を扱う" in text and "「we study A」" in text
    assert "AIによる推定 → 堀が" in text and "[採用]（押せない）" in text
    # 見送りの説明はカードから外れ、見送りの操作の場（prompt）に出る
    assert ax.GAP_DISMISS_NOTE not in text and ax.EDGE_DISMISS_NOTE not in text


def test_sync_r2_sea_fog_from_placements_and_region_home():
    seen: list = []
    skel = {"skeleton_version": "2026.1", "levels": {"1": {
        "regions": [{"id": "r1", "label": "領域1", "kind": "fog"}, {"id": "r2", "label": "空の領域"}],
        "nodes": []}}, "nodes": {"n9": {"label": "霧の概念"}}}
    land = {"placements": [{"anchor_node_id": "n9", "document_id": "d1", "region_id": "r1"}]}
    c = _client({("GET", "/api/learning/corpus/landscape"): land, ("GET", "/api/atlas"): skel,
                 ("GET", "/api/learning/corpus/documents"): {"documents": []}}, seen)
    s = PersonaSession(persona_id="st-01")
    _run("learning.atlas.sea_map", {"domain_key": "astrophysics"}, s, c)
    text = s.last_body["_ui_fact"]
    assert "- 領域1: 少しある" in text and "- 空の領域: 霧" in text
    assert "○ 霧の概念（円の大きさ: 小・領域「領域1」の箱の中）" in text
