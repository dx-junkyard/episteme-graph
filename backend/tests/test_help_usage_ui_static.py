"""学生 HELP ルート（設計書 docs/features/manual_help_kb_design.md §1-3）の
フロントエンド実装（frontend/public/js/app.js, index.html, styles.css）に対する
静的ガードレール。

バックエンド（`support_action='usage_help'` の意図分類・`manual_citations` を
返す `LearningChatResponse`）は並行実装中のため、ここでは JS/HTML/CSS 側の
静的契約のみを検証する:

1. §1-3-1 ヘルプボタン: `support_action='usage_help'` を送る typed action 経路
   （分類LLMを経由しない誤爆ゼロの一次経路）が存在する。
   **学習UI再編 Phase 2（docs/features/learning_ui_inspect_hover_design.md §4）で、
   ボタン押下そのものの挙動は「即送信」から「使い方インスペクト・モードの
   ON/OFF トグル」に置き換わった。** typed usage_help の送信は
   `sendUsageHelpMessage()`（composer/音声の発話経路から呼ばれる）に移設済み —
   ID・typed action 配線・外側クリック許可リストという不変条件は維持する。
2. §1-3-6 出典表示: `manual_citations` があればアシスタントバブルに出典行を
   表示し、既存の `/source-chunk/` チャンクポップアップには接続しない
   （マニュアル節は chunk ではないため）。
3. §1-3-7 音声フェイルソフト: ハンズフリーモードで `manual_citations` が
   非空のときは教材パネル表示（`/source-chunk/` フェッチ）をスキップする。
   Phase 2 でインスペクト・モード中は casual ではなく usage_help ルートへ
   分岐するようになったが、manual_citations ガードの位置関係は不変。
4. 追加コードにポーリング（`setInterval`）を持ち込まない。
5. §4-3 UI内コンテキストヘルプ（Phase 2）: 送信時点の画面モード
   (`screen_mode: "voice"|"lecture"|"chat"`) を判定する pure 関数が存在し、
   ヘルプボタン・通常送信・音声経路すべてが合流する `sendMessage` の
   送信ペイロード構築に組み込まれている。proactive カード・滞留検知・
   自動ポップアップ・ポーリングは作らない（G4）。

インスペクト・モード自体（状態機械・ツールチップ・no_hit 記録・ui_anchor 送信）の
詳細な検証は `test_learning_ui_phase2_static.py` に分離する。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestHelpUsageButton:
    def test_help_button_present_in_index_html(self):
        src = _read(INDEX_HTML)
        assert 'id="help-usage-btn"' in src

    def test_help_button_toggles_inspect_mode_not_immediate_send(self):
        """Phase 2: 押下は使い方インスペクト・モードの ON/OFF トグルのみを行う。
        旧仕様（押下で即 usage_help 送信）はここでは行わない — 送信は
        composer/音声の発話経路（sendUsageHelpMessage 経由）に移設済み。"""
        src = _read(APP_JS)
        assert 'document.getElementById("help-usage-btn")' in src
        start = src.index('const helpUsageBtn = document.getElementById("help-usage-btn");')
        end = src.index("\n    }", start)
        block = src[start:end]
        assert "toggleInspectMode();" in block
        # 押下そのものはメッセージを送らない（送信は sendUsageHelpMessage 側の責務）。
        assert "sendMessage(" not in block
        assert "support_action" not in block
        assert "setInterval" not in block
        assert "confirm(" not in block  # 確認ダイアログは不要（誤爆ゼロの一次経路）

    def test_send_usage_help_message_falls_back_to_fixed_message_when_empty(self):
        """typed usage_help の送信ロジック（旧ヘルプボタンの送信ブロック）は
        `sendUsageHelpMessage()` に再利用されている。入力が空なら固定文へ
        フォールバックし、`support_action='usage_help'` で送る。"""
        src = _read(APP_JS)
        assert "function sendUsageHelpMessage(typedText) {" in src
        start = src.index("function sendUsageHelpMessage(typedText) {")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "この画面の使い方を教えてください" in block
        assert "typed || " in block
        assert 'support_action: "usage_help"' in block
        assert "sendMessage(text" in block
        assert "setInterval" not in block
        assert "confirm(" not in block

    def test_help_button_added_to_material_collapse_outside_click_allowlist(self):
        """機能2の自動畳み（collapseMaterialForChat）が、ヘルプボタン押下直後に
        「領域外クリック」と誤判定されて即座に元へ戻ってしまわないよう、
        既存の #send-btn と同様に外側クリック判定の許可リストに入れる。"""
        src = _read(APP_JS)
        assert "#help-usage-btn" in src
        idx = src.index("if (!_matCollapse.active) return;")
        line = src[idx:src.index("\n", idx + 200)]
        assert "#help-usage-btn" in src[idx:idx + 400]


