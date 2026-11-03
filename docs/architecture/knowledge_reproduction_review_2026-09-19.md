# 論文の再現性レビュー 2026-09-19 — 知識のコンポーネント化と接続で元の論文が再現できているか

> **状態: 調査記録（完了）+ 是正の実装記録（§7）**（2026-09-19。HEAD `3896349`・開発 DB の実論文 12 本
> （2026-09-15 に現行パイプラインで解析した arXiv 論文 10 本 + 旧 2 本）の原本と成果を照合。
> [知識構造の見直し提案 2026-09-12](knowledge_structure_review_2026-09-12.md) の Phase 0〜4 実装後の
> **再照合**にあたる。本調査以降の変更は未評価。）
>
> **体制**: Fable 5.1 が指揮・統合、Opus 5 が調査 5 班（A1/A2 忠実度・B 接続整合・C 再現/消費・D コード上の
> 欠落点）と実装 6 班（§6 の第 1 波）を並列実施。調査班の全文は指揮者の作業領域にのみ残り、本書は
> 指揮者がコード・DB で裏取りした事実だけを載せる。`E-n` は本書の発見番号。
>
> **照らす正本**: [vision.md §6 の 14 原則](../vision.md)（特に原則 1・3・7・8・13）、
> [改善サイクル](improvement_cycle.md) §2、[課題ナレッジ](../issue_knowledge/README.md)。

---

## 0. 一枚の結論

1. **問い・命題・支持構造・章の並び・学ぶ単位（section_block）は原本を忠実に再現している。** 前回 F-14 の
   「文章層は忠実」は不変で、Phase 1〜2 により claim（親子・tier・型）・学ぶ単位（5 種別）が一級の行になった
   ことで、「何を主張した論文か」は 10 本すべてで読める（E-0）。
2. **式・導出・図表の層は PDF 由来の論文で再現できていない。** PDF 経路では①原文の数式を LLM に見せず
   文脈から創作させ②その創作を第一級の値として保存し③しかし導出には使えない、という三重の欠陥で、
   10 本すべてが `claim_chain` に縮退し、理論操作グラフの式リンクは 0、export は全本 `failed_validation`
   （E-1）。TeX 経路の旧 DHOST だけが式の 6 割を導出に使える。
3. **「先頭 N 件で切る」欠陥が下流に残っていた。** P0-1 で直したのは役割判定だけで、主張採否（先頭 96 span）
   と式の意味解析（64 件）と骨格（先頭 12 節）は依然として文書順で打ち切り、しかも被覆報告が無い。
   74 頁の論文は 803 ブロック中先頭 151 だけが主張層に入り、結論・限界の節が claim 化されない（E-2）。
4. **役割判定は 65〜98% の span が修復失敗で `unknown`** になっている。LLM に 1,800 字ブロックの文字
   オフセットを要求する検証契約が原因で、主張採否の選別が事実上「位置」だけになっていた（E-3）。
5. **文書構造の入口で層がまるごと落ちる。** GROBID の付録（`<back>/annex`）と body 直下の図表を読まず、
   節の階層は平坦、caption の検出は区切り記号依存。表は 12 本すべてで 0 件（E-4）。
6. **壊れている事実が計器に映らない。** `reference_health` は 12/12 本で「参照の切れはありません」、
   日次上限で 8/11 本の文脈説明が 0 件・1 本の学ぶ単位が全件ロールバックしても run は `completed`、
   resume すると被覆報告が消える（E-5〜E-7）。
7. **接続の継ぎ目が 3 箇所で切れている。** ノード↔部品（claim 交差を突合しない）、導出 step の ID 体系
   （DB は `chain:step`・グラフは裸 ID）、claim 参照の `claim:` 接頭辞（開幕画面に内部 ID が 27〜68% 露出、
   thesis 参照が全件未解決）（E-8〜E-10）。
8. **改善しきれないもの**は §5 に理由付きで置く（表本体の構造化・vision OCR・main グラフの集約粒度・
   日次予算の意味論・分野未指定の既定 cartridge）。

---

## 1. 方法と材料

