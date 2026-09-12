# 調査A: 原本論文 ⇄ パイプライン構造化成果の忠実度・網羅性レビュー

> **状態: 調査記録（完了）**（2026-09-12、Opus 5 サブタスク。本調査以降の変更は未評価。親文書は
> [知識構造の見直し提案](../knowledge_structure_review_2026-09-12.md)）

対象: 論文A = arXiv 2407.01221v2（DHOST 重力理論・歪度/尖度 consistency relation、TeX 20頁相当）/ 論文B = fujimoto_d.pdf（博士論文 190頁・光リング共振器によるアクシオン暗黒物質探索 DANCE）。どちらも cartridge `particle_physics` で処理。

**記法**: 〔確認〕= 現物 JSON / 原本 / コードで直接確認した事実。〔推測〕= そこから導いた解釈。

## ① 要約

1. **RAG 層（chunks）は両論文とも全編を覆うが、構造化層（claim / component / graph）は一部しか覆わない。** 論文Bでは構造化成果の 100% が第3章由来。
2. 原因は単一で機械的: `rhetorical_role/input_builder.py` の `_MAX_BLOCKS = 64` による打ち切り。論文Bは 936 ブロック中 64（6.8%）しか役割判定されない。
3. しかも選抜順が `(page, order)` ソートで、GROBID が 212 ブロックに `page=1` を誤付与しているため、**「どの64ブロックか」は内容の重要度と無関係な事故で決まっている**。
4. `theory_claims` に永続化されるのは atomic claim ではなく「too_broad と自己判定された段落まるごと」。論文A 9件は 100% `granularity="too_broad"`、132件の atomic claim は DB に落ちない。
5. claim_type は 84%（201/239）が DB の CHECK 語彙外のため全行 `diagnostic_claim` に潰れ、component_type も全行 `theory` に潰れる。**型による横断検索・転用が構造的に不可能**。
6. 永続 claim の legacy_id が全件 `claim_span_001` で衝突。claim 参照 ID は3方式が併存し、14–15件は解決不能。
7. component 名の 16/21（論文A）は "Transform representation: …" 等の機械生成文字列で、学習単位（course topic）の題名がそのまま劣化する。
8. cartridge 由来の誤注入を実測: 原本に "Standard Model" が **0回**の論文Aで、グラフ 28ノード中 25ノードが `concepts` と `prerequisite_concepts` に "Standard Model" を持つ（alias "SM" の部分文字列一致が "cosmological" 等に当たる）。
9. 表・図の欠落が致命的。論文A は表4・図3が **全て0件**（TeX パーサが tabular/figure を落とす）。論文B は図86件を拾うが `interpretation` は全件空。
10. 意味が最も忠実に残っているのは **DSL 層と thesis/skeleton/narrative 層**で、学習者に出る component/graph 層ではない。**可読な意味は既に作られているのに、保存される「単位」の側で捨てられている**。

## ② 論文A（arXiv 2407.01221）照合表

### A-1. 原本の節と成果の対応

| 原本の節 | 本文段落 | 式block | 役割判定 | 採択claim | 保留 | 却下 | evidence | 成果としての表現 |
|---|---|---|---|---|---|---|---|---|
| Abstract | 2 | 0 | 2 | 1 | 0 | 1 | 5 | claim 1件（段落まるごと） |
| 1 Introduction | 6 | 0 | 6 | 3 | 1 | 2 | 14 | claim 3件 |
| 2 Matter density fluctuation | 1 | 0 | 1 | 0 | 0 | 1 | 0 | — |
| 2.1 Evolution of matter density | 8 | 7 | 8 | **0** | 4 | 4 | 8 | claim 0。式は保持 |
| 2.2 Bias model | 17 | 11 | 17 | **0** | 6 | 12 | 12 | **claim 0**。手法の核が claim 化されない |
| 3 Skewness and kurtosis CR | 1 | 0 | 1 | 0 | 0 | 1 | 0 | — |
| 3.1 Skewness/kurtosis parameters | 17 | 13 | 17 | 2 | 6 | 9 | 34 | claim 2件 |
| 3.2 Consistency relations | 11 | 6 | 11 | 2 | 2 | 7 | 18 | claim 2件（主結果を含む） |
| 4 Conclusion | 3 | 0 | **1** | 1 | 0 | 0 | 10 | 3段落中1段落のみ役割判定 |
| Appendix | — | — | — | — | — | — | — | 原本自体が空 |

