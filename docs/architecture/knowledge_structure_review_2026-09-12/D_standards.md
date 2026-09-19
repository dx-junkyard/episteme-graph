# 調査D: 外部の知識表現標準・先行実践との照合

> **状態: 調査記録（完了）**（2026-09-12、Opus 5 サブタスク。本調査以降の変更は未評価。親文書は
> [知識構造の見直し提案](../knowledge_structure_review_2026-09-12.md)）

凡例: **【一次】**=仕様書・原論文・公式ドキュメントを直接読んだ / **【二次】**=検索結果の要約経由 / **【推測】**=筆者の解釈・当てはめ

## ① 要約

1. 本システムが独自に作り込んだ構造の**大半に、外の世界の対応標準がすでにある**（claim+evidence+attribution = Micropublications、証拠の束 = SEPIO evidence line、概念の別名 = SKOS、名寄せの記録 = SSSOM、版と来歴 = PROV-O / Trusty URI、論文構造 = DoCO/DEO、記号と理論の再利用 = OMDoc/MMT）。
2. **一致度が最も高いのは Micropublications ontology**（claim / statement / 逐語 quotation / support graph / challenge graph / attribution）。本システムの claim + evidence_registry + D層は事実上その独立再発明。借りるべきは「support と challenge を DAG として明示」「undercut（間接的挑戦）の区別」。
3. **最大の構造的欠落は3つ** — (a) claim の**安定した同一性**（再解析で ID が揺れる。`synth_claim_*`／atomic 子 claim は DB にすら無い）、(b) 名寄せの**根拠の記録**（`element_identity_links`／`atlas_anchor_aliases` は「誰が」を残すが「どうやって」= SSSOM の mapping_justification を残さない）、(c) 台帳の **evidence line 中間層**（`verification_scopes` は条件のみ、「どの証拠の束が支えるか」は `support_paths` が毎回計算するだけで記帳されない）。
4. 概念の同一性が**5系統に分裂**（cartridge aliases / atlas concept / atlas_anchor_aliases / symbol notation_variants / library_entries）。SKOS の `prefLabel/altLabel/hiddenLabel` + `broader/related/exactMatch/closeMatch` という**関係語彙だけ**で統合できる（RDF 化は不要）。
5. 「immutable な本体＋打ち消しを別レコードで足す」（nanopub の `npx:retracts`/`npx:supersedes`）は本システムの「行削除しない・status 遷移のみ」の成熟形。版非依存キー（`cluster_key`/`edge_key`）の一般化が効く。
6. 学習側は KLI の Knowledge Component と KST の surmise relation が `topic.prerequisites` の平坦な文字列リストを構造化する道を示すが、**付随する能力推定・適応は UC5/UC7 で恒久排除**。「半順序の記述」までしか借りられない。
7. **借りてはいけないものが明確にある**: ORKG comparison の数値表 / scite の自動分類を確定値として出すこと / Novak-Gowin のスコアリング / KST の adaptive assessment / RDF-OWL 全面移行 / nanopub の分散署名インフラ。改訂原則4・数値非表示・確定は人間・core の純関数設計と衝突。
8. 持ち出し（V層 `shared_versions.snapshot`）は現状**内部形式**。RO-Crate（JSON-LD + schema.org）を採れば他インスタンス・他ツールへの転用が成立する。「転用のしやすさ」への最大の単発リターン。
9. UI の定石は ScholarPhi / Semantic Reader の **position-sensitive definition**。`symbol_registry` の未活用資産と直結する。
10. 総じて **schema を外部標準に置き換えるのではなく、外部標準を「語彙・行形式・鍵の作り方」として部分輸入する**のが不変条項と最も相性が良い。

## ② 領域別の照合表

### 領域1: 科学的主張の最小単位