- **材料**: 開発 DB（localhost:5432）の `documents` 12 本。うち 10 本は 2026-09-15 に「arXiv から探す」経由で
  取り込み `particle_physics` cartridge・`gpt-5.4` で解析（PDF 経路・`analyze_images` は 2 本のみ）。
  旧 2 本は DHOST（TeX 経路・2026-06）と fujimoto_d（2026-07・knowledge 行なし）。原本は MinIO
  `raw-papers` から取得し PyMuPDF で本文を目録化。
- **観点**: 実データの読み（`data_inspection`）・入力から出力までの経路追跡（`trace_walk`）・全件棚卸し
  （`inventory`）・原本との再現照合（`reproduction`）。
- **手順**: 調査 5 班の指摘を指揮者がコード（`path:line`）と DB で裏取りし、同じ原因の指摘を 1 つの発見
  （E-n）に束ねた。数値は内部調査の事実として載せる（学習者向け表示ではない）。

## 2. 論文ごとの成果の規模（2026-09-19 時点・live 行）

| 論文 | chunks | claims（親/atomic/式由来） | 部品（親/子） | 式 | 導出 step | 学ぶ単位 | グラフ main/detail | 図 | 節 |
|---|---|---|---|---|---|---|---|---|---|
| 2609.15385v1（74 頁） | 103 | 236（23/164/49） | 10/4 | **64**（式ブロック 348） | 187 | 61 | 4/187 | 6 | 74 |
| 2609.15542v1 | 60 | 656（64/559/33） | 15/4 | **64**（37 + inline） | 623 | 98 | 4/623 | 14 | 18 |
| 2609.15931v1 | 20 | 252（25/187/40） | 12/4 | 51 | 94 | 67 | 4/94 | 0 | **2** |
| 2609.15827v1 | 25 | 221（29/178/14） | 15/4 | 23 | 207 | **0** | 3/207 | 19 | 15 |
| 他 6 本 | 33〜75 | 250〜578 | 11〜18 / 4〜10 | **64** が 5 本 | 216〜470 | 72〜98 | 4/… | 0〜8 | 11〜24 |
| DHOST（TeX・旧） | 19 | 132 | 3/18 | 53 | 23 | 59 | 5/23 | 0 | 9 |

## 3. 発見（原因まで遡ったもの）

### E-0 🟢 文章層と学ぶ単位は忠実（維持すべきもの）
〔確認〕`thesis_reconstruction.central_question` / `central_thesis` / `paper_skeleton.logical_blocks` /
`support_structure` は 12 本で原本と一致。`learning_units(section_block)` を order 順に並べると原本 §1〜§6 の
議論の弧になる（例: 2609.15542v1「等方 n(z) 仮定は測光クラスタリングで破れうる → 項分解 → 手法 → 中核関係
→ 診断 → 結果 → 限界 → 結論」）。グラフの claim 参照は 100% が DB 行に解決（前回 C-1 / S-1 は解消）。

### E-1 🔴 PDF 由来の式は「創作 → 第一級の値 → 導出不可」の三重欠陥
〔確認〕
- `src/episteme_graph/agents/equation_semantics/prompt.py:173` が PDF 経路で `## Equation Text` を
  `[OMITTED - PDF math text is treated as corrupted. Reconstruct from context only.]` に置き換え、候補の
  `raw_text`（劣化はしているが可読）を LLM に渡さない。結果 PDF 10 本の式は 100% `reconstruction`、agent
  自身の整合判定で mismatch 27〜35%。実例: 2609.15827v1 の式 (4) は原本が KLD の定義なのに保存 latex は
  λ/θ の記法導入（confidence 0.38）。2609.15542v1 の式 (13) の latex は `\text{Possibly }…` で始まる。
- `backend/core/document_pipeline/persistence.py:287` `latex = extraction.get("latex") or reconstruction.get("latex")`
  — 抽出が空なので創作が `knowledge_equations.latex` と `chunks.formulas[].latex` に無条件で入り、
  学習者向け射影（`component_context.py:200`）は注記なしに返す。
- `src/episteme_graph/agents/equation_semantics/reconstruction_layer.py:24-33` が PDF 由来を無条件に
  `needs_math_review=True` にし、`schema.py:447-479` で `review_required=True`、`schema.py:598`
  `if consistency_review: can_derivation = False` が無条件で veto する（直前の「再構成 confidence ≥ 0.7 なら
  救う」分岐を潰す）。PDF 10 本で `can_be_used_in_derivation` は 0 件、`derivation_chain/agent.py:100-110`
  の claim_chain フォールバックが常に発火。
