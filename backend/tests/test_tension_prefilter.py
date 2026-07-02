"""TensionPrefilter (Stage 0) — 非LLMヒューリスティックの純関数テスト。

観点（設計書 §11-1）: マーカー陽性/陰性、再訪検知、例外時にチャットを止めない。
"""

from __future__ import annotations

from core.tension.prefilter import _has_revisit, judge_tension_hint


class TestHedgeMarkers:
    def test_hedge_marker_raises_hint(self):
        assert judge_tension_hint("説明はわかるんですが、なんとなく気持ち悪くて。", []) is True

    def test_multiple_marker_variants(self):
        for text in (
            "どうも腑に落ちないんです",
            "この定義、違和感があります",
            "でもその場合はどうなるんですか",
            "そもそもこれは等方性を前提にしていますよね",
            "この主張、本当に正しいんでしょうか",
        ):
            assert judge_tension_hint(text, []) is True, text

    def test_plain_question_is_not_hint(self):
        assert judge_tension_hint("リー群の定義を教えてください", []) is False

    def test_empty_text_is_not_hint(self):
        assert judge_tension_hint("", []) is False
        assert judge_tension_hint(None, []) is False


class TestRevisit:
    def test_revisit_of_same_noun_phrase(self):
        recent = ["重クォーク展開の1/m補正って、結局どこまで信用していいんですか"]
        assert judge_tension_hint("重クォーク展開だと格子QCDの結果はどうなりますか", recent) is True

    def test_no_revisit_without_common_substring(self):
        recent = ["ゲージ対称性について教えてください"]
        assert judge_tension_hint("繰り込み群の話をしましょう", recent) is False

    def test_hiragana_only_overlap_is_ignored(self):
        # 「について」のような助詞・言い回しの一致では再訪と見なさない
        assert _has_revisit("これについて教えて", ["あれについて知りたい"]) is False

    def test_satisfied_close_suppresses_revisit(self):
        # 納得表明で閉じた発話は、字面一致だけでヒントを立てない
        recent = ["重クォーク展開の補正について"]
        assert judge_tension_hint("なるほど、重クォーク展開の件は理解できました", recent) is False

    def test_hedge_wins_over_counter_marker(self):
        # ヘッジがあれば納得語と共存してもヒントは立つ（ヘッジ優先）
        assert judge_tension_hint("なるほど。でも、まだ腑に落ちないところがあります", ["前の質問"]) is True


class TestBestEffort:
    def test_exception_returns_false(self):
        # recent_user_texts に list 化できない値 → 例外を握りつぶして False
        assert judge_tension_hint("違和感" * 1, 0) in (True, False)  # ヘッジ判定が先に立つ
        assert judge_tension_hint("普通の質問です", 0) is False
