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
        # 生 LaTeX をタイトルに出さない。見出しはラベルラダー（labels.equation_label）
        # へ委譲済みなので、論文の式番号形の id は「式 (1)」に整形される
        # （element_context_presentation_redesign.md §5.1 / §8 Phase 2）。
        assert eq["title"] == "式 (1)"
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


class TestEquationSnapshotPhase2:
    """掲載節 / 前段リンク状態 / 成立条件のスナップショット投影。

    設計正本: docs/features/element_context_presentation_redesign.md §5.1・§6 S1・
    §8 Phase 2（CP4 訳語を焼かない / CP10 空は沈黙ではない / 事実でないものは出さない）。
    """

    _EQUATION = {
        "equation_id": "eq_tex_b14",
        "latex": "\\delta = 1",
        "source_extraction": {"source_location": {"section_id": "sec_2_1", "block_id": "b14"}},
        "semantics": {
            "equation_type": "definition",
            "summary": "密度揺らぎの定義",
            "link_status": "axiomatic",
            "assumptions": ["背景が一様等方であること", "揺らぎが線形領域にあること", "第3の条件"],
            "defined_symbols": [
                {"symbol": "δ(t,x)", "meaning": "密度コントラスト", "definition_status": "defined"},
            ],
        },
    }
    _STRUCTURE = {
        "document_structure": {
            "sections": [{"section_id": "sec_2_1", "title": "2.1 Density contrast", "level": 2}],
        },
    }

    def _collected_equation(self):
        from core.course_content_builder import _collect_structured_content

        bundle = _collect_structured_content({
            "doc-1": {
                **self._STRUCTURE,
                "equation_semantics": {"equations": [self._EQUATION]},
            },
        })
        return bundle["equations"]["eq_tex_b14"]

    def test_section_label_is_resolved_from_document_structure(self):
        assert self._collected_equation()["section_label"] == "2.1 Density contrast"

    def test_section_label_is_omitted_when_the_heading_cannot_be_resolved(self):
        """節見出しが引けない section_id は**生値を出さず**キーごと省く。"""
        from core.course_content_builder import _collect_structured_content, _equation_semantic_projection

        bundle = _collect_structured_content({
            "doc-1": {"equation_semantics": {"equations": [self._EQUATION]}},  # structure なし
        })
        equation = bundle["equations"]["eq_tex_b14"]
        assert "section_label" not in equation
        projected = _equation_semantic_projection(equation)
        assert "section_label" not in projected
        # 内部 ID（section_id）はどのフィールドにも出さない。
        assert "sec_2_1" not in repr(projected)

    def test_projection_carries_section_link_status_and_assumptions(self):
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection(self._collected_equation())
        assert projected["section_label"] == "2.1 Density contrast"
        # 訳語ではなく統制語彙キーを載せる（CP4: 訳語の正本は element_vocab）。
        assert projected["link_status"] == "axiomatic"
        assert projected["assumptions"] == ["背景が一様等方であること", "揺らぎが線形領域にあること"]
        # 定義される記号には defined_here が立つ（ラベルラダー③の材料）。
        assert projected["symbols"][0]["defined_here"] is True

    def test_unknown_link_status_is_dropped_fail_closed(self):
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({"semantics": {"link_status": "made_up"}})
        assert "link_status" not in projected

    def test_tex_assumptions_are_not_projected(self):
        """EH1: 成立条件が生 TeX なら表示材料にしない（数式の再掲を作らない）。"""
        from core.course_content_builder import _equation_semantic_projection

        projected = _equation_semantic_projection({
            "semantics": {"assumptions": [r"\begin{aligned} x &= y \end{aligned}"]},
        })
        assert "assumptions" not in projected

    def test_content_blocks_and_links_carry_the_new_fields(self):
        from core.course_content_builder import _content_blocks, _topic_evidence_links

        equation = self._collected_equation()
        item = next(
            b for b in _content_blocks("", [], [], [equation], []) if b["type"] == "equations"
        )["items"][0]
        assert item["section_label"] == "2.1 Density contrast"
        assert item["link_status"] == "axiomatic"
        assert len(item["assumptions"]) == 2

        link = next(
            link for link in _topic_evidence_links([], [equation], {}, {}, "high")
            if link["kind"] == "equation"
        )
        assert link["section_label"] == "2.1 Density contrast"
        assert link["link_status"] == "axiomatic"
        assert len(link["assumptions"]) == 2

    def test_evidence_items_expose_the_new_fields_on_both_read_paths(self):
        from core.course_content_builder import (
            _content_blocks,
            _topic_evidence_links,
            build_topic_evidence_items,
        )

        equation = self._collected_equation()
        topic = {
            "evidence_links": _topic_evidence_links([], [equation], {}, {}, "high"),
            "content_blocks": _content_blocks("", [], [], [equation], []),
        }
        item = next(i for i in build_topic_evidence_items(topic) if i["kind"] == "equation")
        assert item["section_label"] == "2.1 Density contrast"
        assert item["link_status"] == "axiomatic"
        assert item["assumptions"][0] == "背景が一様等方であること"
        # 見出しは記号 + 役割の決定論合成（EH1: 生 TeX を出さない）。
        assert item["title"] == "δ(t,x) を定義する式"

        # content_blocks だけ（evidence_links の無いトピック）でも同じ材料が届く。
        only_blocks = {"content_blocks": topic["content_blocks"]}
        block_item = next(
            i for i in build_topic_evidence_items(only_blocks) if i["kind"] == "equation"
        )
        assert block_item["section_label"] == "2.1 Density contrast"
        assert block_item["link_status"] == "axiomatic"
        assert block_item["assumptions"][0] == "背景が一様等方であること"

    def test_no_raw_tex_lands_in_any_evidence_item_field(self):
        """回帰: 生 TeX が title / summary / section_label / assumptions に出ない。"""
        from core.course_content_builder import (
            _collect_structured_content,
            _content_blocks,
            _topic_evidence_links,
            build_topic_evidence_items,
            looks_like_tex_math,
        )

        tex = r"\begin{aligned} \delta(t,{\bm{x}}) := \frac{\rho-\bar\rho}{\bar\rho} \end{aligned}"
        bundle = _collect_structured_content({
            "doc-1": {
                "equation_semantics": {"equations": [{
                    "equation_id": "eq_tex_b14",
                    "label": tex,
                    "source_extraction": {
                        "raw_text": tex, "latex": tex, "plain_text": tex,
                        "source_location": {"block_id": "b14"},
                    },
                    "semantics": {
                        "equation_type": "definition",
                        "summary": tex,
                        "assumptions": [tex],
                        "link_status": "axiomatic",
                    },
                }]},
            },
        })
        equation = bundle["equations"]["eq_tex_b14"]
        topic = {
            "evidence_links": _topic_evidence_links([], [equation], {}, {}, "high"),
            "content_blocks": _content_blocks("", [], [], [equation], []),
        }
        item = next(i for i in build_topic_evidence_items(topic) if i["kind"] == "equation")
        for key in ("title", "summary", "plain_text", "section_label", "semantic_kind"):
            assert not looks_like_tex_math(item.get(key) or ""), key
            assert "\\begin{aligned}" not in (item.get(key) or ""), key
        assert item["assumptions"] == []
        assert item["title"] == "定義式"  # 役割訳へ縮退（生 TeX も内部 ID も出さない）
        # 本文カード用の latex / raw_text は温存する。
        assert item["latex"] == tex


