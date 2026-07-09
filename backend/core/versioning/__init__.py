"""共有物のバージョン管理 + 更新通知 + 削除猶予（V層）。

パイプライン生成物（教材の解析成果）とコースを「発行版(Release)」として不変スナップショット化し、
消費側は Release にピンして所有者の一方的な更新・削除から保護される層。

- A層（src/episteme_graph/agents/）は非改変。既存 API/関数を呼ぶ側として実装する。
- このパッケージは FastAPI を import しない（project rule 2。テスタビリティ確保）。
- 監査は theory_review_events（entity_type は open vocab）。

各モジュール:
    schema        — 語彙・定数の正本
    audit         — theory_review_events への監査追記（core 内で完結）
    releases      — Release の発行・スナップショット・一覧・状態
    subscriptions — 消費者ピン（ensure_pin / adopt_latest / resolve_effective_release_id）
    notifications — 通知インボックス（fan_out / list_inbox / mark_read）
    deletion      — 削除予約・取消・物理 purge
    resolver      — 読み取りの版解決（ピン viewer に旧版スナップショットを返す）
    artifact_view — pinned run の stage_outputs → 表示モデル形へ整形
    worker        — 削除猶予の定期スイーパ
"""
from __future__ import annotations

from . import (  # noqa: F401
    audit,
    deletion,
    notifications,
    releases,
    resolver,
    schema,
    subscriptions,
    worker,
)
