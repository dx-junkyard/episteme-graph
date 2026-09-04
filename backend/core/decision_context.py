"""確定文脈の記帳（`decision_context`）— 一括確定を**再構成可能**にする共通プリミティブ。

正本設計書: ``docs/features/decision_context_design.md``。
上位の根拠: ``docs/vision.md`` §4 改訂原則1（2026-09-04）—

    AI は生成と検査を担いうる。確定は、十分な能力・情報・時間・拒否権を持つ人間の
    判断を含み、後から再構成・異議申立できる手続にのみ与える。

人間のクリックは、それ自体では正当性ではない。**何が提示され / 何を選べて / 断ることが
できて / 誰がどこから覆せるか**が記帳されて初めて、その確定は後から再構成できる
（automation bias と moral crumple zone への構造的な対処）。本モジュールは監査
metadata に載せるその1ブロックを組み立てるだけで、DB にも HTTP にも触らない。

不変条項:

- **DC1 一括確定は `decision_context` 無しに記帳しない** — 「次へ＝承認」型の一括確定
  経路は、監査 metadata に本ブロックを必ず含める。ガードレール
  ``backend/tests/test_decision_context_guardrails.py`` が各経路のソースを検査する。
- **DC2 提示と適用を分けて記帳し、一致を偽らない** — ``presented`` と ``applied`` は
  別のキーで持ち、その一致は :func:`build_decision_context` が集合比較で導出する
  （呼び出し側が「一致した」と申告できない）。表示上限で切り詰めた事実は
  ``truncated`` で正直に出す。
- **DC3 代替が無い確定は記帳できない** — ``alternatives_available`` が空なら
  :class:`ValueError`。却下・再検討・後回しのいずれも無い「確定」はゴム印であって
  判断ではなく、判断として記帳してはならない。``decline_possible`` は引数ではなく
  導出値（常に ``True``）で、「断れなかった確定」を本プリミティブでは表現しない。
- **DC4 来歴申告（`client_reported`）はサーバ導出値と混ぜない** — 「画面に何を出して
  いたか」はクライアントの自己申告であり、サーバが検証できない。
  ``core/teacher_triage.py::sort_metadata`` が ``sort_order`` を未指定なら載せない
  （``default`` と偽装しない）のと同じ流儀で、申告は独立キーに隔離する。

数値（``confidence`` / ``weight`` / ``score``）は載せない（原則4）。件数は
「提示・適用の各件数」という事実に限る（スコア・達成率にしない）。

本モジュールは純データ + 純関数（FastAPI / sqlalchemy / LLM 非 import）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = [
    "ALTERNATIVES",
    "ALT_DESELECT",
    "ALT_DISMISS",
    "ALT_EDIT",
    "ALT_RECONSIDER",
    "ALT_REJECT",
    "ALT_SKIP_STEP",
    "BASIS_EXPLANATION_REVIEW_BULK",
    "BASIS_RELEASE_REVIEW_PLACEMENTS",
    "DECISION_CONTEXT_KEY",
    "PRESENTED_IDS_MAX",
    "REOPEN_ACTOR_TEACHER",
    "attach_decision_context",
    "build_decision_context",
]


#: 監査 metadata へ載せるときのキー（記帳側・検査側で共有する唯一の綴り）。
DECISION_CONTEXT_KEY = "decision_context"

# ---------------------------------------------------------------------------
# basis 語彙（どの画面のどの一括確定か）
# ---------------------------------------------------------------------------

#: リリース前の確認ウィザード ステップ2「この配置で次へ」（RR2）。
BASIS_RELEASE_REVIEW_PLACEMENTS = "release_review.placements"
#: 説明レビューキューの一括承認・一括却下（E2）。
BASIS_EXPLANATION_REVIEW_BULK = "explanation_review.bulk"

# ---------------------------------------------------------------------------
# 代替（確定者がその場で選べた「承認しない」選択肢）
# ---------------------------------------------------------------------------

ALT_REJECT = "reject"            # 却下（受講者に出さない）
ALT_RECONSIDER = "reconsider"    # 再検討（確認待ちとして残す）
ALT_DISMISS = "dismiss"          # 却下（候補を保持したまま見送る）
ALT_EDIT = "edit"                # 本文を編集してから確定する
ALT_SKIP_STEP = "skip_step"      # このステップを飛ばす（「あとで」）
ALT_DESELECT = "deselect"        # 選択から外す（一括の対象にしない）

#: 記帳できる代替の全語彙（未知の値は :class:`ValueError`）。
ALTERNATIVES = (
    ALT_DESELECT,
    ALT_DISMISS,
    ALT_EDIT,
    ALT_RECONSIDER,
    ALT_REJECT,
    ALT_SKIP_STEP,
)

#: 再審を開始できる主体。v1 は教員のみ（学習者からの異議申立は vision §9 の未実装項目）。
REOPEN_ACTOR_TEACHER = "teacher"

#: ``presented`` / ``applied`` に列挙する id の上限（監査行の肥大を避ける）。
#: 超過分は列挙しないが、件数（``count``）と ``truncated`` で事実は落とさない。
PRESENTED_IDS_MAX = 200


def _normalize_ids(values: Iterable[Any] | None) -> list[str]:
    """id 列を「空でない文字列・重複なし」に正規化する（順序は入力順を保つ）。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _id_block(ids: list[str]) -> dict:
    """``{"count", "ids", "truncated"}``（DC2: 切り詰めを隠さない）。"""
    capped = sorted(ids)[:PRESENTED_IDS_MAX]
    return {
        "count": len(ids),
        "ids": capped,
        "truncated": len(ids) > PRESENTED_IDS_MAX,
    }