| 標準 | 何か | 本システムの対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **Nanopublications**【一次】 | Head / assertion / provenance / pubinfo の4名前付きグラフ。Trusty URI で immutable 化 | `ClaimObjectRecord`（本文）+ `EvidenceRecord`（逐語・出所）+ `theory_review_events`（誰がいつ確定したか）。**3者が別テーブル/別 artifact に散り、1つの公開単位として束ねられていない** | ①主張本文・来歴・公開情報の三分離が構造として無い ②公開単位の同一性がない | **部分採用**（三分離の考え方 = X-16）。RDF 化・署名網は不要 |
| **Trusty URI**【一次】 | URI に45文字の artifact code（ハッシュ）。1バイト変われば URI が変わる | 無し。claim_id は run 内連番的な agent 側 ID | 再解析で ID が揺れ、教員の確定が新 ID に追随できない。`select_supersedable` は「AI が確定を復活させない」は守るが「同じ主張だと分かる」は守れない | **採用推奨（最優先, X-1）**。ハッシュのみ |
| **npx:retracts / npx:supersedes**【二次】 | 編集・削除不可。撤回は別 nanopub を発行。新版は `supersedes` で旧版を指す | `candidate_flow.py`、`landscape_placements` の superseded、gap/edge decisions の**版非依存キー** | 本システムの方が実装は進む（8系統）。ただし claim/component 本体には版非依存キーが無い | **既に実質採用済み**。X-2 で一般化 |
| **Micropublications**【一次】 | Claim / Statement / Representation / Attribution / Evidence / Method。`supports` は推移的、`challenges` は `directlyChallenges` と `indirectlyChallenges`（undercut）。claim を root とする **DAG**。「引用が document 全体を指し裏付け claim に解決しない箇所が support chain の断裂として可視化される」 | claim（`support_status`）+ `source_evidence_ids` + `ThesisNode.support_structure`（8カテゴリ）+ D層 `challenges`（4語彙）+ `support_paths.py` | ①support/challenge が**同じグラフの2側面として型づけされていない**（thesis / D層 / component_graph の三分裂）②**undercut の型が無い**③claim レベルの support 断裂を「発見」として出していない | **採用推奨（X-6）** |
| **SEPIO**【二次】 | Assertion ← **Evidence Line** ← Evidence Item。線ごとに provenance | `epistemic_ledger`（`verification_scopes` 配列）+ `support_paths.support_lines`（読み時計算） | **evidence line の記帳層が無い**。教員が「この線を確かめた」と記帳する先がない | **採用推奨（X-5）** |
| **CiTO**【一次】 | 引用意図語彙（`citesAsEvidence`/`extends`/`qualifies`/`disagreesWith` …） | `component_citations`（帰属 + 版固定）。**意図フィールドが無い** | 版固定は本システムの強み。「なぜ引いたか」がゼロ | **部分採用（X-7）** 3〜5語彙に絞る |
| **ORKG**【二次】 | ResearchContribution（Problem × Method × Result）。**Template** = 専門家が決める述語集合。**Comparison** = contribution 同士を述語軸で並べた表 | cartridge が template 相当。comparison は無し | ①cartridge は語彙の**列挙**で「形の宣言」になっていない②同 template の論文を property 軸で並べる装置が無い | **部分採用（X-15）**。数値表は不可 |

### 領域2: 数学知識管理

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **OMDoc / MMT**【二次】 | **theory** と **theory morphism**（`structure` / `view`）。"little theories" | `theory_components` + `library_entries` + `element_identity_links` | **`view` 相当が無い**。同一性リンクは「同じ部品だ」しか言えず「論文Aの記号σは共通部品の s に対応」という**写像の中身**を持たない | **採用推奨（X-8）** |
| **sTeX**【二次】 | `\symdef` が概念を1つ導入し**一意識別子**を生成。`\symvariant` で表記バリアント | `SymbolRecord`（`canonical_symbol`/`notation_variants`/`scope`/`defining_equation_ids`） | **記号→概念（atlas concept / library entry）のリンクが無い**。scope が文書内に閉じる | **採用推奨（X-9）** |
| **Lean mathlib 依存グラフ**【二次】 | `#min_imports` が最小 import 集合、`#redundant_imports` が推移的冗長を検出 | `DerivationChainRecord` + `TheoryOperationGraph` + `nearby.py` | **「最小前提集合」の計算が無い**（nearby は1-hop、support_paths は一点吊り検出） | **採用推奨（X-11 と併せて）** |
| **Metamath / ProofWiki**【二次】 | 定理ページが前提へリンクし任意深度まで降りられる | `core/descent/engine.py::build_ladder` | 等価物あり | **不要** |

