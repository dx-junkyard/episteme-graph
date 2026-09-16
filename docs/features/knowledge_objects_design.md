# 知識オブジェクト層（Knowledge Objects — 論文の構造化成果を一級の行にする）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration **078** `knowledge_objects` /
> **079** `analysis_artifacts` / **080** `document_id_uuid`。実装記録は §12。以後は §12 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md)
> の **Phase 1**（§4 P1-1〜P1-9）。本書はその専用設計書で、§6 のオーナー判断のうち Phase 1 が
> 依存する 2 件を冒頭に固定する。

**オーナー判断（本書の前提）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| O-1 | artifact を「正本」から「生成ログ」へ降格する | **(a) 降格し、知識オブジェクト層を正本にする** | 親文書 §6 の推奨。オーナーの着手指示（2026-09-13「Phase 1 を修正せよ」）を推奨案の採用と解し、本書冒頭に固定する。撤回する場合は本書の状態を「提案」に戻し §12 に記す |
| O-2 | `theory_claims` の意味論（1 行 = 1 span → 親子 2 階層） | **(a) nullable 列の追加で既存行不変とみなす** | `parent_claim_id IS NULL` = 従来行。名前空間を増やさない（C-2 の再演回避） |
| D1 | 版化のために `persistence.py` に手を入れてよいか（六つのレンズ §9） | **認める（2026-09-10 裁定済み）**。触るのは backend 側のみ・agent 非改変。atomic / 合成 claim も `origin` 付きで永続化 | 本書はこの裁定を実装する |

**正本**: 本ドキュメント。**関連**: [candidate_flow](candidate_flow_design.md)（supersede の選別規則
`select_supersedable` を照合キーだけ差し替えて使う）/ [label_vocab](label_vocab_design.md)（語彙表の
正本化の作法）/ [六つのレンズ調査](../architecture/vision_ux_gap_six_lenses_2026-09-10.md)（F2 / K1 / D1）/
[グラフの論文層](graph_paper_layer_design.md)（読み時 join の消費者）/ [W層](element_deliberation_workspace_design.md)
（`element_explanations` / `element_annotations` の element_id 参照）/ [D層](doubt_layer_issues.md)
（`epistemic_ledger.target_id` 参照）。

---

## 1. 目的 — 「読めているのに保存されていない」を解く

親文書 §0 の結論 3〜4・7 が本 Phase の対象である。

- 知識の正本が `document_analysis_runs.stage_outputs._artifacts` の JSONB blob で、`theory_claims` /
  `theory_components` は劣化投影（S-1）。atomic claim・equation・evidence・derivation・symbol には
  DB 行が無い（S-2）。永続グラフの claim 参照 6,379 件中 DB に着地するのは 2 件。
- ID が出現順由来で（S-4）、再解析は `DELETE → 再 INSERT` で UUID が変わる（S-6）。C層の承認・R層の
  産出物が FK CASCADE で消え、台帳・説明・痕跡は宙に浮く（C-7 / S-14）。
- `content_hash` は artifact に既にあるのに保存されない（S-5）。型語彙は DB CHECK で潰れ全行
  `diagnostic_claim` / `theory` になる（F-3 / K-1）。`document_id` は TEXT で FK が無く孤児が滞留する（S-8）。

本 Phase は **A層（`src/episteme_graph/agents/`）を 1 行も変えず**、永続化層と DB だけで
①知識オブジェクトを一級の行にし ②内容由来の版非依存キーで同一性を取り ③再解析を supersede 遷移にし
④参照を再係留し ⑤artifact を生成ログに降格する。Phase 2（学ぶ単位）・Phase 3（概念レジストリ）は
本 Phase の stable_key を前提にする。

---

## 2. 不変条項（KO1〜KO10）

| # | 条項 | 照らす原則 |
|---|---|---|
| KO1 | **A層非改変**。stable_key の計算・行の同期・再係留はすべて `backend/core/knowledge_objects/` と `persistence.py` に置く。agent の出力スキーマ・ID 採番は触らない | W1 / 原則 13 |
| KO2 | **stable_key は内容由来・版非依存・決定論・非LLM**。材料は `document_id` + 正規化テキスト + 出典 block_id 集合（+ 種別固有の少数の構造項）。run_id・出現順・agent ID・confidence を材料にしない。同一 run 内で衝突したら決定論順（agent ID 昇順）で `#2` `#3` を付ける | 原則 2 / 9 |
| KO3 | **再解析は DELETE しない**。stable_key が一致する live 行は **同じ UUID のまま**内容列を更新し、人間の確定列（§5.3）は触らない。一致しない旧 live 行は `superseded_at` / `superseded_by_run_id` を刻んで残す。一致しない新オブジェクトは新行として INSERT する。**他表への FK 経由の CASCADE も「DELETE しない」に含まれる**（`theory_claims.chunk_id` の `ON DELETE CASCADE` は、再解析の `DELETE FROM chunks` で claim の live 行ごと消していた。migration 084 で `ON DELETE SET NULL` に是正・§8.3） | 原則 1 改訂 / 3 / 六つのレンズ F2・D1 |
| KO4 | **全知識オブジェクトが行になる**。claim は親 span / claim object / atomic 子 / 式由来合成の全部を `origin` 付きで、equation / evidence / derivation step / symbol は専用テーブルへ。artifact にしか無い知識を残さない（DB 化率 100%） | 原則 3 |
| KO5 | **読み手は live ビューを読む**（`theory_claims_live` / `theory_components_live`）。基表を直接 SELECT してよいのは書き手（persistence / 削除経路 / 監査・履歴の明示読み）だけで、ガードレールが固定する | 原則 10 / 11 |
| KO6 | **artifact は生成ログ**（1 run × 1 ステージ = 1 行 = `document_analysis_artifacts`。同じ (run, stage) への再書き込みは resume / revision 昇格のために許し、**最後の書き込みが勝つ**。「不変」の意味は「**後段のステージ・フックが他ステージの行を書き換えない**」）。stage_outputs にはもう `_artifacts` を書かない。読み手の契約（`document_run_artifacts()` が `{stage: payload}` を返す）は不変で、persistence の getter が表から組み立てる。知識行は `produced_by_run_id` で自分を出した run を指す | 原則 8 / 14 |
| KO7 | **型語彙の正本は `core/schema.py`**（`CLAIM_TYPES` / `CLAIM_TIERS` / `COMPONENT_TYPES` / `CLAIM_ORIGINS` / `CorePredicate` に `PRODUCES`）。DB は CHECK ではなく語彙表への FK で守り、語彙表の中身は migration が **同じ列挙を `ON CONFLICT DO NOTHING` でシード**する（コード ⇄ SQL の一致はテストで固定）。LLM の自称は `claim_type_text` / `component_type_text` に落とさず保持する | 原則 3 / label_vocab 設計 |
| KO8 | **参照の再係留は決定論・stable_key 一致でのみ**。`element_id_remap` に (old_id → new_id) を残し、`element_explanations` / `epistemic_ledger` / `challenges` / `element_annotations` / `deliberation_sessions` / `element_identity_links` の agent-ID 参照を書き換える。一意制約に当たる行は書き換えず `reanchored` にスキップを記録する（推測で結び直さない）。UUID 参照は KO3 により書き換え不要 | 原則 3 / 14 |
| KO9 | **`document_id` は UUID + `REFERENCES documents(id) ON DELETE CASCADE`**。教材の物理削除経路は `_purge_document` 1 本（`delete_material` は委譲）。到達不能な孤児行の掃除は migration で **1 回だけ**行う（本 Phase 唯一の破壊的ステップ。§8.3 に理由と範囲） | 原則 11 / S-8 |
| KO10 | **数値非表示・監査**。supersede / 再係留 / 語彙外型の丸めは `theory_review_events` に `AUDIT_ENTITY_KNOWLEDGE_OBJECT`（新設）で記帳する（`changed_by` は run 実行者、無ければ NULL）。**migration 内で起きる語彙の丸め（078 の `unknown` / `theory` への自己収束 UPDATE）は記帳対象外**で、`RAISE NOTICE` の件数報告に留める（DDL ランナーはアプリのセッション・実行者を持たないため、取れない帰属を偽装記帳しない。help_kb の content-hash 記帳と同じ判断）。学習者向け DTO に stable_key・produced_by_run_id・superseded_at を出さない（教員 UI には出してよい） | 原則 4 / 14 |