class TestManualCitationsRendering:
    def test_manual_citations_stored_on_assistant_message(self):
        src = _read(APP_JS)
        assert "manual_citations: data.manual_citations || null," in src

    def test_manual_citations_rendered_as_citation_line(self):
        src = _read(APP_JS)
        assert "msg.manual_citations && msg.manual_citations.length > 0" in src
        start = src.index("if (msg && msg.manual_citations && msg.manual_citations.length > 0) {")
        end = src.index("\n    }", start)
        block = src[start:end]
        assert "manual-citation-line" in block
        assert "manual-citation-chip" in block
        assert "📖" in block

    def test_manual_citations_do_not_wire_source_chunk_popup(self):
        """マニュアル節は chunk ではないため、manual_citations の描画ブロックは
        既存のチャンクポップアップ (/source-chunk/, openSourcePopup, src-cite) に
        一切接続しない。"""
        src = _read(APP_JS)
        start = src.index("if (msg && msg.manual_citations && msg.manual_citations.length > 0) {")
        end = src.index("\n    }", start)
        block = src[start:end]
        assert "source-chunk" not in block
        assert "openSourcePopup" not in block
        assert "src-cite" not in block
        assert "setInterval" not in block


class TestVoicePanelFailSoft:
    def test_voice_panel_skips_material_fetch_when_manual_citations_present(self):
        """`handleVoiceSegment` は Phase 2 でインスペクト・モード分岐
        （casual か usage_help かで payload を切り替える）を得たため、
        直前の `const data = ...` 一行は固定文字列でピン留めしない
        （`async function handleVoiceSegment() {` からの関数本体で検査する）。"""
        src = _read(APP_JS)
        assert "showVoiceSourceMaterial(data.sources || []);" in src
        start = src.index("async function handleVoiceSegment() {")
        end = src.index("const spoken = await speakVoiceAnswer(data.answer);", start)
        block = src[start:end]
        assert '{ intent_mode: "casual" }' in block
        assert "data.manual_citations" in block
        assert "showVoiceSourceMaterial" in block
        # ガード節の内側で呼ばれている（無条件呼び出しに戻っていない）こと。
        guard_idx = block.index("if (!data.manual_citations")
        call_idx = block.index("showVoiceSourceMaterial(data.sources || []);")
        assert guard_idx < call_idx
        assert "setInterval" not in block


class TestScreenModeContextHelp:
    """§4-3: 学生UIの「？」ボタン向けコンテキストヘルプ（Phase 2）。

    `LearningChatRequest.screen_mode` はバックエンド側で別エージェントが並行実装中
    のため、ここではフロント側の判定関数と送信経路への組み込みのみを検証する。
    """

    def test_resolve_screen_mode_function_exists(self):
        src = _read(APP_JS)
        assert "function resolveScreenMode()" in src
        start = src.index("function resolveScreenMode()")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert '"voice"' in block
        assert '"lecture"' in block
        assert '"chat"' in block
        assert "voiceState.active" in block
        assert "lectureState.active" in block

    def test_resolve_screen_mode_priority_is_voice_then_lecture_then_chat(self):
        """優先順は voice → lecture → chat（voiceState.active の判定が
        lectureState.active の判定より先に現れる）。"""
        src = _read(APP_JS)
        start = src.index("function resolveScreenMode()")
        end = src.index("\n  }", start)
        block = src[start:end]
        voice_idx = block.index("voiceState.active")
        lecture_idx = block.index("lectureState.active")
        assert voice_idx < lecture_idx

    def test_send_message_payload_includes_screen_mode(self):
        """全送信経路の合流点である sendMessage の API 送信ペイロードに
        screen_mode が含まれる（ヘルプボタン・通常送信・音声経路のいずれも
        sendMessage を経由するため、ここ1箇所への組み込みで全経路をカバーする）。"""
        src = _read(APP_JS)
        start = src.index("async function sendMessage(text, actionPayload) {")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "screen_mode: resolveScreenMode()" in block

    def test_no_proactive_help_polling_or_auto_popup(self):
        """proactive カード・滞留検知・自動ポップアップ・ポーリングは作らない（G4）。
        既存の❓使い方ボタンは押下時のみ送信する（挙動を変えない）。"""
        src = _read(APP_JS)
        start = src.index("function resolveScreenMode()")
        end = src.index('const helpUsageBtn = document.getElementById("help-usage-btn");')
        block = src[start:end]
        assert "setInterval" not in block
        # ❓ボタンは既存どおり click ハンドラ内でのみ送信する。
        assert 'helpUsageBtn.addEventListener("click", function () {' in src


class TestStylesCss:
    def test_help_and_citation_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".help-usage-btn {",
            ".manual-citation-line {",
            ".manual-citation-chip {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"