### 領域3: 学習科学の知識単位

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **Knowledge Component（KLI）**【二次】 | KC =「関連課題群での遂行から推測される獲得された認知機能の単位」。概念・原理・技能・**誤概念**を含む | `learning_courses.data.topics[]`、`theory_components`、`misconceptions_by_topic` | ①学ぶ単位が course_data の JSON にしかなく**コース横断の同一性が無い**②誤解を KC と同層で扱っていない | **部分採用（X-10）**。習得確率推定は不可（UC5/UC7） |
| **Knowledge Space Theory**【二次】 | **surmise relation** `q ≤ q'`。ALEKS が実装 | `topic.prerequisites`（文字列リスト） | 前提が**半順序として扱われていない**（推移閉包・循環・矛盾検査なし） | **半順序の記述のみ（X-11）**。adaptive assessment は恒久排除 |
| **Concept map（Novak/Gowin）**【二次】 | 命題・階層・cross-link を**採点**する | 個人知識ネットワーク、atlas 骨格 | 構造は本システムの方が豊か | **採点法は採用不可** |
| **LRMI / schema.org**【二次】 | `teaches`/`assesses`/`educationalAlignment` | `landscape_placements`、`topics[].atlas_node_id` | `landscape_placements` は上位互換。**`teaches`（この部品は何を学ばせるか）が無い** | **部分採用（X-15）** |
| **IMS CASE**【二次】 | `CFItem` 恒久識別子 + `isChildOf` 木 | atlas 骨格（draft/frozen、版履歴、retire） | 既に同等以上 | **ほぼ不要** |

### 領域4: 概念の同一性と別名

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **SKOS**【一次】 | `prefLabel`/`altLabel`/`hiddenLabel`。`broader`/`narrower` は**意図的に非推移**。`exactMatch`/`closeMatch`/…。**`owl:sameAs` を避ける理由が明記** — 統合すると各概念の prefLabel と文脈が潰れる | 5系統に分裂: ①cartridge aliases ②atlas concept ③`atlas_anchor_aliases` ④`SymbolRecord.notation_variants` ⑤`library_entries` | ①**共通の関係語彙が無い** ②`hiddenLabel` 相当が無い（OCR ノイズ・誤綴りを保持できない）③`closeMatch` が無い（「近いが別物」を rejected に丸め、次の AI 提案で同じ判断を繰り返す） | **採用推奨（最優先, X-3）**。`owl:sameAs` 忌避の理由は KN-2 と**同一の論理** |
| **Wikidata**【二次】 | Q-ID + statement（claim + references + rank `preferred/normal/deprecated`）。deprecated は「繰り返し追加・削除されるのを防ぐため」に残す | `status` 遷移群 + 行削除しない原則 | 却下ゾンビ防止は `atlas_gap_decisions` と同じ発想 | **既に実質採用済み** |
| **OBO Foundry ID 政策**【一次(PURL)／二次】 | 不透明 ID・意味を埋めない・`replaced_by` | atlas `node_id` は意味を含む文字列 | リネームが ID に波及（`course.atlas_binding_stale`） | **部分採用（X-1 派生）** |
| **SSSOM**【二次】 | `<subject, predicate, object>` + **mapping_justification**（統制語彙）+ confidence。「不精確さ・不完全さを明示する」 | `element_identity_links`、`atlas_anchor_aliases`（`source` 2値）、`landscape_placements` | **mapping_justification が無い**。「なぜ同じだと言えたか」を再構成できない — **改訂原則1 に直接効くギャップ** | **採用推奨（最優先, X-4）** |

