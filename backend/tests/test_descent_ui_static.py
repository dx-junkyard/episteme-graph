"""構造の降下路（Phase 3 — 足場ダイヤル・楽屋 v1）フロントエンドの静的ガードレール。

設計正本: docs/features/structure_descent_design.md（SD1〜SD6）。
test_return_door_ui_static.py と同じ方式（Path 読み + 素の assert + re / 波括弧カウント
による関数本体抽出）で、フロントエンドソースの静的検証により受け入れ条件を固定する
（実ブラウザ・実 API には依存しない）。

受け入れ条件との対応:
1. 宣言一行「いまは答えを配らない対話です」が app.js（降下路枠）・discuss.js
   （openCyclePredictArea）・reconstruction.js（renderElicit）の3箇所すべてに逐語存在（SD6）
2. 楽屋宣言文「ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります」の
   逐語存在（SD4）
3. 産出欄（descent-produce）に送信先が無い（fetch/送信配線なしの構造検査, SD3）
4. 誘導語彙（「降りるべき」「今すぐ」）が app.js の新規追加分（descent/backstage 関数
   本体）に無い（SD1/G4）
5. 楽屋送信は sendPrompt 経由で backstage: true を渡す
6. UI アンカー4点セット（material.descent-ladder: KNOWN 登録・UI_ANCHORS 値・
   マニュアル節 {#descent-ladder} 実在・frontend の data-ui-anchor 値が KNOWN ⊆）
7. 新規文言に禁止語彙（踏破/達成率/ランキング/獲得/成長しました/おすすめ/スコア）なし
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
RECON_JS = ROOT / "frontend" / "public" / "js" / "reconstruction.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

ANCHOR_ID = "material.descent-ladder"

# SD6: 宣言された留保（EX-3a 裁定・今井の「宣言された留保」）。
DECLARATION = "いまは答えを配らない対話です"
# SD4: 楽屋の宣言文（API の declaration と同文をフェッチ前に先出しする）。
BACKSTAGE_DECLARATION = "ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります"

# SD1/G4: 「降りるべきだ」等、システムからの降下誘導を示す語彙。
STEERING_WORDS = ("降りるべき", "今すぐ")

# 数値・進捗・ゲーミフィケーションを匂わせる禁止語彙
# （test_return_door_ui_static.py の FORBIDDEN_WORDS と同一集合）。
FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")

# app.js に新規追加した降下路 / 楽屋の関数シグネチャ（本体抽出の対象）。
APP_NEW_FUNCTION_SIGNATURES = (
    "function buildDescentProduceAreaHtml() {",
    "function insertDescentFrame(pop, body, elementType, elementId) {",
    "function buildDescentRungNode(rung) {",
    "async function openBackstagePanel(pop, body, elementType, elementId) {",
    "function renderBackstageSteps(box, steps) {",
    "function backstageItemText(item) {",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを波括弧カウントで抽出する
    （test_return_door_ui_static.py と同じ流儀）。"""
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


def _app_new_bodies() -> str:
    js = _read(APP_JS)
    return "\n".join(_extract_function_body(js, sig) for sig in APP_NEW_FUNCTION_SIGNATURES)


class TestDeclarationLine:
    """SD6: 宣言一行の逐語存在（3箇所すべて・同一文言）。"""

    def test_declaration_constant_in_app_js(self):
        js = _read(APP_JS)
        assert f'const DESCENT_DECLARATION = "{DECLARATION}"' in js

    def test_declaration_wired_into_descent_frame(self):
        """枠の先頭（.descent-declaration）に textContent で常設する。"""
        block = _extract_function_body(
            _read(APP_JS), "function insertDescentFrame(pop, body, elementType, elementId) {"
        )
        assert "DESCENT_DECLARATION" in block
        assert "descent-declaration" in block
        assert ".textContent = DESCENT_DECLARATION" in block

    def test_declaration_in_discuss_elicit(self):
        """精読モードの Elicit（openCyclePredictArea）— 既存プロンプト文言の前に置く。"""
        block = _extract_function_body(_read(DISCUSS_JS), "function openCyclePredictArea() {")
        assert DECLARATION in block
        assert "elicit-declaration" in block
        assert block.index(DECLARATION) < block.index("この論文は何を示すと思いますか？")

    def test_declaration_in_reconstruction_elicit(self):
        """R層の出題カード（renderElicit）— recon-prompt の前に置く。"""
        block = _extract_function_body(_read(RECON_JS), "function renderElicit() {")
        assert DECLARATION in block
        assert "elicit-declaration" in block
        assert block.index(DECLARATION) < block.index("recon-prompt")

    def test_same_wording_in_all_three_files(self):
        for path in (APP_JS, DISCUSS_JS, RECON_JS):
            assert DECLARATION in _read(path), f"{path.name} に宣言一行がありません"

    def test_existing_elicit_wording_untouched(self):
        """既存の文言・プロンプトは非改変（宣言一行の追加のみ）。"""
        assert "この論文は何を示すと思いますか？" in _read(DISCUSS_JS)
        assert "esc(item.prompt)" in _read(RECON_JS)


