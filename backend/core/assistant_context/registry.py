"""画面文脈アダプター — 解決器レジストリと事実文ブロックの描画。

設計: ``docs/features/assistant_screen_adapter_design.md`` §4.3。

- ``register(screen, kind, resolver)``: 画面 ID ごとに解決器を登録する。
- ``resolve(ctx, sources)``: 登録順に解決器を適用し、事実文を連結して返す。
  1つの解決器が例外を投げても**その解決器の facts だけが欠ける**（SA2 fail-soft）。
- ``render_block(facts)``: 固定ヘッダ付きの独立ブロックにする（SA7）。空なら ``""``。

``sources`` の組み立て（権限ゲート付きの取得）は route の責務で、本モジュールは
DB も LLM も触らない（SA3）。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .schema import BLOCK_HEADER, MAX_BLOCK_CHARS, TRUNCATION_LINE, ScreenContext

#: 解決器の型。入力を mutate せず・例外を出さず・事実文の列を返す純関数。
Resolver = Callable[[ScreenContext, Mapping[str, Any]], list[str]]

#: screen → [(kind, resolver)]（登録順を保つ）。
_REGISTRY: dict[str, list[tuple[str, Resolver]]] = {}


def register(screen: str, kind: str, resolver: Resolver) -> None:
    """画面 ``screen`` の種別 ``kind`` の解決器を登録する。

    同じ ``(screen, kind)`` の再登録は**置き換え**（登録順は維持）。モジュールの
    二重 import で解決器が二重適用されないようにするため。
    """
    entries = _REGISTRY.setdefault(str(screen), [])
    for index, (existing_kind, _existing) in enumerate(entries):
        if existing_kind == kind:
            entries[index] = (kind, resolver)
            return
    entries.append((str(kind), resolver))


def registered_kinds(screen: str) -> tuple[str, ...]:
    """``screen`` に登録済みの種別を登録順に返す（テスト・診断用）。"""
    return tuple(kind for kind, _resolver in _REGISTRY.get(str(screen), ()))


def resolve(ctx: ScreenContext | None, sources: Mapping[str, Any] | None = None) -> list[str]:
    """``ctx.screen`` に登録された解決器を登録順に適用し、事実文を集める。

    解決器の例外は握り潰す（1つの失敗で対話全体を止めない = SA2）。
    """
    if ctx is None:
        return []
    payload: Mapping[str, Any] = sources if isinstance(sources, Mapping) else {}
    facts: list[str] = []
    for _kind, resolver in tuple(_REGISTRY.get(ctx.screen, ())):
        try:
            produced = resolver(ctx, payload)
        except Exception:  # noqa: BLE001 - 解決器の失敗はその facts だけ欠ける
            continue
        if not isinstance(produced, (list, tuple)):
            continue
        for fact in produced:
            if isinstance(fact, str) and fact.strip():
                facts.append(fact.strip())
    return facts


def render_block(facts: list[str] | None) -> str:
    """事実文の列を固定ヘッダ付きの独立ブロックへ描画する（SA7）。

    空なら空文字。``MAX_BLOCK_CHARS`` を超える場合は**行境界**で打ち切り、
    最終行に ``…（以下省略）`` を置く（件数は書かない = SA4）。
    """
    cleaned = [f.strip() for f in (facts or []) if isinstance(f, str) and f.strip()]
    if not cleaned:
        return ""
    lines = [f"- {fact}" for fact in cleaned]
    block = "\n".join([BLOCK_HEADER, *lines])
    if len(block) <= MAX_BLOCK_CHARS:
        return block

    budget = MAX_BLOCK_CHARS - len(BLOCK_HEADER) - 1 - (len(TRUNCATION_LINE) + 1)
    kept: list[str] = []
    used = 0
    for line in lines:
        need = len(line) + (1 if kept else 0)
        if used + need > budget:
            break
        kept.append(line)
        used += need
    return "\n".join([BLOCK_HEADER, *kept, TRUNCATION_LINE])
