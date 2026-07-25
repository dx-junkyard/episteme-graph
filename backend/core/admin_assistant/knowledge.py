"""操作ナレッジベース（KB）ローダ — 説明モードの根拠（設計 §5.1）。

- 実体: ``docs/admin_operations/*.md``。各節は見出しで区切り、`{#anchor}` で
  capability の ``howto_doc`` に結び付ける（KB を registry に強く紐付ける）。
- role / screen フィルタは capability 側が持つため、KB 検索は
  **role で絞った capability の howto_doc** に限定する（P1: 権限外の手順を出さない）。
- リアルタイム生成はしない（P4 / P6）。KB に節が無ければ「未整備」を返す。

Phase 1（``docs/manual`` 知識源化, §1-1）で索引エンジン本体は
``backend/core/help_kb/index.py`` に一般化・移設した。本ファイルは
admin_operations 特有のディレクトリ探索 + capability 起点のスコアリングのみを
保持する薄い委譲層（外部シグネチャ ``search`` / ``section_for_howto`` /
``clear_cache`` / ``kb_available`` は不変）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from core.help_kb import index as _kb_index


def _candidate_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    cands = []
    # backend/core/admin_assistant/knowledge.py -> parents[3] = repo root
    try:
        cands.append(here.parents[3] / "docs" / "admin_operations")
    except IndexError:
        pass
    # 実行 cwd からの探索（docker / 別レイアウト対策）
    cwd = Path.cwd()
    cands.append(cwd / "docs" / "admin_operations")
    cands.append(cwd.parent / "docs" / "admin_operations")
    # コンテナ向け保険候補（help_kb/manual.py の _candidate_roots() と同型。
    # backend/Dockerfile は docs/admin_operations を /app/docs/admin_operations に COPY する）。
    cands.append(Path("/app/docs/admin_operations"))
    return cands


def _kb_dir() -> Optional[Path]:
    for d in _candidate_dirs():
        if d.is_dir():
            return d
    return None


@lru_cache(maxsize=1)
def _load_index() -> dict:
    """`{ "<file>.md#<anchor>": {title, body, file, anchor} }` を構築する。

    admin_operations 側は front-matter を読み飛ばすだけ（解釈しない）・コメント
    除去やテーブル平坦化・TODO除外も行わない（``manual_transforms=False`` で
    ``docs/manual`` 向けの決定論変換を適用しない = 現行挙動を完全維持）。
    """
    kb_dir = _kb_dir()
    if kb_dir is None:
        return {}
    index, _excluded = _kb_index.build_section_index(kb_dir, manual_transforms=False)
    return index


def _normalize_howto(howto_doc: str) -> str:
    """`admin_operations/materials.md#upload` -> `materials.md#upload`。"""
    doc = (howto_doc or "").strip()
    if doc.startswith("admin_operations/"):
        doc = doc[len("admin_operations/") :]
    return doc


def clear_cache() -> None:
    _load_index.cache_clear()


def section_for_howto(howto_doc: str) -> Optional[dict]:
    if not howto_doc:
        return None
    return _load_index().get(_normalize_howto(howto_doc))


def search(query: str, capabilities: list, limit: int = 3) -> list[dict]:
    """role で絞り込み済みの capability 群から、query に最も近い KB 節を返す。

    各結果: ``{capability_id, title, body, citation}``。KB に節が無い capability は
    citation を持つが body="" の「未整備」マーカーとして返しうる（呼び出し側で扱う）。
    """
    q_tokens = _kb_index.tokenize(query)
    scored: list[tuple[float, dict]] = []
    for cap in capabilities:
        section = section_for_howto(cap.howto_doc)
        body = section["body"] if section else ""
        title = section["title"] if section else cap.title
        # スコア: capability title / KB 本文とクエリの語重なり + タイトル一致ボーナス。
        hay = _kb_index.tokenize(f"{cap.title} {cap.description} {title} {body}")
        overlap = len(q_tokens & hay)
        title_bonus = 2 if q_tokens & _kb_index.tokenize(cap.title) else 0
        score = overlap + title_bonus
        if score <= 0:
            continue
        scored.append(
            (
                float(score),
                {
                    "capability_id": cap.id,
                    "screen": cap.screen,
                    "title": title,
                    "body": body,
                    "citation": f"admin_operations/{section['file']}#{section['anchor']}" if section else "",
                    "documented": bool(body),
                },
            )
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    return [item for _, item in scored[: max(1, limit)]]


def kb_available() -> bool:
    return bool(_load_index())
