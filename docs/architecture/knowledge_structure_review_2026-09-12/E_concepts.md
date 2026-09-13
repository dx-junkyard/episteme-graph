# 調査E: 概念の同一性・語彙体系の共通化レビュー

> **状態: 調査記録（完了）**（2026-09-12、Opus 5 サブタスク。本調査以降の変更は未評価。親文書は
> [知識構造の見直し提案](../knowledge_structure_review_2026-09-12.md)）


調査日 2026-09-12 / 対象 `ura-dev` @ c71fc32 / DB は localhost:5432 実データ（読み取りのみ）
実データ: 論文A = `arXiv-2407.01221v2`（DHOST 重力・大規模構造、doc `f5d9d629`）/
論文B = `fujimoto_d`（アクシオン暗黒物質・光学リング共振器、doc `73460388`）。両者とも
`document_analysis_runs.cartridge_id = "particle_physics"`。

**凡例**: 〔確〕= 現物（DB / artifact / ソース）で確認した事実。〔推〕= 現物からの推論。

---

## ① 要約（10行）

1. 「名前を持つ知識の単位」を保持する系統が **10 系統**あり、系統間を結ぶ FK は1本もない。結合は
   全て「文字列の一致」か「人間の明示リンク」で、後者は本番0件〔確〕。
2. 概念型の語彙が **4 セット**（cartridge `concept_types` 8 / `component_types.json` 14 /
   DB CHECK 9 / dsl_linking `NODE_TYPES` 16）並存し、互いに部分的にしか重ならない〔確〕。
3. `theory_components` 131行の `component_type` は **全件 "theory"**（DB CHECK 側）。実際の型は
   自由文 `component_type_text` にあり、18 値中 10 値（82/131 = 62.6%）がカートリッジ語彙外〔確〕。
4. claim の `concepts` は **610 件全部が `concept_type="symbol"`**（LaTeX 断片）。分野概念は0件。
   論文A∩Bの「共通概念」は `R, λ, φ, θ, a, i, k, t` の8個＝**全部偽の一致**〔確〕。
5. 部品の `concepts` 充填が素朴な部分一致のため、`mismatch` の "sm" が
   cartridge alias `SM → Standard Model` に当たり、**アクシオン共振器の論文に「Standard Model」が
   概念として付与**されている〔確〕。course topic の `introduced_concepts` まで伝播〔確〕。
6. 論文横断の同一性を持つ唯一の座標系は atlas 骨格だが、**node_id が版ごとに総取り替え**
   （modified_gravity 2026.0808 vs 2026.1 の id 重なり 0/49）〔確〕。
7. 共通部品（`library_entries`）3件は全部カートリッジ同梱シードで、**凍結版が0件**。
   パイプラインは凍結版しか読まないので、L層は実質パイプラインから不可視〔確〕。
8. `element_identity_links` 0件。候補を作る経路は **W層モーダルでの教員の手動対話のみ**で、
   パイプラインに同一性提案ステージは存在しない〔確〕。`duplicate_candidates` 列は常に `[]`〔確〕。
9. 同一論文の再解析は毎回新しい component UUID を作り、前 run と結ばれない。131行のうち
   **94行が既に存在しない document_id を指す孤児**〔確〕。
10. 一方 VA層のアンカーベクトルは既に材料を持っている — `large_scale_structure` /
    `galaxy_clusters` / `gravitational_waves` が2ドメインに**同じ node_id で重複**し cos 0.89〔確〕。
    「共通化の素材はあるが、束ねる器と経路が無い」が本調査の総括。

---

## ② 概念系統の地図

「名前を持つ知識の単位」を保持している系統の全数。件数は 2026-09-12 の実 DB / 実 artifact。

| # | 系統 | 所在 | ①ID 体系 | ②語彙の出所 | ③版・凍結 | ④他系統との結合手段 | ⑤実データ件数 |
|---|---|---|---|---|---|---|---|
| 1 | **cartridge ontology** | `backend/cartridges/<id>/ontology.json` | `concept_types[].id`（`Theory` 等8）/ aliases は canonical 文字列 | 人間（JSON を書く） | git のみ（DB 版なし） | 文字列 needle の部分一致（`enrichment._concept_vocab`）・LLM プロンプトへの列挙 | 同梱は `particle_physics` 1本。concept_types 8 / aliases **2** / notation_patterns 2 |
| 2 | **atlas 骨格 region/concept** | `atlas_skeletons.content.atlas_skeleton.regions[].{id,concepts[].id}` | ドメイン内スラッグ（`large_scale_structure` 等）。**(domain_key, version) 内でのみ一意** | LLM 生成（`generated_by=model:gpt-5.4`）→ 教員 draft→freeze。一部は bundled YAML | draft 1 + 凍結版履歴（`version`） | `landscape_placements.node_id` / `learning_courses.data.topics[].atlas_node_id` / `atlas_anchor_*` が**文字列で参照**（FK なし） | 8行（frozen 7 + draft 1）。live 3ドメインで region 24 / concept 133、全行合計 372 ノード |
| 3 | **atlas アンカーベクトル** | `atlas_anchor_embeddings` | `(domain_key, skeleton_version, node_id)` UNIQUE | 機械（骨格ラベル＋確定別名＋confirmed 配置引用の合成テキストを embed） | 版スコープ（`skeleton_version` 列） | cosine のみ（`atlas_vectors/query.py`）。**同一ドメイン内でしか比較しない** | 157（astro 59 / mg 49 / pp 49） |
| 4 | **atlas 別名レジストリ** | `atlas_anchor_aliases` | `(domain_key, node_id, normalized_alias)` UNIQUE・**版非依存** | 人間（教員が gap レビューから確定） | 無し（status 遷移のみ） | `normalize_label` 一致 → concept_normalizer の `teacher_alias` 供給源 / keyphrase 供給 | **0** |
| 5 | **共通部品ライブラリ（L層）** | `library_entries` / `library_entry_versions` | UUID。`name` + `aliases[]` | 人間（昇格操作）+ カートリッジ `library/*.json` シード | draft 正本 + 凍結版履歴 | `element_identity_links.shared_part_id`（FK）。パイプラインは**凍結版の embedding 検索のみ** | entries **3**（全部シード・`source_document_ids` 空）/ versions **0** |
| 6 | **同一性リンク** | `element_identity_links` | UUID。instance 4列 + shared_part_id | 人間（W層対話 → commit） | 無し（candidate/confirmed/rejected） | instance 側は `(element_type, element_id, document_id)` 文字列、shared 側は FK | **0** |
| 7 | **theory_components** | `theory_components` | UUID（DB）+ agent 側 `comp_001` / `comp_002__op1`（`source_scope.legacy_ids`）。**document ローカル** | LLM（component_assembly） | 無し（`status`/`review_status` 遷移のみ） | `document_id`（**FK なし**）・`course_id`（FK）。論文間は `name` 文字列のみ | **131**（うち94行は存在しない document_id を指す孤児） |
| 8 | **symbol_registry の記号** | `stage_outputs._artifacts.symbol_registry.records`（DB テーブル無し） | `sym_{document_id}_{symbol}` — **論文 ID を焼き込んだ ID**。原理的に論文横断不可 | 非LLM（決定論抽出） | run スコープ（artifact） | `used_in_equation_ids` / `defining_equation_ids` のみ。概念系統へは繋がらない | A 238 / B 119 |
| 9 | **dsl_linking ノード** | `_artifacts.dsl_linking.nodes` | `n1`…`n22` — **document ローカルの連番** | LLM | run スコープ | `linked_dsl_node_ids` で component から参照。`ontology_type` は **全件 null** | A 16 / B 22 |
| 10 | **course concepts** | `learning_courses.data.concepts[]` / `topics[].introduced_concepts` | **ID なし**（`name` + `children[]` の自由文） | LLM（course_mapping / course builder） | コースの freeze（V層）に同乗 | 無し（atlas への binding は `topics[].atlas_node_id` 側のみ） | course A 6 / course B 5（子を含め A 24 / B 19） |
| （参考） | **schema_ontology_types / schema_predicates** | DB | `OntologyType` 名 | 人間（`core/schema.py` 定数のシード） | 無し | **どこからも使われていない**（`chunks.smiles_dsl` 215行中 0行が非NULL） | 10 / 9 |
| （参考） | **chunks.variables / smiles_dsl** | `chunks` | — | — | — | — | **0 / 215**（全件 NULL） |