---

## 3. 全体像

```
artifact（生成ログ, 1 run × 1 stage = 1 行）─┐
                                             │ persistence（同一トランザクション）
                    stable_key を計算 ─────────┤
                                             ▼
   live 行（theory_claims / theory_components / knowledge_equations / knowledge_evidence /
            knowledge_derivation_steps / knowledge_symbols）
      ├ 一致 → 同 UUID で内容更新（人間の確定列は不変）
      ├ 不一致（旧）→ superseded_at 刻印（行は残る・FK 子も残る）
      └ 不一致（新）→ INSERT
                                             │
                    element_id_remap ◀────────┤ old agent id → new agent id（stable_key 一致時のみ）
                                             ▼
   参照の再係留: element_explanations.element_id / epistemic_ledger.target_id / challenges.target_id /
                 element_annotations.element_id / deliberation_sessions.element_id /
                 element_identity_links.instance_element_id
```

読み手（W層・D層・R層・グラフレビュー・論文層・学習者向け文脈 API・RAG）は `*_live` ビューと
新テーブルを読む。`document_run_artifacts()` の契約は不変なので artifact 消費者は無改変で動く。

---

## 4. DB 設計（migration 3 本）

番号は実装時に採番した（M1 = **078** / M2 = **079** / M3 = **080**）。以下「M1 / M2 / M3」と呼ぶ。すべて冪等
（`backend/tests/test_migrations_runner.py` の lint に従う: `IF NOT EXISTS` / `DO $$` ガード /
`CREATE OR REPLACE VIEW` / `INSERT ... ON CONFLICT`）。

### 4.1 M1 `knowledge_objects` — 既存 2 表の拡張・新 5 表・語彙表・live ビュー

**`theory_claims` に nullable 追加**（O-2(a)。既存行は全列 NULL / 既定で意味不変）

| 列 | 型 | 意味 |
|---|---|---|
| `stable_key` | TEXT | §5.1 の内容由来キー |
| `agent_claim_id` | TEXT | この行を出した run での agent 側 ID（remap の old 側） |
| `parent_claim_id` | UUID → `theory_claims(id) ON DELETE SET NULL`（DO ガード） | atomic 子の親。NULL = 従来行 |
| `origin` | TEXT | `span` / `claim_object` / `atomic_rewrite` / `equation_synthesis`（語彙は `core/schema.py::CLAIM_ORIGINS`） |
| `claim_tier` | TEXT | `paper_core` / `paper_supporting` / `background` / `prior_work` / `meta`（`CLAIM_TIERS`） |
| `claim_type_text` | TEXT NOT NULL DEFAULT '' | LLM の自称（語彙外でも落とさない） |
| `content_hash` | TEXT | agent 側 `content_hash`（あれば） |
| `produced_by_run_id` | UUID | 出した run |
| `superseded_at` / `superseded_by_run_id` | TIMESTAMPTZ / UUID | KO3 の遷移 |

**`theory_components` に nullable 追加**: `stable_key` / `agent_component_id` / `operation TEXT NOT NULL DEFAULT ''`
/ `teaching_takeaway TEXT NOT NULL DEFAULT ''` / `teaching_granularity JSONB NOT NULL DEFAULT '{}'` /
`prerequisite_concepts JSONB '[]'` / `assumptions JSONB '[]'` / `approximations JSONB '[]'` /
`linked_claim_ids` / `linked_equation_ids` / `linked_evidence_ids` / `linked_derivation_ids`（JSONB '[]'、agent 側 ID）/
`agent_payload JSONB NOT NULL DEFAULT '{}'`（上記以外の agent フィールド全部。GIN index）/
`produced_by_run_id` / `superseded_at` / `superseded_by_run_id`。（P1-9）

**`theory_component_links`**: `produced_by_run_id` を追加。links は派生構造で人間の書き込み経路が
無い（本書起票時点で `UPDATE/INSERT theory_component_links` は persistence 以外に無い）ため、
**document 単位の DELETE → 再作成を本 Phase の明示例外として維持**する（live component 間の辺だけを作る）。

**`theory_component_graphs`**: `produced_by_run_id` を追加（1 document 1 行の upsert は不変）。

**部分一意索引**: `(document_id, stable_key) WHERE superseded_at IS NULL AND stable_key IS NOT NULL`
を `theory_claims` / `theory_components` / 新 4 表に張る（live 行の同一性を DB が守る）。

**live ビュー**（KO5）: `CREATE OR REPLACE VIEW theory_claims_live AS SELECT * FROM theory_claims WHERE superseded_at IS NULL;`
と `theory_components_live`。`SELECT *` は作成時の列で固定されるため、**以後これら 2 表に列を足す
migration は末尾で同じ `CREATE OR REPLACE VIEW` を再実行する**（ガードレールで固定）。