### A-2. 構成要素ごとの捕捉/欠落

| 原本の要素 | 成果での扱い | 判定 |
|---|---|---|
| 中心的問い・主張 | `thesis_reconstruction.central_question` / `central_thesis` が正確 | ◎ |
| 仮定（local bias, tree-level, 速度項無視, 平滑化スケール） | `support_structure.assumptions` に4件 | ○（claim 化はゼロ） |
| カーネル F_2, F_3 | eq:F2 は record 有・**eq:F3 は label 付き record なし** | △ |
| バイアスカーネル Z_2, Z_3 | eq:Z2 のみ。**eq:Z3 欠落** | △ |
| 歪度/尖度の定義 S_g, K_g | **3件とも欠落** | ✗ |
| **主結果 = 2本の尖度 consistency relation** | kurtosis-consistency-1/-2 に **label 付き equation record なし** | ✗（最重要） |
| 数値係数表（Table 1–4） | `tables = 0`。"tabular" 0回・"14.55" 0回 | ✗ 全欠落 |
| 図3個 | `figures = 0`（`figure_image_extraction.reason = "not_pdf"`） | ✗ 全欠落 |
| 定量的予測（κ, λ 制約） | tex_b110 が64打ち切りで対象外 → claim なし | ✗ |
| 引用・相互参照 | 本文から削除。本文は "derived in ." と破綻 | △ |
| インライン数式 | `$\beta_2,\beta_{K^2}$` → `"_2,_K^2"`（41/113 ブロック） | ✗ 破損 |

**欠落ラベルの証拠**〔確認〕: `tex_equation_inventory.labels` 29件 vs `equation_semantics.equations[].label` 非 null 12件。差分17件に主結果 `kurtosis-consistency-1/-2` と定義 `eq:S_g` `eq:K_g` を含む。にもかかわらず `document_completeness.equation_artifact_coverage.complete = true`。

### A-3. component の粒度（21件）

`refinement_report.split_actions` より〔確認〕: LLM が作った**意味のある8個**を決定論的 refinement（#308/#319）が **21個の断片**に分割し、label を機械生成している。

| 元の component | 分割後 | 子の名前 |
|---|---|---|
| comp_001 DHOST kernel parameterization | 1 | 維持 |
| comp_002 **Galaxy bias kernel map** | 5 | "Define: …" / "Apply to context: …" / "Transform representation: …" / "Unknown specific operation: …" / "Derive: P_{\rm L}(k), R, R^{-2a}" |
| comp_003 Smoothed observable basis | 1 | 維持 |
| comp_004 **Observable equation system** | 4 | 同上 |
| comp_005 **Bias elimination method** | 4 | 同上 |
| comp_006 Residual consistency relations | 1 | 維持 |
| comp_007 | 2 | **"Application" / "Constraint"**（内容ゼロ） |
| comp_008 **Higher-order correction limit** | 3 | 同上 |

summary も機械文（`"Reusable theory unit (transform_representation) within …; covers 5 equation step(s)."`）。**"Derive: P_{\rm L}(k), R, R^{-2a}" が同一名で4件**。`course_mapping.topics` は component と 1:1（21件）。

## ③ 論文B（fujimoto_d、190頁）章別照合表