class TestEquationDisplayTitle:
    """EH2: 裸の内部 ID をタイトルに出さない。

    element_context_presentation_redesign.md §8 Phase 2 以降、見出しの生成規則は
    ``core/deliberation/labels.py`` のラベルラダーが正本で、本関数はその委譲。
    """

    def test_label_wins(self):
        """ラダーが尽きたときは、人間可読な明示ラベルを「数式」に潰さない（P4）。"""
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("物質密度揺らぎの定義", "eq_tex_b14") == "物質密度揺らぎの定義"

    def test_paper_equation_number_is_kept(self):
        """式番号は残す（表記はラダー正本の ``式 (2.7)`` 形に整う）。"""
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("", "eq_2_7") == "式 (2.7)"

    def test_synthetic_id_falls_back_to_generic_label(self):
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("", "eq_tex_b14") == "数式"
        assert _equation_display_title(None, "") == "数式"

    def test_internal_id_label_is_not_used_as_a_title(self):
        """明示ラベルが内部 ID 形・生 TeX のときは一般ラベルへ落とす（EH1/EH2）。"""
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title("eq_tex_b16", "eq_tex_b14") == "数式"
        assert _equation_display_title("Define eq_tex_b16", "eq_tex_b14") == "数式"
        assert _equation_display_title(
            "\\begin{aligned} x &= y \\end{aligned}", "eq_tex_b14"
        ) == "数式"

    def test_headline_is_composed_from_symbol_and_role(self):
        """ラダー③: 記号 + 役割の決定論合成（翻訳なしで日本語の見出しになる）。"""
        from core.course_content_builder import _equation_display_title

        record = {
            "role_in_argument": "definition",
            "symbols": [{"symbol": "δ(t,x)", "meaning": "密度コントラスト", "defined_here": True}],
        }
        assert _equation_display_title("", "eq_tex_b14", record=record) == "δ(t,x) を定義する式"

    def test_headline_falls_back_to_semantic_kind_then_role(self):
        """ラダー④→⑤: 意味の一行 → 役割訳 + 「式」。"""
        from core.course_content_builder import _equation_display_title

        assert _equation_display_title(
            "", "eq_tex_b14", record={"semantic_kind": "密度揺らぎの定義。続きの文。"}
        ) == "密度揺らぎの定義。"
        assert _equation_display_title(
            "", "eq_tex_b14", record={"role_in_argument": "constraint"}
        ) == "制約式"

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
        # 合成 ID は出さないが「数式」で潰しもしない（意味の一行が見出しになる）。
        assert item["title"] == "密度揺らぎの定義"
        assert item["role_in_argument"] == "definition"
        assert item["symbols"] == [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}]

    def test_evidence_item_title_synthesizes_symbol_and_role(self):
        """スナップショットの平坦フィールド（役割 + defined_here 記号）からも合成できる。"""
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items({
            "evidence_links": [{
                "kind": "equation",
                "target_id": "eq_tex_b14",
                "latex": "\\delta = 1",
                "role_in_argument": "definition",
                "symbols": [
                    {"symbol": "δ(t,x)", "meaning": "密度コントラスト", "defined_here": True},
                ],
            }],
        })
        item = next(i for i in items if i["kind"] == "equation")
        assert item["title"] == "δ(t,x) を定義する式"

    def test_evidence_item_title_never_repeats_the_equation(self):
        """EH1: 説明材料が無い式の見出しに latex / raw_text を使わない。"""
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items({
            "evidence_links": [{
                "kind": "equation",
                "target_id": "eq_tex_b14",
                "latex": "\\frac{\\rho-\\bar\\rho}{\\bar\\rho}",
            }],
        })
        item = next(i for i in items if i["kind"] == "equation")
        assert item["title"] == "数式"

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