### 領域5: 来歴と版

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **PROV-O**【一次】 | `Entity`/`Activity`/`Agent`。版2系統 `specializationOf` / `alternateOf`、`wasRevisionOf`。qualified pattern | `document_analysis_runs` + `stage_outputs` + `theory_review_events` + `decision_context.py` | ①Entity と Activity の分離が暗黙②stage 間の導出は artifact 再読みでしか辿れない③**`decision_context` は qualified Attribution そのもの** | **部分採用（X-12）** |
| **RO-Crate**【二次】 | JSON-LD + schema.org の可搬アーカイブ | `shared_versions.snapshot`（内部形式 JSONB） | 外に出せない | **採用推奨（X-13）** |
| **FAIR / DataCite**【二次】 | 永続識別子 | 無し | 機関内単一インスタンスに過剰 | **採用不可** |

### 領域6: 論文構造の標準

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **DoCO / DEO**【一次】 | 68クラスの文書部品 + 修辞要素 | `TypedBlock.block_type` 9種 + `Section` | **対応表が無い** | **部分採用（X-14）** |
| **TEI / JATS**【二次】 | GROBID 一次出力 / 学術出版標準 XML | `extractor.py` TEI→TypedBlock | JATS 入力経路なし | **低優先** |
| **Argumentative Zoning / CoreSC**【二次】 | AZ 7〜15カテゴリ、CoreSC は文単位機能 | `ROLE_LABELS` **23種** | 本システムの方が細かい。ただし「**著者の主張か他者の主張か**」の帰属軸が独立次元でない | **部分採用（X-14 同梱）** |

### 領域7: 議論・認識論

| 標準 | 何か | 対応物 | ギャップ | 採用可否 |
|---|---|---|---|---|
| **AIF**【二次】 | I-node と S-node（RA/CA/PA） | `TheoryOperationGraph`、D層 `challenges`、`counterfactual_sessions` | **衝突と選好が一級のノードでない** | **部分採用（X-6 同梱）**。Walton カタログは不可 |
| **Toulmin**【二次】 | Claim / Data / Warrant / Backing / Qualifier / Rebuttal | claim + evidence + derivation + `support_status` + `challenges` | **Warrant と Backing が構造として無い**。`assumption_nodes` が実質この穴 | **語彙整理のみ**（`assumption_nodes` を warrant/backing として位置づけ直す） |

### 領域8: 実装済み参考システム

| システム | 照合 | 借りるべき点 |
|---|---|---|
| **ORKG** | cartridge が template 相当、comparison 無し | template の**形の宣言**化（X-15）。数値表は不可 |
| **scite** | `component_citations` に意図なし。実測分布 **mentioning 92.6% / supporting 6.5% / contrasting 0.8%**【一次データ】 | 3値語彙は借りる価値。自動分類の確定値化は不可 |
| **Semantic Reader / ScholarPhi** | `SymbolRecord` が**入力データとして揃っているのに UI で使われていない** | **位置依存の定義ツールチップ**（X-9 の UI 側）。既存データで実装可 |
| **Connected Papers / OKM** | 論文の海 + レーダー + landscape | 借りるものなし |
| **Elicit / Consensus** | discuss / compare | **賛否集計表示は採用不可**（SL1・数値非表示） |

## ③ そのまま借りられる設計パターン

### X-1【最優先】claim・component の内容アドレス指定キー
- **何を**: `claim_content_key = sha256(normalized_text + source_evidence の block_id 集合 + document_id)` の決定論ハッシュ。component にも同様。
- **どこに**: `ClaimObjectRecord`/`ComponentRecord` の additive フィールド。`theory_claims`/`theory_components` に nullable 列1本。`candidate_flow.select_supersedable` の照合キーを `claim_id` から差し替え。
- **解く問題**: 再解析で `claim_id` が揺れ教員の `teacher_approved` が追随できない。`claim_objects` が永続化されていない非対称も、content key があれば「同じ主張が前回もあった」と言える。
- **整合**: 決定論・非LLM。行を消さない。内部キーで UI に出さない。改訂原則1 を強化。

### X-2【高】版非依存キーを候補系統の共通プリミティブへ
- `cluster_key`/`edge_key` の「版に依存しない安定キー」を `core/candidate_flow.py` に `stable_key_builder` として宣言化。既存8系統は巻き取らず、新系統と X-1 適用先が使う。