**新テーブル**（document_id は最初から `UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE`）:

| テーブル | 主な列 |
|---|---|
| `knowledge_equations` | `id` / `document_id` / `stable_key` / `agent_equation_id` / `label` / `latex` / `plain_text` / `raw_text` / `block_id` / `section_id` / `page` / `equation_type` / `semantic_status` / `content_hash` / `defined_symbols JSONB` / `used_symbols JSONB` / `input_equation_ids` / `output_equation_ids` / `linked_claim_ids` / `source_evidence_ids`（JSONB・agent ID）/ `review_status` / `needs_math_review BOOL` / `agent_payload JSONB` / `produced_by_run_id` / `superseded_at` / `superseded_by_run_id` / `created_at` / `updated_at` |
| `knowledge_evidence` | `id` / `document_id` / `stable_key` / `agent_evidence_id` / `block_id` / `section_id` / `page` / `span_start` / `span_end` / `evidence_text` / `evidence_role` / `parent_evidence_id`（agent ID）/ `public_export_policy` / `agent_payload` / run・supersede・timestamps |
| `knowledge_derivation_steps` | `id` / `document_id` / `stable_key` / `agent_derivation_id` / `agent_step_id` / `step_index` / `operation` / `operation_subtype` / `chain_type` / `input_equation_ids` / `output_equation_ids` / `input_claim_ids` / `output_claim_ids` / `required_claim_ids` / `assumption_ids` / `source_evidence_ids`（JSONB・agent ID）/ `review_status` / `teaching_takeaway`（chain 由来）/ `agent_payload` / run・supersede・timestamps |
| `knowledge_symbols` | `id` / `document_id` / `stable_key` / `agent_symbol_id` / `canonical_symbol` / `notation_variants JSONB` / `kind` / `unit` / `scope` / `definition_status` / `defining_equation_ids` / `used_in_equation_ids` / `source_evidence_ids` / `definition_evidence_texts`（JSONB）/ `agent_payload` / run・supersede・timestamps |
| `element_id_remap` | `id` / `document_id`（FK CASCADE）/ `run_id` / `object_kind`（`claim` / `component` / `equation` / `evidence` / `derivation_step` / `symbol`）/ `old_id` / `new_id` / `stable_key` / `reanchored JSONB`（表ごとの書換件数とスキップ理由）/ `created_at`。index `(document_id, object_kind, old_id)` |
| `knowledge_claim_types` / `knowledge_component_types` | `value TEXT PRIMARY KEY`。migration が `core/schema.py` と同じ列挙を `ON CONFLICT DO NOTHING` でシード（KO7） |

各知識表には `(document_id)` / `(document_id, agent_*_id)` の index を張る。`confidence` は
`agent_payload` の中にだけ残し、列に昇格させない（原則 4）。

**CHECK → 語彙表 FK**（P1-4）: `theory_claims_claim_type_check` / `theory_components_component_type_check`
を `DROP CONSTRAINT IF EXISTS` し、`claim_type → knowledge_claim_types(value)` /
`component_type → knowledge_component_types(value)` の FK を DO ガードで張る（既存値は旧 CHECK
語彙 ⊂ 新語彙なので違反しない）。

### 4.2 M2 `analysis_artifacts` — 1 ステージ 1 行（P1-8）

```
document_analysis_artifacts(
  run_id      UUID NOT NULL REFERENCES document_analysis_runs(id) ON DELETE CASCADE,
  stage       TEXT NOT NULL,          -- パイプラインの stage 名 or revision の artifact キー
  payload     JSONB NOT NULL,
  created_at / updated_at,
  PRIMARY KEY (run_id, stage)
)
```

同ファイル内で**既存 blob を 1 回だけ移送**する: `stage_outputs->'_artifacts'` を `jsonb_each` で
行に展開して `ON CONFLICT DO NOTHING`、続けて `stage_outputs = stage_outputs - '_artifacts'`。
2 回目以降は対象行がゼロで何もしない（冪等）。GIN は張らない（1 payload が数 MB のため索引コストが
利益を上回る。検索は知識行の側で行う — 親文書 P1-8 の「GIN」はこの理由で見送り）。

### 4.3 M3 `document_id_uuid` — UUID 統一 + FK（P1-7）

対象（本書起票時点で TEXT）: `theory_claims` / `theory_components` / `theory_component_links` /
`theory_component_graphs` / `document_analysis_runs` / `document_embeddings` / `document_figures` /
`epistemic_ledger` / `counterfactual_sessions` / `reconstruction_items` / `section_assembly_status` /
`deliberation_sessions`（nullable）/ `element_annotations`（nullable）/ `element_identity_links.instance_document_id`。

各表について順に ①`document_id = documents.source_path` の行を `documents.id::text` に書き換える
（material_id 形の正規化。開発 DB では 0 行だが本番に存在し得る）②`documents` に対応行が無い行を
DELETE（§8.3）③DO ガードで `ALTER COLUMN document_id TYPE uuid USING NULLIF(document_id, '')::uuid`
（`data_type = 'text'` のときだけ）+ `DROP DEFAULT` ④DO ガードで
`REFERENCES documents(id) ON DELETE CASCADE` を張る。`document_analysis_runs` と
`documents.active_analysis_run_id` の相互 FK は削除時に同一文で解消される（検証は §10）。

---

## 5. stable_key と同期の規則

### 5.1 stable_key（`backend/core/knowledge_objects/stable_key.py`、純関数）

正規化は `episteme_graph.agents.content_normalization` の `normalize_text_for_hash` /
`normalize_equation_for_hash`（stdlib のみ・agent 側 `content_hash` と同じ正規化）を使い、
**新しい正規化を書かない**。`digest(parts) = "k1:" + sha256("\x1f".join(parts)).hexdigest()[:32]`。

| 種別 | parts |
|---|---|
| claim | `["claim", document_id, norm_text(normalized_text or text), ",".join(sorted(block_ids))]`。block_ids = `source_evidence_ids` → evidence の `source.block_id`（主）/ span 行は自身の `block_id` |
| component | `["component", document_id, norm_text(label), operation or primary_operation, ",".join(sorted(block_ids))]`。block_ids = linked claim / evidence の block_id 集合 |
| equation | `["equation", document_id, norm_equation(latex, plain_text or raw_text), block_id or label or ""]` |
| evidence | `["evidence", document_id, block_id, norm_text(evidence_text)]` |
| derivation_step | `["derivation_step", document_id, operation, ",".join(sorted(input_eq_keys)), ",".join(sorted(output_eq_keys)), derivation_id, str(step_index)]`。eq_keys は equation の stable_key（解決不能なら agent ID）。**末尾 2 項は KO2 の明示例外**（下記） |
| symbol | `["symbol", document_id, canonical_symbol, scope, ",".join(sorted(defining_eq_keys))]` |