| 章（原本 TOC） | 頁 | blocks | 式block | 図caption | 役割判定 | 採択claim | 保留 | 却下 | chunk文字数 | equation record | component |
|---|---|---|---|---|---|---|---|---|---|---|---|
| front（要旨・目次・Glossary） | i–xii | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 6,740 | 0 | 0 |
| 1 Introduction | 1–9 | 68 | 3 | 2 | 3 | **0** | 1 | 2 | 17,144 | 1 | **0** |
| 2 Axion-photon coupling | 10–38 | 233 | 37 | 13 | 7 | **0** | 1 | 6 | 55,125 | 15 | **0** |
| 3 Sensitivity with bow-tie ring cavity | 39–54 | 133 | 48 | 2 | **48** | **17** | 14 | 17 | 17,846 | 27 | **14（全件）** |
| 4 Zero Phase Shift Mirrors（中核技術） | 55–65 | 67 | 14 | 6 | **0** | **0** | 0 | 0 | 19,070 | 3 | **0** |
| 5 Experimental setup | 66–98 | 155 | 17 | 19 | 5 | **0** | 0 | 5 | 33,633 | **0** | **0** |
| 6 Performance evaluation（同時共振実現・g_aγ 上限値） | 99–129 | 139 | 19 | 10 | **0** | **0** | 0 | 0 | 37,368 | **0** | **0** |
| 7 Discussion（周波数雑音・L/R 分裂） | 130–144 | 44 | 14 | 3 | **0** | **0** | 0 | 0 | 13,998 | **0** | **0** |
| 8 Future Prospects and Conclusion | 145–150 | 29 | 3 | 1 | **0** | **0** | 0 | 0 | 11,919 | **0** | **0** |
| App B SNR の導出 | 153–155 | 29 | 5 | 0 | 0 | **0** | 0 | 0 | 9,648 | **0** | **0** |
| App C/D/E・Bibliography | 156–173 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| （section 未帰属） | — | 38 | 0 | 30 | 1 | 0 | 1 | 0 | 9,599 | 18 | 0 |

- **採択 claim 17件も component 14件も、すべて第3章（15頁分）から**〔確認〕。
- 博士論文の**実際の成果**（フィネス2500超での s/p 同時共振の初実現、約3桁の感度向上、g_aγ 上限値、非平面性による L/R 分裂の発見と解決）は第6・7章にあり、**claim にも component にも 1件も入っていない**。ただし `paper_skeleton.logical_blocks`（9件）と `thesis_reconstruction.support_structure.direct_supports`（3件）は**文章としては正しく捉えている**。「見えているが構造化されない」。
- 第4章（ゼロ位相差ミラー＝本研究の中核技術）は 67ブロック・14式ありながら役割判定 0 件。
- PDF 168–190頁は blocks に 1件も無い（`structure_page_coverage_ratio = 0.7842`）。Glossary の記号表・略語表も未取込。

### B-1. 章構造の劣化
`document_structure.sections` は **145件すべて level=1**。見出しに GROBID 誤検出が混入（`"It carries no electric charge."` / `"14)"` / `"L a s e r"` など）。

### B-2. 式の網羅
原本の番号付き式 176 に対し `equation_semantics.equations` 64件。**Ch5–Ch8・App B の式は record ゼロ**。それでも `equation_artifact_coverage.complete = true`。**derivation_chain が参照する equation は 0/64**（論文A は 31/53）。第3章の導出は `infer_intermediate_claim` 47/72 ステップの散文推論で構成され、component_graph 77ノード全部に `missing_equation_link`。

## ④ 横断の発見

### F-1. 構造化層の網羅性は「64ブロック上限 × 壊れたページ番号」という事故で決まっている 🔴最重要
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-1 — `rhetorical_role/input_builder.py` の 64 打ち切り廃止・層化サンプリング・`order` 整列・coverage 報告）
- **証拠**〔確認〕: `src/episteme_graph/agents/rhetorical_role/input_builder.py:11` `_MAX_BLOCKS = 64`、`build()` が `sorted(structure.blocks, key=lambda b: (b.page, b.order))` を走査し打ち切る。`role_annotations` は両論文とも**ちょうど 64件**。論文B は 936ブロック中 212 に `page = 1` が誤付与され、「(page,order) 順の先頭64 body_paragraph の集合」と「実際に注釈された集合」が完全一致することを検証済み。
- 論文A は末尾3段落が落ち、**「限界」節がちょうど落ちている**。
- **示唆**: (a) 上限を切り捨てでなく**複数バッチ反復**に。(b) 打ち切るなら**節単位の層化サンプリング**にし、未処理節を `stage_outputs` に正直に残す。(c) `page` が信用できない経路ではソートキーを `order` のみに。

### F-2. 永続化される claim は「too_broad と自己判定された段落」で、atomic claim は捨てられる 🔴
- 論文A **9/9 が `too_broad`**、論文B 14/17。`claim_object_builder.claims` は A 132 / B 107 だが `db.claims` は 9 / 17 で本文は span 全文（平均772字・最大1,338字）。
- 承認・疑義・再構成・反証条件の対象単位が段落になる。段落単位 claim に「検証されているか」と問うのは原理的に意味を持たない。
- **示唆**: `persist_qualified_claims` が `claim_object_builder` の出力を親子2階層（`parent_claim_id`）で保存する。

