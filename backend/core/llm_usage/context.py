"""帰属（attribution）伝搬 — contextvars ベースの UsageContext。

呼び出し側のシグネチャを変えずに「どの機能の呼び出しか」を core/llm.py のフックまで
伝えるためのコンテキストマネージャ（設計書 §6）。

contextvars はスレッドをまたがない。パイプライン orchestrator や各 worker がスレッドを
起こす場合は、スレッド起動側で文脈を取得しスレッド内で再セットすること（設計書 §6 注記）。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Iterator

from core.llm_usage.schema import UNATTRIBUTED


@dataclass(frozen=True)
class UsageContext:
    """現在の LLM 呼び出しの帰属先。frozen — with ブロック内で書き換えず都度差し替える。"""

    feature: str = UNATTRIBUTED
    user_id: str | None = None
    document_id: str | None = None
    course_id: str | None = None
    run_id: str | None = None


_DEFAULT_CONTEXT = UsageContext()

_current_context: ContextVar[UsageContext] = ContextVar(
    "llm_usage_current_context", default=_DEFAULT_CONTEXT
)


@contextmanager
def usage_context(
    feature: str,
    *,
    user_id: str | None = None,
    document_id: str | None = None,
    course_id: str | None = None,
    run_id: str | None = None,
) -> Iterator[UsageContext]:
    """帰属コンテキストを設定する。ネスト時は内側が勝つ。

    None の帰属 ID（user_id / document_id / course_id / run_id）は外側（呼び出し時点）の
    値を引き継ぐ（例: 外側で document_id をセットしていれば、内側で明示指定しない限り
    そのまま伝播する）。feature は常に呼び出し時の値で上書きする。
    """
    outer = _current_context.get()
    new_context = UsageContext(
        feature=feature if feature is not None else outer.feature,
        user_id=user_id if user_id is not None else outer.user_id,
        document_id=document_id if document_id is not None else outer.document_id,
        course_id=course_id if course_id is not None else outer.course_id,
        run_id=run_id if run_id is not None else outer.run_id,
    )
    token = _current_context.set(new_context)
    try:
        yield new_context
    finally:
        _current_context.reset(token)


def current_usage_context() -> UsageContext:
    """現在の UsageContext を返す。未設定なら ``UsageContext()``（feature='unattributed'）。"""
    return _current_context.get()


def bind_usage_context(
    feature: str,
    *,
    user_id: str | None = None,
    document_id: str | None = None,
    course_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """現在のスレッドに UsageContext を直接セットする（reset しない）。

    pipeline orchestrator や各 worker のような**専用スレッド**（1回の実行で使い捨てる
    daemon thread）の冒頭で使う。スレッドが再利用される文脈（FastAPI のリクエスト
    ハンドラは threadpool 上で動く）では**使ってはならない** — コンテキストが次の
    リクエストへ漏れる。リクエスト処理では必ず ``usage_context()`` を使うこと。
    """
    _current_context.set(
        UsageContext(
            feature=feature,
            user_id=user_id,
            document_id=document_id,
            course_id=course_id,
            run_id=run_id,
        )
    )


def set_current_feature(feature: str) -> None:
    """現在の UsageContext の feature だけを差し替える（帰属 ID は維持、リセットしない）。

    pipeline orchestrator のように「同一スレッド内で feature が順に変わる」用途向け
    （ステージ進行の report ヘルパーから ``set_current_feature("pipeline:<stage>")`` を
    呼ぶ）。``usage_context()`` コンテキストマネージャと併用でき、次の
    ``set_current_feature`` か外側の with ブロック終了（``reset(token)``）まで有効。
    """
    _current_context.set(replace(_current_context.get(), feature=feature))