derivation_step の `derivation_id` / `step_index` は **KO2「出現順・agent ID を材料にしない」の
明示例外**（2026-09-13 の実データ検証 V-5）。実論文では 1 チェーンに同じ operation の step が
何十個も並び、式参照が解決できないと素キーが数種類に潰れて大半が `#n` になる。`#n` は
**文書全体の項目順**で振られるため、別チェーンの step が 1 つ増減しただけで付け替わり、内容が
変わっていない step まで supersede が連鎖した。チェーン ID と序数を材料に含めれば、他チェーンの
変化はこのチェーンのキーに波及しない（同一チェーン内での挿入で以降がずれる限界は `#n` と同じ）。

derivation_step の **agent ID は `{derivation_id}:{step_id}`**（正本は
`stable_key.derivation_step_agent_id`）。agent 側の `step_001` はチェーン内でしか一意でなく、
`dedupe_stable_keys` は `{agent_id: key}` で引くため、別チェーンの同名 step が 1 件に潰れて
部分一意索引違反になっていた（V-2）。書き手（`persistence._derivation_items`）と取り込み
（`core/knowledge_import/rows.py`）が同じ関数を使う。

同一 run 内の衝突は `dedupe_stable_keys(items, key=agent_id)` が agent ID 昇順で `#2` … を付ける
（`sync_live_rows` の冒頭でも同じ処理を通す = 最後の砦。P1-R9）。
`content_hash`（agent 側）は別列に保存するだけで、同一性判定には使わない（synth claim で空・
equation で衝突があるため — S-5）。

### 5.2 同期（`backend/core/knowledge_objects/sync.py`）

`sync_live_rows(session, *, table, document_id, run_id, incoming, content_columns, preserved_columns, agent_id_column)`:

1. live 行（`superseded_at IS NULL`）を `id / stable_key / agent_id / preserved_columns` で読む。
2. incoming の stable_key が live に在れば **UPDATE**（`content_columns` + `produced_by_run_id` +
   `agent_id_column` + `updated_at`。`preserved_columns` は触らない）。agent ID が変わっていれば remap 候補。
3. 無ければ **INSERT**。
4. incoming に無い live 行は `superseded_at = now(), superseded_by_run_id = run_id`。
5. 戻り値 `{id_map: {agent_id: uuid}, remaps: [(old, new, stable_key)], stats: {updated, inserted, superseded}}`。

`select_supersedable`（candidate_flow）の思想を継承する: **人間が確定した行を AI が消したり復活
させたりしない**。ただし本層の「候補 / 確定」は行ではなく列（§5.3）にあるため、行の入れ替え
ではなく列の保護で実現する。

### 5.3 人間の確定列（match 時に不変）

| 表 | 保護する列 | 判定 |
|---|---|---|
| `theory_claims` | `review_status`（既定 `teacher_review_required` 以外のとき）/ `created_by` / **人間が触った行では `text` / `normalized_text` も保護** | 常に保護（AI は review_status を下げない）。「触った」= `review_status <> 'teacher_review_required'` or `created_by` 非 NULL |
| `theory_components` | `review_status` / `status` / `teacher_notes` / `created_by` / `maturity_source`（`teacher_reviewed` のとき）/ **人間が触った行では `name` / `summary` も保護** | 「触った」= `status <> 'candidate'` or `review_status <> 'teacher_review_required'` or `teacher_notes <> ''` |
| `knowledge_equations` / `knowledge_derivation_steps` | `review_status`（既定以外） | 同上 |
| `knowledge_evidence` / `knowledge_symbols` | **無し**（逐語の写し・記号の索引で、人間が確定する列を持たない。078 にも `review_status` 列は無い） | — |

### 5.4 claim の 4 origin と親子

1. claim object（`claim_object_builder.claims`）を先に処理する。`parent_claim_id` が付く記録は
   `atomic_rewrite`、`synthesis_method` 非空または `synth_claim_` 始まりは `equation_synthesis`、
   それ以外は `claim_object`。親を先に INSERT/UPDATE してから子の `parent_claim_id` を解決する（2 パス）。
2. `claim_qualification.qualified_spans`（decision ≠ rejected）のうち、stable_key が 1. と重ならない
   span だけを `origin='span'` で残す（同じ命題を二重に持たない）。
3. `claim_type` は `CLAIM_TYPES` にあればそのまま、無ければ `unknown`（`claim_type_text` に自称を残す）。
   `claim_tier` は span の `qualification.tier`。`equation` JSONB には `equation_ids`（agent）と
   `equation_stable_keys` を併記する。
4. `claim_id_map`（agent claim ID → UUID）は **全 claim** を覆う。graph・component の claim 参照は
   これで UUID に置き換わる（graph review の artifact フォールバックは旧 run にだけ残る）。

### 5.5 再係留（`backend/core/knowledge_objects/remap.py`）

remap 候補 `(kind, old_id, new_id, stable_key)` を `element_id_remap` に INSERT し、同じトランザクションで:

| 表 | 書き換え列 | 条件 |
|---|---|---|
| `element_explanations` | `element_id`（`element_type ∈ theory_claim / theory_component / equation`） | `document_id` 一致 |
| `epistemic_ledger` | `target_id`（`target_type ∈ claim / component / equation`） | `UNIQUE(target_id, target_type)` に当たる場合は書き換えず `reanchored.skipped` に記録 |
| `challenges` | `target_id` | — |
| `element_annotations` / `deliberation_sessions` | `element_id` | `document_id` 一致 |
| `element_identity_links` | `instance_element_id` | 一意制約に当たる場合はスキップ記録 |

UUID を直接持つ参照（`component_explanations.component_id` / `reconstruction_items.claim_id` /
`epistemic_ledger.target_id` が UUID の行）は KO3 で UUID が保たれるため書き換え不要。
再解析後に live 行と一致しない参照は**消さず**、`element_id_remap` に old_id だけの行も残さない
（存在しない対応を捏造しない）。宙に浮いた参照の可視化は読み時 join の欠落として現れる（P4-3）。

### 5.6 stable_key の既存行バックフィル