### X-3【最優先】SKOS 型の概念別名レジストリ統合（語彙だけ）
- ラベル3種（`preferred`/`alternate`/`hidden`）と関係4種（`broader`/`related`/`exact_match`/`close_match`）**だけ**を共通語彙表として持つ。既存5系統はテーブルを変えず、関係を記す列の値域を揃える。
- `hidden` = OCR ノイズ・誤綴りを「検索に当たるが表示しない」形で保持。`close_match` = 「近いが同じとは言えない」を教員が記帳でき、rejected に丸めて同じ判断を繰り返す現状を解く。
- RDF 化・推論エンジンは導入しない。

### X-4【最優先】SSSOM 型の mapping_justification
- 名寄せ行に `mapping_justification`（`manual_curation`/`lexical_match`/`vector_similarity`/`cartridge_declared`/`corpus_cooccurrence`/`llm_candidate`/`unspecified`）と `justification_detail` を additive に。
- 対象: `element_identity_links`/`atlas_anchor_aliases`/`atlas_gap_decisions`/`atlas_edge_decisions`/`landscape_placements`。
- **改訂原則1（確定は再構成可能な手続にのみ）の直接の補強**。confidence は DB のみ。

### X-5【高】SEPIO 型の evidence line を台帳に
→ **2026-09-13 解消**（P4-5 migration 083 `epistemic_ledger.evidence_lines`。人間の記帳専用・worker は書かない・support_paths の結果は記帳しない。[知識の転用層](../../features/knowledge_transfer_design.md) §8）
- `epistemic_ledger` に `evidence_lines` JSONB 配列（`{line_id, line_kind, evidence_ids[], claim_ids[], equation_ids[], recorded_by, reason, recorded_at}`）。`support_paths` の計算結果は記帳しない（PN-2）。worker は書かない（SL3 同型）。

### X-6【高】support/challenge を DAG として明示し undercut を型に
→ **2026-09-13 解消**（P4-5 migration 083 `challenges.challenge_mode ∈ {direct, undercut}` + `target_element_ref`。[知識の転用層](../../features/knowledge_transfer_design.md) §8）
- `challenges` に `challenge_mode ∈ {direct, undercut}`、疑義の対象を `target_element_ref`（グラフ上の位置）に。記帳は人間のみ不変。

### X-7【中】引用意図の最小語彙
→ **2026-09-13 解消**（P4-5 migration 083 `component_citations.citation_intent`（5 語彙・NULL = 記録なし）。正本 `core/schema.py::CITATION_INTENTS`。[知識の転用層](../../features/knowledge_transfer_design.md) §8）
- `component_citations` に `citation_intent ∈ {uses_as_evidence, extends, qualifies, contrasts_with, cites_for_background}`（教員が選ぶ）。

### X-8【中】OMDoc の view = 記号対応表つきの同一性リンク
- `element_identity_links` に `symbol_correspondence` JSONB。"little theories" は `component_type` の粒度指針にも。

### X-9【中〜高】記号→概念リンク + position-sensitive definition
- `SymbolRecord` に `concept_ref`、UI は記号タップで**その位置より前で最も近い定義**を出す（ScholarPhi 規則）。既存データのみ・新 LLM コール0。

### X-10【中】Knowledge Component 層
- topic に閉じている「学ぶ単位」をコース横断の識別子として持つ（claim と component を跨ぐ・誤概念も含む）。習得確率推定は採らない。

### X-11【中】前提を半順序として扱う（推定なし）
- 循環検出・推移的冗長・最小前提集合の非LLM 純関数。学習者には出さない。adaptive assessment は恒久排除。

### X-12【中】PROV-O の版2系統（revision と alternate を混ぜない）
→ **2026-09-13 解消**（P4-4 — `docs/architecture/layer_registry.md` §4「版の語彙」に revision / alternate を全「版」構造へ宣言。コード変更 0・`test_version_semantics_docs.py` が網羅を固定。[知識の転用層](../../features/knowledge_transfer_design.md) §7）
- V層 / landscape / library の3つの「版」が担う意味を語彙として区別。コード変更ゼロで始められる。