補足〔確〕:
- 4 セットの型語彙の実物 —
  ① `ontology.json concept_types`（8）: Theory / Approximation / Observable / CorrectionTerm /
     UncertaintySource / Operator / Parameter / DecayProcess
  ② `component_types.json`（14）: Domain{Concept,Theory,Method,Assumption,Observable}Component /
     Paper{Claim,Hypothesis,Relation,Correction,Uncertainty,Evidence}Component / apparatus /
     instrument / part
  ③ DB CHECK `theory_components_component_type`（9）: theory / concept / law / mechanism /
     operator / observation / apparatus / instrument / part
  ④ `dsl_linking/schema.py NODE_TYPES`（16）: TheoreticalFramework / Approximation / Relation /
     EquationRelation / Observable / Parameter / UncertaintySource / CorrectionSource /
     CorrectionTerm / Method / Experiment / Constraint / Diagnostic / Result / ClaimProxy /
     ThesisProxy
  ⑤ さらに `core/schema.py OntologyType`（11）が5つ目として存在するが未使用。
- 述語語彙も2セットに分裂: `core/schema.py CorePredicate` 9値に対し
  `dsl_linking/schema.py CORE_PREDICATES` は **PRODUCES を足した10値**。実データでも
  PRODUCES が A 1本 / B 3本出ている〔確〕。CLAUDE.md 開発ルール3の記載（9値）と実装が食い違う。

---

## ③ 実データでの同一概念の分裂

### E-1 「アクシオン」はどこにも概念として存在しない〔確〕

論文Bの主題である axion が、概念系統のどこにも **名前つきの単位として現れない**:

| 系統 | 論文Bでの現れ方 |
|---|---|
| theory_components（14件） | `Ideal resonance assumptions` / `Sideband resonance response` / `Final DANCE reach` 等。axion を名前に持つ部品は **0件** |
| component `concepts` | `e, t, l, c, r, a, n, i, x, z, sideband, cavity, polarization, k, π, snr, Standard Model` の17語のみ。axion **なし** |
| symbol_registry（119件） | `H_a`, `m_a`, `P_a` 等の記号はあるが `kind` は全件 `unknown`。「アクシオン場」という概念名には結びつかない |
| dsl_linking nodes | `n3 Observable: polarization rotation angle delta_phi_a(t)` 等の自然文。`node_value` は文であって概念参照ではない |
| atlas 骨格 | `astrophysics` の **旧版 20260721**（教員生成）に `axion_dm_theory` region があるが、これは 2026-08-05 の bundled 宇宙物理骨格 2026.1 に **created_at DESC で上書き**され live から外れている（`atlas_store.load_frozen_skeleton` は `ORDER BY created_at DESC` の1行）〔確〕 |
| landscape_placements | 論文Bの配置は **0件**（そもそも run に `landscape_placement` ステージが無い。下記 ④） |
| course concepts | `理想共振条件` / `側帯波と共振器応答` 等5群。axion なし |

→ 「同じ概念が別名で分裂」以前に、**この論文の中心概念が概念として立っていない**。

### E-2 論文A∩Bの「共通概念」は8個の1文字記号＝全部偽の一致〔確〕

`claim_object_builder.claims[].concepts` を集約すると（A 386 mention/171 distinct、
B 224/98）、両論文に共通する文字列は次の8個だけ:

```
R  \lambda  \phi  \theta  a  i  k  t      （すべて concept_type="symbol"）
```

意味は完全に別物: 論文Aの `R` は平滑化スケール（`\delta_{gs}(\bm{x};R)`）、論文Bの `R` は
鏡の反射率。`a` は A では摂動次数ラベル `s^{(a)}`、B では添字。**現在システムが計算できる唯一の
論文横断の概念一致は、この偽の一致である**。

### E-3 cartridge alias の部分一致による誤正規化: 「Standard Model」〔確〕

`src/episteme_graph/agents/component_assembly/enrichment.py` の
`_concept_vocab()` が `{canonical: [小文字の needle …]}` を作り、`_fill_concepts()` が
**`needle in body` の素朴な部分一致**で canonical を付与する（L336-339）。
`particle_physics/ontology.json` の alias は
`{"canonical": "Standard Model", "aliases": ["SM", "standard model"]}` の1件なので
needle に `"sm"` が入る。結果:

- 論文B `comp_010 "Resonance mismatch limitation"` の本文 `"…mirror phase **mism**atch shifts s/p resonance…"` が
  `"sm"` にヒット → `concepts` に **`"Standard Model"`** が入り、
  `introduced_concepts: ["Standard Model"]` として**この論文が標準模型を導入した**ことになっている〔確〕。
- 論文B `comp_011 "Nonideal sideband structure"` でも `reused_concepts` に `"Standard Model"`〔確〕。
- 論文A `comp_003 "Smoothed observable basis"` にも同様に混入〔確〕（DHOST 宇宙論の論文）。

つまり cartridge の唯一効いている正規化経路が、**2エントリの alias 表に対する
substring マッチで、両論文とも誤爆している**。

### E-4 1文字概念の氾濫が course topic まで届く〔確〕

`_concept_vocab()` の第2の供給源は claim の concepts（= 記号）で、`"e"`, `"t"`, `"l"`,
`"c"`, `"r"`, `"a"`, `"n"`, `"i"` が needle になる。英文本文には必ず含まれるので
**論文Bの14部品すべてに8文字全部が付く**（`concepts` 出現回数がちょうど14）。
これが `course_mapping` の topic へそのまま流れる:

```json
{"title": "Ideal resonance assumptions",
 "introduced_concepts": ["e","x","t","l","c","r","a","n","i","cavity"]}
```

学習者に見える「このトピックで新しく出てくる概念」がこれである〔確〕。

### E-5 同一記号の引数違いが別エントリに割れる〔確〕

symbol_registry は `canonical_symbol` を丸ごとキーにするため、引数の違いで分裂する。
括弧の前を base として集計すると:

| | 全エントリ | base | 2件以上に割れた base | そこに含まれる行 |
|---|---|---|---|---|
| 論文A | 238 | 164 | 37 | 111（46.6%） |
| 論文B | 119 | 105 | 6 | 20（16.8%） |

実例:
- 論文B `E_{L/R}` → `E_L/R`, `E_L/R(0,\cdot)`, `E_L/R(0,t)`, `E_L/R(l,t)`, `E_L/R(z,t)`,
  `E_L/R(z=0,t)`, `E_L/R(z=l,t)` の**7エントリ**
