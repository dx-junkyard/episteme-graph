"""W層（Element Deliberation Workspace / 要素検討ワークスペース）core パッケージ。

パイプライン（A層）が生成した1要素を文脈の中で深掘りするための **読む側** の集約層。
`docs/features/element_deliberation_workspace_design.md` が設計の正本。

Phase 0 スコープ:
- `schema.py`   : ElementRef・スコープ・要素型・同一性リンク状態の語彙と dataclass の正本
- `refs.py`     : ElementRef の解決（存在確認 + document_id/domain_key 補完）
- `decomposition.py` : 面①「内訳・同定」の組み立て（既存データの読み出しのみ・非LLM）
- `positioning.py`   : 面②「文脈的位置づけ」の4レンズ合成（既存データの読み出しのみ・非LLM）

Phase W-β スコープ（同一性リンク。知識ネットワークビジョン §7 / W層設計 §5.5）:
- `identity_links.py` : `element_identity_links`（migration 048）の DB プリミティブ。
  instance（scope='document'）↔ shared_part（L層 library_entries）の同一性を
  candidate → 人間確定（confirmed/rejected）のリンクとして記録する（KN-2/KN-3）。

**開発ルール2**: 本パッケージは FastAPI を import しない（テスタビリティ確保）。
権限ゲート（`_ensure_document_viewable` / `_ensure_document_editable` 等）は
API 層（`routes/deliberation.py`）の責務。
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
