"""鏡面化 move（EX-3b 裁定 / 案B-lite）のガードレール。

正本: ``docs/features/seminar_brief_mirroring_design.md`` §2 / §3 精査記録①②③。

検証すること:
- ``_get_discuss_system_prompt`` に 〔鏡〕 契約フレーズが逐語存在する（逐語引用・
  そのまま（言い換えずに）・持ち込まず・能力/傾向/人物像の禁止）。指示は既存ルール2・3の
  **内部**に追記されており、番号を増やしていない（§3 精査②）。
- 質問 move（ルール1・即答）は鏡で置換されていない（EX-3b ①）。
- 既存の契約フレーズ（「ルール1を優先」・DA6 の11フレーズ・足場分岐の切り出しキー・
  ``window_history`` の字面・関数シグネチャ）が不変であること。
- ``core/discuss/mirroring.py::extract_mirror`` の純関数挙動（verbatim 合格/不合格/
  マーカーなし/複数マーカー/引用ゼロの純合成禁止/all-quotes 判定=捏造混在の不合格/
  1文字引用の不合格/閉じマーカー欠落時のマーカー剥がし/ネスト時の鏡文非汚染）。
- 窓の外への持ち出しの禁止（EX-3b ④・§3 精査③）: 鏡文が interest_traces / 専用テーブル /
  assistant_meta へ流れる経路が構造的に無いこと（痕跡記録・履歴保存は鏡抽出より**前**に
  完了している）。mirroring.py が FastAPI / LLM / DB を import しないこと。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.discuss.mirroring import extract_mirror  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_MIRRORING_SRC = (BACKEND / "core" / "discuss" / "mirroring.py").read_text(encoding="utf-8")
_SCHEMAS_SRC = (BACKEND / "api" / "schemas.py").read_text(encoding="utf-8")


def _discuss_prompt_body() -> str:
    return _LEARNING_SRC.split("def _get_discuss_system_prompt(")[1].split("\ndef ")[0]


# ===========================================================================
# 1. プロンプト契約（〔鏡〕 の指示がルール2・3の内部にあること）
# ===========================================================================


class TestMirrorPromptContract:
    def test_mirror_marker_form_is_specified(self):
        body = _discuss_prompt_body()
        assert "〔鏡〕" in body
        assert "〔/鏡〕" in body
        assert "で合っていますか" in body  # 推量形 + 確認（EX-3b ③）

    def test_verbatim_quote_is_required(self):
        """EX-3b ②: 本人発話の逐語引用を必須にする（純合成の禁止）。"""
        body = _discuss_prompt_body()
        assert "学生の直前の発話からの逐語引用" in body
        assert "そのまま（言い換えずに）引用" in body

    def test_no_external_knowledge_inside_mirror(self):
        """EX-3b ⑦: 一般知識の持ち込み禁止は鏡 move の内部制約に限定。"""
        body = _discuss_prompt_body()
        assert "持ち込まず" in body
        assert "論文の内容・一般知識・教科書的な正解" in body

    def test_mirror_reflects_utterance_not_person(self):
        """鏡が映してよいのは発話であって能力・傾向ではない。"""
        body = _discuss_prompt_body()
        assert "学生の能力・傾向・人物像について述べてはいけません" in body

    def test_rule_numbering_is_not_extended(self):
        """§3 精査②: 鏡の指示は既存ルール2・3の内部に追記（番号を増やさない）。"""
        body = _discuss_prompt_body()
        assert "8. 【数値を見せない】" in body
        assert re.search(r"^9\. 【", body, re.MULTILINE) is None

    def test_question_move_is_not_replaced_by_mirror(self):
        """EX-3b ①: 発動は解釈表明・詰まりのみ。質問への即答（ルール1）は非改変。"""
        body = _discuss_prompt_body()
        rule1 = body.split("1. 【質問には即答・出し惜しみ禁止】")[1].split("2. 【解釈には言い直しから】")[0]
        assert "〔鏡〕" not in rule1
        # 鏡の指示はルール2（言い直し）とルール3（足場かけ）の内部にある
        rule2 = body.split("2. 【解釈には言い直しから】")[1].split("3. 【詰まりには一点だけの足場かけ】")[0]
        rule3 = body.split("3. 【詰まりには一点だけの足場かけ】")[1].split("4. 【質問と解釈が同居するとき】")[0]
        assert "〔鏡〕" in rule2
        assert "〔鏡〕" in rule3


class TestExistingDiscussContractsUnchanged:
    """鏡面化の追記が既存テスト契約を1つも壊していないこと（追記のみの保証）。"""

    def test_function_signature_unchanged(self):
        assert (
            "def _get_discuss_system_prompt(domain: str, response_persona: str | None = None) -> str:"
            in _LEARNING_SRC
        )

    def test_rule1_priority_phrase_unchanged(self):
        body = _discuss_prompt_body()
        assert "ルール1を優先" in body
        assert "質問を保留にして言い直しから始めることはしないでください" in body

    def test_da6_contract_phrases_unchanged(self):
        body = _discuss_prompt_body()
        for phrase in (
            "すぐに答えて", "出し惜しみ", "雑談調にはしないでください", "LaTeX", "出典",
            "必ず", "確率的な付加は不可", "言い換え", "why / how",
            "この論文に書かれている内容ではなく", "数値スコア",
        ):
            assert phrase in body, f"DA6 契約フレーズが欠落: {phrase}"

    def test_scaffold_branch_keys_unchanged(self):
        assert "if _is_discuss:\n        _scaffold_user_instruction = (" in _LEARNING_SRC
        assert "messages: list[dict] = [" in _LEARNING_SRC

    def test_history_window_literal_unchanged(self):
        assert "window_history(body.history, max_messages=20, max_chars=2000)" in _LEARNING_SRC


# ===========================================================================
# 2. extract_mirror の純関数挙動
# ===========================================================================


class TestExtractMirror:
    _MSG = "量子もつれは古典相関と同じだと思います"

    def test_verbatim_pass_extracts_mirror_and_cleans_body(self):
        answer = (
            "〔鏡〕あなたは「量子もつれは古典相関と同じ」と捉えている、で合っていますか？〔/鏡〕\n"
            "論文の主張と突き合わせましょう。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror == {
            "text": "あなたは「量子もつれは古典相関と同じ」と捉えている、で合っていますか？"
        }
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean
        assert clean == "論文の主張と突き合わせましょう。"

    def test_verbatim_fail_strips_markers_and_keeps_text(self):
        """不合格時は鏡扱いせずマーカーだけ剥がして本文に残す（縮退・再生成なし）。"""
        answer = "〔鏡〕あなたは「まったく別の言い換え」と捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean
        assert "まったく別の言い換え" in clean  # 情報を落とさない
        assert "本文。" in clean

    def test_mirror_without_any_quote_is_rejected(self):
        """引用ゼロの純合成は禁止（EX-3b ②）。"""
        answer = "〔鏡〕あなたはそう捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔鏡〕" not in clean

    def test_mixed_verbatim_and_fabricated_quotes_are_rejected(self):
        """all-quotes 判定: 逐語引用1つに捏造引用を混ぜた鏡は全体が不合格。"""
        answer = (
            "〔鏡〕あなたは「量子もつれ」を「隠れた変数の産物」と捉えている、"
            "で合っていますか？〔/鏡〕本文。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean
        assert "隠れた変数の産物" in clean  # 情報を落とさない

    def test_all_verbatim_multiple_quotes_pass(self):
        """すべての引用が逐語（2文字以上）なら複数引用でも合格。"""
        answer = (
            "〔鏡〕あなたは「量子もつれ」を「古典相関と同じ」と捉えている、"
            "で合っていますか？〔/鏡〕本文。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is not None
        assert clean == "本文。"

    def test_single_char_quote_is_rejected(self):
        """1文字だけの引用は逐語証拠として弱すぎるため不合格（最短2文字）。"""
        answer = "〔鏡〕あなたは「量」と捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔鏡〕" not in clean

    def test_single_char_quote_mixed_with_verbatim_is_rejected(self):
        """逐語の長い引用があっても、1文字引用が混ざれば all-quotes で不合格。"""
        answer = (
            "〔鏡〕あなたは「量子もつれは古典相関と同じ」の「量」に注目している、"
            "で合っていますか？〔/鏡〕本文。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None

    def test_no_marker_returns_answer_unchanged(self):
        answer = "マーカーの無い通常の応答です。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert clean == answer

    def test_only_first_marker_is_structured(self):
        answer = (
            "〔鏡〕あなたは「量子もつれは古典相関と同じ」と捉えている、で合っていますか？〔/鏡〕"
            "本文。〔鏡〕二つ目「量子もつれ」の鏡。〔/鏡〕続き。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is not None
        assert mirror["text"].startswith("あなたは「量子もつれは古典相関と同じ」")
        # 2個目は構造化されず、マーカーのみ剥がして本文に残る（生マーカーを見せない）
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean
        assert "二つ目「量子もつれ」の鏡。" in clean

    def test_surrounding_whitespace_in_quote_is_tolerated(self):
        """許容する正規化は前後空白のみ（内部の言い換えは不合格のまま）。"""
        answer = "〔鏡〕あなたは「 量子もつれは古典相関と同じ 」と捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is not None

    def test_dotall_mirror_spanning_newlines(self):
        answer = "〔鏡〕あなたは「量子もつれは古典相関と同じ」\nと捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is not None
        assert "本文。" == clean

    def test_empty_learner_message_never_validates(self):
        answer = "〔鏡〕あなたは「」と捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, "")
        assert mirror is None

    def test_unclosed_marker_is_stripped_from_answer(self):
        """閉じマーカー欠落（ペア不成立）でも生マーカーを学習者に見せない。"""
        answer = "〔鏡〕あなたは「量子もつれ」と捉えている、で合っていますか？ 本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean
        assert "あなたは「量子もつれ」と捉えている" in clean  # 情報を落とさない

    def test_orphan_close_marker_is_stripped_from_answer(self):
        """開きマーカー欠落（閉じだけ残存）でも生マーカーを学習者に見せない。"""
        answer = "あなたは「量子もつれ」と捉えている、で合っていますか？〔/鏡〕本文。"
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is None
        assert "〔/鏡〕" not in clean

    def test_nested_marker_does_not_leak_into_mirror_text(self):
        """ネスト等の残存マーカーは鏡文（mirror.text）にも混入させない。"""
        answer = (
            "〔鏡〕あなたは「量子もつれは古典相関と同じ」〔鏡〕と捉えている、"
            "で合っていますか？〔/鏡〕本文。"
        )
        clean, mirror = extract_mirror(answer, self._MSG)
        assert mirror is not None
        assert "〔鏡〕" not in mirror["text"] and "〔/鏡〕" not in mirror["text"]
        assert "〔鏡〕" not in clean and "〔/鏡〕" not in clean


# ===========================================================================
# 3. 窓の外への持ち出しの禁止（構造検査）
# ===========================================================================


class TestNoMirrorPersistence:
    def test_mirroring_module_is_pure(self):
        """mirroring.py は FastAPI / LLM / DB を import しない純関数モジュール。"""
        assert_source_does_not_import(
            _MIRRORING_SRC,
            ("fastapi", "core.llm", "openai", "sqlalchemy", "core.postgres"),
            context="core/discuss/mirroring.py",
        )
        assert_source_forbids(
            _MIRRORING_SRC, ("INSERT INTO", "UPDATE ", "DELETE FROM", "interest_traces"),
            context="core/discuss/mirroring.py",
        )

    def test_mirror_extraction_happens_after_persistence_and_trace(self):
        """痕跡記録（record_interest_trace）と履歴保存（persist_chat_history）は鏡抽出より
        **前**に完了している = 鏡文が痕跡・履歴メタへ流れる経路が構造的に無い
        （会話履歴 JSONB に生 answer がマーカー込みで残るのは §3 精査③が許容する
        窓内再注入であり、ここで固定するのは**窓外**経路の不在）。"""
        chat_fn = _LEARNING_SRC.split("def learning_chat(")[1].split("\n@router")[0]
        mirror_pos = chat_fn.index("extract_mirror(clean_answer, body.message)")
        assert chat_fn.index("_persisted = persist_chat_history(") < mirror_pos
        assert chat_fn.index("record_interest_trace(") < mirror_pos

    def test_mirror_is_discuss_only_and_wired_to_response_field(self):
        chat_fn = _LEARNING_SRC.split("def learning_chat(")[1].split("\n@router")[0]
        assert "if _is_discuss and not degraded:" in chat_fn
        assert "clean_answer, _mirror = extract_mirror(clean_answer, body.message)" in chat_fn
        assert "mirror=_mirror" in chat_fn

    def test_mirror_never_appears_as_a_payload_key(self):
        """learning.py に文字列キー "mirror" が無い = 痕跡 payload / assistant_meta /
        専用テーブルへの書き込みキーとして使われていない（応答 DTO の kwarg のみ）。"""
        assert '"mirror"' not in _LEARNING_SRC
        assert "'mirror'" not in _LEARNING_SRC

    def test_response_schema_has_optional_mirror_field(self):
        assert "mirror: dict | None = None" in _SCHEMAS_SRC
