# 調査B: 構造化成果の格納構造・ID 体系・版管理のレビュー

> **状態: 調査記録（完了）**（2026-09-12、Opus 5 サブタスク。本調査以降の変更は未評価。親文書は
> [知識構造の見直し提案](../knowledge_structure_review_2026-09-12.md)）


調査日 2026-09-12 / 対象 `ura-dev` @ `c71fc32` / DB は localhost:5432 の生 DB（読み取りのみ）
凡例: **[確認]** = コード・SQL・実データを直接読んだ事実 / **[推測]** = そこからの推論。

---

## ① 要約（10行）

1. **正本は artifact（`stage_outputs._artifacts` の JSONB blob）で、正規化テーブルは劣化投影**。export API 自身が `export_source_policy: "artifact_first"` を名乗り DB を fallback 扱いにしている（`export.py:3236`）。
2. **永続化された理論操作グラフの参照は 810 ノード中 808、claim 参照 6,379 件中 6,377 が agent 側 ID** で `theory_claims` に対応行が無い（全13グラフ実測）。グラフ行は関係表ではなく artifact への文字列ポインタ。
3. **equations / evidence / derivation / symbol には DB テーブルが存在しない**（92テーブル中いずれも無し）。論文1本あたり知識オブジェクトの 90%超が artifact のみ。
4. **`legacy_ids` は claim で機能していない**。persist は span_id しか入れず、span_id は文書内で一意でないため 9 行すべてが同一キー `['claim_span_001','span_001']`。
5. **agent ID は位置依存（`comp_001` / `claim_span_001_9_sub02`）で run 間で不安定**。凍結コースが参照する `comp_002__r2` は現 DB に存在しない（実測の dangling）。
6. **再解析は `DELETE FROM theory_claims/theory_components WHERE document_id` → 新 UUID 再 INSERT**（`persistence.py:567,700,704`）。教員の `review_status` / `teacher_notes` と C層の承認・引用が CASCADE で消える。是正 F2/K1 は判断済み・**未実装**。
7. **`document_id` が TEXT で FK 無し**のため孤児が大量に滞留（`theory_components` 71.8%、`document_figures` 82.8%、`document_analysis_runs` 48.7%）。
8. **`stage_outputs` 1行に全成果が乗る**設計で最大 10.46 MB。revision のたびに deep merge で単調増加し、部分更新・差分・per-object 監査ができない。
9. **同一 52 数式が chunks に 19 本、凍結コースに 26 本、さらに latex/raw_text/plain_text で 3 重**に複製。distinct 62 KB が約 3.3 MB に膨らむ。
10. **`theory_components` は artifact の 60 フィールド中 46 を捨てる**。`teaching_takeaway` / `teaching_granularity` / `prerequisite_concepts` / 各種 `linked_*_ids` など「学ぶ単位」の属性が関係層に無い。

---

## ② 格納の地図

### 表 2-1 知識の種類 × 所在 × 正本/投影 × 件数実測

件数は arXiv-2407.01221 / fujimoto_d の 2 文書。

