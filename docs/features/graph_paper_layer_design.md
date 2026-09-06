# グラフの論文層（Paper Layer — フレームに論文を肉付けする層）

> **状態: 実装済み（正本・凍結）**（2026-09-03 起票・同日 Phase 0 実装。migration なし・
> 新テーブルなし・LLM 0回の読み時射影。実装記録は §10。Phase 1/2 は別途起票）

**正本**: 本ドキュメント。
**関連**: [グラフ対話レビュー](graph_dialogue_review_design.md)（GR1〜GR8 — 表示先の画面。
本層はその画面に「論文側の顔」と「論文の順で見る」ビューを足す）/
[要素中心コンテキストレンズ](element_context_lens_design.md)（要素 1-hop の読み時導出 —
本層は同じ artifact 群を論文構造の側から読む）/
[二層説明（generic / contextual）](hierarchical_context_explanation_design.md)
（component 単位の contextual 説明を本層が join して表示する）/
[図⇄概念構造の接続](figure_concept_linking_design.md)（claim ⇄ 図表リンクの正本 =
`FigureRecord.linked_claim_ids`）/ [段階ラベル・共有語彙表](label_vocab_design.md)
（`SUPPORT_SECTION_LABELS` を再利用する）。

---

## 1. 目的 — 「このノードは論文の何か」を読めるようにする

理論操作グラフ（TheoryOperationGraph）はコンポーネント間の関係をよく表す一方、
**もとの論文との対応・文脈を含まないため、各ノードが論文の何を表しているかが
ほとんど読めない**。これは偶然ではなく設計の帰結である:

- 主グラフ（`graph_layer="main"`）のラベルは #308 で **theory stage 名そのもの**
  （Theory basis / Equation system …）に固定され、分野・論文固有の語を排している。
  関係を domain-neutral に読める代わりに、「この論文では何か」がラベルから消える。
- 一方でノードは論文への指し先を **すでに持っている**: `linked_equation_ids` /
  `linked_claim_ids` / `linked_evidence_ids` / `linked_derivation_ids`、
  `eliminated_symbols` / `retained_symbols`、`supports_claim_ids`。
- しかしグラフレビュー画面（`admin-graph-review.js`）が描くのは `description` と
  根拠 claim 行だけで、式本体・evidence 逐語引用の所在（章・ページ）・図表・記号・
  導出ステップ・NarrativeAnnotator の `narrative_role` / `transition_text` を出していない。
- 論文全体の文脈も別 artifact に揃っている: paper_skeleton（目的・backbone）、
  thesis_reconstruction（中心命題・支持構造）、document_structure（章構成）、
  figure_table_semantics（claim⇄図表）、contextual_explanation（component の文脈説明）。
  いずれもグラフには射影されていない。

つまり「肉付けの部品は散在しているが、フレームに掛ける層が無い」。本層はその層を、
**フレームを触らず・保存せず・LLM を呼ばず**、読み時導出で足す。

### 1.1 二層モデル

| 層 | 実体 | 変更 |
|---|---|---|
| **フレーム層**（既存） | `theory_component_graphs.graph_json`（main / equation_detail / debug） | 非改変 |
| **論文層**（本層） | 論文自身の骨格（章 → 式・claim・図表の論文順）+ フレーム各ノードの「論文側の顔」+ 双方向リンク + 被覆 | 読み時導出・保存なし |

読む向きは二つあり、**どちらも一つの DTO で返す**:

- **フレーム→論文**（このノードは論文の何か）: ノード単位に式・claim・evidence・図表・
  記号・導出・narrative・thesis 上の役割・component 要約・contextual 説明を束ねる。
- **論文→フレーム**（この章・この図はどのノードか）: 章立て・式番号・図番号の論文順を
  背骨にして、そこにノードを吊る。「論文を再構成したもの」として読めるのはこちら。

ノード属性の追記だけでは後者が読めないため、論文の順序構造を一級の `paper` キーとして持つ。

## 2. 不変条項（PL1〜PL8）

