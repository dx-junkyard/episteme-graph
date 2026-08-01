"""学習者側への統一パーツカード適用（編集不可バリアント）の静的ガードレール。

正本: ``docs/architecture/admin_ux_issues_2026-08-01.md`` §3（方針 P1〜P5 /
§3.2「2バリアントの差分（P4）」/ §3.3 Phase 4）。

守るもの:

1. 学習画面（``app.js``）の要素表示は **``window.ElementCard`` に委譲**する
   （claim / equation の文脈パネル・component チップ展開の3経路）。
   画面ごとの独自パーツ描画を新規に作らない（P2）。
2. 学習者側は必ず ``VARIANT_READONLY``（``variant: "editable"`` を渡さない）。
3. 学習者に出してはならないものを **アダプタの時点で dto に詰めない**
   （カード側の二重ガードに頼らない）: confidence / role・対応付け来歴 /
   ``status='candidate'`` の関係 / ``review_status`` / notes / 内部 ID 列。
4. 「深く検討」等の教員向け操作をカードに渡さない（``onDeliberate`` /
   ``extraActions`` / ``reviewNotes`` を使わない）。
5. 近傍チップの「旅」は**学習者が実際に開ける型だけ**（fail-closed）。
6. 表示劣化の禁止: 数式は既存レンダラを ``opts.renderMath`` で注入し、
   component の rich 投影（supports / 出典論文 / 教員の説明 / 共通部品タブ）と
   教材内ジャンプを落とさない。

``element-card.js`` 自体の契約は ``test_element_card_ui_static.py`` が、
claim / equation 文脈 API の導線は ``test_learner_element_context_ui_static.py`` が
守る。ここは「学習画面がカードをどう使うか」だけを見る。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _block(src: str, start_marker: str, end_marker: str = "\n  }") -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# 学習者カード関連のアダプタ・配線関数（すべて app.js のトップレベル関数）。
_ADAPTERS = (
    "function elementContextToCardDto(elementType, data)",
    "function componentContextToCardDto(data)",
    "function learnerElementCardItems(items)",
)

_WIRING = _ADAPTERS + (
    "function learnerElementCardOpts(onCenter)",
    "function learnerElementCardHtml(dto, opts)",
    "function bindLearnerElementCard(container, dto, opts)",
    "function travelToElementContext(pop, body, item)",
    "function augmentLearnerElementCardJumps(container, dto)",
)


# ---------------------------------------------------------------------------
# 1. カードへの委譲（P2）
# ---------------------------------------------------------------------------


class TestDelegatesToSharedCard:
    def test_index_html_loads_the_card_before_app(self):
        src = _read(INDEX_HTML)
        assert "/js/element-vocab.js" in src
        assert "/js/element-card.js" in src
        assert src.index("/js/element-vocab.js") < src.index("/js/element-card.js") < src.index("/js/app.js")

    def test_app_uses_window_element_card(self):
        src = _read(APP_JS)
        assert "window.ElementCard" in src
        for fn in ("function learnerElementCardHtml(dto, opts)", "function bindLearnerElementCard(container, dto, opts)"):
            block = _block(src, fn, "\n  }\n")
            assert "window.ElementCard" in block

    def test_claim_equation_panel_renders_through_the_card(self):
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "elementContextToCardDto(elementType, data)" in block
        assert "learnerElementCardHtml(dto, opts)" in block
        assert "bindLearnerElementCard(body, dto, opts)" in block

    def test_component_chip_expansion_renders_through_the_card(self):
        src = _read(APP_JS)
        block = _block(src, "function renderComponentContextPanel(pop, body, data, resolver)", "\n  }\n")
        assert "componentContextToCardDto(data)" in block
        assert "learnerElementCardOpts(" in block
        assert "componentContextCardOrFallbackHtml(cardDto, cardOpts)" in block
        assert "bindLearnerElementCard(" in block

    def test_no_hand_rolled_lane_renderer_remains(self):
        """レーン・裏付けバッジをカードの外で再実装しない（P2）。"""
        src = _read(APP_JS)
        for gone in (
            "function renderElementContextLane(",
            "function elementContextStatusBadge(",
            "var MATERIAL_ELEMENT_CONTEXT_LANE_LABELS",
            "var MATERIAL_ELEMENT_CONTEXT_STATUS_LABELS",
        ):
            assert gone not in src, f"{gone} がまだ残っている（カードへ寄せる）"


# ---------------------------------------------------------------------------
# 2. readonly バリアント固定（P4）
# ---------------------------------------------------------------------------


class TestReadonlyVariant:
    def test_opts_pin_the_readonly_variant(self):
        src = _read(APP_JS)
        block = _block(src, "function learnerElementCardOpts(onCenter)", "\n  }\n")
        assert "card.VARIANT_READONLY" in block
        # 未読み込み時のフォールバックも readonly 側（fail-closed）。
        assert '"readonly"' in block

    def test_learner_never_requests_the_editable_variant(self):
        src = _read(APP_JS)
        assert "VARIANT_EDITABLE" not in src
        assert '"editable"' not in src

    def test_no_teacher_only_card_options_are_passed(self):
        """操作行・要確認事項・出所バッジ（教員向け）をカードへ渡さない。"""
        src = _read(APP_JS)
        for forbidden in ("onDeliberate", "extraActions", "reviewNotes", "deliberateAnchor"):
            assert forbidden not in src, f"学習画面が {forbidden} をカードへ渡している"
        assert "深く検討" not in src


# ---------------------------------------------------------------------------
# 3. アダプタ段階での非表示（二重ガード）
# ---------------------------------------------------------------------------


class TestAdapterDoesNotCarryHiddenFields:
    def test_no_confidence_or_score_in_adapters(self):
        src = _read(APP_JS)
        for fn in _WIRING:
            block = _block(src, fn, "\n  }\n")
            assert "confidence" not in block, f"{fn} が confidence を詰めている"
            assert "score" not in block, f"{fn} が score を詰めている"

    def test_no_role_provenance_or_review_status_in_adapters(self):
        """role（対応付け来歴）・provenance（内部 ID 列）・review_status は学習者に出さない。
        （``contextual_role`` は「この文脈での役割」で別物なので対象外。）"""
        src = _read(APP_JS)
        for fn in _ADAPTERS:
            block = _block(src, fn, "\n  }\n")
            assert "review_status" not in block, f"{fn} が review_status を詰めている"
            assert "provenance" not in block, f"{fn} が provenance を詰めている"
            assert "evidence_refs" not in block, f"{fn} が evidence_refs を詰めている"

    def test_candidate_relations_are_dropped_by_the_adapter(self):
        src = _read(APP_JS)
        block = _block(src, "function learnerElementCardItems(items)", "\n  }\n")
        assert 'item.relation_status === "candidate"' in block
        assert "return;" in block

    def test_notes_are_not_passed_to_the_card(self):
        """カードは dto.notes をそのまま描くため、渡さないことが唯一のガード。"""
        src = _read(APP_JS)
        for fn in _ADAPTERS:
            block = _block(src, fn, "\n  }\n")
            assert "notes" not in block, f"{fn} が notes を詰めている"

    def test_focus_element_type_uses_the_vocab_keys(self):
        """種別チップは ElementVocab の element_type キーで引かれるため、公開語彙
        （claim）ではなく内部語彙（theory_claim）へ寄せる（生キー表示の防止）。"""
        src = _read(APP_JS)
        block = _block(src, "var LEARNER_CARD_FOCUS_TYPES = {", "};")
        assert 'claim: "theory_claim"' in block
        assert 'equation: "equation"' in block
        component = _block(src, "function componentContextToCardDto(data)", "\n  }\n")
        assert 'element_type: "theory_component"' in component


# ---------------------------------------------------------------------------
# 4. 旅（P3）は開ける型だけ
# ---------------------------------------------------------------------------


class TestTravelIsFailClosed:
    def test_travel_types_are_whitelisted(self):
        src = _read(APP_JS)
        block = _block(src, "var LEARNER_CARD_TRAVEL_TYPES = [", "];")
        for allowed in ("theory_claim", "theory_component", "equation"):
            assert allowed in block
        for denied in ("figure", "section", "thesis", "derivation", "symbol", "evidence", "stage", "part"):
            assert f'"{denied}"' not in block, f"{denied} には学習者向けの文脈取得口が無い"

    def test_navigable_is_recomputed_from_the_whitelist(self):
        src = _read(APP_JS)
        block = _block(src, "function learnerElementCardItems(items)", "\n  }\n")
        assert "LEARNER_CARD_TRAVEL_TYPES.indexOf(item.element_type) >= 0" in block

    def test_travel_dispatches_to_the_matching_context_api(self):
        src = _read(APP_JS)
        block = _block(src, "function travelToElementContext(pop, body, item)", "\n  }\n")
        assert "LEARNER_CARD_TRAVEL_TYPES.indexOf(type) < 0) return;" in block
        assert "fetchComponentContextAndRender(pop, body, item.id, null)" in block
        assert "fetchElementContextAndRender(" in block
        # 再フェッチ不能な型では何もしない（fail-soft）。
        assert "return;" in block

    def test_travel_wired_as_on_center_in_both_panels(self):
        src = _read(APP_JS)
        for fn in (
            "function renderElementContextPanel(pop, body, elementType, data)",
            "function renderComponentContextPanel(pop, body, data, resolver)",
        ):
            block = _block(src, fn, "\n  }\n")
            assert "travelToElementContext(pop, body, item)" in block, fn


# ---------------------------------------------------------------------------
# 5. 表示劣化の禁止
# ---------------------------------------------------------------------------


class TestNoDisplayRegression:
    def test_math_renderer_is_injected(self):
        """数式レンダラは注入されたまま。ただし TeX と判定できる文字列だけを通す
        （EC2, equation_context_panel_display_design.md — KaTeX の throwOnError:false は
        平文・壊れた TeX を赤いエラーとして描いてしまうため）。"""
        src = _read(APP_JS)
        block = _block(src, "function learnerElementCardOpts(onCenter)", "\n  }\n")
        assert "renderMaterialKatex(expr, display)" in block
        assert "looksLikeRenderableTex(expr)" in block
        assert "escapeHtml: escHtml" in block

    def test_component_rich_projection_is_kept_next_to_the_card(self):
        """supports の内訳・出典論文・教員の説明・共通部品タブを落とさない。"""
        src = _read(APP_JS)
        details = _block(src, "function renderContextInstanceDetails(instance, resolver)", "\n  }\n")
        for kept in (
            "renderSupportsSummaryLine(supports)",
            'renderSupportsTextSection("前提", supports.preconditions)',
            "renderContextEquationSection(supports.equations, resolver)",
            "renderContextDependencySection(supports.dependencies)",
            "renderContextClaimSection(supports.claims)",
            "出典論文: ",
            "教員の説明",
            "endorsement_label",
        ):
            assert kept in details, f"component の既存表示 {kept} が失われている"
        panel = _block(src, "function renderComponentContextPanel(pop, body, data, resolver)", "\n  }\n")
        assert "この論文では" in panel
        assert "共通部品として" in panel
        assert "グラフで見る" in panel

    def test_component_card_body_keeps_the_existing_priority(self):
        """位置づけ本文は narrative_role → teaching_takeaway → summary の既存優先順。"""
        src = _read(APP_JS)
        block = _block(src, "function componentContextToCardDto(data)", "\n  }\n")
        assert "inPaper.narrative_role || component.teaching_takeaway ||" in block
        assert "component.summary" in block

    def test_in_material_jump_is_preserved(self):
        src = _read(APP_JS)
        aug = _block(src, "function augmentLearnerElementCardJumps(container, dto)", "\n  }\n")
        assert "materialElementContextJumpRef(items[i])" in aug
        row = _block(src, "function buildMaterialJumpRow(ref)", "\n  }\n")
        assert "教材内で見る" in row
        assert "jumpToMaterialEvidenceRef(ref)" in row
        # bind の一部として必ず走る（呼び忘れの防止）。
        bind = _block(src, "function bindLearnerElementCard(container, dto, opts)", "\n  }\n")
        assert "augmentLearnerElementCardJumps(container, dto)" in bind

    def test_card_absence_degrades_to_a_factual_sentence(self):
        """element-card.js が読み込めていない場合も画面を壊さない（fail-soft）。"""
        src = _read(APP_JS)
        panel = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "if (!html)" in panel
        assert "MATERIAL_ELEMENT_CONTEXT_UNAVAILABLE" in panel
        fallback = _block(src, "function componentContextCardOrFallbackHtml(cardDto, cardOpts)", "\n  }\n")
        assert "if (html) return html;" in fallback
        assert "intrinsic_summary" in fallback
