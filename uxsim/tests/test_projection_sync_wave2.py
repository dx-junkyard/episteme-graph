"""第 25 周 第 2 波（第 2 周の是正）の後の投影の同期 — 投影が製品の画面と同じ欄・同じ形を写すことを固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave2.md``、同期の記録は ``projection_sync_wave2.md``。
JS の文言の逐語ミラーは原文に現れることをテストで固定する（PE6）。MockTransport のみ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.config import REPO_ROOT
from uxsim.oracles import atlas as atlas_oracle
from uxsim.oracles import doubt as doubt_oracle
from uxsim.oracles import share as share_oracle
from uxsim.oracles import studio as studio_oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import atlas_exec as ax
from uxsim.runner import doubt_exec as dx
from uxsim.runner import share_exec as shx
from uxsim.runner import studio_exec as sx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, learner_focus_params, project_observation
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

_JS = REPO_ROOT / "frontend" / "public" / "js"
CART = "/api/admin/cartridges/astrophysics/atlas"


def _client(routes: dict, seen: list) -> EpistemeClient:
    """``(method, path の一部)`` → 応答（dict / (status, dict) / callable / (status, dict, headers)）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, frag), resp in routes.items():
            if request.method == method and frag in request.url.path:
                body = resp(request) if callable(resp) else resp
                headers = {}
                if isinstance(body, tuple) and len(body) == 3:
                    headers = body[2]
                    body = (body[0], body[1])
                if isinstance(body, tuple):
                    return httpx.Response(body[0], json=body[1], headers=headers)
                return httpx.Response(200, json=body, headers=headers)
        return httpx.Response(404, json={"detail": "見つかりません"})
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))


def _run(execute: Callable[[str, Any], bool], aid: str, args: dict, s: PersonaSession, c: EpistemeClient):
    c.take_traces()
    assert execute(aid, _Ctx(REGISTRY[aid], args, s, c))
    return c.take_traces()


def _obs(aid: str, s: PersonaSession) -> str:
    return project_observation(aid, s.last_status, s.last_body)


def _factory(_steps: list[TranscriptStep]) -> FindingFactory:
    return FindingFactory(RunMeta(run_id="r", campaign_id="c", domain="astro", snapshot="s"))


# ---------------------------------------------------------------------------
# 面 C（分野の地図）
# ---------------------------------------------------------------------------
FROZEN = {"version": "2026.1", "regions": [{"id": "r_cmb", "label": "CMB",
                                            "concepts": [{"id": "c_a", "label": "温度揺らぎ"},
                                                         {"id": "c_b", "label": "偏光"}]}],
          "edges": []}
DRAFT = {"version": "2026.2", "regions": FROZEN["regions"],
         "edges": [{"from": "c_a", "to": "c_b", "kind": "depends"}]}
ORPHAN_GAP = {"cluster_key": "gap|astrophysics|r_cmb|再電離", "proposed_label": "再電離", "layer_label": "概念",
              "reason_fact": "現行版の候補の条件（2 本以上の論文）に届いていません。", "in_draft": False,
              "decision": {"status": "accepted", "provenance_line": "AIによる検出 → 浦川が 2026-10-22 に採用しました"}}
ORPHAN_EDGE = {"edge_key": "edge|astrophysics|c_a|c_b", "from_id": "c_a", "to_id": "c_b", "from_label": "温度揺らぎ",
               "to_label": "偏光", "direction_line": "「温度揺らぎ」が「偏光」に依存する関係", "in_draft": False,
               "decision": {"status": "accepted", "edge_kind": "depends"}}
EDGE = {"edge_key": "edge|astrophysics|c_a|c_x", "from_id": "c_a", "to_id": "c_x", "from_label": "温度揺らぎ",
        "to_label": "銀河団", "origins": ["vector"], "nearness_label": "かなり近い",
        "basis_line": "両方の項目の説明文どうしが近いことによる候補です。", "in_draft": True,
        "in_draft_note": "この関係は次版の下書きに入っています。",
        "decision": {"status": "accepted", "edge_kind": "related"}}