| # | 条項 | 根拠・帰結 |
|---|---|---|
| PL1 | **フレーム非改変** | `graph_json`・#308 のラベル規律・`theory_components` / `theory_claims` の列に触れない。論文層は読み時導出で DB に保存しない（Phase 0）。 |
| PL2 | **決定論・非LLM** | 同期パスで LLM を呼ばない（W6 / GR5 と同じ）。artifact と DB 行の join のみ。 |
| PL3 | **リンクの無いものに対応を推定しない** | 章への結び付けは linked ids の実所在（式の `source_location.section_id`、evidence の `source.section_id`、claim の `section_id`、無ければ `block_id` → `document_structure.blocks[].section_id`）のみ。どれも無いノードは「論文上の位置を特定できません」と事実文で出す。名寄せ・類似度・見出し推定は使わない。 |
| PL4 | **数値非表示** | `confidence` / `weight` / `candidate_score` を DTO に載せない（GR3・W8）。件数バッジも作らない（リストは列挙で見せる）。 |
| PL5 | **承認オブジェクトを増やさない** | 承認は component / claim のまま（GR1）。論文層に review 状態を持たせない。将来 LLM 由来の対応候補を足す場合も candidate 止まりで、確定は教員（Phase 1）。 |
| PL6 | **権限 = document viewable** | `_ensure_document_viewable`（グラフと同一ゲート）。fail-closed。 |
| PL7 | **内部 ID を UI に出さない** | `eq_op_0001` / `theory_op_0003` / `ev_0012` / `claim_span_001` を表示ラベルに使わない。式は印字番号（「式 (12)」）、章は見出し、図表は `figure_label` / caption。番号の無い式は「番号なし」+ 本文先頭で示す。 |
| PL8 | **fail-soft・欠落の明示** | artifact / DB 行の欠落は例外にせず、その部品だけ空にして `facts[]` に「◯◯の解析結果が無いため…」を1行足す。グラフ未構築は `available:false`。 |

## 3. DTO 契約（Phase 0）

`GET /api/admin/documents/{document_id}/paper-layer`（TEACHER・PL6）。
既存 `GET .../component-graph` のレスポンスは **不変**（別エンドポイント・遅延取得）。