- 論文A `\kappa^{(a)}` → `κ^(0)`, `κ^(1)`, `κ^(2_I)`, `κ^(2_II)`, `κ^(3)`, `κ^(a)` の**6エントリ**
- 論文A `Z_2` → `Z_2`, `Z_2(\bm k_1,\bm k_2)`, `Z_2(\bm p,\bm q)`, `Z_2(k_1,k_2)`, `Z_2(k_3,k_4)`

また `Eq.(3.38)` / `Eq.(3.43)` が記号として登録されている〔確〕（式番号参照のノイズ）。
`notation_variants` が2件以上ある行は A 17 / B 9 しかなく、表記ゆれ吸収はほぼ効いていない。

### E-6 論文再解析で同じ概念が別 UUID に増殖〔確〕

`theory_components.name` を NFKC→casefold→英数かな以外除去で正規化すると:

- 131行 → 正規化後 distinct **101**
- 2件以上に重なるグループ 14、そこに属する行 **44**
- 14グループ**全部**が「複数 document_id にまたがる」

ただしその「複数 document」は別論文ではなく、**同じ論文の再解析 run が作った別 document 行**である。
名前一致から復元した論文ファミリ:

```
論文A系: 926ae5f0, de497c00, f5d9d629(現行)
論文B系: 07699cf5, 91a29fe6, 73460388(現行) / 4940dc03, 6cb80b07
```

具体例:
- `Ideal sideband symmetry` → `07699cf5`, `73460388`, `91a29fe6` の**3つの UUID**
- `Residual consistency relations` → `926ae5f0`, `de497c00`, `f5d9d629` の**3つの UUID**
- `Axion-photon coupling basis` / `Simultaneous resonance requirement` /
  `Sensitivity improvement comparison` → 各2つ

再解析の前後を結ぶリンクは無い（`element_identity_links` 0件、`duplicate_candidates` は
書き込み時に常に `[]`）。`theory_components.document_id` に FK が無いため、
**131行中94行（71.8%）が `documents` に存在しない document_id を指す孤児**として残っている〔確〕。

### E-7 機械生成ラベルが論文をまたいで衝突〔確〕

component_assembly は operation 分割時に `"Define: X"` / `"Derive: …"` /
`"Unknown specific operation: X"` / `"Application"` / `"Constraint"` / `"Definition"` /
`"Derivation"` / `"Equation System"` という**内容を持たないラベル**を作る。結果、
論文Aファミリと論文Bファミリが `Derivation`（3 doc）/ `Definition`（2 doc）で
名前一致してしまう〔確〕。さらに `Derive: P_{\rm L}(k), R, R^{-2a}` という
**LaTeX を含むラベル**が5行に重複している〔確〕。名前が同一性の唯一の手掛かりである以上、
このラベルは共通化の入力として使えない。

### E-8 atlas node_id が版ごとに総取り替え〔確〕

同一 domain_key の骨格版どうしのノード ID / ラベルの重なり:

| ドメイン | 版ペア | node_id の共通数 | label の共通数 |
|---|---|---|---|
| modified_gravity | 2026.0808 ↔ 2026.1 | **0**（49 vs 42） | **0** |
| modified_gravity | 2026.0808 ↔ 2026.7.8 | 18（49 vs 49） | 15 |
| modified_gravity | draft ↔ 2026.1 | 1（72 vs 42） | 0 |
| particle_physics | 2026.1 ↔ 2026.7.6 | **0**（9 vs 49） | 1 |
| astrophysics | 2026.1 ↔ 20260721 | **0**（59 vs 43） | 0 |

同じ「MOND の基礎」が版ごとに `mond_foundations` / `foundations_and_motivation` /
`phenomenology_and_motivation` / `modified_gravity_foundations` と名前を変えている〔確〕。
`landscape_placements` は `(domain_key, skeleton_version, node_id)` を保持するが、
`store.list_for_document` / `list_for_documents` は**現行版でのフィルタをしない**〔確〕ので、
再凍結すると確定済み配置は「現行骨格に存在しない node_id」として静かに落ちる〔推〕。
別名 / gap decision / edge decision は設計上あえて版非依存にしてあるのに
（`atlas_gaps` の `cluster_key`・`atlas_anchor_aliases`・`atlas_edge_decisions.edge_key`）、
**配置とアンカーベクトルだけが版スコープ**という非対称がある〔確〕。

### E-9 ドメイン名前空間の衝突〔確〕

`atlas_domain_meta` の `astrophysics` 行:

```
name = "光学リング共振器を用いたアクシオン暗黒物質探索の理論と計測"   （= 論文Bのタイトル）
created_at = 2026-07-21   created_by = 8f19bf16…
```

教員がコース起点で新分野を作ったときに `domain_key = astrophysics` を取った〔推〕。
その後 2026-08-05 に bundled の宇宙物理基準地図が**同じ domain_key** へシードされ
（`generated_by = bundled_import`、meta は仕様どおり上書きしない）、live 骨格だけが
宇宙物理10領域に差し替わった〔確〕。結果、UI 上のドメイン名は論文Bのタイトルのまま、
実体は汎用の宇宙物理地図という**名前と中身の乖離**が残っている。

### E-10 ドメインをまたぐ同一概念に結合手段が無い〔確〕

一方で座標系の側には既に重複がある。`atlas_anchor_embeddings` 157件で調べると:

- **node_id が2ドメインで完全一致**: `large_scale_structure`（大規模構造 / 大規模構造形成）、
  `galaxy_clusters`（銀河団）、`gravitational_waves`（重力波）
- cosine 上位: `astrophysics:large_scale_structure` ↔ `modified_gravity:large_scale_structure`
  = **0.896**、`galaxy_clusters` = 0.894、`gravitational_waves` = 0.888、
  `cosmic_expansion` ↔ `cosmological_background_expansion` = 0.850、
  `general_relativity` ↔ `general_relativity_limit` = 0.726、
  `modified_gravity:effective_field_theory_viewpoint` ↔ `particle_physics:effective_field_theory` = 0.630

にもかかわらず `atlas_anchor_aliases` は `(domain_key, node_id)` スコープで**ドメインをまたげず**、
`atlas_vectors/query.py` の `prefilter_domains` / `landing_for_vector` も
ドメインごとに独立して上位を返すだけ〔確〕。SKOS でいう `exactMatch` / `closeMatch` に
あたる構造がどこにも無い。

---

## ④ cartridge の適用ミスマッチ（定量）

論文A（宇宙論）・論文B（光学実験）とも `cartridge_id = "particle_physics"` で処理された〔確〕。
カートリッジが実際にどれだけ効いたか:

| 指標 | 論文A | 論文B | 出典 |
|---|---|---|---|
| cartridge `concept_types`（8型）が付いた概念 | **0** | **0** | claim concepts は全件 `concept_type="symbol"` |
| symbol_registry の `kind` が `unknown` 以外 | 0 / 238 | 0 / 119 | `Counter(kind)` = `{'unknown': N}` |
| dsl_linking ノードに `ontology_type` | 0 / 16 | 0 / 22 | キー自体が出力に無い |
| cartridge alias（2件）のヒット | 1件（`Standard Model`・**誤爆**） | 2件（同・**誤爆**） | E-3 |
| notation_patterns（`R_…` / `C_…`、2件）のヒット | 0 | 0 | 該当記号が出ない分野 |
| `component_type_text` がカートリッジ語彙内 | 3 / 21 | 8 / 14 | 下表 |
| landscape_placement で particle_physics に配置 | **0**（`unplaced_domains` に理由付きで申告） | — | run artifact |