def _atlas_routes(over: dict | None = None) -> dict:
    routes = {
        ("GET", "/api/admin/atlas/domains"): {"domains": [{"domain_key": "astrophysics", "domain_name": "宇宙物理"}]},
        ("GET", CART + "/skeleton"): {"cartridge_id": "astrophysics",
                                      "draft": {"skeleton": DRAFT, "revision": 3, "validation": {"errors": [], "warnings": []}},
                                      "frozen": {"skeleton": FROZEN}},
        ("GET", CART + "/reports"): {"reports": []},
        ("GET", CART + "/gap-candidates"): {"candidates": [], "skeleton_version": "2026.1", "draft_exists": True,
                                            "empty_notes": ["この分野の論文から、現行版に無い項目の信号はまだありません。"],
                                            "orphaned_decisions": [ORPHAN_GAP], "orphaned_note": "外れた判断の注記。"},
        ("GET", CART + "/edge-candidates"): {"candidates": [EDGE], "skeleton_version": "2026.1", "draft_exists": True,
                                             "folded_note": "説明文どうしの近さだけの候補を畳んでいます。",
                                             "orphaned_decisions": [ORPHAN_EDGE], "orphaned_note": "外れた関係の注記。"},
        ("GET", CART + "/vectors/status"): {"available": True, "skeleton_version": "2026.1", "embedded_nodes": 4,
                                            "total_nodes": 4, "facts": ["索引は現在の公開版のものです。"],
                                            "built_at": "2026-10-22T03:04:05Z", "built_at_label": "2026-10-22 03:04（UTC）"},
        ("GET", CART + "/aliases"): {"aliases": []},
    }
    routes.update(over or {})
    return routes


def _atlas_opened(routes: dict, seen: list):
    s = PersonaSession(persona_id="te-03", role="TEACHER")
    c = _client(routes, seen)
    _run(ax.execute, "admin.atlas.open", {"domain_hint": "astrophysics"}, s, c)
    return s, c


def test_atlas_empty_notes_orphans_folded_and_vector_facts():
    seen: list = []
    s, _c = _atlas_opened(_atlas_routes(), seen)
    out = _obs("admin.atlas.open", s)
    assert ax.GAP_EMPTY_TEXT in out and "現行版に無い項目の信号はまだありません。" in out
    assert ax.GAP_ORPHANED_TITLE in out and "外れた判断の注記。" in out and "[候補へ戻す]" in out
    assert "現行版の候補の条件（2 本以上の論文）に届いていません。" in out
    assert ax.EDGE_ORPHANED_TITLE in out and "「温度揺らぎ」が「偏光」に依存する関係" in out
    assert "説明文どうしの近さだけの候補を畳んでいます。 [すべて表示]" in out
    # 近さの段階ラベルはチップに描かない・basis_line と in_draft_note を描く・下書きに入った関係は反映を押せない
    assert "（かなり近い）" not in out and "両方の項目の説明文どうしが近いことによる候補です。" in out
    assert "この関係は次版の下書きに入っています。" in out and "[次版の下書きへ反映…]（押せない）" in out
    # 索引: facts と built_at_label（件数・ISO 時刻は描かない）
    assert "索引は現在の公開版のものです。" in out and "最後に作成: 2026-10-22 03:04（UTC）" in out
    assert "4/4" not in out and "2026-10-22T03:04:05Z" not in out
    # 下書きプレビューの関係（IK-0886）
    assert "関係（辺）:" in out and "「温度揺らぎ」が「偏光」に依存する — 次版で加わる関係" in out


