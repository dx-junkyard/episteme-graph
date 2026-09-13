"""画面文脈アダプター — 画面ごとの解決器。

各モジュールは import されるだけで自身の解決器を ``registry`` へ登録する
（``core.assistant_context`` を import すれば全画面分が揃う）。
"""

from __future__ import annotations

from . import graph_review  # noqa: F401  # import 副作用で解決器を登録する
from . import learning  # noqa: F401  # import 副作用で解決器を登録する

__all__ = ["graph_review", "learning"]