`component_type_text` の分布（131行全体）と `component_types.json`（14語彙）との照合〔確〕:

| 値 | 件数 | カートリッジ語彙内か |
|---|---|---|
| MethodComponent | 24 | × |
| PaperRelationComponent | 20 | ○ |
| CorrectionComponent | 14 | × |
| RelationComponent | 13 | × |
| DiagnosticComponent | 12 | × |
| TheoryComponent | 8 | × |
| PaperClaimComponent | 8 | ○ |
| UncertaintySource… | 5 | × |
| DomainObservableComponent | 5 | ○ |
| DomainAssumptionComponent | 4 | ○ |
| AssumptionComponent | 3 | × |
| PaperCorrectionComponent | 3 | ○ |
| PaperUncertaintyComponent | 3 | ○ |
| DomainConceptComponent | 3 | ○ |
| DomainMethodComponent | 2 | ○ |
| （空文字） | 2 | × |
| ClaimBundleComponent | 1 | × |
| DomainTheoryComponent | 1 | ○ |

→ **語彙内 49 / 131（37.4%）、語彙外 82 / 131（62.6%）**。うち語彙外の多くは
「カートリッジ語彙の `Paper`/`Domain` 接頭辞を落とした自称」（`RelationComponent` /
`MethodComponent` / `CorrectionComponent`）で、**validator が弾いていない**〔確〕。
その上で DB 側の CHECK は全く別の9語彙なので、永続化時に**全件 `"theory"` に潰される**〔確〕。

`landscape_placement` は逆に正しく振る舞っている: 論文Aについて3ドメインを照合し、
particle_physics には「標準模型・量子場理論・フレーバー・崩壊などの粒子物理の対象は素材に
現れない」と**理由付きで配置しない**（`unplaced_domains`）〔確〕。
つまり **atlas 骨格（分野の地図）は分野違いを検出できるが、cartridge ontology は検出できない**。

### 役割重複の所見〔推〕

いま3つの層が「分野の語彙」を名乗っている:

| 層 | 粒度 | 版管理 | 分野違いの検出 | 実運用の状況 |
|---|---|---|---|---|
| cartridge `ontology.json` | 型8 + 別名2 | git のみ | できない（部分一致で誤爆） | 1分野・別名2件で実質未整備 |
| atlas 骨格 | 領域24 + 概念133（live） | draft→freeze・履歴あり | できる（`unplaced_domains`） | 3ドメイン・157アンカーが稼働 |
| L層 `library_entries` | 部品3 | draft→freeze | — | 凍結版0で不稼働 |

cartridge が担っていた「概念型・別名・記法」のうち、**別名は VA層 `atlas_anchor_aliases` が、
概念の存在は atlas 骨格が引き取れる位置にある**。cartridge に残る固有の価値は
`validation_rules.json` / `extraction_hints` / prompt 断片で、これは語彙レジストリとは別の関心事。

---

## ⑤ 論文横断の結合が育っていない原因

観測: `element_identity_links` 0 / `library_entry_versions` 0 / `atlas_anchor_aliases` 0 /
`atlas_gap_decisions` 0 / `atlas_edge_decisions` 0 / `landscape_placements` 22（論文Aのみ）/
`landscape_gap_signals` 1。なお配置22件のうち **live（`inferred`）は8件だけ**で、残り14件は
再解析で `superseded` になった履歴（astro: inferred 4 / superseded 6、mg: inferred 4 / superseded 8）〔確〕。
**教員が `confirmed` にした配置は1件も無い**。原因を経路ごとに切り分ける。

**(a) 共通部品（L層）— パイプラインから見えない**〔確〕
`core/library/seed.py` はカートリッジ同梱 JSON を `store.create_entry` で **draft として**取り込むだけで、
freeze しない。よって `library_entry_versions` = 0。パイプライン側の retrieval
`search.search_frozen_entries` / `_embedding_search` は `FROM library_entry_versions` を引くので、
**3件のエントリが存在しても検索結果は常に空**。`apparatus_semantics` の
`match_status` は構造的に `novel`/`unknown` にしかならない。加えて今回の2論文は
`analyze_images=false`（B）/ 未指定（A）なので apparatus 経路自体走っていない。

**(b) 同一性リンク — 候補を作る自動経路が存在しない**〔確〕
`element_identity_links` に行を作れるのは `identity_links.create_candidate` だけで、呼び出し元は
`core/deliberation/annotations.py::_commit_identity`（W層の注釈 commit）と
`POST /identity-links`（教員の直接操作）の2つ。`orchestrator._PIPELINE_STEPS` の29ステージに
同一性提案は無い〔確〕。しかも commit には `body.shared_part_id` に**実在する `library_entries.id`** が
必要で、(a) により候補となる部品が実質存在しない。→ 依存が循環している
（部品を作るには昇格操作が要る／昇格候補を出すには対話が要る／対話で結ぶ相手は部品）。

**(c) 標準化判定 — 入力が空**〔確〕
`POST /shared-parts/{id}/standardization/assess` は shared_part（= library_entries）専用。
3件の entries は `standardization_status='unknown'` のまま。三角測量の②「L層凍結版類似」も
③「コーパス内反復」も、母集団が3件（かつ全部シード）では意味を持たない。

**(d) 配置（landscape）— 後発ステージが遡らない**〔確〕
論文Bの run（2026-07-21）の `stage_outputs` に `landscape_placement` キーが無い。
このステージは migration 065（2026-08-04）で追加されたため、**それ以前に解析された教材は
配置候補を一度も生成していない**。バックフィル経路は「教員が再解析するか、
landscape モーダルで [AIで再提案] を押す」のみ。同じ理由で論文Bには `discuss_opening` も無い。

**(e) 別名レジストリ — 上流のギャップ候補が出ない**〔確〕
`atlas_anchor_aliases` への登録は「gap レビューキューのカードから教員が『別名として登録』」が
主経路。キューに出るには `landscape_gap_signals` が **distinct 2 document 以上**でクラスタ化する
必要がある（`MIN_DOCUMENTS_FOR_CANDIDATE`）。現状の signals は **1件**（論文Aの「摂動統計解析」）
なので候補は1つも浮上せず、別名登録の入口が開かない。コーパス2本では構造的に到達不能。

**(f) キーフレーズ供給・コーパス補完 — 承認済み部品が0**〔確〕
`paper_discovery/vocab.py::APPROVED_REVIEW_STATUSES = ("teacher_approved","teacher_reviewed","endorsed")`
に対し、DB の `theory_components.review_status` は
`teacher_review_required` 95 / `review_required` 29 / `source_backed` 7 の3値しか無く、
**承認済みは0件**。`component` 供給源は空を返す。`_cartridge_phrases(domain_key)` も
`astrophysics` / `modified_gravity` にカートリッジが無いため空。結局 keyphrase は
`skeleton` 供給源のみで回っている。同じ理由で「コーパスを補う論文」レンズB
（承認済み claim / 人間確定 assumption）も空になる。

**判定**〔推〕: 教員操作待ちの設計であることは意図どおりだが、本件で効いているのはそれ以前の
**構造的な不到達**である — ①候補を作るパイプライン経路が無い（同一性）②候補の母集団が
仕様上の閾値に届かない（gap→別名は2論文必要）③シードが凍結されず読み手に届かない（L層）
④後発ステージが既存教材に遡らない（配置）⑤承認語彙が実際に使われていない（供給源）。
UI 導線の問題ではない（導線はどれも実装済み）。

