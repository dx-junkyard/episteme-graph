"""教材埋め込みの学習画面向け解決 DTO（evidence_items）の回帰テスト。

問題: 同じ教材 DSL（``![[kind:id]]``）に対し、管理画面（授業用ドラフト）は
``lsTopicEvidenceItems`` で全 kind を解決できていたのに、学習画面は equation / figure
以外を常に「未解決」表示にしていた（解決コンテキストが画面ごとに違う不整合）。

このテストは正本 ``core.course_content_builder.build_topic_evidence_items`` /
``normalize_evidence_id`` を検証する:

- 各 kind（component / claim / source / equation / figure）が公開済み参照から解決される。
- 未登録 ID・別トピック専用 ID・コース外 ID は解決されず、情報を露出しない
  （build_topic_evidence_items は「そのトピックに公開済みの参照」だけを返す＝
  DB 上の任意 ID をクライアント入力から解決する経路を作らない）。
- ID 正規化（空白・legacy 二重 eq_・``[[...]]`` 表記）が仕様どおり。
- ``get_topic_material`` レスポンスに evidence_items が載る / フォールバック経路では空。

外部依存なし（純関数）。route テストは境界（get_course_data /
_load_course_figures_by_id）のみモックする（test_learner_figure_delivery.py と同型）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

# backend/api/routes/*.py は `from schemas import ...` の裸 import を使うため api/ を載せる。
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _items_by_ref(items):
    return {f"{it['kind']}:{it['id']}": it for it in items}


class TestBuildTopicEvidenceItems:
    def test_all_kinds_resolved_from_evidence_links(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_001", "summary": "コンポ要約",
                 "support_role": "support", "confidence": "high"},
                {"kind": "claim", "target_id": "claim_001", "summary": "主張テキスト",
                 "support_role": "source_backed"},
                {"kind": "source", "target_id": "ev_001", "summary": "原文引用",
                 "support_role": "source_quote"},
                {"kind": "equation", "target_id": "eq_1", "latex": "a=b", "label": "L",
                 "support_role": "equation"},
                {"kind": "figure", "target_id": "fig1", "figure_id": "FIG-UUID",
                 "figure_key": "fk", "caption": "図の説明", "support_role": "figure"},
            ],
        }
        by_ref = _items_by_ref(build_topic_evidence_items(topic))

        assert by_ref["component:comp_001"]["summary"] == "コンポ要約"
        assert by_ref["component:comp_001"]["role"] == "support"
        assert by_ref["claim:claim_001"]["summary"] == "主張テキスト"
        assert by_ref["source:ev_001"]["summary"] == "原文引用"
        eq = by_ref["equation:eq_1"]
        assert eq["latex"] == "a=b"
        assert eq["title"] == "L"  # 生 LaTeX をタイトルに出さない
        fig = by_ref["figure:FIG-UUID"]
        assert fig["figure_id"] == "FIG-UUID"
        assert fig["caption"] == "図の説明"

    def test_equation_reading_fallback_fields_present(self):
        """LaTeX が無くても plain_text / raw_text を DTO に載せる（3段階フォールバック維持）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "content_blocks": [{
                "type": "equations",
                "items": [
                    {"equation_id": "eq_plain", "plain_text": "F は a と b の和"},
                    {"equation_id": "eq_raw", "raw_text": "delta(k) = ..."},
                    {"equation_id": "eq_empty", "label": "空"},  # 表現ゼロ → 落とす
                ],
            }],
        }
        by_ref = _items_by_ref(build_topic_evidence_items(topic))
        assert by_ref["equation:eq_plain"]["plain_text"] == "F は a と b の和"
        assert by_ref["equation:eq_raw"]["raw_text"] == "delta(k) = ..."
        assert "equation:eq_empty" not in by_ref

    def test_source_equation_quote_moves_tex_to_latex(self):
        """生 TeX を summary に持つ source は latex に移す（220字切り詰めで壊さない）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "source", "target_id": "ev_eq", "support_role": "equation_quote",
                 "summary": r"\begin{aligned} x&=y \\ z&=w \end{aligned}"},
            ],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["source:ev_eq"]
        assert item["latex"].startswith(r"\begin{aligned}")
        assert item["summary"] == ""
        assert item["title"] == "数式引用"

    def test_topic_summary_and_source_summary_resolve(self):
        from core.course_content_builder import build_topic_evidence_items

        by_ref = _items_by_ref(build_topic_evidence_items({"summary": "概要テキスト"}))
        assert by_ref["source:topic_summary"]["title"] == "トピック概要"
        assert by_ref["source:summary"]["title"] == "トピック概要"
        assert "概要テキスト" in by_ref["source:topic_summary"]["summary"]

    def test_component_fallback_from_content_blocks(self):
        """evidence_links に無い linked_component_id は content_blocks から補完する。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "linked_component_ids": ["comp_extra"],
            "content_blocks": [{
                "type": "components",
                "items": [{"component_id": "comp_extra", "label": "ラベルX",
                           "teaching_takeaway": "要点X"}],
            }],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_extra"]
        assert item["title"] == "ラベルX"
        assert item["summary"] == "要点X"

    def test_source_excerpt_becomes_source_item(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {"source_excerpt": "原文抜粋テキスト", "linked_chunk_ids": ["chunk_9"]}
        item = _items_by_ref(build_topic_evidence_items(topic))["source:chunk_9"]
        assert item["title"] == "原文抜粋"
        assert item["summary"] == "原文抜粋テキスト"

    def test_dedup_prefers_evidence_link_over_content_block(self):
        """同一 (kind, id) は先勝ち（evidence_links を content_blocks より優先）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_x", "summary": "正本の要約"},
            ],
            "linked_component_ids": ["comp_x"],
            "content_blocks": [{
                "type": "components",
                "items": [{"component_id": "comp_x", "label": "別ラベル", "summary": "重複"}],
            }],
        }
        items = build_topic_evidence_items(topic)
        comps = [it for it in items if it["kind"] == "component" and it["id"] == "comp_x"]
        assert len(comps) == 1
        assert comps[0]["summary"] == "正本の要約"

    def test_empty_topic_is_safe(self):
        from core.course_content_builder import build_topic_evidence_items

        assert build_topic_evidence_items({}) == []
        assert build_topic_evidence_items(None) == []


class TestComponentEvidenceRedesignPhase1:
    """component_evidence_redesign.md Phase 1: evidence_links 経由の component は
    title に summary を流用せず label を使い、content_blocks の rich 投影
    （label / narrative_role / document_id / supports）をマージする。"""

    def test_component_title_uses_link_label_not_summary(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "コンポ要約",
                 "support_role": "support", "label": "ラベルA"},
            ],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_1"]
        assert item["title"] == "ラベルA"
        # summary はそのまま維持される(title と重複する文言を summary から消したりしない)。
        assert item["summary"] == "コンポ要約"

    def test_component_title_falls_back_to_generic_label_without_any_label(self):
        """label が evidence_link にも content_blocks にも無ければ「論理コンポーネント」
        にする(summary をタイトルへ流用しない)。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "コンポ要約",
                 "support_role": "support"},
            ],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_1"]
        assert item["title"] == "論理コンポーネント"
        assert item["summary"] == "コンポ要約"

    def test_component_title_falls_back_to_content_block_label(self):
        """evidence_link に label が無くても content_blocks 側の label を使う。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "コンポ要約",
                 "support_role": "support"},
            ],
            "content_blocks": [{"type": "components", "items": [
                {"component_id": "comp_1", "label": "content_blocksラベル"},
            ]}],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_1"]
        assert item["title"] == "content_blocksラベル"

    def test_component_merges_supports_narrative_and_document_id(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "コンポ要約",
                 "support_role": "support"},
            ],
            "content_blocks": [{"type": "components", "items": [
                {
                    "component_id": "comp_1",
                    "label": "ラベルA",
                    "narrative_role": "この段階は前提を与える。",
                    "document_id": "doc-1",
                    "preconditions": [{"text": "前提A", "claim_ids": [], "equation_ids": []}],
                    "inputs": [],
                    "outputs": [],
                    "cautions": [],
                    "equations": [{"id": "eq_1", "role": "input"}],
                    "claims": ["clm_1"],
                    "dependencies": [{"type": "requires", "targets": ["comp_2"], "reason": "r"}],
                },
            ]}],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_1"]

        assert item["label"] == "ラベルA"
        assert item["narrative_role"] == "この段階は前提を与える。"
        assert item["document_id"] == "doc-1"
        assert item["supports"]["preconditions"] == [{"text": "前提A", "claim_ids": [], "equation_ids": []}]
        assert item["supports"]["equations"] == [{"id": "eq_1", "role": "input"}]
        assert item["supports"]["claims"] == ["clm_1"]
        assert item["supports"]["dependencies"] == [{"type": "requires", "targets": ["comp_2"], "reason": "r"}]
        # 既存フィールドは維持される(フロント互換)。
        assert item["kind"] == "component"
        assert item["id"] == "comp_1"
        assert item["summary"] == "コンポ要約"
        assert item["role"] == "support"

    def test_component_without_content_block_match_has_no_supports_key(self):
        """content_blocks に該当する component が無ければ supports は付けない
        (rich投影が無い旧データ・別トピックの component でも壊れない)。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_unmatched", "summary": "要約"},
            ],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_unmatched"]
        assert "supports" not in item
        assert "narrative_role" not in item
        assert "document_id" not in item

    def test_component_fallback_path_also_merges_rich_projection(self):
        """linked_component_ids フォールバック経路でも同じ rich マージが効く。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "linked_component_ids": ["comp_extra"],
            "content_blocks": [{"type": "components", "items": [
                {
                    "component_id": "comp_extra",
                    "label": "ラベルX",
                    "teaching_takeaway": "要点X",
                    "narrative_role": "位置づけ文",
                    "document_id": "doc-9",
                    "equations": [{"id": "eq_9", "role": "output"}],
                },
            ]}],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_extra"]

        assert item["title"] == "ラベルX"
        assert item["summary"] == "要点X"
        assert item["label"] == "ラベルX"
        assert item["narrative_role"] == "位置づけ文"
        assert item["document_id"] == "doc-9"
        assert item["supports"]["equations"] == [{"id": "eq_9", "role": "output"}]

    def test_component_rich_projection_without_new_fields_is_backward_compatible(self):
        """content_blocks の components 投影が旧フォーマット(narrative_role 等の
        新フィールド無し)でも build_topic_evidence_items は壊れない。supports は
        存在してもすべて空リストへ縮退する(存在自体は rich マッチの証跡)。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "linked_component_ids": ["comp_old"],
            "content_blocks": [{"type": "components", "items": [
                {"component_id": "comp_old", "label": "旧ラベル", "summary": "旧要約"},
            ]}],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_old"]

        assert item["title"] == "旧ラベル"
        assert item["label"] == "旧ラベル"
        assert item["supports"] == {
            "preconditions": [], "inputs": [], "outputs": [], "cautions": [],
            "equations": [], "claims": [], "dependencies": [],
        }

    def test_component_confidence_stays_topic_label_not_raw_narrative_value(self):
        """narrative_role がマージされても confidence はトピックの段階ラベル
        (文字列)のままで、narrative 由来の生 confidence(float)は混入しない。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "content_confidence": "high",
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "コンポ要約",
                 "support_role": "support", "confidence": "high"},
            ],
            "content_blocks": [{"type": "components", "items": [
                {"component_id": "comp_1", "label": "ラベルA", "narrative_role": "位置づけ"},
            ]}],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_1"]

        assert item["confidence"] == "high"
        assert isinstance(item["confidence"], str)


class TestScopingAndPrivacy:
    """解決対象は「そのトピックに公開済みの参照」だけ — DB 上の任意 ID は解決しない。"""

    def test_only_this_topic_refs_are_resolvable(self):
        from core.course_content_builder import build_topic_evidence_items

        topic_a = {"evidence_links": [{"kind": "claim", "target_id": "claim_A", "summary": "A"}]}
        topic_b = {"evidence_links": [{"kind": "claim", "target_id": "claim_B", "summary": "B"}]}

        refs_a = set(_items_by_ref(build_topic_evidence_items(topic_a)))
        refs_b = set(_items_by_ref(build_topic_evidence_items(topic_b)))

        assert "claim:claim_A" in refs_a
        # 別トピック専用の ID は解決集合に入らない（露出しない）。
        assert "claim:claim_B" not in refs_a
        assert "claim:claim_A" not in refs_b

    def test_unregistered_id_is_not_present(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {"evidence_links": [{"kind": "component", "target_id": "comp_1", "summary": "x"}]}
        refs = set(_items_by_ref(build_topic_evidence_items(topic)))
        assert "component:comp_999" not in refs
        assert "claim:comp_1" not in refs  # kind 違いキーも生成しない


class TestNormalization:
    def test_normalize_matches_frontend_spec(self):
        from core.course_content_builder import normalize_evidence_id

        assert normalize_evidence_id("  comp_001  ") == "comp_001"
        assert normalize_evidence_id("[[claim_1]]") == "claim_1"
        assert normalize_evidence_id("[[[[eq_x]]]]") == "eq_x"
        # legacy 二重 eq_ プレフィックス（case-insensitive）
        assert normalize_evidence_id("eq_eq_F2") == "eq_F2"
        assert normalize_evidence_id("EQ_EQ_F2") == "eq_F2"
        assert normalize_evidence_id("eq_F2") == "eq_F2"
        assert normalize_evidence_id(None) == ""

    def test_ids_in_items_are_normalized(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "equation", "target_id": "eq_eq_F2", "latex": "a=b"},
                {"kind": "component", "target_id": "[[comp_7]]", "summary": "s"},
            ],
        }
        refs = set(_items_by_ref(build_topic_evidence_items(topic)))
        assert "equation:eq_F2" in refs
        assert "component:comp_7" in refs


class TestGetTopicMaterialResponse:
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_response_includes_resolved_evidence_items(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        mock_course.return_value = {
            "topics": [{
                "id": "topic-1",
                "student_material": {"source_text": "本文 ![[component:comp_1]] と ![[claim:claim_1]] と ![[source:ev_1]]"},
                "evidence_links": [
                    {"kind": "component", "target_id": "comp_1", "summary": "コンポ"},
                    {"kind": "claim", "target_id": "claim_1", "summary": "主張"},
                    {"kind": "source", "target_id": "ev_1", "summary": "原文"},
                ],
            }],
            "sources": [{"material_id": "mat-1"}],
        }
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        chunk = resp.chunks[0]
        by_ref = _items_by_ref(chunk.evidence_items)
        assert "component:comp_1" in by_ref
        assert "claim:claim_1" in by_ref
        assert "source:ev_1" in by_ref

    @patch("api.routes.learning.get_course_chunks_ordered")
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_fallback_chunk_path_has_empty_evidence_items(self, mock_course, mock_figs, mock_chunks):
        """topic content 未生成の PDF チャンク経路では evidence_items は空（後方互換）。"""
        from api.routes.learning import get_topic_material

        mock_course.return_value = {
            "topics": [{"id": "topic-1"}],  # student_material / content / summary なし
            "sources": [{"material_id": "mat-1"}],
        }
        mock_figs.return_value = {}
        mock_chunks.return_value = [{
            "id": "chunk-1", "text": "PDF由来チャンク", "chunk_index": 0,
            "formulas": [], "chapter": None, "section": None,
            "material_id": "mat-1", "graph_mentions": [],
        }]

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        assert resp.chunks[0].evidence_items == []


class TestEquationExplanatoryProjection:
    """数式の説明材料（役割 / 意味の要約 / 記号の意味）の投影。

    設計正本: docs/features/equation_hover_content_design.md（EH1〜EH5）。
    背景: equations.json は役割・記号の意味を持つのに、コーススナップショットへは
    latex / plain_text / raw_text しか落ちておらず、学習画面の数式ホバーが
    「生 TeX の再掲」しかできなかった（設計書 §1.3）。
    """

    def test_projection_flattens_nested_semantics(self):
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({
            "equation_id": "eq_2_7",
            "semantics": {
                "equation_type": "definition",
                "summary": "密度揺らぎの定義",
                "defined_symbols": [
                    {"symbol": "\\delta", "meaning": "物質密度揺らぎ"},
                    {"symbol": "\\bar\\rho", "meaning": "平均密度"},
                ],
            },
        })
        assert projected["role_in_argument"] == "definition"
        assert projected["semantic_kind"] == "密度揺らぎの定義"
        assert projected["symbols"] == [
            {"symbol": "\\delta", "meaning": "物質密度揺らぎ"},
            {"symbol": "\\bar\\rho", "meaning": "平均密度"},
        ]

    def test_symbols_without_meaning_are_dropped(self):
        """EH2: 意味が解決できていない記号を推測で埋めない。"""
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({
            "semantics": {
                "equation_type": "relation",
                "defined_symbols": [{"symbol": "k"}, {"symbol": "P", "meaning": "パワースペクトル"}],
            },
        })
        assert projected["symbols"] == [{"symbol": "P", "meaning": "パワースペクトル"}]

    def test_role_derived_from_equation_type_when_missing(self):
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({
            "semantics": {"equation_type": "transformation", "input_equation_ids": ["eq_1"]},
        })
        assert projected["role_in_argument"] == "derived"

    def test_no_semantics_yields_empty_role_not_a_guess(self):
        """EH2: equation_type が無い式（チャンク由来）に既定の「前提」を貼らない。"""
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({"latex": "a=b"})
        assert projected["role_in_argument"] == ""
        assert projected["semantic_kind"] == ""
        assert projected["symbols"] == []

    def test_projection_carries_no_raw_numbers(self):
        """EH4: confidence 等の生数値を投影に持ち込まない。"""
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({
            "semantics": {"equation_type": "result", "confidence": 0.91, "summary": "結果"},
            "confidence": 0.42,
        })
        assert "confidence" not in projected

    def test_content_blocks_carry_explanatory_fields(self):
        from core.course_content_builder import _content_blocks

        blocks = _content_blocks("", [], [], [{
            "equation_id": "eq_2_7",
            "latex": "\\delta = 1",
            "semantics": {
                "equation_type": "definition",
                "summary": "密度揺らぎの定義",
                "defined_symbols": [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}],
            },
        }], [])
        item = next(b for b in blocks if b["type"] == "equations")["items"][0]
        assert item["role_in_argument"] == "definition"
        assert item["semantic_kind"] == "密度揺らぎの定義"
        assert item["symbols"] == [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}]

    def test_evidence_links_carry_role_and_symbols(self):
        from core.course_content_builder import _topic_evidence_links

        links = _topic_evidence_links([], [{
            "equation_id": "eq_2_7",
            "latex": "\\delta = 1",
            "semantics": {
                "equation_type": "definition",
                "summary": "密度揺らぎの定義",
                "defined_symbols": [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}],
            },
        }], {}, {}, "high")
        link = next(link for link in links if link["kind"] == "equation")
        assert link["role_in_argument"] == "definition"
        assert link["symbols"] == [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}]

    def test_tex_like_summary_never_leaks_into_equation_link(self):
        """EH1: semantics.summary が TeX 混じりでも summary には残さない。

        equation 分岐は必ず latex= を渡すため、TeX ガードが「latex 未指定のときだけ」
        だと素通りし、link.summary → ホバー本文に生 TeX が出る（レビュー指摘）。
        """
        from core.course_content_builder import _topic_evidence_links

        links = _topic_evidence_links([], [{
            "equation_id": "eq_2_7",
            "latex": "\\delta = 1",
            "semantics": {
                "equation_type": "definition",
                "summary": "\\begin{aligned} x = y \\end{aligned}",
            },
        }], {}, {}, "high")
        link = next(link for link in links if link["kind"] == "equation")
        assert link["summary"] == ""
        # latex は元の式のまま（TeX 風 summary で上書きしない）。
        assert link["latex"] == "\\delta = 1"
        assert "\\begin{aligned}" not in link["summary"]

    def test_stored_tex_summary_is_dropped_at_read_time(self):
        """EH1/EH2: リンク生成時ガード導入前に freeze された既存コースの防衛。

        既存スナップショットの evidence_link には TeX 混じり summary が保存され得る。
        読み取り時（build_topic_evidence_items）にも落とさないと、コースを再構築する
        まで数式ホバーの「意味の要約」行に生 TeX が出続ける。
        """
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [{
                "kind": "equation",
                "target_id": "eq_tex_b14",
                "summary": "\\begin{aligned} \\delta := \\frac{\\rho-\\bar\\rho}{\\bar\\rho} \\end{aligned}",
                "latex": "\\delta := \\frac{\\rho-\\bar\\rho}{\\bar\\rho}",
            }],
        }
        items = build_topic_evidence_items(topic)
        item = next(i for i in items if i["kind"] == "equation")
        assert item["summary"] == ""
        # latex 自体は本文カードの描画用に保持する（ツールチップは参照しない）。
        assert item["latex"].startswith("\\delta")

    def test_tex_plain_text_is_not_used_as_summary_or_reading(self):
        """EH1/EH2: チャンク由来 formula の plain_text = 原文 TeX を表示に使わない。

        読み上げ原稿を持たない fallback formula は freeze 時に plain_text へ生 TeX が
        入ることがある（実機 2026-08-02 で観測: eq_tex_b14）。branch 3 の
        summary（semantic_kind || plain_text）と「読み:」行の両方から落とし、
        ホバーを IH8 固定文へ縮退させる。latex は本文カード用に温存する。
        """
        from core.course_content_builder import build_topic_evidence_items

        tex = "\\begin{aligned} \\delta(t, {\\bm{x}}) := \\frac{\\rho-\\bar\\rho}{\\bar\\rho} \\end{aligned}"
        topic = {
            "content_blocks": [{
                "type": "equations",
                "items": [{
                    "equation_id": "eq_tex_b14",
                    "label": "",
                    "latex": tex,
                    "plain_text": tex,
                    "raw_text": tex,
                }],
            }],
        }
        items = build_topic_evidence_items(topic)
        item = next(i for i in items if i["kind"] == "equation")
        assert item["title"] == "数式"
        assert item["summary"] == ""
        assert item["plain_text"] == ""
        assert item["latex"] == tex

    def test_evidence_link_without_semantics_omits_empty_keys(self):
        from core.course_content_builder import _topic_evidence_links

        links = _topic_evidence_links([], [{"equation_id": "eq_9", "latex": "a=b"}], {}, {}, "high")
        link = next(link for link in links if link["kind"] == "equation")
        assert "symbols" not in link
        assert link.get("role_in_argument", "") == ""


class TestEquationDisplayTitle:
    """EH2: 裸の内部 ID をタイトルに出さない。"""

    def test_label_wins(self):
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("物質密度揺らぎの定義", "eq_tex_b14") == "物質密度揺らぎの定義"

    def test_paper_equation_number_is_kept(self):
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("", "eq_2_7") == "eq_2_7"

    def test_synthetic_id_falls_back_to_generic_label(self):
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("", "eq_tex_b14") == "数式"
        assert _equation_display_title(None, "") == "数式"

    def test_evidence_item_title_and_fields(self):
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items({
            "content_blocks": [{
                "type": "equations",
                "items": [{
                    "equation_id": "eq_tex_b14",
                    "latex": "\\delta = 1",
                    "role_in_argument": "definition",
                    "semantic_kind": "密度揺らぎの定義",
                    "symbols": [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}],
                }],
            }],
        })
        item = next(i for i in items if i["kind"] == "equation")
        assert item["title"] == "数式"
        assert item["role_in_argument"] == "definition"
        assert item["symbols"] == [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}]

    def test_summary_is_never_the_latex(self):
        """EH1: content_blocks 経路の summary に latex を流し込まない。"""
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items({
            "content_blocks": [{
                "type": "equations",
                "items": [{"equation_id": "eq_9", "latex": "\\frac{a}{b}"}],
            }],
        })
        item = next(i for i in items if i["kind"] == "equation")
        assert item["summary"] == ""
        assert item["latex"] == "\\frac{a}{b}"  # 本文カード用には保持する
