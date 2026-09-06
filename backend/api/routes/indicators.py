"""制度指標カタログの公開 API — **定義だけ**を全当事者に開く（読み取り専用）。

正本設計書: ``docs/features/indicator_governance_design.md``（IG1〜IG5）。
カタログ本体は ``core/indicator_catalog.py``。

不変条項の実装点:

- **IG1** 依存は ``_get_current_user``（**``_require_teacher`` ではない**）。
  制度を観察する計器の定義は、観察される側である学習者も読めなければ意味がない
  （vision §6.1 の「全当事者」行）。ただし公開するのは定義だけで、**値は1つも
  返さない** — 各計器の値は従来どおりそれぞれの API のロールゲートの内側にある。
  ``readable_by_me`` は「あなたはその計器の**値**を読めるか」という事実の投影で
  あって、ここでの認可判断ではない（この API は誰にでも全件の定義を返す）。
- **IG2/IG3** 非利用4項目・粒度・副作用レビューは spec 側で構造強制済み。
  本ルーターは整形しない（勝手に間引かない）。
- 書き込みメソッドを作らない（カタログはコードが正本 — API から変えられない）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from dependencies import ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _get_current_user

from core import privacy
from core.indicator_catalog import (
    AUDIENCE_LEARNER_SELF,
    AUDIENCE_SYSTEM_ADMIN,
    AUDIENCE_TEACHER,
    CATALOG_NOTE,
    catalog_public_view,
    get_indicator,
)

router = APIRouter(prefix="/api/indicators", tags=["Indicators"])


def _can_read_values(role: str, values_audience: str) -> bool:
    """呼び出し元のロールが、その計器の**値**を読める立場かどうか。

    - ``learner_self``: 誰でも「自分の分」を読める（本人の記録なので全員 True）。
    - ``teacher``: TEACHER 以上。
    - ``system_admin``: SYSTEM_ADMIN のみ。
    """
    if values_audience == AUDIENCE_LEARNER_SELF:
        return True
    if values_audience == AUDIENCE_TEACHER:
        return role in (ROLE_TEACHER, ROLE_SYSTEM_ADMIN)
    if values_audience == AUDIENCE_SYSTEM_ADMIN:
        return role == ROLE_SYSTEM_ADMIN
    return False


def _with_readability(items: list[dict], role: str) -> list[dict]:
    for item in items:
        item["readable_by_me"] = _can_read_values(role, item.get("values_audience", ""))
    return items


@router.get("")
def list_indicators(current_user: dict = Depends(_get_current_user)) -> dict:
    """制度指標カタログの全件（定義のみ・値なし）。認証済みなら誰でも読める。"""
    role = str(current_user.get("role") or "")
    return {
        "indicators": _with_readability(catalog_public_view(), role),
        "note": CATALOG_NOTE,
        "k_anonymity": privacy.K_ANONYMITY,
    }


@router.get("/{indicator_id}")
def get_indicator_detail(
    indicator_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """1件の定義。未知の id は 404（存在しない計器を作って見せない）。"""
    spec = get_indicator(indicator_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Indicator not found")
    role = str(current_user.get("role") or "")
    item = spec.public_dict()
    item["readable_by_me"] = _can_read_values(role, item["values_audience"])
    return {
        "indicator": item,
        "note": CATALOG_NOTE,
        "k_anonymity": privacy.K_ANONYMITY,
    }
