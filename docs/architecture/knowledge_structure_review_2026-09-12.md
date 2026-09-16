# 知識構造の見直し提案 2026-09-12 — 論文の構造化成果を「学ぶ人の知識」として共通化・管理・転用するために

> **状態: 提案（実装対象外）+ 調査記録（完了）**（2026-09-12。HEAD `c71fc32`・開発 DB の実データ2論文を
> 原本と照合。**Phase 0 は同日、Phase 1・2・3 は 2026-09-13 に実装済み**（§4 の各 Phase 実装記録・専用設計書
> [knowledge_objects_design.md](../features/knowledge_objects_design.md) / [learning_units_design.md](../features/learning_units_design.md) /
> [concept_registry_design.md](../features/concept_registry_design.md)）。
> **本調査以降の変更は未評価**。着手時は Phase ごとに専用設計書を切り、migration 番号は
> `ls backend/db/` で採番する）
>
> **体制**: Fable 5.1 が指揮・統合、Opus 5 が5つの調査サブタスク（A 忠実度 / B 格納構造 / C 下流消費 /
> D 外部標準 / E 概念同一性）を並列実施。付属調査の全文は
> [knowledge_structure_review_2026-09-12/](knowledge_structure_review_2026-09-12/) 配下
> （[A](knowledge_structure_review_2026-09-12/A_fidelity.md) /
> [B](knowledge_structure_review_2026-09-12/B_storage.md) /
> [C](knowledge_structure_review_2026-09-12/C_consumers.md) /
> [D](knowledge_structure_review_2026-09-12/D_standards.md) /
> [E](knowledge_structure_review_2026-09-12/E_concepts.md)）。本文中の `F-n` / `S-n` / `C-n` / `X-n` /
> `K-n` はそれぞれの発見番号、`F0-n` は指揮者が直接確認した事実。
>
> **照らす正本**: [vision.md §6 の14原則](../vision.md)（特に原則1・3・7・8・13）。

---

## 0. 一枚の結論

1. **論文は読めているが、知識として保存されていない。** 文章層（paper_skeleton / thesis_reconstruction / narrative / DSL）は原本の骨格を正確に捉えている（F-14）。壊れているのは「それをどの単位に、どの ID で、どこに保存するか」の側であり、LLM の読解力の問題ではない。
2. **構造化層の網羅性は事故で決まっている。** rhetorical_role の `_MAX_BLOCKS = 64` 打ち切りと GROBID のページ誤付与により、190頁の博士論文の component 14 件・claim 17 件は**すべて第3章**由来で、本研究の成果（第6・7章）は1件も構造に入らない（F-1）。
3. **正本と投影が倒錯している。** 知識の正本は `document_analysis_runs.stage_outputs._artifacts` の JSONB blob で、`theory_claims` / `theory_components` は劣化投影（S-1）。atomic claim・equation・evidence・derivation・symbol には DB 行が無く（S-2）、永続グラフの claim 参照 6,379 件中 `theory_claims` に着地するのは 2 件（S-1）。
4. **ID が位置依存で、再解析が教員の確定を消す。** `comp_001` / `claim_span_001_9_sub02` は出現順由来（S-4）、再解析は `DELETE → 再 INSERT` で UUID が変わる（S-6）。台帳・説明・痕跡の参照が現に宙に浮いている（C-7）。`content_hash` は artifact に既にあるのに保存されていない（S-5）。
5. **「学ぶ単位」が定義されていない。** 学習者が触れるのは `learning_courses.data.topics[]` の LLM 散文だけ（C 節③）。topic と component は 1:1 で、component 名は `Transform representation: …` の機械生成（F-5）、前提知識は1文字トークン（`["r","a","p","i","s","t"]`。F0-6 / F-6）。
6. **概念の同一性が無い。** 概念を保持する系統が 10 あり、系統間 FK は 0 本（E 節①）。claim の `concepts` 610 件は全部 LaTeX 記号（K-2）。cartridge alias `SM` の部分一致で、宇宙論論文に「Standard Model」が前提知識として注入されている（F-7 / K-3）。論文横断の横糸（identity link / alias / library）は本番 0 件（C-12）。
7. **確定の弁を通らない経路だけが機能している。** 教員承認は component / claim / explanation とも 0 件で、承認の下流（C層・R層）は空回り。一方コース freeze は承認を経由せず artifact から直接教材を作って学習者に届けており、実質の一括確定なのに `decision_context` の対象外（C-6）。
8. **外の標準は「語彙・行形式・鍵の作り方」として部分輸入できる。** 内容アドレス指定キー（Trusty URI）、SKOS の label 3種 + 関係4種、SSSOM の mapping_justification、SEPIO の evidence line、RO-Crate の可搬スナップショット。RDF/OWL 全面移行・数値表・自動分類の確定値化・適応評価は借りない（D 節③④）。
9. **提案の骨子**: ①artifact を「不変の生成ログ」に降格し、知識オブジェクト（claim 親子 / equation / evidence / derivation / symbol / 図）を**内容由来の版非依存キー**を持つ一級の行にする ②「学ぶ単位」を topic から分離し、文章層と親 component を単位として永続化する ③概念は `library_entries` を軸に SKOS 語彙でリンクし、cartridge は語彙列挙から「形の宣言」へ ④export に import と JSON-LD を足す。
10. **順序**: Phase 0（migration 0・是正8件）→ Phase 1（知識オブジェクトの一級化）→ Phase 2（学ぶ単位）→ Phase 3（概念レジストリ）→ Phase 4（転用）。Phase 1 の安定キーが以降すべての前提（B 節）。オーナー判断は §6 の5件のみ。

---

## 1. 調査の方法と材料

### 1.1 材料（開発 DB・2026-09-12 時点）

| 論文 | 形式 | 規模 | cartridge | 永続化された成果 |
|---|---|---|---|---|
| A: arXiv 2407.01221v2（DHOST 重力の歪度・尖度 consistency relations） | TeX tar.gz（52KB） | 9 節・113 block・19 chunk | particle_physics | components 21 / claims 9 / graph 1（main 5 + detail 23）/ element_explanations 279 / landscape_placements 22 |
| B: fujimoto_d.pdf（DANCE: 光リング共振器によるアクシオン暗黒物質探索、博士論文） | PDF 190 頁（53MB） | 145 節・936 block・196 chunk | particle_physics | components 14 / claims 17 / graph 1（main 5 + detail 72）/ element_explanations 40 / landscape_placements 0 |

artifact 側の規模: A = claim_object_builder 132 claims / equations 53 / symbol records 238 / derivation chains 11。B = claims 107 / equations 64 / symbol records 119 / derivation chains 5。コースは A から 26 topics（cartridge astrophysics・atlas バインド 1 topic）、B から 14 topics（cartridge なし・バインド 0）。

### 1.2 観点と担当

