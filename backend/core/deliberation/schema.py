"""W層 core の語彙・dataclass の正本（設計書 §2）。

`ElementRef` は全要素型を1つの多態参照で扱うための抽象。要素には2つの **スコープ** がある:

- ``scope='document'`` : ある1論文から抽出された具体的な出現（インスタンス）。
  ``element_type ∈ {figure, theory_component, theory_claim, equation}``、anchor = ``document_id``。
- ``scope='domain'``   : 複数論文にまたがって再利用したい抽象（共通部品）。1論文に紐づけない。
  ``element_type ∈ {shared_part}``、anchor = ``domain_key``。共通部品の実体は L層 ``library_entries``。

このモジュールは DB にも FastAPI にも依存しない純粋な語彙定義に留める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── スコープ ─────────────────────────────────────────────────────────────
SCOPE_DOCUMENT = "document"
SCOPE_DOMAIN = "domain"
SCOPES = (SCOPE_DOCUMENT, SCOPE_DOMAIN)

# ── 要素型（設計書 §2）────────────────────────────────────────────────────
ELEMENT_FIGURE = "figure"
ELEMENT_THEORY_COMPONENT = "theory_component"
ELEMENT_THEORY_CLAIM = "theory_claim"
ELEMENT_EQUATION = "equation"
ELEMENT_SHARED_PART = "shared_part"

DOCUMENT_ELEMENT_TYPES = (
    ELEMENT_FIGURE,
    ELEMENT_THEORY_COMPONENT,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
)
DOMAIN_ELEMENT_TYPES = (ELEMENT_SHARED_PART,)
ELEMENT_TYPES = DOCUMENT_ELEMENT_TYPES + DOMAIN_ELEMENT_TYPES


class ElementResolutionError(Exception):
    """ElementRef の解決に失敗した（要素型が不正 / スコープ不一致 / 要素が存在しない）。

    API 層はこれを 404 / 422 にマッピングする（core は HTTP を知らない）。
    """

    def __init__(self, message: str, *, kind: str = "not_found") -> None:
        super().__init__(message)
        self.kind = kind  # 'invalid' | 'not_found'


def scope_for_element_type(element_type: str) -> str:
    """要素型からスコープを一意に導出する（要素型とスコープは1対多だが逆は一意）。"""
    if element_type in DOCUMENT_ELEMENT_TYPES:
        return SCOPE_DOCUMENT
    if element_type in DOMAIN_ELEMENT_TYPES:
        return SCOPE_DOMAIN
    raise ElementResolutionError(
        f"unknown element_type: {element_type!r}", kind="invalid"
    )


@dataclass
class ElementRef:
    """要素への多態参照（設計書 §2）。

    - ``scope='document'`` のとき ``document_id`` が非空・``domain_key`` は None。
    - ``scope='domain'``   のとき ``domain_key`` が非空・``document_id`` は None。

    ``element_id`` の実体は要素型ごとに異なる（設計書 §2 の表）:
    figure=document_figures.id / theory_component=theory_components.id(UUID) /
    theory_claim=theory_claims.id(UUID) / equation=equations.json の equation_id /
    shared_part=library_entries.id。
    """

    scope: str
    element_type: str
    element_id: str
    document_id: str | None = None
    domain_key: str | None = None
    # resolver が補助的に載せる出所情報（course_id・chunk_id・run_id 等）。API 応答の
    # デバッグ/リンク生成に使う。中身は要素型依存で緩く保持する（情報を落とさない・W4）。
    provenance: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.element_type not in ELEMENT_TYPES:
            raise ElementResolutionError(
                f"unknown element_type: {self.element_type!r}", kind="invalid"
            )
        expected_scope = scope_for_element_type(self.element_type)
        if self.scope != expected_scope:
            raise ElementResolutionError(
                f"element_type {self.element_type!r} requires scope "
                f"{expected_scope!r}, got {self.scope!r}",
                kind="invalid",
            )
        if not str(self.element_id or "").strip():
            raise ElementResolutionError("element_id is empty", kind="invalid")
        if self.scope == SCOPE_DOCUMENT and not str(self.document_id or "").strip():
            raise ElementResolutionError(
                "document-scoped ElementRef requires document_id", kind="not_found"
            )
        if self.scope == SCOPE_DOMAIN and not str(self.domain_key or "").strip():
            raise ElementResolutionError(
                "domain-scoped ElementRef requires domain_key", kind="not_found"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "element_type": self.element_type,
            "element_id": self.element_id,
            "document_id": self.document_id,
            "domain_key": self.domain_key,
            "provenance": dict(self.provenance),
        }
