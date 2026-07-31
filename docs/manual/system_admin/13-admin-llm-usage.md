---
audience: system_admin
screen: llm-usage
---

# LLM使用量（管理画面）

[← マニュアル索引](../README.md)

「LLM使用量」タブでは、システム全体の LLM トークン消費量を確認します（基本操作の要約は
[LLM使用量メトリクスを確認する](../../admin_operations/llm_usage.md#view-metrics) も
参照してください）。**このタブはシステム管理者（SYSTEM_ADMIN）ロールでログインしたときの
み表示されます。** ヘッダー・タブナビゲーション・確認モーダルなど、管理画面に共通の操作は
[管理画面の共通操作](../teacher/10-admin-common.md) を参照してください。

## 画面の概要 {#overview}

表には、選んだ集計軸（内訳）ごとに、実測（reported）と推計（estimated）のトークン数・
呼び出し回数が分けて表示されます。混ぜた単一の数値は表示されません。

---

## メトリクスを確認する {#metrics}

### 集計軸を選ぶ {#groupby}

内訳の粒度を選びます。「feature × model」（既定）・「feature」・「model」・
「provider」・「operation」・「day」から選べます。選ぶと自動的に表が再取得されます。

### 更新 {#refresh}

現在選んでいる集計軸のまま、表を再取得します。

### 表示される数値の見方 {#reading-metrics}

- 実測（reported）と推計（estimated_tokenizer / estimated_heuristic）は常に分離して
  集計表示されます。合算した単一数値は作られません。
- 記録バッファから溢れて記録できなかったイベント数（dropped_events）は、0件のときも
  必ず表示されます（隠しません）。
- 「費用(概算)」列は、価格表（環境変数 `LLM_PRICE_TABLE_PATH`）が設定されている場合のみ
  金額が入ります。設定が無い場合、費用は表示されません（金額のハードコードはしません）。

教材ごとの解析コスト**事前見積り**（レンジのみ・金額なし）は、教員（TEACHER）以上が
教材管理タブから確認できます。本タブとは別の窓口です。

---

[← マニュアル索引](../README.md)
