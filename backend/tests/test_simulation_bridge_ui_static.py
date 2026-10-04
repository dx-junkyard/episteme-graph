"""検証シミュレーション層（SB層）の管理 UI の静的ガードレール。

正本: docs/features/simulation_bridge_design.md §7（UI）/ §2 SB1・SB6・SB9。

- 入口: 教材行「⋯」メニューの「検証シミュレーション…」（ゼミ前ブリーフの直後・見積りより前）
- モーダル本体: frontend/public/js/admin-simulation-bridge.js（ES5・window.SimulationBridge）
- SB1: 一致は検証ではない — 固定の事実文（schema.FACT_NOT_VERIFICATION）を画面に常に出す
- SB6: 段階ラベルはサーバが解決した日本語を描くだけ（JS にラベル表を持たない）・confidence を扱わない
- SB9: ポーリングしない
- 3点セット: ADMIN_UI_ANCHORS / data-ui-anchor 担体 / teacher マニュアル節
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PUBLIC = ROOT / "frontend" / "public"
ADMIN_HTML = PUBLIC / "admin.html"
ADMIN_JS = PUBLIC / "js" / "admin.js"
SIM_JS = PUBLIC / "js" / "admin-simulation-bridge.js"
MANUAL = ROOT / "docs" / "manual" / "teacher" / "27-admin-simulation-bridge.md"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.simulation_bridge import schema as sb_schema  # noqa: E402
from core import label_vocab  # noqa: E402

SIM_ANCHOR_IDS = (
    "materials.row-simulation",
    "simulation.modal",
    "simulation.generate",
    "simulation.program-decide",
    "simulation.wiring-new",
    "simulation.wiring-decide",
    "simulation.run",
    "simulation.ledger-candidate",
    "simulation.propose-revision",
    "simulation.ledger-candidate-decide",
    "simulation.ledger-candidate-decided",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_without_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in src.splitlines())


class TestScriptTag:
    def test_script_tag_with_version(self):
        html = _read(ADMIN_HTML)
        assert re.search(r'<script src="/js/admin-simulation-bridge\.js\?v=[^"]+"></script>', html)

    def test_load_order_after_graph_review_before_admin_js(self):
        html = _read(ADMIN_HTML)
        sim = html.index("/js/admin-simulation-bridge.js?v=")
        assert html.index("/js/admin-graph-review.js?v=") < sim
        assert html.index("/js/admin-indicators.js?v=") < sim
        assert html.index("/js/admin-llm-models.js?v=") < sim
        assert sim < html.index("/js/admin.js?v=")


class TestMaterialRowEntry:
    def test_button_declared_with_document_id_guard(self):
        src = _read(ADMIN_JS)
        start = src.index("var simulationBtn = m.document_id")
        block = src[start : src.index("\n      //", start)]
        assert 'class="ls-menu-item admin-simulation-bridge-btn"' in block
        assert 'data-ui-anchor="materials.row-simulation"' in block
        assert "検証シミュレーション…</button>" in block
        assert ': ""' in block

    def test_button_order_in_more_menu(self):
        src = _read(ADMIN_JS)
        panel = src[src.index('<div class="ls-menu material-more-panel" hidden>') :]
        panel = panel[: panel.index("</div>")]
        assert panel.index("seminarBriefBtn") < panel.index("simulationBtn") < panel.index("referenceHealthBtn")
        assert panel.index("simulationBtn") < panel.index("estimateBtn")

    def test_button_bound_to_simulation_bridge_open(self):
        src = _read(ADMIN_JS)
        start = src.index('tbody.querySelectorAll(".admin-simulation-bridge-btn")')
        block = src[start : start + 400]
        assert "window.SimulationBridge.open(" in block

    def test_init_and_locate_resolver(self):
        src = _read(ADMIN_JS)
        assert "window.SimulationBridge.init({ apiFetch: apiFetch, escHtml: escHtml, getToken: getAuthToken })" in src
        assert "simulation_bridge_button: function (id)" in src
        assert '_matRowActionAnchor(id, ".admin-simulation-bridge-btn")' in src


class TestModalContract:
    def test_public_api(self):
        src = _read(SIM_JS)
        assert "window.SimulationBridge = {" in src
        for name in ("init:", "open: open", "close: close", "getScreenContext: getScreenContext"):
            assert name in src, name

    def test_es5_only(self):
        code = _js_without_comments(_read(SIM_JS))
        assert not re.search(r"\b(const|let)\s", code)
        assert "=>" not in code
        assert "`" not in code

    def test_disclaimer_verbatim(self):
        assert sb_schema.FACT_NOT_VERIFICATION in _read(SIM_JS)

    def test_no_label_tables_mirrored(self):
        """段階ラベルはサーバの run.result.labels を描くだけ（SB6）。"""
        src = _read(SIM_JS)
        labels = set()
        for table in (label_vocab.SIMULATION_AGREEMENT_LABELS, label_vocab.SIMULATION_SEPARATION_LABELS):
            labels.update(v for v in table.values() if isinstance(v, str) and v)
        # 「一致」は固定の事実文（FACT_NOT_VERIFICATION）にも現れる一般語なので除く。
        labels.discard("一致")
        assert labels, "label_vocab のラベル表が空"
        leaked = sorted(v for v in labels if v in src)
        assert not leaked, f"JS に段階ラベルが直書きされている: {leaked}"
        assert "おおむね一致" not in src
        assert "result.labels" in src or "obj(result.labels)" in src

    def test_no_polling(self):
        code = _js_without_comments(_read(SIM_JS))
        assert "setInterval" not in code
        assert "setTimeout" not in code

    def test_no_confidence(self):
        assert "confidence" not in _read(SIM_JS)

    def test_screen_context_has_no_text(self):
        src = _read(SIM_JS)
        start = src.index("function getScreenContext()")
        block = src[start : src.index("\n  }\n", start)]
        assert "textContent" not in block
        assert "innerText" not in block
        assert "innerHTML" not in block
        assert '"simulation_bridge"' in src
        for key in ("document_id:", "program_id:", "wiring_id:", "run_id:", "step:", "visible_entities: []"):
            assert key in block, key

    def test_ai_proposal_label_and_dotted_frame(self):
        src = _read(SIM_JS)
        assert "AI の提案（未確認）" in src
        assert "is-ai-proposal" in src
        assert ".simulation-wiring-card.is-ai-proposal" in _read(PUBLIC / "css" / "styles.css")

    def test_reuses_graph_review_modal_css(self):
        src = _read(SIM_JS)
        assert "graph-review-modal" in src and "graph-review-frame" in src


class TestAnchors:
    def test_all_anchors_registered(self):
        for anchor in SIM_ANCHOR_IDS:
            assert anchor in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor
            ref = admin_anchors_mod.ADMIN_UI_ANCHORS[anchor]
            assert ref.startswith("teacher/27-admin-simulation-bridge.md#"), ref

    def test_all_anchors_have_carriers(self):
        blob = _read(SIM_JS) + _read(ADMIN_JS) + _read(ADMIN_HTML)
        for anchor in SIM_ANCHOR_IDS:
            assert 'data-ui-anchor="' + anchor + '"' in blob, anchor

    def test_manual_anchors_resolve(self):
        raw = _read(MANUAL)
        assert raw.startswith("---\naudience: teacher\nscreen: materials\n---")
        explicit = set(re.findall(r"\{#([a-z0-9-]+)\}", raw))
        for anchor in SIM_ANCHOR_IDS:
            target = admin_anchors_mod.ADMIN_UI_ANCHORS[anchor].split("#", 1)[1]
            assert target in explicit, target

    def test_resolves_for_teacher(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor in SIM_ANCHOR_IDS:
            assert anchor in resolved, anchor
            assert resolved[anchor]["body"], anchor

    def test_manual_states_not_verification(self):
        raw = _read(MANUAL)
        assert "シミュレーションの一致は検証ではありません" in raw
        assert "学習者" in raw


class TestRound22LedgerDecision:
    """第 22 周: 判断済みの候補の折りたたみ・権限の無い教員のボタン無効化・主張はラベルで描く。"""

    def test_decided_candidates_folding(self):
        src = _read(SIM_JS)
        assert "simulation_candidates_decided" in src
        assert "判断済みの候補" in src
        assert "dismiss_reason" in src
        assert 'data-ui-anchor="simulation.ledger-candidate-decided"' in src

    def test_disabled_without_edit_permission(self):
        src = _read(SIM_JS)
        assert "ov().can_decide_ledger !== false" in src
        m = re.search(r'var LEDGER_DECIDE_NO_PERMISSION = "([^"]+)";', src)
        assert m, "LEDGER_DECIDE_NO_PERMISSION が無い"
        assert m.group(1).split("、", 1)[0] in _read(MANUAL)
        assert "!canDecide" in src

    def test_claim_is_drawn_by_label_not_id(self):
        src = _read(SIM_JS)
        assert "|| it.claim_id" not in src
        assert "obj(data).target_label" in src

    def test_module_symbols_line(self):
        src = _read(SIM_JS)
        for key in ("m.input_symbols", "m.output_symbols", "m.output_symbols_note", "m.subtitle",
                    "m.simulable === false", "m.simulability_note"):
            assert key in src, key
        assert sb_schema.FACT_NOT_SIMULABLE not in src
        assert sb_schema.FACT_NO_OUTPUT_SYMBOLS not in src

    def test_manual_round22(self):
        raw = _read(MANUAL)
        assert "{#simulation-ledger-candidate-decided}" in raw
        assert "どういう実行が候補になるか" in raw
        assert "確定は学習者からどう見えるか" in raw
        assert "教材の編集権限" in raw
