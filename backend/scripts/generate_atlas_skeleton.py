"""分野の地図 — 骨格のバッチ生成 CLI (Issue A-2)。

カートリッジ単位で一度だけ実行するバッチジョブ。draft を
`backend/cartridges/<id>/atlas/skeleton.draft.yaml` に書き出す。

使い方:
    python -m scripts.generate_atlas_skeleton --cartridge particle_physics
    python -m scripts.generate_atlas_skeleton --cartridge particle_physics --force

- 既存 draft がある場合は --force を付けない限り再生成しない (再実行は明示操作)
- 生成された draft は教員レビュー (管理画面「分野の地図」タブ) を経て凍結するまで
  学習者向けには一切表示されない
"""

from __future__ import annotations

import argparse
import logging
import sys

from core import atlas
from core.atlas_generator import generate_skeleton_draft
from core.cartridges import cartridge_directory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="分野の地図の骨格 draft をバッチ生成する")
    parser.add_argument("--cartridge", required=True, help="カートリッジID (例: particle_physics)")
    parser.add_argument("--force", action="store_true", help="既存 draft を上書き再生成する")
    parser.add_argument("--model", default=None, help="使用モデルの上書き")
    args = parser.parse_args()

    draft_path = cartridge_directory(args.cartridge) / atlas.DRAFT_FILENAME
    if draft_path.exists() and not args.force:
        logger.error("draft が既に存在します: %s (再生成は --force)", draft_path)
        return 1

    skeleton = generate_skeleton_draft(args.cartridge, model=args.model)
    atlas.write_skeleton(skeleton, draft_path)
    report = atlas.validate_skeleton(skeleton)
    logger.info("draft を書き出しました: %s", draft_path)
    logger.info("generated_by: %s", skeleton.generated_by)
    logger.info("regions=%d, warnings=%d", len(skeleton.regions), len(report.warnings))
    for warning in report.warnings:
        logger.warning("validation: %s", warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
