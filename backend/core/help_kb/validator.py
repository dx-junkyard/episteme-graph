"""``docs/manual/`` の front-matter / 見出し / リンク整合性を検証する。

起動時（``main.py`` lifespan）から呼ばれ、違反は ``logger.warning`` するだけの
fail-open 検証として使う（起動は止めない）。CI ガードレールテストからも
「マージ = 凍結バリデーション」の実体として呼ばれる想定。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from . import index as _index
from .manual import AUDIENCES, manual_root

logger = logging.getLogger(__name__)

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


def check_ui_anchor_mappings() -> list[str]:
    """UI アンカー表（``core/help_kb/ui_anchors.py``）の全参照を検証する。

    学習画面インスペクト・モード（``docs/features/learning_ui_inspect_hover_design.md``
    §5.2/§9-2）のガバナンス条項: ``UI_ANCHORS`` の値は必ず ``student/`` 配下を指し
    （audience 越境禁止）、かつ実在する節でなければならない。

    ``validate_manual()`` とは**独立の関数**として提供する（呼び出し元 ``main.py``
    lifespan が同じ fail-open スタイルで別途呼ぶ想定）。``validate_manual()`` 自身に
    統合しないのは、既存のガードレールテスト群が ``manual_root()`` を差し替えた
    最小限の合成ツリーで ``validate_manual()`` の戻り値を厳密にピン留めしており
    （例: ``test_help_kb.py`` の空ツリー/最小ツリーケース）、ここに UI_ANCHORS 実在
    チェックを混ぜると本 UI アンカー機能と無関係なテストが軒並み壊れるため。

    ``resolve_ui_anchors()``（配信用・DB draft/freeze の serving_source 切替に追従する）
    ではなく、**常にファイルシステム（``docs/manual/student/``）を直接検証する**。
    ``UI_ANCHORS`` は git 管理された docs に対して人手で書いたマッピングであり、
    その静的な正しさを見るのが目的のため（``validate_manual_texts`` の
    admin_operations リンク検証が serving_source に関わらず常にファイルシステムを
    参照するのと同じ理由・同じパターン）。DB 配信中の内容が一時的にこれと食い違う
    ケース（教員が DB draft から該当節を削除した等）は、配信側の
    ``resolve_ui_anchors()`` が fail-open で当該アンカーを黙って除外するため実害は
    ない（no_hit 経路にフォールバックするだけ）。
    """
    try:
        from . import ui_anchors as _ui_anchors
    except Exception:  # noqa: BLE001 — 並行実装中のモジュール不在に対する防御
        logger.warning("ui_anchors module unavailable during validation", exc_info=True)
        return []

    violations: list[str] = []
    root = manual_root()
    idx: dict = {}
    if root is not None:
        student_dir = root / "student"
        if student_dir.is_dir():
            idx, _excluded = _index.build_section_index(
                student_dir, manual_transforms=True, audience_tag="student"
            )

    for anchor_id, ref in _ui_anchors.UI_ANCHORS.items():
        if not ref.startswith("student/"):
            violations.append(
                f"ui_anchors: {anchor_id!r} が student/ 以外を参照している: {ref!r}"
            )
            continue
        _audience, _file, rest = ref.partition("/")
        file, _, anchor = rest.partition("#")
        if f"{file}#{anchor}" not in idx:
            violations.append(
                f"ui_anchors: {anchor_id!r} の参照先が見つからない: {ref!r}"
            )
    return violations


def check_admin_ui_anchor_mappings() -> list[str]:
    """管理画面 UI アンカー表（``core/help_kb/admin_ui_anchors.py``）の全参照を検証する。

    学習側 ``check_ui_anchor_mappings()`` と同型・独立の関数（同じ理由でこの2つは
    統合しない — 既存ガードレールテストが最小合成ツリーで戻り値をピン留めしている）。
    検証内容:
      ①``ADMIN_UI_ANCHORS`` の値は必ず ``teacher/`` か ``system_admin/`` を指す
        （``student/`` を指す値は audience 越境として違反）。
      ②参照先ファイル・見出し ``{#anchor}`` が実在する（teacher 索引・system_admin
        索引をそれぞれファイルシステムから直接構築して検証する。理由は
        ``check_ui_anchor_mappings`` と同じ — 配信ソースが DB に切り替わっていても
        ``ADMIN_UI_ANCHORS`` 自体は git 管理された docs に対する人手マッピングであり、
        その静的な正しさを見るのが目的のため）。
      ③``KNOWN_ADMIN_UI_ANCHOR_IDS`` が ``ADMIN_UI_ANCHORS`` の全キーを包含する
        （no_hit 記録が「未知のアンカーID」として弾かれないことを保証する）。
    """
    try:
        from . import admin_ui_anchors as _admin_ui_anchors
    except Exception:  # noqa: BLE001 — 並行実装中のモジュール不在に対する防御
        logger.warning("admin_ui_anchors module unavailable during validation", exc_info=True)
        return []

    violations: list[str] = []
    root = manual_root()
    idx_by_audience: dict[str, dict] = {}
    if root is not None:
        for audience in ("teacher", "system_admin"):
            directory = root / audience
            if directory.is_dir():
                idx, _excluded = _index.build_section_index(
                    directory, manual_transforms=True, audience_tag=audience
                )
                idx_by_audience[audience] = idx

    mapped_ids = set(_admin_ui_anchors.ADMIN_UI_ANCHORS.keys())
    unknown_ids = mapped_ids - set(_admin_ui_anchors.KNOWN_ADMIN_UI_ANCHOR_IDS)
    for anchor_id in sorted(unknown_ids):
        violations.append(
            f"admin_ui_anchors: {anchor_id!r} が KNOWN_ADMIN_UI_ANCHOR_IDS に含まれていない"
        )

    for anchor_id, ref in _admin_ui_anchors.ADMIN_UI_ANCHORS.items():
        audience, _, rest = (ref or "").partition("/")
        if audience not in ("teacher", "system_admin"):
            violations.append(
                f"admin_ui_anchors: {anchor_id!r} が teacher/ system_admin/ 以外を参照している: {ref!r}"
            )
            continue
        file, _, anchor = rest.partition("#")
        idx = idx_by_audience.get(audience, {})
        if f"{file}#{anchor}" not in idx:
            violations.append(
                f"admin_ui_anchors: {anchor_id!r} の参照先が見つからない: {ref!r}"
            )
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
