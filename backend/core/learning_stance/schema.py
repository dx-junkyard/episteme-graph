"""学習チャットの「様相（stance）」語彙の**正本**（Phase 1 入口統合）。

正本設計書: ``docs/features/learning_chat_entry_unification_design.md``（LC1〜LC8）。

様相は「どう話すか」（会話の調子）だけを表す軸で、**検索範囲（discuss_scope）・
出題モード（cycle_mode）・記録の私有化（backstage）・確認問題の壁打ち
（check_scaffold）とは無関係**である（LC1）。このモジュールはその語彙と、
リクエストの明示状態から様相を決める純関数だけを持つ。

方針:

* **純データ + 純関数のみ**（FastAPI / sqlalchemy / core.llm / A層 agents を
  import しない）。表示ラベルの正本は ``core/label_vocab.py`` の
  :data:`~core.label_vocab.LEARNING_STANCE_LABELS` で、ここは語彙（enum）だけを持つ。
* **数値を持たない**（LC7）。confidence・一致度・推定の当たり外れは
  DTO にも痕跡にも入れない。
* **推定は「様相」だけ**（LC1）。:func:`resolve_stance` は
  ``discuss_scope`` / ``cycle_mode`` / ``backstage`` / ``check_scaffold`` を
  **読むだけで書かない**（引数として受け取らない設計にしてある）。

ガードレールは ``backend/tests/test_learning_stance_{core,routing,guardrails}.py``。
"""

from __future__ import annotations

__all__ = [
    "STANCES",
    "STANCE_SOURCES",
    "STANCE_CASUAL_LIGHT",
    "STANCE_CYCLE_DIFF",
    "STANCE_CYCLE_ELICIT",
    "STANCE_DISCUSS",
    "STANCE_TUTOR",
    "SOURCE_EXPLICIT",
    "SOURCE_INFERRED",
    "build_stance_dto",
    "resolve_stance",
]


#: 様相（会話の調子）。``usage_help`` / ``backstage`` は**この軸に載せない** —
#: 前者は HELP pre-route で早期 return し、後者は記録の私有化であって調子ではない。
STANCE_TUTOR = "tutor"
STANCE_CASUAL_LIGHT = "casual_light"
STANCE_DISCUSS = "discuss"
STANCE_CYCLE_ELICIT = "cycle_elicit"
STANCE_CYCLE_DIFF = "cycle_diff"

STANCES: tuple[str, ...] = (
    STANCE_TUTOR,
    STANCE_CASUAL_LIGHT,
    STANCE_DISCUSS,
    STANCE_CYCLE_ELICIT,
    STANCE_CYCLE_DIFF,
)

#: 様相がどう決まったか。``explicit`` = 学習者・UI が明示した / ``inferred`` =
#: 当該発話からサーバが読んだ（LC6: 推定したことは隠さず事実として返す）。
SOURCE_EXPLICIT = "explicit"
SOURCE_INFERRED = "inferred"

STANCE_SOURCES: tuple[str, ...] = (SOURCE_EXPLICIT, SOURCE_INFERRED)


def resolve_stance(
    *,
    cycle_mode: str | None = None,
    is_discuss: bool = False,
    is_casual: bool = False,
    explicit_casual: bool = False,
    has_typed_action: bool = False,
    has_atlas_context: bool = False,
) -> tuple[str, str]:
    """当該往復の様相と、その出所を返す純関数（``(stance, source)``）。

    優先順位（設計 §4.2 / 契約 §3）:

    1. ``cycle_mode``（elicit / diff）— 常に明示（UC1: ELICIT-first は opt-in）
    2. ``is_discuss`` — 常に明示（DM1: 範囲の変更を伴うので推定しない）
    3. ``is_casual`` — ``explicit_casual`` が真なら明示、偽なら推定
       （CHIT_CHAT 判定から合流した casual_light）
    4. それ以外は ``tutor``。typed action / 地図アクション由来なら明示、
       自然文なら推定（既定の様相を「読んだ結果」として正直に返す）

    未知の ``cycle_mode`` は無視する（値検証は呼び出し側の 422 precheck が担う）。
    この関数は ``discuss_scope`` / ``backstage`` / ``check_scaffold`` を
    引数に取らない — **様相はそれらを切り替えない**（LC1）。
    """
    mode = (cycle_mode or "").strip()
    if mode == "elicit":
        return STANCE_CYCLE_ELICIT, SOURCE_EXPLICIT
    if mode == "diff":
        return STANCE_CYCLE_DIFF, SOURCE_EXPLICIT
    if is_discuss:
        return STANCE_DISCUSS, SOURCE_EXPLICIT
    if is_casual:
        return (
            STANCE_CASUAL_LIGHT,
            SOURCE_EXPLICIT if explicit_casual else SOURCE_INFERRED,
        )
    return (
        STANCE_TUTOR,
        SOURCE_EXPLICIT if (has_typed_action or has_atlas_context) else SOURCE_INFERRED,
    )


def build_stance_dto(stance: str, source: str) -> dict | None:
    """レスポンス用の様相 DTO を組み立てる（語彙外なら ``None``）。

    ``{"stance": ..., "source": ..., "label": ...}`` の3キーだけ。**数値キーを
    持たない**（LC7）。表示ラベルは ``core/label_vocab.py`` の正本から引く
    （フロントに日本語表を持たせないため、サーバが解決済みの文字列を返す）。
    """
    # 遅延 import: 語彙（このモジュール）とラベル（label_vocab）の依存方向を
    # 一方向に保ちつつ、循環を作らない。
    from core.label_vocab import LEARNING_STANCE_LABELS

    if stance not in STANCES or source not in STANCE_SOURCES:
        return None
    return {
        "stance": stance,
        "source": source,
        "label": LEARNING_STANCE_LABELS[stance],
    }
