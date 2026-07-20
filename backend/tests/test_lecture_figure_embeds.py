"""Phase 4: 図のコース流通（§7.2 記法解決）の回帰テスト。

数式の ``![[equation:xxx]]`` → ``[[FORMULA_N]]`` 解決（test_lecture_equation_embeds.py）と
同格の、図記法 ``![[figure:<figure_id>]]`` → ``[[FIGURE_N]]`` 解決を検証する。

対象:
  - ``core.lecture.resolve_figure_embeds`` / ``strip_figure_embeds`` / ``find_figure_embed_ids``
  - ``core.lecture._display_length`` の図1個=200字換算（解決前後で不変であること）
  - ``core.lecture.split_slides`` / ``auto_paginate_slides`` の figures 割り当て
  - ``core.lecture.build_topic_slides`` の figures_by_id 配線（display 解決・spoken 除去）

外部依存なし（DB/LLM を一切呼ばない純関数のみ）。
"""

from __future__ import annotations


_FIG_UUID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# resolve_figure_embeds
# ---------------------------------------------------------------------------


class TestResolveFigureEmbeds:
    def test_known_figure_resolved_to_placeholder(self):
        from core.lecture import resolve_figure_embeds

        text, figures = resolve_figure_embeds(
            f"導入 ![[figure:{_FIG_UUID}]] まとめ",
            {_FIG_UUID: {"caption": "図1のキャプション", "image_url": "/img/fig-1"}},
        )

        assert text == "導入 [[FIGURE_1]] まとめ"
        assert figures == [{
            "id": "[[FIGURE_1]]",
            "figure_id": _FIG_UUID,
            "caption": "図1のキャプション",
            "explanation": None,
            "image_url": "/img/fig-1",
        }]

    def test_explanation_is_always_none_in_v1(self):
        """v1 は常に explanation=None（後続エージェントが配線する, §7.2）。"""
        from core.lecture import resolve_figure_embeds

        _text, figures = resolve_figure_embeds(
            f"![[figure:{_FIG_UUID}]]",
            {_FIG_UUID: {"caption": "c", "image_url": "/i", "explanation": "無視されるべき値"}},
        )
        assert figures[0]["explanation"] is None

    def test_unknown_figure_id_left_as_is(self):
        """figures_by_id に無い figure_id の埋め込みは原文のまま残す（情報を落とさない）。"""
        from core.lecture import resolve_figure_embeds

        text, figures = resolve_figure_embeds("前 ![[figure:unknown-id]] 後", {})
        assert text == "前 ![[figure:unknown-id]] 後"
        assert figures == []

    def test_none_figures_by_id_leaves_all_embeds_unresolved(self):
        from core.lecture import resolve_figure_embeds

        text, figures = resolve_figure_embeds(f"![[figure:{_FIG_UUID}]]", None)
        assert text == f"![[figure:{_FIG_UUID}]]"
        assert figures == []

    def test_placeholder_numbering_starts_at_one_and_is_sequential(self):
        from core.lecture import resolve_figure_embeds

        text, figures = resolve_figure_embeds(
            "![[figure:a]] and ![[figure:b]]",
            {"a": {"caption": "A"}, "b": {"caption": "B"}},
        )
        assert text == "[[FIGURE_1]] and [[FIGURE_2]]"
        assert [f["id"] for f in figures] == ["[[FIGURE_1]]", "[[FIGURE_2]]"]

    def test_mixed_known_and_unknown_ids(self):
        from core.lecture import resolve_figure_embeds

        text, figures = resolve_figure_embeds(
            "![[figure:known]] ![[figure:missing]]",
            {"known": {"caption": "K"}},
        )
        assert text == "[[FIGURE_1]] ![[figure:missing]]"
        assert len(figures) == 1
        assert figures[0]["figure_id"] == "known"

    def test_empty_or_none_text_returns_empty(self):
        from core.lecture import resolve_figure_embeds

        assert resolve_figure_embeds("", {"x": {}}) == ("", [])
        assert resolve_figure_embeds(None, {"x": {}}) == ("", [])

    def test_missing_caption_and_image_url_default_to_none(self):
        from core.lecture import resolve_figure_embeds

        _text, figures = resolve_figure_embeds(f"![[figure:{_FIG_UUID}]]", {_FIG_UUID: {}})
        assert figures[0]["caption"] is None
        assert figures[0]["image_url"] is None