| 知識の種類 | (a) `_artifacts` JSONB | (b) 正規化テーブル | (c) `learning_courses.data` 凍結 | (d) `element_explanations` | (e) `chunks` | 正本 |
|---|---|---|---|---|---|---|
| 本文チャンク | `source_chunking` 19 / 196 | — | `material_chunk_ids` 参照のみ | — | **19 / 196 行** | (e) |
| 文書構造 blocks | `document_structure` 113 blocks / 9 sections | — | — | — | `block_ids`/`section_id` に断片 | (a) のみ |
| span 判定 | `claim_qualification` 9+19+37 / 17+17+30 | `theory_claims` **9 / 17**（qualified のみ） | — | — | — | (a)。(b) は 14%/25% の抜粋 |
| atomic 子 claim | `atomic_claims` 154 / 82 | **0 行** | ID 文字列のみ | `element_id` に ID 文字列 | — | (a) のみ |
| claim object | `claim_object_builder.claims` **132 / 107** | **0 行**（span 由来 9/17 を除く） | ID 参照 | ID 参照 | — | (a) のみ |
| 合成 claim `synth_claim_*` | 49 / 35 | **0 行** | ID 参照 | — | — | (a) のみ |
| equation | `equation_semantics` **53 / 64** | **テーブル無し** | `content_blocks.equations` 1,352 item | `element_type='equation'` 150 行 | `formulas` に 53×19 複製 | (a) |
| evidence（逐語根拠） | `evidence_registry` **101 / 207** | **テーブル無し**（`theory_claims.evidence_text` は意図的に空文字） | `source_evidence_ids` 参照のみ | `evidence` JSONB に断片 | — | (a) のみ |
| derivation chain | `derivation_chain` **11 / 5** | **テーブル無し** | — | — | — | (a) のみ |
| symbol registry | `symbol_registry` **238 / 119** | **テーブル無し** | — | — | — | (a) のみ |
| component | `component_assembly` **21 / 14**（60 フィールド） | `theory_components` **21 / 14**（32 列） | `content_blocks.components` 9 item | 70 行 | — | (a)。(b) は部分投影 |
| 理論操作グラフ | `component_graph` 28n/47e, 77n/78e | `theory_component_graphs.graph_json` 1 行 | — | — | — | (b) だが参照は全部 agent ID |
| thesis | `thesis_reconstruction` | `thesis_refs`/`thesis_context` に抜粋 | topic `summary` に文面 | `role='discussion_seed'` 9 行 | — | (a) |
| 図の意味論 | `figure_table_semantics`, `apparatus_semantics` | 画像のみ `document_figures` | `linked_figure_ids` | 4 行 | — | (a) |
| 説明（二層） | `contextual_explanation` | — | 承認済みのみ凍結時コピー | **319 行（正本）** | — | (d) |
| 配置 | `landscape_placement` | `landscape_placements` 22 行 | — | — | — | (b) |
| コース教材本文 | `blueprint`/`course_mapping` | — | **`topics[]` 1.94MB / 0.20MB（正本）** | — | — | (c) |

**[確認] 正本の判定根拠**: `export_document_bundle`（`export.py:3194-3226`）が `_resolve_artifact_first_claims` / `_resolve_artifact_first_components` / `_resolve_artifact_first_graph` を使い、DB 行は `fallback_sources` に記録したうえでの代替。

### 表 2-2 バイト予算（1文書あたり、`length(...::text)` 実測）

| 格納先 | arXiv | 比率 | fujimoto | 比率 |
|---|---:|---:|---:|---:|
| `document_analysis_runs.stage_outputs` | 2,238,930 | 36.8% | 3,501,372 | 47.2% |
| `chunks`（text+display+spoken+formulas） | 1,411,143 | 23.2% | 3,384,430 | 45.6% |
| `learning_courses.data`（凍結） | 1,940,089 | 31.8% | 201,838 | 2.7% |
| `element_explanations` | 277,301 | 4.6% | 37,396 | 0.5% |
| `theory_component_graphs.graph_json` | 143,858 | 2.4% | 238,660 | 3.2% |
| **`theory_components`** | **58,927** | **1.0%** | **32,696** | **0.4%** |
| **`theory_claims`** | **21,803** | **0.4%** | **27,933** | **0.4%** |
| 合計 | 6,092,051 | | 7,424,325 | |

**正規化テーブル（components + claims）は知識バイトの 1.4% / 0.8%**。

---

## ③ ID 対応表

### 表 3-1 ID 体系の一覧

