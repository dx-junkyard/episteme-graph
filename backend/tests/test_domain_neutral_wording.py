"""学習者可視文言・生成プロンプトの分野非依存ガードレール（2026-09-05）。

このシステムは分野を限定しない（分野固有の語彙・ルールは
``backend/cartridges/<cartridge_id>/`` から読む — 開発ルール7）。にもかかわらず、
学習者に見える文言と LLM プロンプトに「物理学」「素粒子物理学・場の理論・有効理論」が
直書きされており、経済学・生物学などのコースでも AI が自分を「物理学の学習支援AI」と
名乗り、物理の一般知識で補完するよう指示されていた。

固定する不変条件:
  1. 対象ファイルのコード・文字列（コメント行を除く）に分野名を直書きしない。
  2. 分野を語る必要がある箇所は、コース名（``course_title``）かカートリッジ由来の
     語彙から導く／中立表現にする。
  3. 使い方質問の判定（``_is_usage_question``）が持つ「教材内容らしさ」語彙は
     分野非依存語のみで、分野固有語はカートリッジ ontology から読む。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 学習者に見える文言・LLM プロンプトを持つファイル（担当範囲）。
_TARGET_FILES = (
    "api/routes/learning.py",
    "api/routes/lecture_studio/scripts.py",
    "api/routes/lecture_studio/topics.py",
    "api/routes/lecture_studio/pipeline.py",
    "api/routes/theory_components.py",
    "core/theory_components.py",
    "core/course_content_builder.py",
)

# 直書きを禁じる分野名。``backend/cartridges/**`` は分野定義そのものなので対象外。
_FORBIDDEN_DOMAIN_WORDS = ("物理", "素粒子", "場の理論", "有効理論")


def _code_lines(rel_path: str) -> list[tuple[int, str]]:
    """``#`` コメント行を除いた (行番号, 行) を返す。

    コメントは実行時の出力に出ないため対象外（この規約自体を説明する注記が
    分野名に言及できないと、規約の意図を書き残せない）。
    """
    text = (BACKEND / rel_path).read_text(encoding="utf-8")
    return [
        (idx, line)
        for idx, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("rel_path", _TARGET_FILES)
def test_no_hardcoded_field_names(rel_path):
    hits = [
        f"{rel_path}:{lineno}: {line.strip()}"
        for lineno, line in _code_lines(rel_path)
        for word in _FORBIDDEN_DOMAIN_WORDS
        if word in line
    ]
    assert not hits, (
        "学習者可視文言・プロンプトに分野名が直書きされている（分野はコース名・"
        "カートリッジから導くか、中立表現にすること）:\n" + "\n".join(hits)
    )


class TestChitChatWording:
    """CHIT_CHAT 分岐の分野中立性（入口統合 Phase 1 で検査対象が移った）。

    旧: 拒否文（「私は{分野}の学習支援に特化したAIです」）がコース名・中立表現から
    組み立てられていること。
    新: 拒否文そのものが廃止され、CHIT_CHAT は casual_light 様相として通常フローへ
    合流する（``docs/features/learning_chat_entry_unification_design.md`` §4.3）。
    分野語ハードコード禁止の趣旨は、合流先で学習者に見える文言 — すなわち様相
    ラベル（``LEARNING_STANCE_LABELS``）と casual プロンプト — へ引き継ぐ。
    """

    def test_chit_chat_merges_into_casual_light_instead_of_refusing(self):
        src = (BACKEND / "api/routes/learning.py").read_text(encoding="utf-8")
        idx = src.find('if intent == "CHIT_CHAT":')
        assert idx > 0
        block = src[idx: idx + 200]
        # 合流の実体は _is_casual の再代入だけ（下流は無改変 = LC8）。
        assert "_is_casual = True" in block
        # 拒否文は残っていない（分野中立かどうか以前に、文言自体が無い）。
        # コメント行は対象外 — 廃止の経緯をコード注記に書き残せなくなるため
        # （このファイルの _code_lines と同じ規約）。
        code = "\n".join(line for _, line in _code_lines("api/routes/learning.py"))
        assert "学習支援に特化したAI" not in code
        assert "_scope_label" not in code

    def test_stance_labels_have_no_hardcoded_field_names(self):
        """様相ラベルは学習者に直接見える文言なので、分野名を含まないこと。"""
        from core.label_vocab import LEARNING_STANCE_LABELS

        for key, label in LEARNING_STANCE_LABELS.items():
            for word in _FORBIDDEN_DOMAIN_WORDS:
                assert word not in label, f"様相ラベルに分野名が入っている: {key}={label!r}"


class TestUsageQuestionVocabulary:
    def test_content_terms_are_domain_neutral(self):
        import api.routes.learning as learning_mod

        for term in learning_mod._CONTENT_QUESTION_TERMS:
            for word in _FORBIDDEN_DOMAIN_WORDS:
                assert word not in term, f"分野固有語が語彙に混ざっている: {term!r}"

    def test_domain_terms_come_from_cartridge(self):
        """分野固有語はカートリッジ ontology から読む（同梱カートリッジで実証）。"""
        import api.routes.learning as learning_mod

        terms = learning_mod._cartridge_content_terms("particle_physics")
        assert terms, "同梱カートリッジから分野語彙を取得できていない"
        assert all(len(t) >= learning_mod._CARTRIDGE_TERM_MIN_LEN for t in terms), (
            "短すぎる別名（例: 'SM'）は部分一致の誤爆源なので除外すること"
        )

    def test_unknown_cartridge_degrades_silently(self):
        import api.routes.learning as learning_mod

        assert learning_mod._cartridge_content_terms("") == ()
        assert learning_mod._cartridge_content_terms("no_such_cartridge_xyz") == ()

    def test_usage_question_still_rejects_content_questions(self):
        import api.routes.learning as learning_mod

        # 分野非依存語（方程式・式）による誤爆ガードは維持される
        assert learning_mod._is_usage_question("運動方程式の使い方") is False
        assert learning_mod._is_usage_question("この式はどう使うの") is False
        # 画面・操作の質問は従来どおり真
        assert learning_mod._is_usage_question("音声モードの使い方がわからない") is True

    def test_cartridge_terms_reject_domain_specific_content_question(self):
        """分野固有語（カートリッジ由来）だけが内容語である質問も偽に倒せる。"""
        import api.routes.learning as learning_mod

        message = "Standard Modelの使い方"
        assert learning_mod._is_usage_question(message) is True
        assert learning_mod._is_usage_question(message, cartridge_id="particle_physics") is False