# ---------------------------------------------------------------------------
# Phase 3: 承認済み contextual 説明の見出し結線（snapshot 側）
#
# 設計正本: docs/features/element_context_presentation_redesign.md §8 Phase 3 /
# docs/features/security_and_context_phase3_implementation_directive.md §5.4・§5.5。
# ---------------------------------------------------------------------------


_APPROVED_BODY = (
    "この式は物質密度のゆらぎを空間平均に対する比として定義する。"
    "以降の摂動論はこの量を出発点にする。"
)
_APPROVED_FIRST_SENTENCE = "この式は物質密度のゆらぎを空間平均に対する比として定義する。"

_PHASE3_EQUATION = {
    "equation_id": "eq_tex_b14",
    "latex": "\\delta = 1",
    "semantics": {
        "equation_type": "definition",
        "role_in_argument": "definition",
        "summary": "密度揺らぎの定義",
    },
}


def _phase3_bundle(explanations=None, *, document_id="doc-1", equation=None):
    from core.course_content_builder import _collect_structured_content

    return _collect_structured_content(
        {document_id: {"equation_semantics": {"equations": [equation or _PHASE3_EQUATION]}}},
        explanations,
    )


class TestApprovedExplanationCollection:
    def test_body_is_attached_for_the_matching_document_and_equation(self):
        from core.course_content_builder import _APPROVED_EXPLANATION_KEY

        bundle = _phase3_bundle({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        assert bundle["equations"]["eq_tex_b14"][_APPROVED_EXPLANATION_KEY] == _APPROVED_BODY

    def test_explanation_of_another_document_is_not_borrowed(self):
        """同じ equation ID が別論文にもあり得るため、索引キーは (document_id, equation_id)。"""
        from core.course_content_builder import (
            _APPROVED_EXPLANATION_KEY,
            _equation_semantic_projection,
        )

        bundle = _phase3_bundle({("doc-OTHER", "eq_tex_b14"): _APPROVED_BODY})
        equation = bundle["equations"]["eq_tex_b14"]
        assert _APPROVED_EXPLANATION_KEY not in equation
        assert "headline" not in _equation_semantic_projection(equation)

    def test_no_explanations_argument_keeps_the_previous_behaviour(self):
        from core.course_content_builder import _APPROVED_EXPLANATION_KEY

        bundle = _phase3_bundle(None)
        assert _APPROVED_EXPLANATION_KEY not in bundle["equations"]["eq_tex_b14"]


class TestApprovedExplanationHeadlineProjection:
    def _projection(self, explanations=None, equation=None):
        from core.course_content_builder import _equation_semantic_projection

        bundle = _phase3_bundle(explanations, equation=equation)
        return _equation_semantic_projection(bundle["equations"]["eq_tex_b14"])

    def test_headline_is_the_first_sentence_only(self):
        projected = self._projection({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        assert projected["headline"] == _APPROVED_FIRST_SENTENCE
        assert "以降の摂動論" not in projected["headline"]

    def test_headline_key_is_absent_without_an_approved_explanation(self):
        assert "headline" not in self._projection(None)

    def test_tex_only_explanation_is_rejected(self):
        projected = self._projection(
            {("doc-1", "eq_tex_b14"): "\\delta \\equiv \\frac{\\rho-\\bar{\\rho}}{\\bar{\\rho}}"}
        )
        assert "headline" not in projected

    def test_internal_id_only_explanation_is_rejected(self):
        projected = self._projection({("doc-1", "eq_tex_b14"): "Derive result eq_tex_b16"})
        assert "headline" not in projected

    def test_semantic_kind_is_not_replaced_by_the_headline(self):
        """CP1: 見出しと意味の一行を同じ文字列にしない。"""
        projected = self._projection({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        assert projected["semantic_kind"] == "密度揺らぎの定義"
        assert projected["semantic_kind"] != projected["headline"]

    def test_snapshot_carries_no_review_metadata(self):
        """§5.4-5: reviewer / status / 説明本文そのものはスナップショットへ焼かない。"""
        from core.course_content_builder import _content_blocks, _topic_evidence_links

        bundle = _phase3_bundle({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        equation = bundle["equations"]["eq_tex_b14"]
        item = next(
            b for b in _content_blocks("", [], [], [equation], []) if b["type"] == "equations"
        )["items"][0]
        link = next(
            link for link in _topic_evidence_links([], [equation], {}, {}, "high")
            if link["kind"] == "equation"
        )
        for payload in (item, link):
            assert payload["headline"] == _APPROVED_FIRST_SENTENCE
            assert "reviewed_by" not in payload
            assert "review_status" not in payload
            assert "label_source" not in payload
            assert "_approved_contextual_explanation" not in payload
            # 説明**全文**は保存しない（第1文の見出しだけ）。
            assert _APPROVED_BODY not in repr(payload)


class TestApprovedExplanationEvidenceItemTitle:
    def _topic(self, explanations):
        from core.course_content_builder import _content_blocks, _topic_evidence_links

        bundle = _phase3_bundle(explanations)
        equations = [bundle["equations"]["eq_tex_b14"]]
        return {
            "content_blocks": _content_blocks("", [], [], equations, []),
            "evidence_links": _topic_evidence_links([], equations, {}, {}, "high"),
            "content_confidence": "high",
        }

    def _equation_item(self, explanations):
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items(self._topic(explanations))
        return next(i for i in items if i["kind"] == "equation")

    def test_title_uses_the_approved_headline(self):
        item = self._equation_item({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        assert item["title"] == _APPROVED_FIRST_SENTENCE

    def test_title_falls_back_to_the_existing_ladder_without_an_explanation(self):
        assert self._equation_item(None)["title"] == "密度揺らぎの定義"

    def test_content_block_route_also_uses_the_headline(self):
        """evidence_links を持たない（content_blocks だけの）トピックでも同じ見出し。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = self._topic({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        topic.pop("evidence_links")
        item = next(
            i for i in build_topic_evidence_items(topic) if i["kind"] == "equation"
        )
        assert item["title"] == _APPROVED_FIRST_SENTENCE

    def test_title_never_shows_tex_or_internal_ids(self):
        item = self._equation_item({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        assert "\\" not in item["title"]
        assert "eq_tex" not in item["title"]

    def test_learner_item_does_not_leak_provenance(self):
        item = self._equation_item({("doc-1", "eq_tex_b14"): _APPROVED_BODY})
        for forbidden in ("label_source", "reviewed_by", "review_status", "explanation_status"):
            assert forbidden not in item
        assert _APPROVED_BODY not in repr(item)

    def test_a_stored_headline_that_is_tex_or_an_internal_id_is_ignored(self):
        """旧データ・手編集への読み取り時の防衛（保存値をそのまま信じない）。"""
        from core.course_content_builder import build_topic_evidence_items

        items = build_topic_evidence_items({
            "evidence_links": [{
                "kind": "equation",
                "target_id": "eq_tex_b14",
                "headline": "\\begin{aligned} x &= y \\end{aligned}",
                "semantic_kind": "密度揺らぎの定義",
            }],
        })
        assert next(i for i in items if i["kind"] == "equation")["title"] == "密度揺らぎの定義"


class TestApprovedExplanationBulkLoad:
    def test_document_ids_are_passed_to_the_store_helper_in_one_call(self):
        from unittest.mock import MagicMock

        from core.course_content_builder import _load_approved_equation_explanations

        session = MagicMock()
        with patch(
            "core.element_explanations.approved_contextual_bodies",
            return_value={("doc-1", "eq_tex_b14"): _APPROVED_BODY},
        ) as bulk:
            result = _load_approved_equation_explanations(session, ["doc-1", "doc-2"])
        assert result == {("doc-1", "eq_tex_b14"): _APPROVED_BODY}
        bulk.assert_called_once()
        assert bulk.call_args.args[1] == ["doc-1", "doc-2"]
        assert bulk.call_args.kwargs["element_type"] == "equation"

    def test_lookup_failure_degrades_to_empty_and_rolls_back(self):
        from unittest.mock import MagicMock

        from core.course_content_builder import _load_approved_equation_explanations

        session = MagicMock()
        with patch(
            "core.element_explanations.approved_contextual_bodies",
            side_effect=RuntimeError("db down"),
        ):
            assert _load_approved_equation_explanations(session, ["doc-1"]) == {}
        session.rollback.assert_called_once()

    def test_snapshot_still_builds_when_the_lookup_failed(self):
        from core.course_content_builder import _content_blocks

        equations = [_phase3_bundle({})["equations"]["eq_tex_b14"]]
        block = next(
            b for b in _content_blocks("", [], [], equations, []) if b["type"] == "equations"
        )
        assert block["items"][0]["equation_id"] == "eq_tex_b14"
        assert "headline" not in block["items"][0]


class TestApprovedExplanationEndToEndCourseBuild:
    """``build_course_content`` が (document_id, equation_id) で説明を解決し、
    保存されるスナップショットの見出しに反映すること（§5.4）。"""

    def _run(self, explanation_rows):
        from unittest.mock import MagicMock

        from core.course_content_builder import build_course_content

        course_data = {
            "sources": [{"material_id": "m1"}],
            "topics": [{"id": "t1", "title": "Topic 1"}],
        }
        saved: dict = {}

        def _execute(query, params=None):
            sql = " ".join(str(query).split())
            result = MagicMock()
            if "SELECT data, user_id" in sql:
                result.fetchone.return_value = (course_data, "user-1")
            elif "FROM element_explanations" in sql:
                result.fetchall.return_value = list(explanation_rows)
            elif "FROM chunks" in sql and "DISTINCT" in sql:
                result.fetchall.return_value = [("doc-1",)]
            elif "chunk_index ASC" in sql:
                result.fetchall.return_value = []
            elif "FROM document_figures" in sql:
                result.fetchall.return_value = []
            elif "title = :title" in sql:
                saved["data"] = params.get("data")
            return result

        session = MagicMock()
        session.execute.side_effect = _execute

        artifacts = {
            "doc-1": {
                "stage_outputs": {
                    "_artifacts": {
                        "course_mapping": {
                            "topics": [
                                {
                                    "title": "Topic 1",
                                    "description": "desc",
                                    "linked_component_ids": ["comp_1"],
                                }
                            ]
                        },
                        "component_assembly": {
                            "components": [
                                {
                                    "component_id": "comp_1",
                                    "label": "密度コントラスト",
                                    "linked_equation_ids": ["eq_tex_b14"],
                                }
                            ]
                        },
                        "equation_semantics": {"equations": [_PHASE3_EQUATION]},
                    },
                },
            },
        }

        with patch("core.course_content_builder._pg_session", return_value=session), \
             patch(
                 "core.document_pipeline.persistence.resolve_artifact_runs",
                 return_value=artifacts,
             ), \
             patch("core.course_content_builder.generate_text_with_structured_output") as gen:
            gen.return_value = {
                "key_concepts": ["k"],
                "student_material": {"source_format": "eg-markdown-v1", "source_text": "本文"},
                "spoken_script": "script",
                "cautions": [],
                "check_questions": [],
            }
            result = build_course_content("user-1", "course-1")

        assert result["status"] == "completed"
        return course_data

    def _equation_item(self, course):
        from core.course_content_builder import build_topic_evidence_items

        topic = course["topics"][0]
        return next(
            i for i in build_topic_evidence_items(topic) if i["kind"] == "equation"
        )

    def test_snapshot_title_reflects_the_approved_explanation(self):
        course = self._run([("doc-1", "eq_tex_b14", _APPROVED_BODY)])
        assert self._equation_item(course)["title"] == _APPROVED_FIRST_SENTENCE

    def test_explanation_of_another_document_does_not_reach_this_snapshot(self):
        course = self._run([("doc-OTHER", "eq_tex_b14", _APPROVED_BODY)])
        assert self._equation_item(course)["title"] == "密度揺らぎの定義"

    def test_build_succeeds_with_no_approved_explanations(self):
        course = self._run([])
        assert self._equation_item(course)["title"] == "密度揺らぎの定義"
