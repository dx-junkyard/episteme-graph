"""グラフ対話レビュー — フロントエンドの静的検証。

正本: ``docs/features/graph_dialogue_review_design.md`` §3/§6/§7。
admin-graph-review.js（ES5・GraphReview）・admin.js の導線・admin.html の読み込み順・
CSS の存在を、Node 実行なしのソース検査で確認する。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_SRC = (ROOT / "frontend" / "public" / "js" / "admin-graph-review.js").read_text(encoding="utf-8")
VOICE_SRC = (ROOT / "frontend" / "public" / "js" / "admin-voice-chat.js").read_text(encoding="utf-8")
ADMIN_SRC = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
STUDIO_SRC = (ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
HTML_SRC = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
CSS_SRC = (ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")


class TestEs5Compat:
    def test_no_es6_syntax(self):
        # 管理画面 JS は ES5 互換（開発ルール5）。
        assert "=>" not in JS_SRC
        assert not re.search(r"\bconst\s", JS_SRC)
        assert not re.search(r"\blet\s", JS_SRC)
        assert "`" not in JS_SRC  # テンプレートリテラル禁止

    def test_iife_and_public_api(self):
        assert "window.GraphReview" in JS_SRC
        assert '"use strict"' in JS_SRC


class TestAnchors:
    def test_all_screen_anchors_present(self):
        for anchor in (
            "graph-review.modal",
            "graph-review.layer",
            "graph-review.filter-unreviewed",
            "graph-review.next-unreviewed",
            "graph-review.approve",
            "graph-review.reject",
            "graph-review.claim-approve",
            "graph-review.chat",
            "graph-review.graph-chat",
            "graph-review.open-deliberation",
            "graph-review.new-chat",
            "graph-review.voice",
        ):
            assert 'data-ui-anchor="' + anchor + '"' in JS_SRC, anchor

    def test_row_button_anchor_in_admin_js(self):
        assert 'data-ui-anchor="materials.row-graph-review"' in ADMIN_SRC


class TestAdminWiring:
    def test_row_button_requires_document_id(self):
        # 検出要素ボタンと同条件: document_id を持つ行のみメニューに出す。
        idx = ADMIN_SRC.index("admin-graph-review-btn")
        assert "m.document_id" in ADMIN_SRC[max(0, idx - 600):idx]

    def test_click_opens_graph_review(self):
        assert "window.GraphReview.open(" in ADMIN_SRC

    def test_init_injected(self):
        assert (
            "window.GraphReview.init({ apiFetch: apiFetch, escHtml: escHtml, getToken: getAuthToken })"
            in ADMIN_SRC
        )

    def test_assistant_anchor_resolver_registered(self):
        assert "material_graph_review_button" in ADMIN_SRC


class TestGraphViewDelegation:
    def test_uses_lecture_studio_graph_view(self):
        assert "window.LectureStudio && window.LectureStudio.graphView" in JS_SRC
        for api in ("filterByLayer", "layerOptions", "layoutPositions", "displayEdges",
                    "visNodeSpec", "visEdgeSpec", "networkOptions", "detailHeading",
                    "sourceBackingLabel", "reviewReasonLabel"):
            assert api in JS_SRC, api

    def test_studio_delegates_layer_filter(self):
        # lsGraphForCurrentLayer は純粋版への薄い委譲（挙動不変のリファクタ）。
        idx = STUDIO_SRC.index("function lsGraphForCurrentLayer")
        body = STUDIO_SRC[idx: STUDIO_SRC.index("}", idx) + 1]
        assert "lsGraphFilterByLayer(graph, lsState.graphLayerFilter" in body


class TestReviewSemantics:
    def test_unreviewed_filter_keeps_context(self):
        # 未レビュー強調は非該当ノードを消さず薄くする（構造の文脈を保つ）。
        assert "opacity = 0.22" in JS_SRC

    def test_component_actions_present(self):
        assert ">承認</button>" in JS_SRC
        assert ">却下</button>" in JS_SRC
        assert "深く検討" in JS_SRC

    def test_claim_approve_uses_transition_api(self):
        assert '"/review"' in JS_SRC or "/review\"" in JS_SRC
        assert "teacher_approved" in JS_SRC

    def test_component_actions_use_transition_endpoints(self):
        # フル PUT を使わない（同時編集の巻き戻し防止 — 設計書 §4）。
        assert '"/admin/theory-components/" + encodeURIComponent(componentId) + "/" + action' in JS_SRC
        assert 'method: "PUT"' not in JS_SRC

    def test_reload_after_decision(self):
        # 承認・却下後はサーバの状態で再描画（楽観更新でズレを残さない）。
        assert "loadGraph(true)" in JS_SRC


class TestChat:
    def test_graph_chat_uses_graph_sessions_api(self):
        assert "/graph-sessions" in JS_SRC

    def test_node_chat_uses_w_layer_sessions(self):
        assert '"/admin/deliberation/sessions"' in JS_SRC
        assert '"theory_component"' in JS_SRC or "element_type: \"theory_component\"" in JS_SRC

    def test_degraded_reply_is_labeled(self):
        assert "縮退応答" in JS_SRC


class TestVoiceChat:
    """音声対話追補（設計書 §12）— エンジンの独立性と GR1 の維持を静的に固定する。"""

    def _voice_section(self) -> str:
        # 音声配線の区画（ハンズフリー音声対話 〜 公開 API の直前）。
        start = JS_SRC.index("ハンズフリー音声対話")
        end = JS_SRC.index("公開 API", start)
        return JS_SRC[start:end]

    def test_engine_is_es5(self):
        # 管理画面 JS は ES5 互換（開発ルール5）。
        assert "=>" not in VOICE_SRC
        assert not re.search(r"\bconst\s", VOICE_SRC)
        assert not re.search(r"\blet\s", VOICE_SRC)
        assert "`" not in VOICE_SRC

    def test_engine_public_api(self):
        assert "window.AdminVoiceChat" in VOICE_SRC
        assert '"use strict"' in VOICE_SRC

    def test_engine_is_dom_independent(self):
        # エンジンは画面を知らない（アンカー担体は admin-graph-review.js 側）。
        assert "data-ui-anchor" not in VOICE_SRC

    def test_script_order_engine_before_review(self):
        voice = HTML_SRC.index('src="/js/admin-voice-chat.js')
        review = HTML_SRC.index('src="/js/admin-graph-review.js')
        assert voice < review

    def test_voice_section_never_calls_decision_api(self):
        # GR1: 音声は対話の入出力手段のみ。承認・却下 API を呼ぶ経路を作らない。
        section = self._voice_section()
        assert "/approve" not in section
        assert "/reject" not in section

    def test_voice_loop_stops_on_rate_limit(self):
        # 上限（429）に達したら回し続けない。
        idx = JS_SRC.index("function voiceUtterance")
        body = JS_SRC[idx: JS_SRC.index("function voiceTranscribe", idx)]
        assert "429" in body
        assert "stopVoice(" in body

    def test_close_stops_voice(self):
        # 画面を閉じたらマイクを解放する（見えない場所で録音を続けない）。
        idx = JS_SRC.index("function close()")
        assert "stopVoice()" in JS_SRC[idx: idx + 600]


class TestHtmlAndCss:
    def test_script_loaded_between_studio_and_admin(self):
        studio = HTML_SRC.index("admin-lecture-studio.js")
        review = HTML_SRC.index("admin-graph-review.js")
        admin = HTML_SRC.index("/js/admin.js")
        assert studio < review < admin

    def test_css_defined(self):
        assert ".graph-review-modal" in CSS_SRC
        assert ".graph-review-modal[hidden]" in CSS_SRC