- 帰結: 導出 step の式リンク 0/2,600、グラフ全ノード `missing_equation_link`、main が 3〜4 ノードの
  同一ラベル（1 ノードが step の 7〜9 割を吸う）、`thesis_coverage.reachable_refs` 0/50〜0/74、export
  10/10 本 `failed_validation`、論文層の「式」ペインが全ノードで空。

### E-2 🔴 「先頭 N 件で切る」が主張採否・式・骨格に残り、被覆報告が無い
〔確認〕`claim_qualification/input_builder.py:10 _MAX_SPANS = 96`（文書順先頭。11 本中 6 本でちょうど 96。
2609.15385v1 は 474 span 中 96 = 80% 未評価で §3〜§5・付録 A〜J が主張層に入らない。2609.15542v1 は
末尾 15 span = Conclusions 全 10 ブロックが落ち、`A_s 2.7σ` の結論が claim に無い）。
`equation_semantics/input_builder.py:23 _MAX_EQUATIONS = 64`（`(page, order)` 順。page は GROBID 誤付与で
803 中 558 が page=1。inline 候補の切り捨ては coverage の母集合外で `truncated: 0` と報告）。
`paper_skeleton/input_builder.py` `_MAX_SECTIONS = 12`（先頭 12 節・付録除外・coverage なし）。
`thesis_reconstruction` の 32/16/16 も未報告。`claim_qualification` は `_attach_coverage` の対象外
（前回 F-18 の「保留」が残存）。

### E-3 🔴 役割判定の span が 65〜98% 修復失敗で `unknown`
〔確認〕`reason == "Repair failed after max attempts"` が 12 本で 65.6〜97.8%（`role_labels=["unknown"]`・
`confidence 0.0`・`is_claim_candidate=false`）。成功する block は短い断片だけ（中央値 22〜217 字、失敗は
191〜424 字）。原因は `rhetorical_role/validator.py:82-95` が `block_text[char_start:char_end] != span.text`
を error にし、LLM に正確な文字オフセットを要求する契約（`repair.py:51` の `on_exhausted` が fallback）。
coverage は `455/455 truncated 0`（件数は正直・中身は不正直）。主張採否の `_should_include` は
`"unknown" in role_labels` を拾うので選別が位置選抜に堕ちる。

### E-4 🔴 文書構造の入口で付録・図表・階層が落ちる
〔確認〕`document_structure/grobid_parser.py:179-181 _parse_body` は `<body>` 直下 div しか歩かず、
`<back><div type="annex">`（2609.15542v1: 付録 A〜F 11,012 字・19 段落・6 見出しが blocks にも chunks にも
0 文字）を読まない。`<body>` 直下の `<figure>` / `<figure type="table">` も読まないため 2609.15931v1 は
TEI に figDesc 8 個あるのに figure_caption 0・図 0（PDF 10 本中 8 本が figure_caption 0、12 本すべて
table_caption 0）。`head/@n` から `level` を導かず全節 level=1。PyMuPDF 側の caption 補完
（`agent.py:39`）は `:` かダッシュ区切りだけで REVTeX の `FIG. 1.` を落とす。TeX 経路の `_CAPTION_RE`
（`backend/core/document_pipeline/tex_archive.py:47`、`[^{}]+`）は入れ子中括弧で一致 0。表は主結果の
数値そのもの（2609.15542v1 Table 2 の `0.3227` 等はどの層にも無い）。

### E-5 🟠 日次上限で文脈説明が 8/11 本まるごと 0 件・run は completed
〔確認〕`orchestrator.py:4008 CTXEXPL_MAX_CALLS_PER_DAY=20` を同日投入 10 本の先着 2〜3 本が使い切り、
残りは `{llm_calls:0, saved_candidates:0, skipped_by_limit:true, coverage:{population:746, processed:0}}`。
G層 To-Do も教材行の表示も無い。

### E-6 🟠 学ぶ単位の永続化失敗が無音（1 本 78 件全ロールバック）
〔確認〕2609.15827v1 は `uq_learning_units_stable_key_live` の UniqueViolation（FIG.2 と FIG.2(cont.) が同じ
figure_id）で 78 件全件ロールバック。原因のコードは `fecf00a`（2026-09-19 V-6）で解消済みだが DB は未再生。
`stage_outputs.knowledge_objects` はどの run にも無く、失敗は artifact の中にしか残らない。

