# 調査C: 構造化成果の下流消費と転用のレビュー

> **状態: 調査記録（完了）**（2026-09-12、Opus 5 サブタスク。HEAD `c71fc32`・実 DB 読み取りのみ。本調査以降の変更は未評価。
> 親文書は [知識構造の見直し提案](../knowledge_structure_review_2026-09-12.md)）

表記: 【確認】= 現物（DB 行・コード・実データ）で確かめた事実、【推測】= コードからの推論で現物未確認。

## ① 要約

1. 成果の**所在が3系統に分裂**している — DB 行（`theory_*`）/ run artifact（`stage_outputs._artifacts`）/ コース freeze（`learning_courses.data`）。同じ知識が3箇所に別 ID で存在する。
2. 【確認】**ID 名前空間が4つ並存**（DB UUID / agent ID `comp_001` / atomic sub-claim `claim_span_001_9_sub02` / グラフノード `eq_op_0001`）。消費側はどこかで必ず再結合している。
3. 【確認】**graph の claim backing は DB から 0/30・0/72 しか解決できない**。`theory_claims` に sub-claim / `synth_claim_*` が永続化されないため、artifact 併読なしでは全滅。
4. 【確認】コース topic ↔ 成果の結合は**タイトル文字列の重なり率（0.18 / 0.12）**。DHOST コースは 26 topic 中 18 が `content_confidence="none"`（成果と無接続）。
5. 【確認】無接続 topic の「出典」は**位置による代入**（`chunks[topic_index]`）で意味的根拠ではない。
6. 【確認】学習者に届くのは *LLM が書いた日本語散文 + 数式*。claim 埋め込みは 2 コース合計 6 個、component は 6 個。**claims/components は事実上届いていない**。
7. 【確認】学習チャット RAG は **chunks 本文のみ**。構造化成果はチップを押したときの SA層 4 解決器経由でしか入らない。
8. 【確認】**教員承認が 0 件**（approved explanation 0 / endorsement 0 / component・claim の監査イベント 0）。承認ゲートの下流（C層説明・`reconstruction_items=0`）は全部空回り。
9. 【確認】転用の横糸がほぼ死んでいる — `element_identity_links`=0、`atlas_anchor_aliases`=0、`library_entries`=3（分野違い）、live placement は 1 論文のみ。
10. 【確認】再解析は `theory_claims/components/graphs/chunks` を **DELETE→再 INSERT**（新 UUID）。台帳は FK 無しで 194/194 component 行・156/182 claim 行が現に宙に浮いている。

## ② 消費者マトリクス

所在: **DB**=`theory_*` 等の行 / **ART**=`document_analysis_runs.stage_outputs._artifacts` / **FRZ**=`learning_courses.data`。