### F-3. 型語彙が DB の CHECK 制約で潰れ、横断検索の軸が消える 🔴
- claim_type 239件・23語彙のうち **201/239（84%）が CHECK 語彙外** → 全行 `diagnostic_claim`。component_type は全行 `theory`（実型は `component_type_text` 自由文字列のみ）。`claim_tier`（`paper_core` / `paper_supporting` / `background` / `prior_work`）は DB に列がなく永続化されない。
- **示唆**: 語彙の正本を1箇所に置き、DB は CHECK を捨てて語彙テーブル参照に。`claim_tier` は列に昇格。

### F-4. claim の同一性（ID）が壊れている 🔴
- `legacy_ids` は**論文A 9件すべて `["claim_span_001","span_001"]`**。同一 claim を指す ID が3方式併存（`claim_span_001_9` / `claim:tex_b104:span_001` / DB UUID）、②形式は A 14件 / B 15件が解決不能。
- **示唆**: span_id を `{block_id}:{n}` で一意化し、claim_id をそこから決定論導出。参照形式を全ステージで1方式に統一。

### F-5. 意味のある component 名が決定論的 refinement で破壊される 🟠
- 親 label が子では `"{Operation}: {親label}"` に置換。21件中16件がこの形。学習トピック一覧が「Transform representation: …」で埋まる。
- **示唆**: 分割は**構造（親子）として持ち、名前は親を維持**。operation は属性で表す。

### F-6. concept が「記号トークン」であって概念ではない 🔴
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-3 — 式記号の concepts 混入を撤去、記号書式の概念名を除外。概念レジストリへの解決は Phase 3 のまま）
- 論文B の `components[].concepts` 頻出値は `e` `t` `l` `c` `r` `a` `n` `i`（14 component 全部が同じ1文字集合）。`db.claims[].concepts` は全件 `[]`。`prerequisite_concepts` = `["e","x","t","l","c","r","a","sideband","n","i","cavity"]`。
- **示唆**: 概念層と記号層を分離。記号は `symbol_registry` に閉じ、concept は「名前を持つ主題」だけ（最低条件: 2文字超・ontology か atlas 骨格ノードへの解決に成功）。

### F-7. cartridge alias の部分文字列一致で存在しない概念が注入される 🔴
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-2 — `agents/alias_matching.py` の語境界照合を 4 箇所に適用）
- 論文A の原本に "Standard Model" は **0回**。にもかかわらず `component_graph.nodes` **28件中25件**が `concepts` と `prerequisite_concepts` に "Standard Model" を含む。機構は `rhetorical_role/validator.py:190-195` の `str(alias).lower() in lowered`（alias `"SM"` が `"cosmological"` に部分一致）。
- **示唆**: alias 照合を**語境界付き**に。短い alias は大文字完全一致のみ。cartridge 由来の語を成果へ書き戻す経路には必ず `normalization_source` を付ける。

### F-8. particle_physics cartridge は実質「B→D(*)τν フレーバー」用で、両論文にほぼ無力 🟠
- `ontology.json` の `aliases` は **2件のみ**（HQET / Standard Model）。両論文の artifact に "HQET" "Wilson" "R_D" は 0回。唯一働いた alias が誤爆した。`landscape_placement` は正直に `unplaced_domains` を報告している。
- **示唆**: cartridge 名と実内容の乖離を解消。分野選択の入口で論文と cartridge の適合度を事実として提示。

### F-9. 図・表が知識構造に入らない 🔴
- 論文A: `figures: [], tables: []`。原本には figure 3・table 4 があり、**表は主結果の数値係数そのもの**。学習者は「三本の関係式がある」と知れるが**中身を一切見られない**。
- 論文B: 図86件 caption 取得済みだが `interpretation` 非空 **0/86**、`linked_claim_ids` 非空 4/86。表は 0。
- **示唆**: TeX 経路の figure/table パース（少なくとも表を本文テキストとして取り込む）。図の意味付けを `analyze_images` オプトインに依存させない非LLM最低経路。