`core/knowledge_objects/backfill.py::backfill_stable_keys(session)` を `main.py` lifespan から
fail-open で呼ぶ（`stable_key IS NULL` の live 行だけ・冪等）。claim は `normalized_text or text` +
`source_scope.block_id`、component は `name` + `''` + `evidence_claims` から引いた block_id 集合。
agent 側と同じ材料が揃わない旧行では近似キーになる（label / 本文が変わらなければ次の再解析で一致する）。
`agent_component_id` は `source_scope.legacy_ids[0]` から補う。

バックフィルは migration と同じく **`pg_advisory_lock`（専用キー `BACKFILL_LOCK_KEY`）配下**で
走らせる。複数レプリカが同時起動すると、同じ NULL 行に同じ `#n` を割り当てて部分一意索引
`uq_*_stable_key_live` で片方が落ちるため。

**第2段突合（近似キーの取りこぼし）**: 近似キーは agent 側の計算結果と一致しないことがあり、
そのままだと「同じ主張が supersede + 新規 INSERT に割れる」（教員の `review_status` が
superseded 側に取り残される）。そこで `sync_live_rows(..., fallback_match_column=...)` が、
stable_key で結べなかった組だけを **claim は `normalized_text`・component は `name` の完全一致**で
結び直し、UUID と人間の確定列を引き継いで stable_key を新しい値へ更新する。曖昧なとき
（同じ値の live 行が 2 件以上、または同じ値の incoming が 2 件以上）は結ばない — 推測で寄せず、
従来どおり supersede + INSERT にする。結んだ事実は `element_id_remap` に
`old_id` / `new_id` = 旧/新 **stable_key**、`reanchored = {"remap_kind": "stable_key"}` で記録する
（参照が持っているのは agent ID なので、この行では再係留を行わない）。

---

## 6. artifact の生成ログ化（P1-8）

- `upsert_analysis_run(..., stage_outputs={"_artifacts": {...}})` と `update_revision_status` は
  `_artifacts` を **stage_outputs から取り出し**、`document_analysis_artifacts` に stage ごと upsert する。
  他のキーは従来どおり `stage_outputs` に浅マージする。
- orchestrator の `save_artifact(stage, value)` は **その 1 stage だけ**を渡す（従来は in-memory の
  全 artifact を毎回書き戻していた = S-9 の単調増加の直接原因）。
- `accept_revision` の promoted artifacts も同表へ upsert する（`jsonb_set` の deep merge は撤去）。
- `resolve_artifact_runs` / `get_latest_analysis_run` / `get_analysis_run` / `get_active_analysis_run`
  は run 行を返す前に `stage_outputs["_artifacts"]` を表から組み立てる（旧 blob が残る行は blob を
  下敷きにして表が勝つ）。`document_run_artifacts()` の戻り値の形は不変。
- 知識行の `produced_by_run_id` により「この component はどの run が出したか」に答えられる（S-10）。

---

## 7. 型語彙（P1-4）

`core/schema.py` に追加:

- `CLAIM_TYPES`: 旧 CHECK 17 語彙 ∪ `claim_object_builder.schema.CLAIM_TYPE_ONTOLOGY`（30）∪ `unknown`。
- `CLAIM_TIERS = ("paper_core", "paper_supporting", "background", "prior_work", "meta")`。
- `COMPONENT_TYPES`: 旧 CHECK 9 語彙 ∪ cartridge `component_types.json` の 14 型 ∪ `unknown`。
- `CLAIM_ORIGINS = ("span", "claim_object", "atomic_rewrite", "equation_synthesis")`。
- `KNOWLEDGE_OBJECT_KINDS = ("claim", "component", "equation", "evidence", "derivation_step", "symbol")`。
- `CorePredicate.PRODUCES`（dsl_linking の `CORE_PREDICATES` 10 語彙と一致。src 側は非改変で、
  「src の語彙 ⊆ core の語彙」をテストで固定）。
- `AUDIT_ENTITY_KNOWLEDGE_OBJECT = "knowledge_object"` を `AUDIT_ENTITY_TYPES` に追加。

persistence は `component_type` を「語彙にあればその値、無ければ従来どおり `theory`」で書く
（`component_type_text` に自称）。`claim_type` は「語彙にあればその値、無ければ `unknown`」。
既存の読み手で `component_type = 'theory'` を前提にしているものは無い（apparatus 系 3 語彙の
フィルタのみ）。

---

## 8. 削除経路と孤児（P1-7）

### 8.1 `delete_material` → `_purge_document`

HTTP 層（所有者確認・確認名照合・監査 `AUDIT_ENTITY_MATERIAL`・V層 teardown・教材図 MinIO の
best-effort 削除）は `routes/admin.py` に残し、DB の削除本体は `core/versioning/deletion.py::_purge_document`
に委譲する。`_purge_document` は `document_figures.minio_key` を削除前に集めて返し、呼び出し側が
MinIO を best-effort で消す（現状はどの経路も図画像を消していない）。

### 8.2 FK 化後の SQL

`document_id IN (:a, :b)`（UUID / material_id 両形）と `d.id::text = t.document_id` 型の join・
`CAST(:x AS uuid)::text` 比較は全て UUID 同士の比較に直す。material_id を受け取る API は
`_resolve_document(ref)` で UUID に解決してから知識表を引く（既に大半がそうなっている）。

### 8.3 破壊的ステップの一覧

本 Phase で**行や値を失わせ得る**処理はこの 3 つだけで、他はすべて状態遷移（supersede）である。

| # | 何が消えるか | 扱い |
|---|---|---|
| ① M3（080）の孤児掃除 | `documents` に対応行の無い行 | 下記のとおり実施（到達不能・export 対象外） |
| ② M2（079）の `_artifacts` 剥がし | `stage_outputs._artifacts` の blob | **表へ移せた object 形だけ**剥がす。object でない壊れた値は run 行に残す（移送先が無いのに消さない）。 |
| ③ 再解析の `DELETE FROM chunks` → **FK CASCADE で `theory_claims` の live 行**（〜2026-09-13） | 教員がレビュー済みの claim 行ごと | **是正済み**。migration 084 が `theory_claims.chunk_id` を `ON DELETE SET NULL` に張り替え、`persist_source_chunks` は `chunk_index` キーの upsert（余剰行だけ DELETE）にした。claim は残り、失われた参照は `chunk_id = NULL` として正直に現れる。 |

①の詳細: M3 は `documents` に対応行の無い行を各表から DELETE する。これらは①全読み取り経路が
`documents` 行の存在を前提にする権限ゲート（`_ensure_document_viewable` 等）の内側にあり到達
不能 ②export にも載らない ③開発 DB 実測で `document_analysis_runs` の 17MB / 18MB を占める
（S-8）。掃除件数は migration が `RAISE NOTICE` で出す。material_id 形で書かれた行は削除ではなく
UUID へ正規化する（§4.3 ①）。