| # | 消費層 | 読む成果 | 所在 | 使う ID | 読み時の再結合・フォールバック |
|---|---|---|---|---|---|
| 1 | コース内容生成（freeze）`backend/core/course_content_builder.py:61` | course_mapping / component_assembly / equation_semantics / claim_object_builder / evidence_registry / figure_table_semantics / narrative_annotator / document_structure | **ART**（`resolve_artifact_runs` = adopted run）+ DB(chunks, document_figures, element_explanations) | agent ID 全種 | ①topic↔mapping を**タイトル重なり 0.18**（`:1570`）②component を**重なり 0.12**（`:1587`）③fallback chunk を**位置**（`:1629`）④figure を `normalize_figure_join_key` 二段キー（`:909`）⑤equation 説明を `(document_id, equation_id)` で join（`:197`） |
| 2 | blueprint（narrative_arc） | blueprint | ART | agent component ID | **live 消費者ゼロ**。export bundle の `visualization_plan` だけ（`backend/api/routes/export_artifacts.py:1009`） |
| 3 | 原稿スタジオ（教員） | equation_semantics crop / document_structure / chunks / topic FRZ | **ART（別ポリシ: 最新 run・status 無視、`backend/api/routes/lecture_studio/_shared.py:266`）** + FRZ | agent equation ID, chunk UUID | 右ペイン「根拠リンク」は `lsTopicEvidenceItems`（FRZ のみ） |
| 4 | 学習チャット RAG `backend/api/routes/learning.py:3593` | **chunks 本文のみ** | DB | chunk UUID / material_id | `search_chunks_with_metadata(..., allowed_document_ids=)`（`backend/api/services.py:1639`）。**claims/components/equations を一切引かない** |
| 5 | 学習チャットの構造 grounding（SA層 Phase4） | element 文脈 / 台帳 fact_line / landscape 配置 / 表示モード | DB 経由の学習者射影 | チップが持つ ID | `backend/core/assistant_context/resolvers/learning.py:403-406`。**学習者が選んだときだけ**。`topic`/`visible` は未登録（保留） |
| 6 | 教材表示・出典タブ `backend/api/routes/learning.py:2230` | FRZ の `student_material` / `content_blocks` / `evidence_links` | **FRZ のみ** | agent ID（正規化後） | `build_topic_evidence_items`（`course_content_builder.py:1356`）。**DB を一切引かない**（設計どおり） |
| 7 | component 文脈 API `backend/core/component_context.py:100` | theory_components 行 + component_assembly / claim_object_builder / equation_semantics | DB + ART | DB UUID **∪** `source_scope.legacy_ids` | `scoped_id_match_sql` で document スコープ内 fail-closed。曖昧一致は先頭1行 |
| 8 | discuss 開幕 `backend/core/discuss/opening.py` | thesis_reconstruction / **paper_skeleton（paper_goal の正本）** / theory_component_graphs / element_explanations(approved) / epistemic_ledger | ART（`backend/core/deliberation/refs.py:59`= 完了優先・updated_at）+ DB | agent claim ID | `_claim_label_index`（`:755`）が **theory_claims → claim_object_builder artifact** の2段フォールバック |
| 9 | 再構成ループ `backend/core/reconstruction/worker.py:62` | theory_claims 行のみ | **DB** | claim UUID | `support_status='source_backed' AND review_status ∈ APPROVED_REVIEW_STATUSES`（`backend/core/reconstruction/schema.py:37`） |
| 10 | D層台帳 builder `backend/core/doubt/ledger_builder.py` | theory_claims / theory_component_graphs / equations / component_explanations | DB | **target_type ごとに別名前空間**: claim=UUID、**component=グラフノード ID `eq_op_0001`**、equation=agent ID | FK 無し・`ON CONFLICT (target_id,target_type)` のみ |
| 11 | personal map `backend/core/personal_graph/` | interest_traces / theory_claims / theory_components / theory_component_graphs / epistemic_ledger / library_entries / learner_reconstructions / chunks | DB | anchor の生 ID（claim UUID / chunk UUID / `seg_0` / stage 名） | `derive.py:38` の `_ATTRIBUTION_OWNED`（learner_selected/confirmed）のみ採用。`nearby.py` が main ノードの `linked_claim_ids` と素集合演算 |
| 12 | W層（要素検討）`backend/core/deliberation/` | 全 artifact + theory_* + document_figures | ART（`refs.py`）+ DB | `ElementRef(scope, element_type, element_id, anchor)` | `decomposition.py:344` は **`get_latest_analysis_run`（最新・status 無視）** で `refs.py` と別ポリシ |
| 13 | 論文層 `backend/core/graph_paper_layer/builder.py` | graph + document_structure / equation_semantics / evidence_registry / claim_object_builder / symbol_registry / derivation_chain / figure_table_semantics / paper_skeleton / thesis_reconstruction / component_assembly + document_figures + element_explanations | ART + DB 行2種 | agent ID 中心 | **章解決4段**（`builder.py:272` `resolve_section`: eq.source_location.section_id → evidence.source.section_id → claim.section_id → block_id → `unlocated`）、図は二段キー |
| 14 | landscape `backend/core/landscape/` | 骨格 + 論文重心（chunks embedding） | DB | node_id + document_id | `ranking.document_centroid` 再利用 |
| 15 | 論文の海 `backend/core/corpus_view.py` | landscape_placements / landscape_gap_signals / documents / paper_discovery_subscriptions | DB | node_id / document_id | 母集合＝「配置あり ∪ gap 信号あり」 |
| 16 | Admin Copilot `backend/core/admin_assistant/` | **パイプライン成果を一切読まない**（capability registry + docs KB） | — | — | — |
| 17 | Export Bundle `backend/api/routes/export.py:3006 / :3194` | artifact-first（DB は fallback） | ART（`resolve_artifact_runs`）+ DB | agent ID | `_resolve_artifact_first_{claims,components,graph}` + `_normalize_export_references` + `check_refs`。topic↔mapping は **exact title のみ**（`export_artifacts.py:1002`）— live の 0.18 fuzzy と**別規則** |

