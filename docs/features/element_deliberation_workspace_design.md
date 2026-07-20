# W層（Element Deliberation Workspace / 要素検討ワークスペース）設計

> **状態: Phase 0 + Phase 1 + Phase W-β + Phase 2 + Phase S 実装済み**（2026-07-16 時点）。本書は設計の正本。
> **追補（設計・未実装）**: 選択要素を中心に上位構造・文脈上の役割・下位構造を一体表示する
> [要素中心コンテキストビュー設計](element_context_lens_design.md) は Issue [#498](https://github.com/dx-junkyard/episteme-graph/issues/498) を正本とする。
> Phase 1 実装物: `core/deliberation/positioning.py` に §4.2 コーパス横断レンズ
> （`cross_corpus`）を追加。要素→代表テキスト（非LLM連結・§15 未決2）→
> `core.embedder.search_similar_papers` で近傍 chunk を検索し、自 document を除外した
> 上位5件の他 document を返す（各 item に `document_id` を付与）。embedding 生成を
> 伴う唯一のレンズだが新規抽出ロジックは無く、書き込みもゼロ。U層計測は
> `usage_context("deliberation:cross_corpus", ...)` でラップ（`core/llm_usage/schema.py`
> の `KNOWN_FEATURES` に追記）。閲覧不可 document 由来の候補は `positioning.py`（core、
> per-user 権限を判定できない）ではなく `routes/deliberation.py` の
> `_apply_cross_corpus_gate`（既存 `_filter_by_document_view` / `_make_document_view_checker`
> を再利用）が overview 応答の段階で最終フィルタする（W5・fail-closed。フィルタ後 items が
> 空ならレンズごと None に落とす）。フロントは `deliberation.js` の `LENS_LABELS`/`LENS_ORDER`
> に `cross_corpus: "コーパス横断"` を追加するだけで、既存レンダラが `item.label`/`item.value`
> のみを描画するため `document_id`/`hidden_count` は自然と非表示になる（W8）。
> W-β 実装物: migration 048 `element_identity_links`（candidate/confirmed/rejected・
> `local_expression` はリンク行が保持・library_entries へ実FK・孤児掃除は
> `_purge_document`/`delete_material` に同乗）+ `core/deliberation/identity_links.py`
> （`create_candidate` は status 固定・`decide` は decided_by 必須・
> `confirmed_links_for_document` が P-2 traversal の読み取り正本）+ API 5本
> （POST candidate 作成 / confirm / reject・GET instance/shared_part 別一覧。
> confidence は段階ラベルのみ・監査 `AUDIT_ENTITY_DELIBERATION`）。同一性リンクの一意性は
> `instance_document_id` を含めた4列で判定する（equation の `element_id` は論文間で衝突
> しうるため。レビュー指摘2026-07-15で修正・migration 048 の `DO $$` 冪等ガードで旧3列
> 制約からの移行にも対応）。`GET /shared-parts/{id}/identity-links` は各リンクの
> `instance_document_id` を閲覧できないリクエスト者からは除外し、隠した件数を
> `hidden_count` として正直に返す（同レビュー指摘で追加）。
> library_entries 側 `local_expressions` への materialize は W-2（コミットルーティング）で行う。
> 実装済み: `backend/core/deliberation/`（schema / refs / decomposition / positioning）+
> `routes/deliberation.py`（overview = 面①内訳 + 面②位置づけ §4.1/4.3/4.4。shared_part の
> `frozen_content.exemplar_images` は各画像の由来 document の閲覧権限（
> `services.resolve_document_access`）でフィルタし、隠した件数を
> `fields.exemplar_images_hidden_count` として正直に返す — W5 の「画像は由来 document の
> 権限を継承」を core 層の分離を保ったまま route 層で強制する。レビュー指摘2026-07-15）+
> `frontend/public/js/deliberation.js`（「深く検討」モーダル本体）。4要素型すべてに
> 導線がある（レビュー指摘2026-07-15で追加）: `admin.js` の図モーダル（figure）・
> revisions 画面の equation 変更、`admin-lecture-studio.js`（原稿スタジオ）の
> チャンク/セクションの論理要素カード・「選択中コンポーネント」ビュー（theory_component、
> `TheoryComponentOut.source_scope.document_id` を使う）・チャンクの主張一覧
> （theory_claim、`ClaimOut.document_id` を使う）。revisions 画面上の変更差分
> （`entity_changes`）に限っては、claim/component の `entity_id` が pipeline 内部 id で
> theory_claims/theory_components の DB id と一致する保証がないため、そこでの導線のみ
> 引き続き equation に限定する（理由は admin.js にインライン文書化）+
> ガードレール（test_deliberation_guardrails / test_deliberation_positioning /
> test_deliberation_ui_static）。
>
> **Phase 2 実装済み（2026-07-16）**: migration 049（`deliberation_sessions` /
> `element_annotations`・§6 のスキーマどおり）+ `core/deliberation/{store,dialogue,annotations}.py`
> + `core/llm.py::generate_conversation_turn`（マルチターン+vision・structured output 同時取得・
> system ロール回避の家風）+ API 6本（sessions 作成/取得・messages・annotations 一覧/commit/dismiss）。
> 1応答=1 LLM コール（W6。注釈抽出も同一コールの structured output。スキーマ検証失敗は注釈なしに
> 縮退・LLM 失敗は `degraded:true` の非LLM フォールバック）。CostGate
> `DELIBERATION_MAX_CALLS_PER_SESSION`(8)/`_PER_DAY`(40)・fast tier 既定。孤児掃除は
> `_purge_document`/`delete_material` に同乗。**コミットルーティング v1 は3経路**（§15 未決1 の判断）:
> `interpretation`→C層 explanation(kind='personal') / `meaning`・`decomposition`→
> `theory_components.summary`/`teacher_notes` / `identity`→W-β `create_candidate`。
> `positioning_note` は 422（後続）。`standardization` は **Phase S（2026-07-16 実装済み）**で解禁:
> `core/deliberation/standardization/`（llm_worker 6系統目アダプタ・三角測量＝LLM事前知識+
> L層凍結版類似+コーパス反復（confirmed identity links ∪ source_document_ids ≥2）→
> `aggregate.decide()` の決定論5語彙合成。**LLM 単独主張は unknown（幻覚ガード）**・
> 修復失敗は evidence①非認知として続行）+ worker（threading・冪等スキップ・`force` 再評価・
> `STDPART_MAX_CALLS_PER_DAY` 既定10・`STDPART_LLM_MODEL`）+ 手動バッチ API 2本
> （shared-part 単体 / domain 一括、`{"queued", "note"}` 応答・生件数なし）+ migration 050
> （`library_entries.standardization_status`。revision 非変更のガバナンス列・
> `UPDATABLE_FIELDS` 外＝draft 編集から書けず教員 commit のみ）。語彙の正本は
> `core/library/schema.py`（ライブラリ自身の統治列のため）。フロントは
> モーダル2ペイン化（右=対話・注釈カード confirm/dismiss・遅延セッション作成・429/degraded は
> 事実文）。セッション履歴の一覧・復元 UI は未実装（DB には全ログ保持・P4。後続で一覧 UI）。
>
> **migration 番号の補正（2026-07-15）**: 本書の「migration 046」は起草後に 046/047 が
> 他機能（atlas_report_incorporation / topic_lecture_audio）で使用されたため無効。
> **Phase W-β の `element_identity_links` は migration 048** で実装する。Phase 2 の
> `deliberation_sessions` / `element_annotations` は着手時点の次番号を取ること。
> 実装に着手する際は §13 の issue 分割を正本として使うこと。
>
> **命名の注意**: 「E層」は既に Exposition Layer（`exposition_layer_design.md`）が占有している
> ため、本層は **W層（Workspace）** とする。「分野の地図（Field Atlas）」とは別機能
> （コード・API・UI 文言は `deliberation-` / `element-` プレフィックスで衝突回避）。
>
> **親文書**: `knowledge_network_vision.md`（知識ネットワークビジョン）。本層はその
> Phase W-α / W-β の実装先であり、KN-1〜KN-4 の不変条項に従う。特に **KN-2/KN-3 により、
> §5 の「昇格・統合」は「重心の移動」ではなく「同一性リンクの追加（非破壊・candidate →
> 人間確定）」と読むこと**。library_entry には `local_expressions`（出所付き表現リスト）を
> 追加し、論文ごとの表記を潰さない。`element_annotations.kind` には `identity` /
> `standardization` を加える（ビジョン文書 §3 修正②③・§7 Phase W-α）。
>
> **W-α 反映済み（2026-07-14）**: 上記の意味論修正を本文へ反映した。§5.5（同一性・標準化と
> 非破壊リンク）・§6 の `element_annotations.kind` 拡張・§5 コミットルーティング表に
> `identity` / `standardization` を追記。実体テーブル `element_identity_links` は Phase W-β
> （migration 046 同乗）で追加する（本 W-α は意味論と注釈語彙の確定まで。コードなし）。

---

## §0 位置づけと不変条項

### 何を解くか

パイプライン（A層 `src/episteme_graph/agents/`）は PDF を一度処理して図・知識コンポーネント・
claim・数式などの成果を生成する。しかしこれらは**生成されたきり**で、教員が

- 「この要素は**関連論文・資料の中でどこに位置づくのか**」（文脈）
- 「この要素の**中に何が含まれ、それぞれ何を意味するのか**」（内訳）

を一箇所で確認・対話・追記する場が無い。W層は、**一度処理された任意の1要素**を選び、
その要素を文脈の中で深掘りし、AI と対話し、解釈を**候補として**付与できる横断ハブを提供する。

### 立場（他層と同じ「読む側」）

W層は A層成果（`theory_components` / `theory_claims` / `theory_component_graphs` /
`document_figures` / `document_analysis_runs.stage_outputs`）を**読むだけ**で、A層コードを
変更しない。C層（承認）・D層（疑義/検証）・atlas（分野の地図）・L層（ライブラリ）が
既に持つ機能を**再発明せず合成する**ハブとして実装する（§10）。

### 不変条項（他層から継承）

- **W1 A層非改変**: `src/episteme_graph/agents/` を読むだけ。成果テーブルに列を足さない
  （W層専用テーブルに積む）。
- **W2 確定は人間・AIは候補のみ**: 対話で AI が出す解釈は常に `status='candidate'`。
  人間が明示コミットするまで既存構造（component/claim/explanation/ledger/library）に
  反映しない。`source_backed` を自動付与しない。
- **W3 evidence-based**: すべての AI 出力に `evidence`（逐語引用・要素参照）+ `reason` +
  `confidence` を付ける。断定形にせず「〜の可能性」の仮説文体（D層 §0 と同じ）。
- **W4 情報を落とさない（P4）**: 対話ログ・候補注釈・却下は削除しない。status 遷移
  （`candidate → committed` / `candidate → dismissed`）で保持。行削除 API を作らない。
- **W5 権限 fail-closed（スコープで分岐）**: document-scoped 要素は `_ensure_document_viewable`
  / `_ensure_document_editable`（既存 `services.resolve_document_access`）。domain-scoped 共通部品
  （L層 library_entry）は **L層の権限モデル**（本文テキストは教員全体に開示、例示画像は由来
  document の権限を継承・非所有者 403）。いずれも fail-closed。
- **W6 同期パスを重くしすぎない**: 対話は教員起動の同期だが 1 応答=1 LLM コール上限、
  失敗時は非LLM（既存データ集約のみ）へ縮退。コスト上限は session/day で bound（§11）。
- **W7 監査必須**: セッション開始・候補生成・コミット・却下を `theory_review_events`
  （`entity_type='deliberation'`）に記録。
- **W8 数値を見せない**: `confidence` の生値・件数の生数字は UI に出さず段階ラベル
  （暫定 / 参考 / 確度高 等）で表示。教員向けでも他層と同じ流儀を守る。
- **W9 U層計測**: 新規 LLM 呼び出しは `core/llm.py` 経由なので U層計測は自動。feature 語彙は
  `deliberation:chat` / `deliberation:vision`。

---

## §1 スコープ（v1）

### 対象要素型（4つ）

| element_type | 実体 | 由来 |
|---|---|---|
| `figure` | 図画像 + 装置/部品候補 | `document_figures` + apparatus_semantics artifact |
| `theory_component` | 知識コンポーネント（TheoryOperationNode 含む） | `theory_components` / `theory_component_graphs` |
| `theory_claim` | claim（atomic claim 含む） | `theory_claims` |
| `equation` | 数式ブロック | `theory_claims.equation` + `stage_outputs`(equations.json) + graph の `linked_equation_ids` |

### 含めるもの

- 3つの面すべて（§3）: **内訳・同定** / **文脈的位置づけ** / **対話的検討**。
- 対話（マルチターン、text + figure は vision）まで含む。
- **共通部品（domain-scoped, `shared_part`）** も対象。複数論文にまたがる再利用部品は
  1論文に紐づけず L層 library_entry として扱い、対話・注釈はそこに蓄積する（§2 / §5）。
  上表の4型は「共通部品を見つける入口＝インスタンス」で、`shared_part` は「見つけた部品を
  共通化した先」。両者を同じワークスペースで繋ぐのが本層の主眼。

### 利用者

- **教員（TEACHER 以上）のみ**。学習者向け表示は v1 非スコープ（§14）。

---

## §2 核となる抽象: ElementRef と2つのスコープ

要素型ごとに UI/API/権限を作り直さないため、全要素を1つの多態参照で扱う
（既存 `object_group_permissions` / `shared_versions` のポリモーフィック集約と同じ思想）。
ただし **要素には2つのスコープがある**。これを分けないと「**共通化したい部品を1論文だけに
紐づける**」不整合が起きる（本層の設計上の要点）:

- **インスタンス（`scope='document'`）**: ある1論文から抽出された具体的な出現。
  figure / theory_component / theory_claim / equation。`document_id` に紐づく。
- **共通部品（`scope='domain'`）**: 複数論文にまたがって再利用したい抽象。**1論文に紐づけない**。
  分野（`domain_key`）に属し、複数の出現（instances）を provenance として束ねる。

```
ElementRef = (scope, element_type, element_id, anchor)
scope='document' → element_type ∈ {figure, theory_component, theory_claim, equation}, anchor = document_id
scope='domain'   → element_type ∈ {shared_part},                                     anchor = domain_key
```

**「共通化したい部品」の格納庫は L層 `library_entries`（`domain_key` スコープ・
`source_document_ids` / `source_component_ids` 複数）を正本とする**（W層は共通部品テーブルを
新設しない・§10）。`shared_part` の `element_id` は `library_entries.id`。W層は
「インスタンス→共通部品への昇格・リンク」導線（既存 L層 昇格経路・人間操作。同一性は非破壊リンク＝
§5.5/KN-2）と、共通部品そのものへの対話・注釈を提供する（§5）。

### element_id の解決（`core/deliberation/refs.py`）

**document-scoped**:

| element_type | element_id の実体 | 解決方法 |
|---|---|---|
| `figure` | `document_figures.id` | 直接 |
| `theory_component` | `theory_components.id` (UUID) | 直接 |
| `theory_claim` | `theory_claims.id` (UUID) | 直接 |
| `equation` | equations.json の `equation_id`（**テーブル無し**） | run の `stage_outputs.equations` を索く + 逆に `theory_claims.equation` / graph の `linked_equation_ids` から参照元を辿る |

**domain-scoped**:

| element_type | element_id の実体 | 解決方法 |
|---|---|---|
| `shared_part` | `library_entries.id` | L層 store（対話・retrieval が読むのは凍結版本文） |

> **設計上の注意**: 数式は独立テーブルを持たない。`equation` の ElementRef は
> 「document_id + equations.json 内 equation_id」で一意化し、resolver が `stage_outputs`
> （＝不変な run artifact）から本文・記号・導出リンクを組み立てる。将来テーブル化しても
> ElementRef の外形は変えない。

---

## §3 「深く検討する」の3つの面

| 面 | 内容 | 実装 |
|---|---|---|
| **① 内訳・同定** | この要素は何か／中に何が含まれるか | A層成果を読むだけ（§3.1） |
| **② 文脈的位置づけ** | この要素が置かれた文脈を4レンズで（§4） | 既存機構を合成（§10） |
| **③ 対話的検討** | ①②を根拠に AI と Q&A し、解釈を候補として産出（§5） | 新規（会話版 LLM/vision） |

### §3.1 内訳・同定（面①）

要素型ごとの内訳（すべて既存データの読み出し）:

- **figure**: apparatus/instrument/part 候補（`review_required` 系）、caption、近傍本文。
- **theory_component**: 構成 claim 群、関連数式、TheoryOperationGraph 上の node（stage /
  source_backing_status / review_reasons）、member/parent 関係。
- **theory_claim**: `claim_type` / `support_status` / `evidence_text` / `normalized_text` /
  `concepts` / 紐づく `equation` / atomic 性。
- **equation**: 本文、記号（SymbolRegistry）、導出チェーン上の入出力、参照する claim。

---

## §4 文脈的位置づけ（面②）— 4レンズ

`core/deliberation/positioning.py` が ElementRef を受け、以下4レンズを**合成して**返す。
各レンズは既存機構を呼ぶだけで、新しい抽出はしない（唯一の例外はコーパス横断・§4.2）。

### §4.1 論文内（intra-document）

要素が属するセクション・導出チェーン・thesis の位置。A層 structure（`stage_outputs` の
document_structure / derivation_chain / thesis）を辿る。

### §4.2 コーパス横断（cross-corpus）— **唯一の新下地**

「関連する論文・資料の中での位置づけ」の核。他 document の類似要素と出現箇所を返す。

- **問題**: `theory_components` / `theory_claims` に embedding 列が**無い**
  （embedding は `chunks` と `library_entry_versions` のみ）。
- **v1 方針（chunk-proxy）**: 要素→代表テキスト（claim.text / component.summary /
  equation の記号説明）→ **既存の chunk ベクトル検索**（`embedder.search_similar_papers` /
  `services` のベクトル検索）で近傍 chunk を引き、その `material_id`/`document_id` から
  「関連論文」を提示する。**新 migration 不要ですぐ動く**（粒度は粗い）。
- **将来（Phase 3）**: 精度が要れば `theory_claims` / `theory_components` に embedding 列を
  追加し要素↔要素の直接類似に置換（ElementRef 外形は不変）。
- 権限: 近傍 document のうち閲覧不可のものは `_ensure_document_viewable` で除外
  （fail-closed。件数の生数字は出さない・W8）。

### §4.3 分野の地図（field atlas）

要素（特に claim/component の概念）が atlas のどのノードに対応するか。既存
`atlas_state.build_concept_signals` / `load_corpus_snapshot` を再利用。骨格なし分野では
このレンズを非表示（atlas の fail-closed をそのまま継承）。

### §4.4 承認・疑義（social / epistemic）

- **C層**: この要素（component/explanation）を誰が承認・引用したか
  （`component_endorsements` / `component_citations` の集計。段階ラベルのみ）。
- **D層**: この要素の検証状態（`epistemic_ledger` の verification_status/scopes）、
  ついている疑義（`challenges`）、所属する暗黙前提（assumption_mining/corpus_audit）。

---

## §5 対話的検討（面③）

### 会話モデル

- 教員が要素を開いた状態で自由文の質問を送る。W層は面①②で集めた文脈を **system 相当の
  grounding**（要素本文 + 内訳 + 4レンズ要約）として LLM に注入し、応答を返す。
- **figure は vision**（画像 + caption + 近傍本文）。現状 `core/llm.py` の vision は
  `generate_structured_with_images()`（structured・1発）のみなので、**マルチターン会話版
  vision/text を新設**する（`core/llm.py`、v1 OpenAI 経路）。
- 応答は自由文（教員向け）だが、AI が「これは注釈化できる」と判断した箇所は
  **候補注釈（element_annotation, `status='candidate'`）** として構造化提案も返す
  （evidence/reason/confidence 付き・W2/W3）。

### 注釈の帰属先はスコープで決まる（本層の要点）

- **共通部品（domain-scoped）への注釈は分野全体に蓄積・再利用される**。1論文に紐づかないので、
  同じ部品が別論文に現れても同じ蓄積を参照できる（＝「共通化したい部品を1論文に閉じ込めない」）。
- **インスタンス（document-scoped）への注釈はその出現に固有**。ただし「これは共通化したい」と
  判断したら **インスタンス→共通部品への昇格・リンク**（既存 L層 昇格経路・人間操作）で
  domain-scoped に引き上げ、以降の対話・注釈は共通部品側へ貯める。これが本層の中心導線。
  cross-corpus レンズ（§4.2）が「この部品は論文 X/Y/Z にも出る」を示し、昇格・リンクの判断材料になる。
- **「統合」は非破壊のリンク追加であって、表現の置換ではない（KN-2/KN-3）**。既存の共通部品に
  「実は同じもの」を対応づけるときも、インスタンス側の label / notation / 文脈は**書き換えない**。
  同一性は `element_identity_links`（instance ↔ shared_part、Phase W-β）の**リンク**で表し、
  共通部品側は `local_expressions`（出所付き表現リスト）に「この論文ではこう書く」を追記するだけ。
  詳細は §5.5。

### 候補注釈の確定（コミット）

教員が候補注釈を「確定」すると、**既存構造へルーティング**する（W層独自の最終格納庫を持たない）:

| 注釈の種類（kind） | コミット先（既存） | scope |
|---|---|---|
| 共通部品の意味づけ・内訳（`meaning`/`decomposition`） | L層 `library_entries`（draft 更新→凍結。人間操作） | domain |
| インスタンスの意味づけ・内訳補正（`meaning`/`decomposition`） | `theory_components.body`/`summary` / apparatus 候補の精緻化 | document |
| 解釈バージョン（`interpretation`） | C層 `component_explanations`（`kind='personal'`） | document |
| 検証スコープ・疑義（`positioning_note` 起点） | D層 `epistemic_ledger.scope_candidates` / `challenges`（起動するだけ） | document |
| **同一性（`identity`）** | **`element_identity_links`（instance ↔ shared_part、W-β）＋ shared_part 側 `local_expressions` 追記。非破壊（KN-2）** | document→domain |
| **標準化判定（`standardization`）** | **shared_part（L層 library_entry）の `standardization_status`。LLM 直書き経路なし（L層ガードレール維持）** | domain |

- コミット権限はスコープで分岐（W5）。`source_backed` 自動付与なし（W2）。
- コミットしない候補・却下候補も `element_annotations` に status を残す（W4）。
- **`identity` / `standardization` は常に candidate 始まり・人間確定のみ（KN-3）**。確定・却下は
  status 遷移で保持し（P4）、`theory_review_events`（`entity_type='deliberation'`）に監査記録する。

### §5.5 同一性・標準化と非破壊リンク（W-α 反映）

ビジョン §3 修正②③・KN-2/KN-3 を本層の語彙で確定する。W層は同一性・標準化の**候補提示器**であり、
確定の格納庫（identity link・library_entry）は既存の domain-scoped 実体を使う（新設しない・§10）。

- **同一性（`kind='identity'`）**: 「論文 A の component_x と論文 B の equation_y は実は同じ共通部品」
  という対応を、**インスタンス側を書き換えず**リンクで表す。対話・cross-corpus レンズ（§4.2）が
  候補を提示し、教員が確定すると `element_identity_links`（instance ↔ shared_part、Phase W-β の
  テーブル。旅の traversal のため JSONB でなくテーブル）に1本追加し、共通部品側の
  `local_expressions` に「この論文での label / notation / 文脈」を追記する。**既存の表現は潰さない**。
  対象は当面ハブ経由（instance ↔ shared_part）に限定する（ビジョン §8 未決2。instance ↔ instance の
  直接リンクは将来判断）。
- **標準化判定（`kind='standardization'`）**: 「この共通部品は教科書級の標準か / 分野内標準か /
  コーパス内でのみ反復する未発見の共通パーツ候補か」を三角測量（LLM 事前知識 + L層凍結版類似 +
  コーパス内反復）で候補化し、`standardization_status`（`standard` / `field_standard` /
  `emerging_common` / `novel` / `unknown`）＋証拠＋reason＋confidence を注釈に持つ。判定 worker 本体は
  ビジョン Phase S（llm_worker 6系統目アダプタ）で実装し、W層はその候補の**置き場と確定 UI**を担う。
  `emerging_common` が発見的価値の在り処（ビジョン §3 修正③）。**LLM が library_entry へ直接書く
  経路は作らない**（L層既存ガードレール維持）。教員確定で library_entry の `standardization_status`
  へ反映する。

---

## §6 データモデル（migration 046）

W層専用は2枚のみ。成果テーブルには列を足さない（W1）。

### `deliberation_sessions`（対話セッション）

```
id             UUID PK
scope          TEXT  CHECK (scope IN ('document','domain'))  NOT NULL
element_type   TEXT  CHECK (element_type IN ('figure','theory_component','theory_claim','equation','shared_part'))
element_id     TEXT  NOT NULL              -- ElementRef（equation=equations.json id / shared_part=library_entries.id）
document_id    TEXT                        -- scope='document' のとき正規化済み。'domain' は NULL
domain_key     TEXT                        -- scope='domain' のとき分野。'document' は NULL
title          TEXT  DEFAULT ''
messages       JSONB DEFAULT '[]'          -- [{role, content, grounding_ref?, created_at}]（P4: 追記のみ）
created_by     UUID  REFERENCES users(id)
created_at / updated_at  TIMESTAMPTZ
```

FK は `element_id` に張らない（ポリモーフィック）。孤児掃除は scope で分岐:
- `scope='document'`: document 削除経路（`_purge_document` / `purge_object`）が明示 DELETE
  （V層 orphan gap と同じ扱い）。
- `scope='domain'`: L層 library_entry のライフサイクルに従う（library は削除せず `retired` 遷移
  なので、W層行も残す＝P4）。

### `element_annotations`（候補注釈・確定注釈）

```
id             UUID PK
scope          TEXT  CHECK (scope IN ('document','domain'))  NOT NULL
element_type   TEXT  (同上 CHECK・shared_part 含む)
element_id     TEXT  NOT NULL
document_id    TEXT                        -- scope='document' のみ
domain_key     TEXT                        -- scope='domain' のみ
session_id     UUID  REFERENCES deliberation_sessions(id) ON DELETE SET NULL
kind           TEXT  -- 'meaning' | 'decomposition' | 'positioning_note' | 'interpretation'
                     --      | 'identity' | 'standardization'（W-α で追加。§5.5）
body           JSONB DEFAULT '{}'          -- identity: {shared_part_id, local_expression{label,notation,context}}
                                           -- standardization: {standardization_status, claimed_canonical_name?, evidence_kinds[]}
evidence       JSONB DEFAULT '[]'          -- 逐語引用・要素参照
reason         TEXT  DEFAULT ''
confidence     REAL                        -- 生値は DB のみ・API はラベル（W8）
status         TEXT  CHECK (status IN ('candidate','committed','dismissed'))  DEFAULT 'candidate'
committed_target JSONB DEFAULT '{}'        -- コミット先（component/explanation/ledger/library/identity_link の id）
created_by / updated_by  UUID
created_at / updated_at  TIMESTAMPTZ
```

削除 API 無し（W4）。`candidate → committed / dismissed` の遷移のみ。`identity` / `standardization`
の確定先は §5.5 のとおり（それぞれ `element_identity_links`＋`local_expressions` /
library_entry の `standardization_status`）。いずれも LLM 直書きなし・人間確定のみ（KN-3）。

### 監査カタログ拡張

`core/schema.py` に `AUDIT_ENTITY_DELIBERATION = "deliberation"` を追加し `AUDIT_ENTITY_TYPES`
に登録（既存27→28語彙）。

---

## §7 core モジュール構成

`backend/core/deliberation/`（**FastAPI 非 import**・開発ルール2）:

```
__init__.py
refs.py          → ElementRef 解決（要素型ごと resolver。equation は stage_outputs 索き）
positioning.py   → 4レンズ合成（§4。既存 atlas/C層/D層/embedder を呼ぶだけ）
decomposition.py → 面①内訳の組み立て（要素型ごと）
dialogue.py      → grounding 構築 + 会話1ターン（llm_worker 基盤 + 会話版 vision）
annotations.py   → 候補注釈の生成・status 遷移・コミットルーティング（§5）
store.py         → deliberation_sessions / element_annotations の DB プリミティブ
schema.py        → 語彙・dataclass の正本
```

- 会話は llm_worker の `client` を使いつつ、**同期・マルチターン**なので `run_with_repair` は
  構造化提案部分にのみ適用（自由文応答は非構造）。コスト上限は `CostGate`（§11）。
- 集約系（positioning/decomposition）は非LLM。W6 の縮退先＝この非LLM 集約のみ。

---

## §8 API（`backend/api/routes/deliberation.py`、実パス `/api/admin/deliberation/...`、`_require_teacher`）

| メソッド・パス | 役割 |
|---|---|
| `GET /elements/{element_type}/{element_id}/overview` | 面①内訳 + 面②位置づけの集約（**非LLM・DB非変更**。Phase 0 の主役） |
| `POST /sessions` | 対話セッション開始（ElementRef 指定） |
| `GET /sessions/{id}` | セッション取得（messages 込み） |
| `POST /sessions/{id}/messages` | 1ターン送信 → 応答 + 候補注釈（1 LLM コール・W6） |
| `GET /elements/{element_type}/{element_id}/annotations` | 候補/確定注釈一覧（confidence はラベル・W8） |
| `POST /annotations/{id}/commit` | 確定 → 既存構造へルーティング（`_ensure_document_editable`） |
| `POST /annotations/{id}/dismiss` | 却下（status 遷移・保持） |

- overview / 一覧は `_ensure_document_viewable`、commit は `_ensure_document_editable`。
- 図の実画像は既存 `GET /api/admin/documents/{id}/figures/{fid}/image` を流用（権限ゲート済み）。

---

## §9 フロント（`frontend/public/js/deliberation.js`、ES5・`window.Deliberation`）

- 管理画面（教材詳細の図ペイン、原稿スタジオのチャンク/セクション論理要素カード・
  主張一覧・「選択中コンポーネント」ビュー）の4要素型すべてに
  **「深く検討」ボタン** → 右ペイン/モーダルで W層ワークスペースを開く。
- レイアウト: 左=**内訳＋4レンズ位置づけ**（overview API）、右=**対話**（sessions API）。
- 候補注釈は対話下にカード表示 → `[確定]`（commit）/ `[却下]`（dismiss）。
- 既存の詳細表示（`admin.js` の図モーダル・revisions 画面、`admin-lecture-studio.js`
  （原稿スタジオ）の component graph / claims / theory 一覧）は**壊さず**、そこから
  W層への入口を足す（Phase 0 は overview の統合表示のみで価値が出る）。theory_component /
  theory_claim は DB UUID（`theory_components.id` / `theory_claims.id`）が手に入る原稿
  スタジオの画面を導線に使い、revisions 画面の変更差分一覧（`entity_id` が pipeline
  内部 id で DB UUID と一致する保証がない）には導線を出さない（equation のみ例外。
  equation は独立テーブルを持たず artifact 上の `equation_id` をそのまま ElementRef に
  使う設計のため、この不一致が生じない）。
- **数値を出さない**（W8）: confidence・関連件数はラベル/レンジ表示。

---

## §10 既存層との合成（重複させない）

W層の新規価値は **①統合入口（4要素型を1つの場に）②コーパス横断の要素位置づけ ③対話ループ**
の3点に限定する。他はすべて既存機能を **surface（呼び出して見せる）** だけにする:

- 「検証されていない」と気づいたら → **D層の challenge / verification proposal を起動**（新設しない）
- 「この解釈を承認・共有」 → **C層 explanation / endorsement**
- 「分野の別テーマと繋がる」 → **atlas のリンク**
- 「分野知識に昇格 / 共通部品として蓄積」 → **L層の既存昇格経路（人間操作）**。
  **共通部品（domain-scoped ElementRef）の実体は L層 library_entry であり、W層は独自の
  共通部品テーブルを作らない**（§2）。domain-scoped 対話・注釈も library_entry を軸に貯まる。

これを守らないと D層/C層/L層と機能が二重化する。W層は**ハブ**であって格納庫ではない。

---

## §11 コスト・権限・計測

- **コスト上限**: `DELIBERATION_MAX_CALLS_PER_SESSION`（既定 8）/ `DELIBERATION_MAX_CALLS_PER_DAY`
  （既定 40、他機能と独立）。`llm_worker.cost_gate.CostGate` を再利用。モデルは fast tier 既定
  （`DELIBERATION_LLM_MODEL` で上書き）。
- **権限**: 全経路 `_require_teacher` + document 単位ゲート（W5）。commit は editor 以上。
- **U層**: `usage_context(feature='deliberation:chat'|'deliberation:vision', user_id, document_id)`
  を dialogue 呼び出し前にセット（W9）。
- **監査**: `entity_type='deliberation'` で session 開始 / 候補生成 / commit / dismiss を記録（W7）。

---

## §12 ガードレール（`backend/tests/test_deliberation_guardrails.py`）

`guardrail_helpers.py` を使い構造的に守る:

- `core/deliberation/` が FastAPI を import しない。
- AI 出力が常に candidate（commit 前に `source_backed` を付けない）。
- 削除 API が存在しない（status 遷移のみ・W4）。
- overview / annotations が document 権限ゲートを通す（fail-closed・W5）。
- confidence の生値を返す API/UI 経路が無い（W8）。
- コーパス横断レンズが閲覧不可 document を除外する。
- A層コード（`src/episteme_graph/agents/`）に差分が無い（W1）。
- 監査 entity_type がカタログ定数を使う。

---

## §13 issue 分割（実装フェーズ）

- **Phase 0（W-0）**: ElementRef + `refs.py` + `decomposition.py` + `positioning.py`（§4.1/4.3/4.4
  ＝既存データ合成のみ）+ `GET .../overview` + フロント統合パネル。**新 LLM・新 migration 無し**、
  4要素型の「内訳＋位置づけ」を1画面に束ねるだけで即価値。
- **Phase 1（W-1）**: コーパス横断レンズ（§4.2、chunk-proxy）。
- **Phase 2（W-2）**: migration 049（`deliberation_sessions` / `element_annotations` の2テーブル・
  scope 分岐。起草時は 046 を想定したが冒頭の補正注記どおり実装は 049）+ 対話（会話版 vision/text）+
  候補注釈（`identity` / `standardization` を含む・§5.5）+
  **インスタンス→共通部品（L層 library_entry）への昇格・リンク導線（非破壊・KN-2）+ domain-scoped
  共通部品への対話・注釈** + コミットルーティング + 監査 + コスト上限 + ガードレール。
- **Phase W-β**: `element_identity_links`（instance ↔ shared_part、candidate/confirmed/rejected、
  evidence、確定者）を migration 048 で追加（起草時は 046 同乗を想定したが冒頭の補正注記どおり
  実装は 048）。旅の traversal のため JSONB 埋め込みでなくテーブルで持つ
  （ビジョン §7）。`identity` 注釈の確定先。
- **Phase 3（W-3・任意）**: 要素粒度 embedding へ置換（§4.2 将来）。

---

## §14 非スコープ（v1）

- 学習者向け表示・学習者の対話（教員向けから。B層のプライバシー規定を別途載せる必要があるため
  別 issue）。
- 要素粒度 embedding テーブル（Phase 3 まで chunk-proxy で近似）。
- W層独自の最終格納庫（コミットは既存構造へ返す・§5）。
- TheoryOperationGraph への W層由来ノード追加（グラフ構造は A層のまま）。
- リアルタイム自動対話・バッチ対話（対話は教員起動の同期のみ）。

---

## §15 未決事項

1. **コミット先ルーティングの粒度**: 候補注釈の種類→既存構造の対応（§5 表）を v1 で全部繋ぐか、
   まず component.body と C層 explanation の2経路だけにするか。
2. **コーパス横断の representative text 生成**: claim/component/equation の代表テキストの作り方
   （非LLM で連結 vs 要約 LLM）。W6 的には非LLM 連結が無難。
3. **overview のキャッシュ**: 4レンズ合成は重くなり得る。atlas overlay cache のように
   コーパス signature でキャッシュするか、都度計算か（Phase 0 は都度計算で開始でよい）。
4. **domain-scoped ref の対象範囲**: v1 の共通部品を L層 library_entry のみとするか、D層
   `assumption_nodes`（論文横断の暗黙前提）や atlas concept も `scope='domain'` の ElementRef
   として開くか。まず library_entry のみで開始し、必要になれば assumption / concept を足すのが
   無難（いずれも既存の domain-scoped 実体なので ElementRef 外形は不変）。
5. **昇格 vs 統合の既定挙動**: インスタンスを共通部品化するとき、新規 library_entry 作成と
   既存エントリへの統合（source_document_ids 追記）のどちらを既定提示にするか。cross-corpus
   類似ヒットがあれば統合を優先提示するのが自然。