```jsonc
{
  "document_id": "…",
  "available": true,                 // グラフのノード 0 or artifact 皆無 → false
  "facts": ["章構成の解析結果が無いため、論文の順では表示できません。"],   // PL8 事実文
  "graph_updated_at": "…" | null,

  "paper": {                         // 論文→フレーム（論文順の背骨）
    "title": "…",                    // document_structure.metadata.title（無ければ ""）
    "goal": "…" | null,              // paper_skeleton.paper_goal.text
    "central_question": "…" | null,  // thesis_reconstruction.central_question ?? paper_skeleton.central_question.text
    "central_thesis": { "text": "…", "claim_ids": [db uuid…], "node_ids": ["…"] } | null,
    "sections": [
      {
        "section_id": "…", "title": "…", "level": 1, "order": 3,
        "page_start": 4, "page_end": 6, "parent_section_id": null,
        "node_ids": ["theory_op_0002", "eq_op_0007"],   // この章に結ばれたノード（main + detail）
        "equations": [ { "equation_id": "eq_12", "display_label": "式 (12)", "node_ids": [...] } ],
        "figures":   [ { "figure_id": "fig_3", "display_label": "Figure 3", "node_ids": [...] } ],
        "tables":    [ { "table_id": "table_1", "display_label": "Table 1", "node_ids": [...] } ],
        "claims":    [ { "claim_id": "<db uuid or ''>", "agent_id": "claim_…", "text": "…(≤200字)", "node_ids": [...] } ]
      }
    ],
    "backbone": [                    // paper_skeleton.logical_blocks（論文の論理ブロック）
      { "block_type": "derivation", "label": "…", "summary": "…", "section_ids": [...], "node_ids": [...] }
    ]
  },

  "nodes": {                         // フレーム→論文（graph の全ノード分。main は member を合算）
    "<node_id>": {
      "node_id": "…", "graph_layer": "main" | "equation_detail" | "debug", "label": "…",
      "narrative_role": "…",                       // graph_json.narrative.node_narratives[id].narrative_role
      "component": { "summary": "…", "teaching_takeaway": "…", "role_in_thesis": "…" } | null,
      "explanation": { "body": "…", "status": "approved" | "candidate" } | null,   // contextual 説明（approved 優先）
      "thesis_roles": [ { "thesis_ref": "central_thesis" | "support:direct_supports:0",
                          "section_label": "直接支持", "text": "…" } ],
      "sections":  [ { "section_id": "…", "title": "…", "page_start": 4 } ],   // 論文順
      "equations": [ { "equation_id": "…", "display_label": "式 (12)", "latex": "…", "plain_text": "…",
                       "role": "input" | "intermediate" | "output" | "definition" | "constraint" | "linked",
                       "section_id": "…", "page": 4, "needs_math_review": false } ],
      "claims":    [ { "agent_id": "…", "claim_id": "<db uuid or ''>", "text": "…", "review_status": "…",
                       "resolution": "db" | "artifact", "section_id": "…", "is_atomic": true } ],
      "evidence":  [ { "evidence_id": "…", "text": "…(≤200字・逐語)", "section_id": "…", "page": 4,
                       "block_id": "…", "role": "source_quote" } ],
      "figures":   [ { "figure_id": "…", "db_id": "<uuid>" | null, "display_label": "Figure 3",
                       "caption": "…", "page": 5, "via_claim_ids": ["…"] } ],
      "tables":    [ { "table_id": "…", "display_label": "Table 1", "caption": "…", "page": 6, "via_claim_ids": [...] } ],
      "symbols":   [ { "symbol": "…", "kind": "parameter", "role": "eliminated" | "retained",
                       "definition_quote": "…", "defining_equation_labels": ["式 (3)"] } ],
      "derivations": [ { "derivation_id": "…", "operation": "…", "chain_type": "…",
                         "steps": [ { "step_id": "…", "operation": "…", "input_labels": ["式 (3)"],
                                      "output_labels": ["式 (4)"], "reason": "…" } ] } ],
      "unlocated": false             // sections が空のとき true（PL3・PL8 の事実文は facts でなく node 単位で UI が出す）
    }
  },

  "edges": {                         // 遷移文（NarrativeAnnotator）と根拠式ラベル
    "<edge_id>": { "transition_text": "…", "equation_labels": ["式 (3)", "式 (4)"] }
  },

  "coverage": {                      // フレームに掛かっていない論文要素（失敗ではなく信号）
    "unbound_sections":  [ { "section_id": "…", "title": "…" } ],
    "unbound_equations": [ { "equation_id": "…", "display_label": "式 (7)", "section_id": "…" } ],
    "unbound_figures":   [ { "figure_id": "…", "display_label": "Figure 2" } ],
    "unbound_claims":    [ { "agent_id": "…", "claim_id": "…", "text": "…(≤200字)", "section_id": "…" } ]
  },

  "narrative": { "graph_summary": "…" }
}
```

**載せないもの（PL4/PL7）**: `confidence` / `weight` / `candidate_score` / `reason`（LLM の理由文）/
`qualification_reason`。ID は `*_id` キーとしては返す（クリック遷移に必要）が、UI は
`display_label` / `title` / `text` だけを描く。

### 3.1 結び付け規則（決定論・PL3）

ID の対応関係は 2026-09-03 時点の実装から次のとおり（正本はコード）:

- **ノード ID**: main = `theory_op_NNNN`、detail = `eq_op_NNNN`（graph-native、DB 行なし）。
  debug / fallback ノードは `theory_components.id`（DB UUID）に書き換えられている。
  main の `member_component_ids` / `detail_node_ids` は `eq_op_*`、`linked_component_ids` /
  `representative_component_id` と detail の `parent_component_id` は **component_assembly の
  agent ID**（`theory_components.source_scope->'legacy_ids'` で DB 行に解決できる）。
- **式**: `artifacts["equation_semantics"]["equations"][]`（ネスト形。`to_equations_export` の
  フラット形ではない）。所在 = `source_extraction.source_location.{page, section_id, block_id}`、
  本文 = `reconstruction.latex ?? source_extraction.latex`、印字番号 = `label`（括弧は
  すでに剥がされている）。`display_label = "式 (" + label + ")"`、label 無しは
  `"番号なし: " + plain_text[:40]`。
- **evidence**: `artifacts["evidence_registry"]["records"][]`。所在 = `source.{page, section_id, block_id}`、
  本文 = `evidence_text`（逐語のみ）。
- **claim**: 既存 `reference_index.claims`（DB → artifact の2段解決、`resolution` 付き）を
  そのまま使い、章は `artifacts["claim_object_builder"]["claims"][].section_id`（無ければ
  `source_evidence_ids` → evidence の section_id）で補う。