### F-10. 記号レジストリが定義を持たず、単位も持たず、文書内に閉じている 🟠
- `kind` 全件 `unknown`、`unit` 全件 null。B の `defined` は 12/119。`symbol_id` は `sym_{document_uuid}_…` で文書スコープ。ノイズ（`2!` `(2π)^3` `axiondarkmattersignal`）と表記ゆれ未統合。
- **示唆**: 文書の記号表（Glossary）を最優先の定義源に。単位・次元を第一級フィールドに。記号 ID に横断キーを併置。

### F-11. 主グラフが「理論ステージ5個」に収斂し、論文が違っても同じ形になる 🟠
- A も B も main 5ノード。B の `theory_op_0003 "Consistency relation"` は member 52個の巨大バケツ、`"Transforms"` は `THEORY_STAGE_LABELS` に無いラベル（schema.py:156 で既知不具合として修正済み・この run はその前）。thesis_coverage は A 37/62、**B 0/24**。
- **示唆**: main の表示名は `narrative_annotator` の説明文や親 component label から取る。巨大ノードは stage 以外の分割条件も持つ。

### F-12. DSL 層が最も忠実なのに、下流にも学習者にも届いていない 🟡
- `dsl_linking` A nodes 16 / B nodes 22。node_value が実際に意味を持つ（B: `"ideal simultaneous s/p resonance with mirror phase difference pi"` / `"power-spectrum peak at frequency corresponding to axion mass"`）。**16–22ノードで論文の骨格を最もよく表している。**
- ただし node_value は自由文で横断照合できず、`dsl_linking/schema.py:52` の `CORE_PREDICATES` は10語彙（`PRODUCES` 含む）だが `backend/core/schema.py` の `CorePredicate` は9語彙。正本語彙が2つに割れている。
- **示唆**: DSL 層を「学習者に見せる骨格」の候補に昇格。node_value を概念レジストリへ解決すれば論文間の共通語彙になる。

### F-13. 説明（element_explanations）の9割が対象に到達できない 🔴
- live の theory_claim 説明の element_id 解決: **A 1/26・B 1/22**。component は A 21/34（13件は前回 run の ID 体系 `comp_003__r1`）。
- **示唆**: 紐付けは永続化された対象の ID に対してのみ。解決できないものは `status='unresolved'` として正直に数える。

### F-14. 文章層（skeleton / thesis / narrative / contextual）は忠実。壊れているのは「単位」の側 🟢
- A の `central_question` は原本と一致、`support_structure.assumptions` も正しい（evidence_block_ids に64打ち切りで claim にならなかった段落 tex_b111 を含む）。B の `paper_skeleton.logical_blocks` 9件は**第1章から第8章まで**を覆い、成果・診断を正しく挙げる。
- **結論**: **理解可能な記述はすでに生成されている。失われているのは「それをどの単位に結び付けて保存するか」。**
- **示唆**: 文章層（skeleton の logical_blocks / thesis の support_structure）を一級の知識単位として永続化するのが最も安価な網羅性改善。

### F-15. 原文の可読性がパース時点で壊れ、それがそのまま claim になる 🟠
- TeX: 引用・相互参照が削除され `"…has been already derived in ."` と破綻。インライン数式のマクロ名が食われる（113ブロック中41）。PDF: 936ブロック中187で単語内スペース破損。
- **示唆**: 引用・相互参照をインラインマーカーで残す。インライン数式は `$...$` を保つ。

### F-16. チャンクと式の対応が失われている 🟠
- A は 19チャンクすべてが同一の53式全部を保持。B は196中60が同じ71式集合、残り136は0件。
- **示唆**: `formulas` は `block_ids` から導出したそのチャンクの式だけに。

### F-17. 完全性ゲートが「complete」と誤報する 🟠
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-5 — `equation_labels_missing_from_registry` / `structure_page_coverage_low`）
- A `equation_artifact_coverage.complete = true` だが29ラベル中17欠落。判定は件数比較のみ。B は `ingest_coverage.sufficient = true` だが PDF 168–190頁が未取込。
- **示唆**: ラベル差集合を判定条件に。`structure_page_coverage_ratio < 0.9` を review 条件に。

