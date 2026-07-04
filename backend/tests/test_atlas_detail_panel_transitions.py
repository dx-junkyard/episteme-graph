"""分野の地図 — 詳細パネル・チャット遷移・学習パス提案カード (Issue C: C-1/C-2/C-3)。

既存テスト (test_tension_api_and_aggregation.py) と同様、
純粋関数 (core.atlas_path) のユニットテスト + SQL/ソースの静的検証で構成する。

受け入れ条件との対応:
1. フィクスチャ全15ノードのパネル表示規則 (霧・行間で learn/evid 非表示を含む)
2. 3つの ↗ アクション: オーバーレイを閉じる → プロンプト送出 → 構造化ペイロード添付
3. 「気になる」が既存 tension 記録 (interest_traces kind='tension') に帰属つきで到達
4. 学習パスカードの各ステップに出所と状態、行間に「先生に聞くポイント」
5. 「今はやめる」が interest_traces に記録される (status='dismissed')
6. パネル・カードのどこにも評価語・推薦理由・誘導文言がない
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.atlas_path import MAX_STEPS, build_learning_path_card  # noqa: E402

LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
PANEL_JS = ROOT / "frontend" / "public" / "js" / "atlas-panel.js"
OVERLAY_JS = ROOT / "frontend" / "public" / "js" / "atlas-overlay.js"
FIXTURE_JS = ROOT / "frontend" / "public" / "js" / "atlas-fixture.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
ATLAS_CSS = ROOT / "frontend" / "public" / "css" / "atlas.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fixture() -> dict:
    """atlas-fixture.js の window.ATLAS_FIXTURE を JSON として読む (キーは全て引用済み)。"""
    src = _read(FIXTURE_JS)
    body = src.split("window.ATLAS_FIXTURE =", 1)[1].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


# 評価語・推薦・誘導の禁止語彙 (§1.2 / 受け入れ条件6)。事実の語彙のみ許す。
_FORBIDDEN_WORDS = ("重要", "要注意", "おすすめ", "オススメ", "推奨", "ぜひ",
                    "学びましょう", "候補地帯", "スコア", "バッジ")


def _assert_no_evaluative_words(text: str, context: str) -> None:
    for word in _FORBIDDEN_WORDS:
        assert word not in text, f"{context} に評価語/誘導文言 '{word}' が含まれる"


# ---------------------------------------------------------------------------
# C-3: 学習パス提案カード (core.atlas_path) のユニットテスト
# ---------------------------------------------------------------------------

def _card(**overrides):
    kwargs = dict(
        node_id="ope",
        node_label="演算子積展開",
        level=2,
        skeleton_version="2026.1",
        node_status="verified",
        node_pill="実験で確認",
        related=[
            {"node_id": "disp", "label": "分散関係", "status": "verified", "pill": "実験で確認"},
            {"node_id": "gy", "label": "高次項の抑制", "status": "gap", "pill": "行間 — AIが補完"},
            {"node_id": "dual", "label": "クォーク・ハドロン双対性", "status": "assumed", "pill": "暗黙の前提"},
        ],
        juxtapose=[],
        course_topics=[],
        interest_traces=[],
    )
    kwargs.update(overrides)
    return build_learning_path_card(**kwargs)


class TestLearningPathCard:
    def test_every_step_has_source_and_status(self):
        """受け入れ条件4: 各ステップに出所 (教材/AI一般知識) と状態が表示される。"""
        card = _card()
        assert card["kind"] == "learning_path_proposal"
        assert len(card["steps"]) == 4  # target + related 3
        for step in card["steps"]:
            assert step["source"] in ("course_material", "model_knowledge")
            assert step["source_label"] in ("教材", "AI一般知識")
            assert step["status_label"]

    def test_gap_step_gets_teacher_question(self):
        """受け入れ条件4: 行間ステップに「先生に聞くポイント」(質問テンプレート) が付く。"""
        card = _card()
        gap = next(s for s in card["steps"] if s["label"] == "高次項の抑制")
        assert gap["is_gap"] is True
        assert gap["source"] == "model_knowledge"  # 行間は AI一般知識由来
        assert "高次項の抑制" in gap["teacher_question"]
        assert "先生" in gap["teacher_question"]

    def test_assumed_shows_ledger_fact_only(self):
        """暗黙の前提は台帳状態をそのまま表示するのみ (評価しない)。"""
        card = _card()
        assumed = next(s for s in card["steps"] if s["status"] == "assumed")
        assert assumed["ledger_note"] == "台帳: 記帳された直接検証なし"
        assert assumed["teacher_question"] == ""

    def test_juxtapose_terminal_with_two_candidates(self):
        """終端は可能なら並置 (2つの構造) + 「自分で繋ぐ」入力。"""
        card = _card(juxtapose=[
            {"node_id": "vcb", "label": "Vcb の測定間の不一致", "status": "contested", "pill": "解釈が分かれる"},
            {"node_id": "now", "label": "B→c 和則", "status": "now", "pill": "いまここ"},
        ])
        assert card["terminal"]["mode"] == "juxtapose"
        assert len(card["terminal"]["pair"]) == 2
        assert card["terminal"]["connect_input"] is True
        # 接続の言語化をしない: pair は構造の事実のみで、繋がり方の説明文を持たない
        for cell in card["terminal"]["pair"]:
            assert "だから" not in json.dumps(cell, ensure_ascii=False)

    def test_single_juxtapose_candidate_pairs_with_target(self):
        card = _card(juxtapose=[
            {"node_id": "vcb", "label": "Vcb の測定間の不一致", "status": "contested", "pill": ""},
        ])
        labels = [c["label"] for c in card["terminal"]["pair"]]
        assert labels == ["演算子積展開", "Vcb の測定間の不一致"]

    def test_no_juxtapose_falls_back_to_single(self):
        card = _card(juxtapose=[])
        assert card["terminal"]["mode"] == "single"
        assert card["terminal"]["pair"] == []
        assert card["terminal"]["connect_input"] is True

    def test_step_cap_is_not_silent(self):
        """上限で省いたステップは notes に明示する (silent cap 禁止)。"""
        many = [{"node_id": f"n{i}", "label": f"概念{i}", "status": "verified", "pill": ""}
                for i in range(10)]
        card = _card(related=many)
        assert len(card["steps"]) == MAX_STEPS
        assert card["notes"] and "省略" in card["notes"][0]
        assert "概念9" in card["notes"][0]

    def test_course_prerequisites_come_first(self):
        """コーストピックの prerequisites (概念グラフ依存の近似) を先頭に置く。"""
        card = _card(course_topics=[
            {"title": "演算子積展開", "prerequisites": ["場の量子論の基礎"]},
        ])
        assert card["steps"][0]["label"] == "場の量子論の基礎"
        assert card["steps"][0]["status_label"] == "コースの前提知識"
        assert card["steps"][1]["label"] == "演算子積展開"

    def test_interest_traces_mark_visited_and_thread(self):
        """interest_traces から既習の痕跡 (事実提示) と「いまの糸」を反映する。"""
        card = _card(interest_traces=[
            {"kind": "question", "status": "open", "text": "分散関係はなぜ厳密なのか",
             "context_label": ""},
        ])
        assert card["current_thread"] == "分散関係はなぜ厳密なのか"
        disp = next(s for s in card["steps"] if s["label"] == "分散関係")
        assert disp["visited_note"] != ""

    def test_fog_steps_are_model_knowledge(self):
        card = _card(related=[
            {"node_id": "fogL", "label": "格子QCD（霧の領域）", "status": "fog", "pill": "論文未投入"},
        ])
        fog = next(s for s in card["steps"] if s["status"] == "fog")
        assert fog["source"] == "model_knowledge"
        assert fog["ledger_note"] == "台帳: 記帳なし"

    def test_card_contains_no_evaluative_words(self):
        """受け入れ条件6: カードのどこにも評価語・推薦理由・誘導文言がない。"""
        card = _card(
            juxtapose=[{"node_id": "vcb", "label": "Vcb の測定間の不一致",
                        "status": "contested", "pill": "解釈が分かれる"}],
            course_topics=[{"title": "演算子積展開", "prerequisites": ["場の量子論の基礎"]}],
            interest_traces=[{"kind": "question", "status": "open", "text": "q", "context_label": ""}],
        )
        _assert_no_evaluative_words(json.dumps(card, ensure_ascii=False), "learning path card")

    def test_decisions_vocabulary(self):
        assert _card()["decisions"] == ["proceed", "edit", "dismiss"]


# ---------------------------------------------------------------------------
# C-2: チャット遷移 — サーバ側 (learning.py / schemas.py) の静的検証
# ---------------------------------------------------------------------------

class TestChatTransitionServer:
    def test_schemas_carry_structured_payload(self):
        source = _read(SCHEMAS)
        assert "atlas_context: dict | None = None" in source
        assert "atlas_path_card: dict | None = None" in source

    def test_mind_reaches_tension_store_with_attribution(self):
        """受け入れ条件3: 「気になる」が既存 tension 記録に帰属つきで到達する。"""
        source = _read(LEARNING)
        block = source.split('if action == "mind":')[1].split('if action == "learn":')[0]
        assert 'kind="tension"' in block
        assert 'status="open"' in block  # 本人発の宣言 — candidate ではない (P1)
        assert '"atlas": attribution' in block  # 帰属 (node_id/level/skeleton_version)

    def test_learn_returns_deterministic_card(self):
        source = _read(LEARNING)
        block = source.split('if action == "learn":')[1].split("return None")[0]
        assert "build_learning_path_card" in block
        assert "atlas_path_card=card" in block
        # リアルタイム LLM 生成をしない (§1.2-6)
        assert "generate_text" not in block

    def test_atlas_actions_skip_intent_classification_and_prereq_gate(self):
        source = _read(LEARNING)
        assert "None if (_is_casual or _atlas_ctx) else (" in source
        assert "None if (_is_casual or _atlas_ctx) else check_prerequisites(" in source

    def test_evid_flow_bakes_attribution_into_trace(self):
        source = _read(LEARNING)
        assert '**({"atlas": _atlas_attribution(_atlas_ctx)} if _atlas_ctx else {})' in source

    def test_path_decision_route_records_dismiss(self):
        """受け入れ条件5: 「今はやめる」(dismiss) が interest_traces に記録される。"""
        source = _read(LEARNING)
        assert '"/courses/{course_id}/atlas/path-decision"' in source
        decisions = source.split("_ATLAS_PATH_DECISIONS = {")[1].split("}")[0]
        assert '"dismiss": ("dismissed"' in decisions  # 却下も記録 (削除しない)
        assert '"proceed": ("resolved"' in decisions
        assert '"connect": ("articulated"' in decisions  # 自分で繋ぐ = 本人の言葉

    def test_deterministic_answers_have_no_evaluative_words(self):
        source = _read(LEARNING)
        block = source.split("def _atlas_action_response")[1].split("def learning_chat")[0]
        answers = re.findall(r'"([^"]*」[^"]*)"', block)
        _assert_no_evaluative_words("".join(answers), "atlas deterministic answers")


# ---------------------------------------------------------------------------
# C-1/C-2: フロントエンド (atlas-panel.js / atlas-overlay.js / app.js) の静的検証
# ---------------------------------------------------------------------------

class TestDetailPanelFrontend:
    def test_panel_subscribes_node_select_event(self):
        source = _read(PANEL_JS)
        assert 'document.addEventListener("atlas:nodeselect"' in source

    def test_panel_renders_verify_endorse_actions(self):
        source = _read(PANEL_JS)
        assert "atlas-panel-verify" in source
        assert "atlas-panel-endorse" in source
        assert "atlas-panel-actions" in source

    def test_learn_evid_hidden_without_flags(self):
        """learn/evid はフラグで表示制御 (霧・行間では非表示)。mind/report は常時。"""
        source = _read(PANEL_JS)
        assert "if (!act.always && !info[act.key]) return;" in source
        actions = source.split("const ACTIONS = [")[1].split("];")[0]
        assert '{ key: "learn", label: "ここから学ぶ ↗", always: false }' in actions
        assert '{ key: "evid", label: "根拠を見る ↗", always: false }' in actions
        assert '{ key: "mind", label: "気になる ↗", always: true }' in actions
        assert '{ key: "report", label: "修正を報告", always: true }' in actions

    def test_all_three_arrow_actions_close_overlay_and_attach_payload(self):
        """受け入れ条件2: ↗ 3種すべてがオーバーレイを閉じ、構造化ペイロード付きで送出する。"""
        source = _read(PANEL_JS)
        send = source.split("function sendToChat")[1].split("function runAction")[0]
        assert "window.AtlasOverlay.close()" in send
        assert "window.sendPrompt(text, { atlas_context: atlasContext })" in send
        run = source.split("function runAction")[1]
        for action in ("learn", "evid", "mind"):
            assert f'actionKey === "{action}"' in run
        # 3アクションとも共通機構 sendToChat を通る (= close + payload 添付)
        assert run.count("sendToChat(") == 3
        # ペイロードの必須キー
        base = run.split("const base = {")[1].split("};")[0]
        for key in ("node_id", "level", "skeleton_version", "action"):
            assert re.search(rf"\b{key}:", base), key

    def test_prompts_match_mock(self):
        source = _read(PANEL_JS)
        assert "」から学習パスを組んで、いまの文脈に繋げて" in source
        assert "」の根拠（原文の該当箇所・式・承認の履歴）を見せて" in source
        assert "」について、うまく言えないが引っかかる。違和感として記録して" in source

    def test_report_button_only_dispatches_hook_event(self):
        """修正報告はボタン設置のみ (フォームと送信は issue D)。"""
        source = _read(PANEL_JS)
        assert 'CustomEvent("atlas:reportrequest"' in source

    def test_overlay_restores_selection_on_reopen(self):
        source = _read(OVERLAY_JS)
        assert "state.reopen" in source
        assert "const restoreLevel = state.reopen ? state.level : 1;" in source
        assert "if (restoreSel) selectNode(restoreSel);" in source

    def test_index_includes_panel_script(self):
        assert '<script src="/js/atlas-panel.js"></script>' in _read(INDEX_HTML)

    def test_panel_and_css_have_no_evaluative_words(self):
        """受け入れ条件6 (スナップショットレビューの自動化部分)。

        コメント (設計原則の記述) は表示されないため除去し、
        ユーザーに見えるコード部分のみを走査する。
        """
        def strip_comments(src: str) -> str:
            src = re.sub(r"/\*[\s\S]*?\*/", "", src)
            return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", src)

        _assert_no_evaluative_words(strip_comments(_read(PANEL_JS)), "atlas-panel.js")
        _assert_no_evaluative_words(strip_comments(_read(ATLAS_CSS)), "atlas.css")


class TestFixturePanelRules:
    def test_all_15_nodes_have_panel_fields(self):
        """受け入れ条件1: フィクスチャ全15ノードで名前・ピル・検証/承認行が揃う。"""
        nodes = _fixture()["nodes"]
        assert len(nodes) == 15
        for node_id, info in nodes.items():
            assert info.get("label"), node_id
            assert info.get("pill"), node_id
            assert info.get("verify"), node_id
            assert info.get("endorse"), node_id
            assert isinstance(info.get("learn"), bool), node_id
            assert isinstance(info.get("evid"), bool), node_id

    def test_fog_and_gap_hide_learn_evid(self):
        """霧 (fog) と行間 (gap) では learn/evid 非表示。"""
        nodes = _fixture()["nodes"]
        for node_id, info in nodes.items():
            if info["status"] in ("fog", "gap"):
                assert info["learn"] is False, node_id
                assert info["evid"] is False, node_id
            else:
                assert info["learn"] is True, node_id
                assert info["evid"] is True, node_id

    def test_fog_boilerplate_states_facts_without_solicitation(self):
        """霧の定型文: 事実の提示のみで、投入の指示・催促をしない。"""
        nodes = _fixture()["nodes"]
        fogs = [n for n in nodes.values() if n["status"] == "fog"]
        assert len(fogs) == 2
        for fog in fogs:
            assert "AIの一般知識による地形" in fog["verify"]
            assert "灯りがつきます" in fog["endorse"]
            for word in ("追加してください", "しましょう", "ぜひ"):
                assert word not in fog["verify"] + fog["endorse"]


class TestPathCardFrontend:
    def test_app_sends_payload_through_send_prompt(self):
        source = _read(APP_JS)
        assert "window.sendPrompt = function (text, payload) {" in source
        assert "sendMessage(text, payload);" in source

    def test_card_render_shows_source_status_and_teacher_question(self):
        source = _read(APP_JS)
        assert "renderAtlasPathCard" in source
        assert "先生に聞くポイント: " in source
        assert "atlas-path-badge" in source  # 状態・出所バッジ

    def test_card_has_three_decisions_and_connect_input(self):
        source = _read(APP_JS)
        assert 'data-decision="proceed"' in source and "この糸で進む" in source
        assert 'data-decision="edit"' in source and "編集する" in source
        assert 'data-decision="dismiss"' in source and "今はやめる" in source
        assert "自分で繋ぐ（自分の言葉で）" in source

    def test_decisions_post_to_path_decision_api(self):
        source = _read(APP_JS)
        assert '"/atlas/path-decision"' in source
        block = source.split("function bindAtlasPathCards")[1].split("function renderNextActions")[0]
        assert 'postAtlasPathDecision(card, decision)' in block
        assert 'postAtlasPathDecision(card, "connect", text)' in block

    def test_juxtapose_renders_without_connective_language(self):
        """並置は2カランの単純並置 + 「自分で繋ぐ」。接続の言語化をしない。"""
        source = _read(APP_JS)
        block = source.split("function renderAtlasPathCard")[1].split("function postAtlasPathDecision")[0]
        assert "atlas-path-juxta" in block
        assert "接続の言語化はしない" in block  # 設計コメントの明示
