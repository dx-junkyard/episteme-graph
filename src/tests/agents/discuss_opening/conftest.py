"""DiscussOpeningAgent のテスト用パス設定。

``DiscussOpeningAgent`` は修復ループを ``core/llm_worker/repair.py``
（8系統目のアダプタ・コピー実装しない方針）に委譲するため、``run()`` を実地で
動かすテストでは ``backend/`` が sys.path に必要になる。``backend/tests/conftest.py``
が逆方向（backend テストから ``src/`` を載せる）で同じことをしているのと対称の措置。

``core.llm_worker.repair`` は core 内の他モジュールを import しないため、この
パス追加だけで LLM SDK / DB / Settings を一切必要としない（実際の LLM 呼び出しは
テスト側が client を差し替えて遮断する）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[4] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.append(str(_BACKEND_DIR))
