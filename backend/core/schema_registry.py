"""動的スキーマレジストリ — DBからOntologyType/CorePredicateを動的に読み込む。

固定のEnum定義（schema.py）をシードデータとして初期投入し、
教員の承認を経て追加された新しい概念カテゴリ・関係性を
LLM抽出パイプラインに動的に反映する。

Usage::

    from core.schema_registry import get_ontology_types, get_predicates, seed_builtin_schema

    # 起動時にビルトイン定義をDBにシード
    seed_builtin_schema()

    # 抽出時に動的に読み込み
    types = get_ontology_types()  # [{"id": "Agent", "label": "Agent", ...}, ...]
    predicates = get_predicates()  # [{"id": "CAUSES", "label": "CAUSES", ...}, ...]
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session
from core.schema import CorePredicate, OntologyType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "ontology_types": None,
    "predicates": None,
    "ts": 0.0,
}
_CACHE_TTL = 60.0  # seconds


def invalidate_cache() -> None:
    """キャッシュを無効化する。スキーマ変更時に呼び出す。"""
    with _cache_lock:
        _cache["ontology_types"] = None
        _cache["predicates"] = None
        _cache["ts"] = 0.0


# ---------------------------------------------------------------------------
# Seed builtin schema
# ---------------------------------------------------------------------------

# ビルトインOntologyTypeの説明
_BUILTIN_ONTOLOGY_DESCRIPTIONS: dict[str, str] = {
    "Agent": "人間、組織、機関、チーム、政府、法人、行動を起こす主体",
    "Event": "発生、プロセス、現象、動的な変化",
    "Resource": "物質、情報、資本、出力、成果物",
    "Intentional Moment": "意図、目標、信念、コミットメント、心的状態",
    "MathematicalObject": "テンソル、群、多様体、作用素、スピノル、計量",
    "PhysicalPhenomenon": "相転移、散乱、崩壊、輻射補正、閉じ込め",
    "TheoreticalFramework": "QFT、QED、QCD、標準模型、弦理論、有効場理論",
    "Theorem": "Noetherの定理、Ward恒等式、LSZ公式、CPT定理",
    "Symmetry": "ゲージ対称性、ローレンツ対称性、CPT対称性、カイラル対称性",
    "Particle": "クォーク、レプトン、ゲージボソン、ヒッグス場、グルーオン",
}

# ビルトインCorePredicateの説明
_BUILTIN_PREDICATE_DESCRIPTIONS: dict[str, str] = {
    "CAUSES": "一方の変数が他方を直接生成またはトリガーする",
    "INHIBITS": "一方の変数が他方を抑制、ブロック、または減少させる",
    "CORRELATES": "明確な方向性なしに2つの変数が共変する",
    "DEFINES": "一方の変数が他方を特徴づけ、規定、または構成する",
    "MEASURES": "一方の変数が他方を操作化または定量化する",
    "TRANSFORMS": "一方の変数が他方の状態を変換または変更する",
    "REQUIRES": "一方の変数が他方に依存または前提とする（前提条件/依存関係）",
    "CONTAINS": "親子（マクロ-ミクロ）の包含関係。ソースがターゲットを構成要素として含む",
    "EQUIVALENT": "同一階層レベルの概念間の等価性またはコントラスト（横方向の関係）",
}


def seed_builtin_schema() -> None:
    """ビルトインのOntologyTypeとCorePredicateをDBにシードする（冪等）。"""
    session = _pg_session()
    try:
        # OntologyTypes
        for ot in OntologyType:
            desc = _BUILTIN_ONTOLOGY_DESCRIPTIONS.get(ot.value, "")
            session.execute(
                sa_text("""
                    INSERT INTO schema_ontology_types (id, label, description, is_builtin)
                    VALUES (:id, :label, :description, true)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        description = EXCLUDED.description,
                        is_builtin = true
                """),
                {"id": ot.value, "label": ot.value, "description": desc},
            )

        # CorePredicates
        for cp in CorePredicate:
            desc = _BUILTIN_PREDICATE_DESCRIPTIONS.get(cp.value, "")
            session.execute(
                sa_text("""
                    INSERT INTO schema_predicates (id, label, description, is_builtin)
                    VALUES (:id, :label, :description, true)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        description = EXCLUDED.description,
                        is_builtin = true
                """),
                {"id": cp.value, "label": cp.value, "description": desc},
            )

        session.commit()
        logger.info("Seeded %d ontology types and %d predicates", len(OntologyType), len(CorePredicate))
    except Exception:
        session.rollback()
        logger.exception("Failed to seed builtin schema")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Read dynamic schema
# ---------------------------------------------------------------------------


def _load_from_db() -> tuple[list[dict], list[dict]]:
    """DBからオントロジータイプと述語を読み込む。"""
    session = _pg_session()
    try:
        ot_rows = session.execute(
            sa_text("SELECT id, label, description, is_builtin FROM schema_ontology_types ORDER BY is_builtin DESC, id")
        ).fetchall()
        pred_rows = session.execute(
            sa_text("SELECT id, label, description, is_builtin FROM schema_predicates ORDER BY is_builtin DESC, id")
        ).fetchall()
    finally:
        session.close()

    ontology_types = [
        {"id": r[0], "label": r[1], "description": r[2], "is_builtin": r[3]}
        for r in ot_rows
    ]
    predicates = [
        {"id": r[0], "label": r[1], "description": r[2], "is_builtin": r[3]}
        for r in pred_rows
    ]
    return ontology_types, predicates


def _ensure_cache() -> None:
    """キャッシュが有効であることを保証する。"""
    with _cache_lock:
        if _cache["ontology_types"] is not None and (time.time() - _cache["ts"]) < _CACHE_TTL:
            return

    ontology_types, predicates = _load_from_db()
    with _cache_lock:
        _cache["ontology_types"] = ontology_types
        _cache["predicates"] = predicates
        _cache["ts"] = time.time()


def get_ontology_types() -> list[dict]:
    """登録済みのOntologyType一覧を返す（キャッシュ付き）。"""
    _ensure_cache()
    with _cache_lock:
        return list(_cache["ontology_types"] or [])


def get_predicates() -> list[dict]:
    """登録済みのCorePredicate一覧を返す（キャッシュ付き）。"""
    _ensure_cache()
    with _cache_lock:
        return list(_cache["predicates"] or [])


# ---------------------------------------------------------------------------
# Add new schema elements
# ---------------------------------------------------------------------------


def add_ontology_type(type_id: str, label: str, description: str = "") -> None:
    """新しいOntologyTypeをDBに追加する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO schema_ontology_types (id, label, description, is_builtin)
                VALUES (:id, :label, :description, false)
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": type_id, "label": label, "description": description},
        )
        session.commit()
        invalidate_cache()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def add_predicate(pred_id: str, label: str, description: str = "") -> None:
    """新しいCorePredicateをDBに追加する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO schema_predicates (id, label, description, is_builtin)
                VALUES (:id, :label, :description, false)
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": pred_id, "label": label, "description": description},
        )
        session.commit()
        invalidate_cache()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Build dynamic prompt fragment
# ---------------------------------------------------------------------------


def build_ontology_type_prompt() -> str:
    """LLMプロンプト用のOntologyType一覧テキストを生成する。"""
    types = get_ontology_types()
    lines = []
    for t in types:
        suffix = f" — {t['description']}" if t["description"] else ""
        lines.append(f"  {t['label']}{suffix}")
    return "\n".join(lines)


def build_predicate_prompt() -> str:
    """LLMプロンプト用のCorePredicate一覧テキストを生成する。"""
    preds = get_predicates()
    lines = []
    for p in preds:
        suffix = f" → {p['description']}" if p["description"] else ""
        lines.append(f"  {p['label']}{suffix}")
    return "\n".join(lines)


def get_ontology_type_names() -> list[str]:
    """OntologyType名のリストを返す（バリデーション用）。"""
    return [t["id"] for t in get_ontology_types()]


def get_predicate_names() -> list[str]:
    """Predicate名のリストを返す（バリデーション用）。"""
    return [p["id"] for p in get_predicates()]
