"""cartridge 別名（alias）の**語境界付き**照合の正本。

`docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の P0-2。
従来の ``str(alias).lower() in text.lower()`` は部分文字列一致で、alias ``"SM"`` が
``"cosmological"`` に当たり、原本に 0 回の "Standard Model" が成果へ注入された（F-7 / K-3）。

規約:
- **3 文字以下**の短い alias（``SHORT_ALIAS_MAX_LEN``）は**大文字小文字を区別した**
  単語完全一致のみ（``SM`` は ``SM`` にだけ当たり、``sm`` / ``cosmological`` には当たらない）。
- それより長い alias / canonical 名は大文字小文字を無視した語境界付き一致
  （``Standard Model`` は ``standard model`` にも当たるが ``standard modeling`` の
  ``model`` 途中には当たらない）。
- 語境界は英数字とアンダースコアの連続を1語とみなす（``(?<![0-9A-Za-z_])`` /
  ``(?![0-9A-Za-z_])``）。``\\b`` を使わないのは、ハイフンやギリシャ文字を含む
  alias（``B->D(*)``、``τν``）でも境界判定を一定にするため。
- 空文字・空白のみの alias は何にも当たらない。

rhetorical_role の validator と component_assembly の enrichment がこの関数を使う。
alias を文字列に含めるかどうかの判定を **各所で再実装しない**。
"""

from __future__ import annotations

import re
from functools import lru_cache

SHORT_ALIAS_MAX_LEN = 3

_BOUNDARY_BEFORE = r"(?<![0-9A-Za-z_])"
_BOUNDARY_AFTER = r"(?![0-9A-Za-z_])"


@lru_cache(maxsize=4096)
def alias_pattern(alias: str) -> re.Pattern[str] | None:
    """alias 1つ分の照合パターン（キャッシュ付き）。空なら ``None``。"""
    text = str(alias or "").strip()
    if not text:
        return None
    body = _BOUNDARY_BEFORE + re.escape(text) + _BOUNDARY_AFTER
    if len(text) <= SHORT_ALIAS_MAX_LEN:
        return re.compile(body)
    return re.compile(body, re.IGNORECASE)


def text_mentions_alias(text: str, alias: str) -> bool:
    """``text`` の中に ``alias`` が語として現れるか。"""
    if not text:
        return False
    pattern = alias_pattern(str(alias or ""))
    if pattern is None:
        return False
    return pattern.search(str(text)) is not None


def text_mentions_any(text: str, names: object) -> bool:
    """``names``（canonical + alias 群）のどれかが語として現れるか。"""
    if not text or not names:
        return False
    if isinstance(names, (str, bytes)):
        names = [names]
    try:
        iterator = iter(names)  # type: ignore[arg-type]
    except TypeError:
        return False
    return any(text_mentions_alias(text, str(name)) for name in iterator if name)


def mentioned_canonicals(text: str, aliases_by_canonical: dict) -> list[str]:
    """``{canonical: [alias, ...]}`` の表から、``text`` に語として現れる canonical を
    表の順で返す（重複なし）。canonical 自身も照合対象に含める。"""
    if not text or not isinstance(aliases_by_canonical, dict):
        return []
    matched: list[str] = []
    for canonical, aliases in aliases_by_canonical.items():
        canon = str(canonical or "").strip()
        if not canon:
            continue
        names: list[str] = [canon]
        if isinstance(aliases, (list, tuple, set)):
            names.extend(str(a) for a in aliases if a)
        elif isinstance(aliases, str) and aliases.strip():
            names.append(aliases)
        if text_mentions_any(text, names) and canon not in matched:
            matched.append(canon)
    return matched
