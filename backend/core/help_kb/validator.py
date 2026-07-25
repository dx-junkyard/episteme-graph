"""``docs/manual/`` の front-matter / 見出し / リンク整合性を検証する。

起動時（``main.py`` lifespan）から呼ばれ、違反は ``logger.warning`` するだけの
fail-open 検証として使う（起動は止めない）。CI ガードレールテストからも
「マージ = 凍結バリデーション」の実体として呼ばれる想定。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import index as _index
from .manual import AUDIENCES, manual_root

_LINK_RE = re.compile(r"\(\.\./\.\./admin_operations/([^)#\s]+\.md)#([^)\s]+)\)")

# Phase 3 §7-2: DB draft/freeze の凍結検証ゲートに昇格する student/ 禁止語彙。
# PR レビューが効かない DB 経路（教員が DB draft をそのまま freeze しうる）向けの
# コード側ガード。§3-1 の学生安全ルール（ADMIN_PASSWORD・_require_teacher 等の
# 内部名を学生向けページへ露出しない）を機械検査に落とし込む。
STUDENT_DENYLIST = (
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "admin.html",
    "localhost:8001",
    "localhost:9001",
    "/api/admin",
    "_require_",
    "MAX_CALLS_PER_DAY",
)


def _admin_operations_dir(root: Path) -> Optional[Path]:
    candidate = root.parent / "admin_operations"
    return candidate if candidate.is_dir() else None


def check_student_denylist(texts_by_file: dict[str, str]) -> list[str]:
    """``texts_by_file``（student/ の ``{ファイル名: 本文}``）に禁止語彙が無いか検査する。

    違反は1ファイルにつき複数語彙あればまとめて全件報告する（P4: 情報を落とさない）。
    """
    violations: list[str] = []
    for filename in sorted(texts_by_file.keys()):
        raw = texts_by_file[filename] or ""
        for term in STUDENT_DENYLIST:
            if term in raw:
                violations.append(f"student/{filename}: 禁止語彙 {term!r} を含む")
    return violations


def validate_manual_texts(texts_by_audience: dict[str, dict[str, str]]) -> list[str]:
    """front-matter audience 一致・anchor 明示/一意・admin_operations リンク実在・

    student denylist をテキスト入力（ファイルシステムを経由しない）で検証する。

    ``texts_by_audience`` は ``{audience: {ファイル名: 本文}}``。DB draft/freeze の
    凍結検証ゲート（Phase 3 §7-2）から使う。admin_operations 側のリンク解決先だけは
    引き続きファイルシステム（``docs/admin_operations/``）から読む
    （admin_operations は本 KB の書き込み対象外のため、テキスト入力を持たない）。
    """
    violations: list[str] = []
    root = manual_root()
    admin_ops_index: dict = {}
    if root is not None:
        admin_ops_dir = _admin_operations_dir(root)
        if admin_ops_dir is not None:
            admin_ops_index, _excluded = _index.build_section_index(
                admin_ops_dir, manual_transforms=False
            )

    for audience in AUDIENCES:
        files = texts_by_audience.get(audience) or {}
        for filename in sorted(files.keys()):
            raw = files[filename]
            rel = f"{audience}/{filename}"
            lines = raw.splitlines()
            front_matter, body_lines = _index.parse_front_matter(lines)

            declared_audience = front_matter.get("audience")
            if declared_audience != audience:
                violations.append(
                    f"{rel}: front-matter audience={declared_audience!r} はディレクトリ "
                    f"{audience!r} と不一致"
                )

            seen_anchors: set = set()
            for line in body_lines:
                m = _index.HEADING_RE.match(line)
                if not m:
                    continue
                heading_text = m.group(2)
                if not _index.ANCHOR_RE.search(heading_text):
                    violations.append(f"{rel}: 見出し「{heading_text}」に明示 {{#anchor}} が無い")
                    continue
                anchor = _index.slugify(heading_text)
                if anchor in seen_anchors:
                    violations.append(f"{rel}: anchor '{anchor}' がファイル内で重複")
                seen_anchors.add(anchor)

            for link_file, link_anchor in _LINK_RE.findall(raw):
                key = f"{link_file}#{link_anchor}"
                if key not in admin_ops_index:
                    violations.append(f"{rel}: リンク先が存在しない admin_operations/{key}")

        if audience == "student":
            violations.extend(check_student_denylist(files))

    return violations


def _validate_manual_files() -> list[str]:
    """ファイルシステム（``docs/manual/``）を検証する（Phase 1 からの既存ロジック）。"""
    violations: list[str] = []
    root = manual_root()
    if root is None:
        return violations

    texts_by_audience: dict[str, dict[str, str]] = {}
    for audience in AUDIENCES:
        directory = root / audience
        if not directory.is_dir():
            continue
        files: dict[str, str] = {}
        for md in sorted(directory.glob("*.md")):
            try:
                files[md.name] = md.read_text(encoding="utf-8")
            except OSError:
                continue
        texts_by_audience[audience] = files

    return validate_manual_texts(texts_by_audience)


def validate_manual() -> list[str]:
    """配信中のソースを検証する。違反があれば人間可読の文字列リストで返す。

    Phase 3（DB draft/freeze, §7-2）で ``db`` 配信に切り替わっている場合は現行
    active version のテキストを検証し、既定の ``files`` 配信（Phase 1）ならば
    ``docs/manual/`` を検証する。DB 参照自体が失敗した場合は files 検証へ
    fail-open する（起動チェックを止めない。DB 障害時にマニュアル配信を止めない
    という store.py の方針と同じ）。

    ``docs/manual/`` 自体が無い、または audience ディレクトリへの分離前
    （移行途中）の場合は、検証対象が存在しないものとして空リストを返す
    （fail-open。docs 未整備を理由に起動を止めない）。
    """
    served_texts: Optional[dict] = None
    try:
        from . import store as _store  # 遅延 import（store.py は本モジュールを import する）

        state = _store.get_state()
        if state.get("serving_source") == "db" and state.get("active_version_no"):
            served_texts = _store.get_active_version_texts()
    except Exception:  # noqa: BLE001 — DB 未接続・未 migration 環境等は files 検証へ
        served_texts = None

    if served_texts is not None:
        return validate_manual_texts(served_texts)

    return _validate_manual_files()
