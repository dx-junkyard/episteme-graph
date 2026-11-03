"""IK-0393: 構造の手がかりに渡す主張本文の衛生（語の途中で切らない・断片を出さない）。

ペルソナ通し受講 第 8 周で、検索由来の構造ブロックの主張が 120 字の素スライスで
語の途中から切れ（``…indicating tha`` / ``…in the t``）、``≈9.3`` のような数値だけの
主張が「構造化された主張」として渡っていた。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.assistant_context import SCREEN_LEARNING, normalize_screen_context  # noqa: E402
from core.assistant_context.resolvers import graph_review as graph_review_resolver  # noqa: E402
from core.assistant_context.resolvers import learning as learning_resolver  # noqa: E402
from core.assistant_context.schema import MAX_LEARNING_RETRIEVED_CLAIM_CHARS  # noqa: E402

_LONG_CLAIM = (
    "The estimated mass-to-flux ratio suggests that the filament is magnetically "
    "subcritical on global scales, indicating that the magnetic field can support "
    "the filament against gravitational collapse."
)


def _facts(*claims):
    sources = {"retrieved_structure": {"sources": [{"index": "1", "claims": list(claims)}]}}
    return learning_resolver.resolve_retrieved_structure(
        normalize_screen_context({"screen": SCREEN_LEARNING}), sources
    )


class TestWordBoundaryTruncation:
    def test_long_claim_is_cut_at_a_word_boundary_with_ellipsis(self):
        facts = _facts({"text": _LONG_CLAIM, "claim_type": "result"})
        assert len(facts) == 1
        quoted = facts[0].split("「", 1)[1].split("」", 1)[0]
        assert len(quoted) <= MAX_LEARNING_RETRIEVED_CLAIM_CHARS
        assert quoted.endswith("…")
        body = quoted[:-1]
        # 切れ目の直前は語の終わり（元の本文で次の文字が空白・句読点）
        assert _LONG_CLAIM.startswith(body)
        assert not _LONG_CLAIM[len(body)].isalpha()

    def test_short_claim_is_untouched(self):
        facts = _facts({"text": "Core spacing is variable.", "claim_type": "result"})
        assert "「Core spacing is variable.」" in facts[0]

    def test_graph_review_text_helper_uses_the_same_excerpt(self):
        cut = graph_review_resolver._text(_LONG_CLAIM, 60)
        assert cut.endswith("…")
        assert not _LONG_CLAIM[len(cut) - 1].isalpha()

    def test_no_raw_slice_left_in_resolvers(self):
        for module in (learning_resolver, graph_review_resolver):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "return text[:limit]" not in source


class TestFragmentClaimsAreNotOffered:
    def test_number_only_claim_is_dropped(self):
        facts = _facts(
            {"text": "≈9.3", "claim_type": "result"},
            {"text": "Core spacing is variable.", "claim_type": "result"},
        )
        assert len(facts) == 1
        assert "≈9.3" not in facts[0]

    def test_fragment_predicate(self):
        f = learning_resolver.is_fragment_claim_text
        for text in ("≈9.3", "(2)", "0.47 ± 0.02", "B = 3", ""):
            assert f(text) is True, text
        for text in ("Core spacing is variable.", "主張0。", "we obtain B3D ≈195 μG", "DCF"):
            assert f(text) is False, text
