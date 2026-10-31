"""第 25 周 第 5 波（第 5 周の是正）の後の投影の同期 — 再開担当が足した分を固定する。

写すキーの一覧は ``uxsim/runs/round25/sync_keys_wave5.md``、同期の記録は ``projection_sync_wave5.md``。
第 5 周のハーネス是正（H27〜H38）の分は ``test_round25_r5_harness_fixes.py``。MockTransport のみ（外部呼び出し・DB なし）。
"""
from __future__ import annotations

import json

import httpx

from uxsim.config import REPO_ROOT
from uxsim.runner import actions_exec, digest, share_exec as sx, studio_exec as stx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_scenario
from uxsim.runner.state import PersonaSession, project_observation, render_material_text

_JS = REPO_ROOT / "frontend" / "public" / "js"
VERSION_NOTE = "同じ論文の別の版（TeX 版・PDF 版など）にも同じ箇所があり、1 つにまとめています。"
WITHHELD = {"latex": "", "latex_withheld": True, "reconstructed": True, "reconstructed_mark": "AI復元",
            "reconstructed_note": "PDF の式が崩れていたため、式は表示していません。"}


def _js(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


# --- chat / RAG（IK-0996 / IK-0986）---------------------------------------------------------------
def test_chat_source_version_note_is_projected_and_digested():
    body = {"answer": "回答 [出典1]", "sources": [{"index": 1, "source_title": "論文A", "chunk_id": "c1",
                                                   "version_note": VERSION_NOTE}, {"index": 2, "source_title": "論文B"}]}
    text = project_observation("learning.chat.ask", 200, body)
    assert f"出典1: 論文A\n  {VERSION_NOTE}" in text
    assert text.count(VERSION_NOTE) == 1  # 畳んでいない出典には付けない
    rec = digest.chat_record(body)
    assert rec["sources"][0]["version_note"] == VERSION_NOTE and "version_note" not in rec["sources"][1]
    assert "s.version_note" in _js("app.js")


def _client(seen: list, routes: dict) -> EpistemeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, suffix), payload in routes.items():
            if request.method == method and request.url.path.endswith(suffix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"detail": "見つかりません"})
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))


def _chat_bodies(seen: list) -> list[dict]:
    return [json.loads(r.content) for r in seen if r.method == "POST" and r.url.path.endswith("/chat")]


def test_open_component_context_is_declared_only_on_voice_turns():
    seen: list = []
    routes = {("GET", "/components/comp-1/context"): {"instance": {"component": {"label": "部品"}}},
              ("POST", "/chat"): {"answer": "はい", "sources": []}}
    s = PersonaSession(persona_id="p", course_id="C", topic_id="t1", uses_voice=True)
    s.remember("components", "comp-1")  # 教材の ⚓ 一覧に出ている部品
    client = _client(seen, routes)
    actions_exec.execute("learning.component.context", {"component_id": "comp-1"}, s, client)
    actions_exec.execute("learning.chat.casual", {"message": "先生の説明を教えて"}, s, client)
    actions_exec.execute("learning.chat.casual", {"message": "もう少し"}, s, client)  # 手を使わない往復は開いたまま
    first, second = _chat_bodies(seen)
    for b in (first, second):
        sel = b["screen_context"]["selection"]
        assert sel["kind"] == "element" and sel["element_id"] == "comp-1" and sel["element_type"] == "component"
        assert b["screen_mode"] == "voice"
    # 別の操作（クリック）でポップオーバーは閉じる → 申告しない
    actions_exec.execute("learning.component.context", {"component_id": "comp-1"}, s, client)
    s.scratch["open_context_element"]["topic_id"] = "t9"  # 別のトピックで開いたもの
    actions_exec.execute("learning.chat.casual", {"message": "どう？"}, s, client)
    assert "screen_context" not in _chat_bodies(seen)[-1]


