"""学習者 UI がサーバの判断応答（自己確認・帰属の確定/却下）をそのまま映すことの静的検査。

- IK-0399: 確認問題の自己確認で「違っていた」はトピックを完了にしない
  （``core/check_review.py::SELF_CHECK_ADVANCING == ("agreed",)``）。UI は完了をクライアントで
  推測せず応答の ``topic_completed`` を使い、``notice`` を textContent で出す。
- IK-0411: 帰属（structure_anchor）の confirm / dismiss 応答は学習者向けに射影された
  （ok / trace_id / status / notice / 各ラベル / related_assumption.statement）。UI は
  ``notice`` を描き、射影で落ちたキー（confidence / reason / anchor_id / detector_version /
  evidence_quote / anchor_status）を読まない。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
ATLAS_OVERLAY_JS = ROOT / "frontend" / "public" / "js" / "atlas-overlay.js"
CORPUS_SEA_JS = ROOT / "frontend" / "public" / "js" / "corpus-sea.js"

sys.path.insert(0, str(ROOT / "backend"))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    m = re.search(r"function " + name + r"\([^)]*\)\s*\{[\s\S]+?\n  \}\n", src)
    assert m, f"function {name} が見つかりません"
    return m.group(0)


_REMOVED_ANCHOR_KEYS = (
    "confidence", "reason", "anchor_id", "detector_version", "evidence_quote", "anchor_status",
)


class TestCheckSelfCheckFollowsServer:
    def test_server_contract_only_agreed_advances(self):
        from core import check_review

        assert check_review.SELF_CHECK_ADVANCING == ("agreed",)

    def test_done_state_requires_server_topic_completed(self):
        block = _function_block(_read(APP_JS), "checkSelfCheckHtml")
        # 完了表示は「合っていた」かつサーバが完了と返したときだけ。
        assert 'review.topicCompleted && review.selfCheck === "agreed"' in block
        # disagreed を完了扱いにしていた旧判定を戻さない。
        assert 'review.selfCheck === "disagreed"' not in block

    def test_note_does_not_claim_disagreed_finishes(self):
        block = _function_block(_read(APP_JS), "checkSelfCheckHtml")
        assert "どちらを選んでも記録の中身は同じです" not in block
        assert "まだ完了になりません" in block

    def test_notice_slot_filled_with_text_content(self):
        src = _read(APP_JS)
        assert 'id="check-selfcheck-notice"' in _function_block(src, "checkSelfCheckHtml")
        apply_block = _function_block(src, "applyCheckReviewState")
        assert 'noticeEl.textContent = review.selfCheckNotice || ""' in apply_block
        assert "innerHTML = review.selfCheckNotice" not in src

    def test_submit_reads_topic_completed_and_notice_from_response(self):
        block = _function_block(_read(APP_JS), "submitCheckSelfCheck")
        assert "review.topicCompleted = !!data.topic_completed" in block
        assert "review.selfCheckNotice = typeof data.notice === \"string\" ? data.notice : \"\"" in block
        # 記録に失敗して agreed でも完了にならなかったら、終えたと言わない。
        assert 'value === "agreed" && !review.topicCompleted' in block


class TestAnchorDecisionNotice:
    def test_app_confirm_and_dismiss_store_server_notice(self):
        src = _read(APP_JS)
        for name in ("confirmAnchor", "dismissAnchor"):
            block = _function_block(src, name)
            assert 'state.anchorNotice = ""' in block, name
            assert "data.notice" in block, name

    def test_app_digest_card_renders_notice_escaped(self):
        block = _function_block(_read(APP_JS), "renderAnchorDigestCard")
        assert "escHtml(state.anchorNotice)" in block
        assert 'class="lx-digest-notice"' in block

    def test_app_notice_cleared_on_course_switch(self):
        block = _function_block(_read(APP_JS), "switchCourse")
        assert 'state.anchorNotice = ""' in block

    def test_discuss_landing_cards_render_notice_via_text_content(self):
        src = _read(DISCUSS_JS)
        for name in ("confirmAnchorCard", "dismissAnchorCard"):
            block = _function_block(src, name)
            assert "readAnchorDecisionNotice(res)" in block, name
        assert "done.textContent = text" in _function_block(src, "setAnchorCardDone")
        assert "data.notice" in _function_block(src, "readAnchorDecisionNotice")

    def test_decision_handlers_do_not_read_removed_keys(self):
        app = _read(APP_JS)
        discuss = _read(DISCUSS_JS)
        blocks = [
            _function_block(app, "confirmAnchor"),
            _function_block(app, "dismissAnchor"),
            _function_block(app, "confirmTension"),
            _function_block(app, "dismissTension"),
            _function_block(discuss, "confirmAnchorCard"),
            _function_block(discuss, "dismissAnchorCard"),
            _function_block(discuss, "dismissTensionCard"),
            _function_block(discuss, "readAnchorDecisionNotice"),
        ]
        for block in blocks:
            for key in _REMOVED_ANCHOR_KEYS:
                assert "data." + key not in block, key
                assert "data[\"" + key + "\"]" not in block, key


class TestAtlasPillNote:
    """IK-0412: GET /api/atlas の nodes[*].pill_note（ピルの意味の1行）をピルの下に出す。"""

    def test_pill_note_element_sits_outside_panel_body(self):
        src = _read(ATLAS_OVERLAY_JS)
        i_note = src.index('id: "atlas-panel-pill-note"')
        i_body = src.index('id: "atlas-panel-body"')
        # body は atlas-panel.js が差し替えるので、その外（直前）に置く。
        assert i_note < i_body

    def test_select_node_writes_server_text_via_text_content(self):
        block = _function_block(_read(ATLAS_OVERLAY_JS), "selectNode")
        assert 'typeof info.pill_note === "string"' in block
        assert "pillNote.textContent = noteText" in block
        assert "pillNote.hidden = !noteText" in block
        assert "pillNote.innerHTML" not in block

    def test_breadcrumb_comes_from_server_crumbs_not_cartridge_key(self):
        src = _read(ATLAS_OVERLAY_JS)
        assert "state.data.crumbs" in src
        assert "data.cartridge" not in src


class TestCorpusDocumentFacts:
    """IK-0414: GET /api/learning/corpus/documents の optional facts を一覧の下に出す。"""

    def test_facts_read_fail_soft(self):
        src = _read(CORPUS_SEA_JS)
        block = _function_block(src, "selectDomain")
        assert "state.documentFacts = []" in block
        assert "Array.isArray(documents.facts)" in block

    def test_facts_rendered_escaped_under_list_and_empty_state(self):
        src = _read(CORPUS_SEA_JS)
        assert "esc(f)" in _function_block(src, "documentFactsHtml")
        block = _function_block(src, "renderPapers")
        assert block.count("documentFactsHtml()") == 2


class TestCacheBuster:
    def test_app_and_discuss_carry_version_query(self):
        html = _read(INDEX_HTML)
        assert re.search(r'<script src="/js/app\.js\?v=[^"]+"></script>', html)
        assert re.search(r'<script src="/js/discuss\.js\?v=[^"]+"></script>', html)
        assert re.search(r'<script src="/js/atlas-overlay\.js\?v=ik0412-[^"]+"></script>', html)
        assert re.search(r'<script src="/js/corpus-sea\.js\?v=ik0414-[^"]+"></script>', html)