def test_atlas_show_all_sends_query_and_orphan_actions():
    seen: list = []
    routes = _atlas_routes({("POST", CART + "/gap-candidates/decide"): {"decision": {"status": "candidate"}},
                            ("POST", CART + "/edge-candidates/decide"): {"decision": {"status": "dismissed"}},
                            ("POST", CART + "/edge-candidates/incorporate-preview"):
                                {"from_id": "c_a", "to_id": "c_b", "kind": "depends", "patched_draft": DRAFT,
                                 "validation": {"errors": [], "warnings": []}}})
    s, c = _atlas_opened(routes, seen)
    seen.clear()
    _run(ax.execute, "admin.atlas.edges.show_all", {}, s, c)
    assert any(r.url.params.get("show_all") == "true" for r in seen)
    seen.clear()
    _run(ax.execute, "admin.atlas.gaps.orphaned_restore", {"gap_hint": "再電離"}, s, c)
    body = json.loads(next(r for r in seen if r.method == "POST").content)
    assert body["action"] == "restore" and body["cluster_key"] == ORPHAN_GAP["cluster_key"]
    seen.clear()
    _run(ax.execute, "admin.atlas.edges.orphaned_dismiss", {"edge_hint": "偏光"}, s, c)
    assert not any(r.method == "POST" for r in seen)  # 理由が空なら送らない
    _run(ax.execute, "admin.atlas.edges.orphaned_incorporate", {"edge_hint": "偏光"}, s, c)
    out = _obs("admin.atlas.edges.orphaned_incorporate", s)
    # _edgeCandidateByKey は candidates しか引かないので、外れた関係の確認はプレビューの id を描く（製品のまま）
    assert "つなぐ項目: c_a — c_b" in out and s.scratch["atlas_pending_incorporation"]["kind"] == "edge"


def test_atlas_freeze_blocked_by_readiness_does_not_post():
    seen: list = []
    impact = {"frozen_version": "2026.1", "removed_node_ids": [], "affected_courses": [], "facts": [],
              "readiness": {"ready": False, "facts": ["このままでは公開できません: 再電離"],
                            "pending_labels": ["再電離"], "pending_edges": []}}
    routes = _atlas_routes({("GET", CART + "/freeze-impact"): impact})
    s, c = _atlas_opened(routes, seen)
    _run(ax.execute, "admin.atlas.freeze_impact", {"version": "2026.2"}, s, c)
    out = _obs("admin.atlas.freeze_impact", s)
    assert "このままでは公開できません: 再電離" in out
    assert f"[{ax.FREEZE_BLOCKED_GOTO}] [{ax.FREEZE_BLOCKED_BUTTON}]（押せない）" in out
    seen.clear()
    traces = _run(ax.execute, "admin.atlas.freeze", {"version": "2026.2"}, s, c)
    assert not any(r.method == "POST" for r in seen) and traces[-1].error == "precondition:freeze_ready"
    _run(ax.execute, "admin.atlas.freeze_blocked_goto", {}, s, c)
    assert ax.GAP_ORPHANED_TITLE in _obs("admin.atlas.freeze_blocked_goto", s)


def test_atlas_ready_facts_join_check_lines_and_b14_b16():
    impact = {"facts": ["下書きの領域・概念・関係は、現在の公開版と同じです。"],
              "readiness": {"ready": True, "facts": ["公開を止める条件はありません。"]}}
    assert ax.impact_facts(impact) == ["下書きの領域・概念・関係は、現在の公開版と同じです。", "公開を止める条件はありません。"]
    # B14: 影響は ready と言ったのに凍結が 409 / B16: 止めた候補が一覧に見えない
    impact_t = HttpTrace(method="GET", path=CART + "/freeze-impact", status=200,
                         digest={"atlas": {"kind": "freeze_impact", "ready": True}})
    gaps_t = HttpTrace(method="GET", path=CART + "/gap-candidates", status=200,
                       digest={"atlas": {"kind": "gap_candidates", "candidates": [], "empty_notes": False,
                                         "orphaned_labels": []}})
    freeze_t = HttpTrace(method="POST", path=CART + "/skeleton/freeze", status=409,
                         response_excerpt=json.dumps({"detail": {"message": "x", "pending_labels": ["再電離"]}},
                                                     ensure_ascii=False))
    steps = [TranscriptStep(seq=1, persona_id="te-03", action_id="admin.atlas.gaps.list", http=[gaps_t]),
             TranscriptStep(seq=2, persona_id="te-03", action_id="admin.atlas.freeze_impact", http=[impact_t]),
             TranscriptStep(seq=3, persona_id="te-03", action_id="admin.atlas.freeze", http=[freeze_t])]
    text = " ".join(f.hypothesis for f in atlas_oracle._check_freeze_readiness(steps, _factory(steps)))
    assert "候補が無い理由の事実文も" in text and "押す前に言われない" in text and "別枠にも見えない" in text


