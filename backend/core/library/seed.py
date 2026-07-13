"""カートリッジ同梱ライブラリの起動時シード取込（`core/atlas_store.py` のシードパターンを踏襲）。

`backend/cartridges/<id>/library/*.json`（任意）を走査し、
(domain_key, name, entry_type) が未存在の組み合わせのみ ``store.create_entry`` 経由で
取り込む（冪等）。ファイルが無ければ何もしない。DB 接続不可時も起動を止めない
（例外を握って warning ログ + エラー情報を戻り値に含める）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text as sa_text

from core import revision_store
from core.postgres import get_session

from . import schema, store

logger = logging.getLogger(__name__)


def _iter_bundled_entries(directory: Path) -> list[dict]:
    """``directory`` 配下の *.json を読み、単体 or 配列の両形式を dict のリストへ正規化する。"""
    entries: list[dict] = []
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("invalid bundled library file: %s", path, exc_info=True)
            continue
        if isinstance(data, list):
            entries.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            entries.append(data)
        else:
            logger.warning("unexpected bundled library content (not object/array): %s", path)
    return entries


def import_bundled_library() -> dict:
    """カートリッジ同梱ライブラリ JSON を起動時に冪等取込する。

    Returns:
        {"imported": n, "skipped": n} — 正常時。
        {"imported": 0, "skipped": 0, "error": "..."} — DB / カートリッジ走査が
        使えなかった場合（起動は止めない）。
    """
    try:
        from core.cartridges import cartridge_directory, list_cartridges
    except Exception as exc:  # noqa: BLE001
        logger.warning("cartridges module unavailable for library seed", exc_info=True)
        return {"imported": 0, "skipped": 0, "error": str(exc)}

    try:
        summaries = list_cartridges()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to list cartridges for library seed", exc_info=True)
        return {"imported": 0, "skipped": 0, "error": str(exc)}

    try:
        session = get_session()
    except Exception as exc:  # noqa: BLE001
        logger.warning("library seed DB session unavailable", exc_info=True)
        return {"imported": 0, "skipped": 0, "error": str(exc)}

    imported = 0
    skipped = 0
    try:
        candidates: list[dict] = []
        for summary in summaries:
            domain_key = summary.cartridge_id
            try:
                directory = cartridge_directory(domain_key) / "library"
            except FileNotFoundError:
                continue

            for entry in _iter_bundled_entries(directory):
                entry_type = str(entry.get("entry_type") or "").strip()
                name = str(entry.get("name") or "").strip()
                if not entry_type or not name:
                    logger.warning(
                        "skipping bundled library entry without entry_type/name: domain=%s entry=%r",
                        domain_key,
                        entry,
                    )
                    skipped += 1
                    continue
                if not schema.is_valid_entry_type(entry_type):
                    logger.warning(
                        "skipping bundled library entry with invalid entry_type=%r domain=%s",
                        entry_type,
                        domain_key,
                    )
                    skipped += 1
                    continue
                candidates.append(
                    {"domain_key": domain_key, "entry_type": entry_type, "name": name, "entry": entry}
                )

        def _exists(candidate: dict) -> bool:
            row = session.execute(
                sa_text(
                    """
                    SELECT 1 FROM library_entries
                     WHERE domain_key = :domain_key AND name = :name AND entry_type = :entry_type
                     LIMIT 1
                    """
                ),
                {
                    "domain_key": candidate["domain_key"],
                    "name": candidate["name"],
                    "entry_type": candidate["entry_type"],
                },
            ).fetchone()
            return row is not None

        def _create(candidate: dict) -> None:
            entry = candidate["entry"]
            store.create_entry(
                domain_key=candidate["domain_key"],
                entry_type=candidate["entry_type"],
                name=candidate["name"],
                aliases=entry.get("aliases") or [],
                summary=str(entry.get("summary") or ""),
                body=entry.get("body") or {},
                source_component_ids=entry.get("source_component_ids") or [],
                source_document_ids=entry.get("source_document_ids") or [],
                created_by="bundled_import",
            )

        def _on_created(candidate: dict) -> None:
            logger.info(
                "bundled library entry imported: domain=%s name=%s",
                candidate["domain_key"],
                candidate["name"],
            )

        def _on_create_error(candidate: dict, exc: Exception) -> None:
            logger.warning(
                "failed to import bundled library entry: domain=%s name=%s",
                candidate["domain_key"],
                candidate["name"],
                exc_info=exc,
            )

        result = revision_store.idempotent_seed_import(
            candidates,
            exists_fn=_exists,
            create_fn=_create,
            on_created=_on_created,
            on_create_error=_on_create_error,
        )
        imported += result["imported"]
        skipped += result["skipped"]
    finally:
        session.close()

    return {"imported": imported, "skipped": skipped}