| ID | 例 | 生成主体 | 安定性 | DB 側の受け皿 | 解決方法 |
|---|---|---|---|---|---|
| `documents.id` | UUID | DB | 安定 | `documents.id` (uuid) | 直接 |
| `material_id`(`source_path`) | `390e0df8-899` | upload | 安定 | `documents.source_path`, `chunks.material_id`, course `sources[].material_id` | `_resolve_document(ref)` 両対応 |
| `chunks.id` | UUID | DB（再解析で再生成） | **不安定** | `chunks.id` | 直接 |
| `block_id` | `tex_b1` / `blk_48f8fa3c` | DocumentStructure | 文書内一意 | `chunks.block_ids`, `source_scope.block_id` | JSONB 走査 |
| `span_id` | `span_001` | RhetoricalRole | **block ごとに振り直し＝非一意** | `source_scope.span_id` | 不能（S-3） |
| `section_id` | — | DocumentStructure | 文書内一意 | `chunks.section_id`(indexed) | 直接 |
| component agent ID | `comp_001`, `comp_002__op3`, `comp_007__r2` | ComponentAssembly（連番＋refine） | **run 間で不安定** | `source_scope.legacy_ids[0]` | JSONB 走査 |
| `theory_components.id` | UUID | DB（再解析で再生成） | **不安定** | — | 直接 |
| claim agent ID | `claim_span_001_9_sub02`, `synth_claim_0001` | ClaimQualification/ObjectBuilder | **run 間で不安定** | 大半に受け皿なし | artifact 走査 |
| equation ID | `eq_tex_b14`, `eq_delta_fourier` | EquationSemantics | 半安定 | **無し** | artifact / `chunks.formulas[].id` |
| evidence ID | `ev_0005` | EvidenceRegistry（連番） | **不安定** | 無し | artifact のみ |
| derivation ID | `derivation_eq_eq_F2`, `step_001` | DerivationChain | 半安定/連番混在 | 無し | artifact のみ |
| graph node ID | `theory_op_0001`, `eq_op_0001` | ComponentGraph（連番） | **不安定** | `graph_json.nodes[].component_id` | graph_json 内のみ |
| `content_hash` | `157b2ea7d491b0dd` | claim/equation agent | **内容由来＝安定**（未網羅） | 無し | **未使用** |
| thesis ref | `central_thesis`, `support:assumptions:2` | ThesisReconstruction | 位置依存 | `thesis_refs`/`thesis_context` | 文字列一致 |

### 表 3-2 実測した解決可能性

| 参照 | 母数 | 解決可 | 率 |
|---|---:|---:|---:|
| 永続グラフ nodes の `component_id` が DB UUID | 810 | 2 | **0.2%** |
| 永続グラフ内の claim 参照が `theory_claims.id` | 6,379 | 2 | **0.03%** |
| artifact グラフの claim 参照が DB で解決 | 46 / 72 | 0 / 1 | **0% / 1.4%** |
| 同・`claim_object_builder` artifact で解決 | 46 / 71 | 46 / 71 | **100%** |
| `element_explanations(theory_claim)` の `element_id` が DB 行 | 86 | 4 | **4.7%** |
| `element_explanations(theory_component)` 同上 | 70 | 57 | 81.4% |
| 凍結コースの component 参照が DB `legacy_ids` で解決 | 3 | 2 | 66.7% |
| `epistemic_ledger`（生存 doc 上）の claim target | 53 | 26 | 49.1% |
| 同・equation target（`eq_op_*`） | 31 | 0 | **0%** |

**[確認]** `element_explanations.element_id` は列内で ID 体系が混在: `figure`/`document` は UUID、`theory_component`/`theory_claim`/`equation` は agent ID。

---

## ④ 版管理の現状

### 表 4-1 再解析で確定が守られるか

| 対象 | 守られるか | 仕組み / 根拠 |
|---|---|---|
| `element_explanations` | ✅ | `candidate` 行だけ `superseded`、`approved`/`dismissed` は不可侵。`core/element_explanations.py:202-280`, `db/056:15-22` |
| `landscape_placements` | ✅ | `inferred` のみ supersede、同キー新候補は skip。`core/landscape/store.py:190-227,253` |
| `document_figures` の `reviewed_*` 列 | ✅ | AI 候補と教員確定が**別列**。`db/052`,`db/053:1-5` |
| `learning_courses.data` 凍結フィールド | ✅（副作用） | 値コピー・自動再構築なし |
| **`theory_components` / `theory_claims` 本体** | ❌ | `DELETE ... WHERE document_id` → 新 UUID。`persistence.py:567,700,704` |
| **`review_status`/`status`/`teacher_notes`/`summary`** | ❌ | INSERT が固定値で上書き（:741 `status='candidate'`、:756 `teacher_notes=''`、:600 `review_status='teacher_review_required'`） |
| **C層 explanations/endorsements/citations** | ❌ | FK CASCADE。`db/021:18,47,69` |
| **R層 reconstruction_items/learner_reconstructions** | ❌ | FK CASCADE。`db/036:25,50` |
| D層 ledger/challenges、W層 annotations/identity_links | ❌（孤児化） | FK 無しで旧 UUID を指したまま残る |
| V層 document ピン | ❌ | `resolver.py:36-46` は呼び出し元ゼロのデッドコード |

### 表 4-2 版の器の実装状況