- **章の解決順**: 直接の `section_id` → `block_id` から `document_structure.blocks[].section_id` →
  無ければ未特定。章の並びは `document_structure.sections[].order`。
- **図表**: `FigureRecord.linked_claim_ids` ∩ ノードの claim（agent ID）で図→ノード。
  `document_figures` 行（`id` / `figure_key` / `figure_label` / `page`）は
  `normalize_figure_join_key`（`core/document_pipeline/figure_images.py`）で `figure_id` と
  照合し `db_id` を付ける（画像 URL の生成はフロント側。行が無ければ `db_id:null`）。
- **記号**: `artifacts["symbol_registry"]["records"][]` を `canonical_symbol` / `notation_variants`
  でノードの `eliminated_symbols` / `retained_symbols` と照合。`definition_quote =
  definition_evidence_texts[0]`。
- **導出**: `artifacts["derivation_chain"]["chains"][]` を `linked_derivation_ids` で引き、
  `steps[]` をそのまま（`reason` は非LLM の理由文なので載せる。confidence は落とす）。
- **thesis 上の役割**: `thesis_reconstruction.central_thesis.claim_ids` と
  `support_structure[section][i].claim_ids` を claim agent ID で索き、`thesis_ref` は
  `"central_thesis"` / `f"support:{section}:{i}"`（`persistence._thesis_ref_nodes` と同じ規約）、
  `section_label` は `label_vocab.SUPPORT_SECTION_LABELS`（新しい訳語表を作らない）。
- **component 要約**: `artifacts["component_assembly"]["components"][]` を agent ID
  （`linked_component_ids` / `representative_component_id` / `parent_component_id` /
  `agent_component_id`）で引く。`teaching_takeaway` は DB に無いため artifact のみ。
- **contextual 説明**: `element_explanations`（`element_type='theory_component'`、
  `element_id` は **agent ID**、`kind='contextual'`）。`approved` があればそれ、無ければ
  `candidate` を `status` 付きで返す（`dismissed` / `superseded` は返さない）。
- **main ノードの顔** = 自身の linked ids ∪ `member_component_ids` の detail ノードの顔（重複除去・
  論文順）。detail ノードが結び付けの粒度で、main は集約表示（多対多を隠さない）。

### 3.2 被覆（coverage）

- `unbound_sections`: どのノードにも結ばれなかった章（全章を正直に列挙。参考文献も除外しない）。
- `unbound_equations`: どのノードの `*_equation_ids` にも現れない式。
- `unbound_figures`: `linked_claim_ids` がノードの claim と交差しない図。
- `unbound_claims`: `is_atomic` かつ `support_status="source_backed"` でどのノードにも結ばれない
  claim object（本文 200 字）。

被覆は「フレームが表現していない部分」の信号であり、失敗表示・警告色にしない。

## 4. 実装配置

| 部品 | 場所 | 規律 |
|---|---|---|
| core | `backend/core/graph_paper_layer/`（`__init__.py` / `schema.py` / `builder.py`） | **FastAPI・LLM・sqlalchemy 非 import**。純関数 `build_paper_layer(graph, artifacts, *, figure_rows, explanation_rows) -> dict`。入力は全て dict / list。 |
| route | `routes/theory_components.py` に `GET /documents/{document_id}/paper-layer` を追加 | 既存の `_components_for_document` → `_normalize_stored_component_graph` → `_build_graph_reference_index` を再利用してグラフを組み、`document_run_artifacts` を **1回** 読み、`document_figures` と `element_explanations` を小さな SELECT で引いて builder に渡す。各取得は try/except で fail-soft（PL8）。 |
| UI | `admin-graph-review.js` + `styles.css` の `graph-review-*` ブロック | ES5・`window.LectureStudio.graphView` 委譲（GR8）・新しい数式描画パイプラインを作らない（`richText` 経由）。 |
| 3点セット | `admin_ui_anchors.py`（KNOWN + ADMIN 両方）+ `docs/manual/teacher/26-admin-graph-review.md` の節 + `data-ui-anchor` | 件数の正は `test_admin_help_ui_anchors.py`。 |

### 4.1 UI（Phase 0）