class TestDescentFrame:
    """足場ダイヤル（SD1: 段を引くのは常に本人・開示状況をサーバへ送らない）。"""

    def test_ladder_fetched_once_via_get_only(self):
        """ladder API は初回クリックの1回フェッチのみ。開示状況の送信（POST 等）を
        しない — insertDescentFrame に method 指定の apiFetch が無いこと。"""
        js = _read(APP_JS)
        assert js.count("/descent/ladder") == 1
        block = _extract_function_body(
            js, "function insertDescentFrame(pop, body, elementType, elementId) {"
        )
        assert "/descent/ladder" in block
        assert '"method"' not in block and "method:" not in block, (
            "降下路枠から GET 以外の呼び出しをしてはならない（開示状況を送らない）"
        )

    def test_unavailable_removes_frame_quietly(self):
        block = _extract_function_body(
            _read(APP_JS), "function insertDescentFrame(pop, body, elementType, elementId) {"
        )
        assert "available !== true" in block
        assert "frame.remove()" in block

    def test_all_rungs_open_disables_button(self):
        block = _extract_function_body(
            _read(APP_JS), "function insertDescentFrame(pop, body, elementType, elementId) {"
        )
        assert "すべて開きました" in block
        assert "disabled = true" in block

    def test_rungs_rendered_via_textcontent_only(self):
        """段の本文は createElement + textContent のみ（本文変数を innerHTML に渡さない）。"""
        block = _extract_function_body(_read(APP_JS), "function buildDescentRungNode(rung) {")
        assert "textContent" in block
        assert "innerHTML" not in block
        assert "insertAdjacentHTML" not in block

    def test_no_evidence_ref_attribute_on_new_ui(self):
        """新ボタン・新枠に data-evidence-ref を付けない（ホバー係留 _latchState との
        干渉回避）。"""
        assert "data-evidence-ref" not in _app_new_bodies()

    def test_no_polling_or_persistent_settings(self):
        """setInterval（ポーリング）・localStorage.setItem（楽屋の永続設定）を作らない。"""
        body = _app_new_bodies()
        assert "setInterval" not in body
        assert "localStorage" not in body


class TestProduceAreaNoSubmission:
    """SD3: 産出欄は無判定 — どこにも送信・保存しない。"""

    def test_produce_area_labels(self):
        block = _extract_function_body(
            _read(APP_JS), "function buildDescentProduceAreaHtml() {"
        )
        assert "自分の語で書いてみる（任意・判定されません）" in block
        assert "<details" in block and "descent-produce-input" in block
        assert "<details open" not in block, "産出欄は既定畳み"

    def test_produce_area_has_no_submission_wiring(self):
        """産出欄の近傍に fetch/送信・イベント配線が無い（構造検査）。"""
        block = _extract_function_body(
            _read(APP_JS), "function buildDescentProduceAreaHtml() {"
        )
        for token in ("fetch", "apiFetch", "POST", "sendPrompt", "addEventListener",
                      "submit", "<button"):
            assert token not in block, f"産出欄に送信系の記述: {token!r}"

    def test_produce_textarea_value_never_read(self):
        """.descent-produce-input は静的シェル内にのみ現れ、値を読む箇所が存在しない
        （app.js 全域での出現が buildDescentProduceAreaHtml 内の1回だけ）。"""
        js = _read(APP_JS)
        assert js.count("descent-produce-input") == 1