---

## 9. 読み手の変更（KO5）

`FROM|JOIN theory_claims` / `theory_components` を持つ読み手（本書起票時点 36 ファイル）は
`_live` ビューへ切り替える。例外（基表を読んでよい）: `persistence.py` / `versioning/deletion.py` /
`knowledge_objects/*` / 監査・履歴目的で superseded を明示的に見せる読み手（`superseded_at` を DTO
に載せることが条件）。ガードレール `test_knowledge_objects_guardrails.py` が SQL 文字列を走査して
固定する。

教員 UI（グラフレビュー・W層）で superseded 行を見せる導線は本 Phase の非スコープ（§11）。

---

## 10. 検証

- 単体: `test_knowledge_objects_{stable_key,sync,remap,vocab}.py`（純関数・fake session）。
- 永続化: `test_persist_claims_legacy_ids.py` / `test_revision_projection_rebuild.py` /
  `test_revision_decisions.py` を「DELETE を発行しない・stable_key 一致で UUID 維持・不一致で
  superseded」の契約に書き換える。
- migration: `test_migrations_runner.py` の冪等 lint + 開発 DB の**複製**（`CREATE DATABASE ... TEMPLATE`）
  に対して 3 本を 2 回適用し、2 回目が無変更で通ること・FK 循環（documents ⇄ runs）で削除が通る
  ことを確認する（開発 DB 本体には適用しない）。
- ガードレール: KO1（`src/` 非改変 — git diff）/ KO5（基表 SELECT の allowlist）/ KO7（migration
  シード = `core/schema.py`）/ live ビュー再作成の規律（M1 以降で 2 表に ADD COLUMN する migration は
  `CREATE OR REPLACE VIEW` を含む）/ 数値非漏洩（学習者向け DTO に `stable_key` / `produced_by_run_id` /
  `superseded_at` が出ない）。

---

## 11. 非スコープ（v1）

- 学ぶ単位（`learning_unit`）・概念レジストリ・import・JSON-LD（Phase 2〜4）。
- superseded 行を教員 UI で並べて見せる「版の履歴」画面（読み手は live のみ。API に
  `include_superseded` を足すのは実測後）。
- `chunks.formulas` の ID 参照化（P1-3 後段）。`knowledge_equations` が正本になった後、freeze /
  lecture の読み手を移してから複製を落とす（本 Phase では表を作るだけ）。
- W層 meaning commit の旧本文退避（六つのレンズ 項目 12 の一部。W層設計書で扱う）。
- 学習者向け表示の変更（一切なし）。

---

## 12. 実装記録

### 12.1 2026-09-13 — Phase 1 v1（Fable 5.1 指揮・Opus 5 の 4 担当）

**採番**: M1 = `backend/db/078_knowledge_objects.sql` / M2 = `079_analysis_artifacts.sql` / M3 = `080_document_id_uuid.sql`。

**担当分割**（ファイル担当を重ねない並列 3 体 → 直列 1 体）:

| 担当 | 成果 |
|---|---|
| 指揮者（先置き） | `core/schema.py` の語彙定数（CLAIM_TYPES 38 / COMPONENT_TYPES 21 / CLAIM_TIERS / CLAIM_ORIGINS / KNOWLEDGE_OBJECT_KINDS / `CorePredicate.PRODUCES` / `AUDIT_ENTITY_KNOWLEDGE_OBJECT`）、`core/knowledge_objects/{__init__,schema,stable_key}.py`、`schema_registry.py` の PRODUCES 説明、本書、docs 索引・CLAUDE.md・親文書の解消注記 |
| A スキーマ | 078 / 079、`core/knowledge_objects/backfill.py` + lifespan 呼び出し、013 / 041 の CHECK 再作成ガード（FK が在れば作らない）、`test_knowledge_objects_{vocab,stable_key,backfill}.py` |
| B 永続化 | `core/knowledge_objects/{sync,remap}.py`、`persistence.py`（DELETE 撤去・全 claim object の永続化・`persist_knowledge_objects`・artifact 表の読み書き・`_rebuild_*_in_session` の同期化・監査）、`orchestrator.py`（`save_artifact` を1ステージだけ・persist ステージの結線）、`tests/knowledge_object_fakes.py`、`test_knowledge_objects_{sync,remap,persist}.py`、既存 8 テストの契約更新 |
| C 読み手 | 34 ファイル・97 箇所を `theory_claims_live` / `theory_components_live` へ（基表のまま残した読み手ゼロ）、`test_knowledge_objects_guardrails.py`（KO1 / KO5 / KO10 / 純粋性 / ビュー定義） |
| D document_id | 080（14 組の (表, 列) を DO ループで TEXT → UUID + FK CASCADE。view の DROP → 型変更 → 再作成）、Python 側 SQL の UUID 同士比較化（両形 `IN (:a,:b)` 撲滅・`SELECT document_id::text`・`CAST(NULLIF(:x,'') AS uuid)`）、`delete_material` → `_purge_document` 委譲（`PurgedDocument(course_ids, teaching_figure_keys, figure_image_keys)`・図画像 MinIO の best-effort 削除）、`test_knowledge_objects_document_id.py`、既存 11 テストの追随 |

**設計からの逸脱・解釈**（本文は不変・ここに記す）:

- §4.2「GIN」は張らない（1 payload が数 MB で索引コストが利益を上回る。検索は知識行側で行う）。
- §5.4-2 の「重ならない span だけ残す」は claim object との衝突にだけ適用し、span × span の同キーは `dedupe_stable_keys` の `#2` で区別する（同キー 2 span を 1 行に潰すと片方の痕跡が消える）。
- 078 は FK 化の前に、旧 CHECK が外れた状態で書かれた語彙外値を `unknown` / `theory` に丸める自己収束 UPDATE を DO ガード内に持つ（自称は `*_type_text` へ退避。開発 DB では 0 件）。013 / 041 は「FK 制約が在れば CHECK を作り直さない」条件を足した（毎起動再実行で CHECK ⇄ FK の往復を防ぐ）。
- 080 は `format('%I')` ではなく `quote_ident() || ...` で組む。ランナーが `exec_driver_sql` に空パラメータを渡すため psycopg2 が `%` を補間しようとする（**SQL コメント内の `%` も同様に落ちる** — 実機検証で判明し `%%` に修正。`RAISE NOTICE` も `%%`）。
- `challenges` は `document_id` 列を持たないため、同じ対象の `epistemic_ledger` 行が当該論文に在るときだけ再係留する（絞れない疑義は書き換えない）。
- `human_touched` は `status <> 'candidate'` / `review_status <> 'teacher_review_required'` / `teacher_notes <> ''` / `maturity_source = 'teacher_reviewed'` のいずれかで、その行では `name` / `summary` / `maturity_source` も保護する。
- `_record_knowledge_audit` は例外を握らない（KO10。PostgreSQL では失敗した文の後の commit がどのみち通らないため、黙って続ける方が事故になる）。
- psycopg2 は uuid 列を `uuid.UUID` で返すため、`SELECT document_id::text`（38 箇所）で str を維持した。material_id 形と UUID 形が混在し得る参照（`paper_discovery/{vocab,radar}` / `doubt/observation_targets` / `status/projector`）は `::text` 比較のまま。

