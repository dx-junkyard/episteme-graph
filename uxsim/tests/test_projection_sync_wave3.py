"""第 25 周 第 3 波（第 3 周の是正）の後の投影の同期 — 投影が製品の画面と同じ欄・同じ形を写すことを固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave3.md``、同期の記録は ``projection_sync_wave3.md``。
面 A（share）・面 B（doubt）の同期は第 3 波の是正者・ハーネス是正者が先に入れた分（``test_round25_share_harness.py`` /
``test_r25_doubt_harness*.py`` / ``test_round25_r3_harness_fixes.py``）が固定しているので、ここは面 C（atlas）の残りと
新しい行為 ``learning.atlas.select_node``。JS の文言の逐語ミラーは原文に現れることを固定する（PE6）。
MockTransport のみ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.config import REPO_ROOT
from uxsim.runner import atlas_exec as ax
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, project_observation

_JS = REPO_ROOT / "frontend" / "public" / "js"
CART = "/api/admin/cartridges/astrophysics/atlas"
PENDING = "採用済みで、まだ次版の下書きに入っていません。下書きへ反映するか候補へ戻すまで、公開（凍結）はできません。"
DOC = {"document_id": "d1", "title": "CMB 偏光の論文", "reason": "偏光を主題にしている",
       "evidence_quote": "We measure the polarization", "skeleton_version": "2026.1", "version_mismatch": False}
GAP = {"cluster_key": "gap|astrophysics|r_cmb|偏光", "layer": "concept", "layer_label": "概念",
       "parent_region_label": "宇宙背景放射", "proposed_label": "偏光", "documents": [DOC], "decision": None}


def _client(routes: dict, seen: list) -> EpistemeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = routes.get((request.method, request.url.path))
        if callable(body):
            body = body(request)
        if body is None:
            return httpx.Response(404, json={"detail": "見つかりません"})
        status, payload = body if isinstance(body, tuple) else (200, body)
        return httpx.Response(status, json=payload)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))


def _run(aid: str, args: dict, s: PersonaSession, c: EpistemeClient):
    c.take_traces()
    assert ax.execute(aid, _Ctx(REGISTRY[aid], args, s, c))
    return c.take_traces()


def _body(req: httpx.Request) -> dict:
    return json.loads(req.content.decode("utf-8")) if req.content else {}


def _teacher() -> PersonaSession:
    s = PersonaSession(persona_id="te-03", role="TEACHER", course_id="c1", topic_id="t1")
    s.scratch["atlas_domain"] = "astrophysics"
    return s


# ---------------------------------------------------------------------------
# JS の逐語ミラー
# ---------------------------------------------------------------------------
def test_js_mirrors_exist_in_admin_js():
    admin = (_JS / "admin.js").read_text(encoding="utf-8")
    for text in (ax.GAP_DOCUMENTS_HIDDEN_TEXT, ax.GAP_ACCEPT_MEMO_PROMPT.replace("\n", "\\n"),
                 ax.GAP_RESTORE_REASON_PROMPT.replace("\n", "\\n"), ax.EDGE_ACCEPT_MEMO_PROMPT.replace("\n", "\\n"),
                 "判断のメモ: ", '"採用: " + preview.decision_line', "marked.result_line", "readiness.pending_items",
                 '"・" + parts.join(" ／ ")'):
        assert text in admin, text
    panel = (_JS / "atlas-panel.js").read_text(encoding="utf-8")
    assert ax.PANEL_VERIFY_FALLBACK in panel
    for _key, label, _always in ax.PANEL_ACTIONS:
        assert label in panel
    sea = (_JS / "corpus-sea.js").read_text(encoding="utf-8")
    body = sea[sea.index("function anchorLabel"):]
    assert body.index("regionById[anchorId]") < body.index("posById[anchorId]")  # 領域が先（IK-0941）


# ---------------------------------------------------------------------------
# カード（pending_note・別枠の根拠・判断のメモ）
# ---------------------------------------------------------------------------
def test_gap_card_shows_pending_note_after_decision():
    c = dict(GAP, pending_note=PENDING, decision={"status": "accepted", "status_label": "採用済み", "review_note": "要る"})
    lines = ax.gap_card_lines(c, 1, True)
    text = "\n".join(lines)
    assert PENDING in text and text.index(PENDING) < text.index(ax.GAP_DOCUMENTS_HEAD)
    assert "判断のメモ: 要る" in text
    assert PENDING not in "\n".join(ax.gap_card_lines(GAP, 1, True))  # 旧サーバ・未判断は何も足さない


def test_gap_orphaned_shows_documents_hidden_and_memo():
    s = _teacher()
    item = {"cluster_key": "gap|astrophysics|r_cmb|旧", "proposed_label": "旧項目", "layer_label": "概念",
            "decision": {"status": "accepted", "provenance_line": "堀が採用しました", "review_note": "根拠は前の解析"},
            "reason_fact": "候補の条件から外れました", "documents": [DOC], "documents_hidden": True,
            "in_draft_note": "下書きに入っています", "in_draft": True}
    text = "\n".join(ax.gap_orphaned_lines(s, {"orphaned_decisions": [item], "draft_exists": True}))
    order = ["候補の条件から外れました", ax.GAP_DOCUMENTS_HEAD, "CMB 偏光の論文", ax.GAP_DOCUMENTS_HIDDEN_TEXT,
             "判断のメモ: 根拠は前の解析", "下書きに入っています", "[次版の下書きに取り込む…]（押せない）"]
    assert [text.index(x) for x in order] == sorted(text.index(x) for x in order)
    # 旧サーバ（documents なし）は根拠の区画を出さない
    old = dict(item, documents=None, documents_hidden=False)
    assert ax.GAP_DOCUMENTS_HEAD not in "\n".join(ax.gap_orphaned_lines(s, {"orphaned_decisions": [old]}))


def test_edge_cards_pending_note_and_orphan_basis_and_memo():
    edge = {"edge_key": "edge|astrophysics|a|b", "from_label": "A", "to_label": "B", "origins": ["vector"],
            "pending_note": PENDING, "basis_line": "説明文どうしが近い",
            "decision": {"status": "accepted", "edge_kind": "related", "review_note": "メモ"}}
    text = "\n".join(ax.edge_card_lines(edge, 1, True))
    assert text.index(PENDING) < text.index("説明文どうしが近い") and "判断のメモ: メモ" in text
    s = _teacher()
    item = dict(edge, basis_line=("採用の根拠（論文の配置か説明文の近さか）の記録はありません。"),
                direction_line="A → B")
    otext = "\n".join(ax.edge_orphaned_lines(s, {"orphaned_decisions": [item], "draft_exists": True}))
    assert otext.index("採用の根拠") < otext.index("判断のメモ: メモ") < otext.index("A → B")


# ---------------------------------------------------------------------------
# 採用のメモ・候補へ戻す理由（prompt）
# ---------------------------------------------------------------------------
def test_gap_accept_sends_memo_and_projects_prompt():
    seen: list = []
    s = _teacher()
    c = _client({("POST", CART + "/gap-candidates/decide"): {"decision": {}},
                 ("GET", CART + "/gap-candidates"): {"candidates": [GAP], "draft_exists": False}}, seen)
    s.scratch["atlas_gaps"] = {"candidates": [GAP], "draft_exists": False}
    _run("admin.atlas.gaps.accept", {"gap_hint": "偏光", "memo": "2 本の論文が独立に触れている"}, s, c)
    assert _body(seen[0])["review_note"] == "2 本の論文が独立に触れている"
    obs = project_observation("admin.atlas.gaps.accept", s.last_status, s.last_body)
    assert "（採用のメモ（任意） — ほかの教員が公開（凍結）するときに" in obs and "と書いた" in obs


def test_orphan_restore_sends_reason_and_shows_422_detail():
    seen: list = []
    s = _teacher()
    item = {"cluster_key": "gap|astrophysics|r_cmb|旧", "proposed_label": "旧項目",
            "decision": {"status": "accepted"}}
    detail = "ほかの教員が採用した候補を戻すときは、理由を書いてください（記録に残ります）。"
    c = _client({("POST", CART + "/gap-candidates/decide"):
                 lambda r: (200, {}) if _body(r)["review_note"] else (422, {"detail": detail}),
                 ("GET", CART + "/gap-candidates"): {"candidates": [], "orphaned_decisions": [item]}}, seen)
    s.scratch["atlas_gaps"] = {"candidates": [], "orphaned_decisions": [item]}
    _run("admin.atlas.gaps.orphaned_restore", {}, s, c)
    obs = project_observation("admin.atlas.gaps.orphaned_restore", s.last_status, s.last_body)
    assert "（候補へ戻す理由 — ほかの教員が採用した候補を戻すときは必須です" in obs and "は空のまま OK した" in obs
    assert f"処理に失敗しました: {detail}" in obs
    seen.clear()
    _run("admin.atlas.gaps.orphaned_restore", {"reason": "根拠の論文が消えた"}, s, c)
    assert _body(seen[0]) == {"cluster_key": item["cluster_key"], "action": "restore", "review_note": "根拠の論文が消えた"}


def test_edge_accept_sends_memo():
    seen: list = []
    s = _teacher()
    edge = {"edge_key": "edge|astrophysics|a|b", "from_label": "A", "to_label": "B", "decision": None}
    c = _client({("POST", CART + "/edge-candidates/decide"): {"decision": {}},
                 ("GET", CART + "/edge-candidates"): {"candidates": [edge]}}, seen)
    s.scratch["atlas_edges"] = {"candidates": [edge]}
    _run("admin.atlas.edges.accept", {"edge_hint": "1", "kind": "関連", "memo": "同じ観測で結ばれる"}, s, c)
    assert _body(seen[0]) == {"edge_key": edge["edge_key"], "action": "accept", "review_note": "同じ観測で結ばれる",
                              "kind": "related"}


# ---------------------------------------------------------------------------
# 取り込みの確認・結果文・凍結の止め理由
# ---------------------------------------------------------------------------
def test_preview_decision_lines_order_and_old_server():
    preview = {"decision_line": "堀が 10/23 に採用", "basis_line": "根拠の論文: 『A』、『B』",
               "note_line": "採用のメモはありません。"}
    assert ax.preview_decision_lines(preview) == ["採用: 堀が 10/23 に採用", "根拠の論文: 『A』、『B』",
                                                  "採用のメモはありません。"]
    assert ax.preview_decision_lines({}) == []


def test_incorporate_apply_status_is_result_line():
    seen: list = []
    s = _teacher()
    s.scratch["atlas_pending_incorporation"] = {"kind": "gap", "key": GAP["cluster_key"],
                                                "preview": {"patched_draft": {"regions": []}, "node_id": "n1"}}
    c = _client({("PUT", CART + "/skeleton/draft"): {"draft": {"revision": 2, "skeleton": {}}},
                 ("POST", CART + "/gap-candidates/mark-incorporated"): {"result_line": "『偏光』を次版の下書きに追加しました。"},
                 ("GET", CART + "/gap-candidates"): {"candidates": []}}, seen)
    _run("admin.atlas.gaps.incorporate_apply", {}, s, c)
    assert "『偏光』を次版の下書きに追加しました。" in project_observation("admin.atlas.gaps.incorporate_apply",
                                                                   s.last_status, s.last_body)


def test_freeze_blocked_lines_list_pending_items():
    impact = {"readiness": {"ready": False, "facts": ["採用済みの判断が残っています"],
                            "pending_items": [{"kind": "gap", "label": "偏光", "decision_line": "堀が採用",
                                               "basis_line": "根拠の論文: 『A』", "note_line": "採用のメモ: 要る"},
                                              {"kind": "edge", "label": "A — B"}]},
              "facts": ["既存論文の配置は再解析するまで変わりません"]}
    lines = ax.freeze_blocked_lines(impact)
    assert lines[1:4] == ["採用済みの判断が残っています", "・偏光 ／ 堀が採用 ／ 根拠の論文: 『A』 ／ 採用のメモ: 要る",
                          "・A — B"]
    assert lines[4] == "既存論文の配置は再解析するまで変わりません"


# ---------------------------------------------------------------------------
# 学習者: 地図のパネル（新行為）・論文の海の円の名前
# ---------------------------------------------------------------------------
LIT_VERIFY = "この領域には論文が置かれています（論文の位置）。検証の記録（台帳）はまだありません。"
ATLAS = {"domain_name": "宇宙物理", "skeleton_version": "2026.1",
         "levels": {"1": {"regions": [{"id": "r_cmb", "label": "宇宙背景放射", "kind": "lit"},
                                      {"id": "r_fog", "label": "重力波", "kind": "fog"}], "nodes": []}},
         "nodes": {"r_cmb": {"label": "宇宙背景放射", "verify": LIT_VERIFY, "mind": True},
                   "r_fog": {"label": "重力波", "pill": "論文未投入", "pill_note": "まだ論文が入っていない地形です。"}}}


def test_select_node_renders_panel_like_atlas_panel():
    seen: list = []
    s = PersonaSession(persona_id="st-05", course_id="c1", topic_id="t1")
    c = _client({("GET", "/api/atlas"): ATLAS}, seen)
    _run("learning.atlas.select_node", {"node_hint": "宇宙背景放射"}, s, c)
    obs = project_observation("learning.atlas.select_node", s.last_status, s.last_body)
    panel = obs.split("右のパネル:")[1]
    assert "選んだ: 宇宙背景放射" in panel and LIT_VERIFY in panel and "—" in panel
    assert "[気になる ↗] [修正を報告]" in panel and "ここから学ぶ" not in panel
    _run("learning.atlas.select_node", {"node_hint": "重力波"}, s, c)
    panel = project_observation("learning.atlas.select_node", s.last_status, s.last_body).split("右のパネル:")[1]
    assert "選んだ: 重力波（論文未投入）" in panel and ax.PANEL_VERIFY_FALLBACK in panel and "（霧の領域）" not in panel
    traces = _run("learning.atlas.select_node", {"node_hint": "存在しない概念"}, s, c)
    assert traces[-1].error == "precondition:atlas_node"


def test_sea_anchor_label_prefers_region():
    regions = {"r1": {"label": "宇宙背景放射"}}
    skeleton = {"nodes": {"r1": {"label": "宇宙背景放射（霧の領域）"}}}
    assert ax._sea_anchor_label("r1", {}, regions, skeleton) == "宇宙背景放射"
    assert ax._sea_anchor_label("c1", {"c1": {"label": "温度揺らぎ"}}, regions, skeleton) == "温度揺らぎ"