| 調査 | 問い | 主な材料 |
|---|---|---|
| A 忠実度 | 原本のどこが成果に入り、どこが落ちたか。学ぶ単位として何が足りないか | 原本 TeX / PDF 全文 ⇄ 全 artifact |
| B 格納構造 | 何がどこに保存され、ID・版はどう管理されているか | persistence.py / db/*.sql / 実 DB の件数 |
| C 下流消費 | 誰が何をどの ID で読み、学習者に何が届くか | 17 消費層のコード / 実コース2件 |
| D 外部標準 | 外で何が定石か、何を借り何を借りないか | Nanopub / Micropub / SKOS / SSSOM / PROV-O / OMDoc / KLI ほか |
| E 概念同一性 | 「同じ概念」がどう表現され、論文間でどう結ばれているか | 10 概念系統 / theory_components 131 行 / atlas 8 版 |

### 1.3 指揮者が直接確認した事実（F0）

- **F0-1** B の 190 頁が component 14 件・claim 17 件に落ち、component は全件 `component_type='theory'`、全件が第3章由来。
- **F0-2** A の component 名は `Define: Galaxy bias kernel map` / `Unknown specific operation: …` / `Derive: P_{\rm L}(k), R, R^{-2a}`（同名 4 件）。summary は `Reusable theory unit (define_relation) within …; covers N equation steps` の定型。
- **F0-3** B の main 層 5 ノードのラベルに generic な `Transforms` が含まれ、5 件すべて `partially_source_backed`。
- **F0-4** claim は artifact に A 132 / B 107 件あるが `theory_claims` には A 9 / B 17 件。コース B の `linked_claim_ids` 51 件の形式は `claim_span_001_3_sub03`（artifact 側 ID）。
- **F0-5** コース B は 14 topics = 14 components の 1:1 写像。`source_excerpt` が topic 内容と無関係（「Ideal resonance assumptions」の excerpt が第1章の暗黒物質の観測証拠）。
- **F0-6** topics の `prerequisite_concepts` に `['r','a','p','i','s','t']`。発生源は `component_assembly/enrichment.py::_concept_vocab` が `claim_index[..]["concepts"]` を list 前提で反復する一方、上流でこれが str の場合がある（1文字ずつ回る）。型契約が Pydantic で固定されていない箇所が学習者向け DTO まで貫通する例。
- **F0-7** element_explanations は A で equation 150 件（うち 100 superseded）、B は equation 0 件。同じパイプラインでも生成対象が揺れる。
- **F0-8** 両論文とも `particle_physics` で処理（A は宇宙論、B は光学実験）。

---

## 2. 診断 — 5つの構造的問題

### D1. 網羅性が「事故」で決まる（原本 → 構造化層）

| 事実 | 出典 |
|---|---|
| rhetorical_role は `(page, order)` 順の先頭 **64 ブロック**しか役割判定しない。B は 936 中 64（6.8%）。GROBID が 212 ブロックに `page=1` を誤付与しているため、どの 64 かは内容と無関係 | F-1 |
| B の採択 claim 17 / component 14 は**全件第3章**。第4章（中核技術）・第6章（成果）・第7章（診断）は 0 | F-1 / A 節③ |
| A は TeX 経路で figure 3・table 4 が**全欠落**。表は主結果の数値係数そのもの。主結果の式 `kurtosis-consistency-1/-2` に label 付き record なし | F-9 / A-2 |
| 完全性ゲートは件数比較のみで `complete = true` と誤報。B は頁被覆 0.78 でも `ingest_coverage.sufficient = true` | F-17 |
| 打ち切った量はどの stage_output にも現れない（`contextual_explanation` の `truncated_count` は正直） | F-18 |
| 一方 chunks（RAG 層）は原本の 75% を覆い全章に届く。**対話は答えられるのに知識構造は空**という非対称 | A 節⑤ |

**学習者への影響**: 論文Bを教材にした学習者は、博士論文の成果（同時共振の初実現・約3桁の感度向上・上限値）に構造経由で一度も出会わない。

### D2. 正本と投影の倒錯（格納・ID・版）

| 事実 | 出典 |
|---|---|
| export API 自身が `export_source_policy: "artifact_first"` を名乗り DB を fallback とする。正規化2表は知識バイトの 1.4% / 0.8% → **2026-09-13 Phase 1**: 知識オブジェクト層が正本（O-1(a)）・artifact は 1 run × 1 stage 1 行の生成ログへ | S-1 |
| equation / evidence / derivation / symbol に DB テーブルが無い（92 テーブル中）。claim object の DB 化率 6.8% / 15.9% → **2026-09-13 解消**（P1-2 / P1-3: 全 claim object と `knowledge_*` 4 表） | S-2 |
| 永続化される claim は `granularity="too_broad"` の**段落まるごと**（A 9/9）。atomic claim 132 件は捨てられる | F-2 |
| claim_type 239 件の **84% が DB CHECK 語彙外** → 全行 `diagnostic_claim`。component_type は全行 `theory`。`claim_tier` は列が無い → **2026-09-13 解消**（P1-4） | F-3 |
| `legacy_ids` は全行 `["claim_span_001","span_001"]`（span_id が block ごとに振り直され一意でない） | S-3 / F-4 |
| agent ID は出現順由来。凍結コースが参照する `comp_002__r2` は現 DB に不在 → **2026-09-13 解消**（P1-1 stable_key。agent ID は `agent_*_id` 列に保持） | S-4 |
| `content_hash` は artifact に存在するが保存されない → **2026-09-13 解消**（列に保存。同一性は `stable_key`） | S-5 |
| 再解析は `DELETE FROM theory_claims / theory_components` → 新 UUID・`teacher_review_required` 固定値で再 INSERT。C層承認・R層産出物が CASCADE で消える。六つのレンズ F2 の判断 D1 は済んでいるが**未実装** → **2026-09-13 解消**（P1-5 `sync_live_rows`） | S-6 |
| `document_id` が TEXT で FK 無し。孤児 = document_figures 82.8% / epistemic_ledger 79.4% / theory_components 71.8%。孤児 run が stage_outputs の 17MB/18MB → **2026-09-13 解消**（P1-7 migration 080） | S-8 |
| stage_outputs 1 行が最大 10.5MB、revision ごとに deep merge で単調増加（7.4 倍） → **2026-09-13 解消**（P1-8 migration 079） | S-9 |
| 同一 52 式が chunks に 19 回・凍結コースに 26 回・3 表現で複製（×46、3.7MB） | S-11 / C-10 |
| `theory_components` は artifact の 60 フィールド中 46 を落とす（`teaching_takeaway` / `teaching_granularity` / `prerequisite_concepts` / `assumptions` / `linked_*_ids`） → **2026-09-13 解消**（P1-9 列 + `agent_payload`） | S-12 |
| 説明・台帳・疑義が実体の無い ID に紐づく（`element_explanations` theory_claim 86 行中 82 行が対応行なし） → **2026-09-13 解消**（P1-2 で全 claim が行に・P1-6 再係留。既存の宙に浮いた行は次の再解析で追随） | S-14 / F-13 |
| export はあるが import が無い（転用は一方通行） | S-16 / C-14 |

### D3. 「学ぶ単位」が定義されていない（成果 → 教材 → 学習者）

| 事実 | 出典 |
|---|---|
| 唯一の学習単位は `learning_courses.data.topics[]`。進捗・チャット・音声・確認問題が全部 topic キー | C 節③ |
| topic ↔ 成果の結合はタイトル文字列の重なり率（0.18 / 0.12）。A コースは 26 topic 中 18 が `content_confidence="none"` → **2026-09-13 解消**（§4 Phase 2 実装記録）（P2-3: `topic.units` 優先・文字列一致は救済のみ） | C-3 |
| 無接続 topic の「出典」は `chunks[topic_index]` の位置代入。tier を source まで底上げする | C-4 |
| 学習者が読むのは LLM 二次生成の日本語散文。claim 埋め込みは 2 コース合計 6 個、component 6 個 | C 節③ |
| component 21 件は LLM 原案 8 個を決定論 refinement が分割した断片。名前は `{Operation}: {親}`、同名 4 件 → **2026-09-13 解消**（§4 Phase 2 実装記録）（P2-2: 原案は `learning_units(parent_component)`・子行に `parent_agent_component_id`・学習者表示は `display_label`） | F-5 / A-3 |
| 粒度が両極端: A は「1 operation = 1 単位」で細かすぎ、B は「第3章だけ 14 件」で粗すぎ。同じ規則が両方を生む | F-19 |
| 前提知識は名前文字列（`prerequisites: [{"name": "…"}]`）で ID 参照ではない。半順序として検査されない → **2026-09-13 解消**（§4 Phase 2 実装記録）（P2-4: `topic_id` 併記 + 非LLM 半順序検査 API） | S-17 |
| 文章層（skeleton の `logical_blocks` 9 件は B の第1〜8章を覆う / thesis の `support_structure` / DSL 16〜22 ノード）が最も忠実だが、**単位として永続化されず学習者に届かない** → **2026-09-13 解消**（§4 Phase 2 実装記録）（P2-1: `learning_units` 表に5種別で永続化・コースビルダーの候補に提示） | F-14 / F-12 |
| blueprint（語りの弧）は live 消費者ゼロ → **2026-09-13 解消**（§4 Phase 2 実装記録）（P2-6: freeze で `topic.narrative` に持ち込み・散文生成の文脈へ） | C-9 |
| RAG は chunks 本文のみ。claims / components / equations / graph / 台帳を一切引かない | C-5 |

### D4. 概念の同一性が無い（共通化）

| 事実 | 出典 |
|---|---|
| 概念を保持する系統が 10（cartridge ontology / atlas 骨格 / atlas_anchor_aliases / library_entries / theory_components / symbol_registry / dsl_linking ノード / course concepts / chunks.variables / keyphrase）。系統間 FK は 0 本 | E 節② |
| 型語彙が 5 セット（cartridge 8 / component_types.json 14 / DB CHECK 9 / dsl NODE_TYPES 16 / 未使用 OntologyType 11）。述語も `CorePredicate` 9 vs dsl_linking 10（`PRODUCES` の有無） | K-1 / F-12 |
| claim の `concepts` 610 件は**全部 `concept_type="symbol"`**（LaTeX 断片）。A∩B の「共通概念」は `R λ φ θ a i k t` = 全部偽の一致 | K-2 / F-6 |
| alias `SM` の部分文字列一致（`"cosmological"` に当たる）で、原本に 0 回の "Standard Model" が A のグラフ 28 ノード中 25 の `prerequisite_concepts` に注入 | F-7 / K-3 |
| particle_physics cartridge は実質フレーバー物理用（aliases 2 件）。両論文にほぼ無力 | F-8 |
| atlas node_id は版ごとに総取り替え（modified_gravity 2 版の重なり 0/49）。別名・gap・辺は版非依存なのに配置とベクトルだけ版スコープ | K-6 |
| ドメイン跨ぎの同一概念に結合手段が無い（`large_scale_structure` が 2 ドメインに同 node_id・cos 0.89 で重複）。SKOS `exactMatch` 相当が無い | K-7 / K-12 |
| L層 library_entries 3 件はシードのみ・凍結版 0 → パイプラインから不可視 | K-4 |
| 同一性候補を作るパイプライン経路が無い（`duplicate_candidates` は常に `[]`）。W層モーダルの手動対話のみ | K-5 / C-12 |
| identity links 0 / aliases 0 / live placement 1 論文 / コース↔骨格バインド 1 topic | C 節④(b) |

### D5. 確定の弁を通らない経路だけが機能している（原則1・8）

| 事実 | 出典 |
|---|---|
| `teacher_approved` の component 0 / claim 0 / approved explanation 0 / endorsement 0 / `theory_review_events` に component・claim・explanation の記帳 0 / `reconstruction_items` 0 / placement confirmed 0 | C-6 |
| コース freeze は承認を経由せず artifact から教材を作り学習者に届ける。「コース登録」1 操作が実質の一括確定だが `decision_context` の対象外 | C-6 |
| 承認 0 のまま配信されている事実は教員 UI に出ない | C-3 / C-6 |
| 学習者痕跡の structure_anchor は `segment` 33 件の anchor_id が全部 `'seg_0'`、確定済み 1 件 | C-11 |
| run 選択ポリシが 4 種（採用 run / 完了優先 / 最新・status 無視 / DISTINCT ON）に分裂 | C-8 |

---

## 3. 目標構造 — 「知識オブジェクト層」

### 3.1 設計原理（4つ）

| # | 原理 | 根拠 | 照らす原則 |
|---|---|---|---|
| P-a | **artifact は不変の生成ログ、知識オブジェクトは一級の行** | S-1 / S-2 / X-16（nanopub の assertion / provenance / pubinfo 三分離） | 3（情報を落とさない）・10（読み時導出）・13（積層） |
| P-b | **同一性は内容由来の版非依存キー** | S-4 / S-5 / X-1（Trusty URI のハッシュ部分）/ X-2（cluster_key・edge_key の一般化） | 1（確定が再解析に追随する）・7（リンクであってマージではない） |
| P-c | **「学ぶ単位」は「教える単位」で定義し、topic と分離する** | F-14 / F-19 / C-13 / X-10（Knowledge Component） | 8（出所の正直さ）・12（押し付けない） |
| P-d | **概念はレジストリで「リンク」し、正規化は追加であって置換ではない** | E 節⑦ 案B / X-3（SKOS）/ X-4（SSSOM）/ KN-2 | 7・原則1改訂（確定は再構成可能な手続にのみ） |

### 3.2 目標構造の層

```
原本（PDF / TeX、MinIO）
  ↓ 生成（run）
生成ログ層 …… document_analysis_runs + 1ステージ1行の artifacts（不変・追記のみ・run 刻印）      ← S-9 / S-10
  ↓ 永続化（persistence が全知識オブジェクトを行に）
知識オブジェクト層 …… claim（親子 2 階層・claim_tier・語彙表参照）/ equation / evidence /
                     derivation_step / symbol / figure・table / **learning_unit**（文章層・親 component）
                     すべて stable_key（内容ハッシュ）+ produced_by_run_id + supersede 遷移        ← P-a / P-b
  ↓ リンク（候補は AI・確定は人間・justification 必須）
概念レジストリ …… library_entries を拡張（entry_type 拡張・SKOS label 3種・関係 4種・
                  mapping_justification）。symbol → concept、claim.concepts → concept              ← P-d
  ↓ 座標（凍結版・版非依存リンク）
分野の地図 …… atlas 骨格（現行のまま。レジストリ ↔ node は版非依存リンク表）                    ← K-6 / K-7
  ↓ 教材化
教材層 …… topic = learning_unit の**並び**（`topic.units[]`）。freeze は unit の stable_key を参照し、
          本文（散文・読み上げ）だけをコース固有に持つ。freeze は decision_context を記帳          ← P-c / C-6
  ↓ 学習
学習者層 …… 痕跡・アンカー・台帳・説明は stable_key に係留（再解析で切れない）                    ← C-7 / C-11
```

**現行との差分**は3点だけに集約できる: ①知識オブジェクト層の新設（artifact から行へ）②learning_unit と概念レジストリの2つの「一級化」③stable_key による全参照の係留。A層 agent（`src/episteme_graph/agents/`）は非改変（Phase 0 の是正2件を除く）。

### 3.3 14 原則との整合

| 原則 | 本提案での扱い |
|---|---|
| 1 AI は候補まで・確定は人間 | 知識オブジェクトは全て `candidate` 始まり。identity / alias / unit の確定は教員。freeze を一括確定として `decision_context` に記帳（DC1〜DC4）。mapping_justification で「なぜ同じと言えたか」を再構成可能に |
| 2 evidence-based | stable_key は `normalized_text + evidence block_id 集合 + document_id` から導出。verbatim 検査は不変 |
| 3 情報を落とさない | 再解析は DELETE をやめ supersede 遷移（`element_explanations` / `landscape_placements` の既存規則を移植）。既存 freeze の縮小はしない（新規のみ） |
| 4 数値の用途と粒度 | `content_confidence` / cosine / 一致件数を教員にも出さない。「接続できた / できなかった」の事実文のみ |
| 5 監視しない | 学習者アンカーの stable_key 係留は本人可視のまま。新 kind は `trace_registry` 登録 |
| 6 egocentric | 「コーパス全体の知識グラフ」画面は作らない。共通化の成果は旅・近傍・地図の形でのみ |
| 7 リンクであってマージではない | 概念レジストリは SKOS 型の**関係語彙**（`exact_match` / `close_match`）で並存。`owl:sameAs` 的な統合はしない。`text` / `normalized_text` 並存不変 |
| 8 出所の正直さ | 打ち切り・未処理節・未接続 topic を事実文で報告（`material.partial_coverage`）。位置代入の出典を廃止 |
| 9 同期パスに LLM を入れない | RAG の構造 grounding 拡張は決定論 join。stable_key・半順序検査・候補生成の重複検出は非LLM |
| 10 完了フラグを持たない | remap 表は事実の記録。「解決済み」フラグは保存しない |
| 11 fail-closed | stable_key での ID 解決も `document_id = ANY(:doc_ids)` のスコープ強制を外さない |
| 12 押し付けない | 承認 0 の配信を「止める」のではなく事実文で見せる（RR7 と整合） |
| 13 層は積層 | 知識オブジェクト層は**新テーブル**。`theory_claims` に足すのは nullable 列のみ（`parent_claim_id` NULL = 従来行）。A層 agent は非改変 |
| 14 監査必須 | 再係留・supersede・identity 確定は `theory_review_events` にカタログ定数で記帳 |

---

## 4. 段階的着手案

各項目: 何を / どこを触る / migration / 効果 / 出典。**Phase 0 は不変条項に触れず互いに独立**で、いつでも着手できる。Phase 1 が以降の前提。

### Phase 0 — 即効の是正（migration 0・A層の関数 2 本と backend 数箇所）

| # | 何を | どこ | 効果 | 出典 |
|---|---|---|---|---|
| P0-1 | rhetorical_role の 64 打ち切りを**複数バッチ反復**に。打ち切る場合は節単位の層化サンプリング。`page` が信用できない経路はソートキーを `order` のみに。未処理ブロック数・節を `stage_outputs` に報告 | `src/episteme_graph/agents/rhetorical_role/input_builder.py`（A層・是正） | 190 頁論文の第4〜8章が構造に入る。最大の網羅性改善 | F-1 / F-18 → **2026-09-12 解消**（`rhetorical_role/input_builder.py`: 既定は上限なし・env `RHETORICAL_ROLE_MAX_BLOCKS`・節単位の層化サンプリング・`order` 一意なら `order` のみで整列・`summary_stats["coverage"]`） |
| P0-2 | cartridge alias 照合を**語境界付き**に。3 文字以下の alias は大文字完全一致のみ | `rhetorical_role/validator.py:190-195`、`component_assembly/enrichment.py::_fill_concepts` | "Standard Model" 誤注入の停止。下流の共通化が汚染されない | F-7 / K-3 → **2026-09-12 解消**（正本 `agents/alias_matching.py`。validator / enrichment / component_refiner / claim_object_builder の4箇所を語境界照合へ） |
| P0-3 | `concepts` の str/list 契約を dataclass で固定。concept は 2 文字超・記号（`concept_type="symbol"`）を `concepts` から除外し `symbol_registry` に閉じる | `component_assembly/enrichment.py::_concept_vocab` / claim_object_builder の出力型 | 1 文字前提知識の消滅。概念層と記号層の分離 | F0-6 / F-6 / K-2 → **2026-09-12 解消**（`component_assembly/schema.py::concept_name_list` / `is_symbol_like_concept_name`、式記号の concepts 混入を enrichment・refiner から撤去。導出 component の数学性は式リンクで判定） |
| P0-4 | コース生成の位置代入出典（`chunks[topic_index]`）を廃止し、接続できない topic は `material_chunk_ids` 空 + 事実文。fallback formulas を `linked_equation_ids ∪ 本文参照` に絞る（新規 freeze のみ） | `backend/core/course_content_builder.py:1629,1638` | 原則8 の要請。freeze 85% の重複除去 | C-4 / C-10 / S-11 → **2026-09-12 解消**（`_fallback_chunk_for_topic` 削除。出典は evidence の block_id ∩ `chunks.block_ids`、無接続は `content_source="unlinked"` + `grounding_note`、式は linked / 本文参照に限定） |
| P0-5 | 完全性ゲートに**ラベル差集合**と `structure_page_coverage_ratio < 0.9` を判定条件に追加 | document_completeness の判定 | 「complete」誤報の停止 | F-17 → **2026-09-12 解消**（`equation_labels_missing_from_registry` / `structure_page_coverage_low`、`STRUCTURE_PAGE_COVERAGE_MIN = 0.9`） |
| P0-6 | `legacy_ids` に `claim_id` と `{block_id}:{span_id}` を入れる | `persistence.py:585` | claim の逆引きが一意化（Phase 1 までの応急） | S-3 / F-4 → **2026-09-12 解消**（`{block_id}:{span_id}` + claim object の `claim_id`（evidence→block_id の一意 join）を `legacy_ids` へ） |
| P0-7 | L層シード取込時に初版を凍結 | `core/library/seed.py` | パイプラインの凍結版検索に L層が初めて見える | K-4 → **2026-09-12 解消**（`seed.py` が取込直後に `freeze_entry`、`bundled_import` 由来の版ゼロ行を起動時バックフィル） |
| P0-8 | artifact の run 選択を `document_run_artifacts(document_id, *, policy)` 1 本に | `persistence.py` / `deliberation/refs.py` / `figure_presentation.py` / `lecture_studio/_shared.py` | 画面ごとに別 run を映す事故の防止 | C-8 → **2026-09-12 解消**（`persistence.document_run_artifacts(document_id, *, policy)`・`ARTIFACT_RUN_POLICIES = ("adopted", "latest")`・ガードレール `test_artifact_run_policy_guardrails.py`） |
| P0-9 | 文章層（skeleton `logical_blocks` / thesis `support_structure`）と DSL ノードを**読み時**に「章の骨格」として論文層・discuss 開幕・graph review に出す | `core/graph_paper_layer` / `core/discuss/opening.py`（読むだけ） | Phase 2 の前哨。B の第6・7章の成果が画面に現れる | F-14 / F-12 → **2026-09-12 解消**（論文層 `paper.support_structure` / `paper.dsl` / `coverage.unbound_backbone`、discuss 開幕 `documents[].chapter_skeleton`） |
| P0-10 | 「取りこぼしの量」を全ステージ共通の報告形式（母集合 / 処理数 / 打ち切り数 / 理由）に | orchestrator の `stage_outputs` 契約 | G層 To-Do `material.partial_coverage` の材料 | F-18 → **2026-09-12 解消**（正本 `agents/coverage_report.py`、orchestrator `_attach_coverage` で 8 ステージに `coverage`。claim_qualification は母集合が事実で導けず保留） |

#### Phase 0 実装記録（2026-09-12）

10 項目すべてを同日に実装した（migration 0・A層は `rhetorical_role` / `component_assembly` /
`claim_object_builder` の関数是正のみ・不変条項の解釈変更なし）。設計書は切らず本書の各行に
解消注記を付ける（開発チェックリスト §5-3）。テストは backend 14,361 → 14,496 pass、
src 1,859 → 1,924 pass（ベースラインからの増分がすべて追加テスト）。

- **共通正本を 2 本新設**: `src/episteme_graph/agents/coverage_report.py`（取りこぼし報告
  `{population, processed, truncated, reasons, unit?, details?}`。`truncated` は導出値で申告不可）
  と `src/episteme_graph/agents/alias_matching.py`（語境界付き alias 照合。3 文字以下は大小区別の
  単語完全一致）。stdlib のみ依存で src / backend 双方から使う。
- **判断（担当が決めた既定値）**: ①rhetorical_role の既定上限は **0 = 全ブロック**（F-1 の
  主推奨に従う。コストは env で戻せる）②概念名の記号判定は「ASCII は 3 文字未満 / 非 ASCII は
  2 文字未満 / LaTeX 制御記法 / 各部分が 2 文字以下の添字記法」を記号とし、`重力` や
  `zero_recoil_limit` は概念として残す ③導出 component の「数学性」は記号ジャンクではなく
  **式リンクの有無**で判定（export_validation_gate / component validator 同一規則）④無接続 topic は
  `topic.coverage = {status: "missing", message: grounding_note}` も書き、管理 UI の
  "missing" 表示を維持（JS 非改変）。
- **保留（事実で導けないため足していない）**: `claim_qualification` の `coverage`（入力上限 96 は
  実在するが、規則除外と上限切断を同じ 3 値に潰すと読み手を誤らせる）/ landscape prefilter の
  concept 単位の間引き（ドメイン単位の coverage とは別単位）/ G層 `material.partial_coverage`
  （運用実測後）/ P0-3 の記号判定で `concept_type` が読めない str 形の概念は長さ・書式規則のみ。
- **副作用として消えた誤報**: `atlas_state.resolve_topic_concept_via_corpus` が位置代入チャンク由来の
  無関係な骨格概念を返さなくなる（P0-4）。`refs.document_run_artifacts` の意味が「completed 優先の
  最新」から「採用 run」に変わり、走行中・失敗中 run の成果物が表示に混ざらなくなる（P0-8）。

### Phase 1 — 知識オブジェクトの一級化（migration 2〜3 本・新テーブル・A層非改変）

| # | 何を | 効果 | 出典 |
|---|---|---|---|
| P1-1 | **stable_key** = `sha256(normalized_text + evidence block_id 集合 + document_id)` を claim / component / equation / evidence に発行し nullable 列で保存。`candidate_flow.select_supersedable` の照合キーを stable_key に | 再解析後も「同じ主張」が判る。以降すべての前提 | X-1 / S-4 / S-5 |
| P1-2 | **claim 親子 2 階層**: `theory_claims` に `parent_claim_id` / `origin`（span / atomic_rewrite / equation_synthesis）/ `claim_tier` を nullable 追加し、atomic 子 claim と `synth_claim_*` も行にする。既存行の意味は不変（NULL = 従来の span 行） | 承認・疑義・再構成・アンカーの単位が命題になる。graph の claim 参照 6,379 件が DB に着地 | F-2 / C-1 / S-2 |
| P1-3 | **equation / evidence / derivation_step / symbol テーブル**を新設（`content_hash` 既存）。`chunks.formulas` は ID 参照に | 「この式は他のどの論文で使われているか」が SQL で引ける。×46 複製の解消 | S-2 / S-11 / F-16 |
| P1-4 | **型語彙の正本を 1 箇所**（`core/schema.py` の語彙表）にし、DB の CHECK を語彙テーブル参照へ。`claim_type` 23 語彙 / `component_type` の実型 / `CorePredicate` に `PRODUCES` を統合 | 「主結果だけ」「前提だけ」が検索できる | F-3 / K-1 / F-12 |
| P1-5 | **再解析は supersede**: DELETE を撤去し `superseded_at` 遷移 + 確定値（review_status / teacher_notes）を stable_key 一致の新行へ引き継ぐ。`element_explanations` / `landscape_placements` の既存規則を移植 | 教員の確定が再解析で消えない（六つのレンズ F2 判断 D1 の実装） | S-6 / S-7 |
| P1-6 | **element_id_remap(document_id, run_id, old_id, new_id)** を永続化（`claim_id_map` は既に in-memory で存在）。台帳・説明・痕跡を再係留 | 参照が宙に浮かない。宙に浮いた 129 行のゾンビが可視化される | C-7 / S-14 |
| P1-7 | `document_id` を UUID 統一して FK。`delete_material` を `_purge_document` に委譲 | 孤児 17MB の解消。削除経路 1 本 | S-8 |
| P1-8 | artifacts を **1 ステージ 1 行**（`document_analysis_artifacts(run_id, stage, payload)`）に分割。`produced_by_run_id` を知識行へ | 部分更新・GIN・保持期間。「どの run のどのモデルが出したか」 | S-9 / S-10 |
| P1-9 | `theory_components` から落ちている学習属性（`teaching_takeaway` / `teaching_granularity` / `prerequisite_concepts` / `assumptions` / `linked_*_ids`）を列または GIN 付き `agent_payload` に | 学習単位属性の検索と**検証**（列でないものは検証されない） | S-12 / S-13 |

#### Phase 1 実装記録（2026-09-13）

専用設計書 [knowledge_objects_design.md](../features/knowledge_objects_design.md)（KO1〜KO10・§12）に従い、
Fable 5.1 指揮 + Opus 5 の 4 担当（A スキーマ / B 永続化 / C 読み手 / D document_id・削除経路）で同日実装。
migration は **078 / 079 / 080** に採番（`ls backend/db/` で確認）。オーナー判断は O-1(a)・O-2(a) を推奨どおり
採用（設計書冒頭に明記・撤回可）。

| # | 解消 | 実装先 |
|---|---|---|
| P1-1 | stable_key（`k1:` + sha256[:32]。材料は document_id + 正規化テキスト + 出典 block 集合）を claim / component / equation / evidence / derivation step / symbol に発行。既存行は起動時バックフィル | `core/knowledge_objects/stable_key.py` / `backfill.py`・078 |
| P1-2 | claim object 全件（親 / atomic 子 / 式由来合成）+ 吸収されない span を `origin` / `parent_claim_id` / `claim_tier` 付きで行に。`claim_id_map` が全 claim を覆い graph の claim 参照が DB UUID になる | `persistence.persist_qualified_claims` |
| P1-3 | `knowledge_equations` / `knowledge_evidence` / `knowledge_derivation_steps` / `knowledge_symbols` 新設・保存。`chunks.formulas` の ID 参照化は非スコープ（表を作るまで） | 078・`persistence.persist_knowledge_objects` |
| P1-4 | 型語彙の正本を `core/schema.py`（CLAIM_TYPES 38 / COMPONENT_TYPES 21 / CLAIM_TIERS / CLAIM_ORIGINS）に。DB CHECK → 語彙表 FK。`CorePredicate.PRODUCES` | 078・`core/schema.py` |
| P1-5 | 再解析は supersede（`sync_live_rows`: 一致 = 同 UUID 更新・人間の確定列は不変 / 不一致 = `superseded_at`）。DELETE は links の派生構造のみ明示例外。読み手は `theory_*_live` ビュー | `core/knowledge_objects/sync.py`・persistence・34 ファイルの読み手 |
| P1-6 | `element_id_remap` + 6 表の再係留（一意制約はスキップ記録） | `core/knowledge_objects/remap.py`・078 |
| P1-7 | 14 組の `document_id` を UUID + FK CASCADE、孤児は適用時に1回掃除、`delete_material` → `_purge_document` 委譲（図画像の MinIO も掃除） | 080・`deletion.py` / `routes/admin.py` |
| P1-8 | `document_analysis_artifacts`（1 run × 1 stage 1 行）。旧 blob は 079 が1回移送。`save_artifact` は1ステージだけ書く。知識行に `produced_by_run_id` | 079・persistence・orchestrator |
| P1-9 | components の学習属性を列に、残りを `agent_payload`（GIN） | 078・`persistence.persist_components` |

検証: backend 14,637 pass / src 1,924 pass（2026-09-13）。開発 DB の複製に 3 本を 2 回適用して無変更、
全 14 表が UUID + FK、孤児 0、artifact 78 行 = 移送元と一致、`DELETE FROM documents` 1 文で runs まで
CASCADE、`sync_live_rows` の実 PG 往復（同キー = 同 UUID・保護列不変・不一致 = superseded）を確認。

### Phase 2 — 「学ぶ単位」の一級化（migration 1 本）

| # | 何を | 効果 | 出典 |
|---|---|---|---|
| P2-1 | **learning_unit** テーブル: 種別 = `section_block`（skeleton logical_block）/ `thesis_support` / `parent_component`（LLM 原案の 8 個）/ `dsl_node` / `figure`。stable_key・document_id・出典 block 集合・`teaches`（LRMI 相当）を持つ | B で第1〜8章を覆う単位が初めて永続化される | F-14 / F-19 / X-10 / X-15 |
| P2-2 | component の決定論分割は**親子構造**として保存し、名前は親を維持。operation は属性 | 「Transform representation: …」が学習者から消える | F-5 |
| P2-3 | `topic.units: [{kind, stable_key}]` を additive に追加。コース outline 生成時に unit を**選ばせる**（文字列一致は救済のみ）。freeze は unit 参照 + 本文のみコース固有 | 同じ unit を別コースで再利用できる。承認状態が凍結後にも追随 | C-3 / C-13 / C 節④(a) |
| P2-4 | `prerequisites` を unit / concept の ID 参照に（題名は表示用併記）。非LLM の半順序検査（循環・推移的冗長・最小前提集合）を教員向け事実文に | 題名変更でリンクが切れない。**適応評価は恒久排除**（UC5/UC7） | S-17 / X-11 |
| P2-5 | freeze を一括確定として `decision_context` に記帳（`basis` 定数 1 本追加）。承認 0 のまま配信されている事実を教員に事実文で提示（G層ルール 1 本） | 原則1改訂の適用。配信は止めない（RR7） | C-6 |
| P2-6 | blueprint の `narrative_role` / `visual_strategy` を freeze 時に topic へ持ち込む（narrative_annotator と同経路） | 語りの弧が export ZIP の外に出る | C-9 |
| P2-7 | 学習者が chat で選んだ `element` を `learner_selected` アンカーとしてそのまま記帳（AI 候補に回さない）。`seg_0` 固定の採番不具合を是正 | 痕跡が構造に着地する（vision §3.3） | C-11 |

#### Phase 2 実装記録（2026-09-13）

専用設計書 [learning_units_design.md](../features/learning_units_design.md)（LU1〜LU9・§12）に従い、Fable 5.1 指揮 +
Opus 5 の 4 担当（A スキーマ・導出・永続化 / B コース側 / C 前提・G層 / D 痕跡の着地）で同日実装。migration は
**081** に採番（`ls backend/db/` で確認）。オーナー判断は O-3(a)・O-5(a) を推奨どおり採用（設計書冒頭に明記・撤回可）。

| # | 解消 | 実装先 |
|---|---|---|
| P2-1 | `learning_units`（5 種別 = section_block / thesis_support / parent_component / dsl_node / figure。stable_key・teaches・出典 block・review_status candidate 始まり・supersede）+ 語彙表 `knowledge_unit_kinds` + `learning_units_live`。導出は決定論・非LLM | 081・`core/knowledge_objects/learning_units.py`・`persistence.persist_learning_units` |
| P2-2 | 決定論分割の子行に `parent_agent_component_id`（親 = LLM 原案は `parent_component` unit として一級化・子の `name` / stable_key 材料は不変）。学習者表示は unit 経由の `display_label` で親 label を優先（UI の描画配線は残課題） | 081・`persist_components`・`course_content_builder` |
| P2-3 | `topic.units[{kind, stable_key, unit_id, label, source}]` を additive 追加。コースビルダーは候補 handle `U1..Un` を提示し LLM が選ぶ（候補に無い handle は捨てる）。freeze は units 優先・文字列一致は救済のみ（`source:"title_match"` で区別）。学習者 DTO は kind / label のみ | `core/course_units.py`・`routes/admin.py`・`admin.js`・`routes/learning.py::create_course` |
| P2-4 | `prerequisites[].topic_id`（正規化題名の完全一致・曖昧なら引かない）+ 非LLM 半順序検査（循環 / 推移的冗長 / 未解決 / 前方参照の事実文・件数なし）`POST /api/admin/course-builder/prerequisite-check`。`check_prerequisites` は表示名だけ現在の題名に。学習者入力ゼロ（UC5/UC7 恒久排除） | `core/course_prerequisites.py`・`routes/course_prerequisites.py`・`services.check_prerequisites` |
| P2-5 | コース登録を一括確定として `decision_context`（`basis=course_register.units`・presented = 候補 / applied = 束ねた unit・候補ゼロなら記帳しない）で `AUDIT_ENTITY_COURSE_TOPIC` に記帳。G層 `course.delivered_unreviewed`（公開コースの束ねた component / claim に承認が1件も無い事実文・capability `materials.graph_review` 再利用） | `routes/learning.py`・`core/admin_assistant/next_steps.py` |
| P2-6 | blueprint `narrative_arc` を `component_id → {role, visual_strategy}` で索引化し、freeze で `topic.narrative` に持ち込み散文生成の文脈へ（数値・rationale は載せない） | `course_content_builder._collect_structured_content` / `_enrich_topics` |
| P2-7 | 学習チャットの `screen_context.selection` の要素を `learner_selected`（reason=screen_selection）で記帳。`seg_0` 固定は廃止 — 区画はクライアント申告（選択範囲を含む `data-segment-index`）→ 教材本文との逐語一致（一意のときだけ）→ 決まらなければ空 | `routes/learning.py::_learner_selected_anchor`・`core/structure_anchor/selection_segment.py`・`app.js` |

検証: backend 14,905 pass / src 1,924 pass（2026-09-13）。空 DB に init〜081 の 79 ファイルを 2 回適用して無変更（冪等）、
`persist_learning_units` の実 PG 往復（同キー = 同 UUID 更新・`review_status` / `teacher_notes` 保護・不一致 = superseded・
documents 削除で CASCADE）を確認。

### Phase 3 — 概念レジストリ（migration 1〜2 本）

軸は **B 案 = `library_entries` を拡張**（E 節⑦。既に `name + aliases[] + draft/freeze + standardization_status + element_identity_links FK` を持ち SKOS と一対一。atlas 骨格は「座標系」として残す）。

| # | 何を | 効果 | 出典 |
|---|---|---|---|
| P3-1 | `entry_type` を `apparatus / theory_component` から `concept / observable / method / …` へ拡張 | K-1 の型語彙統合の受け皿 | E ⑦ |
| P3-2 | **SKOS 語彙だけ**を共通語彙表に: label 3 種（`preferred / alternate / hidden`）+ 関係 4 種（`broader / related / exact_match / close_match`）。RDF 化しない | `hidden` で OCR ノイズを保持、`close_match` で「近いが別」を記帳でき rejected の反復判断が止まる | X-3 / K-12 |
| P3-3 | **mapping_justification**（`manual_curation / lexical_match / vector_similarity / cartridge_declared / corpus_cooccurrence / llm_candidate`）を identity_links / aliases / gap・edge decisions / placements に additive | 「なぜ同じと言えたか」の再構成（原則1改訂の直接補強） | X-4 |
| P3-4 | レジストリ ↔ atlas node の**版非依存リンク表**（`edge_key` の前例と同型）。ドメイン跨ぎの `exact_match` | `large_scale_structure` の 2 ドメイン重複が結ばれる | K-6 / K-7 |
| P3-5 | `SymbolRecord` に `concept_ref`。UI は記号タップで**直前の定義**を出す（ScholarPhi 規則。既存データのみ・LLM 0 回） | symbol_registry の未活用資産が学習者に届く | X-9 / F-10 |
| P3-6 | 2 論文目以降の解析時に既存コーパスの unit / component とのベクトル近傍を identity **候補**として自動生成（`status='candidate'`）→ 教員レビューキュー | 横糸 0 件の解消。確定は人間のまま | K-5 / C-12 |
| P3-7 | cartridge を語彙列挙から**形の宣言**（ORKG template / SHACL shape 相当）へ。名前と実内容の乖離（particle_physics = フレーバー物理）を解消。入口で論文との適合度を事実として提示（`unplaced_domains` が材料） | 分野中立性の回復 | X-15 / F-8 |

#### Phase 3 実装記録（2026-09-13）

専用設計書 [concept_registry_design.md](../features/concept_registry_design.md)（KR1〜KR10・§13）に従い、Fable 5.1 指揮 + Opus 5 の
4 担当（A スキーマ・コア・正当化列 / B 候補導出・パイプラインステージ / C 記号の直前定義・cartridge 形の宣言 / D 管理 UI・マニュアル・
docs 索引）で同日実装。migration は **082** に採番。オーナー判断 O-4 は (b) `library_entries` 拡張を推奨どおり採用（設計書冒頭に明記・撤回可）。

| # | 解消 | 実装先 |
|---|---|---|
| P3-1 | `library_entries.entry_type` を語彙表 `knowledge_entry_types` FK へ（`LIBRARY_ENTRY_TYPES` = apparatus / theory_component + concept / theory / method / observable / assumption / quantity / process）。既存型語彙からの決定論写像 `entry_type_for_component_type` | 082・`core/schema.py`・`core/library/schema.py` |
| P3-2 | SKOS 語彙だけを語彙表に（label 3 種 / 関係 4 種）。`library_entry_labels`（alternate / hidden。preferred は `name`）+ `library_entry_relations`（無向 kind は `relation_key`・ドメイン跨ぎ可）。RDF 化なし | 082・`core/library/registry.py` |
| P3-3 | `knowledge_mapping_justifications` 6 語彙 + `mapping_justification` を identity_links / aliases / gap・edge decisions / placements と新 3 表に additive 追加（書き込みは必須・既存行は導出可能なものだけバックフィル） | 082・各 store |
| P3-4 | `library_atlas_node_links`（版非依存 `link_key`・exact_match / close_match・ハブ経由でドメイン跨ぎ）+ 決定論の候補導出 + `POST /api/admin/library/atlas-links/derive` | `core/library/atlas_links.py` |
| P3-5 | 記号 → 概念は `element_identity_links` の `symbol` instance（`SymbolRecord` は非改変 = A層非改変。`concept_ref` は読み時 join）+ 学習者 API `symbols/lookup`（ScholarPhi 規則「直前の定義」・LLM 0 回）+ KaTeX 記号クリックのポップオーバー | `core/symbol_lookup.py`・`routes/learning.py`・`app.js` |
| P3-6 | パイプラインステージ `identity_candidates`（末尾・非LLM・embedding 0 回・非致命）が live 親 component の語彙一致 / 他 document との正規化名一致 / chunk-proxy 近傍から candidate entry + identity link を作り `duplicate_candidates` を埋める。教員レビューは `GET /identity-candidates` + ナレッジライブラリタブ「同一性の候補」 | `core/library/identity_candidates.py`・`orchestrator.py`・`admin.js` |
| P3-7 | `shape.json`（`covers` / `does_not_cover` / `expects` / `atlas_domain_key`）+ `particle_physics` の description を実内容（フレーバー物理）に訂正 + 適合事実 `GET /api/admin/cartridges/{id}/fit`（`unplaced_domains` / live 配置 / covers 語の語境界一致・数値なし）を再解析モーダルに | `core/cartridge_shape.py`・`routes/cartridge_shape.py`・`admin-cartridge-fit.js` |

検証: backend 15,276 pass / src 1,924 pass（2026-09-13）。実 DB での 082 適用・E2E は docker 復帰後。

**K-6 追補（同日）**: atlas node_id の版間対応は [atlas_node_correspondence_design.md](../features/atlas_node_correspondence_design.md)
（NC1〜NC8・migration なし）で実装。骨格の既存スロット `id_migrations` を格納庫にし、凍結前の `freeze-impact` に決定論候補を並べて教員が確定
（`decision_context`）、読み手は `NodeResolver` で読み替えるだけ（`node_id` を UPDATE しない・未対応は事実文）。オーナー判断は推奨案
（対応表を持つ / 確定は凍結前 / 未対応は旧行を残し事実文）を採用。

**K-2 追補（同日）**: `claim.concepts` の concept 層は、オーナー判断（既存 `concept_resolver` 注入口への辞書供給 = A層非改変の範囲内 /
LLM 抽出ステージ = 実測後に判断）を受けて [claim_concept_grounding_design.md](../features/claim_concept_grounding_design.md)
（CG1〜CG7・migration なし・LLM 0 回）で実装。辞書 = レジストリ confirmed ラベル + cartridge 別名（分野指定時のみ）+ DSL ノード名
（記号除外・語境界一致）。前段は builder への注入、後段は `dsl_linking` 直後の決定論フック `_hook_claim_concept_grounding`。
出所は `theory_claims.concepts` の各要素へ additive、`identity_candidates` 規則 ④ で主張 → レジストリの糸。学習者の概念マップは記号を除く。
`concept_assignment_status` は昇格させない（registry だけの run は非空の provenance-only ontology で `inferred` に留める）。

### Phase 4 — 転用（migration 0〜1 本）

| # | 何を | 効果 | 出典 |
|---|---|---|---|
| P4-1 | export bundle に `@context` を被せ **RO-Crate 型 JSON-LD** で出せるように。**import** を実装（`_normalize_export_references` の逆写像・stable_key 前提） | 他インスタンス・他ツールへの転用。片道の解消 | X-13 / S-16 / C-14 |
| P4-2 | RAG を「chunk 近傍 → その chunk を出典に持つ claim → backing に持つ graph ノード」へ **1 hop 決定論拡張**（SA層 `kind="retrieved_structure"` 解決器 1 本） | 構造化の投資が対話に還る。LLM 回数不変 | C-5 |
| P4-3 | `check_refs` を常時の健全性チェックとして教材行の事実文に | ID 破断の可視化 | C-14 |
| P4-4 | 版の 2 系統（PROV-O `wasRevisionOf` / `alternateOf`）を語彙として layer_registry に宣言（コード変更 0） | V層 / landscape / library の「版」の意味分離 | X-12 |
| P4-5 | `challenges` に `challenge_mode ∈ {direct, undercut}`、台帳に `evidence_lines`（SEPIO）、`component_citations` に `citation_intent`（CiTO 3〜5 語） | D層・C層の表現力。記帳は人間のみ不変 | X-5 / X-6 / X-7 |

#### Phase 4 実装記録（2026-09-13）

専用設計書 [knowledge_transfer_design.md](../features/knowledge_transfer_design.md)（KT1〜KT8・T-1〜T-3・§14）に従い、Fable 5.1 指揮 + Opus 5 の
5 担当（第1波 A〜D = P4-1 / P4-2 / P4-3 / P4-5 のバックエンド、第2波 E = UI・アンカー・マニュアル）で同日実装。P4-4 と索引系文書は指揮者。
migration は **083** に採番（P4-5 の列追加のみ）。オーナー判断を要する項目は無し（取り込み行の承認非継承 = T-1 / live 行がある document への
取り込みは明示 `replace` = T-2 / 健全性は事実文 1 行 + 列挙 = T-3 を推奨で固定・撤回可）。

| # | 解消 | 実装先 |
|---|---|---|
| P4-1 | export bundle に `ro-crate-metadata.json`（RO-Crate 1.1 + PROV・人名なし）と各項目の `stable_key` / `knowledge_object_id`、manifest 0.3.0。import `POST /api/documents/{id}/import-bundle`（dry-run → 教員確定・`sync_live_rows` へ行として・stable_key は取り込み先で再計算・承認は継承しない・DELETE なし・`AUDIT_ENTITY_IMPORT`） | `routes/export.py`・`core/knowledge_import/` |
| P4-2 | SA層 kind `retrieved_structure`（採用 chunk → `theory_claims_live` → 理論操作グラフ main ノードの決定論 1 hop を当該ターンの入力に。LLM 回数不変・casual / elicit では出さない・登録 kind は 5 つ） | `core/assistant_context/resolvers/learning.py`・`routes/learning.py` |
| P4-3 | `core/reference_health.py`（live 行の参照切れ 4 検査・数字を書かない）→ 解析完了時の `stage_outputs.reference_health` + 教材行の事実文チップ + `GET /api/admin/documents/{id}/reference-health` | `core/reference_health.py`・`routes/reference_health.py`・orchestrator・`MaterialOut` |
| P4-4 | `layer_registry.md` §4「版の語彙」で revision（`prov:wasRevisionOf`）/ alternate（`prov:alternateOf`）を全「版」構造へ宣言。コード変更 0・`test_version_semantics_docs.py` が網羅を固定 | docs |
| P4-5 | 083: `challenges.challenge_mode` / `target_element_ref`、`epistemic_ledger.evidence_lines`（人間専用）、`component_citations.citation_intent`（`core/schema.py::CITATION_INTENTS`）。API は additive・削除なし・学習者向けは事実文 1 行 | `backend/db/083_*.sql`・`core/doubt/schema.py`・`core/label_vocab.py`・`routes/doubt.py`・`routes/theory_components.py` |

検証: backend フルスイート 15,863 pass / src 1,924 pass（2026-09-13）。実 DB での 083 適用・往復 E2E は docker 復帰後。

---

## 5. 借りないこと・やらないこと

| 借りない | 理由 |
|---|---|
| RDF/OWL への全面移行 | `core/` の純関数設計と JSONB + pgvector の資産を捨てる。SKOS 自身が「OWL より弱い橋渡し」を標榜 |
| ORKG comparison の数値表 / Elicit・Consensus 型の賛否集計 | 数値が優劣として読まれる。SL1 閉世界語彙・改訂原則4 と衝突 |
| scite 型の自動分類の確定値化 | W2 / GR1。実測分布 contrasting 0.8% では UI が空 |
| Novak/Gowin の concept map 採点・Bloom の段位・KST の adaptive assessment | 地図を成績の代理にする。UC5 / UC7 が恒久条項（F4 の撤去判断を逆行させない） |
| Walton の argumentation scheme カタログ | 語彙爆発。本システムの議論は「論文がどう推論したか」 |
| nanopub の分散署名 / OBO の中央 ID 発行 / DataCite | 単一機関内に過剰。V層「所有者のみ発行」で等価 |
| 「コーパス全体の知識グラフ」画面 | 原則6（egocentric） |
| 承認 0 の配信を止めること | 原則12 / RR7。事実文で見せる方向のみ |
| 既存 freeze の破壊的縮小・既存 `theory_claims` 行の意味変更 | 原則3 / 13。新規のみ・nullable 追加のみ |

---

## 6. オーナー判断が要る点（5件・推奨付き）

「不変条項の解釈変更・人の権利や制度・後戻りしにくい構造」の3種だけを挙げる。是正の是非と実装方式は担当が決める。

| # | 判断 | 選択肢 | 推奨 | 理由 |
|---|---|---|---|---|
| O-1 | **artifact を「正本」から「生成ログ」へ降格する宣言**（後戻りしにくい構造。Phase 1 全体の前提） | (a) 降格し知識オブジェクト層を正本に (b) 逆に「正本は artifact」と決め DB を索引に降格 (c) 現状維持 | **(a)** | (c) は「中途半端な現状が最悪」（C-1）。(b) は検索・FK・制約・部分更新を永久に諦める。(a) は A層非改変で積める |
| O-2 | **`theory_claims` の意味論**（1 行 = 1 qualified span → 親子 2 階層）は原則13 の「既存キーの意味変更」に当たるか | (a) nullable 列追加で既存行不変とみなす (b) 新テーブル `knowledge_claims` を別に立て `theory_claims` は凍結 | **(a)** | `parent_claim_id` NULL = 従来行で意味不変。(b) は名前空間をさらに増やす（C-2 の再演） |
| O-3 | **freeze を「一括確定」として `decision_context` に記帳するか**（原則1改訂の解釈） | (a) 記帳する（`basis` 1 本追加） (b) freeze は教材化であって確定ではないとみなす | **(a)** | 承認 0 でも学習者に届く唯一の経路が freeze（C-6）。「提示された根拠・代替・拒否可能性」を残せるのはここだけ |
| O-4 | **概念レジストリの軸** | (a) atlas 骨格 (b) `library_entries` 拡張 (c) 新設 | **(b)** | atlas は座標系で語彙ではなく AI が書けない（LS7/AB4）。(c) は 11 系統目。(b) は SKOS と一対一で「確定は人間・行削除なし・candidate 始まり」を既に実装済み（E ⑦） |
| O-5 | **承認ゼロで配信されている事実の見せ方**（原則8 vs 12） | (a) 教員に事実文（G層 1 本）(b) 学習者にも「教員未確認」ラベル (c) 見せない | **(a)** | (b) は「未検証と検証済みを同じ精度で併記」（D層 §8-1）とは別軸で、承認制度の実態を学習者に転嫁する。(c) は原則8 に反する |

---

## 7. 統合定量サマリ

| 指標 | 論文A | 論文B | 出典 |
|---|---|---|---|
| 原本 → chunks の被覆 | 全編 | 頁 78% / 文字 75% | A 節⑤ |
| 役割判定されたブロック | 64 / 113（57%） | 64 / 936（**6.8%**） | F-1 |
| claim: artifact → DB | 132 → 9（6.8%、too_broad 9/9） | 107 → 17（15.9%、too_broad 14/17） | F-2 / S-2 |
| claim_type が CHECK 語彙外 | 84%（両論文合計 201/239）→ 全行 `diagnostic_claim` | 同 | F-3 |
| component: LLM 原案 → 分割 | 8 → 21（同名 4 件） | 14 | F-5 |
| 図 / 表 | 0 / 0（原本 3 / 4） | 86（interpretation 0）/ 0 | F-9 |
| graph の claim 参照が DB に着地 | 0 / 30 | 0 / 72 | C-1 |
| 永続グラフ全体（13 本）の claim 参照 → DB | 2 / 6,379 | — | S-1 |
| コース topic ↔ 成果の接続 | exact 0 / similarity 8 / none 18 | exact 14 / 14 | C-3 |
| 学習者に届く claim / component 埋め込み | 0 / 3 | 6 / 3 | C 節③ |
| freeze の `content_blocks` 重複 | 26 topic × 同一 52 式（85% = 1.6MB） | — | C-10 |
| 教員承認（component / claim / explanation） | 0 / 0 / 0 | 0 / 0 / 0 | C-6 |
| 論文横断の横糸（identity / alias / library 凍結版） | 0 / 0 / 0 | 同 | C-12 / K-4 |
| 概念系統 / 系統間 FK | 10 / 0 | — | E 節② |
| `document_id` 孤児（components / ledger / figures） | 71.8% / 79.4% / 82.8% | — | S-8 |
| stage_outputs 最大 / 孤児 run の占有 | 10.5MB / 17MB of 18MB | — | S-9 / S-8 |

---

## 8. 付属資料と着手時の手順

- 付属調査（全文・証拠・出典キーパス）: [A 忠実度](knowledge_structure_review_2026-09-12/A_fidelity.md) / [B 格納構造](knowledge_structure_review_2026-09-12/B_storage.md) / [C 下流消費](knowledge_structure_review_2026-09-12/C_consumers.md) / [D 外部標準](knowledge_structure_review_2026-09-12/D_standards.md) / [E 概念同一性](knowledge_structure_review_2026-09-12/E_concepts.md)。
- 関連する既存記録: [六つのレンズ調査](vision_ux_gap_six_lenses_2026-09-10.md)（F2 再解析 DELETE の判断 D1）/ [知識ネットワークビジョン](../features/knowledge_network_vision.md)（KN-1〜4）/ [E層設計](../features/exposition_layer_design.md)（本提案の learning_unit は E層の「足場」ではなく A層側の「単位」。E層とは別物で、E層着手時は learning_unit を翻訳の入力にできる）/ [candidate_flow](../features/candidate_flow_design.md) / [label_vocab](../features/label_vocab_design.md)。
- 着手の型は「討論 → 設計書 → 実装 → 実装記録」（vision §9）。Phase 0 は設計書不要で個別 PR 可。Phase 1 以降は Phase ごとに `docs/features/*_design.md` を切り、本書 §6 の判断結果を冒頭に記す。
- 本書の数値は 2026-09-12 の開発 DB（実論文 2 本 + テスト 9 本）の実測。本番コーパスでは比率が変わり得るが、構造的原因（64 打ち切り・非永続化・位置依存 ID・FK 不在・文字列一致）は運用データ量に依存しない。

---

## 9. 実装レビューと是正の記録（2026-09-13）

Phase 1〜4 の実装完了後、Fable 5.1 指揮 + Opus 5 で **敵対的コードレビュー 4 本（Phase 別）+ scratch DB での実データ検証**を
行い、同日に是正した。各 Phase の詳細は専用設計書の実装記録（KO §12.2 / LU §12.2 / KR §13.3 / KT §14）が正本。

### 9.1 検証方法
- 稼働中の開発 DB は一切変更せず、同一 Postgres 上に新規 DB を作って ①`init.sql` + 077 以下 → 全データ複写 → 078〜083 を
  3 回適用（アップグレード経路・冪等性・孤児掃除件数）②空 DB に全 migration → 実論文 A/B の artifact を LLM 0 回で永続化 →
  同一 artifact 再実行・内容変更・承認引き継ぎ（再解析の supersede）を実測。
- 結果: **アップグレード経路は GO**（孤児掃除 components 94 / links 92 / graphs 9 / runs 19 / figures 560 / ledger 323 =
  設計書 §8.3 の明記どおり、backfill 冪等）。**永続化経路は HEAD で NO-GO**（下記 V-1 / V-2 / V-10）→ 是正後に再検証。

### 9.2 致命（🔴）と是正
| # | 事実 | 是正 |
|---|---|---|
| P1-R1 / V-10 | `DELETE FROM chunks` → `theory_claims.chunk_id` の CASCADE で再解析が claim 行を物理削除（承認込み） | migration 084 で FK を `SET NULL`、chunks を `chunk_index` キーの upsert に |
| V-1 | `persist_knowledge_objects` が evidence / symbols に無い `review_status` を preserved 指定し `UndefinedColumn` で run 全体が failed | preserved 列を表ごとに |
| V-2 | derivation step の agent_id がチェーン内でしか一意でなく stable_key 衝突で一意制約違反（import も同型） | `derivation_step_agent_id` / `derivation_step_stable_key(derivation_id, step_index)` を正本化 |
| P1-R2 / R11 | live ビュー読み漏れ 3 箇所（superseded 行を優先して返す）+ 動的表名を見逃すガードレール | 3 箇所を live へ、ガードレールを全文走査 + `ファイル:シンボル` 粒度に |
| P4-R1 | import-bundle に展開後サイズ上限が無く zip bomb で OOM（654KB → RSS 1.1GB を実測） | 展開後サイズ・項目数の上限、`RecursionError` の 422 化 |
| P4-R2 | `replace=true` が承認済み live 行を回復不能に supersede し dry-run が範囲を開示しない | 人間確定行を supersede 対象から除外、dry-run に内訳と対象列挙、事実文の是正 |

### 9.3 要修正（🟠）の主なもの
教員選択 unit の上書き（P2-R1）/ 散文の title 一致混入と `content_confidence` の偽装（P2-R2）/ `seg_0` 固定（P2-R3 →
`build_topic_slides` のページ境界に統一）/ unit 候補・教材コンテキストの可視性ゲート欠落（P2-R5）/ 候補エントリ名の全教員露出
（P3-R2 / R3）/ 学習者 DTO の内部 ID（P3-R4）/ dry-run↔確定の TOCTOU と `decision_context` 欠落（P4-R4）/
claim 本文の教員編集が上書きされる（P1-R4）/ backfill 近似キーの構造不一致（P1-R3）/ `claim_tier` 常に空（V-3）/
式由来 claim が全件 `unknown`（V-4）/ 生 LaTeX の学習者露出（V-6 / V-8）/ `reference_health` が常時 broken（V-9）。

### 9.4 新たに生じたオーナー判断
| # | 判断 | 推奨 |
|---|---|---|
| O-6 | L層の不変条項「昇格は人間の操作のみ」を Phase 3 が「candidate 行はパイプラインが作る・可視化（凍結）は人間のみ」と読み替えた点の可否 | **(a) 認める**（凍結 409 ゲートで構造的に守られ、tension / landscape と同じ candidate 始まりの型。否決時は候補の保存先を `library_entries` から専用表へ移す差し替えのみ） |

### 9.4b 是正後の再検証（scratch DB）
永続化経路は迂回なしで完走し、行数は §7 と完全一致。再解析 132/132 同 UUID・承認保持・chunks upsert・第 2 段突合の UUID 引き継ぎを実測。
是正の途中で **migration 084 の `%I`（`%%I` であるべき 1 文字）が起動失敗を招く**ことが見つかり修正、`TestPercentEscapeLint` で再発を固定
（KO §12.3）。

### 9.5 見送り・後続
`pipeline:identity_candidates` の `KNOWN_FEATURES` 削除（P3-R12）は前提誤りで見送り（U層 feature は帰属語彙表・全ステージ網羅がテストで強制）。
sync の N+1・3 系統の別トランザクション（P1-R8）/ P4-2 の同期パス追加セッション（P4-R12）は実測後の後続課題。
K-6 版間対応は開発 DB の 2 版が別主題（ラベル一致 0）のため**有効性未検証**（同一主題の改訂版で測り直す）。
