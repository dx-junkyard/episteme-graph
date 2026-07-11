"""LLM トークン使用量推計（U層）— core/llm_usage パッケージ。

実測（プロバイダ報告値）を正本、決定論的推計をフォールバックとする使用量台帳。
正本ドキュメント: docs/features/llm_usage_metering_design.md

FastAPI には依存しない（開発ルール2 / U4）。計測フックは core/llm.py（+ core/tts.py）
に一元化し、帰属（feature 等）は contextvars（context.py）で伝搬する。
"""

from __future__ import annotations

from core.llm_usage.context import (
    UsageContext,
    bind_usage_context,
    current_usage_context,
    set_current_feature,
    usage_context,
)
from core.llm_usage.estimator import (
    TokenEstimate,
    estimate_messages_tokens,
    estimate_text_tokens,
    estimate_vision_tokens,
)
from core.llm_usage.metrics import collect_metrics
from core.llm_usage.observe import (
    observe_chat,
    observe_embeddings,
    observe_transcribe,
    observe_tts,
    observe_vision,
)
from core.llm_usage.pricing import compute_cost_usd, load_price_table
from core.llm_usage.recorder import dropped_count, flush_now, record, reset_for_tests
from core.llm_usage.schema import (
    KNOWN_FEATURES,
    OPERATIONS,
    UNATTRIBUTED,
    USAGE_SOURCES,
    UsageEvent,
)

__all__ = [
    # context
    "UsageContext",
    "usage_context",
    "current_usage_context",
    "set_current_feature",
    "bind_usage_context",
    # estimator
    "TokenEstimate",
    "estimate_text_tokens",
    "estimate_messages_tokens",
    "estimate_vision_tokens",
    # schema
    "UsageEvent",
    "OPERATIONS",
    "USAGE_SOURCES",
    "UNATTRIBUTED",
    "KNOWN_FEATURES",
    # recorder
    "record",
    "dropped_count",
    "flush_now",
    "reset_for_tests",
    # observe
    "observe_chat",
    "observe_embeddings",
    "observe_vision",
    "observe_transcribe",
    "observe_tts",
    # pricing
    "load_price_table",
    "compute_cost_usd",
    # metrics
    "collect_metrics",
]