- **ツールバーに表示切替** `グラフ | 論文の順`（アンカー `graph-review.paper-view`）。
  「論文の順」は左ペインの network を **章アウトライン**に差し替える: 章見出し（level で
  インデント・ページ範囲）→ その章に結ばれたノードのチップ（main = stage ラベル、detail =
  `graphView.detailHeading`）・式チップ（印字番号）・図表チップ（`figure_label`）。
  ノードチップのクリック = `selectNode` + 右ペイン詳細更新（network は非表示のまま）。
  結ばれたノードの無い章は「このフレームには掛かっていません」の事実文。
  末尾に被覆（掛かっていない式・図・claim）を列挙。
- **右ペイン詳細に「論文での対応」区画**（アンカー `graph-review.paper-facing`）を
  既存の description の直後・承認ボタンの前に置く: narrative_role → 論文上の位置（章・
  ページ）→ thesis 上の役割 → 式（KaTeX・役割チップ）→ 根拠の逐語引用（章・ページ付き）→
  図表（ラベル+caption）→ 記号（定義の引用）→ 導出ステップ → contextual 説明（status 付き）。
  `unlocated` のノードは「論文上の位置を特定できませんでした（式・根拠・claim への
  リンクがありません）」。取得失敗・`available:false` は区画ごと事実文1行に縮退し、
  既存のレビュー操作は影響を受けない。
- 論文層は `open()` 時に **グラフと並行して遅延取得**（`state.paperLayer`）。stale-response
  ガード（`state.documentId` 不一致で破棄）は graph fetch と同型。
- 既存の根拠 claim 行・承認/却下・深く検討・チャットは非改変。

## 5. Phase 計画

| Phase | 内容 | LLM | 保存 |
|---|---|---|---|
| **0（本設計の実装対象）** | 上記 DTO・API・UI。読み時射影・被覆列挙 | 0回 | なし |
| 1（後続・別途起票） | 「この論文ではこのノードが何をしているか」の一段落。まず contextual 説明の join で足りるかを実測し、足りなければ NarrativeAnnotator の拡張（candidate・確定は教員） | 1コール/document | element_explanations 相乗り想定 |
| 2（後続） | 被覆信号の下流: 掛かっていない章・claim を G層 To-Do やカテゴリギャップと同型の候補に | 0回 | 判断のみ |

## 6. 非スコープ（v1）

学習者向け表示 / 論文層の保存・版管理 / LLM による章推定 / edge の承認 / 被覆の件数バッジ /
図画像のインライン表示（v1 はラベル+caption。画像は既存の図モーダルへの導線を後続で検討）/
`to_equations_export` フラット形の受理（persist される artifact はネスト形のみ）。

## 7. ガードレール（実装時に追加）

- `test_graph_paper_layer_guardrails.py`: core が fastapi / routes / sqlalchemy / core.llm を
  import しない・DTO に `confidence` / `weight` / `candidate_score` / `qualification_reason`
  キーが再帰的に現れない・`DELETE` / `INSERT` / `UPDATE` 文が core に無い（読み時導出）・
  graph_json を書き換えない（入力 dict を mutate しない）。
- `test_graph_paper_layer_core.py`: 章解決順（section_id → block_id → 未特定）/ main の
  member 合算 / 図の二段キー照合 / thesis_ref 規約 / 被覆の除外規則 / artifact 欠落ごとの
  facts 事実文 / display_label に内部 ID が出ない。
- `test_graph_paper_layer_api.py`: 権限ゲート（viewable）/ artifact 取得失敗時の fail-soft /
  既存 component-graph レスポンス不変。
- `test_graph_review_ui_static.py` に論文層の節: アンカー2件・ES5・graphView 委譲・
  内部 ID 非描画・`available:false` の縮退文言。

## 8. 設計上の判断メモ

- **別エンドポイントにした理由**: component-graph は既存テスト・呼び出し元（原稿スタジオ）
  が多く、レスポンス肥大と副作用を避ける。論文層はレビュー画面だけが使う。
- **保存しない理由**: 入力（graph_json・artifacts・element_explanations）はいずれも既に永続化
  されており、論文層はその決定論的射影。保存すると再解析・承認との整合維持が必要になる。
  性能が問題になれば `stage_outputs` への artifact 追加（パイプライン末尾の非LLM ステージ）で
  対応でき、DTO は変わらない。
