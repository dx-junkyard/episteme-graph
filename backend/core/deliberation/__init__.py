"""W層（Element Deliberation Workspace / 要素検討ワークスペース）core パッケージ。

パイプライン（A層）が生成した1要素を文脈の中で深掘りするための **読む側** の集約層。
`docs/features/element_deliberation_workspace_design.md` が設計の正本。

Phase 0 スコープ（本パッケージ現状）:
- `schema.py`   : ElementRef・スコープ・要素型の語彙と dataclass の正本
- `refs.py`     : ElementRef の解決（存在確認 + document_id/domain_key 補完）
- `decomposition.py` : 面①「内訳・同定」の組み立て（既存データの読み出しのみ・非LLM）

**開発ルール2**: 本パッケージは FastAPI を import しない（テスタビリティ確保）。
権限ゲート（`_ensure_document_viewable` 等）は API 層（`routes/deliberation.py`）の責務。
"""

from core.deliberation.schema import (
    DOMAIN_ELEMENT_TYPES,
    DOCUMENT_ELEMENT_TYPES,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
    ElementResolutionError,
)

__all__ = [
    "DOMAIN_ELEMENT_TYPES",
    "DOCUMENT_ELEMENT_TYPES",
    "SCOPE_DOCUMENT",
    "SCOPE_DOMAIN",
    "ElementRef",
    "ElementResolutionError",
]