### run 選択ポリシが4種類に分裂【確認】

| ポリシ | 実装 | 使う層 |
|---|---|---|
| 採用 run（`active_analysis_run_id` → 最新 completed） | `backend/core/document_pipeline/persistence.py:1479` | コース freeze(#1)、export(#17) |
| 完了優先・`updated_at` 降順 | `backend/core/deliberation/refs.py:59-61` | W層、discuss 開幕、論文層、graph review |
| 最新 run（`created_at` 降順・**status 無視**） | `backend/core/document_pipeline/persistence.py:1355` | `backend/api/routes/figure_presentation.py:39`、`backend/core/deliberation/decomposition.py:344` |
| `DISTINCT ON (document_id) ORDER BY created_at DESC`（status 無視） | `backend/api/routes/lecture_studio/_shared.py:272-279` | 原稿スタジオの数式プレビュー |

→ 同じ論文が画面ごとに別 run の成果を映しうる。実データは 1 run/論文なので未顕在【確認】だが、revision 使用で即食い違う【推測】。

## ③ 学習者に届く知識単位の追跡

```
PDF 原本
  └─(GROBID/PyMuPDF)→ chunks（DHOST 19本 / axion 196本）  ← RAG が読むのはここだけ
  └─(A層 29 artifact)→ claims / components / equations / graph / thesis / evidence …
        └─(course_content_builder: タイトル重なり + 位置)→ learning_courses.data.topics  ← freeze
              └─(LLM 二次生成)→ topics[].student_material（日本語散文）  ← 学習者が読むのはここ
```

### 実コース A: `5969a478` DHOST（source = arXiv-2407.01221v2）【確認】

| 段 | 実測 |
|---|---|
| 原本 | 53 equations / 9 claims(DB) / 21 components(DB) / graph 28 nodes（main 5 + equation_detail 23）, 47 edges |
| course_mapping artifact | 21 topics、題名は**agent オペレーション名**（`Define: Galaxy bias kernel map` / `Unknown specific operation: …` / `Derive: P_{\rm L}(k), R, R^{-2a}` ×4 重複、生 LaTeX 入り） |
| コース outline | 8 章 / 26 topics、題名は日本語 |
| 結合 | `exact_title` **0** / `title_similarity` **8** / `none` **18** → 69% が成果と無接続 |
| 各 topic | `student_material` = `{source_text, source_format:"eg-markdown-v1"}`（平均 1,650 字）+ `spoken_script`（平均 560 字）+ `check_questions` |
| 埋め込み | `![[equation:…]]` **165** / `![[component:…]]` **3** / `![[source:…]]` **1** / claim **0** |
| 埋め込み解決 | 全部 freeze 内で解決可 |
| atlas | `atlas_node_id` を持つのは t0 のみ。他 25 は null |
| snapshot | `data` = 1,940,089 字。うち **`content_blocks` 1,643,013 字（85%）** — 全 26 topic が**同じ 52 式を丸ごと**保持 |

t1 の `student_material` は **LLM が書いた教育的散文**で、原本の一文も逐語では含まない。逐語が残るのは `source_excerpt`（英語 200 字）と `evidence_links[].summary` だけで、いずれも本文には出ない。

### 実コース B: `f6ef97f5` アクシオン（source = fujimoto_d）【確認】

| 段 | 実測 |
|---|---|
| 結合 | **`exact_title` 14 / 14**（outline が course_mapping の題名をそのまま採用） |
| 埋め込み | equation 25 / figure 4 / component 3 / **claim 6** |
| `linked_claim_ids` | 49 個（全 topic 合計）— 本文で引用されるのは 6 |
| atlas | 全 topic null、`atlas_binding_pending: "astrophysics"` のまま |
| landscape | 配置 **0 件** → 「論文の海」に出ない |

### 各段の増減

| 段 | 捨てられる | 付け加わる |
|---|---|---|
| 原本 → chunks | レイアウト・図・式の意味 | 埋め込みベクトル |
| 原本 → A層成果 | （原則3で捨てない） | claim 区分・operation 語彙・evidence・DSL・graph・stage |
| A層 → freeze | ①`content_confidence="none"` の topic は**成果全部**（DHOST 69%）②`referenced_sections` は 2026-07-26 以降**生成されない** ③`derivation` / `symbol` / `thesis` / node 単位 narrative は**入らない** ④graph 自体が入らない | 日本語散文・読み上げ原稿・確認問題・（無接続 topic には）位置由来のダミー出典 |
| freeze → 学習者 | claim（6/49）・component（3〜6）・graph・台帳・landscape は既定で不可視 | 章立て・語り口・前後接続の説明 |

### 「学ぶ単位」の定義場所【確認】

**topic**。`learning_courses.data.topics[]` が唯一の学習単位で、進捗・チャット履歴・レクチャー音声・確認問題・前提チェックが全部 topic キーで回る。component / claim / equation は topic に従属する装飾（`linked_*_ids` と `evidence_links`）としてしか存在せず、それ自体を辿る導線は「深く検討」（教員専用 W層）と「いまここの周り」（personal map・本人のみ）だけ。

## ④ 転用の現状表

### (a) 同じ論文を別コース・別教員で再利用するとき

| 対象 | コピー | 参照 |
|---|---|---|
| `student_material` / `spoken_script` / `check_questions` | ✅ コース固有（LLM 再生成） | — |
| `content_blocks` の equations | ✅ **topic ごとに全文コピー**（DHOST: 26×52） | — |
| `evidence_links` | ✅ ラベル・summary・latex・symbols | — |
| PDF 原本・chunks・document_figures | — | ✅ `material_id` / `document_id` |
| theory_components / claims / graph | — | ✅ ただし **freeze 時点の agent ID で凍結**、以後 live の承認状態は反映されない |

転用単位は**コース丸ごと**。「この論文のこの component を別コースでも使う」という操作は存在しない。共有されるのは PDF と artifact だけで、教育的加工は共有されない。

**freeze の是非**: 是 — 埋め込み解決が 100% 成立しているのは freeze のおかげ。非 — ①1.9MB の重複 ②教員が component を承認しても freeze 済みコースに**永久に反映されない** ③再 freeze で音声も全消去。

### (b) 論文横断の横糸【確認】

| 仕組み | 実利用 | 効いているか |
|---|---|---|
| `element_identity_links` | **0 行** | ❌ personal map の旅[2]、component context の `shared_part` は常に null |
| `library_entries` | 3 行・全 `particle_physics`・`standardization_status='unknown'` | ❌ 実論文の分野に 0 件 |
| `landscape_placements` | live 8 行 = **1 論文のみ**（全 `inferred`）、superseded 14 | △ 教員確認 0。axion は 0 配置 |
| `atlas_anchor_aliases` | **0 行** | ❌ 語彙標準化の回路が一度も回っていない |
| `atlas_skeletons` | astrophysics 2版 / modified_gravity 3版+draft / particle_physics 2版 | ✅ 骨格はある |
| コース↔骨格バインド | DHOST 26 topic 中 **1**、axion 0（pending） | ❌ 地図とコースがほぼ未接続 |
| コース側 `concepts` | DHOST 6 / axion 5、`status:"future"` 固定 | △ 骨格とも component とも ID で繋がらない独立の木 |

→ **論文横断の同一性は事実上ゼロ**。

### (c) 外部エクスポート・再利用【確認】

- `POST /api/courses/{id}/export-bundle` / `POST /api/documents/{id}/export-bundle`。ZIP: manifest / export_validation / course_info / claims / thesis / components（`component_schema_version: "0.1.0"`）/ evidence / equations / derivations / document_boundary。
- ✅ artifact-first + `fallback_sources` 明記、`_normalize_export_references` で ID 整合、`check_refs`（`export.py:2437-2457`）で dangling 検出。**唯一まともな可搬形式**。
- ❌ **import 経路が無い**。❌ 外部安定識別子が無い。❌ JSON-LD/RDF 等の標準語彙マッピング無し。❌ 学習者・外部向け読み取り API 無し。
- ⚠️ export の topic↔mapping は **exact title のみ**なので、DHOST コースを export すると live と違い topic↔component リンクが 0 になる【推測】。

### (d) 学習者痕跡と成果の ID 安定性【確認】

再解析は DELETE→再 INSERT で UUID が変わる: `persistence.py:261`(chunks) / `:567`(theory_claims) / `:704`(theory_components) / `:1191`(theory_component_graphs)。

| 参照元 | 生存 / 全体 |
|---|---|
| `epistemic_ledger` claim → theory_claims | **26 / 182**（27 行は**現存する DHOST 論文**の消えた claim UUID、129 行は削除済み document のゾンビ） |
| `epistemic_ledger` component → theory_components | **0 / 194**（target_id が `eq_op_0001` = グラフノード ID） |
| `element_explanations` theory_claim → claim | **4 / 86** |
| `element_explanations` theory_component → legacy_ids | 68 / 70 |
| `interest_traces` の claim アンカー | **2 / 5** |
| `interest_traces` の chunk アンカー | 7 / 7（再解析前） |
| graph node の `linked_claim_ids` → theory_claims | **0 / 30**（DHOST `synth_claim_*`）・**0 / 72**（axion `*_subNN`） |

痕跡側のアンカー分布: `segment` 25（**`anchor_id` は全部 `'seg_0'`**）、`chunk` 7、**`claim` 5**、`stage` 1。**全部 `llm_candidate`**（本人確定 1 件のみ）。

## ⑤ 発見

### C-1. claim の ID 名前空間が3つに割れ、DB が正本でない
→ **2026-09-13 解消**（本文 §4 Phase 1 実装記録: P1-1 / P1-2 — 全 claim が `stable_key` 付きの DB 行になり、graph の claim 参照は DB UUID）
**証拠**【確認】: `theory_claims.source_scope.legacy_ids` は `['claim_span_001','span_001']` の2値のみ。course の `linked_claim_ids` は `claim_span_001_9_sub02` 形式で **6/6・49/49 未解決**、graph は **0/30・0/72 未解決**、`element_explanations` の claim は **4/86**。根因は `persist_qualified_claims`（`persistence.py:550-`）が `qualified_spans` だけを保存し、ClaimObjectBuilder の atomic 子 claim と式合成 claim を保存しないこと。
**困りごと**: claim が「承認・疑義・台帳・再構成出題・学習者アンカー」の共通の係留点になれず、各層が自前で artifact を開き直す。再解析で artifact が差し替わると**静かに別物を指す**。
**改善方向**: atomic claim を一級の永続オブジェクトにする（`theory_claims` に `parent_claim_id` + `origin` を足して sub-claim も行にする）か、逆に「claim の正本は artifact」と決めて `theory_claims` を索引に降格する。中途半端な現状が最悪。

### C-2. 「component」という語が3つの別物を指している
①`theory_components` 行（UUID、legacy `comp_001`）②`course_mapping.linked_component_ids` の `comp_002__op1`③`epistemic_ledger.target_type='component'` の `eq_op_0001`（**graph の equation_detail ノード ID**）。台帳の「component の検証状態」を component context から join できない（0/194）。**改善方向**: 語彙を分離（`theory_unit` / `operation_node` / `graph_node`）するか、`epistemic_ledger` に `target_namespace` を足す。

### C-3. コース topic ↔ 成果の結合がタイトル文字列の重なり率
→ **2026-09-13 解消**（本文 §4 Phase 2 実装記録: P2-3 — `topic.units` を優先し、文字列一致は units 空のときの救済のみ・`source` で区別）
`course_content_builder.py:1570`（重なり 0.18）、`:1587`（重なり 0.12・上位3件）。DHOST は `exact_title` 0 / `title_similarity` 8 / `none` 18。「教員が自分の言葉で章立てするほど成果との接続が切れる」逆インセンティブ。`content_confidence` は教員 UI にも警告として出ない。**改善方向**: outline 生成時に topic へ `linked_component_ids` を**生成させる**。文字列一致は救済にとどめる。

### C-4. 無接続 topic の「出典」が位置による代入
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-4 — `_fallback_chunk_for_topic` 削除・無接続 topic は `content_source="unlinked"` + `grounding_note`）
`_fallback_chunk_for_topic`（`:1629`）は `chunks[topic_index]`。原則8（出所の正直さ）に反し、さらに `has_topic_material`（`learning.py:3598`）がこの本文を「実根拠あり」として **tier を source まで底上げ**する。**改善方向**: 接続できなかった topic は `material_chunk_ids` を空にし事実で書く。

### C-5. RAG は chunks しか読まない — 構造化の投資が対話に還っていない
→ **2026-09-13 解消**（本文 §4 Phase 4 実装記録: P4-2 — SA層 kind `retrieved_structure`。採用 chunk → `theory_claims_live` → 理論操作グラフ main ノードの 1 hop を決定論で当該ターンの入力に足す。LLM 回数不変。[知識の転用層](../../features/knowledge_transfer_design.md) §5）
`api/services.py:1639` の SQL は `FROM chunks c LEFT JOIN documents d`。最大の資産（`CorePredicate` グラフ、導出鎖、記号レジストリ）が**学習者の対話に一度も現れない**。**改善方向**: 検索を「chunk 近傍 → その chunk を出典に持つ claim → その claim を backing に持つ graph ノード」へ 1 hop 拡張し決定論的に grounding へ足す。SA層に `kind="retrieved_structure"` 解決器を1本足すのが最小形。

### C-6. 承認ゲートの下流が全部空回りしている
→ **2026-09-13 一部解消**（P2-5 — コース登録を `decision_context` 付きの一括確定として記帳・承認ゼロ配信を G層 `course.delivered_unreviewed` の事実文で教員に提示。承認語彙の実態合わせは未着手）
`component_explanations` 0 / `component_endorsements` 0 / `element_explanations` approved 0 / `theory_components.review_status` に `teacher_approved` 0 / `theory_claims` 28 件全部 `teacher_review_required` / `theory_review_events` に component・claim・explanation・endorsement が **1 件も無い** / `reconstruction_items` **0 行** / `landscape_placements` confirmed 0。一方 freeze は**承認を経由せず** artifact から直接教材を作るので学習者には届く。**確定の弁を通らない経路だけが実際に機能している**。freeze 時の「コース登録」1操作が実質的な一括承認になっており `decision_context` の対象外。**改善方向**: ①freeze を「一括確定」として `decision_context` に記帳 ②承認 0 のまま配信されている事実を教員に事実文で見せる ③承認語彙の実態合わせはオーナー判断。

### C-7. 再解析が参照を壊し、壊れた参照が誰にも見えない
→ **2026-09-13 解消**（P1-5 UUID 維持 + P1-6 `element_id_remap` と再係留）
`epistemic_ledger` の 129 行が削除済み document のゾンビ。通常の reanalyze は台帳・注釈・痕跡の再係留をしない。**参照が切れた情報は落ちたのと同じ**。**改善方向**: 再解析時に `element_id_remap(document_id, run_id, old_id, new_id)` を1枚作る。`claim_id_map`（`persistence.py:695`）が既に存在するので**それを永続化するだけ**で大半は救える。

### C-8. artifact の run 選択ポリシが4種類
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-8 — `persistence.document_run_artifacts(document_id, *, policy)` に一本化、ガードレールで新規の自前 SELECT を禁止）
`resolve_artifact_runs` の docstring は「成果物参照には active run」と明記するが `figure_presentation.py:39` と `decomposition.py:344` は latest を使う。**改善方向**: `document_run_artifacts(document_id, *, policy)` 1本に寄せる。

### C-9. blueprint が live に接続されていない
→ **2026-09-13 解消**（P2-6 — freeze で `topic.narrative` に持ち込み）
`narrative_arc` を読むのは export のみ。「論文の語りの弧」という転用価値の高い成果が export ZIP の中にしか無い。

### C-10. freeze スナップショットの 85% が同一式の重複
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-4 — 式は `linked_equation_ids` ∪ 本文参照に限定（新規 freeze のみ））
`_fallback_formulas`（`:1638`）が位置代入チャンクの `formulas` を丸ごと content_blocks へ足す。**改善方向**: `linked_equation_ids` ∪ 本文参照 ID に絞る（新規 freeze のみ）。

### C-11. 学習者の痕跡が構造に着地していない
→ **2026-09-13 解消**（P2-7 — 画面選択の要素を `learner_selected` で記帳・`seg_0` 既定を廃止し区画は申告→逐語一致→空）
`structure_anchor` 付き 42 件のうち `segment` 33（`anchor_id` 全部 `'seg_0'`）、確定済み 1 件のみ。**改善方向**: ①`seg_0` 固定は segment ID 採番と slide_index の不接続に見える ②SA層で chat に渡している `element` 選択をそのまま `learner_selected` アンカーとして記帳する。

### C-12. 論文横断の横糸（同一性・別名・共通部品）が 0 件のまま
受け皿は実装済みだが一度も使われていない。原因は「教員の明示操作が必要」+「候補を出す導線が細い」【推測】。**改善方向**: 2論文目の解析時に既存コーパスの component とのベクトル近傍を identity **候補**として自動生成し教員のレビューキューへ（確定は人間のまま）。

### C-13. 学習単位が topic に固定され、component/claim 単位の学習が存在しない
→ **2026-09-13 解消（additive）**（P2-1 / P2-3 — `learning_units` を一級化し topic を `units` の並びとして定義。既存キーの意味は不変）
component:topic が 1:1。**改善方向**: topic を「component の並び」として定義し直す（`topic.units: [{kind, id}]`）。大きな構造変更で不変条項との衝突を要検討。

### C-14. export は良いが片道
→ **2026-09-13 解消**（本文 §4 Phase 4 実装記録: P4-1 import + P4-3 `core/reference_health.py` — DB live 行の参照整合を解析完了時に run へ事実として残し、教材行の事実文と `GET .../reference-health` で常時読める。[知識の転用層](../../features/knowledge_transfer_design.md) §4 / §6）
`check_refs` は本調査で見つけた ID 破断を**検出できる唯一の仕組み**だが import が無く CI にも組み込まれていない。

## ⑥ 触れてはいけない不変条項

| 線 | 出典 | 具体的に何を禁じるか |
|---|---|---|
| **A層非改変** | 原則13 / W1 / UC10 / SL10 / KN-3 | `theory_components` / `theory_claims` / `theory_component_graphs` に**列を足さない**（新層は自分のテーブルを持つ）。C-1/C-7 の改善は「新テーブル + 読み時 join」に落とす |
| **AI は候補まで・確定は人間** | 原則1 / P1 / W2 / LS2 / SL2 | identity link / alias / placement / 説明 / 承認を**パイプラインが確定させない**。C-12 は `status='candidate'` 限定 |
| **確定は再構成可能な手続にのみ** | DC1〜DC4 | freeze を一括確定として扱うなら `decision_context` を記帳 |
| **evidence-based / verbatim** | 原則2 | 再結合の便宜で `evidence_quote` の逐語検査を緩めない |
| **情報を落とさない（知識オブジェクト）** | 原則3 / P4 | 削除 API を作らない。C-10 は新規 freeze のみ |
| **リンクであってマージではない** | 原則7 / KN-2 | 名前空間統合を「片方を消して寄せる」形にしない。対応表で並存 |
| **数値の用途と粒度を統治する** | 原則4 / LS5 / W8 | `content_confidence` 等の**生値を教員にも出さない**。「接続できた/できなかった」の事実文 |
| **出所の正直さ / 閉世界語彙** | 原則8 / DM1 / SL1 | C-4 の是正は**この原則の要請** |
| **同期パスに LLM を入れない** | 原則9 | C-5 の grounding 拡張は**決定論 join** |
| **完了フラグを持たない** | 原則10 / G1 / PN-2 | 「解決済みフラグ」を保存しない。remap 表は事実の記録 |
| **fail-closed** | 原則11 / CR1 | **スコープ無しの ID 解決を新設しない**（`scoped_id_match_sql` が正本） |
| **監視しない** | 原則5 / PN-1 | C-11 は本人可視のまま。新 kind は `trace_registry` に登録 |
| **監査必須・帰属必須** | 原則14 | 再係留・一括承認・identity 確定は `theory_review_events` に記帳 |
| **層は積層し下層を改変しない** | 原則13 | C-13 が最も危険。additive なら可、既存キーの意味変更は不可 |
| **egocentric のみ** | 原則6 / KN-1 | 「コーパス全体の知識グラフを一枚で見る」画面を作らない |

### 特に注意すべき緊張

- **C-1 vs 原則13**: sub-claim を行にするのは A層コードの改変ではないが、`theory_claims` の意味論（1行=1 qualified span）が変わる。**列追加 + 既存行の意味不変**（`parent_claim_id` NULL = 従来行）に限る。
- **C-10 vs 原則3**: 既存 freeze の縮小は保存済み情報を消す。新規 freeze のみに適用。
- **C-6 の「承認 0 のまま配信」**: 「配信を止める」方向は原則12・RR7 と衝突。**事実文で見せる**方向のみが整合する。