### F-18. 「取りこぼしの量」がどこにも報告されない 🟠
→ **2026-09-12 解消**（本文 §4 Phase 0 実装記録: P0-10 — `agents/coverage_report.py` を正本に orchestrator `_attach_coverage` が 8 ステージへ `coverage` を付与。claim_qualification は保留）
- 64件で打ち切ったこと・残り872ブロックを見ていないことは**どの stage_output にも現れない**。一方 `contextual_explanation` は `truncated_count` を、`landscape_placement` は `unplaced_domains` を正直に報告する。正直さの実装が層ごとにばらつく。
- **示唆**: 「入力母集合 / 処理数 / 打ち切り数 / 理由」を全ステージ共通の報告形式に。G層 To-Do `material.partial_coverage` に直結。

### F-19. 学習単位としての妥当性 — 種類の偏り 🟠

| 観点 | 論文A | 論文B | 学習者にとって |
|---|---|---|---|
| 定義 | `defined_symbols` の meaning が引用断片 | Glossary 未取込 | ✗ |
| 前提知識 | 生 TeX / "Standard Model" 誤注入 | 1文字トークン | ✗ |
| 直観的説明 | contextual_explanation 90/206 保存 | 40/189 | △ |
| なぜこの手法か | support_structure / DSL にある | 同左 | △（保存単位がない） |
| 数値結果 | 表4個が全欠落 | 上限値・3桁改善が claim に無い | ✗ |
| 失敗と限界 | 限界の段落が打ち切り | 第7章の構造化ゼロ | ✗ |
| 図の役割 | 図0件 | interpretation 0/86 | ✗ |
| 主張のタイプ分け | 全件 `diagnostic_claim` | 同左 | ✗ |

- 粒度: A は「1 operation = 1 単位」で細かすぎ、B は「第3章の理論だけ 14件」で粗すぎ。**同じ規則が両極端の失敗を生んでいる。**
- **示唆**: 学習単位の粒度を「operation 数」でなく**教える単位**（節・主張・装置・手続き）で定義し直す。親 component を topic、子 operation を topic 内ステップに。

## ⑤ 定量サマリ

| 指標 | 論文A | 論文B |
|---|---|---|
| 原本の規模 | TeX 50,953 bytes | PDF 190頁 / 311,465字 |
| sections | 9（階層あり） | 145（全て L1） |
| blocks | 113 | 936 |
| 取込頁カバレッジ | — | **0.7842** |
| chunks | 19（22,465字） | 196（232,090字） |
| rhetorical_role 注釈ブロック | **64**（57%） | **64**（**6.8%**） |
| 採択 span | 9 | 17 |
| claim_object_builder claims | 132（atomic 123） | 107（atomic 93） |
| **DB theory_claims** | **9**（too_broad 9/9） | **17**（too_broad 14/17） |
| claim_type / concepts | 全件 `diagnostic_claim` / `[]` | 同 |
| equation records | 53（ラベル付12 / TeX ラベル29） | 64（原本176） |
| derivation chain から参照 | 31/53 | **0/64** |
| symbol_registry | 238（defined 74） | 119（defined **12**） |
| figures / tables | **0 / 0** | 86 / **0** |
| component_assembly | 21（LLM 原案 8 → 分割 21） | 14 |
| DB theory_components | 21（全件 `theory`） | 14（同） |
| component_graph nodes（main） | 28（5） | 77（5） |
| dsl_linking nodes / edges | 16 / 18 | 22 / 18 |
| element_explanations live 対象未解決 | claim 25/26・component 13/34 | claim 21/22 |
| export_validation | warning 727 / review 104 | error 5 / warning 853 / review 130 |
| thesis_coverage reachable | 37/62 | **0/24** |

### 圧縮率の内訳（論文B）

| 段階 | 量 |
|---|---|
| PDF 190頁 / 311,465字 | — |
| document_structure blocks | 936（頁 78% まで） |
| chunks（RAG 層） | 196 / 232,090字 = **原本の 75%**、全章を覆う |
| rhetorical_role 入力 | 64 ブロック = **本文の 9.3%** ← 律速 |
| qualified span | 17（第3章のみ） |
| DB claims / components | 17 / 14 |
| main graph nodes | 5 |

→ **190頁 → 14 component は「要約」ではなく「64ブロックへの切断」の結果**。圧縮率ではなく切断位置の議論をすべき。