def test_atlas_mirrored_texts_exist_in_js():
    js = (_JS / "admin.js").read_text(encoding="utf-8")
    for t in (ax.GAP_ORPHANED_TITLE, ax.EDGE_ORPHANED_TITLE, ax.DRAFT_RELATIONS_EMPTY, ax.DRAFT_RELATION_NEW,
              ax.FREEZE_BLOCKED_GOTO, ax.FREEZE_BLOCKED_BUTTON, ax.EDGE_SHOW_ALL_BUTTON, "候補へ戻す",
              "<strong>関係（辺）</strong>", "最後に作成: "):
        assert t in js, t


# ---------------------------------------------------------------------------
# 面 A（共有）
# ---------------------------------------------------------------------------
def _share_session() -> PersonaSession:
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch["share_element"] = {"element_type": "theory_component", "element_id": "comp-1", "document_id": "d1"}
    return s


def test_share_domain_select_line_and_domain_key_query():
    seen: list = []
    first = {"entries": [], "note": "この教材は複数の分野に関わっています。分野を選ぶと共通部品を探せます。",
             "domains": [{"domain_key": "astrophysics", "domain_label": "宇宙物理"},
                         {"domain_key": "unassigned", "domain_label": ""}], "domain_key": ""}
    second = {"entries": [{"shared_part_id": "e1", "name": "TOV 方程式"}], "domain_key": "astrophysics",
              "domains": first["domains"], "domain_label": "宇宙物理"}

    def resp(req):
        return second if req.url.params.get("domain_key") else first
    c = _client({("GET", "/shared-part-candidates"): resp}, seen)
    s = _share_session()
    _run(shx.execute, "admin.deliberation.shared_part_search", {"q": "TOV"}, s, c)
    out = _obs("admin.deliberation.shared_part_search", s)
    assert f"探す分野: [{shx.DLB_DOMAIN_PLACEHOLDER} ▼]（選択肢: 宇宙物理 / {shx.DLB_DOMAIN_NO_NAME}）" in out
    assert "複数の分野に関わっています" in out
    seen.clear()
    _run(shx.execute, "admin.deliberation.shared_part_domain", {"domain_hint": "宇宙"}, s, c)
    assert seen[-1].url.params.get("domain_key") == "astrophysics" and seen[-1].url.params.get("q") == "TOV"
    out = _obs("admin.deliberation.shared_part_domain", s)
    assert "探す分野: [宇宙物理 ▼]" in out and "TOV 方程式" in out


def test_share_explanation_fact_header_and_add_detail():
    from urllib.parse import quote

    seen: list = []
    fact = "標準の説明を用意できませんでした。独自解釈は追加できます。"
    routes = {("GET", "/explanations"): (200, [], {"X-Explanation-Fact": quote(fact)}),
              ("POST", "/explanations"): (500, {"detail": "説明を保存できませんでした。時間をおいてもう一度お試しください。"})}
    c = _client(routes, seen)
    s = PersonaSession(persona_id="te-02", role="TEACHER")
    s.scratch["share_component"] = {"id": "comp-1", "name": "TOV"}
    s.scratch["share_course_id"] = "c1"
    _run(shx.execute, "admin.explanations.add", {"body": "本文"}, s, c)
    assert json.loads(seen[0].content)["course_id"] == "c1"
    assert "説明を保存できませんでした。" in _obs("admin.explanations.add", s)
    shx._reload_explanations(_Ctx(REGISTRY["admin.explanations.open"], {}, s, c))
    assert fact in _obs("admin.explanations.open", s)