# ---------------------------------------------------------------------------
# strip_figure_embeds
# ---------------------------------------------------------------------------


class TestStripFigureEmbeds:
    def test_removes_figure_embed_from_spoken_text(self):
        from core.lecture import strip_figure_embeds

        result = strip_figure_embeds(f"読み上げ ![[figure:{_FIG_UUID}]] 本文")
        assert "![[figure:" not in result
        assert "読み上げ" in result and "本文" in result

    def test_none_and_empty_are_safe(self):
        from core.lecture import strip_figure_embeds

        assert strip_figure_embeds(None) == ""
        assert strip_figure_embeds("") == ""

    def test_no_embed_is_noop(self):
        from core.lecture import strip_figure_embeds

        assert strip_figure_embeds("plain text") == "plain text"

    def test_multiple_embeds_all_removed(self):
        from core.lecture import strip_figure_embeds

        result = strip_figure_embeds("a ![[figure:x]] b ![[figure:y]] c")
        assert "![[figure:" not in result


# ---------------------------------------------------------------------------
# find_figure_embed_ids
# ---------------------------------------------------------------------------


class TestFindFigureEmbedIds:
    def test_collects_unique_ids_in_order(self):
        from core.lecture import find_figure_embed_ids

        ids = find_figure_embed_ids("a ![[figure:x]] b ![[figure:y]] c ![[figure:x]]")
        assert ids == ["x", "y"]

    def test_empty_or_none_text_returns_empty_list(self):
        from core.lecture import find_figure_embed_ids

        assert find_figure_embed_ids(None) == []
        assert find_figure_embed_ids("") == []

    def test_no_embed_returns_empty_list(self):
        from core.lecture import find_figure_embed_ids

        assert find_figure_embed_ids("no figure embeds here") == []


# ---------------------------------------------------------------------------
# _display_length: 図1個=200字換算（§7.2）
# ---------------------------------------------------------------------------


class TestFigureCharCountEquivalence:
    """resolve 前後で換算長が変わらないこと。figures_by_id の有無（呼び出し元が図メタ
    データを供給するか）によって、readiness/音声生成側とのページ分割境界がずれない
    ことを保証する（N18 の slide_index 一致原則を図にも継承）。"""

    def test_unresolved_figure_embed_counts_as_200_chars(self):
        from core.lecture import _display_length

        assert _display_length(f"![[figure:{_FIG_UUID}]]") == 200

    def test_resolved_figure_placeholder_counts_as_200_chars(self):
        from core.lecture import _display_length

        assert _display_length("[[FIGURE_1]]") == 200

    def test_resolution_does_not_change_display_length(self):
        from core.lecture import _display_length, resolve_figure_embeds

        raw_text = f"前置き ![[figure:{_FIG_UUID}]] 後書き"
        resolved_text, _figures = resolve_figure_embeds(raw_text, {_FIG_UUID: {"caption": "c"}})

        assert _display_length(raw_text) == _display_length(resolved_text)

    def test_display_length_combines_formula_and_figure_placeholders(self):
        from core.lecture import _display_length

        text = "text [[FORMULA_0]] middle [[FIGURE_1]] end"
        # "text " (5) + "middle " (7, 前後スペース込み) + "end" (3) = プレーン文字数
        plain = "text  middle  end"
        assert _display_length(text) == len(plain) + 60 + 200


# ---------------------------------------------------------------------------
# split_slides / auto_paginate_slides: figures の割り当て
# ---------------------------------------------------------------------------


