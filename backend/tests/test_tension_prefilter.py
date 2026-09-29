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


class TestFalsePositivesObservedInRun8:
    """IK-0392: ペルソナ通し受講 第 8 周で全発話にヒントが立っていた実例。

    原因は同語再訪の4文字窓が英語の機能語（``What`` / ``does`` / ``this``）で毎回一致して
    いたこと、および定義質問・依頼の定型を区別していなかったこと。
    """

    def test_english_definition_question_is_not_hint(self):
        recent = ["What does the paper mean by thermally supercritical here?"]
        assert judge_tension_hint("What does external feedback mean here?", recent) is False

    def test_check_question_request_is_not_hint(self):
        recent = ["確認問題を出してもらえますか", "確認問題を一つください"]
        assert judge_tension_hint("確認問題を出してください", recent) is False

    def test_japanese_definition_question_with_repeated_term_is_not_hint(self):
        recent = ["DCF法で磁場を推定するんですよね"]
        assert judge_tension_hint("DCF法とは何ですか", recent) is False

    def test_i_dont_know_is_gap_not_tension(self):
        recent = ["Could you explain ambipolar diffusion?"]
        assert judge_tension_hint(
            "I don't know 'ambipolar diffusion'. Could you explain it simply?", recent
        ) is False

    def test_english_function_words_do_not_count_as_revisit(self):
        assert _has_revisit("What does this mean?", ["What does that do?"]) is False

    def test_english_content_word_revisit_still_counts(self):
        assert _has_revisit("Is the filament stable?", ["Why is the filament dense?"]) is True

    def test_adverbial_demo_is_not_contrast(self):
        assert judge_tension_hint("何でも質問していいですか", []) is False


class TestTrueTensionStillDetected:
    def test_japanese_contradiction(self):
        assert judge_tension_hint("でも、それだと磁場の推定と矛盾しませんか", []) is True

    def test_english_contradiction(self):
        text = (
            "I am confused: the filament is 'thermally supercritical' but "
            "'magnetically subcritical'. Aren't these contradictory?"
        )
        assert judge_tension_hint(text, []) is True

    def test_english_markers_variants(self):
        for text in (
            "However, doesn't that mean the field is overestimated?",
            "This doesn't make sense if the gas is supercritical.",
            "How can it collapse when the field supports it?",
            "Isn't that inconsistent with the Planck result?",
        ):
            assert judge_tension_hint(text, []) is True, text

    def test_marker_wins_over_request_form(self):
        # 「〜ください」の依頼形でも、逆接・矛盾のマーカーがあればヒントは立つ
        assert judge_tension_hint("でも矛盾している気がするので説明してください", []) is True

    def test_curly_apostrophe_is_normalized(self):
        assert judge_tension_hint("Doesn’t that contradict the earlier estimate?", []) is True