### E-7 🟠 resume すると被覆報告が消える
〔確認〕各ステージの `_attach_coverage` が `if not resumed_from_artifact:` の中だけにあり、
`upsert_analysis_run` は `stage_outputs || new`（top-level 結合・stage の dict を丸ごと置換）なので、resume
した run（2609.15385v1・15750v1・15969v1）は equation / figure の coverage が無い。**一番切られた
2609.15385v1（式 348→64）が一番無言**。

### E-8 🟠 ノード↔部品が全 10 本で無接続・detail の親が空
〔確認〕`component_graph/normalizer.py:1179-1203 _linked_components_for_step()` の突合条件は derivation_id /
step_id / 式集合の交差だけで、claim_chain では常に空。component 側には `linked_claim_ids` が入っている。
帰結: 2,600 ノードで `component=null` / `explanation=null`、detail 623 件の `parent_component_id` 空
（`orphan_detail_node` 警告 623）、edge に `graph_layer` が無い。`_paper_layer_explanation_rows` は
`theory_component` の説明行しか読まない。承認ゲート `_component_approval_problems` は `source_chunks`
と `source_refs/evidence_claims` を要求するがパイプラインの部品は `claim_ids` を持つ → **0/211 が承認可能**。

### E-9 🟠 導出 step の ID 体系が DB とグラフでずれる
〔確認〕DB の agent ID は `{derivation_id}:{step_id}`（`stable_key.py:81-94`）、グラフは裸の `step_001`
（`normalizer.py:684`）、edge は chain id を持たない → edge の `evidence_derivation_ids` は DB に 0% 解決。

### E-10 🟠 claim 参照の `claim:` 接頭辞で内部 ID が露出・thesis 参照が未解決
〔確認〕thesis / dsl の参照は `claim:blk_xxx:span_001`、`theory_claims.source_scope.legacy_ids` は
`blk_xxx:span_001`。`discuss/opening.py:189-195` は索引に無い id を label にするため開幕画面の item の
27〜68% が内部 ID。`thesis_coverage.unreachable_refs` の missing_claim 19〜27 件は接頭辞を落とせば全部
解決（実測）。export の `DSL_EDGE_DANGLING_EVIDENCE` 20 件も同根。
`export_validation_gate.py:3040` は `evidence.evidence_claims` を読むがグラフは `evidence_claim_ids` を書く
→ `COMPONENT_GRAPH_EDGE_NO_EVIDENCE` 618 件が誤警報で本物を埋める。

### E-11 🟠 計器が「壊れていない」と言う
〔確認〕`reference_health` は「参照先が存在しないリンク」しか数えず（6 種）、層が空・リンク自体が無い・
上流で打ち切られた事実は対象外。12/12 本 `ok`。`document_completeness.complete=false`（11/12）は artifact
止まり。`export_validation.failed_validation` でも run は adopted。

### E-12 🟡 残る歪み（前回からの継続）
- F-16 chunks の式ばらまき: `_merge_equation_previews_for_chunk` が page 一致だけで判定（87〜94% が当該
  chunk の block 外）。
- F-5 部品名: split された子が `Constraint` / `Application` 等の機械名になり親の名前が行から消える。
- F-10 記号: kind 100% unknown・unit 0・数値リテラルや環境名がノイズとして登録。
- 概念名: `w(\theta)` `n(z,\theta)` のような括弧付き関数形と 60 字超の命題文が concept を通過。
- 式由来合成 claim の本文に内部 ID（`eq_eqcand_inline_…`）・自己依存・非記号の `$…$` が焼き込まれる。
- 学ぶ単位の並び: `order_index` が種別内 0 始まり・`section_ids` は 2 種別だけ → 章順に並べ直せない。
- F-8 分野: 宇宙論・重力波の論文が既定 `particle_physics` で解析（`unplaced` は正直に出る）。

## 4. 前回 F-1〜F-19 の現状判定

