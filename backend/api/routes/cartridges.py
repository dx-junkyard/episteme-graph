"""Domain cartridge endpoints (Issue #176)。

`backend/cartridges/` 配下に格納されたカートリッジ定義を読み取り専用で公開する。
書き込みは MVP では不要。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from core.cartridges import (
    CartridgeSummary,
    DomainCartridge,
    list_cartridges,
    load_cartridge,
)
from dependencies import _require_teacher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cartridges", tags=["Admin"])


def _summary_to_dict(summary: CartridgeSummary) -> dict:
    return {
        "cartridge_id": summary.cartridge_id,
        "name": summary.name,
        "version": summary.version,
        "description": summary.description,
        "target_domain": list(summary.target_domain),
    }


def _load_or_404(cartridge_id: str) -> DomainCartridge:
    try:
        return load_cartridge(cartridge_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cartridge '{cartridge_id}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def list_available_cartridges(current_user: dict = Depends(_require_teacher)) -> list[dict]:
    """利用可能なカートリッジのサマリ一覧を返す。"""
    return [_summary_to_dict(summary) for summary in list_cartridges()]


@router.get("/{cartridge_id}")
def get_cartridge(cartridge_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    """カートリッジ全体 (manifest + ontology + 各種定義) を返す。"""
    cartridge = _load_or_404(cartridge_id)
    return cartridge.to_dict()


@router.get("/{cartridge_id}/ontology")
def get_cartridge_ontology(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """カートリッジの ontology 部分のみを返す。"""
    cartridge = _load_or_404(cartridge_id)
    return {
        "concept_types": list(cartridge.ontology.concept_types),
        "aliases": list(cartridge.ontology.aliases),
        "notation_patterns": list(cartridge.ontology.notation_patterns),
        "extraction_hints": list(cartridge.ontology.extraction_hints),
        "normalization_rules": list(cartridge.ontology.normalization_rules),
    }


@router.get("/{cartridge_id}/component-types")
def get_cartridge_component_types(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    cartridge = _load_or_404(cartridge_id)
    return {"component_types": list(cartridge.component_types)}


@router.get("/{cartridge_id}/relation-types")
def get_cartridge_relation_types(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    cartridge = _load_or_404(cartridge_id)
    return {"relation_types": list(cartridge.relation_types)}


@router.get("/{cartridge_id}/maturity-levels")
def get_cartridge_maturity_levels(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    cartridge = _load_or_404(cartridge_id)
    return {
        "maturity_levels": list(cartridge.maturity_levels),
        "maturity_sources": list(cartridge.maturity_sources),
    }


@router.get("/{cartridge_id}/support-statuses")
def get_cartridge_support_statuses(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    cartridge = _load_or_404(cartridge_id)
    return {"support_statuses": list(cartridge.support_statuses)}
