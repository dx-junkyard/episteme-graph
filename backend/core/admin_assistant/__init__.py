"""Admin Copilot（横断ユーティリティ層, migration 034）のコアモジュール。

正本: docs/features/admin_assistant_design.md。既存 A/B/C/D 層のコードは変更せず、
既存 API を呼ぶ側として実装する（P7）。このパッケージは FastAPI を import しない
（core のテスタビリティ確保、開発ルール2）。
"""

from __future__ import annotations

__all__ = [
    "capabilities",
    "knowledge",
    "intent",
    "action_store",
    "actions",
    "schema",
]
