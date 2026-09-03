# 動的スキーマ進化

[← ドキュメント目次](../README.md) ｜ [← DSL と理論操作グラフ](theory-graph.md)

固定の `OntologyType` / `CorePredicate` を超えて、**運用中に得た学生の質問からグラフ DSL の語彙を成長させる**仕組みです。
関連モジュール: `backend/core/schema_registry.py` / `meta_analyzer.py` / `simulator.py` / `reextractor.py`、
API は `backend/api/routes/admin.py`。管理 UI 側の操作は [管理機能](../features/admin.md)。

> **更新注記（2026-09-03）— この層の実装状況**
> テーブル・モジュール・API・UI は**実装され、配線もされています**（未実装の構想ではない）。
> ただし本層は **A層（PDF 解析 Agent パイプライン）とは別系統の旧経路**の上に載っており、
> 現行パイプラインとは次の 3 点で接続していません。以下 §2 / §4 / §5 の各節に明記します。
> 1. 動的語彙（`schema_ontology_types` / `schema_predicates`）を読むのは **Shadow Testing の
>    シミュレーションと提案生成だけ**で、A層 Agent のプロンプトには注入されない
>    （A層のドメイン語彙は[カートリッジ](cartridges.md)から来る）。
> 2. Shadow Testing が読む `documents.knowledge_graph` は A層パイプラインが書かない
>    （旧「構造再解析」経路のみが書く → [DSL と理論操作グラフ §4](theory-graph.md#4-理論コンポーネント抽出theory_componentspy)）。
> 3. 再抽出ジョブは旧 `services.build_knowledge_graph` / `chunk_text` / `embed_chunks` 経路で
>    チャンクを作り直すため、**A層パイプラインが作ったチャンクを破棄する**（§5 の警告）。
>
> これらは「文書の記述漏れ」ではなく実装の現状です。整理の要否はオーナー判断の未決事項です。

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

**①の記録点**: 学習チャットが RAG で答えられなかったときに
`services.log_unanswered_query()` が `unanswered_query_logs` に 1 行足す（`routes/learning.py` から呼ぶ）。
**⑤は④に自動で続く** — `approve` / `approve-with-scope` は承認と同時に再抽出ジョブを
enqueue する（教員が別途起動する操作ではない。§5 の警告を必ず読むこと）。

### エンドポイント（すべて `_require_teacher`、`routes/admin.py`）

| メソッド・パス | 役割 |
|---|---|
| `GET /api/admin/schema-proposals` | 提案一覧（`status` で絞込） |
| `POST /api/admin/schema-proposals/analyze` | 未回答クエリを分析して提案を生成（生成なしなら事実文を返す） |
| `POST /api/admin/schema-proposals/{id}/simulate` | Shadow Testing を実行 |
| `PUT /api/admin/schema-proposals/{id}/approve` | 承認 + 再抽出ジョブ enqueue |
| `PUT /api/admin/schema-proposals/{id}/approve-with-scope` | scope=`full` / `canary` 付き承認（§4 の注意） |
| `PUT /api/admin/schema-proposals/{id}/reject` | 却下 |
| `GET /api/admin/reextraction-jobs` | 再抽出ジョブ一覧 |
| `GET /api/admin/courses/{course_id}/unanswered-queries` | コースの未回答クエリ一覧 |

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
① 提案生成（meta_analyzer）② Shadow Testing（simulator）③ 管理 API の語彙一覧
```

主な関数:
- `seed_builtin_schema()` — 起動時にビルトインを冪等投入（`api/main.py` の lifespan から）
- `get_ontology_types()` / `get_predicates()` — キャッシュ付き取得
- `add_ontology_type()` / `add_predicate()` — 承認後に追加（キャッシュクリア）
- `build_ontology_type_prompt()` / `build_predicate_prompt()` — 現行語彙のプロンプト断片を生成
- `invalidate_cache()` — 承認後にキャッシュを落とす

> **接続先の実態**: 動的語彙の消費者は `core/meta_analyzer.py`（提案生成の入力）・
> `core/simulator.py`（Shadow Testing のプロンプト合成）・`routes/admin.py` の語彙一覧 API の
> 3 箇所だけです。**A層 Agent（`src/episteme_graph/agents/`）はこのレジストリを import しません** —
> A層のドメイン語彙は[カートリッジ](cartridges.md)から来ます。したがって
> 「承認 → 以後の PDF 解析の抽出語彙が変わる」という接続は現状ありません。

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

> **実装上の注意 2 点（2026-09-03 時点）**
> - **Before は `documents.knowledge_graph`（旧 DSL）を読む**。A層パイプラインはこの列を書かないため、
>   「構造再解析」（`POST /api/admin/courses/{id}/structure/reanalyze`）を回していない教材では
>   Before が空になり、差分が意味を持ちません
>   （→ [DSL と理論操作グラフ §4](theory-graph.md#4-理論コンポーネント抽出theory_componentspy)）。
>   After は実際の再抽出ではなく **LLM に変化を推測させるインメモリのシミュレーション**です。
> - **`approve-with-scope` の `scope="canary"` は、対象コース由来のドキュメントだけを再抽出します**
>   （2026-09-03 修正。それ以前は対象件数だけコース由来で数え、実行は全 `status='completed'`
>   ドキュメントに及んでいました）。`_canary_target_document_ids(course_ids)` が
>   `documents × chunks × learning_courses` の JOIN で対象 document.id を解決し、
>   `total_docs`（= 解決件数）と実行対象を**同じクエリから**導きます。解決結果が 0 件のときは
>   全件へフォールバックせず、`processed_docs=0` のまま完了します。
>   `course_ids` はバインドパラメータ（`= ANY(:course_ids)`）で渡します（リクエストボディ由来の
>   値を SQL 文字列へ埋め込まない）。ガードレールは
>   `backend/tests/test_simulator_canary.py`。

---

## 5. 再抽出（`reextractor.py`）

承認後、既存ドキュメントを新スキーマでバックフィルします。

1. **ジョブ登録** — `proposal_id` を受け、`status='completed'` の全 `documents` を数えて `reextraction_jobs` を作成、バックグラウンドスレッド（daemon）起動
2. **バックグラウンド実行** — 各ドキュメントについて:
   - MinIO から PDF 取得 → `services.extract_pdf_text()` でテキスト抽出（取れなければ skip）
   - `services.build_knowledge_graph()` で知識グラフを再構築
   - `services.chunk_text(chunk_size=1000, overlap=100)` で再チャンク
   - **`DELETE FROM chunks WHERE document_id = …`** → `services.embed_chunks()` で pgvector へ再埋め込み
   - `documents.knowledge_graph` を更新
   - 進捗を `reextraction_jobs.processed_docs` に記録
3. **状態取得** — `get_job_status(job_id)`

> **⚠ 破壊的な副作用（2026-09-03 時点の実装）**
> この再抽出は **A層パイプラインではなく旧 `services.*` 経路**を使い、対象ドキュメントの
> **チャンクを全削除してから 1000 字固定のチャンクで作り直します**。A層が作ったチャンクが持つ
> `display_text` / `spoken_text` / `formulas` / `block_ids` / `section_id` / `page_start` /
> `page_end` などは再現されないため、レクチャー原稿・スライド分割・根拠リンク（`block_ids`
> 経由の遡及）が失われます。`theory_claims` / `theory_components` /
> `theory_component_graphs` / `document_analysis_runs` は touch されないので、
> **チャンクだけが A層成果物と食い違う状態**になります。
> 復旧は当該教材の再解析（`POST /api/admin/documents/{id}/reanalyze`）です。
> 承認 API（`approve` / `approve-with-scope`）がこのジョブを**自動で enqueue する**点に注意して
> ください。整理・分離の要否はオーナー判断の未決事項です。

---

## 6. 関連テーブル

| テーブル | migration | 役割 |
|---|---|---|
| `unanswered_query_logs` | `003_unanswered_queries.sql` | スキーマ提案の入力となる未回答質問 |
| `schema_ontology_types` / `schema_predicates` | `004_schema_evolution.sql` | 動的な概念/関係語彙（`is_builtin` で区別） |
| `schema_proposals` / `schema_proposal_items` | `004_schema_evolution.sql` | 提案とその項目（status: pending/approved/rejected） |
| `reextraction_jobs` | `004_schema_evolution.sql` | 再抽出バックグラウンドジョブ |

詳細は [データモデル](../architecture/data-model.md#スキーマ進化)。

---

[← DSL と理論操作グラフ](theory-graph.md) ｜ 次へ: [学習機能 →](../features/learning.md)