| # | 判定 | 根拠 |
|---|---|---|
| F-1 / F-3 / F-4 / F-7 / F-17 | 解消 | 役割判定 truncated 0・claim_type 20 種以上・stable_key 全件一意・alias 誤注入 0・completeness が発火 |
| F-2 | **解消（注記漏れを本書で補う）** | Phase 1 で claim object 全件が親子で行になった（656/252 件） |
| F-5 / F-6 / F-10 / F-16 | 部分解消 | §3 E-12 |
| F-8 / F-9 | 未解消 | E-4（表 0 件・図 2/10 本） |
| F-11 | 未解消・悪化 | E-1 の帰結（main 同一 4 ラベル・1 ノードが 84%） |
| F-12 / F-19 | 部分解消 | `dsl_node` / 5 種別の学ぶ単位が行に（E-0） |
| F-13 | 原因が入れ替わった | ID 解決は 100%・生成自体が 0（E-5） |
| F-14 | 維持 | E-0 |
| F-15 | ほぼ解消 | `" ."` 破綻 20 箇所程度・引用は保持 |
| F-18 | 部分解消 | E-2 / E-7 |

## 5. 判断と、改善しきれないものの理由

### 5.1 指揮者判断（改善サイクル §2.2 の 3 種に当たらず、推奨案を採用して進めたもの）
- **J-1 再構成された式を導出の根拠にしてよい**（E-1）。条件は既存分岐（mismatch なし・
  `reconstruction.status != none`・confidence ≥ 0.7・`reconstruction_based|context_inferred`）のとおりで、
  backing は `partially_source_backed` 止まり・`review_status` は要レビューのまま。`source_backed` にはしない
  （原則 1・確定は人間）。これは新しい信頼の付与ではなく、コードに既にあった意図（救済分岐）を無条件 veto が
  潰していた状態の是正。異論があれば `schema.py` の 1 条件で戻せる。
- **J-2 数式の原文は隠さず untrusted と明示して見せる**（E-1）。TB1〜TB4（PDF 由来は untrusted 入力）の
  規約どおり、ラベル付き区画で提示し、復元必須・`source_backed` 禁止は維持。
- **J-3 打ち切りの既定は「上限なし」**（E-2）。P0-1 と同じ処方（既定 0 + env 明示 + 層化サンプリング +
  被覆報告）。LLM 呼び出し回数は増える（式 348 の論文なら +284 コール）が、役割判定が既に「全件」なので
  同じ規律。上限を敷く運用は env で明示する。
- **J-4 日次上限の意味論は変えず、見えるようにする**（E-5）。予算は運用パラメータで、まず G層 To-Do と
  計器の事実文で「説明が作られていない」を出す。予算の按分（run 単位）はオーナー判断に上げる（§5.2）。

### 5.2 改善しきれないもの（理由付き）
| 項目 | 理由 | 出口 |
|---|---|---|
| 表本体の構造化（セル・単位・数値の一級化） | 表の意味は列見出しと単位の解釈を要し、決定論だけでは分野非依存に組めない。今回は caption と本体テキストを落とさず chunks / block に載せるところまで | 表専用 agent（LLM-first）は別設計書 |
| PDF 数式の vision OCR | `review_reason` に `vision_ocr_unavailable:…:unknown_provider` が残り、式領域の region_render → vision の経路が現行では無効。有効化はコスト（式 1 本 = vision 1 コール）と M層の scene 設計を伴う | 別件（M層 `pipeline.vision` の適用範囲の判断） |
| main グラフの集約粒度（stage 5 個への収斂） | #308 の規律「main は theory stage 単位」自体の変更で、表示原則に関わる。E-1 の是正で detail が式 step になれば偏りは緩和する見込み | 実測後にオーナー判断（stage × 親部品の 2 段集約の可否） |
| 日次予算の run 按分 | コスト統制の意味論の変更 | オーナー判断 O-A（推奨: 上限到達を To-Do に出したうえで、バッチ取り込み時は run ごとの最小枠を確保） |
| 分野未指定の既定 cartridge | 2026-09-10 F9 で出荷既定は空になっている。開発 DB の 10 本は `.env` の `EPISTEME_DEFAULT_CARTRIDGE_ID=particle_physics` が効いた運用側の事実 | 運用: 取り込み時に分野を選ぶ（既存 UI） |
| 旧 2 本（DHOST / fujimoto_d）と 2609.15827v1 の再生 | 是正後の再解析は LLM を数百回呼ぶ。指揮者は今回 live 呼び出しを行わない | 再解析（`reanalyze`）または `persist` ステージの再実行はオーナーの操作で |
| TeX 経路の図ファイル取り込み | `figure_image_extraction` は PDF のみ。TeX 束の図 PDF を画像化する経路が無い | 別件 |