def test_open_component_context_closed_by_text_send():
    seen: list = []
    routes = {("GET", "/components/comp-1/context"): {"instance": {}},
              ("POST", "/chat"): {"answer": "はい", "sources": []}}
    s = PersonaSession(persona_id="p", course_id="C", topic_id="t1", uses_voice=False)
    s.remember("components", "comp-1")
    client = _client(seen, routes)
    actions_exec.execute("learning.component.context", {"component_id": "comp-1"}, s, client)
    assert s.scratch["open_context_element"]["element_id"] == "comp-1"
    actions_exec.execute("learning.chat.ask", {"message": "この部品は？"}, s, client)
    # 第 25 周 第 7 周 share H46: 開いたまま尋ねた 1 往復は申告する。送った後は閉じる（控えを捨てる）
    sel = _chat_bodies(seen)[-1]["screen_context"]["selection"]
    assert sel["kind"] == "element" and sel["element_id"] == "comp-1"
    assert "open_context_element" not in s.scratch
    actions_exec.execute("learning.chat.ask", {"message": "もう一つ"}, s, client)
    assert "screen_context" not in _chat_bodies(seen)[-1]
    # 画面側の実装（ラッチなしでも開いている部品を申告）が原文にあること
    app = _js("app.js")
    # 第 8 波（IK-1064）: 控えに部品の名前が加わり、オブジェクトの書き方が複数行になった
    assert "state.openContextElement = {" in app and 'element_type: "component"' in app and "_srcOutsideClose" in app


# --- 面 D（studio）IK-1008 の伏せた式 -----------------------------------------------------------------
def test_withheld_formula_in_body_placeholder_and_symbol_popover():
    text = render_material_text({"text": "前 [[FORMULA_0]] 後", "formulas": [WITHHELD]})
    assert "〔AI復元〕 PDF の式が崩れていたため" in text and "$$" not in text and "FORMULA_0" not in text
    body = {"available": True, "symbol": "S", "definition": {"text": "作用", **WITHHELD},
            "equations": [{"name": "作用の式", **WITHHELD}, {"name": "空の式", "latex": ""}]}
    pop = sx.symbol_popover_view(200, body, "S")
    assert pop.count("PDF の式が崩れていたため") == 2  # 定義と equations[] の両方（伏せた式も落とさない）
    assert "作用の式" in pop and "空の式" not in pop and "$$" not in pop


# --- 面 D（studio）IK-0878 の 2 つのボタン ------------------------------------------------------------
def test_align_spoken_markers_mirrors_js():
    js = _js("admin-lecture-studio.js")
    assert stx.INSERT_BOTH_BUTTON in js and stx.ALIGN_MARKERS_BUTTON in js
    assert stx.ALIGN_SHORT_NOTICE.split("、")[0] in js and "一部の区切りを入れられませんでした。" in js
    display = "段落1\n\n段落2\n===\n段落3\n\n段落4"
    spoken = "読み1\n===\n読み2\n\n読み3\n\n読み4"
    out, wanted, placed = stx.align_spoken_markers(display, spoken)
    assert (wanted, placed) == (1, 1) and out.count("\n===\n") == 1
    assert out.split("\n===\n")[0].strip().endswith("読み2")  # 教材欄の相対位置（真ん中）に近い段落の境目
    # 段落も文の境目も無ければ入れきれない
    out2, wanted2, placed2 = stx.align_spoken_markers(display, "一続きの読み上げ")
    assert (wanted2, placed2) == (1, 0) and "===" not in out2


def test_split_save_scenario_offers_new_buttons():
    sc = load_scenario("t-studio-split-save")
    acts = []

    def walk(steps):
        for st in steps:
            acts.extend(o.action for o in st.options)
            walk(getattr(st, "children", []) or [])
    walk(sc.steps)
    assert "admin.lecture_studio.align_slide_markers" in acts
    assert "admin.lecture_studio.insert_slide_marker_both" in acts
    assert acts.index("admin.lecture_studio.insert_slide_marker") < acts.index("admin.lecture_studio.align_slide_markers")
    for aid in ("admin.lecture_studio.align_slide_markers", "admin.lecture_studio.insert_slide_marker_both"):
        assert aid in stx.HANDLERS


# --- 表示名（IK-0848 / IK-1007）----------------------------------------------------------------------
def test_profile_name_actions_registered():
    assert actions_exec._HANDLERS["learning.profile.name"] is actions_exec._HANDLERS["admin.profile.name"]