### X-13【中〜高】RO-Crate 型の可搬スナップショット
→ **2026-09-13 解消（束側で）**（P4-1 — export bundle に `ro-crate-metadata.json`（RO-Crate 1.1 + PROV 語彙）と各項目の `stable_key` / `knowledge_object_id`。`shared_versions.snapshot` 自体は非改変で、JSON-LD 化は束の出口に置いた。[知識の転用層](../../features/knowledge_transfer_design.md) §4.1）
- `shared_versions.snapshot` に `@context` を被せ JSON-LD として出せる export。DB 変更なし。「転用のしやすさ」への**最大の単発リターン**。

### X-14【低〜中】外部語彙への写像表（DoCO/DEO / CoreSC / AZ）
- `block_type` 9種 → DoCO、`ROLE_LABELS` 23種 → DEO/CoreSC/AZ の対応表を cartridge の隣に JSON で。AZ の帰属軸（Own/Other/Contrast）を独立次元として `SpanAnnotation` に。

### X-15【低〜中】`teaches` の宣言 + template 化された cartridge
- 部品に「何を学ばせるか」、cartridge に「この分野の component はこの述語を埋める」形の宣言。`educationalLevel`・Bloom 階層は入れない。

### X-16【低】assertion / provenance / pubinfo の三分離
→ **2026-09-13 一部解消**（P4-1 の `ro-crate-metadata.json` が ①主張本文 = 各 JSON ファイル ②どう得たか = `prov:Activity`（解析 run）③いつ公開したか = ルート `Dataset` の `datePublished` に分ける。誰が = 監査台帳のみ（束には書かない）。[知識の転用層](../../features/knowledge_transfer_design.md) §4.1）
- 新たに作る公開単位（X-13 の export）で①主張本文②どう得たか③誰がいつ公開したか を3ブロックに。既存テーブルは再設計しない。

## ④ 借りるべきでないもの（理由つき）

| 借りないもの | 理由 |
|---|---|
| **RDF/OWL への全面移行** | `core/` の純関数設計を壊す。JSONB + pgvector の資産（14,000超のテスト）を捨てる。SKOS 自身が「OWL より意図的に弱い橋渡し技術」を標榜 |
| **ORKG comparison の数値表** | 値が数値のとき優劣として読まれる（LS・CR3・W8・改訂原則4 と衝突） |
| **scite 型の自動分類の確定値化** | W2・GR1 に反する。contrasting 0.8% の実測分布では UI が空になる |
| **Novak/Gowin の採点法** | 地図を点数化した瞬間に学びの表現から成績の代理になる |
| **KST の adaptive assessment** | UC5/UC7 が恒久条項。F4 の撤去判断を逆行させる |
| **Bloom の分類を尺度として使うこと** | 事実上の段位表になる |
| **Walton の argumentation scheme カタログ** | 語彙爆発。本システムの議論は「論文がどう推論したか」 |
| **nanopub の分散署名インフラ** | 単一機関内に過剰。V層「所有者のみ発行」で等価実現済み |
| **OBO の中央 ID 発行 / PURL** | 不透明 ID + `replaced_by` のみ |
| **FAIR / DataCite の外部識別子** | 可視性ゲート下の教材に外部恒久解決は意味が薄い |
| **Elicit / Consensus 型の賛否集計** | SL1 閉世界語彙が構造的に禁じる言明 |
| **Wikidata の rank を学習者に出すこと** | 教員側の内部語彙としてのみ |

## ⑤ 参考 URL 一覧