- **detail 粒度で結ぶ理由**: main は stage 単位で複数 derivation を跨いで集約されるため、
  論文との対応は多対多。detail で結び main へ集約すれば、多対多を隠さずに読める。

## 9. 開発チェックリスト §5 対応

- 5-1: 学習者向け機能・パイプラインステージ・ルーター・migration の追加はいずれも無し
  （既存ルーター `theory_components` へのエンドポイント追加のみ → `docs/backend/api.md` の
  A層成果閲覧の表に1行追加）。
- 5-2: 状態ヘッダあり。5-4: migration なし。5-5: 本書はリポジトリ内。5-6: 件数はテスト参照。

## 10. 実装記録（2026-09-03・Phase 0）

- **core**: `backend/core/graph_paper_layer/`（`schema.py` = 語彙・事実文定数・`display_label` /
  `truncate_snippet` / `thesis_ref_for` ヘルパ、`builder.py` = `build_paper_layer`）。
  FastAPI / sqlalchemy / LLM 非 import・入力を mutate しない。`normalize_figure_join_key` は
  `core/document_pipeline/figure_images.py` が sqlalchemy・storage・PyMuPDF を import するため
  同一規則を3行で局所再実装（コメントで正本を参照）。
- **API**: `routes/theory_components.py::get_document_paper_layer`
  （`GET /documents/{document_id}/paper-layer`・`response_model=None`）。builder 呼び出しは
  `_build_paper_layer_payload`（遅延 import・テストで差替可）経由、`document_figures`
  （`status='extracted'`）と `element_explanations`（`theory_component` × `contextual` ×
  approved/candidate・`_is_db_uuid` ガード）は各 try/except で `[]` に縮退、builder 例外は
  `available:false` + 事実文で 200。`_build_graph_reference_index` 内の artifact 再読込は
  許容（コメント明記・未リファクタ）。
- **既存経路への追加**: `_normalize_stored_component_graph` が `edge_id` を落としていたため
  NarrativeAnnotator の `edge_narratives` と辺を突合できなかった。読み時射影で `edge_id` を
  通し（`ComponentGraphEdge.edge_id: str = ""` を additive 追加）。旧 graph_json は空文字のまま。
- **UI**: `admin-graph-review.js` に `state.view` / `state.paperLayer` / `loadPaperLayer`
  （graph と並行取得・stale ガード同型）/ ツールバー「表示: グラフ | 論文の順」/
  `renderPaperOutline`（事実文 → 題名・目的・中心の問い・中心命題 → 章（level インデント・
  ページ範囲・ノード/式/図表チップ・claims details・ノード無しは事実文）→ backbone →
  掛かっていない要素）/ `paperFacingHtml`（§4.1 の順。要確認理由行の直後・承認ボタンの前）。
  network は破棄せず包みの `hidden` を切替、graph へ戻す時に `redraw()`。CSS は
  `.graph-review-paper-*`。アンカー `graph-review.paper-view` / `graph-review.paper-facing`
  + マニュアル `26-admin-graph-review.md#paper-view` / `#paper-facing`（件数の正は
  `test_admin_help_ui_anchors.py`）。
- **契約の解釈（実装時に確定）**: ①`paper.sections[].equations/figures/claims` は「所在が
  その章の要素」を全て列挙し、未結合は `node_ids: []`（§4.1 の被覆と整合。claims は
  結合済み ∪ atomic × source_backed に限定）。②`document_structure` に無い `section_id` は
  `title: ""` で保持（欠落を無言で落とさない。PL8 の事実文は別途出る）。
  ③`linked_derivation_ids` が step ID の場合は親 chain に解決して steps を投影。
  ④`central_thesis` の `section_label` は `""`（支持構造の節ではないため）。
- **ガードレール**: `test_graph_paper_layer_{core,guardrails,api}.py` +
  `test_graph_review_ui_static.py::TestPaperLayer`。
- **既知の限界（後続）**: `persistence.py::persist_component_graph` は辺の
  `evidence_equation_ids` / `evidence_derivation_ids` / `evidence_claim_ids` /
  `source_evidence_ids` を graph_json に保存していない（`edge_id` は保存済み）。このため
  `edges[].equation_labels` は保存済みグラフでは空になりやすい。永続化の是正は再解析後にしか
  効かないため別件で扱う（本層の DTO は不変）。
