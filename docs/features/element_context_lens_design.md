# 要素中心コンテキストビュー（Element-Centered Context Lens）設計

> **状態: Phase 0〜3 実装済み（2026-07-18）**（Issue [#498](https://github.com/dx-junkyard/episteme-graph/issues/498)）。
> 親設計書: [W層（Element Deliberation Workspace）設計](element_deliberation_workspace_design.md)。
> 本書は W層で要素を開いたときに、選択要素を中心として上位構造・文脈上の役割・下位構造を一体表示するための追補設計である。

---

## 0. 目的

図、理論コンポーネント／理論的概念、Claim、数式を参照するとき、読み手が次の三つを同時に理解できるようにする。

1. **Why — 上位構造**: この要素は論文全体でなぜ重要か。中心命題、支持構造、親コンポーネント、節、上位 Claim のどこに位置するか。
2. **What — 選択要素**: この要素自体は何か。この論文・図・導出の文脈で何を担うか。
3. **How — 下位構造**: 何から成り立つか。部品、定義、前提、証拠、記号、導出ステップをどう辿れるか。

単なる巨大グラフを表示する機能ではない。**選択中の一要素を中心に、意味を持つ関係だけを上下1階層ずつ投影する読解UI**である。

---

## 1. 背景と現状のギャップ

W層は既に、要素の内訳（decomposition）と文脈的位置づけ（positioning）を表示できる。しかし、両者は独立した成果として扱われ、要素型ごとの情報量に非対称がある。

- Claim と数式は、導出チェーンや thesis reconstruction の関係を一部表示できる。
- 図は掲載セクション・ページを表示できるが、中心命題や支持する Claim への経路を一般には表示できない。
- 図のパーツは局所的な機能を示せても、親図・実験系・論文主張の中での役割を保ったまま選択できない。
- 図の Vision 解析は画像、caption、近傍本文、同一セクション、図中ラベルを対象とする。論文全体の骨格や thesis は直接入力されず、thesis reconstruction は図解析の後段で生成される。

したがって、局所的な「何であるか」は分かっても、読者にとって重要な「なぜここにあり、何を支えるか」が欠ける。

---

## 2. 不変の設計原則

### 2.1 固有情報と文脈依存情報を分ける

同一の概念、数式、部品でも、論文・図・節によって役割は変わる。そのため、論文内の役割を共通部品や要素本体の固定属性として複製してはならない。

| 区分 | 例 | 所属先 |
|---|---|---|
| 固有情報 | 名称、定義、一般的機能、Claim 本文、数式の意味、入出力 | 要素またはその既存成果 |
| 文脈依存情報 | この図での役割、支える Claim、導出上の位置、中心命題との関係 | 要素の出現・要素間関係・W層の導出投影 |

文脈依存情報は、上位成果物の更新で変化し得る。導出元の run、revision、evidence を追跡できることが必要である。

### 2.2 AI の解釈を原文事実に昇格させない

既存の W1〜W8 と KN-1〜KN-4 を継承する。

- PDF 原文や既存構造から決定論的に辿れる関係と、AI が提案する関係を明確に区別する。
- AI が提案する文脈上の役割は candidate であり、source-backed と自動表示しない。
- 関係が得られない場合は「上位構造との関係は未同定」と表示する。推測で穴埋めしない。
- A層成果を W層のために書き換えない。W層は既存成果を読み、必要なら候補注釈・人間確定の既存経路を使う。

### 2.3 読解の開始点を失わせない

初期表示で全階層を展開しない。読み手は常に「今どの要素を読んでいるか」を把握できる必要がある。

- 選択要素を常に中央に置く。
- 上位・下位は初期状態で各1階層までにする。
- 隣接ノードを選ぶと、そのノードを新しい中心に再配置する。
- パンくず／直前の要素へ戻る履歴を維持する。

---

## 3. 共通UI

```
上位構造（Why）
  中心命題 / 支持構造 / 上位Claim / 親コンポーネント / セクション
                 ↓ 関係を動詞で表示

選択要素（What）
  要素固有の説明 | この文脈での役割 | 根拠・確認状態

                 ↓ 関係を動詞で表示
下位構造（How）
  部品 / 定義 / 前提 / 記号 / 導出 / 図 / 数式 / Evidence
```

### 3.1 中央カード

中央には次を別欄で並べる。

- **要素自体**: 定義、名称、数式の自然言語説明、Claim 本文、図全体の説明など。
- **この文脈での役割**: 「Claim C12 を定量化する」「中心命題を直接支持する」「測定可能性を担保する」のような関係文。
- **根拠・状態**: caption／本文／Claim／Equation などへの出典、AI候補か教員確認済みか、未同定か。

### 3.2 関係の見せ方

関連ノードを名詞の一覧として並べない。関係の意味を動詞で示す。

| 表示例 | 意味 |
|---|---|
| Figure 4 **が証拠を与える** Claim C12 | 図から主張への根拠経路 |
| Equation 8 **が定量化する** Claim C12 | 式から主張への意味経路 |
| Concept X **が説明する** Model M2 | 概念からモデルへの説明経路 |
| Claim C12 **が直接支持する** Thesis T1 | Claim から論文全体への支持経路 |
| EOM **が変調を担う** Figure 4 の測定系 | パーツから親図への機能経路 |

エッジには、根拠を開く導線を置く。根拠は PDF 上の位置、caption、本文、Claim、数式、導出ステップのいずれかへ戻れること。

---

## 4. 要素型ごとの投影

### 4.1 図と図中パーツ

| 領域 | 表示する内容 |
|---|---|
| 上位構造 | 掲載セクション、図が支える／説明する Claim、所属する理論・実験コンポーネント、中心命題・支持構造との関係 |
| 選択要素 | 図全体の説明、論文内で図が果たす役割、presentation mode、AI候補／教員確認状態、caption・参照本文 |
| 下位構造 | パネル、部品、機能、入出力、接続・信号経路、軸、系列、観測点、注目領域 |

図中パーツを選んだ場合、親図を上位に残す。そこから図の役割、関連 Claim、中心命題まで辿れるため、パーツ単体の一般機能と当該論文での役割を混同しない。

### 4.2 理論コンポーネント／理論的概念

| 領域 | 表示する内容 |
|---|---|
| 上位構造 | 親コンポーネント・理論モデル、説明対象の現象、関連 Claim、中心命題との関係 |
| 選択要素 | 定義・概要、この論文での役割、成熟度・レビュー状態・根拠 |
| 下位構造 | 定義、前提、適用条件、性質、入出力、内部フロー、関連数式、派生概念、根拠 Claim |

### 4.3 Claim

| 領域 | 表示する内容 |
|---|---|
| 上位構造 | 上位 Claim、中心命題、support structure 上の位置、掲載セクション、理論コンポーネント |
| 選択要素 | 原文と正規化された単一命題、「主要結果」「前提」「制約」「不確実性」等の文脈上の役割、support／review／verification 状態 |
| 下位構造 | Evidence、前提 Claim・サブ Claim、図、数式、導出ステップ、反例・疑義・不確実性 |

### 4.4 数式

| 領域 | 表示する内容 |
|---|---|
| 上位構造 | 所属する導出チェーン、定量化・定義・支持する Claim、理論コンポーネント、中心命題との関係 |
| 選択要素 | 数式と自然言語での意味、この論文でこの式が必要な理由、semantic／reconstruction／review 状態 |
| 下位構造 | 記号、単位、前提、境界条件、先行式、変形ステップ、入力・出力・近似 |

---

## 5. 関係投影のデータ契約

overview と dialogue grounding の双方で同じ情報を使うため、要素型非依存の読み取り契約を定義する。実体テーブルをこの外形に無理に合わせるのではなく、既存成果から投影する。

```json
{
  "focus": {
    "element_type": "figure",
    "element_id": "...",
    "label": "...",
    "intrinsic_summary": "...",
    "contextual_role": "Claim C12 の測定可能性を担保する実験系",
    "contextual_role_status": "candidate",
    "provenance": ["caption_block_id", "claim:C12"]
  },
  "upper": [
    {
      "element_type": "theory_claim",
      "element_id": "...",
      "label": "Claim C12",
      "relation": "provides_evidence_for",
      "relation_status": "source_backed",
      "evidence_refs": ["..."]
    }
  ],
  "lower": [
    {
      "element_type": "part",
      "element_id": "...",
      "label": "EOM",
      "relation": "contains",
      "relation_status": "candidate",
      "evidence_refs": ["..."]
    }
  ]
}
```

`relation` は内部語彙であり、UI は「証拠を与える」「定量化する」「構成する」のような読み手向け表現へ変換する。`relation_status` は根拠状態の表示・AI候補のガードに使い、数値スコアは表示しない。

---

## 6. 実装方針

### Phase 0 — 読み取り投影の統一

既存の `decomposition.py` と `positioning.py` を読み取り専用で拡張し、Claim・数式・コンポーネントについて既存の section／derivation／thesis 関係を共通契約へ投影する。

- 既存の W層4レンズを廃止せず、論文内レンズの表示モデルを「上位／下位／中心」に再構成する。
- artifact 欠損、旧run、関係なしは空のレーンまたは事実文へ縮退する。
- 既存の raw data／根拠表示を維持する。

### Phase 1 — 図・パーツの文脈経路

図について、caption block、FigureRecord、関連 Claim、Component、thesis の間に存在する根拠付き経路を読み取り投影する。

- caption 由来の Claim link や source scope が明示する関係は、根拠付きの候補として表示できる。
- 図の役割をAIが補う場合は、`candidate` として W層の既存注釈・人間確定フローに従う。
- パーツ選択では `part → parent figure → upper path` を必ず残す。
- A層の Vision 入力へ論文全文を常時追加しない。論文全体との意味づけは thesis reconstruction 後の W層読み取り投影で行う。

### Phase 2 — 中心移動と対話 grounding

- `deliberation.js` に共通の要素中心レイアウトを導入する。
- 上位／下位の隣接ノード選択で ElementRef を切り替え、履歴を保持する。
- `dialogue.py` の grounding へ、表示中と同じ focus／upper／lower を入れる。
- 回答は利用した根拠・関係を読み手が確認できる形式にする。

### Phase 3 — 人間確定した文脈上の役割の再利用

教員が候補注釈を確定した場合のみ、既存の W層注釈・C層 explanation・L層ライブラリ等の正規の格納先へルーティングする。文脈に固有の役割を共通部品の固定属性へ昇格しない。

---

## 7. 非対象

- Visionモデルへ論文全文を無条件に投入すること。
- 文脈上の役割を共有部品・概念の固定属性として保存すること。
- 初期表示で論文全体を巨大グラフとして描画すること。
- AI推論を source-backed な事実として扱うこと。
- 上位関係を得られない要素を非表示にすること。

---

## 8. 受け入れ条件

### 共通

- [ ] 図、理論コンポーネント／概念、Claim、数式の4種類で共通の要素中心ビューを使える。
- [ ] 選択要素の固有説明と文脈上の役割を別欄で表示する。
- [ ] 上位構造と下位構造を同時に最低1階層表示する。
- [ ] 関係を動詞で表示し、根拠へ辿れる。
- [ ] 隣接ノードを選択して中心を移動でき、直前の要素へ戻れる。
- [ ] 原文根拠、AI候補、人間確認済み、未同定を区別する。
- [ ] 関係なし・artifact欠損・旧runでも局所表示が壊れない。

### 図・パーツ

- [ ] 図から関連 Claim／Component／Thesis への経路を表示できる。
- [ ] パーツ選択時も、親図と上位 Claim への経路を参照できる。
- [ ] 図の局所 Vision 解析と論文全体での文脈付けを区別する。

### 対話・テスト

- [ ] 対話の grounding に、画面と同じ上下関係を含める。
- [ ] 4要素型の投影契約、縮退、根拠状態、中心移動、groundingをテストする。

---

## 9. 実装ノート（2026-07-18）

Phase 0〜3 を実装した。実装ファイルは以下のとおり。

- `backend/core/deliberation/context_lens.py`（新規） — 読み取り専用・非LLM・FastAPI 非 import。
  既存 A層成果物（thesis_reconstruction / derivation_chain / equation_semantics /
  figure_table_semantics / claim_object_builder / symbol_registry の各 artifact、
  `theory_component_graphs.graph_json`、`theory_claims` / `theory_components` /
  `document_figures` テーブル、committed な `element_annotations`）を横断し、選択要素を
  中心に上位・下位を各1階層だけ決定論的に投影する。`RELATION_LABELS`（内部語彙 → 日本語
  動詞句のマッピング）はこのファイル内に持つ。
- `backend/core/deliberation/schema.py` — `CONTEXT_STATUS_SOURCE_BACKED` /
  `CONTEXT_STATUS_CANDIDATE` / `CONTEXT_STATUS_CONFIRMED` /
  `CONTEXT_ROLE_STATUS_UNIDENTIFIED` を語彙定数として追加。
- `backend/api/routes/deliberation.py` — 既存
  `GET /api/admin/deliberation/elements/{type}/{id}/overview` のレスポンスに `context`
  キーを追加した（新規エンドポイントは作らない）。権限は既存 `_ensure_document_viewable`
  のまま（投影は同一 document 内に閉じるため追加ゲートは不要）。
- `backend/core/deliberation/dialogue.py` — `build_grounding` に `context` を追加し、
  `grounding_to_text` が画面と同じ上位/下位関係（`relation_label` + 状態ラベル）を
  プロンプトへ整形する。応答が利用した根拠・関係を明示するよう instruction header に
  追記した。
- `frontend/public/js/deliberation.js` — 共通の要素中心レイアウト
  （`#deliberation-context-lens`: 上位レーン → 中心カード（要素自体／この文脈での役割／
  根拠・状態の3別欄）→ 下位レーン）、状態バッジ（原文根拠／AI候補／教員確認済み／未同定）、
  `.deliberation-context-nav` ボタンによる中心移動（`_navigateToElement` — モーダル
  非破棄・チャット状態は新要素へリセット）、`#deliberation-breadcrumb` パンくず + 戻るを
  追加した。
- `frontend/public/css/styles.css` — 上記要素のスタイル定義を追加した。
- テスト: `backend/tests/test_deliberation_context_lens.py`（投影契約・縮退・根拠状態・
  語彙完全性）、`test_deliberation_ui_static.py` の `TestElementContextLens`
  （中心移動・パンくず・escHtml・未同定文言）、`test_deliberation_api.py` /
  `test_deliberation_guardrails.py` の拡張（overview の `context` キー・
  `context_lens.py` の FastAPI 非 import・`"confidence"` 文字列禁止）。

### 確定した契約

overview レスポンスの `context` は次の形で返る。

```json
"context": {
  "available": true,
  "focus": {
    "element_type": "...", "element_id": "...", "document_id": "...",
    "label": "...", "intrinsic_summary": "...",
    "contextual_role": "...", "contextual_role_status": "candidate",
    "provenance": []
  },
  "upper": [
    {
      "element_type": "...", "element_id": "...", "document_id": "...",
      "label": "...", "relation": "provides_evidence_for",
      "relation_label": "が証拠を与える", "relation_status": "source_backed",
      "evidence_refs": [], "navigable": true
    }
  ],
  "lower": ["同形"],
  "notes": []
}
```

- `relation` は内部語彙、`relation_label` は日本語動詞句（主語は常に焦点要素）。数値
  スコアは出さない（W8）。
- `relation_status` の判定則: A層の明示リンクは `source_backed` / `inferred_*` や
  `*_candidates`・vision 由来の関係は `candidate` / 教員確定（committed 注釈・confirmed
  同一性リンク・reviewed 解析）は `confirmed`。
- 上位・下位の各レーンは上限20件。超過分は黙って切り捨てず `notes` に件数を記録する。
- 上位関係が1件も得られない要素は `contextual_role_status="unidentified"` とし、推測で
  埋めない。
- `shared_part`（L層共通部品）は投影対象外で、`{"available": false, "note": "..."}` に
  縮退する。
- 既存の W層4レンズのうち「論文内レンズ」の表示のみ、この context ブロックへ置き換えた。
  他3レンズ（コーパス横断／分野の地図／C層承認・D層疑義）は変更していない。

### §5 契約例との差分

§5 の契約例は設計時点の概形であり、実装では以下を追加している。

- `relation_label`（UI表示用の日本語動詞句。§5 時点は `relation` のみで変換は UI 側の
  想定だったが、実装では投影側が `RELATION_LABELS` で確定して返す）。
- `navigable`（上位／下位ノードが中心移動可能かどうかを明示するフラグ）。
- `notes`（レーン上限超過など、投影が省略した情報の正直な告知）。
- `available`（focus 自体が投影不能な場合の縮退フラグ。§5 は前提としていた）。
- `contextual_role_status` の語彙に `unidentified` を追加した（§2.2 の「上位構造との
  関係は未同定」という文言を状態値として構造化したもの）。

---

## 10. 実装ノート（2026-07-21）— コンポーネントの上位リンク拡充（B）

Phase 0 のコンポーネント投影は、上位構造を **TheoryOperationGraph
（`theory_component_graphs`）由来の関係だけ**（`parent_component_id` の `member_of` /
`is_thesis_anchor` / 入辺 `used_by_component`）から導出していた。ComponentAssembly が
組み立てた `theory_components` はグラフノードとして登録されないことが多く、その結果
`focus.provenance` に `component_graph` が付かないコンポーネントは上位が空になり、
「上位構造との関係は未同定」ばかりが並んでいた。§4.2 が求める「中心命題との関係」が
実際には出ていなかった。

これを解消するため `_build_component` に本流（中心命題・支持構造・掲載セクション）への
接続を追加した。**A層は非改変**（読むだけ・W1 継承）で、既に DB に保存済みの
`theory_components.thesis_context` を読むだけである。

- **主軸: `thesis_context.supports_thesis_node_ids` → 中心命題 / 支持構造**。
  component_assembly が claim/equation の重なりから決定論的に導出したノード参照
  （`persistence.py` の `_thesis_ref_nodes` と同一の `central_thesis` /
  `support:<section>:<idx>` 語彙、#354/#440）を thesis_reconstruction artifact で解決し
  `supports_thesis`（`source_backed`）の上位項目にする。グラフノードでなくても本流に
  結びつく。artifact 欠損・未解決ノードは ID をラベルにして残す（P4・推測で埋めない）。
  グラフ由来の `is_thesis_anchor` 項目もラベルを headline に統一して `_dedupe_items` で
  中心命題項目と重複排除する。
- **`role_in_thesis` を「この文脈での役割」に採用**。`_derive_contextual_role` に
  `fallback_role` を追加し、優先順位を「人間確定注釈 > `role_in_thesis` > 上位項目からの
  機械文 > unidentified」にした（fallback は component のみ渡す。他要素型は不変）。
  `supports_thesis_node_ids` が空でも `role_in_thesis` があれば役割を出し、未同定を脱する。
- **副軸: 掲載セクション**。`source_chunks` → `chunks.section_id` → `document_structure`
  のセクション見出しを `appears_in_section`（`source_backed`）で出す。見出しが引けない
  section_id は本流の位置づけノイズになるため出さない（best-effort・fail-soft）。
- **provenance** に `thesis_context` / `chunks` を追記し、上位が何由来かを正直に示す。

実装・テスト:

- `backend/core/deliberation/context_lens.py` — `_load_component_row` に `thesis_context`
  / `source_chunks` を追加、純粋ヘルパ `_thesis_context_upper_items` /
  `_section_items_from_ids` と DB ヘルパ `_chunk_section_ids` を新設、
  `_derive_contextual_role` に `fallback_role`/`fallback_status` を追加、`_build_component`
  に上記の配線を追加。`RELATION_LABELS` は新規語彙なし（`supports_thesis` /
  `appears_in_section` を再利用）。
- `backend/tests/test_deliberation_context_lens.py` — `TestThesisContextUpperItems` /
  `TestSectionItemsFromIds`、`TestDeriveContextualRole` の fallback ケース、
  `TestBuildComponentUpperFromThesisContext`（グラフノード無し + thesis_context ありで
  上位・役割が出る／`role_in_thesis` 単独でも未同定を脱する／両方無ければ従来どおり
  未同定を維持）を追加。
- フロントは変更不要（上位レーンは項目を汎用にレンダリングするため、埋まった時点で
  「未同定」文言が消える）。

### 同日追補（2026-07-21・第2弾）— theory stage 接続 + evidence_claims 本文表示

§10 冒頭時点で「未対応（別課題）」としていた2件も同日中に実装した。

- **theory stage（main 層）ノードへの接続**: コンポーネントがグラフノードでない
  （`node is None`）場合、`graph_json` の `graph_layer=="main"` ノード（theory stage、
  label は "Theory basis" 等の stage label）の `linked_claim_ids` と component の
  `evidence_claims` を、両辺 `claim_lookup.get(x, x)` で正規化してから交差し、交差した
  main ノードごとに上位項目を出す（`_stage_participation_items`・純粋関数）。
  - graph_json の node `linked_claim_ids` は persist 時に **agent 側 claim ID のまま**
    （remap されない）、component 側 `evidence_claims` は remap 済み DB UUID、という
    非対称を lookup 正規化で吸収する。main ノード id（`theory_op_XXXX`）は
    theory_components 行に対応しないため**非ナビゲーション**（`element_type="stage"` を
    ITEM 契約に追加。`_NAVIGABLE_ELEMENT_TYPES` は不変）。
  - relation は新語彙 `participates_in_stage`（「の理論段階に関与する」）。
    `relation_status` は **candidate**: claim 交差は決定論的だが A層の明示リンクではない
    （figure レンズの `related_component_candidates` と同じ扱い）。`evidence_refs` に
    交差 claim ID の先頭3件を積んで追跡可能にする。
  - node が存在する場合は出さない（親 main ノードが `member_of` で既出のため）。
- **下位 `evidence_claims` の claim 本文表示**: `evidence_claims` は「remap 済み DB UUID
  （親 claim）」と「agent 側 atomic sub-claim ID（`claim_span_001_sub01` 形式。
  theory_claims 行にならないため remap されず素通り）」の混在。従来はループ内 N+1 +
  引けない ID の生ID表示だった。変更後: ①`claim_lookup` で解決できた ID を**1回の
  バッチ** `_claims_by_id` で取得し本文 + navigable ②解決不能 ID は claim_object_builder
  artifact の `claims[]`（`claim_id` → `text`/`normalized_text`、
  `_artifact_claim_text_index`）でラベル補完（非 navigable・`evidence_refs=[生ID]`）
  ③それも無ければ生 ID（P4）。3経路とも status は従来どおり explicit（source_backed）。
- テスト: `TestArtifactClaimTextIndex` / `TestStageParticipationItems` /
  `TestBuildComponentEvidenceClaimsResolution`（バッチ1回検証・DB断時の artifact
  フォールバック生存）/ `TestBuildComponentStageParticipation`（node=None で emit・
  node ありで抑制）+ 語彙登録テスト拡張（計21件追加）。敵対的レビュー済み
  （翻訳一致テストは component 側 DB ID vs graph 側 agent ID の非対称データで検証）。
  backend 全体 5,271 pass。
- 既知の限界（意図的・実害小）: `is_thesis_anchor` 由来と `thesis_context` 由来の
  central_thesis 項目はラベルを揃えて `_dedupe_items` で1件に畳むため、後者の
  `evidence_refs=["central_thesis"]` は畳まれた側では失われる（表示上は同一項目）。