class TestBackstagePanel:
    """楽屋（SD4: 集計に入らない）。"""

    def test_backstage_declaration_verbatim(self):
        js = _read(APP_JS)
        assert f'const BACKSTAGE_DECLARATION = "{BACKSTAGE_DECLARATION}"' in js
        block = _extract_function_body(
            js, "async function openBackstagePanel(pop, body, elementType, elementId) {"
        )
        # フェッチ前にローカル定数で先出しし、取得後に API の宣言文で差し替える。
        assert "BACKSTAGE_DECLARATION" in block
        assert "data.declaration" in block

    def test_backstage_send_uses_sendprompt_with_flag(self):
        block = _extract_function_body(
            _read(APP_JS), "async function openBackstagePanel(pop, body, elementType, elementId) {"
        )
        assert "window.sendPrompt(text, { backstage: true })" in block

    def test_backstage_send_shows_fact_line(self):
        block = _extract_function_body(
            _read(APP_JS), "async function openBackstagePanel(pop, body, elementType, elementId) {"
        )
        assert "回答は会話欄に表示されます。この質問は集計に入りません" in block

    def test_backstage_return_restores_previous_dom(self):
        """「本流に戻る」は直前の DOM を復元する（再フェッチしない）。"""
        block = _extract_function_body(
            _read(APP_JS), "async function openBackstagePanel(pop, body, elementType, elementId) {"
        )
        assert "createDocumentFragment" in block
        assert "backstage-return-btn" in block
        assert "本流に戻る" in block

    def test_backstage_step_headings(self):
        js = _read(APP_JS)
        assert "この分野の記法の約束" in js
        assert "記号の定義" in js
        assert "前提概念の一般説明" in js

    def test_backstage_steps_rendered_via_textcontent(self):
        block = _extract_function_body(
            _read(APP_JS), "function renderBackstageSteps(box, steps) {"
        )
        assert "textContent" in block
        assert "innerHTML" not in block

    def test_no_count_display_in_backstage(self):
        """件数・使用数の表示を作らない（SD5）。"""
        for sig in (
            "async function openBackstagePanel(pop, body, elementType, elementId) {",
            "function renderBackstageSteps(box, steps) {",
        ):
            assert "件" not in _extract_function_body(_read(APP_JS), sig)


class TestNoSteeringVocabulary:
    """SD1/G4: システムから「降りるべきだ」と誘導しない。"""

    def test_new_app_functions_have_no_steering_words(self):
        body = _app_new_bodies()
        hits = [w for w in STEERING_WORDS if w in body]
        assert not hits, f"誘導語彙が見つかりました: {hits}"

    def test_manual_section_has_no_steering_words(self):
        md = _read(STUDENT_MANUAL)
        section = md[md.index("{#descent-ladder}"):]
        hits = [w for w in STEERING_WORDS if w in section]
        assert not hits, f"誘導語彙が見つかりました: {hits}"


class TestLearnerHelpAnchor:
    """UIアンカー4点セット（正本 = core/help_kb/ui_anchors.py。
    test_return_door_ui_static.py の TestLearnerHelpAnchor 型）。
    """

    def test_anchor_registered_in_known_ids(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_student_manual_section(self):
        assert UI_ANCHORS.get(ANCHOR_ID) == "student/02-student.md#descent-ladder"

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(STUDENT_MANUAL)
        assert "{#descent-ladder}" in md
        assert "ヒントを一段引く" in md
        assert "楽屋" in md
        # 宣言一行の意味・無判定の産出欄・楽屋の非集計をマニュアル側にも明記する。
        assert DECLARATION in md
        assert BACKSTAGE_DECLARATION in md
        assert "判定も記録もされません" in md
        # §6 精査記録③: R層の既存「点検口: 記号を確認」とは別物である旨の一文。
        assert "点検口: 記号を確認" in md

    def test_frontend_anchor_values_are_all_known(self):
        used: set[str] = set()
        for path in (INDEX_HTML, APP_JS, DISCUSS_JS):
            used |= set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', _read(path)))
        assert ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS)), (
            f"KNOWN_UI_ANCHOR_IDS に無いアンカーID: {used - set(KNOWN_UI_ANCHOR_IDS)}"
        )

    def test_css_rules_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".descent-frame", ".backstage-panel", ".elicit-declaration"):
            assert cls in css, f"styles.css に {cls} がありません"


class TestForbiddenVocabulary:
    """新規 UI 文言に数値・進捗・ゲーミフィケーション語彙を出さない。"""

    def test_new_app_functions_clean(self):
        body = _app_new_bodies()
        hits = [w for w in FORBIDDEN_WORDS if w in body]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_new_discuss_and_recon_functions_clean(self):
        bodies = "\n".join((
            _extract_function_body(_read(DISCUSS_JS), "function openCyclePredictArea() {"),
            _extract_function_body(_read(RECON_JS), "function renderElicit() {"),
        ))
        hits = [w for w in FORBIDDEN_WORDS if w in bodies]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_manual_section_clean(self):
        md = _read(STUDENT_MANUAL)
        section = md[md.index("{#descent-ladder}"):]
        hits = [w for w in FORBIDDEN_WORDS if w in section]
        assert not hits, f"禁止語彙が見つかりました: {hits}"
