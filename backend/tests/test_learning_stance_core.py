"""学習チャットの様相層（core/learning_stance/）の単体テスト。

正本: ``docs/features/learning_chat_entry_unification_design.md``（§4.2 / §5 / §8）。

ここで固定するのは:

1. 非LLM 一次判定 ``prejudge`` の**決定性・純粋性**と、実際の判定規則
   （内容語の共起 / 数式らしさ / 内容語 + 問い形 + 十分な長さ）。
2. 学習相談・使い方の合図があれば必ず ``None``（LEARNING_ADVICE / USAGE_HELP の
   受け皿を奪わない）。
3. 「casual らしさ」を判定しない（``prejudge`` は DOMAIN_RAG か None の2値のみ）。
4. 様相の解決順（``resolve_stance``）と DTO（``build_stance_dto``）の数値非含有。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.label_vocab import LEARNING_STANCE_LABELS  # noqa: E402
from core.learning_stance import (  # noqa: E402
    SOURCE_EXPLICIT,
    SOURCE_INFERRED,
    STANCE_CASUAL_LIGHT,
    STANCE_CYCLE_DIFF,
    STANCE_CYCLE_ELICIT,
    STANCE_DISCUSS,
    STANCE_SOURCES,
    STANCE_TUTOR,
    STANCES,
    build_stance_dto,
    prejudge,
    resolve_stance,
)

#: learning.py が渡す分野非依存語（テストではこれだけで規則を固定する。
#: 分野固有語はカートリッジ由来で、供給口のテストは別途 routing 側にある）。
_NEUTRAL_TERMS = (
    "式", "方程式", "定理", "法則", "証明", "導出", "定義", "公式",
    "理論", "概念", "仮定", "前提条件", "モデル", "計算", "関数", "係数",
    "変数", "近似", "観測", "実験", "論文",
)


def _judge(message: str, terms=_NEUTRAL_TERMS) -> str | None:
    return prejudge(message, content_terms=terms)


# ===========================================================================
# 1. prejudge — 教材内容の問いと判定する規則
# ===========================================================================


class TestPrejudgeSaysDomainRag:
    @pytest.mark.parametrize("message", [
        # 規則A: 相異なる内容語の共起（2つ以上）
        "この定理の証明を追いたい",
        "近似のあとの方程式はどうなりますか",
        "論文のモデルの仮定を整理したい",
    ])
    def test_content_term_cooccurrence(self, message):
        assert _judge(message) == "DOMAIN_RAG"

    @pytest.mark.parametrize("message", [
        # 規則B: 数式らしさ + 内容語 or 問い形
        "$E = mc^2$ はどこから出てきたの？",
        r"\frac{dx}{dt} の項がなぜ消えるのですか",
        "H=T+V の右辺はなんですか",
    ])
    def test_math_notation_with_question_or_content(self, message):
        assert _judge(message) == "DOMAIN_RAG"

    @pytest.mark.parametrize("message", [
        # 規則C: 内容語1つ + 問い形 + 十分な長さ（12文字以上）
        "この近似はどういう意味ですか",
        "観測のところがよくわからない、教えてください",
    ])
    def test_single_content_term_with_question_form_and_length(self, message):
        assert _judge(message) == "DOMAIN_RAG"

    def test_cartridge_terms_are_supplied_by_the_caller(self):
        """分野固有語は引数で供給する（モジュールに分野語を書かない）。"""
        message = "Standard Model について知りたいことがあります"
        assert _judge(message) is None
        assert prejudge(
            message, content_terms=_NEUTRAL_TERMS + ("Standard Model", "gauge boson"),
        ) is None  # 内容語1つ・問い形なしは依然として決められない
        assert prejudge(
            "Standard Model の gauge boson について教えて",
            content_terms=_NEUTRAL_TERMS + ("Standard Model", "gauge boson"),
        ) == "DOMAIN_RAG"

    def test_cartridge_terms_are_matched_case_insensitively(self):
        assert prejudge(
            "standard model と higgs の関係を教えて",
            content_terms=("Standard Model", "Higgs"),
        ) == "DOMAIN_RAG"


class TestPrejudgeStandsDown:
    @pytest.mark.parametrize("message", [
        "", "   ",
        "おはよう",
        "今日は疲れたなあ",
        "そうなんだ",
        "式",                      # 短すぎる断片（内容語1つ・問い形なし）
        "なるほど？",              # 問い形だけ
    ])
    def test_returns_none_when_undecided(self, message):
        assert _judge(message) is None

    @pytest.mark.parametrize("message", [
        "この講義はどう進めたらいいですか",
        "何から学べばいいですか",
        "学習計画を一緒に考えてほしい",
        "どこから始めればいいでしょうか",
        "学習の順番を教えてください",
    ])
    def test_learning_advice_cues_always_stand_down(self, message):
        """LEARNING_ADVICE ルートを奪わない（設計 §4.2 の保守ガード）。"""
        assert _judge(message) is None

    @pytest.mark.parametrize("message", [
        "音声モードの使い方がわからない",
        "このボタンはどこにありますか",
        "画面の操作方法を教えてください",
        "マイクの使い方を教えて",
    ])
    def test_usage_help_cues_always_stand_down(self, message):
        """USAGE_HELP ルートを奪わない（HELP pre-route より保守的に降りる）。"""
        assert _judge(message) is None

    def test_advice_cue_wins_even_with_content_terms(self):
        """内容語が揃っていても、学習相談の合図があれば降りる。"""
        assert _judge("この定理と証明はどの順番で学べばいいですか") is None

    def test_help_cue_wins_even_with_content_terms(self):
        assert _judge("この式を表示する画面のボタンはどこ") is None


class TestPrejudgeContract:
    def test_returns_only_domain_rag_or_none(self):
        """CHIT_CHAT / casual をヒューリスティックで推定しない（設計 §4.2）。"""
        samples = [
            "おなかすいた", "こんにちは", "この定理の証明を追いたい",
            "$E=mc^2$", "使い方を教えて", "どう進めればいい？", "",
        ]
        assert {_judge(s) for s in samples} <= {"DOMAIN_RAG", None}

    def test_is_deterministic(self):
        message = "この近似の導出はどうなりますか"
        results = {_judge(message) for _ in range(20)}
        assert results == {"DOMAIN_RAG"}

    def test_does_not_mutate_inputs(self):
        terms = list(_NEUTRAL_TERMS)
        snapshot = list(terms)
        prejudge("この定理の証明", content_terms=terms)
        assert terms == snapshot

    def test_works_without_content_terms(self):
        """カートリッジも分野非依存語も無い呼び出しで例外にならない（フェイルソフト）。"""
        assert prejudge("なにか質問があります") is None
        assert prejudge("$a = b$ はなぜ？") == "DOMAIN_RAG"

    def test_handles_none_message(self):
        assert prejudge(None, content_terms=_NEUTRAL_TERMS) is None  # type: ignore[arg-type]


# ===========================================================================
# 2. resolve_stance — 様相の解決順（設計 §4.2 の [1] / 契約 §3）
# ===========================================================================


class TestResolveStance:
    def test_cycle_modes_are_always_explicit(self):
        assert resolve_stance(cycle_mode="elicit") == (STANCE_CYCLE_ELICIT, SOURCE_EXPLICIT)
        assert resolve_stance(cycle_mode="diff") == (STANCE_CYCLE_DIFF, SOURCE_EXPLICIT)
        # cycle は discuss と併送されるが、cycle が優先される（プロンプト分岐と同じ順序）。
        assert resolve_stance(cycle_mode="elicit", is_discuss=True)[0] == STANCE_CYCLE_ELICIT

    def test_unknown_cycle_mode_is_ignored(self):
        """値検証は呼び出し側の 422 precheck の責務（ここでは無視して次の段へ）。"""
        assert resolve_stance(cycle_mode="bogus", is_discuss=True)[0] == STANCE_DISCUSS

    def test_discuss_is_always_explicit(self):
        assert resolve_stance(is_discuss=True) == (STANCE_DISCUSS, SOURCE_EXPLICIT)

    def test_explicit_casual_vs_inferred_casual_light(self):
        assert resolve_stance(is_casual=True, explicit_casual=True) == (
            STANCE_CASUAL_LIGHT, SOURCE_EXPLICIT,
        )
        assert resolve_stance(is_casual=True, explicit_casual=False) == (
            STANCE_CASUAL_LIGHT, SOURCE_INFERRED,
        )

    def test_tutor_is_the_fallback(self):
        assert resolve_stance() == (STANCE_TUTOR, SOURCE_INFERRED)
        assert resolve_stance(has_typed_action=True) == (STANCE_TUTOR, SOURCE_EXPLICIT)
        assert resolve_stance(has_atlas_context=True) == (STANCE_TUTOR, SOURCE_EXPLICIT)

    def test_signature_has_no_scope_or_mode_switches(self):
        """LC1: 様相は discuss_scope / backstage / check_scaffold を受け取らない。"""
        import inspect

        params = set(inspect.signature(resolve_stance).parameters)
        for forbidden in ("discuss_scope", "backstage", "check_scaffold", "allowed_document_ids"):
            assert forbidden not in params


# ===========================================================================
# 3. build_stance_dto — 語彙・ラベル・数値非含有（LC7）
# ===========================================================================


class TestBuildStanceDto:
    @pytest.mark.parametrize("stance", STANCES)
    def test_every_stance_has_a_label_from_label_vocab(self, stance):
        dto = build_stance_dto(stance, SOURCE_INFERRED)
        assert dto == {
            "stance": stance,
            "source": SOURCE_INFERRED,
            "label": LEARNING_STANCE_LABELS[stance],
        }

    def test_unknown_vocabulary_yields_none(self):
        assert build_stance_dto("bogus", SOURCE_EXPLICIT) is None
        assert build_stance_dto(STANCE_TUTOR, "guessed") is None

    @pytest.mark.parametrize("stance", STANCES)
    @pytest.mark.parametrize("source", STANCE_SOURCES)
    def test_dto_has_no_numeric_values(self, stance, source):
        dto = build_stance_dto(stance, source)
        assert set(dto) == {"stance", "source", "label"}
        assert all(isinstance(v, str) for v in dto.values())

    def test_label_table_covers_exactly_the_vocabulary(self):
        assert set(LEARNING_STANCE_LABELS) == set(STANCES)