| 機構 | 状態 | 根拠 |
|---|---|---|
| `run_type`/`base_run_id`/`revision_status` | **生きている**（UI あり） | `routes/revisions.py`, `admin.js:699`。live DB に revision 17 / initial 22 |
| revision の accept | 生きているが**投影は総入れ替え** | `_rebuild_theory_claims_in_session`(:1952 DELETE) / `_rebuild_theory_components_in_session`(:2007,2011) |
| `shared_versions`（V層） | document 面はバッジのみ | live DB 0 行 |
| コース freeze | 稼働（値コピー） | ただし component 詳細は実行時 join で混成 |
| **知識オブジェクトの版（`stable_key`/`superseded_at`）** | **存在しない** | 全 migration に列なし。六つのレンズ 第1波 項目12・判断 D1「認める」済み・未実装 |

---

## ⑤ 発見

### S-1 正本は artifact blob で、関係テーブルは劣化投影
**[確認]** ① export が `artifact_first`（`export.py:3236`）で DB は fallback ② 永続グラフの参照は 100% agent ID ③ 正規化 2 表は知識バイトの 1.4%/0.8% ④ CLAUDE.md 自身が「artifact 併読」を正規手段として記述。
**困りごと**: 検索・結合・制約・インデックス・トランザクションが知識本体に効かない。承認や同一視が、実体を持たない ID 文字列に対して行われる。
**改善方向**: artifact を「不変の生成ログ」に降格し、知識オブジェクト（claim/equation/evidence/derivation/symbol）を一級の行として正規化する。

### S-2 知識オブジェクトの 9 割超に DB 行が無い
**[確認]** 92 テーブルに equations/evidence/derivations/symbols は無い。`persist_qualified_claims`（`persistence.py:530-637`）は `qualified_spans` しか書かず、docstring(:130-141) が「`synth_claim_*` は never become a `theory_claims` row」と明記。
**実測**: claim object 132→9（6.8%）/ 107→17（15.9%）。equation 53/64、evidence 101/207、derivation 11/5、symbol 238/119 は全部 DB 行ゼロ。
**困りごと**: 「この式は他のどの論文で使われているか」が SQL で引けない。`element_identity_links` が live DB で **0 行**なのも、同一視すべき実体に行が無いことと無関係でない[推測]。
**改善方向**: 最低でも equation と evidence を行にする（両者は既に `content_hash` を持つ）。

### S-3 `legacy_ids` は claim では機能していない（span_id 衝突）
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-6 — `legacy_ids` に `{block_id}:{span_id}` と claim object の `claim_id` を追加（Phase 1 の stable_key までの応急））
**[確認]** `persistence.py:585` が `_claim_legacy_keys({"span_id": span_id})` だけを入れる（`claim_id` を渡していない）。
**実測**: arXiv の 9 行すべてが `span_id="span_001"` / `legacy_ids=['claim_span_001','span_001']`。fujimoto 17 行も同じ。artifact 側でも `span_001` は 64 ブロックで再利用。
**困りごと**: agent ID→DB 行の逆引きが 9way/17way に曖昧。表3-2 の 4.7% の直接原因。
**改善方向**: `block_id`+`span_id` の複合、または `content_hash` を論理キーに。`claim_link_index` は既に block_id キーへ移行済みなので同じ是正を適用。

### S-4 agent ID が位置依存で run を跨ぐと意味がずれる
**[確認]** component は `comp_001`…連番＋`__op1`/`__r2`、claim は `claim_span_{span}_{n}_sub{m}`、evidence は `ev_0005`、node は `theory_op_0001`。すべて内容でなく出現順由来。
**実測の dangling**: 凍結コース `5969a478` が参照する `comp_002__r2` は現 DB の legacy_ids 集合（`comp_002__op1..op5`, `comp_007__r1/r2`）に無い。2026-09-01 の再解析で refine 連番が振り直された[推測]が、参照が解決しないことは確認済み。
**困りごと**: 再解析後に「教員が承認した component がどれだったか」を特定できない。差分レビューも原理的に不能。
**改善方向**: 内容由来の安定キーを agent ID と別に発行し、永続化の同一性はそちらで取る。

### S-5 `content_hash` が既にあるのに使われていない
**[確認]** `claim_object_builder.claims[].content_hash`（version 2）と `equation_semantics.equations[].content_hash` が artifact に存在。DB 側に列なし、persist も保存しない。
**実測**: arXiv claim 132 中 83 に hash（`synth_claim_*` 49 は version 0 = 空）、distinct 84。equation は 53→52 distinct、fujimoto は 64→37 distinct（衝突あり）。
**改善方向**: S-4 の安定キー第一候補。ただし `synth_claim_*` 未カバーと equation 衝突を先に潰す。