- Nanopublication Guidelines: https://nanopub.net/guidelines/working_draft/ / docs: https://nanopub.net/docs/ / Trusty URI: http://trustyuri.net/ / retraction: https://nanopub.readthedocs.io/en/latest/publishing/retraction.html / decentralized services: https://peerj.com/articles/cs-387/
- Micropublications（Clark/Ciccarese/Goble 2014）: https://pmc.ncbi.nlm.nih.gov/articles/PMC4530550/
- SEPIO: https://github.com/monarch-initiative/SEPIO-ontology / VA-Spec: https://va-spec.ga4gh.org/en/latest/appendices/sepio-framework.html / ICBO 2016: https://ceur-ws.org/Vol-1747/IT605_ICBO2016.pdf
- 来歴・主張・証拠オントロジーのサーベイ（2025）: https://pmc.ncbi.nlm.nih.gov/articles/PMC12376154/
- CiTO: https://sparontologies.github.io/cito/current/cito.html / DoCO: https://sparontologies.github.io/doco/current/doco.html
- ORKG: https://arxiv.org/abs/2206.01439
- AIF: http://www.arg-tech.org/wp-content/uploads/2011/09/aif-spec.pdf / Bex & Prakken: https://webspace.science.uu.nl/~prakk101/pubs/aifsem12.pdf
- sTeX: https://kwarc.info/systems/sTeX/ / OMDoc/MMT theory graphs: https://arxiv.org/pdf/1306.3198 / Math-in-the-Middle: https://arxiv.org/pdf/1603.06424
- Mathlib network structure: https://arxiv.org/html/2604.24797v2 / import-graph: https://github.com/leanprover-community/import-graph
- KLI framework: https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1551-6709.2012.01245.x / Knowledge Spaces: https://arxiv.org/abs/1511.06757 / ALEKS: https://www.aleks.com/about_aleks/knowledge_space_theory
- Concept maps（IHMC）: https://cmap.ihmc.us/docs/theory-of-concept-maps / LRMI 1.1: https://www.dublincore.org/specifications/lrmi/1.1/ / CASE v1.1: https://www.imsglobal.org/sites/default/files/spec/case/v1p1/information_model/caseservicev1p1_infomodelv1p0.html
- SKOS Primer: https://www.w3.org/TR/skos-primer/ / OBO ID policy: https://obofoundry.org/id-policy / SSSOM: https://mapping-commons.github.io/sssom/introduction/ / Wikidata Ranking: https://www.wikidata.org/wiki/Help:Ranking / Deprecation: https://www.wikidata.org/wiki/Help:Deprecation
- PROV-O: https://www.w3.org/TR/prov-o/ / RO-Crate 1.1: https://www.researchobject.org/ro-crate/specification/1.1/metadata.html / 論文: https://arxiv.org/pdf/2108.06503
- Argumentative Zoning: https://www.cl.cam.ac.uk/~sht25/az.html / CoreSC: https://academic.oup.com/bioinformatics/article/28/7/991/210210
- scite（QSS）: https://direct.mit.edu/qss/article/2/3/882/102990/scite-A-smart-citation-index-that-displays-the / Semantic Reader: https://dl.acm.org/doi/10.1145/3659096 / ScholarPhi: https://arxiv.org/pdf/2009.14237

## ⑥ 一次情報と推測の切り分け

- **一次情報として確認済み**: nanopub の4グラフ構造 / Trusty URI の構成 / Micropublications の全クラス・関係 / SKOS の label 3種・関係語彙・`owl:sameAs` 忌避の理由 / PROV-O の3クラス・qualified pattern / DoCO のクラス群 / CiTO の property 群 / OBO の CURIE→PURL / scite の分類分布。
- **二次情報（実装前に原典確認を推奨）**: SEPIO の evidence line cardinality / OMDoc の structure と view / sTeX の `\symdef` / KLI の KC 定義 / KST の公理 / SSSOM の CV 値 / AIF の S-node 3分類 / mathlib 統計 / ScholarPhi の4機能。
- **未確認**: OBO Foundry の「ローカル ID は数値・意味を埋め込まない」「`owl:deprecated` + `replaced_by`」は取得ページ本文には明記が無かった。
- **推測**: X-9 が SA層 Phase 4 の `element` 解決器に1種足すだけで実装できる可能性 / X-10 を `library_entries.entry_type` 拡張で migration なしにできる可能性 / X-14 の AZ 帰属軸で「他者の主張を著者が支持している箇所」が落ちている可能性。
