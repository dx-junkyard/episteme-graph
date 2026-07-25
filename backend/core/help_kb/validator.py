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


def _admin_operations_dir(root: Path) -> Optional[Path]:
    candidate = root.parent / "admin_operations"
    return candidate if candidate.is_dir() else None


def validate_manual() -> list[str]:
    """違反があれば人間可読の文字列リストで返す（無ければ空リスト）。

    ``docs/manual/`` 自体が無い、または audience ディレクトリへの分離前
    （移行途中）の場合は、検証対象が存在しないものとして空リストを返す
    （fail-open。docs 未整備を理由に起動を止めない）。
    """
    violations: list[str] = []
    root = manual_root()
    if root is None:
        return violations

    admin_ops_dir = _admin_operations_dir(root)
    admin_ops_index: dict = {}
    if admin_ops_dir is not None:
        admin_ops_index, _excluded = _index.build_section_index(admin_ops_dir, manual_transforms=False)

    for audience in AUDIENCES:
        directory = root / audience
        if not directory.is_dir():
            continue
        for md in sorted(directory.glob("*.md")):
            try:
                raw = md.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = f"{audience}/{md.name}"
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

    return violations
