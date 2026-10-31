"""uxsim のテスト用パス設定。

リポジトリルート（``uxsim`` パッケージ）と ``backend``（審判 B が読む正本）を sys.path に載せる。
"""
from __future__ import annotations

import sys
from pathlib import Path

_UXSIM = Path(__file__).resolve().parent
_REPO = _UXSIM.parent
for _p in (_REPO, _REPO / "backend", _REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