def test_share_outcome_facts_and_symbol_mentions_and_focus_query():
    assert shx._outcome_line({"labels": ["この別名は既に登録されています。変更はありません。"]}, "labels") == \
        ["この別名は既に登録されています。変更はありません。"]
    body = {"available": True, "symbol": "r", "facts": ["この論文の主張では、次のように使われています（定義の文ではありません）。"],
            "mentions": [{"text": "半径 $r$ で質量が決まる"}]}
    out = shx.symbol_popover_view(200, body, "r")
    assert "「半径 $r$ で質量が決まる」" in out
    s = PersonaSession(persona_id="st-04", course_id="c1", topic_id="t1")
    s.material = {"document_id": "d9"}
    assert learner_focus_params(s) == {"topic_id": "t1", "document_id": "d9"}


def test_share_copilot_rules_a10_a11():
    t = HttpTrace(method="POST", path="/api/admin/assistant/chat", status=200,
                  response_excerpt=json.dumps({"answer": "draft を expected_revision 付きで保存し 409 なら読み直します",
                                               "citations": [{"doc": "docs/admin_operations/atlas.md#freeze"}]},
                                              ensure_ascii=False))
    step = TranscriptStep(seq=1, persona_id="te-02", action_id="admin.copilot.chat", args={"tab": "knowledge-library"},
                          http=[t])
    fps = " ".join(f.hypothesis for f in share_oracle._check_copilot_library(step, _factory([step])))
    assert "内部語" in fps and "この画面の手順でも節でもない" in fps