def build_decision_context(
    *,
    basis: str,
    presented_ids: Iterable[Any] | None,
    applied_ids: Iterable[Any] | None,
    alternatives: Iterable[Any] | None,
    reopen_path: str,
    reopen_statuses: Iterable[Any] = (),
    evidence_shown: bool | None = None,
    client_reported: Mapping[str, Any] | None = None,
) -> dict:
    """確定文脈の1ブロックを組み立てる（JSON シリアライズ可能な dict）。

    引数:
        basis: どの一括確定か（``BASIS_*``）。空は :class:`ValueError`。
        presented_ids: 確定者に**提示されていた**対象の id 列（サーバ導出）。
        applied_ids: 実際に**適用された**対象の id 列。
        alternatives: その場で選べた代替（``ALT_*``）。**空は** :class:`ValueError`
            （DC3: 代替の無い確定は判断として記帳しない）。
        reopen_path: 再審の経路（HTTP メソッド + パス）。空は :class:`ValueError`。
        reopen_statuses: 再審で戻せる status 語彙。戻せないなら空のまま
            （「戻せる」と偽らない — DC2）。
        evidence_shown: 根拠（逐語引用）が画面に出ていたか。``None`` は**不明**で、
            確認していないものを ``True`` にしない。
        client_reported: クライアントの来歴申告（サーバが検証していない値）。
            未指定・空なら ``None`` のまま載せる（DC4）。

    ``presented_matches_applied`` は**切り詰め前の集合**で比較する（表示上限の副作用で
    一致判定が変わらないようにする）。
    """
    basis_norm = str(basis or "").strip()
    if not basis_norm:
        raise ValueError("basis is required")
    reopen_norm = str(reopen_path or "").strip()
    if not reopen_norm:
        raise ValueError("reopen_path is required")

    alt_norm = sorted({str(a or "").strip() for a in (alternatives or []) if str(a or "").strip()})
    unknown = [a for a in alt_norm if a not in ALTERNATIVES]
    if unknown:
        raise ValueError(f"unknown alternatives: {unknown!r} (must be in {ALTERNATIVES!r})")
    if not alt_norm:
        # DC3: 却下・再検討・後回しのいずれも無い「確定」は、判断ではなくゴム印である。
        raise ValueError(
            "alternatives_available must not be empty "
            "(a confirmation with no alternative is not a decision)"
        )

    presented = _normalize_ids(presented_ids)
    applied = _normalize_ids(applied_ids)

    reported: dict | None = None
    if client_reported:
        reported = {str(k): v for k, v in dict(client_reported).items()}

    return {
        "basis": basis_norm,
        "presented": _id_block(presented),
        "applied": _id_block(applied),
        "presented_matches_applied": set(presented) == set(applied),
        "alternatives_available": alt_norm,
        # DC3: 導出値（引数にしない）。断れない確定は本プリミティブで表現しない。
        "decline_possible": True,
        "reopen": {
            "path": reopen_norm,
            "statuses": [
                str(s or "").strip() for s in (reopen_statuses or []) if str(s or "").strip()
            ],
            "actor": REOPEN_ACTOR_TEACHER,
        },
        "evidence_shown": evidence_shown,
        "client_reported": reported,
    }


def attach_decision_context(metadata: dict, ctx: dict) -> dict:
    """監査 metadata に確定文脈を足した**新しい dict** を返す（引数を破壊しない）。

    ``core/teacher_triage.py::sort_metadata`` と違い元 dict を書き換えないのは、
    1リクエストで複数行を記帳する経路（一括確定はまさにそれ）で同じ ctx を安全に
    使い回すため。
    """
    merged = dict(metadata or {})
    merged[DECISION_CONTEXT_KEY] = ctx
    return merged
