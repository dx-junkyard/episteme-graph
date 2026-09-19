"""知識の転用層 P4-2 — 採用した出典の構造を学習チャットへ渡す事実文の遮断。

対象は ``core/assistant_context/resolvers/learning.py`` の
``resolve_retrieved_structure`` 周辺（束の取り込みで入ってきた主張も、この経路で
学習者の目に触れる）。

固定するのは **V-8**（2026-09-13 の実データ検証）:
``Equation (3.55) defines \\delta P_{\\mathrm{shot}}.`` のような生 TeX を含む claim 本文が
そのまま学習者向けの事実文に出ていた。内部 ID の遮断はあったが TeX の遮断が無かった。
遮断の述語は ``core/learner_context_common.py::safe_text`` が正本で、
**ここで新しい判定を作らない**。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.assistant_context import SCREEN_LEARNING, ScreenContext  # noqa: E402
from core.assistant_context.resolvers import learning as lr  # noqa: E402


def _ctx() -> ScreenContext:
    return ScreenContext(screen=SCREEN_LEARNING, selection={}, view={}, visible_entities=[])


def _sources(claims: list[dict]) -> dict:
    return {"retrieved_structure": {"sources": [{"index": "1", "claims": claims}]}}


class TestRetrievedStructureBlocksRawTex:
    def test_a_prose_claim_is_kept(self):
        facts = lr.resolve_retrieved_structure(
            _ctx(), _sources([{"text": "観測量は比として定義される。", "claim_type": "definition"}]),
        )
        assert facts
        assert "観測量は比として定義される。" in facts[0]

    def test_a_claim_with_raw_tex_is_dropped(self):
        """V-8: TeX を含む欄は落とす（平文のふりをして生 TeX を渡さない）。"""
        facts = lr.resolve_retrieved_structure(
            _ctx(),
            _sources([{
                "text": "Equation (3.55) defines \\delta P_{\\mathrm{shot}}.",
                "claim_type": "definition",
            }]),
        )
        assert facts == []

    def test_a_tex_claim_does_not_block_the_next_one(self):
        """落ちるのはその欄だけ（他の主張まで巻き添えにしない）。"""
        facts = lr.resolve_retrieved_structure(
            _ctx(),
            _sources([
                {"text": "\\begin{aligned} a &= b \\end{aligned}"},
                {"text": "比の定義から観測量が決まる。"},
            ]),
        )
        assert len(facts) == 1
        assert "比の定義から観測量が決まる。" in facts[0]

    def test_the_blocking_predicate_is_the_shared_one(self):
        """判定の正本は learner_context_common（新しい TeX 判定を作らない）。"""
        source = (
            BACKEND / "core" / "assistant_context" / "resolvers" / "learning.py"
        ).read_text(encoding="utf-8")
        assert "safe_text" in source
        assert "looks_like_tex_math" not in source, "TeX 判定をこの層で再実装しない"