---

## ⑥ 発見

### K-1 概念型の語彙が5セットに分裂し、DB は全部を1語に潰している
→ **2026-09-13 一部解消**（本文 §4 Phase 1 実装記録: P1-4 — 型語彙の正本を `core/schema.py` に置き DB CHECK を語彙表 FK へ。`PRODUCES` の分裂も解消）
→ **2026-09-13 concept 層の受け皿も解消**（Phase 3 P3-1: `library_entries.entry_type` を語彙表 `knowledge_entry_types` FK に拡張し `LIBRARY_ENTRY_TYPES`（concept / theory / method / observable / assumption / quantity / process）を正本化。既存型語彙 → entry_type の決定論写像は `core/library/schema.py::entry_type_for_component_type`。正本 `docs/features/concept_registry_design.md` §4.1）
**証拠**〔確〕: ① cartridge `concept_types` 8 / ② `component_types.json` 14 /
③ DB CHECK 9 / ④ `dsl_linking NODE_TYPES` 16 / ⑤ 未使用の `OntologyType` 11。
`theory_components.component_type` は131行**全件 "theory"**、実際の型は自由文
`component_type_text`（18値・62.6%が語彙外）。述語も `CorePredicate` 9 vs
`dsl_linking CORE_PREDICATES` 10（PRODUCES 追加）で分裂し、実データに PRODUCES が出ている。
**困りごと**: 「同じ種類の部品」を型で束ねられない。型で絞った横断検索・型ごとの提示・
型ベースの同一性ヒントが全部使えない。ドキュメント（CLAUDE.md 開発ルール3）も実装と食い違う。
**改善方向**: 型語彙の正本を1つに決め（`component_types.json` が最も粒度が適切）、DB CHECK は
その語彙をそのまま持つか、CHECK をやめて語彙検証をアプリ層（cartridge 由来）に寄せる。
`component_type_text` は「LLM の自称を落とさない」ための第2列として残す（原則3）。
**触れる不変条項**: 原則3（情報を落とさない — 現状の `"theory"` 潰しは既に違反気味）。
A層非改変（W1）に触れるので、agent 側の語彙変更ではなく**永続化層の写像**として直すのが安全。

### K-2 claim/component の「概念」が記号（LaTeX 断片）であり、分野概念が1つも無い
→ **2026-09-13 解消**（Phase 3 P3-5 で記号 → 概念の経路と「直前の定義」API。同日の追補 [claim_concept_grounding_design.md](../../features/claim_concept_grounding_design.md)（CG1〜CG7）で `claim.concepts` の concept 層を供給: 原因は「辞書が空」で、backend が合成した辞書（レジストリ confirmed ラベル + cartridge 別名 + DSL ノード名・記号除外・語境界一致）を A層 builder の既存 `concept_resolver` 注入口へ渡し、`dsl_linking` 直後のフックで DSL ノードの参照も写す。出所は `theory_claims.concepts` の各要素に additive。オーナー判断 CG-O1 = A層非改変の範囲内 / CG-O2 = LLM 抽出ステージは実測後。学習者の概念マップは記号を除く（`excluded_symbol_concepts` に保持））
**証拠**〔確〕: `claim_object_builder.claims[].concepts` 610 mention が**全件**
`concept_type="symbol"` / `role="subject"`。論文B component の `concepts` 17語のうち
14語が1文字または記号。`concept_assignment_status` は239 claim 全件 `review_required`。
**困りごと**: 概念レベルの索引が存在しないので、論文横断の一致判定が
「`R` と `R` は同じ」という記号一致にしかならない（K-3）。学習者に見える
`introduced_concepts` も記号列になる（E-4）。
**改善方向**: `concepts` に少なくとも2階層（`symbol` と `concept`）を要求し、
`concept` 層は「本文に現れる名詞句 + 出典 span」で埋める。既に
`dsl_linking.nodes[].node_value` が概念に近い自然文を持っている（`DHOST modified-gravity
perturbation framework` 等）ので、これを名前化して concept 層の一次供給源にできる〔推〕。
**触れる不変条項**: evidence-based（全出力に reason/confidence）・LLM 出力は candidate 止まり。
概念名の確定を人間に残す点は現状と同じでよい。

### K-3 論文横断の唯一の一致が、2件の alias 表に対する素朴な部分一致で誤爆している
**証拠**〔確〕: `enrichment._fill_concepts` の `needle in body`。
`"mismatch"` → `"sm"` → `"Standard Model"` がアクシオン共振器論文の
`comp_010.introduced_concepts` に入る。論文Aの `comp_003` にも混入。
論文A∩Bの共通概念は `R λ φ θ a i k t` の8記号で全部偽陽性。
**困りごと**: 共通化の基盤が「偽の一致を生む仕組み」になっている。ここに同一性リンクや
ベクトル検索を積むと誤りが増幅する。
**改善方向**: (i) 部分一致を語境界つき一致 or 正規化キー完全一致に変える（`concept_normalizer.normalize_key`
が既にある）、(ii) needle の最小長を設ける（`paper_discovery/vocab.py` は既に
`_MIN_PHRASE_LENGTH = 2` で1文字を落としている — 同じ規律を enrichment にも）、
(iii) 1文字・記号は `symbol` として別スロットに置き `concepts` に混ぜない。
**触れる不変条項**: 原則8（出所を混ぜない）— いまは alias 由来と claim 由来が同じ
`concepts` 配列に同居して区別が付かない。`concept_normalizer` が既に持つ
`normalization_source` を component 側にも持たせるのが筋。

### K-4 共通部品レジストリ（L層）がパイプラインから構造的に不可視
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-7 — `library/seed.py` が取込直後に初版を凍結し、版ゼロの `bundled_import` 行を起動時にバックフィル）
**証拠**〔確〕: `library_entries` 3 / `library_entry_versions` **0**。
`seed.py` は draft 作成のみで freeze しない。`search._embedding_search` /
`search_frozen_entries` は `library_entry_versions` を引く。
**困りごと**: 「共通部品」という共通化の器が空回りしている。同一性リンクも標準化判定も
この器を前提にしているので、下流の全機能が起動しない。
**改善方向**: シード取込時に初版を freeze する（冪等に `version_no=1` を作る）か、
retrieval を「凍結版があればそれ、無ければ draft を `unfrozen` ラベル付きで」に変える。
前者のほうが「パイプラインが読むのは凍結版のみ」の原則を壊さない〔推〕。
**触れる不変条項**: 「パイプラインが読むのは凍結版のみ」（L層設計 §6）・行削除禁止（P4）。
どちらも freeze 追加なら守れる。

