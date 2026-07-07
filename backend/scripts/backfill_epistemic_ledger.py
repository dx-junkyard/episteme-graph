"""D層 — 既存コーパスへの認識的地位台帳バックフィル CLI (D1-2)。

A層成果物（theory_claims / theory_component_graphs）を読み、epistemic_ledger に
既定値の台帳行を決定論的に生成する（非LLM・再実行冪等）。

使い方:
    python -m scripts.backfill_epistemic_ledger              # 全ドキュメント
    python -m scripts.backfill_epistemic_ledger --document <document_id>

- 人間が記帳済みの行（verification_status ≠ unknown、または scopes 非空）は上書きしない
- directly_verified は人間の記帳専用のため、このスクリプトからは絶対に生成されない
"""

from __future__ import annotations

import argparse
import logging
import sys

from core.doubt.ledger_builder import backfill_all, backfill_document_ledger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="認識的地位台帳のバックフィル（冪等）")
    parser.add_argument("--document", default="", help="対象 document_id（省略時は全件）")
    args = parser.parse_args()

    if args.document:
        result = backfill_document_ledger(args.document)
        logger.info("done: %s", result)
        return 0 if not result.get("failed") else 1

    result = backfill_all()
    logger.info("done: documents=%s targets=%s", result.get("documents"), result.get("written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
