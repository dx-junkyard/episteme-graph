"""``docs/manual/`` の audience 別索引（学生/教員/管理者 HELP アシスタントの知識源）。

正本ディレクトリは ``docs/manual/<audience>/``（``student`` / ``teacher`` /
``system_admin`` の3つ。``README.md`` は人間用索引で KB 非対象）。

**audience の見える範囲は上位継承**: student → student/ のみ / teacher →
student/ + teacher/ / system_admin → 3ディレクトリ全部。

**学生索引ビルダーは student/ のパスしか受け取らない構造**（``_student_index`` の
ソースは ``"student"`` リテラルのみを組み、teacher/system_admin への参照を一切
含まない — 呼び出し時フィルタでなくコード構造で fail-closed を保証する）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import index as _index

logger = logging.getLogger(__name__)

AUDIENCES = ("student", "teacher", "system_admin")

# audience ごとに閲覧できるディレクトリ（上位継承）。
_VISIBLE_DIRS = {
    "student": ("student",),
    "teacher": ("student", "teacher"),
    "system_admin": ("student", "teacher", "system_admin"),
}


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    cands: list[Path] = []
    # backend/core/help_kb/manual.py -> parents[3] = repo root
    try:
        cands.append(here.parents[3] / "docs" / "manual")
    except IndexError:
        pass
    # 実行 cwd からの探索（docker / 別レイアウト対策）
    cwd = Path.cwd()
    cands.append(cwd / "docs" / "manual")
    cands.append(cwd.parent / "docs" / "manual")
    # コンテナ向け保険候補（Phase 0: Dockerfile に docs/manual を COPY する前提）
    cands.append(Path("/app/docs/manual"))
    return cands


def manual_root() -> Optional[Path]:
    for d in _candidate_roots():
        if d.is_dir():
            return d
    return None


def _dir_for(name: str) -> Optional[Path]:
    root = manual_root()
    if root is None:
        return None
    d = root / name
    return d if d.is_dir() else None


def _build_for_dir_name(name: str) -> tuple[dict, list[dict]]:
    """audience 用の索引を構築する。

    配信ソースの既定は ``files``（Phase 1 運用は変えない）。Phase 3 の DB draft/freeze
    （``core/help_kb/store.py``）で明示 freeze され、かつ ``serving_source='db'`` に
    切り替えられた場合のみ DB の active version テキストから構築する。DB 参照時の
    例外・active version 不在は files へ fail-open する（マニュアル配信を止めない）。
    """
    if name in AUDIENCES:
        try:
            from . import store as _store  # 遅延 import（循環回避・store 未整備環境への耐性）

            state = _store.get_state()
            if state.get("serving_source") == "db" and state.get("active_version_no"):
                texts = _store.get_active_version_texts()
                if texts is not None:
                    return _index.build_section_index_from_texts(
                        texts.get(name) or {}, manual_transforms=True, audience_tag=name,
                    )
        except Exception:  # noqa: BLE001 — DB 障害時は files 配信へ fail-open
            logger.warning(
                "help_kb DB serving lookup failed for %s; falling back to files", name, exc_info=True,
            )

    directory = _dir_for(name)
    if directory is None:
        return {}, []
    return _index.build_section_index(directory, manual_transforms=True, audience_tag=name)


@lru_cache(maxsize=1)
def _student_index() -> tuple[dict, list[dict]]:
    """学生索引は ``docs/manual/student/`` のみを読む（他 audience 不参照）。"""
    return _build_for_dir_name("student")


@lru_cache(maxsize=1)
def _teacher_index() -> tuple[dict, list[dict]]:
    return _build_for_dir_name("teacher")


@lru_cache(maxsize=1)
def _system_admin_index() -> tuple[dict, list[dict]]:
    return _build_for_dir_name("system_admin")


_INDEX_BUILDERS = {
    "student": _student_index,
    "teacher": _teacher_index,
    "system_admin": _system_admin_index,
}


def clear_manual_cache() -> None:
    _student_index.cache_clear()
    _teacher_index.cache_clear()
    _system_admin_index.cache_clear()


def _sections_for_audience(audience: str) -> list[dict]:
    if audience not in _VISIBLE_DIRS:
        raise ValueError(f"unknown audience: {audience!r}")
    sections: list[dict] = []
    for dirname in _VISIBLE_DIRS[audience]:
        idx, _excluded = _INDEX_BUILDERS[dirname]()
        sections.extend(idx.values())
    return sections


def manual_available(audience: str) -> bool:
    return bool(_sections_for_audience(audience))


def excluded_sections() -> list[dict]:
    """TODO マーカー入りのため索引から除外されたチャンク一覧（全 audience 分）。"""
    out: list[dict] = []
    for name in AUDIENCES:
        _idx, excluded = _INDEX_BUILDERS[name]()
        out.extend(excluded)
    return out


def section_title_for_citation(citation: str) -> Optional[str]:
    """``manual/<audience>/<file>#<anchor>`` 形式の引用から節タイトルを引く。

    G層 To-Do の理由文など、人間向け表示で anchor パスの代わりに節名を出すための
    読み取り専用ヘルパー。解決できなければ None（呼び出し側が anchor パスへ縮退）。
    """
    ref = (citation or "").strip()
    if not ref.startswith("manual/") or "#" not in ref:
        return None
    rest = ref[len("manual/") :]
    if "/" not in rest:
        return None
    aud, tail = rest.split("/", 1)
    fname, anchor = tail.split("#", 1)
    if aud not in AUDIENCES:
        return None
    idx, _excluded = _INDEX_BUILDERS[aud]()
    sec = idx.get(f"{fname}#{anchor}")
    if sec is None:
        return None
    title = (sec.get("title") or "").strip()
    return title or None


def search_manual(
    query: str,
    *,
    audience: str,
    limit: int = 3,
    screen: Optional[str] = None,
) -> list[dict]:
    """``audience`` から見える節のうち query に最も近いものを返す。

    audience は必須キーワード引数（fail-closed の強制点）。``"student"`` /
    ``"teacher"`` / ``"system_admin"`` 以外は ``ValueError``。

    ``screen``（§4-3 コンテキストヘルプ）を指定すると、front-matter ``screen:``
    が一致する節を検索ランキングの第一候補にする（語彙重なりスコアが正の節に限る。
    詳細は ``index.score_sections`` を参照）。``screen=None`` は完全に従来挙動
    （後方互換）。返却 dict の形（キー集合）は screen 指定の有無で変わらない。
    """
    sections = _sections_for_audience(audience)
    scored = _index.score_sections(query, sections, limit=limit, screen=screen)
    results: list[dict] = []
    for sec in scored:
        sec_audience = sec.get("audience", audience)
        body = sec.get("body", "")
        results.append(
            {
                "file": sec["file"],
                "anchor": sec["anchor"],
                "title": sec["title"],
                "body": body,
                "audience": sec_audience,
                "citation": f"manual/{sec_audience}/{sec['file']}#{sec['anchor']}",
                "documented": bool(body.strip()),
            }
        )
    return results