### K-5 同一性リンクの候補をパイプラインが1つも作らない（人間の確定以前の問題）
→ **2026-09-13 解消**（Phase 3 P3-6: パイプライン末尾のステージ `identity_candidates`（`core/library/identity_candidates.py`・非LLM・embedding 0 回）が正規化名一致 / chunk-proxy 近傍から candidate entry + `element_identity_links` candidate を作り、`duplicate_candidates` を埋める。確定は教員のまま。正本 `concept_registry_design.md` §6.2）
**証拠**〔確〕: `_PIPELINE_STEPS` 29ステージに同一性提案なし。`create_candidate` の
呼び出し元は W層 commit と手動 API の2つ。`theory_components.duplicate_candidates` は
`persistence.py` で**常に `[]` を書く**（L771 / L2075）。
**困りごと**: KN-3「同一視は人間が確定する」は守られているが、**人間に見せる候補が生成されない**ため
確定のしようがない。`duplicate_candidates` という受け皿が既にあるのに死んでいる。
**改善方向**: 決定論的な候補生成なら不変条項に触れない — 例えば
(i) 正規化名の完全一致（同一論文の再解析ペアがまず拾える、E-6）、
(ii) `chunks` と同じ embedding での component summary 近傍、
(iii) 確定済み配置 node_id の共起。いずれも `status='candidate'` で
`duplicate_candidates` / `element_identity_links` に置くだけ。
**触れる不変条項**: KN-3（確定は人間）・W2（AI は候補のみ）・KN-2（正規化は追加であって置換でない）
— 候補生成に留める限り全部守れる。LS7 / AB4（AI が骨格へ書かない）にも触れない。

### K-6 atlas node_id が版ごとに総取り替えで、配置・ベクトルだけが版に縛られている
→ **2026-09-13 解消**（追補 [atlas_node_correspondence_design.md](../../features/atlas_node_correspondence_design.md)・NC1〜NC8・migration なし。格納庫は骨格の既存スロット `id_migrations`。凍結前の `freeze-impact` に決定論の候補（正規化ラベル一致 / 教員確定別名 / レジストリ経由）を並べ、教員が確認した対応だけを凍結 body `id_migrations` で載せる（`decision_context` 記帳・チェック既定オフ）。読み手（landscape 配置・学習者オーバーレイ・論文の海・レジストリ node リンク）は全凍結版の連鎖を辿る `NodeResolver` で読み替えるだけで `node_id` を UPDATE しない。対応の無い配置は旧版の行を残し現行版では事実文。オーナー判断 NC-O1〜O3 は推奨案を採用。ベクトルの版間継承は非スコープ）
**証拠**〔確〕: modified_gravity 2026.0808 ↔ 2026.1 の node_id 重なり **0**。
particle_physics / astrophysics も版間重なり 0。一方
`atlas_anchor_aliases` / `atlas_gap_decisions.cluster_key` / `atlas_edge_decisions.edge_key` は
**版非依存**に設計されているのに、`landscape_placements` と `atlas_anchor_embeddings` は
`skeleton_version` スコープ。`landscape/store.py` の読み出しは現行版フィルタを持たない。
**困りごと**: 骨格を育てるほど過去の教員判断（confirmed 配置）が切れる。
「地図を直すと論文の位置づけが消える」ので、骨格の改訂が抑止される〔推〕。
**改善方向**: (i) 骨格生成・再凍結時に前版との node 対応表（`supersedes` / `same_as`）を
決定論 + 教員確認で持つ、(ii) あるいは placement 側を
`cluster_key` 方式（正規化ラベル由来の版非依存キー）へ寄せる。
既に `atlas_gaps.build_cluster_key` が「版非依存キー」の前例（§4.2 裁定）なので、
同じ思想を配置へ広げるのが一貫する。
**触れる不変条項**: LS3（再解析セマンティクス: confirmed / rejected を AI が覆さない）・
AB3（凍結版は読み取り専用）・LS7（AI が `atlas_skeletons` に書かない）。対応表を
「教員が凍結時に確認する差分」として扱えば AB4 に触れない（`freeze-impact` の拡張で足りる〔推〕）。

### K-7 ドメインをまたぐ同一概念に結合手段が無い（素材はもう揃っている）
→ **2026-09-13 解消**（Phase 3 P3-4: `library_atlas_node_links`（版非依存 `link_key`・exact_match / close_match）でレジストリ entry をハブに別ドメインの node を結ぶ。候補導出 `atlas_links.derive_node_link_candidates` は保存済みアンカーベクトルと正規化ラベル一致だけ。node—node の直接辺（`atlas_edge_decisions` の拡張）ではなくハブ経由を採った。正本 `concept_registry_design.md` §4.5 / §6.1）
**証拠**〔確〕: `large_scale_structure` / `galaxy_clusters` / `gravitational_waves` が
astrophysics と modified_gravity に**同じ node_id・同じラベル**で重複。
cos はそれぞれ 0.896 / 0.894 / 0.888。`cosmic_expansion` ↔
`cosmological_background_expansion` = 0.850、`general_relativity` ↔
`general_relativity_limit` = 0.726。
`atlas_anchor_aliases` は `(domain_key, node_id, normalized_alias)` でドメイン内に閉じ、
`atlas_vectors/query.py` もドメインごとに独立にランクするだけ。
**困りごと**: 論文Aは astrophysics と modified_gravity の**両方**に別々に配置されており
（22件中 astro 10 / mg 12、live はそれぞれ4件）、学習者から見ると同じ「大規模構造」が2つの地図に別々に立つ。
分野をまたぐ転用（本調査の主題）が、まさにこの層で止まっている。
**改善方向**: ノード間リンクを「同一ドメイン内の辺」に限定している現行の
`atlas_edge_decisions`（RE7: concept–concept・NEAR のみ）を、**ドメイン間の
`exactMatch` / `closeMatch` に拡張**する。候補導出は既存の `derive`（cosine + 配置共起）が
そのまま使え、`edge_key` が既に版非依存なのでドメイン対にも自然に広がる〔推〕。
**触れる不変条項**: RE1（地形は不変・主張は離散の辺）・RE2（推定は点線 + 未確認ラベル）・
RE6（候補は読み時導出・embedding API を呼ばない）。ドメイン間辺も「辺」である限り
原則①′（分野マップ表示原則）に整合する。ただし RE7 の「同一 region 内・region 端点は除外」の
規則はドメイン間では別に決め直しが要る。

### K-8 名前が唯一の同一性キーなのに、名前が機械生成ラベルと LaTeX で汚れている
**証拠**〔確〕: `Derive: P_{\rm L}(k), R, R^{-2a}`（5行に重複）/
`Unknown specific operation: Higher-order correction limit` /
`Application` / `Constraint` / `Definition` / `Derivation` / `Equation System`。
論文A 21部品のうち14部品が `comp_00X__opN` 形式の operation 分割で、名前が
`"<動詞>: <親の名前>"` になっている。
**困りごと**: 名前一致による同一性判定（K-5 の最も安価な候補生成）が、
機械ラベルの偶然一致で汚染される。教員の目視レビューでも見分けが付かない。
**改善方向**: 分割由来の部品は `name` を親から継承させず、`parent_component_id` +
`operation` の構造で表す（main/detail の2層分離が graph 側で既にやっていること）。
同一性判定の対象は「親（概念的な部品）」に限る。
**触れる不変条項**: #308 のラベル規律（main label は theory stage label そのもの・
説明は description へ）と同じ思想を component 名にも適用するだけなので、新たな違反は無い。

### K-9 再解析が概念を増殖させ、孤児を残す
→ **2026-09-13 解消**（本文 §4 Phase 1 実装記録: KO3 supersede 遷移 / KO9 `document_id` UUID + FK CASCADE）
**証拠**〔確〕: 131行中 **94行（71.8%）**が `documents` に存在しない document_id を指す。
`theory_components` に `document_id` の FK は無い（制約一覧に `course_id` / `created_by` /
`primary_chunk_id` のみ）。同一論文の再解析ペアが名前一致14グループ・44行。
**困りごと**: コーパスの「概念の数」が信用できない。K-5 の候補生成を素朴に回すと
孤児との重複が大量に湧く。`paper_discovery/vocab.py::_component_phrases` のような
コーパス語彙の供給も孤児を数える〔推〕。
**改善方向**: 再解析時に旧 component を `superseded` 相当の status へ遷移させる
（既に `landscape_placements` / `landscape_gap_signals` / `element_explanations` が
採っているパターン）。行は消さない。あわせて `document_id` の参照整合を
`_purge_document` 側で保証する。
**触れる不変条項**: P4（情報を落とさない）— 削除ではなく status 遷移にすれば守れる。
`theory_components.status` の CHECK は `candidate/draft/teacher_reviewed/rejected` の4値なので
語彙追加が要る（migration 1本）。