## 6. 着手の地図

**第 1 波（バックエンド / A層・UI に触らない・ファイル所有を分離して並列）**
- WP-A 上限と被覆: 層化サンプリングの正本 `agents/stratified_sampling.py` / 主張採否・式・骨格の既定上限なし +
  env / 4 ステージの coverage / resume でも coverage（E-2 / E-7）。
- WP-B 式の信頼連鎖: prompt で原文を untrusted 提示 / veto 条件の限定 / mismatch・低信頼の label 退避 /
  導出の併走 / `chunks.formulas` の block 絞り込みと `reconstructed` 印 / `stage_outputs.knowledge_objects` /
  学習者向け「AI が復元した式」の事実文（E-1 / E-6 / E-12）。
- WP-C 文書構造: annex / body 直下の図表 / head@n の階層 / REVTeX caption / 走り込み見出し（E-4）。
- WP-D 接続配線: claim 交差でノード↔部品 / 合成 step ID の併記 / `claim:` 正規化の正本 / 誤警報 /
  説明行の element_type 拡張 / 承認ゲートの読み時導出 / 学ぶ単位の section 伝播と並び（E-8〜E-10 / E-12）。
- WP-E 役割判定ほか: span オフセットのサーバ側訂正と失敗集計 / 合成 claim の文面 / 記号ゲート / 子部品名 /
  概念名の記号判定（E-3 / E-12）。
- WP-F 計器: `reference_health` の検査種別と文言 / G層 `material.explanations_skipped` /
  `material.ingest_incomplete`（E-5 / E-11）。

**第 2 波（UI・マニュアル・記録）**: 学習画面の式注記の描画 / 教材行の計器チップ / マニュアル節 /
課題ナレッジへの記帳（1 発見 = 1 エントリ）/ `CLAUDE.md` 追補。

**保留**: §5.2。

## 7. 実装記録（2026-09-19・第 1 波 + 第 2 波の一部）

Fable 5.1 が指揮、Opus 5 の実装 6 班（ファイル所有を分離して並列）+ 敵対的レビュー 1 班。
migration は 0 本・新テーブル 0・新エンドポイント 0・LLM 呼び出し箇所の追加 0。**再解析は未実施**
（LLM の live 呼び出しは行わない方針）で、効果は次回の解析から出る。docker E2E は未実施。