def test_share_mirrored_texts_exist_in_js():
    js = (_JS / "deliberation.js").read_text(encoding="utf-8")
    for t in (shx.DLB_DOMAIN_PLACEHOLDER, shx.DLB_DOMAIN_NO_NAME, "探す分野: "):
        assert t in js, t
    assert shx.DERIVE_DOMAIN_FALLBACK in (_JS / "admin.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 面 B（D層）
# ---------------------------------------------------------------------------
def test_doubt_ledger_document_id_notes_and_supporting_claims():
    seen: list = []
    entry = {"document_id": "d-other", "target_label": "式 (3)", "verification_status": "untested",
             "verification_scopes": [{"condition": "低温", "recorded_by_label": "浦川", "recorded_by": "uuid-x"}],
             "can_edit": False, "can_edit_note": "この論文の編集権限が無いため、台帳への記帳はできません（閲覧と疑義の記帳はできます）。",
             "attribution_note": "この記帳は、台帳が論文ごとに分かれる前に残されたものです。",
             "challenges": [], "challenge_note": "疑義は前提と主張に残せます。この対象を支える主張は次のとおりです。",
             "supporting_claims": [{"claim_id": "cl1", "label": "TOV を解く"}]}
    c = _client({("GET", "/api/admin/doubt/ledger/"): entry}, seen)
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    s.scratch["doubt_course_id"] = "c1"
    ctx = _Ctx(REGISTRY["admin.doubt.ledger_open"], {}, s, c)
    dx._open_ledger(ctx, {"target_type": "equation", "target_id": "eq_3", "label": "", "document_id": "d1"})
    traces = c.take_traces()
    assert seen[-1].url.params.get("document_id") == "d1"
    out = _obs("admin.doubt.ledger_open", s)
    assert "対象: 式 (3)" in out and "記帳者: 浦川" in out and "uuid-x" not in out
    assert "分かれる前に残されたもの" in out and "編集権限が無い" in out
    assert "- TOV を解く ［この主張の台帳を開く］" in out and "［スコープを記帳］［検証状態を記帳する］（押せない）" in out
    # 書き込みは画面で無効（HTTP を出さない）
    seen.clear()
    _run(dx.execute, "admin.doubt.scope_record", {"condition": "低温"}, s, c)
    assert not any(r.method == "POST" for r in seen)
    # 支える主張の台帳は同じ論文で開く
    seen.clear()
    _run(dx.execute, "admin.doubt.supporting_claim_ledger", {}, s, c)
    assert "/ledger/claim/cl1" in seen[-1].url.path and seen[-1].url.params.get("document_id") == "d1"
    # DA10: 応答の document_id が開いた論文と違う
    step = TranscriptStep(seq=1, persona_id="te-05", action_id="admin.doubt.ledger_open", http=traces)
    fps = " ".join(f.hypothesis for f in doubt_oracle.check_ledger_document([step], _factory([step])))
    assert "別の論文の台帳行" in fps


def test_doubt_write_note_and_learner_focus():
    seen: list = []
    routes = {("POST", "/scopes"): {"duplicate": True, "note": "同じ内容の検証スコープが既に記帳されています。別の記帳として並べて残しました。"},
              ("GET", "/api/admin/doubt/ledger/"): {"document_id": "d1", "verification_status": "untested",
                                                    "verification_scopes": [], "challenges": [], "can_edit": True}}
    c = _client(routes, seen)
    s = PersonaSession(persona_id="te-05", role="TEACHER")
    s.scratch["doubt_target"] = {"target_type": "claim", "target_id": "cl1", "label": "主張", "document_id": "d1"}
    s.scratch["doubt_ledger"] = {"can_edit": True}
    _run(dx.execute, "admin.doubt.scope_record", {"condition": "低温", "evidence_quote": "q", "reason": "r"}, s, c)
    assert "別の記帳として並べて残しました。" in _obs("admin.doubt.scope_record", s)
    assert any(r.method == "POST" and r.url.params.get("document_id") == "d1" for r in seen)


# ---------------------------------------------------------------------------
# 面 D（原稿スタジオ）
# ---------------------------------------------------------------------------
def _structure(**over) -> dict:
    topic = {"id": "t1", "title": "TOV", "student_material": {"source_text": "本文\n\n## 付録"}, "spoken_script": "読み",
             "cautions": [], "key_concepts": [], "check_questions": [], "evidence_items": [],
             "appendix_state": {"stale": True, "message": "付録がいまの根拠から作る形と違います。"}}
    base = {"title": "コース", "chapters": [{"title": "第一章", "topics": [topic]}],
            "can_save": False, "save_notice": "このコースのトピックの保存は、コースの所有者だけができます。",
            "appendix_reattach_confirm": "付録を付け直しますか？"}
    base.update(over)
    return base


def _studio_routes(structure: dict, extra: dict | None = None) -> dict:
    routes = {("GET", "/lecture-studio/course-structure"): structure, ("GET", "/lecture-scripts"): [],
              ("GET", "/lecture-studio/settings"): {"lecture_language": "ja"},
              ("POST", "/preview-split"): {"display_segment_count": 1, "spoken_segment_count": 1, "slides": []}}
    routes.update(extra or {})
    return routes


def test_studio_cannot_save_shows_notice_and_does_not_put():
    seen: list = []
    c = _client(_studio_routes(_structure()), seen)
    s = PersonaSession(persona_id="te-04", course_id="c1", role="TEACHER")
    _run(sx.execute, "admin.lecture_studio.open_course", {}, s, c)
    _run(sx.execute, "admin.lecture_studio.select_topic", {}, s, c)
    out = sx.project("admin.lecture_studio.select_topic", s.last_status, s.last_body)
    assert "付録がいまの根拠から作る形と違います。" in out and "［付録を付け直す］（押せない" in out
    assert "コースの所有者だけができます。" in out and "［原稿を保存する］（押せない）" in out
    seen.clear()
    _run(sx.execute, "admin.lecture_studio.save_topic", {}, s, c)
    assert not any(r.method == "PUT" for r in seen)


def test_studio_reattach_delivery_and_rewrite_notices():
    seen: list = []
    st = _structure(can_save=True, save_notice="", delivery={"status": "immediate",
                                                            "message": "このコースは版を発行していないので、保存した内容はすぐ受講者に届きます。"})
    routes = _studio_routes(st, {
        ("POST", "/appendix/reattach"): {"student_material": {"source_format": "eg-markdown-v1", "source_text": "本文\n\n## 付録 新"},
                                         "changed": True, "notice": "付録を付け直して編集欄に入れました。保存すると確定します。"},
        ("PUT", "/lecture-studio/course-topics/"): {"status": "edited", "delivery": st["delivery"]},
        ("POST", "/rewrite"): {"student_material": {"source_text": "新"}, "spoken_script": "読み", "changed": True,
                               "notice": "提案を反映しました。保存すると確定します。",
                               "grounding_notice": "根拠の材料が長いため、一部を省いて AI に渡しました（省いたもの: 図）。学ぶ単位は省いていません。"}})
    c = _client(routes, seen)
    s = PersonaSession(persona_id="te-03", course_id="c1", role="TEACHER")
    _run(sx.execute, "admin.lecture_studio.open_course", {}, s, c)
    _run(sx.execute, "admin.lecture_studio.select_topic", {}, s, c)
    assert "すぐ受講者に届きます。" in sx.project("admin.lecture_studio.select_topic", s.last_status, s.last_body)
    _run(sx.execute, "admin.lecture_studio.reattach_appendix", {}, s, c)
    out = sx.project("admin.lecture_studio.reattach_appendix", s.last_status, s.last_body)
    assert "（確認: 付録を付け直しますか？ → OK）" in out and "編集欄に入れました。" in out
    assert s.scratch["studio_draft"]["material"].endswith("付録 新")
    _run(sx.execute, "admin.lecture_studio.save_topic", {}, s, c)
    out = sx.project("admin.lecture_studio.save_topic", s.last_status, s.last_body)
    assert "授業用ドラフトを保存しました すぐ受講者に届きます" not in out
    assert "授業用ドラフトを保存しました このコースは版を発行していないので" in out
    assert seen[-1].method == "GET"  # 保存の後に course-structure を読み直す（lsRefreshTopicServerFacts）
    _run(sx.execute, "admin.lecture_studio.rewrite_topic", {"prompt": "短く"}, s, c)
    assert "一部を省いて AI に渡しました" in sx.project("admin.lecture_studio.rewrite_topic", s.last_status, s.last_body)


def test_studio_t10_t11_and_course_list_role_label():
    sv_forbidden = {"status": 403, "can_save_shown": True}
    sv_missing = {"status": 200, "figure_embeds": ["f9"], "figures_missing_after_refresh": ["f9"]}
    t = HttpTrace(method="PUT", path="/x", status=200, digest={"studio_saved": sv_forbidden})
    t2 = HttpTrace(method="PUT", path="/x", status=200, digest={"studio_saved": sv_missing})
    step = TranscriptStep(seq=1, persona_id="te-04", action_id="admin.lecture_studio.save_topic", http=[t, t2])
    fps = " ".join(f.hypothesis for f in studio_oracle.check([step], _factory([step])))
    assert "押す前に言われていない" in fps and "根拠リンクにその図が出ていない" in fps
    rows = [{"id": "c1", "title": "宇宙ゼミ", "role": "editor", "role_label": "共有（編集可・トピックの保存は所有者）",
             "visibility": "public"}]
    out = project_observation("admin.courses.list", 200, rows)
    assert "- 宇宙ゼミ 〔共有（編集可・トピックの保存は所有者）〕 開示範囲: 公開中・地図: 未設定" in out
    assert "所有者のみ変更可" in out
    assert "course_id に渡す）: 宇宙ゼミ=c1" in out and "editor" not in out


def test_studio_mirrored_texts_exist_in_js():
    js = (_JS / "admin-lecture-studio.js").read_text(encoding="utf-8")
    for t in (sx.REATTACH_FAIL, sx.REATTACH_BUTTON, sx.SPLIT_MISMATCH_CONFIRM):
        assert t in js, t
    admin = (_JS / "admin.js").read_text(encoding="utf-8")
    assert "公開中" in admin and "グループ限定" in admin and "所有者のみ変更可" in admin
