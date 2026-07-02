"""質問ボタン統合 + 出典3分類（教材/別の資料/モデル生成）のテスト。

- 「教材に沿って質問」「自由に質問・探索」ボタンが「質問」1つに統合されていること
- search_chunks_with_metadata が material_id を返すこと（教材スコープ判定に必要）
- learning_chat が origin（course_material/other_material）と content_grounding を
  正しい優先順位（教材 > 別の資料 > モデル生成）で計算・返却すること
- フロントの回答バブル・出典タブに出所バッジが表示されること
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
SERVICES = ROOT / "backend" / "api" / "services.py"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestButtonConsolidation:
    def test_only_one_ask_button_in_markup(self):
        html = _read(INDEX_HTML)
        assert 'id="send-explore"' not in html
        assert 'id="send-btn"' in html
        assert ">質問<" in html
        assert "教材に沿って質問" not in html
        assert "自由に質問・探索" not in html

    def test_no_dangling_explore_button_references_in_js(self):
        js = _read(APP_JS)
        assert "send-explore" not in js

    def test_single_button_sends_adaptively_like_enter_key(self):
        js = _read(APP_JS)
        body = js.split("function initInput()")[1].split("\n  function ")[0]
        assert "function sendCurrent()" in body
        assert 'sendWith(Session.inDetour() ? "explore" : "on_path")' in body
        assert "btn.addEventListener(\"click\", sendCurrent)" in body

    def test_voice_button_hidden_during_lecture_mode(self):
        """レクチャーモード中はハンズフリー音声ボタンも隠す（マイク競合を避ける）。"""
        js = _read(APP_JS)
        assert 'document.getElementById("voice-mode-btn")' in js
        assert "if (voiceState.active) stopVoiceMode();" in js


class TestSearchChunksMaterialId:
    def test_query_selects_material_id(self):
        source = _read(SERVICES)
        body = source.split("def search_chunks_with_metadata")[1].split("\ndef ")[0]
        assert "c.material_id" in body
        assert '"material_id"' in body


class TestSchemaFields(object):
    def test_source_tier_item_has_origin(self):
        source = _read(SCHEMAS)
        body = source.split("class SourceTierItem")[1].split("\nclass ")[0]
        assert "origin: str" in body

    def test_chat_response_has_content_grounding(self):
        source = _read(SCHEMAS)
        body = source.split("class LearningChatResponse")[1].split("\nclass ")[0]
        assert "content_grounding: str | None = None" in body


class TestGroundingClassificationWiring:
    def test_course_material_ids_computed_from_course_sources(self):
        source = _read(LEARNING)
        assert "course_material_ids = {" in source
        assert 'if isinstance(s, dict) and s.get("material_id")' in source

    def test_origin_assigned_per_cited_source(self):
        source = _read(LEARNING)
        assert '_origin = "course_material" if r.get("material_id") in course_material_ids else "other_material"' in source
        assert '"origin": _origin,' in source

    def test_grounding_precedence_course_over_other_over_model(self):
        source = _read(LEARNING)
        block = source.split("# 回答内容の出所分類")[1].split("\n\n")[0]
        assert 'if has_topic_material or any(s["origin"] == "course_material" for s in cited_sources):' in block
        assert 'content_grounding = "course_material"' in block
        assert 'elif cited_sources:' in block
        assert 'content_grounding = "other_material"' in block
        assert 'content_grounding = "model_generated"' in block

    def test_topic_material_counts_as_course_material(self):
        source = _read(LEARNING)
        assert "has_topic_material = False" in source
        assert "has_topic_material = True" in source

    def test_response_includes_content_grounding(self):
        source = _read(LEARNING)
        tail = source.split("return LearningChatResponse(")[-1][:400]
        assert "content_grounding=content_grounding," in tail


class TestFrontendGroundingDisplay:
    def test_grounding_meta_and_badge_helpers_exist(self):
        js = _read(APP_JS)
        assert "GROUNDING_META" in js
        assert "course_material:" in js
        assert "other_material:" in js
        assert "model_generated:" in js
        assert "function groundingBadge(g)" in js

    def test_chat_bubble_shows_grounding_badge(self):
        js = _read(APP_JS)
        body = js.split("function renderAiContent(text, msg)")[1].split("\n  function ")[0]
        assert "msg.content_grounding" in body
        assert "groundingBadge(msg.content_grounding)" in body

    def test_message_state_persists_content_grounding(self):
        js = _read(APP_JS)
        assert "state.lastGrounding = data.content_grounding || null;" in js
        assert "content_grounding: data.content_grounding || null," in js

    def test_sources_tab_shows_grounding_banner_and_origin_badge(self):
        js = _read(APP_JS)
        body = js.split("function renderSourcesTab()")[1].split("\n  function ")[0]
        assert "groundingMeta(state.lastGrounding)" in body
        assert "lx-grounding-banner" in body
        assert "ORIGIN_LABEL[s.origin]" in body

    def test_styles_exist(self):
        css = _read(STYLES)
        assert ".grounding-badge" in css
        assert ".lx-grounding-banner" in css
        assert ".lx-origin-badge" in css