| 発見 | 着地（正本） | 要点 |
|---|---|---|
| E-2 / E-7 | `src/episteme_graph/agents/stratified_sampling.py`（新設）/ `claim_qualification` `equation_semantics` `paper_skeleton` `thesis_reconstruction` の `input_builder.py` / `orchestrator.py` / `config.py` / `.env.example` | 既定「上限なし」+ env `CLAIM_QUALIFICATION_MAX_SPANS` / `EQUATION_SEMANTICS_MAX_EQUATIONS` / `EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS`（inline のみ既定 32）/ `PAPER_SKELETON_MAX_SECTIONS`。上限時は節単位の層化。4 ステージに coverage、resume でも artifact 由来で報告（`details.source="artifact"`） |
| E-1 | `equation_semantics/{prompt,schema,validator,repair}.py` / `derivation_chain/agent.py` / `persistence.py` / `learner_context_common.py` / `element_context.py` / `component_context.py` / `label_vocab.py` / `app.js` | 原文を untrusted 区画で提示（J-2）。veto はその式固有の疑いに限定（J-1・`untrusted_pdf_only()`）。mismatch かつ低信頼は label 退避 + `unknown` 降格。導出は式 chain と claim chain の併走。`chunks.formulas[].reconstructed` / `latex_source`。学習者向け事実文 `RECONSTRUCTED_EQUATION_NOTE`（DTO `latex_note` / `label_note`・JS は素通し） |
| E-12（chunks） | `persistence.py::_merge_equation_previews_for_chunk` | block_id 一致を第一条件・page はフォールバックのみ |
| E-6 | `persistence.py::_record_knowledge_stage_output` | 4 系統の永続化要約と失敗を `stage_outputs.knowledge_objects` に必ず書く |
| E-4 | `document_structure/{grobid_parser,agent,schema}.py` | back/annex・body 直下 figure/table・head@n 階層・REVTeX caption・走り込み見出し・要旨末尾の数値羅列の分離。`Section.section_number` / `is_appendix`、`metadata.structure_recovery` |
| E-3 | `rhetorical_role/{repair,agent,schema}.py` | span オフセットをサーバ側で決定論解決（検証基準は不変）。`summary_stats` に修復失敗・unknown 比率 |
| E-12（文面・記号・概念・子名） | `claim_object_builder/equation_claim_synthesis.py` / `symbol_registry/{builder,schema}.py` / `component_assembly/{granularity_analyzer,component_refiner,schema}.py` | 印字番号のみを呼称に・自己依存除外・記号だけ `$…$`。記号候補の形式ゲート + 除外理由。子部品名は「親 label — 責務」。概念名の記号判定を拡張 |
| E-8 / E-9 / E-10 | `component_graph/{normalizer,schema}.py` / `core/knowledge_objects/references.py`（新設）/ `discuss/opening.py` / `routes/theory_components.py` / `export_validation_gate.py` / `graph_paper_layer/builder.py` | claim 交差で node↔部品（ccb39a23 で 0 → 155 detail に親）・辺 `graph_layer`・合成 step ID 併記・`claim:` 正規化の正本・誤警報 618 件の解消・説明行の element_type 拡張。復元由来しか backing の無い node/edge は `partially_source_backed` 止まり |
| E-8（承認ゲート） | `routes/theory_components.py` / `api/schemas.py` | `claim_ids` / `equation_ids` を出典として読み時受理、`source_chunks` は根拠 claim の chunk から導出（推計 0/211 → 207/227 が承認可能。弁は維持） |
| E-12（学ぶ単位の並び） | `core/knowledge_objects/learning_units.py` / `core/course_units.py` | 3 種別に `section_ids` を伝播、候補順を章順に |
| E-5 / E-11 | `core/reference_health.py` / `core/coverage_facts.py`（新設）/ `admin_assistant/next_steps.py` / `routes/admin.py` | 検査種別 6 → 10、空層・打ち切り・skip・完全性理由を事実文で。文言は「検査した参照はすべて解決しています」。旧文言のスナップショットは未確認扱い。G層 `material.explanations_skipped`（recommended）/ `material.ingest_incomplete`（optional） |

- **記録**: 課題ナレッジ IK-0328〜IK-0343（16 件・candidate）。`docs/pipeline/overview.md` §4 を新規律に改訂。
  前回 A_fidelity.md の F-2 に解消注記。
- **テスト**: backend 16,258 → 16,432 passed / src 2,078 → 2,121 passed（2026-09-19 時点の実行結果。正本はテスト実行）。ガードレールの
  意図的な置き換えは `test_pipeline_coverage_report.py`（resume の報告を artifact 由来に）・`test_landscape_ui_static.py`
  （CSS 版文字列の固定を解除）・`test_reference_health_core.py`（委譲先の検査に更新）・`test_next_steps_material_gaps.py`
  （ingest_incomplete を optional に）。
- **第 2 波の残り（2026-09-19 実施済み・§7.2）**: 教員 UI（`admin-graph-review.js`）で説明の出所 `explanation_element` と
  item 単位の説明を描く / 辺の `graph_layer` をトグルに使う / `materials.rerun_pipeline` capability と道案内 / マニュアル節。
  再解析後の実測（式の救済本数・主張の被覆・図表の回収）は次のサイクルの発見段に回す。

### 7.1 敵対的レビューの是正（2026-09-19・同日）

第 1 波の実装を「壊すつもりで」読んだ敵対的レビュー 1 班の指摘（🔴 1・🟠 5・🟡 13）のうち、
🔴🟠 6 件と🟡の再構成できた 3 件を同日に是正した。課題ナレッジ IK-0344〜IK-0350（candidate）。

