"""知識オブジェクト層（knowledge_objects_design.md）。

論文の構造化成果（claim / component / equation / evidence / derivation step / symbol）を
内容由来の版非依存キー（stable_key）を持つ一級の行として扱うための core モジュール群。
FastAPI / LLM を import しない（純関数 + SQL ヘルパ）。A層（``src/episteme_graph/agents/``）は
読むだけで改変しない（KO1）。

- :mod:`.stable_key` — stable_key の計算（純関数・決定論・非LLM。KO2）
- :mod:`.sync` — live 行の同期（一致 = 同 UUID 更新 / 不一致 = superseded 刻印 / 新規 = INSERT。KO3）
- :mod:`.remap` — agent ID の付け替え記録と参照の再係留（KO8）
- :mod:`.backfill` — 既存行への stable_key バックフィル（起動時・fail-open）
"""
