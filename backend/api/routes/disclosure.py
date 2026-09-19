"""可視性6軸カタログの公開 API — **宣言だけ**を全当事者に開く（読み取り専用）。

正本設計書: ``docs/features/disclosure_axes_design.md``（DA1〜DA6）。
カタログ本体は ``core/disclosure_axes.py``。

不変条項の実装点:

- **DA3** 依存は ``_get_current_user``（**``_require_teacher`` ではない**）。
  自分の入力がどこへ送られるかは、送っている当事者である学習者が読めなければ
  意味がない（``routes/indicators.py`` の IG1 と同じ立場）。
- **DA2** 宛先は provider だけ。provider 名はカタログに焼き込まず、実行時に
  ``core/config.py`` の ``llm_provider`` から解決して差し込む（設定を変えたときに
  カタログが嘘にならないようにする）。モデル名・トークン数・金額は返さない。
- **DA5** 同意を取らない。書き込みメソッドを作らない（告知の層であって、承諾の
  記録を作る層ではない）。カタログはコードが正本なので API から変えられない。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from dependencies import _get_current_user

from core import privacy
from core.config import get_settings
from core.disclosure_axes import (
    DISCLOSURE_NOTE,
    axes_public_view,
    catalog_public_view,
    get_data_kind,
    provider_label,
)

router = APIRouter(prefix="/api/disclosure", tags=["Disclosure"])


def _provider() -> dict:
    """実行時の provider（キーと表示名）。設定が読めないときは総称へ縮退する。"""
    try:
        key = str(get_settings().llm_provider or "")
    except Exception:  # pragma: no cover - 設定読み込み失敗時も告知は出す
        key = ""
    return {"key": key, "label": provider_label(key)}


@router.get("")
def list_disclosure(current_user: dict = Depends(_get_current_user)) -> dict:
    """可視性6軸の宣言（全データ種別）。認証済みなら誰でも読める。"""
    provider = _provider()
    return {
        "axes": axes_public_view(),
        "data_kinds": catalog_public_view(provider_key=provider["key"]),
        "provider": provider,
        "note": DISCLOSURE_NOTE,
        "k_anonymity": privacy.K_ANONYMITY,
    }


@router.get("/{data_kind}")
def get_disclosure_detail(
    data_kind: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """1件の宣言。未知の data_kind は 404（無いデータ種別を作って見せない）。"""
    spec = get_data_kind(data_kind)
    if spec is None:
        raise HTTPException(status_code=404, detail="Disclosure entry not found")
    provider = _provider()
    return {
        "axes": axes_public_view(),
        "data_kind": spec.public_dict(provider=provider["label"]),
        "provider": provider,
        "note": DISCLOSURE_NOTE,
        "k_anonymity": privacy.K_ANONYMITY,
    }
