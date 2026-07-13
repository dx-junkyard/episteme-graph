"""状態管理・通知基盤（Status Projection + 遷移イベント + 統合通知インボックス）。

正本は docs/features/status_notification_design.md。FastAPI / LLM クライアントを
import しない（S3）。既存の A〜V 層テーブルは読むだけで、書き込みは本パッケージが
新設する status_events / user_notifications のみ。
"""