| 指摘 | 内容 | 着地 |
|---|---|---|
| R-1 🔴 | system 導出（`system_derivation`）の step に復元由来の印が無く、復元式だけのノードが `source_backed` に到達しうる | 判定関数 `reconstruction_backed_reason` と定数を `derivation_chain/schema.py` / `system_derivation.py` に置き、ローカル chain と system 導出の両方が同じ印を書く。復元は疑いではないので chain の `review_reasons` には積まない（J-1） |
| R-2 🟠 | オフセット訂正 3 戦略のうち空白正規化・頭尾アンカーの 2 つが、位置を当てても `span.text` が LLM の写しのままで validator の逐語比較を通らない | 訂正したときは本文のスライスを `span.text` にする（本文が正本・検証基準は不変）。戦略ごとに validator を通すテスト |
| R-3 🟠 | PUT 承認経路（`_validate_for_review`）が旧ゲートのままで非対称が逆向きに復活。`equation_ids` だけの出典に復元式が数えられる | 出典キーの読み・根拠 claim からのチャンク導出・復元式の除外を補助関数に寄せ両経路で共有。復元式 ID は採用 run の `equation_semantics` artifact から読む（列は書かない） |
| R-4 🟠 | 表本体の `body_paragraph`（`raw.in_table`）が役割判定・主張採否の母集合に入る | 両 input_builder で除外し、除外 block ID を coverage `details.excluded_table_block_ids` に残す（件数なし） |
| R-5 🟠 | `detail_node_parent` を破断扱いにしたため既存教材が全件 `broken` に振れうる | 欠落の区画（`GAP_DETACHED_DETAIL`）へ移し、status は変えず事実文 `FACT_DETACHED_DETAIL` だけ出す。破断の種別は 9 |
| R-6 🟠 | 主張採否の上限撤廃分の LLM コール数が記録も注記もされない | `ProviderJSONLLMClient.calls` 計器 + `summary_stats.llm_calls`（run ごとの差分）+ stage payload。`docs/pipeline/overview.md` §4 に注記 |
| 🟡 | `max_equations` の理由コードが inline 上限だけ効いたときにも並ぶ | 実際に効いた弁だけを reasons に書く（既定「上限なし」では空） |
| 🟡 | 印「AI復元」の JS 直書き | `label_vocab.RECONSTRUCTED_EQUATION_MARK` + `core/lecture.annotate_reconstructed_formulas`（全投影経路が通る `normalize_to_placeholder_format` で付与）。JS は `reconstructed_mark` / `reconstructed_note` を素通し |
| 🟡 | CSS 版検査の緩和（`?v=` の存在だけ） | 日付 8 桁を取り出して landscape の版以上であることを検査 |

- **テスト**: backend 16,443 passed / src 2,134 passed（是正後の実行結果。正本はテスト実行）。

### 7.2 第 2 波の残り（2026-09-19・同日）

| 項目 | 着地 |
|---|---|
| 説明の出所と item 単位の説明 | `admin-graph-review.js` の「論文での対応」: 「この論文での説明」に出所行（「出所: 論理要素に付いた説明」。要素種別の表示名は `element-vocab.js`、引けなければ出所行を出さない = PL7）、論文側の主張・式の各行に `claims[].explanation` / `equations[].explanation` を「説明: …」+ 状態チップで描く |
| 辺の `graph_layer` | 正本 `admin-lecture-studio.js::lsGraphFilterByLayer`（レビュー画面は委譲）: 層を持つ辺はその層で判定し、持たない旧グラフの辺は端点の可視性だけで判定（後方互換） |
| `materials.rerun_pipeline` | capability（guidance_only・`howto_doc` = `admin_operations/materials.md#rerun-pipeline`・道案内は行 → 「パイプラインを実行 ▼」の論理アンカー `material_pipeline_run_button`）。G層 `material.explanations_skipped` / `material.ingest_incomplete` の `capability_id` を付け替え。再実行は LLM を多数回呼ぶので代行に載せない（P8） |
| マニュアル | `teacher/26-admin-graph-review.md`（層の切り替え・論文での対応）/ `teacher/11-admin-materials.md`（パイプラインを実行 = G層 2 ルールの解消手段）。新しい `data-ui-anchor` は増やさない（既存の `materials.row-pipeline-run` の上に道案内を置く） |

**再構成できなかった🟡**: 死にコードの指摘は差分に含まれる関数・定数の未参照走査（backend / src / frontend）で
該当を見つけられなかった。他の🟡もレビュー本文が残っていないため、次のサイクルの発見段で改めて読む。
