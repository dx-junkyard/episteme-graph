"""IK-0545: 回答本文の逐語引用と出典チャンク本文の一致検査（core/citation_check.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.citation_check import (  # noqa: E402
    CITATION_UNMATCHED_NOTE,
    check_citations,
)
from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

P1 = "We measure the magnetic field along the filament and find alignment."
P2 = "銀河団のガスは重力ポテンシャルに沿って加熱される。"


def test_matched_quote_after_marker_is_checked_not_unmatched():
    ans = "論文は「重力ポテンシャルに沿って加熱される」と述べています[出典2]。"
    out = check_citations(ans, {1: P1, 2: P2})
    assert out == {"checked": ["2"], "unmatched": []}


def test_misattributed_quote_is_unmatched_and_answer_is_not_changed():
    ans = "論文は「重力ポテンシャルに沿って加熱される」と述べています[出典1]。"
    out = check_citations(ans, {1: P1, 2: P2})
    assert out["checked"] == ["1"]
    assert out["unmatched"] == [
        {"citation": "1", "quotes": ["重力ポテンシャルに沿って加熱される"], "note": CITATION_UNMATCHED_NOTE}
    ]


def test_normalization_whitespace_case_width_and_trailing_period():
    ans = '著者は "the  MAGNETIC field along the filament." と書いています [出典1]'
    assert check_citations(ans, {"1": P1})["unmatched"] == []
    # 全角英数・全角空白も吸収する（NFKC）。
    ans2 = "「ｍｅａｓｕｒｅ　ｔｈｅ magnetic」[出典1]"
    assert check_citations(ans2, {1: P1})["unmatched"] == []


def test_marker_before_quote_in_same_sentence():
    ans = "[出典2]によれば「ガスは重力ポテンシャルに沿って」いる。"
    assert check_citations(ans, {2: P2}) == {"checked": ["2"], "unmatched": []}


def test_sentence_boundary_cuts_attribution():
    # 引用の後に句点があり、マーカーは次の文 → 結び付けない（検査しない）。
    ans = "「重力ポテンシャルに沿って加熱される」。次に磁場です[出典1]。"
    assert check_citations(ans, {1: P1, 2: P2}) is None


def test_adjacent_markers_any_match_is_enough():
    ans = "「重力ポテンシャルに沿って加熱される」[出典1][出典2]"
    out = check_citations(ans, {1: P1, 2: P2})
    assert out == {"checked": ["1", "2"], "unmatched": []}


def test_ellipsis_fragments_checked_in_order():
    ans = "「We measure the magnetic…find alignment」[出典1]"
    assert check_citations(ans, {1: P1})["unmatched"] == []
    bad = "「find alignment…We measure the magnetic」[出典1]"
    assert check_citations(bad, {1: P1})["unmatched"]


def test_answers_without_quotes_or_markers_return_none():
    assert check_citations("引用のない説明です[出典1]。", {1: P1}) is None  # 言い換えは検査しない
    assert check_citations("「重力ポテンシャルに沿って加熱される」", {2: P2}) is None
    assert check_citations("", {1: P1}) is None
    assert check_citations("「短い語」[出典1]", {1: P1}) is None  # 用語の強調は検査しない


def test_unknown_marker_number_is_not_checked():
    assert check_citations("「重力ポテンシャルに沿って加熱される」[出典9]", {1: P1}) is None


def test_quote_containing_period_stays_attributed():
    ans = "「銀河団のガスは重力ポテンシャルに沿って加熱される。」[出典2]"
    assert check_citations(ans, {2: P2}) == {"checked": ["2"], "unmatched": []}


def test_output_has_no_numeric_values():
    ans = "「重力ポテンシャルに沿って加熱される」[出典1]"
    out = check_citations(ans, {1: P1})

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                assert k not in {"score", "count", "confidence", "ratio"}
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            assert isinstance(v, str)

    walk(out)


def test_core_module_is_pure():
    src = (Path(_BACKEND) / "core" / "citation_check.py").read_text(encoding="utf-8")
    assert_source_does_not_import(
        src, ["fastapi", "sqlalchemy", "openai", "core.llm", "api"], context="core/citation_check.py"
    )
