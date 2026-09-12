"""カートリッジ同梱ライブラリの起動時シード取込（`core/atlas_store.py` のシードパターンを踏襲）。

`backend/cartridges/<id>/library/*.json`（任意）を走査し、
(domain_key, name, entry_type) が未存在の組み合わせのみ ``store.create_entry`` 経由で
取り込む（冪等）。ファイルが無ければ何もしない。DB 接続不可時も起動を止めない
（例外を握って warning ログ + エラー情報を戻り値に含める）。

P0-7 / K-4: 取り込んだ draft はそのまま**初版を凍結**する。L層設計 §6 のとおり
パイプラインの retrieval（``search_frozen_entries``）は ``library_entry_versions``
だけを引くため、凍結しない限り同梱ライブラリはパイプラインから構造的に不可視で、
同一性リンク・標準化判定といった下流機能がまとめて起動しない状態になっていた
（実測 ``library_entries`` 3 / ``library_entry_versions`` 0）。

**「昇格は人間の操作のみ」（LLM がライブラリへ書き込む経路を作らない）には反しない**:
ここで凍結するのは同梱 JSON、すなわち人間が書いてリポジトリに置いたデータであり、
LLM 出力ではない。起動処理が行うのは「人間が書いた初版をパイプラインから読める
状態にする」ことだけで、新しい内容を生成しない。ただし ``store.freeze_entry`` は
embedding を1エントリ1コール呼ぶため、**初回起動時のみ**エントリ数ぶんの埋め込み
API コールが発生する（DB 不達・API 不達はいずれも fail-soft）。
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

# 同梱シード由来であることの目印（``library_entries.created_by`` / 版の発行者）。
SEED_CREATED_BY = "bundled_import"
SEED_FREEZE_NOTE = "同梱シードの初版（起動時自動凍結）"


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


def _error_result(exc: Exception) -> dict:
    """取込に着手できなかったときの戻り値（起動は止めない）。"""
    return {"imported": 0, "skipped": 0, "frozen": 0, "freeze_failed": 0, "error": str(exc)}


def _unfrozen_seed_entries(exclude_ids: set[str]) -> list[dict]:
    """未凍結の同梱シード行（``created_by='bundled_import'`` かつ版ゼロかつ active）。

    P0-7: 既に取り込み済みだが凍結されていない既存行のバックフィル用。通常運用では
    一度凍結すれば ``latest_version_no >= 1`` になるので**起動ごとに 0 件**になる。

    教員が draft を編集済みかどうかは判定できないため、条件は「同梱シード由来で版が
    ゼロ」だけに絞る。凍結は ``revision`` を進めない append 操作
    （``store.freeze_entry`` は ``library_entry_versions`` へ INSERT し
    ``library_entries.latest_version_no`` のみ UPDATE する。``revision`` は
    ``update_entry`` の楽観ロック経路でしか増えない）なので、編集途中の draft の
    楽観ロックを壊さない。

    行の取得は ``store.list_entries``（active のみの既存投影）に委ね、
    ``created_by`` / ``latest_version_no`` の絞り込みだけを Python 側で行う
    — seed 側に SELECT を二重化して store の正本と食い違わせないため。
    """
    try:
        entries = store.list_entries()
    except Exception:  # noqa: BLE001 — バックフィルの失敗で起動を止めない
        logger.warning("failed to list library entries for seed freeze backfill", exc_info=True)
        return []
    return [
        e
        for e in entries
        if str(e.get("created_by") or "") == SEED_CREATED_BY
        and int(e.get("latest_version_no") or 0) == 0
        and str(e.get("status") or "") == schema.STATUS_ACTIVE
        and str(e.get("id") or "") not in exclude_ids
    ]


def import_bundled_library() -> dict:
    """カートリッジ同梱ライブラリ JSON を起動時に冪等取込し、初版を凍結する。

    Returns:
        {"imported": n, "skipped": n, "frozen": n, "freeze_failed": n} — 正常時。
        {"imported": 0, "skipped": 0, "frozen": 0, "freeze_failed": 0, "error": "..."}
        — DB / カートリッジ走査が使えなかった場合（起動は止めない）。
    """
    try:
        from core.cartridges import cartridge_directory, list_cartridges
    except Exception as exc:  # noqa: BLE001
        logger.warning("cartridges module unavailable for library seed", exc_info=True)
        return _error_result(exc)

    try:
        summaries = list_cartridges()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to list cartridges for library seed", exc_info=True)
        return _error_result(exc)

    try:
        session = get_session()
    except Exception as exc:  # noqa: BLE001
        logger.warning("library seed DB session unavailable", exc_info=True)
        return _error_result(exc)

    imported = 0
    skipped = 0
    frozen = 0
    freeze_failed = 0
    # この起動で凍結を試みた entry。バックフィルで同じ行を二重に試さない。
    freeze_attempted: set[str] = set()

    def _freeze(entry_id: str, *, domain_key: str, name: str) -> None:
        """初版を凍結する（P0-7）。失敗しても draft は残し、取込全体は止めない。"""
        nonlocal frozen, freeze_failed
        if not entry_id:
            freeze_failed += 1
            logger.warning(
                "bundled library entry has no id; skipping initial freeze: domain=%s name=%s",
                domain_key,
                name,
            )
            return
        freeze_attempted.add(entry_id)
        try:
            store.freeze_entry(
                entry_id,
                published_by=SEED_CREATED_BY,
                note=SEED_FREEZE_NOTE,
            )
        except Exception:  # noqa: BLE001 — 凍結失敗はシード全体を止めない
            freeze_failed += 1
            logger.warning(
                "failed to freeze bundled library entry: domain=%s name=%s id=%s",
                domain_key,
                name,
                entry_id,
                exc_info=True,
            )
            return
        frozen += 1

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
            created = store.create_entry(
                domain_key=candidate["domain_key"],
                entry_type=candidate["entry_type"],
                name=candidate["name"],
                aliases=entry.get("aliases") or [],
                summary=str(entry.get("summary") or ""),
                body=entry.get("body") or {},
                source_component_ids=entry.get("source_component_ids") or [],
                source_document_ids=entry.get("source_document_ids") or [],
                created_by=SEED_CREATED_BY,
            )
            # P0-7: draft のままだと retrieval（凍結版のみ）から見えないので初版を凍結する。
            # 凍結の失敗はここで握る — idempotent_seed_import に伝播させると、draft は
            # 作られているのに imported に数えられない（skipped 扱いになる）ため。
            _freeze(
                str((created or {}).get("id") or ""),
                domain_key=candidate["domain_key"],
                name=candidate["name"],
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

        # P0-7 バックフィル: 凍結導入より前に取り込まれた（版ゼロの）同梱シード行を
        # 冪等に凍結する。この起動で凍結を試みた行は除く。通常運用では 0 件。
        for stale in _unfrozen_seed_entries(freeze_attempted):
            _freeze(
                str(stale.get("id") or ""),
                domain_key=str(stale.get("domain_key") or ""),
                name=str(stale.get("name") or ""),
            )
    finally:
        session.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "frozen": frozen,
        "freeze_failed": freeze_failed,
    }
