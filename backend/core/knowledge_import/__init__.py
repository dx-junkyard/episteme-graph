"""束の取り込み（knowledge_transfer_design.md §4.2 / P4-1）。

export bundle（ZIP）を読み、Phase 1 の :func:`core.knowledge_objects.sync.sync_live_rows`
に **行として**流す層。A層（``src/episteme_graph/agents/``）は触らず、agent の結果を
再構成しない（KT1）。LLM / embedding は 0 回（KT3）。取り込み行は常に候補で着地し
（KT2 / T-1）、``stable_key`` は取り込み先の ``document_id`` で**再計算**する（KT4）。

FastAPI / LLM は import しない（``bundle.py`` / ``rows.py`` は sqlalchemy も使わない
純関数。``apply.py`` だけが呼び出し側のセッションで SQL を発行する）。
"""

from .bundle import (  # noqa: F401  (re-export)
    MAX_BUNDLE_BYTES,
    MIN_SCHEMA_VERSION,
    BundleError,
    ImportBundle,
    parse_bundle,
)
from .rows import (  # noqa: F401  (re-export)
    IMPORT_COMPONENT_STATUS,
    IMPORT_PROVENANCE_KEY,
    IMPORT_REVIEW_STATUS,
)

__all__ = [
    "MAX_BUNDLE_BYTES",
    "MIN_SCHEMA_VERSION",
    "BundleError",
    "ImportBundle",
    "parse_bundle",
    "IMPORT_COMPONENT_STATUS",
    "IMPORT_PROVENANCE_KEY",
    "IMPORT_REVIEW_STATUS",
]
