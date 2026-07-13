"""ナレッジライブラリ（L層）— 分野ごとの教員共同財としての知識エントリ。

物理の実験機器（apparatus）を皮切りに、document 単位に閉じていた理論コンポーネント
（theory_component）を分野横断で蓄積・精錬できる器。設計は
docs/features/image_pipeline_knowledge_library_design.md §6 を正本とする。
`core/atlas_store.py`（骨格 draft/凍結/楽観ロック/シード冪等取込）と同型のパターンを踏襲する。

- draft (`library_entries`) が正本。`library_entry_versions` は不変の凍結版履歴。
- パイプライン（apparatus_semantics 等）が参照するのは**凍結版のみ**（draft は使わない）。
- 削除しない（P4）。`status='retired'` 遷移のみで行削除 API は無い。
- LLM がライブラリへ直接書き込む経路は無い（昇格は人間の操作のみ、原則2）。
- 本パッケージは FastAPI を import しない（core/ 共通ルール。テスタビリティ確保）。

各モジュール:
    schema — dataclass・語彙（entry_type / status）・JSONB 変換ヘルパの正本
    store  — draft の作成・取得・一覧・楽観ロック更新・凍結・retire/restore
    search — 凍結版のみを対象にした embedding 類似検索（retrieval）+ 昇格モーダルの類似提示
    seed   — カートリッジ同梱ライブラリ JSON の起動時冪等取込
"""

from __future__ import annotations

from . import schema, search, seed, store  # noqa: F401
