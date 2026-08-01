"""理論グラフ詳細ペインの統一パーツカード化（admin_ux_issues_2026-08-01.md §3.3 Phase 2）。

起票元の症状 §3.1(4)(a)〜(f) が解消された状態を構造的に固定する。

- (a) 「選択中の要素」という見出し注記がある（グラフ全体の説明と選択物の説明が区別できる）
- (b) 見出しの theory stage 名が日本語（§3.4(b) の表と一致。**表示層のみ** — backend の
      `THEORY_STAGE_LABELS` は英語のまま）
- (c) ロールバッジと「役割: 〇〇」行の重複を解消（meta バッジに一本化）
- (d) `description` 空のときに見出しを「説明」欄へ再掲しない
- (e) 根拠リンクの行ラベルが内部語彙のままにならない（種別語彙の正本 element-vocab.js 経由）
- (f) 出所バッジ（source_backing_status）を meta 行＝上部に出す（§3.4(c)）

加えて、クライアント側の場当たり解決だけに頼らずサーバ DTO
（`GET /api/admin/deliberation/elements/{type}/{id}/context`）を遅延マージすること、
その応答に競合ガード（選択中 nodeId との照合）があることを検証する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"

_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# §3.4(b) の決定表。キーは backend が返す英語ラベル文字列。
STAGE_LABELS_JA = {
    "Theory basis": "理論的前提",
    "Observation model": "観測モデル",
    "Observable construction": "観測量の構成",
    "Equation system": "式系",
    "Elimination": "消去",
    "Consistency relation": "整合関係",
    "Diagnostic / application": "診断・応用",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    start = source.index("function " + name)
    nxt = source.find("\n  function ", start + 1)
    return source[start:nxt] if nxt > 0 else source[start:]


def _var_block(source: str, name: str) -> str:
    start = source.index("var " + name + " = {")
    return source[start:source.index("\n  };\n", start)]


# --- ① 統一パーツカードを使っている --------------------------------------------


class TestCardIsUsed:
    def test_node_and_edge_detail_go_through_element_card(self):
        src = _read(ADMIN_LS_JS)
        mount = _fn_body(src, "lsGraphMountCard")
        assert "window.ElementCard.mount(host, dto, opts)" in mount
        for name in ("lsRenderGraphNodeDetail", "lsRenderGraphEdgeDetail"):
            body = _fn_body(src, name)
            assert "lsGraphMountCard(" in body, name

    def test_local_adapter_returns_the_context_lens_dto_shape(self):
        """カードの入力契約は context_lens の DTO 形（{focus, upper, lower, notes}）。"""
        src = _read(ADMIN_LS_JS)
        for name in ("lsGraphNodeToCardDto", "lsGraphEdgeToCardDto"):
            body = _fn_body(src, name)
            for key in ("focus:", "upper:", "lower:", "notes:"):
                assert key in body, name + " / " + key
            assert "element_type:" in body, name
            assert "intrinsic_summary:" in body, name

    def test_card_does_not_receive_confidence(self):
        """W8: confidence の生数値をカードへ渡さない（段階ラベルのみ）。"""
        src = _read(ADMIN_LS_JS)
        for name in ("lsGraphNodeToCardDto", "lsGraphEdgeToCardDto", "lsGraphNodeCardOpts"):
            assert "confidence" not in _fn_body(src, name), name


# --- ② ステージ名の日本語辞書 ---------------------------------------------------


class TestStageLabelsJa:
    def test_dictionary_matches_the_decision_table(self):
        block = _var_block(_read(ADMIN_LS_JS), "LS_THEORY_STAGE_LABELS_JA")
        found = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))
        assert found == STAGE_LABELS_JA, found

    def test_keys_match_backend_theory_stage_labels(self):
        """キーは backend の THEORY_STAGE_LABELS の値（英語）と完全一致すること。"""
        from episteme_graph.agents.component_graph.schema import THEORY_STAGE_LABELS

        assert set(STAGE_LABELS_JA) == set(THEORY_STAGE_LABELS.values())

    def test_backend_labels_stay_english(self):
        """変換は表示層のみ。backend / agent 出力は英語のまま（§3.4(b)）。"""
        from episteme_graph.agents.component_graph.schema import THEORY_STAGE_LABELS

        for label in THEORY_STAGE_LABELS.values():
            assert label.isascii(), label

    def test_conversion_is_applied_on_the_single_label_entrypoint(self):
        """ネットワークのノードラベル・詳細見出し・推奨経路バナーはすべて
        lsGraphSemanticLabel を通るので、変換はそこ1箇所で行う。"""
        src = _read(ADMIN_LS_JS)
        body = _fn_body(src, "lsGraphSemanticLabel")
        assert "lsGraphStageLabelJa(lsGraphSemanticLabelRaw(node))" in body
        # 見出し・ネットワーク・バナーの3経路が同じ関数を通っていること。
        assert "lsGraphSemanticLabel(node)" in _fn_body(src, "lsGraphNodeDisplayLabel")
        assert "lsGraphNodeDisplayLabel(node, nodeId)" in _fn_body(src, "lsGraphDetailHeading")
        assert "lsGraphSemanticLabel(n)" in _fn_body(src, "lsGraphReadingPathBannerHtml")

    def test_unknown_labels_pass_through(self):
        """未知の英語ラベルはそのまま返す（fail-soft・情報を落とさない）。"""
        body = _fn_body(_read(ADMIN_LS_JS), "lsGraphStageLabelJa")
        assert "return raw;" in body

    def test_no_competing_stage_dictionary(self):
        """stage の訳語辞書を2つに増やさない（旧 LS_STAGE_LABELS_JA は統合済み）。"""
        src = _read(ADMIN_LS_JS)
        assert "var LS_STAGE_LABELS_JA" not in src
        for old in ("理論の前提", "方程式系", "整合条件"):
            assert old not in src, "旧訳 " + old + " が残っています"

    def test_stage_ordering_keys_stay_english(self):
        """並び順の正本 LS_MAIN_STAGE_LABELS は英語（backend 照合に使うため）。"""
        src = _read(ADMIN_LS_JS)
        start = src.index("var LS_MAIN_STAGE_LABELS = [")
        block = src[start:src.index("];", start)]
        for label in STAGE_LABELS_JA:
            assert '"' + label + '"' in block, label


# --- ③ サーバ文脈の遅延マージと競合ガード -------------------------------------


class TestServerContextMerge:
    def test_fetches_the_shared_context_endpoint(self):
        body = _fn_body(_read(ADMIN_LS_JS), "lsGraphLoadNodeContext")
        assert '"/admin/deliberation/elements/theory_component/"' in body
        assert "document_id=" in body

    def test_stale_response_is_discarded_by_node_id(self):
        body = _fn_body(_read(ADMIN_LS_JS), "lsGraphLoadNodeContext")
        assert "if (lsGraphSelectedNodeId !== nodeId) return;" in body

    def test_selection_updates_the_guard(self):
        src = _read(ADMIN_LS_JS)
        node_body = _fn_body(src, "lsRenderGraphNodeDetail")
        assert "lsGraphSelectedNodeId = String(nodeId || \"\");" in node_body
        # エッジ選択・背景クリックではノード文脈の遅延応答を反映させない。
        assert 'lsGraphSelectedNodeId = "";' in _fn_body(src, "lsRenderGraphEdgeDetail")

    def test_failure_keeps_the_local_view(self):
        """404 / エラー / available:false は正常系として扱い、ローカル表示を壊さない。"""
        body = _fn_body(_read(ADMIN_LS_JS), "lsGraphLoadNodeContext")
        assert "res.ok ? res.json() : null" in body
        assert "data.available === false" in body
        assert ".catch(" in body

    def test_merge_keeps_the_local_japanese_heading(self):
        body = _fn_body(_read(ADMIN_LS_JS), "lsGraphMergeServerContext")
        assert "localFocus.label || serverFocus.label" in body
        # ローカルだけが持つ事実文（未解決参照の告知など）を捨てない。
        assert "(localDto.notes || []).concat(serverDto.notes || [])" in body


# --- ④ / ⑤ 起票元の症状が復活していない ---------------------------------------


class TestReportedSymptomsStayFixed:
    def test_selected_element_header_note(self):
        """(a) ペインが選択物の説明であることを明示する。"""
        src = _read(ADMIN_LS_JS)
        assert 'headerNote: "選択中の要素"' in _fn_body(src, "lsGraphNodeCardOpts")
        assert 'headerNote: "選択中の関係"' in _fn_body(src, "lsRenderGraphEdgeDetail")

    def test_no_duplicated_role_row(self):
        """(c) 「役割: 〇〇」の重複行を復活させない。"""
        src = _read(ADMIN_LS_JS)
        # 旧実装は badge の直下に '<p class="ls-graph-detail-role">役割: ...' を並べていた。
        assert ">役割: " not in src
        assert "関係種別: " not in src
        assert "ls-graph-detail-role" not in src
        assert "lsGraphRoleLabel(node)" in _fn_body(src, "lsGraphNodeCardOpts")

    def test_no_heading_reuse_fallback_for_empty_description(self):
        """(d) description が空のとき見出し文字列を「説明」欄に再掲しない。"""
        src = _read(ADMIN_LS_JS)
        body = _fn_body(src, "lsGraphNodeToCardDto")
        assert 'intrinsic_summary: String(node.description || "").trim()' in body
        # 旧 fallback（見出しの再掲）が消えていること。
        assert "fallbackText" not in src
        assert 'lsGraphNodeDisplayLabel(node, nodeId) || "").replace' not in src

    def test_source_backing_badge_is_in_the_meta_row(self):
        """(f) 出所バッジは「要確認事項」の中（一番下）ではなく meta 行（上部）。"""
        src = _read(ADMIN_LS_JS)
        badge = _fn_body(src, "lsGraphBackingBadge")
        assert "lsGraphSourceBackingLabel(raw)" in badge
        for name in ("lsGraphNodeCardOpts", "lsRenderGraphEdgeDetail"):
            body = _fn_body(src, name)
            assert "metaBadges" in body, name
            assert "lsGraphBackingBadge(" in body, name
        # 要確認事項（review_reasons）はカードの reviewNotes 側に残す（情報は落とさない）。
        assert "reviewNotes: (node.review_reasons || []).map(lsGraphReviewReasonLabel)" in src

    def test_es5_only(self):
        """開発ルール5: admin-lecture-studio.js は ES5 互換。"""
        src = _read(ADMIN_LS_JS)
        start = src.index("var LS_GRAPH_CARD_HOST_ID")
        end = src.index("function lsRenderGraphFallback")
        block = src[start:end]
        assert "const " not in block
        assert "let " not in block
        assert "=>" not in block
        assert "`" not in block