### K-10 cartridge・atlas 骨格・L層ライブラリが「分野の語彙」を三重に持っている
→ **2026-09-13 解消（役割分担の確定）**（Phase 3 O-4 = 概念名の正本は `library_entries`（レジストリ）・atlas 骨格は座標系・cartridge は `shape.json` による「形の宣言」（P3-7）。レジストリ ↔ 骨格は版非依存の `library_atlas_node_links`、cartridge 別名は `cartridge_declared` の正当化で流入。正本 `concept_registry_design.md` §3 / §8）
**証拠**〔確〕: ④節の役割重複表。cartridge は alias 2件・notation 2件で実質未整備、
しかも唯一効いている経路が誤爆（K-3）。atlas 骨格は 133 概念 + 157 ベクトルで稼働し、
分野違いの検出（`unplaced_domains`）もできる。L層は凍結0で不稼働（K-4）。
`concept_normalizer` は既に「教員別名（VA層）→ カートリッジ別名 → 文字列正規化」の
3段照合を実装済みで、**人間確定の別名を上位に置く**設計になっている〔確〕。
**困りごと**: 新分野を足すときに「カートリッジを作る / 骨格専用ドメインを足す / ライブラリに
エントリを積む」の3択があり、どれが概念名の正本か決まっていない。
**改善方向（設計案の素材）**: 下記⑦。
**触れる不変条項**: 原則8（出所を混ぜない）・KN-2（正規化は追加）・KN-3（確定は人間）。

### K-11 `concept_normalizer` の適用先が空で、正規化が事実上走っていない
→ **2026-09-13 前提解消・適用拡大は非スコープ**（Phase 1 KO4 で claim object 全件が `theory_claims` 行になり `normalize_concepts` の適用先が空でなくなった。component 側への適用拡大は Phase 3 でも行わず、代わりに正規化ラベル完全一致（`atlas_gaps.schema.normalize_label`）で同一性候補を決定論導出する — `concept_registry_design.md` §6）
**証拠**〔確〕: `normalize_concepts` の呼び出し元は `routes/theory_components.py` の2箇所
（claim の永続化時）のみ。`theory_claims` 28行の `concepts` は **全件 `[]`**。
一方 artifact 側の claim（A 132 / B 107）には concepts があり、うち concepts 非空は
A 49 / B 35。つまり**概念を持つ claim が DB に届いていない**（CLAUDE.md 既知の
「claim_objects は永続化されない」ギャップと同根）。
**困りごと**: 唯一「教員確定別名を第2供給源として受ける」設計になっている経路が空回り。
VA層の別名を登録しても、claim の正規化には届かない。
**改善方向**: claim_objects の永続化（オーナー判断の別件）とセットで、
`normalize_concepts` の適用点を component 側へも広げる。
**触れる不変条項**: KN-2（`name` を書き換えず `canonical` を併記）は現行実装が既に守っている。

### K-12 SKOS 相当の語彙がどこにも揃っていない
→ **2026-09-13 解消**（Phase 3 P3-2 / P3-3: `knowledge_label_kinds`（preferred / alternate / hidden）・`knowledge_relation_kinds`（broader / related / exact_match / close_match）・`knowledge_mapping_justifications` の語彙表と `library_entry_labels` / `library_entry_relations` / `library_atlas_node_links` を migration 082 で新設。RDF 化はしない。正本 `concept_registry_design.md` §4）
**現状の対応表**〔確〕:

| SKOS 概念 | 今どこにあるか | 欠落 |
|---|---|---|
| `prefLabel` | atlas `concept.label` / `library_entries.name` / `theory_components.name` | 系統ごとに別々。正本が無い |
| `altLabel` | `atlas_anchor_aliases`（0件）/ `library_entries.aliases`（3件分）/ cartridge `ontology.aliases`（2件）| 3箇所に分散・相互参照なし |
| `broader` / `narrower` | atlas の region→concept（2階層固定）/ `atlas_edge_decisions.edge_kind='depends'` | 概念間の上位下位が無い。region は「入れ物」で概念ではない |
| `related` | `atlas_edge_decisions`（0件）/ W層 context_lens（読み時導出） | 永続化された関連が0 |
| `exactMatch`（横断） | **無し** | ドメイン間（K-7）・再解析間（K-9）・論文↔共通部品（`element_identity_links` 0件）すべて |
| `closeMatch` | **無し** | cosine は都度計算・保存しない |
| `inScheme` | `domain_key` | 概念がどの scheme に属すかは domain_key 1つ。複数 scheme 所属が表せない |
| `notation` | `symbol_registry.canonical_symbol` / `notation_variants` | 概念と記号が結ばれていない（K-2） |
| `definition` | `theory_components.summary` / `library_entries.summary` / `element_explanations` | 3箇所 |
| `scopeNote` | `epistemic_ledger.verification_scopes` | 検証スコープであり語彙スコープではない |

**困りごと**: 共通化に必要な語彙のうち、実質稼働しているのは `prefLabel` と
`broader`（2階層固定）だけ。横断の `exactMatch` が無いことが④の全ての帰結の根。

---

## ⑦ 共通化の設計案の素材（判断材料）

### 軸の候補3つ

| 案 | 軸 | 長所 | 短所 | 不変条項との相性 |
|---|---|---|---|---|
| **A. atlas 骨格を軸にする** | `atlas_skeletons` の region/concept を概念レジストリの正本にする | 唯一稼働している（133概念・157ベクトル・draft→freeze・retire・凍結影響の事前提示・教員レビュー UI が全部ある）。分野違いを検出できる | node_id が版で変わる（K-6）。2階層固定で概念間の上下が無い。ドメイン跨ぎが表せない（K-7）。**AI が書けない**ので論文由来の概念が自動で増えない | LS7 / AB4「AI が `atlas_skeletons` に書かない」が最大の制約。増やすには必ず gap→教員→draft の弁を通す＝スループットが教員の時間で律速 |
| **B. `library_entries` を軸にする** | 共通部品を概念レジストリに拡張 | `name` + `aliases[]` + draft/freeze + `standardization_status` と、SKOS の prefLabel/altLabel/評価が最初から揃っている。`element_identity_links` で論文側と結ぶ FK も既にある。**ドメインに閉じない**（`domain_key` は属性であって座標系ではない） | 現在3件・凍結0で不稼働（K-4）。地図としての可視化が無い（学習者に見せる形が未設計） | 「確定は人間」「行削除なし」「candidate 始まり」を既に全部実装済み。AI 書き込み禁止の制約が atlas ほど強くない（`standardization_status` はガバナンス列として分離済み） |
| **C. 新設の概念レジストリ** | 10系統の上に第11の層 | 設計の自由度 | 11系統目を作るのは本調査の診断（分裂）を悪化させる | — |

### 〔推〕所見

**B を軸に、A を「その概念の置き場所（座標）」として残す**のが、既存の不変条項と
最も摩擦が少ない〔推〕。理由:

1. atlas 骨格は **座標系** であって語彙ではない（`label` はあっても `altLabel` も
   `exactMatch` も持てない設計で、しかも版で id が変わる）。実際
   `atlas_anchor_aliases` が版非依存に作られているのは、「語彙は版に縛られない」という
   認識が既に設計側にあることの表れ〔確〕。
2. `library_entries` は既に `(domain_key, name, entry_type)` で一意化され、
   `aliases[]` を持ち、`element_identity_links` という論文インスタンスへの FK を持つ。
   SKOS の `Concept` / `prefLabel` / `altLabel` / `exactMatch` に一対一で対応する。
3. `entry_type` を `apparatus` / `theory_component` の2値から拡張すれば
   （`concept` / `observable` / `method` …）、K-1 の型語彙統合の受け皿にもなる。
4. atlas 側とは `library_entries.id ↔ (domain_key, node_id)` の対応を**新しい版非依存の
   リンク表**で持てばよい。`atlas_edge_decisions` の `edge_key` が既にその形
   （版非依存・無向・候補→教員確定→凍結反映）の前例〔確〕。

### 各不変条項に照らした制約の整理〔確〕

| 不変条項 | 出典 | 概念レジストリ統合への含意 |
|---|---|---|
| 確定は人間（KN-3 / W2 / LS7 / AB4 / 原則1） | knowledge_network_vision §4、landscape LS7 | 概念の**生成・統合・改名は全部 candidate 始まり**。自動マージを作らない。`atlas_skeletons` への書き込み経路は増やせない（ガードレールで固定済み） |
| 正規化は追加であって置換でない（KN-2 / 原則7） | 同 §4、`concept_normalizer` docstring | 論文側の局所表現（`E_{L/R}(z,t)`）を書き換えない。共通部品側の `local_expressions` に出所付きで足す — この設計は**既に正しい**ので踏襲する |
| 数値を見せない（LS5 / W8 / PN-4 / 原則4改訂） | landscape / W層 / personal_graph | cosine・一致件数・重複数を出さない。段階ラベル（`label_vocab.GradedScale`）か名前の列挙のみ。VA層の `ANCHOR_NEARNESS_SCALE` / `ANCHOR_LANDING_SCALE` が既に2表ある |
| 行削除しない（P4） | 全層共通 | 概念の統合は `merged` / `superseded` の status 遷移。`atlas_gap_decisions` に既に `merged` 語彙がある（ただし merge 導線は v1 未実装） |
| 出所を混ぜない（原則8） | vision §6 | 概念の供給源ラベルを持つ。`concept_normalizer.SOURCE_*` 3値と `KEYPHRASE_SOURCES` 5値が既存の前例。統合レジストリでは最低
 `teacher` / `cartridge` / `skeleton` / `paper`（LLM 由来）を分ける |
| 閉世界の正直さ（SL1 / CR4 / PD6） | stakes_ledger / corpus_roaming | 「この概念は他の論文に無い」と言わない。言えるのは「このコーパスの中では…」だけ |
| 版非依存キーの前例 | `atlas_gaps.build_cluster_key`（§4.2 裁定）・`atlas_edge_decisions.edge_key`・`atlas_anchor_aliases` | **概念の同一性キーは版非依存にする**という判断は既に3回下されている。配置とベクトルだけが版スコープ（K-6）という非対称を解消する方向が一貫する |
| 分野中立（domain-independent） | CLAUDE.md TheoryOperationGraph 節 | レジストリのスキーマに分野語を入れない。`THEORY_STAGES` / `classify_operation` の作法を踏襲 |

### 着手順の素材〔推〕

不変条項に触れず、かつ互いに独立に効く順:

1. **K-3 の誤爆を止める**（`enrichment._fill_concepts` の部分一致を境界付き一致 + 最小長へ）。
   migration 不要・A層の1関数・既存テストで守れる。これをやらないと下流の共通化が汚染される。
2. **K-4 の freeze**（シード取込時に初版を凍結）。migration 不要。L層が動き出す。
3. **K-9 の supersede**（再解析時の旧 component status 遷移 + 孤児の掃除）。migration 1本。
   コーパスの件数が信用できるようになる。
4. **K-5 の候補生成**（決定論の重複候補を `duplicate_candidates` / `element_identity_links` に
   candidate で置く）。migration 不要（受け皿は既にある）。教員に初めて「確定すべきもの」が出る。
5. **K-7 のドメイン間 exactMatch**（`atlas_edge_decisions` の拡張 or 新表）。
   ここで初めて「分野をまたぐ転用」が構造として立つ。
6. **K-1 / K-2 の語彙統合**（型語彙の正本化 + concept 層の新設）。影響範囲が最大なので最後。

---

## 付録: 調査に使った主なコマンド・出典

- DB: `backend/.venv/bin/python` + sqlalchemy、`postgresql://episteme@localhost:5432/episteme`（読み取りのみ）
- 件数: `theory_components` 131 / `theory_claims` 28 / `theory_component_graphs` 13 /
  `chunks` 215 / `documents` 11（実論文2 + テスト9）/ `learning_courses` 2 /
  `atlas_skeletons` 8 / `atlas_anchor_embeddings` 157 / `atlas_anchor_aliases` 0 /
  `library_entries` 3 / `library_entry_versions` 0 / `element_identity_links` 0 /
  `element_annotations` 0 / `component_explanations` 0 / `landscape_placements` 22 /
  `landscape_gap_signals` 1 / `atlas_gap_decisions` 0 / `atlas_edge_decisions` 0 /
  `schema_ontology_types` 10 / `schema_predicates` 9
- artifact: `realdata/*_run.json` → `run.stage_outputs._artifacts`。
  論文A: symbol 238 / equation 53 / evidence 101 / derivation 11 / dsl node 16 edge 18 /
  component 21 / claim_object 132 / course topic 21。
  論文B: symbol 119 / equation 64 / evidence 207 / derivation 5 / dsl node 22 edge 18 /
  component 14 / claim_object 107 / course topic 14。
- コード: `src/episteme_graph/agents/component_assembly/enrichment.py`（L313-348, L451-470）/
  `dsl_linking/schema.py`（L10-62）/ `component_graph/schema.py`（THEORY_STAGES）/
  `backend/core/concept_normalizer.py` / `atlas_gaps/schema.py::normalize_label`（L151）/
  `atlas_vectors/schema.py::build_anchor_source_text` / `atlas_store.load_frozen_skeleton`（L71-92）/
  `core/library/{seed,search,schema}.py` / `core/deliberation/identity_links.py` /
  `core/deliberation/annotations.py::_commit_identity`（L344-385）/
  `core/paper_discovery/vocab.py`（L38-48）/ `core/landscape/store.py`（L290-340）/
  `core/document_pipeline/persistence.py`（L771, L2075）/ `core/document_pipeline/orchestrator.py`（`_PIPELINE_STEPS`）
- 設計書: `docs/features/knowledge_network_vision.md` §4（KN-1〜4）/
  `knowledge_landscape_design.md`（LS1〜10）/ `atlas_vector_anchoring_design.md`（VA1〜9）/
  `category_gap_candidates_design.md`（§4.2 版非依存 cluster_key）/
  `image_pipeline_knowledge_library_design.md`（L層）/
  `element_deliberation_workspace_design.md`（W-β / Phase S）/
  `atlas_relation_edges_design.md`（RE1〜8）/ `docs/pipeline/cartridges.md`