### S-6 再解析が教員の確定を物理削除する（F2/K1 未実装）
**[確認]** `persistence.py:567`(claims) `:700`(links) `:704`(components) `:1191`(graphs) `:261`(chunks)。chunks 削除は `theory_claims.chunk_id → chunks(id) CASCADE`(`db/013:37`) で claim を**二重に**消す。INSERT 側は固定値上書き（:741/:756/:600）。実データでも `theory_claims` 28 行すべてが `teacher_review_required`。
**[確認] 連鎖**: `component_explanations→endorsements→citations`(`db/021:18,47,69`)、`reconstruction_items→learner_reconstructions`(`db/036:25,50`)。
**[確認] 是正状況**: `docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §2 F2 行 / §4 第1波 項目12 / §9 判断 D1「認める。触るのは persistence.py のみ」。**migration もコミットも存在しない**（`stable_key`/`superseded_at` grep 0 件）。
**困りごと**: 再解析が「AI 出力を改善する操作」でなく「教員の仕事を消す操作」になっている。結果として教員が再解析を避け、コーパスが古い解析に固定される[推測]。
**改善方向**: 安定キー＋`superseded_at` で版化。DELETE を撤去し確定値を新行へ引き継ぐ。**`element_explanations`/`landscape_placements` が既に同型の規則を持つので、規則を発明せず移植すればよい。**

### S-7 早期 return で古い行が残る（S-6 の裏側）
**[確認]** `persist_components` は components 空で `:657-659` 即 return（DELETE も走らない）。`persist_qualified_claims` は spans 空で `:556-558` 即 return。`ctx.skip_component_persist` が真でも persist ごとスキップ。
**困りごと**: 「消える/古いまま残る」が抽出結果次第で変わり、どちらの状態かを画面からも事後にも判別できない（S-10）。

### S-8 `document_id` が TEXT で FK が無く、孤児が滞留
**[確認] 型**: TEXT = runs/components/claims/graphs/figures/deliberation_sessions/element_annotations/epistemic_ledger。UUID = chunks/element_explanations/landscape_placements/landscape_gap_signals。**同じ概念が 1 スキーマ内で 2 型**。
**[確認] FK**: `documents` を参照する FK は chunks / landscape_placements / landscape_gap_signals の 3 本のみ。
**孤児実測**: `document_figures` 560/676 (82.8%)、`epistemic_ledger` 323/407 (79.4%)、`theory_components` 94/131 (71.8%)、`theory_component_graphs` 9/13 (69.2%)、`document_analysis_runs` 19/39 (48.7%)、`theory_claims` 0/28（chunks CASCADE で偶然掃除される）。
**[確認] バイト**: 孤児 run が `stage_outputs` の **17MB/18MB（94%）**、JSON 66.4M/69.1M chars。
**[確認] 原因**: `_purge_document`(`deletion.py:178-272`) は明示 DELETE で広く掃除するが、`delete_material`(`admin.py:1830-1990`) は components/graphs/runs/figures/ledger を消さない。
**困りごと**: 論文を消しても MinIO の図と 17MB の解析結果が残る。S-1 と合わさって消したはずの知識が export に混ざり得る[推測]。
**改善方向**: `document_id` を UUID 統一して FK を張る。ポリモーフィック参照は削除経路を 1 本に集約（`delete_material` → `_purge_document` 委譲）。

### S-9 `stage_outputs` 単一行に全成果が乗る
**[確認] 実測**: 最大 10,462,715 chars（圧縮 2.83MB）。39 run 合計 69MB（圧縮 18MB）、平均 1.85MB。`_artifacts` が **99.5〜99.8%**。
**[確認] revision での単調増加**（material `1c91e8ec-43a` の 9 run）:
```
initial   3ddcba75  1,407,100
revision  13a12593  3,772,988  (rejected)
revision  ad9f5e4f  4,137,414  (proposed)
revision  db2a334b  5,400,283  (superseded)
revision  042e88c3  4,182,664  (proposed)
revision  90833a86  7,980,672  (superseded)
revision  8d4bab25 10,462,715  (superseded)  ← 初回の 7.4 倍
revision  b9ef69e1  2,628,334  (proposed)
revision  c427bd11  2,272,377  (accepted)
```
`update_revision_status` の `jsonb_set(..., '{_artifacts}', 既存 || :artifacts)`(`persistence.py:1625-1630`) が deep merge するため太り続ける。
**困りごと**: ①1 ステージ更新でも 10MB を書き戻す ②per-object の差分・監査不能 ③通常 run の `save_artifact`(`orchestrator.py:577-587`) は in-memory 全体の**浅いマージ**で並行書き込みに弱い[推測] ④GIN index が無く artifact 内容検索は全行スキャン。
**改善方向**: `document_analysis_artifacts(run_id, stage, payload)` に 1 ステージ 1 行で分割。

### S-10 生成物に run の刻印が無い
**[確認]** `theory_components`(32列)/`theory_claims`(16列)/`theory_component_graphs`(9列) に `run_id`/`model` 列が無い。`_stage_models` も部分的で、arXiv の run では 13 LLM ステージ中 6 つしか記録されていない（resume 再利用分は前回値保持）。per-record provenance は claim/equation の `content_hash` のみで、evidence/component はゼロ。
**困りごと**: 「この component はどの run のどのモデルが出したか」に答えられず、モデル変更の比較評価ができない。
**改善方向**: 版化と同時に `produced_by_run_id` を各知識行へ。

### S-11 同一 equation の巨大な複製
**[確認] 実測（arXiv、52 distinct equation ≒ 62KB）**

| 所在 | 複製 | 実測バイト |
|---|---:|---:|
| `equation_semantics` artifact（正本） | ×1 | 361,961 |
| `chunks.formulas`（19 チャンク全部に 53 件ずつ） | ×19 | 1,342,642 |
| `chunks.latex_formulas`（52 件ずつ） | ×19 | 355,490 |
| 凍結コース `content_blocks.equations`（26 トピック全部に 52 件ずつ = 1,352 item） | ×26 | 1,618,384 |
| **合計** | **×46 前後** | **3,678,477** |

**[確認] さらに 3 重**: item の 71%（962/1,352）で `latex == raw_text == plain_text` が完全一致。raw_text+plain_text だけで 815,620 chars。
**[確認] 選択性ゼロ**: 26 トピックの equation_id 集合は全部同一（distinct な集合が 1 つ）。19 チャンクも各 53 件。「そのトピック/チャンクに関係する式」ではなく**全部の式**を毎回コピーしている。
**困りごと**: 式を 1 つ直すと 46 箇所（実際には直せない）。`chunks.formulas` は本文 22KB に対し 1.34MB = **60 倍**で、pgvector 検索行を不必要に膨らませる。
**改善方向**: equation を行にして ID 参照へ。`raw_text`/`plain_text` は導出可能なら列ごと落とす。少なくとも該当分だけに絞る。

### S-12 `theory_components` は artifact の 60 フィールド中 46 を落とす
**[確認]** artifact 60 キー vs DB 32 列。受け皿が無い主なもの:

| 落ちるフィールド | 中身 | 「学ぶ単位」としての意味 |
|---|---|---|
| `teaching_takeaway` | 「Gravity dependence enters through kernel coefficients in F2 and F3.」 | 学習の要点そのもの |
| `teaching_granularity` | `{estimated_slide_count:3, teachable_as_single_unit:...}` | 学習単位の粒度・分量 |
| `prerequisite_concepts` | 前提概念リスト | **前提知識** |
| `linked_equation_ids`/`linked_derivation_ids`/`linked_evidence_ids` | 参照の網 | 依存・根拠のたどり |
| `assumptions`/`approximations` | 前提・近似 | 適用範囲 |
| `concepts`/`introduced_concepts`/`reused_concepts` | 導入 vs 再利用の区別 | 新出語の管理 |
| `operation`/`primary_operation` | `parameterize` 等 | グラフ語彙導出(#266)の根拠 |
| `confidence`/`publish_ready`/`confidence_gate` | 出せるか | 公開判断 |
| `support_distance_to_headline_claim` | 中心命題からの距離 | 学習順の材料 |

**[確認] CLAUDE.md も認識済み**: 「`teaching_takeaway` は DB 列に無いため component_assembly artifact 併読」。
**困りごと**: 前提知識・難易度・学習目標・分量で component を検索・並べ替え・依存解決できない。現状これらが使えるのはコース生成時に artifact から直接読んで**凍結 topic に焼き込む**経路だけ。
**改善方向**: 学習単位として使う属性（takeaway/粒度/前提/operation/依存）を列に昇格。残りは `agent_payload JSONB` にまとめて GIN を張る。

### S-13 学習単位属性の品質が誰にも検証されていない
**[確認] 実測**: `prerequisite_concepts` が文字単位に分解された component が arXiv 2/21、fujimoto 5/14。
```
comp_001 (arXiv)    ["r","a","p","i","s","t"]
comp_004 (fujimoto) ["e","x","t","l","c","r","a","sideband","n","i","cavity"]
```
`set(str)` が文字を展開した形[推測: 原因コード未特定]。**学習者まで届いている** — 凍結コース `5969a478` の topic `t1` の `prerequisite_concepts` も `["r","a","p","i","s","t"]`。
**困りごと**: JSONB の中なので CHECK も型も効かず、`validation_issues` を 288 件出すほど厳格な ExportValidationGate にも引っかからない。**列でないものは検証されない**という S-12 の帰結。
**改善方向**: 列に上げるか、export gate に「語彙項目の最小長」検証を足す。

### S-14 説明・台帳・疑義が「実体の無い ID」に紐づいている
**[確認] 実測**: `element_explanations` の `theory_claim` 86 行中 82 行（95.3%）が対応 `theory_claims` 行を持たない `element_id`。`equation` 150 行はテーブル自体が無い。`epistemic_ledger` は生存 doc 上でも claim target の 51% 未解決、equation target 31 行は全部 `eq_op_*`（グラフノード ID）。
**[確認] 設計上は意図的**: agent ID をキーにすることで再解析の UUID 変更に耐える（S-6 への防御）。
**困りごと**: 防御にはなるが参照整合性を DB で保証できない代償。承認済み説明が「存在しない要素の説明」になっても誰も気づかない。agent が同じ要素に別 ID を振れば旧行は superseded にもならず新 candidate と併存する[推測]。
**改善方向**: S-4 の安定キーを入れれば回避策自体が不要になり FK を張れる。

### S-15 V層の document ピンが読み取りに効かない
**[確認]** `core/versioning/resolver.py:36-46 resolve_document_run_id` は定義のみで**呼び出し元ゼロ**（テストからも呼ばれない）。コース側は `services.py:626 _apply_course_version_view` が全読み取り経路に配線されているのと対照的。CLAUDE.md が「v1 未実装の既知の限界」と明記。live DB の `shared_versions` は 0 行。
**困りごと**: 共有先がピンしても所有者の再解析で見えるものが変わる。S-6 と合わせて「共有した知識は所有者の操作で予告なく変わる/消える」。
**改善方向**: S-6 の版化後は run 単位でなく「オブジェクトの版」でピンできる（粒度が合う）。

### S-16 export はあるが import が無い（転用は一方通行）
**[確認]** `POST /api/courses/{id}/export-bundle`(`export.py:3006`) と `POST /api/documents/{id}/export-bundle`(:3194) が zip を返す。中身は `claims/claims.json`/`components/components.json`/`equations/equations.json`/`equation_candidates.json`/`derivations/derivation_chains.json`/`evidence/evidence_snippets.json`/`thesis/thesis_reconstruction.json`/`manifest.json`/`export_validation.json` ほか(:2922-2995)。**`import` に相当するルート・関数はリポジトリに存在しない**（grep 0 件）。
**困りごと**: 他インスタンスへの転用・研究室間共有・バックアップ復元ができない。カートリッジはファイルでデプロイできるのに、知識そのものは持ち出せても戻せない。
**改善方向**: export 側が既に ID 正規化(`_normalize_export_references`)と検証(`_validate_export_references`)を持つので逆写像の余地はある。ただし S-4 の ID 安定化が前提。

### S-17 前提知識が名前文字列でリンクされている
**[確認] 実測**: 凍結コース topic の `prerequisites` は `[{"name":"修正重力理論のテストとしての大規模構造","status":"not_started"}]` — ID ではなく日本語題名。`chapters` も題名だけの並列リストで、topic の `chapter_index` と ID で結ばれていない。
**困りごと**: 題名を直すとリンクが切れる。コースを跨いだ前提の共有ができない。CLAUDE.md の A3 節が繰り返し触れる「トピック名と概念名が一致しない」問題と同根。
**改善方向**: 前提を component/concept の ID 参照に。題名は表示用に併記。

---

## ⑥ 定量サマリ

### 6-1 知識オブジェクトの DB 化率（ダンプ 2 文書）

| 種類 | artifact（arXiv / fujimoto） | DB 行 | DB 化率 |
|---|---|---|---|
| claim object | 132 / 107 | 9 / 17 | **6.8% / 15.9%** |
| うち atomic 子 claim | 74 / 55 | 0 / 0 | 0% |
| うち `synth_claim_*` | 49 / 35 | 0 / 0 | 0% |
| span 判定（全体） | 65 / 64 | 9 / 17 | 13.8% / 26.6% |
| equation | 53 / 64 | 0 / 0 | **0%** |
| evidence | 101 / 207 | 0 / 0 | **0%** |
| derivation chain | 11 / 5 | 0 / 0 | **0%** |
| symbol | 238 / 119 | 0 / 0 | **0%** |
| component | 21 / 14 | 21 / 14 | 100%（60→32 フィールドに減損） |
| graph | 28n / 77n | 1 / 1 行 | 100%（参照は全部 agent ID） |

### 6-2 参照整合性
- 永続グラフ node の DB UUID 率: **2/810 = 0.2%**
- 永続グラフ内 claim 参照の DB 解決率: **2/6,379 = 0.03%**
- `element_explanations(theory_claim)`: **4/86 = 4.7%**
- `element_explanations(theory_component)`: 57/70 = 81.4%
- 生存 doc の `epistemic_ledger` claim target: 26/53 = 49.1%
- 凍結コースの component 参照: 2/3（`comp_002__r2` が dangling）

### 6-3 孤児（live DB 実測）

| テーブル | 孤児/全体 | 率 |
|---|---:|---:|
| `document_figures` | 560/676 | 82.8% |
| `epistemic_ledger` | 323/407 | 79.4% |
| `theory_components` | 94/131 | 71.8% |
| `theory_component_graphs` | 9/13 | 69.2% |
| `document_analysis_runs` | 19/39 | 48.7% |
| `theory_claims` | 0/28 | 0% |

孤児 run が保持するバイト: **17MB/18MB（94.4%）**、JSON 66.4M/69.1M chars。

### 6-4 サイズ
- `document_analysis_runs` テーブル: **37MB**（全テーブル 2 位。1 位 `lecture_audio_cache` 326MB）
- 単一 `stage_outputs` 最大: **10,462,715 chars**（圧縮 2,829,138 bytes）
- `_artifacts` が `stage_outputs` に占める割合: **99.5〜99.8%**
- revision 9 回での増加: 1.41M → 10.46M chars（**7.4 倍**）
- `learning_courses.data` 最大: 1,940,089 chars（`content_blocks` 1,643,013 = 84.7%、うち equations 1,618,384）
- `chunks.formulas` / `chunks.text` 比: **59.8 倍**（1,342,642 / 22,465）
- 正規化 2 表が知識バイトに占める割合: **1.4%（arXiv）/ 0.8%（fujimoto）**

---

## 付録: 出典
- ダンプ: `realdata/arxiv_2407_01221_{run,db}.json`、`realdata/fujimoto_d_{run,db}.json`
- DB: PostgreSQL 16.14 @ localhost:5432（読み取りのみ）
- 主クエリ: 孤児 `LEFT JOIN documents d ON d.id::text = t.document_id` / サイズ `pg_column_size(stage_outputs), length(stage_outputs::text)` / 型・FK `information_schema.columns`・`pg_constraint` / グラフ ID は全13行の `graph_json` を `^[0-9a-f]{8}-[0-9a-f]{4}-` で UUID 判定
- コード: `backend/core/document_pipeline/persistence.py`(:261,:530-637,:645-865,:977-1199,:1594-1655,:1946-2404)、`backend/api/routes/export.py`(:2922-2995,:3194-3280)、`backend/core/versioning/{resolver,deletion}.py`、`backend/api/routes/admin.py`(:1830-1990)、`backend/db/{013,021,036,056,065}*.sql`
- ドキュメント: `docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md`（§2 F2 / §4 第1波項目12 / §5 K1,K6 / §9 D1）、CLAUDE.md（V層・W層・component 文脈 API の各節）
