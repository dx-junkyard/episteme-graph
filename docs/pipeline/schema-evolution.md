# 動的スキーマ進化

[← ドキュメント目次](../README.md) ｜ [← DSL と理論操作グラフ](theory-graph.md)

固定の `OntologyType` / `CorePredicate` を超えて、**運用中に得た学生の質問からグラフ DSL の語彙を成長させる**仕組みです。
関連モジュール: `schema_registry.py` / `meta_analyzer.py` / `simulator.py` / `reextractor.py`。
管理 UI 側の操作は [管理機能](../features/admin.md)。

---

## 1. 全体ワークフロー

```
① 未回答クエリの蓄積
   学生の質問にシステムが答えられない → unanswered_query_logs に記録
        │
② スキーマ提案の生成   meta_analyzer.analyze_unanswered_queries()
   未回答クエリ群 + 現行スキーマ → LLM がパターン分析
   → 不足している OntologyType / CorePredicate を提案
   → schema_proposals / schema_proposal_items に status=pending で保存
        │
③ Shadow Testing       simulator
   承認前に、提案を既存の教材に適用したら抽出がどう変わるかを Before/After で検証
        │
④ 教員が承認/棄却
   PUT /api/admin/schema-proposals/{id}/approve（または approve-with-scope / reject）
   → 承認時 schema_ontology_types / schema_predicates に追加
   → schema_registry.invalidate_cache() でキャッシュ更新
        │
⑤ 再抽出ジョブ          reextractor
   新スキーマで既存ドキュメントをバックグラウンド再抽出
   → reextraction_jobs で進捗管理
```

---

## 2. スキーマレジストリ（`schema_registry.py`）

`OntologyType` / `CorePredicate` を**ハードコード enum ではなく DB から動的にロード**します。

```
schema.py（ビルトイン enum）
   │ seed_builtin_schema()  ← 起動時に DB へ投入（is_builtin=true）
   ▼
PostgreSQL: schema_ontology_types / schema_predicates
   │ get_ontology_types() / get_predicates()  ← 60 秒 TTL キャッシュ、スレッドセーフ
   ▼
LLM 抽出パイプラインのプロンプトに現在の語彙を注入
```

主な関数:
- `seed_builtin_schema()` — 起動時にビルトインを冪等投入
- `get_ontology_types()` / `get_predicates()` — キャッシュ付き取得
- `add_ontology_type()` / `add_predicate()` — 承認後に追加（キャッシュクリア）
- `build_ontology_type_prompt()` / `build_predicate_prompt()` — 現行語彙のプロンプト断片を生成
- `invalidate_cache()` — 承認後に LLM 入力を更新

---

## 3. メタ分析（`meta_analyzer.py`）

`analyze_unanswered_queries()`:
1. 最近の未回答クエリを取得（最大 200、最低 3 件必要）
2. 現行スキーマ語彙を `schema_registry` から取得
3. クエリ + 現行語彙を LLM に渡し、構造化出力（`SchemaAnalysisResult`）で
   - 学生の質問のパターン/テーマ
   - 不足している概念カテゴリ（OntologyType）
   - 不足している関係種別（CorePredicate）
   を抽出
4. `schema_proposals` / `schema_proposal_items` に status=pending で保存
5. 返り値: `proposal_id`, `summary`, `reasoning`, `items`, `source_query_count`, `status`

---

## 4. Shadow Testing（`simulator.py`）

承認前に提案の影響をプレビューします。

1. 提案を引き起こした **対象ドキュメント**を選択
2. 構造的に **類似したドキュメント**を選択
3. **対照（baseline）ドキュメント**を選択
4. 各ドキュメントについて、現行グラフ（Before）と新語彙での抽出（After）を比較
5. Before/After 差分を教員に提示

→ 教員は「新しい OntologyType/CorePredicate を入れると既存抽出がどう変わるか」を確認してから承認できます。
canary 的に一部コースだけに適用する `approve-with-scope` もあります。

---

## 5. 再抽出（`reextractor.py`）

承認後、既存ドキュメントを新スキーマでバックフィルします。

1. **ジョブ登録** — `proposal_id` を受け、status=completed の全 `documents` を数えて `reextraction_jobs` を作成、バックグラウンドスレッド起動
2. **バックグラウンド実行** — 各ドキュメントについて:
   - MinIO から PDF 取得 → テキスト抽出
   - 新スキーマで知識グラフを再構築・再チャンク
   - 旧チャンク削除 → pgvector へ再埋め込み
   - `documents.knowledge_graph` を更新
   - 進捗を `reextraction_jobs.processed_docs` に記録
3. **状態取得** — `get_job_status(job_id)`

---

## 6. 関連テーブル

| テーブル | 役割 |
|---|---|
| `unanswered_query_logs` | スキーマ提案の入力となる未回答質問 |
| `schema_ontology_types` / `schema_predicates` | 動的な概念/関係語彙（`is_builtin` で区別） |
| `schema_proposals` / `schema_proposal_items` | 提案とその項目（status: pending/approved/rejected） |
| `reextraction_jobs` | 再抽出バックグラウンドジョブ |

詳細は [データモデル](../architecture/data-model.md#スキーマ進化)。

---

[← DSL と理論操作グラフ](theory-graph.md) ｜ 次へ: [学習機能 →](../features/learning.md)