**検証**:

- テスト: backend **14,637 passed / 27 skipped**（ベースライン 14,500）、src **1,924 passed**（不変）。
- 開発 DB の複製（空 DB に全 migration → 開発 DB 主要 13 表を読み取りコピー → 全 migration を 2 回）: 2 回目が無変更で通る / `stage_outputs ? '_artifacts'` 0 行・`document_analysis_artifacts` 78 行（移送元の 17 run × stage 数と一致）/ 対象 14 表の `document_id` が全て uuid・`documents` への FK 22 本 / 孤児 0（components 94・ledger 323・figures 560・runs 19 を掃除）/ live ビュー 2 つ・語彙表 38 / 21 / backfill `{claims: 28, components: 37}` → 2 回目 0 / `DELETE FROM documents` 1 文で runs 2 → 0（CASCADE）/ `sync_live_rows` の実 PG 往復: 同キー = 同 UUID・`review_status='teacher_approved'` が保たれたまま本文更新・不一致 1 行が superseded・remap 1 組記録。開発 DB 本体には当てていない。

**残課題**:

- 本番 DB に material_id 形の `document_id` があると 080 の正規化①で `UNIQUE(document_id, ...)` 衝突が起き得る（`theory_component_graphs` / `document_figures` / `document_embeddings` 等。開発 DB では 0 行）。適用前に本番で `document_id !~ '^[0-9a-f]{8}-'` の件数を確認する。
- material_id を document_id 引数に渡している呼び出しが残っていれば、従来の「0 件で静かに返る」から uuid 型エラーに変わる。`_resolve_document` / `resolve_document_access` を通す（教材管理・D層・W層の実機スモークは docker 復帰後）。
- backfill の近似キー: 開発 DB で component 131 件中 11 件が `#n` 付き（同名で block が解けない component）。次の再解析で agent 側の材料が入ると別キーになり旧行は superseded になる（§5.6 の想定どおり）。
- 非スコープ（§11）は不変。superseded 行の教員向け履歴 UI・`chunks.formulas` の ID 参照化・W層 meaning commit の旧本文退避は別件。


### 12.2 2026-09-13 — 敵対的レビュー + scratch DB 実データ検証の是正（migration **084**）

同日の敵対的レビューと、実論文 2 本を scratch DB に通した検証で見つかった欠陥の修正。
**本文（§2 / §5 / §8）を書き換えた箇所はその節に直接反映済み**で、ここには経緯と判断を残す。

**実データ検証（V-x）— HEAD の永続化経路が実論文で必ず落ちていた**

| # | 症状 | 是正 |
|---|---|---|
| V-1 | `_KNOWLEDGE_PRESERVED_COLUMNS = ("review_status",)` を新 4 表すべてに渡していたが、078 が `review_status` を作るのは equation / derivation_step の 2 表だけ。evidence の同期が `UndefinedColumn` → `PipelineStageError` → **run 全体が failed**（equations もロールバック） | preserved 列を**表ごとの dict** にし、evidence / symbol は `()`。両表の `values` から死んだ `review_status` も落とした（§5.3 の表を分割） |
| V-2 | derivation step の agent ID（`step_001`）はチェーン内でしか一意でなく、`dedupe_stable_keys` が `{agent_id: key}` で引くため別チェーンの同名 step が 1 件に潰れ、`uq_knowledge_derivation_steps_stable_key_live` 違反（論文 A/B とも再現） | agent ID を `{derivation_id}:{step_id}` に。規則の正本は `stable_key.derivation_step_agent_id`（取り込み側 `knowledge_import/rows.py` が同じ関数を import できるよう共通箇所に置いた） |
| V-3 | `qualification.get("tier")` を読んでいたが実 artifact のキーは `claim_tier`。**239 claim 全件が `claim_tier=''`** | `_claim_tier_from_qualification()` が `claim_tier` → `tier` の順に見る（旧 fixture 互換） |
| V-4 | `equation_claim_synthesis` の 4 型（`definition_claim` / `dependency_claim` / `equation_system_claim` / `result_claim`）が `CLAIM_TYPES` に無く、**式由来 claim 84 件が全件 `unknown`** | `core/schema.py::CLAIM_TYPES` と 078 の seed に 4 語を追加（additive）。`test_knowledge_objects_vocab.py` に subset 検査を追加 |
| V-5 | derivation step の stable_key 材料が `(document_id, operation, 入出力式キー)` だけで、論文 B は 72 step が素キー 9 種・**65 行が `#n`**。`#n` は文書全体の項目順で振られるため、他チェーンの step 増減で付け替わり内容不変の step まで supersede が連鎖 | 材料に `derivation_id` と `step_index` を追加（**KO2 の明示例外**。§5.1 に理由と限界を明記） |

**敵対的レビュー（P1-Rx）**

