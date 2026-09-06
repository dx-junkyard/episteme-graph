---
screen: llm-usage
role: SYSTEM_ADMIN
---

# LLM使用量（U層 / llm-usage）の操作

「LLM使用量」タブでは、システム全体の LLM トークン消費量を確認します。

## LLM使用量メトリクスを確認する {#view-metrics}

**対象タブ:** LLM使用量（llm-usage） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ

1. 「LLM使用量」タブを開きます。集計期間が表の上に表示されます。
2. 実測（reported）と推計（estimated）が分離して集計表示されます。混ぜた単一数値は表示
   しません。推計には、トークナイザによるもの（estimated_tokenizer）と簡易式によるもの
   （estimated_heuristic）があります。
3. 集計の切り口は右上のセレクタで切り替えます（feature × model / feature / model / provider /
   operation / day / ユーザー別 / ユーザー別 × feature）。「更新」で読み直します。
4. バッファ溢れ（dropped_events）は 0 件でも必ず表示されます（隠しません）。
5. 価格表（`LLM_PRICE_TABLE_PATH`）が設定されていれば概算費用も表示されます。設定が無い
   場合は費用は空欄のままです（金額のハードコードはしません）。

教材ごとの解析コスト事前見積り（レンジのみ・金額なし）は教材管理タブから確認できます
（`admin_operations/materials.md#estimate-cost` 参照）。
