"""IK-0808: A層の LLM 入力の切り詰めは語境界・数式の外・切った側に「…」。"""
from episteme_graph.agents.text_truncation import ELLIPSIS, excerpt_around, truncate_text


def test_head_cut_never_splits_a_word_and_marks_the_cut():
    text = "we take the overall agreement as evidence of the robustness of the pipeline " * 3
    out, cut = truncate_text(text, 120)
    assert cut and out.endswith(ELLIPSIS)
    assert text.startswith(out[:-1])
    assert out[:-1].split()[-1] in text.split()


def test_tail_cut_prefers_a_sentence_start_so_the_subject_survives():
    text = ("Filler words are here. " * 10) + "A_AP is the factor already defined above. It sets the scale."
    out, cut = truncate_text(text, 90, keep_end=True)
    assert cut and out.startswith(ELLIPSIS) and text.endswith(out[1:])
    assert "A_AP is the factor" in out
    # 先頭は文の頭（主語の手前で切らない）
    head = len(text) - (len(out) - 1)
    assert text[:head].rstrip().endswith(".")


def test_never_cuts_inside_dollar_math():
    text = "We set $a + b + c + d + e$ in the general formalism of the paper and more"
    for limit in range(8, len(text)):
        for keep_end in (False, True):
            out, _ = truncate_text(text, limit, keep_end=keep_end)
            assert out.count("$") % 2 == 0


def test_excerpt_around_keeps_the_target_whole():
    text = "x " * 900 + "TARGET SPAN TEXT." + " y" * 900
    start = text.index("TARGET")
    out, cut = excerpt_around(text, start, start + len("TARGET SPAN TEXT."), 300)
    assert cut and "TARGET SPAN TEXT." in out
    assert out.startswith(ELLIPSIS) and out.endswith(ELLIPSIS)