| # | 発見 | 是正 |
|---|---|---|
| P1-R1 | 再解析の `DELETE FROM chunks` が `theory_claims.chunk_id` の FK CASCADE で **claim の live 行を物理削除**していた（KO3 の穴。教員の `review_status` ごと消える） | migration **084**: FK を `ON DELETE SET NULL` へ（pg_constraint から動的に名前を引き、CASCADE のときだけ張り替え）+ `chunks(document_id, chunk_index)` の一意索引（重複が在れば作らず NOTICE）。`persist_source_chunks` を **chunk_index キーの upsert** にし、余剰行だけを `id <> ALL(...)` で削除。chunk UUID が保たれるので `interest_traces` のチャンクアンカーも切れない |
| P1-R2 | live ビュー読み漏れ 3 箇所（`deliberation/refs.py::_LEGACY_ID_TABLES` / `descent/resolve.py` / `persistence.py::load_revision_projection_overlay`） | 3 箇所とも live ビューへ。前 2 者はテーブル名を f-string で受けるため regex が素通りしていた |
| P1-R11 | ガードレールが**行単位の regex** で、動的なテーブル名補間（`FROM {table}`）を見逃していた。allowlist もファイル粒度で、`persistence.py` に後から足した読み手が素通りする | 全文走査 + **動的補間の検出**（`{X}` を `knowledge_objects.schema` の定数として解決し、解決できないものは明示 allowlist を要求）+ allowlist を **`ファイル:シンボル` 粒度**へ。検出器自身の退行検査（合成ソース 4 本）も追加 |
| P1-R3 | バックフィルの**近似キー**が agent 側の計算結果と食い違うと「同じ主張が supersede + 新規 INSERT」に割れ、教員の確定が superseded 側に取り残される | `sync_live_rows(..., fallback_match_column=)` の**第2段突合**（claim=`normalized_text` / component=`name` の完全一致・1 対 1 に決まるときだけ）。引き継ぎは `element_id_remap` に `remap_kind="stable_key"` で記録（§5.6） |
| P1-R4 | claim の保護列が `review_status` / `created_by` だけで、**教員がレビュー済みの claim 本文を再解析が上書き**できた（component は保護済み） | `_claim_human_touched` + `protected_when_touched=("text", "normalized_text")`（component と同型。§5.3） |
| P1-R5 | `load_run_artifacts` の except が `rollback()` せず、同じセッションの後続 SELECT が "current transaction is aborted" で全滅し得た | except 内で `session.rollback()`（それ自体も握って fail-open） |
| P1-R6 | 079 の `_artifacts` 剥がしが、表へ移せなかった**非 object の blob も消していた** | UPDATE に `jsonb_typeof(...) = 'object'` を追加（§8.3 の破壊ステップ表②） |
| P1-R7 | 起動時バックフィルが advisory lock の外にあり、複数レプリカ同時起動で同じ `#n` を取り合って部分一意索引に当たり得た | migration とは**別キー**の `pg_advisory_lock(BACKFILL_LOCK_KEY)` 配下へ（unlock は finally） |
| P1-R9 | `sync_live_rows` が incoming の stable_key 重複を前提にせず、`learning_units` 経路は dedupe を通っていなかった | `sync_live_rows` の冒頭で `dedupe_stable_keys` を通す（**最後の砦**。呼び出し側の dedupe は残す） |
| Phase 3 A層⚠ | `_hook_claim_concept_grounding` が接地結果で `claim_object_builder` artifact を**上書き**していた（KO6 の「生成ログ」を後段が書き換える） | 上書きを撤去。接地結果は専用 artifact `claim_concept_grounding` にだけ残し、知識行への反映は persist 側の join（CG §6 = 既存経路）。フックは resume でも毎回走るので、後段ステージが見る in-memory の値は新規実行と resume で一致する |
| P2-R7 | `persist_learning_units` の失敗が run 全体を failed にしていた（claims / components は commit 済みなのに「解析失敗」に見え、教員が再解析を回す） | 派生表の同期を try/except で包み、`stage_outputs` の `knowledge_objects.learning_units` に `{"failed": true, "error": ...}` を正直に残して completed を維持 |

**新規テスト**: `test_knowledge_objects_chunk_upsert.py`（chunk upsert 5 本 + 084 の内容検査 2 本）、
`test_knowledge_objects_sync.py` に dedupe 3 本・第2段突合 6 本、`test_knowledge_objects_persist.py` に
claim 本文保護 2 本・第2段突合 1 本・V-1 / V-3 / V-2 の回帰 3 本、`test_knowledge_objects_guardrails.py` に
検出器の退行検査 4 本、`test_knowledge_objects_vocab.py` に V-4 の subset 検査 1 本。

**後続課題（本 Phase では直さない）**

- P1-R8: `persist_qualified_claims` → `persist_components` → `persist_knowledge_objects` /
  `persist_learning_units` が**別トランザクション**で、claim だけ commit された状態で component が
  落ちると中途半端な世代が残る。`sync_live_rows` の live 行 SELECT も種別ごとに 1 往復（N+1 ではないが
  トランザクション境界は 3〜4 本）。1 トランザクションへの統合は persist ステージ全体の再設計になるため別 issue。
- バックフィルの行更新は 1 行 1 UPDATE のまま（`UPDATE ... FROM (VALUES ...)` の一括化は未実施。
  起動時 1 回・上限 20,000 行なので実測を待つ）。
- `theory_claims.chunk_id` が NULL になった claim を教員 UI でどう見せるかは未定（現状は従来どおり
  chunk 参照の無い claim として振る舞う）。

### 12.3 scratch DB 再検証（2026-09-13・是正後）

- 実論文 A/B の artifact を迂回パッチ無しで永続化し、行数は A: claims 132 / equations 53 / evidence 101 / derivation_steps 23 /
  symbols 238、B: 107 / 64 / 207 / 72 / 119 で親文書 §7 と一致。derivation step の `#n` サフィックスは 65 → 0。
  `persist_source_chunks` 込みの再解析で claims 132/132 同 UUID・承認 5/5 保持・教員編集本文 2/2 保持・chunks 19/19 同 UUID
  （upsert）。chunk を実 DELETE しても claim は消えず `chunk_id` が NULL 化し、次回同期で再係留される（084 の主張どおり）。
  第 2 段突合（`fallback_match_column`）は evidence block 変更で stable_key が変わっても UUID と承認を引き継ぎ、
  `element_id_remap` に `reanchored.remap_kind="stable_key"` で記録される。
- **W-1（是正済み）**: 084 初版の `format('... %I', fk_name)` は `%` が 1 個で、ランナー（`exec_driver_sql`）経由では全環境で
  `TypeError: immutabledict is not a sequence` → 起動失敗。`%%I` に修正し、`test_migrations_runner.py::TestPercentEscapeLint`
  が「コメント外に奇数個の `%` 連が無い」ことを全 migration で固定した。
- **W-3（仕様として明記）**: `claim_tier` が空なのは `origin='equation_synthesis'` の claim のみ（A 49/49・B 35/35）。式由来合成
  claim は qualification span を持たず tier の供給源が無いため**空は正常**。継承が必要なら親 equation 側の claim から（後続）。
- **W-5（仕様として明記）**: 内容を変えて supersede された行は、本文を元に戻しても復活しない（新 live 行が増える。**revert は
  resurrect ではない**）。KO3 とは整合するが往復で行が単調増加する。un-supersede の分岐は後続判断。
- **W-4（対応不要）**: 残る `claim_type='unknown'`（A 13 / B 18）は artifact 側の値で、永続化の取りこぼしではない。

