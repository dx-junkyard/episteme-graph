"""学生 HELP ルート（設計書 docs/features/manual_help_kb_design.md §1-3）の
フロントエンド実装（frontend/public/js/app.js, index.html, styles.css）に対する
静的ガードレール。

バックエンド（`support_action='usage_help'` の意図分類・`manual_citations` を
返す `LearningChatResponse`）は並行実装中のため、ここでは JS/HTML/CSS 側の
静的契約のみを検証する:

1. §1-3-1 ヘルプボタン: 入力欄付近に `support_action='usage_help'` を送る
   typed action 経路（分類LLMを経由しない誤爆ゼロの一次経路）が存在する。
2. §1-3-6 出典表示: `manual_citations` があればアシスタントバブルに出典行を
   表示し、既存の `/source-chunk/` チャンクポップアップには接続しない
   （マニュアル節は chunk ではないため）。
3. §1-3-7 音声フェイルソフト: ハンズフリーモードで `manual_citations` が
   非空のときは教材パネル表示（`/source-chunk/` フェッチ）をスキップする。
4. 追加コードにポーリング（`setInterval`）を持ち込まない。
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

    def test_help_button_wired_to_typed_usage_help_action(self):
        src = _read(APP_JS)
        assert 'document.getElementById("help-usage-btn")' in src
        start = src.index('const helpUsageBtn = document.getElementById("help-usage-btn");')
        end = src.index("\n    }", start)
        block = src[start:end]
        assert 'support_action: "usage_help"' in block
        assert "sendMessage(text" in block
        # 誤爆ゼロの一次経路: 分類LLM (_classify_intent 相当) を経由せず、
        # 既存の typed action 送信経路 (sendMessage への直接 payload) を再利用する。
        assert "setInterval" not in block
        assert "confirm(" not in block  # 確認ダイアログは不要（誤爆ゼロの一次経路）

    def test_help_button_falls_back_to_fixed_message_when_input_empty(self):
        src = _read(APP_JS)
        start = src.index('const helpUsageBtn = document.getElementById("help-usage-btn");')
        end = src.index("\n    }", start)
        block = src[start:end]
        assert "この画面の使い方を教えてください" in block
        assert "typed || " in block

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
        src = _read(APP_JS)
        assert "showVoiceSourceMaterial(data.sources || []);" in src
        start = src.index("const data = await sendMessage(text, { intent_mode: \"casual\" });")
        end = src.index("const spoken = await speakVoiceAnswer(data.answer);")
        block = src[start:end]
        assert "data.manual_citations" in block
        assert "showVoiceSourceMaterial" in block
        # ガード節の内側で呼ばれている（無条件呼び出しに戻っていない）こと。
        guard_idx = block.index("if (!data.manual_citations")
        call_idx = block.index("showVoiceSourceMaterial(data.sources || []);")
        assert guard_idx < call_idx
        assert "setInterval" not in block


class TestStylesCss:
    def test_help_and_citation_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".help-usage-btn {",
            ".manual-citation-line {",
            ".manual-citation-chip {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"