class TestSplitSlidesFigureAssignment:
    def test_figures_assigned_to_referencing_slide(self):
        from core.lecture import split_slides

        display = "Slide A [[FIGURE_1]]\n===\nSlide B [[FIGURE_2]]"
        figures = [
            {"id": "[[FIGURE_1]]", "figure_id": "f1", "caption": None, "explanation": None, "image_url": None},
            {"id": "[[FIGURE_2]]", "figure_id": "f2", "caption": None, "explanation": None, "image_url": None},
        ]
        slides, _mismatch = split_slides(display, None, [], figures)

        assert slides[0]["figures"] == [figures[0]]
        assert slides[1]["figures"] == [figures[1]]

    def test_unreferenced_figures_go_to_last_slide(self):
        from core.lecture import split_slides

        display = "Slide A\n===\nSlide B"
        figures = [{"id": "[[FIGURE_9]]", "figure_id": "f9", "caption": None, "explanation": None, "image_url": None}]
        slides, _mismatch = split_slides(display, None, [], figures)

        assert slides[0]["figures"] == []
        assert slides[1]["figures"] == figures

    def test_figures_union_preserves_all_input_figures(self):
        """全スライドの figures の和 == 入力 figures（情報を落とさない）。"""
        from core.lecture import split_slides

        display = "Slide A [[FIGURE_1]]\n===\nSlide B"
        figures = [
            {"id": "[[FIGURE_1]]", "figure_id": "f1", "caption": None, "explanation": None, "image_url": None},
            {"id": "[[FIGURE_5]]", "figure_id": "f5", "caption": None, "explanation": None, "image_url": None},
        ]
        slides, _mismatch = split_slides(display, None, [], figures)

        all_figures = [f for s in slides for f in s["figures"]]
        assert len(all_figures) == len(figures)
        assert {f["id"] for f in all_figures} == {f["id"] for f in figures}

    def test_no_figures_input_yields_empty_figures_key(self):
        from core.lecture import split_slides

        slides, _mismatch = split_slides("Hello", "Spoken", [])
        assert slides[0]["figures"] == []

    def test_auto_paginate_slides_threads_figures_through(self):
        from core.lecture import auto_paginate_slides

        display = "Slide A [[FIGURE_1]]"
        figures = [{"id": "[[FIGURE_1]]", "figure_id": "f1", "caption": None, "explanation": None, "image_url": None}]
        slides, _mismatch = auto_paginate_slides(display, None, [], figures=figures)

        assert slides[0]["figures"] == figures


# ---------------------------------------------------------------------------
# build_topic_slides: figures_by_id 配線の統合テスト
# ---------------------------------------------------------------------------


class TestBuildTopicSlidesFigures:
    def test_figure_embed_resolved_and_stripped_from_spoken(self):
        from core.lecture import build_topic_slides

        topic = {
            "student_material": {"source_text": f"教材本文 ![[figure:{_FIG_UUID}]] 続き"},
            "spoken_script": f"読み上げ ![[figure:{_FIG_UUID}]] 続き",
        }
        figures_by_id = {_FIG_UUID: {"caption": "装置図", "image_url": "/img/fig-1"}}

        slide_dicts, display_text, spoken_text, _formulas = build_topic_slides(
            topic, figures_by_id=figures_by_id,
        )

        assert "[[FIGURE_1]]" in display_text
        assert "![[figure:" not in display_text
        assert "![[figure:" not in spoken_text
        assert len(slide_dicts) == 1
        assert slide_dicts[0]["figures"][0]["figure_id"] == _FIG_UUID

    def test_without_figures_by_id_embed_left_unresolved_in_display(self):
        """呼び出し元が figures_by_id を渡さない場合（readiness/音声生成側）は
        埋め込みを解決せず原文のまま残す（既存呼び出し元との後方互換）。"""
        from core.lecture import build_topic_slides

        topic = {"student_material": {"source_text": f"教材本文 ![[figure:{_FIG_UUID}]] 続き"}}
        slide_dicts, display_text, _spoken, _formulas = build_topic_slides(topic)

        assert f"![[figure:{_FIG_UUID}]]" in display_text
        assert slide_dicts[0]["figures"] == []

    def test_spoken_text_never_contains_figure_embed_even_without_figures_by_id(self):
        """読み上げ原稿には図を含めない（figures_by_id の有無に関わらず常に除去, §7.2）。"""
        from core.lecture import build_topic_slides

        topic = {
            "student_material": {"source_text": "表示本文"},
            "spoken_script": f"読み上げ ![[figure:{_FIG_UUID}]] 続き",
        }
        _slides, _display, spoken_text, _formulas = build_topic_slides(topic)

        assert "![[figure:" not in spoken_text
