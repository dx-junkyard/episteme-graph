# グラフ対話レビュー（Graph Dialogue Review, 教材起点のグラフ確認・承認画面）

> **状態: 実装済み（正本）**（2026-08-29 起票・同日実装。migration **075**
> — `deliberation_sessions.element_type` CHECK への `'document_graph'` 追加のみ。
> 新テーブルなし。実装記録は §11。**音声対話の追補は §12**（2026-08-29・
> migration なし・API 2本と `admin-voice-chat.js` の追加のみ））

**正本**: 本ドキュメント。
**関連**: [要素検討ワークスペース（W層）](element_deliberation_workspace_design.md)
（W1〜W9 — 対話・注釈・コスト・権限の基盤を全面的に再利用する）/
[要素中心コンテキストレンズ](element_context_lens_design.md)（ノード対話の grounding に
1-hop 文脈が入る根拠）/ [要素インベントリ](element_inventory_design.md)（教材行からの
成果閲覧の姉妹入口 — インベントリ=一覧・本層=グラフ）/
[再構成ループ（R層）](reconstruction_loop_design.md)（claim 承認フックの下流）/
[承認・共有レイヤー（C層）](../features/component_evidence_redesign.md)（承認済み成果の
学習者向け配信）/ [LLM トークン使用量推計（U層）](llm_usage_metering_design.md) /
[場面別 LLM モデル選択（M層）](llm_model_selection_design.md)。

---

## 1. 目的 — パイプライン成果のレビューを「グラフを歩きながら」行う

パイプラインが生成した `theory_components` / `theory_claims` /
`theory_component_graphs` は全て承認ステータス（`status` / `review_status`）を持つが、
2026-08-29 時点でレビューの導線は次の状態だった:

- **component の承認/却下**は原稿スタジオの論理要素カードにのみ存在し、到達には
  「コース作成 → スタジオでコース選択 → チャンク/セクション選択 → 論理要素ビュー」が
  必要（教材アップロード直後＝コース未作成では入口自体がない）。
- **claim の承認は UI が存在しない**。`PATCH /api/admin/claims/{id}` は監査・却下伝播・
  承認時の R層 item 自動オーサリング起動まで実装済みだが、フロントの呼び出し元ゼロ。
- 要素インベントリ（教材行「検出要素」）は review_status をバッジ表示するだけの閲覧専用。

本層は教材管理「アップロード済み教材」の各行から起動する**グラフ対話レビュー画面**を
追加する。理論操作グラフ（TheoryOperationGraph）を見取り図に、教員が

1. **構造を見る** — main 層バックボーン（theory stage 5〜8個）から式の詳細層まで、
   source_backing / review_status の視覚区別つきで歩く
2. **AI と確かめる** — ノードを選んで既存 W層対話（隣接ノード・backing claim・evidence が
   grounding に入る）で内容を検討する。ノード未選択ではグラフ全体を文脈にした対話
   （「このグラフで裏付けが弱いのはどこか」等）ができる
3. **その場で確定する** — component の承認/却下、backing claim の承認を画面内で実行し、
   「未レビューのみ」フィルタと「次の未レビューへ」ナビで作業を進める

の3つを1画面で行えるようにする。**確定は常に人間**であり、AI は候補・仮説の提示に
限定される（W2 継承）。

## 2. 不変条項（GR1〜GR8）

- **GR1 確定は人間のみ**: AI 対話・注釈は常に candidate 止まり。AI の応答から承認 API を
  呼ぶ経路を作らない。承認・却下は教員の明示ボタン操作のみ。
- **GR2 A層非改変**: `src/episteme_graph/agents/` と成果テーブルのスキーマに手を入れない
  （読む + 既存の status 遷移 API を呼ぶだけ）。
- **GR3 数値非表示**: confidence・スコアの生値を出さない（W8 継承。段階表示は既存
  ElementCard / ラベル語彙に従う）。教員向けの未レビュー**件数**は事実として表示可
  （R層レビューキューのバッジ前例）。
- **GR4 監査必須**: 承認・却下・claim レビュー遷移は既存の `_record_review_event`
  経路（`AUDIT_ENTITY_COMPONENT` / `AUDIT_ENTITY_CLAIM`）に記帳する。新しい
  entity_type を作らない。
- **GR5 同期1コール・コスト相乗り**: グラフ全体対話も1応答=1 LLM コール（W6）。
  CostGate は W層の `DELIBERATION_MAX_CALLS_PER_SESSION` / `_PER_DAY` に**相乗り**
  （専用上限を作らない）。U層 feature は `deliberation:graph_chat` を分離し、M層 scene
  は `deliberation`（既存）にマップする。
- **GR6 権限 fail-closed**: 閲覧・対話 = `_ensure_document_viewable`（W層対話と同水準）。
  承認・却下・claim レビュー = `_ensure_document_editable`。編集権限が無いユーザーには
  承認ボタンを非活性 + 事実文で提示する（強制はサーバ側）。
- **GR7 情報を落とさない**: 却下も status 遷移（既存 API のセマンティクスのまま）。
  グラフ全体対話のセッション・メッセージは追記のみ（W4 継承）。
- **GR8 グラフ描画の正本一元化**: グラフのレイアウト・vis-network 構築・backing 別
  スタイル・ラベル語彙の正本は原稿スタジオの `lsGraph*` 純関数群であり、
  `window.LectureStudio.graphView` として公開して本画面が使う。**描画ロジックを
  二重実装しない**（スライド分割の「クライアント側に再実装しない」規約と同型）。
  ※起票時は新ファイル `component-graph-view.js` への抽出を想定したが、既存の
  静的ガードレール群（`test_issue_447_449_graph_labels.py` /
  `test_issue_451_452_graph_viz.py` / `test_graph_detail_card_ui_static.py` 等）が
  スタジオ内の関数本体を正本として固定しているため、関数は移設せず公開面だけを
  設ける方式に変更した（不変条項の実質 — 単一実装 — は同じ）。

## 3. 画面構成

教材管理の各行 `⋯` メニューに「🕸 グラフレビュー…」を追加（`data-ui-anchor:
materials.row-graph-review`。`document_id` を持つ行のみ表示 — 検出要素ボタンと同条件）。
押下でフルスクリーンモーダル（`admin-graph-review.js`、ES5・`window.GraphReview`・
admin.js から DI 注入）:

```
┌───────────────────────────────────────────────────────┐
│ 🕸 グラフレビュー — <教材タイトル>                  [×] │
│ [主グラフ|式の詳細|すべて] [☐未レビューのみ] 未レビュー N件 [次の未レビューへ] │
├──────────────────────┬────────────────────────────────┤
│                      │ ノード詳細（ElementCard 再利用）│
│   グラフ             │  backing claims（各行に承認）   │
│  （ComponentGraph    │  [承認] [却下] [深く検討]       │
│    View 共有描画）   ├────────────────────────────────┤
│                      │ AI対話                          │
│                      │  ノード選択中: この要素について │
│                      │  未選択: グラフ全体について     │
└──────────────────────┴────────────────────────────────┘
```

- **グラフペイン**: 既定は main 層。層トグル・backing 別スタイル（source_backed=通常 /
  partially=細線 / review_required=点線 / inferred=薄色+⚠）は共有描画モジュールの責務。
  未レビューフィルタ ON のときは `review_status` が承認済み以外のノードのみ強調表示する
  （非該当ノードは薄く残す — 構造の文脈を消さない）。
- **ノード詳細ペイン**: 選択ノードの ElementCard（スタジオのグラフ詳細と同じ DTO 変換を
  共有）。`component_id` が `theory_components` 行に解決できるノードには
  [承認][却下]、backing claim（DB UUID に解決できたもの）には行ごとの [承認] を出す。
  解決できないノード・claim は操作なしで表示だけする（GR7 — 情報は落とさない）。
  承認不能（inputs/outputs 欠落・出典未設定）はサーバが 422 の事実文で返し、UI は
  そのまま提示する。
- **AI対話ペイン**: ノード選択中は W層セッション（`element_type='theory_component'` /
  `'theory_claim'`、既存 `POST /api/admin/deliberation/sessions` + `/messages`）。
  候補注釈が返れば既存 annotations API（commit/dismiss）のカードを出す。
  ノード未選択（またはタブ切替）では**グラフ全体対話**（§5）。どちらの対話も
  「深く検討」ボタンで既存 W層モーダル（deliberation.js）へ遷移できる。

## 4. 承認 API（新設2本 — 既存遷移ロジックの抽出）

原稿スタジオの component 承認は「フルオブジェクト PUT」で実現されているが、グラフ
レビュー画面から同じことをすると**画面が持っていない編集中フィールドを巻き戻す事故経路**
になる。却下に専用 `POST /theory-components/{id}/reject` がある前例に合わせ、
内容を触らない遷移専用エンドポイントを追加する:

1. **`POST /api/admin/theory-components/{component_id}/approve`**
   - `_ensure_document_editable`。`status='teacher_reviewed'` /
     `review_status='teacher_approved'` に遷移（内容フィールドは一切変更しない）。
   - **サーバ側の承認可能性チェック**: inputs / outputs が空でない、かつ各 item に
     `source_refs` または `evidence_claims` があること（スタジオのクライアント側
     ゲート `lsTheoryCanApprove` と同基準をサーバで強制）。満たさなければ 422 +
     日本語事実文。
   - 監査は既存 PUT と同じ `_record_review_event(AUDIT_ENTITY_COMPONENT, ...)`。
2. **`POST /api/admin/claims/{claim_id}/review`**（body: `{"review_status": ...}`）
   - 許可語彙は `teacher_approved | rejected | needs_revision | teacher_review_required`
     のみ（それ以外は 422）。`_ensure_document_editable`。
   - 本文フィールドは一切変更しない。遷移の副作用（監査・`rejected` の伝播
     `_propagate_rejected_claim`・承認時の R層 `maybe_schedule_item_authoring` 起動）は
     既存 `update_claim` から**共通関数に抽出して両者が使う**（二重実装しない）。

既存の `PUT /theory-components/{id}` / `PATCH /claims/{id}`（フル upsert）は非改変。

## 5. グラフ全体対話（element_type='document_graph'）

- **セッションの格納は W層の `deliberation_sessions` に相乗り**する（migration 075 で
  element_type CHECK に `'document_graph'` を追加。`element_id` = document の正規化済み
  UUID）。`element_annotations` の CHECK は**変更しない** — グラフ全体対話は
  **注釈を生成しない**（対話のみ。要素単位の候補注釈はノード対話の責務）。
- **grounding は非LLM・決定論**（`backend/core/deliberation/graph_dialogue.py`、
  FastAPI 非 import）: 最新の component graph から main 層バックボーン
  （label / description / theory stage / source_backing_status / review_reasons）、
  edge の関係（edge_type / backing）、equation_detail 層の規模（件数）、未レビュー
  ノードの一覧、validation_results、narrative 注釈（あれば）をテキスト整形する。
  グラフ未構築（ノード0）はセッションを作らず 422 の事実文。
- **プロンプト制約**: 仮説文体（「〜の可能性があります」）・grounding に現れる関係のみを
  語る・承認/却下の実行や推奨の断定をしない（GR1。「承認すべき」ではなく「裏付けの
  状態はこうである」の事実提示に留める）。数値 confidence を出させない。
- **実行**: `run_turn` は W層 dialogue と同型（1コール・失敗時は degraded 固定文 + 200・
  `run_with_repair` 不使用 = W6）。履歴ウィンドウは `window_history`（W層と同じ
  16/4000/head_keep=1）。CostGate は W層の session+daily を共有し、U層 feature のみ
  `deliberation:graph_chat` で分離（`KNOWN_FEATURES` / `scene_for_feature` に登録、
  scene は `deliberation` を共用 — 専用 env を増やさない）。
- **API**（`routes/deliberation.py`）:
  - `POST /api/admin/deliberation/documents/{document_id}/graph-sessions` —
    get-or-create（本人 × document で最新セッション再開）。`_ensure_document_viewable`。
  - `POST /api/admin/deliberation/documents/{document_id}/graph-sessions/{session_id}/messages`
    — 1ターン実行。セッション所有者のみ。
  - 既存の `GET /sessions/{session_id}` で履歴取得（element_type を問わない既存挙動）。
  - ElementRef リゾルバ（`refs.py`）は**変更しない**。`document_graph` は overview /
    context / annotations / identity の対象外（該当ルートに到達しても既存の未知
    element_type 拒否がそのまま効く）。

## 6. グラフ描画の共有化（LectureStudio.graphView）

原稿スタジオの `lsGraph*` 群のうち **lsState 非依存の純関数**を
`window.LectureStudio.graphView` として公開し、グラフレビュー画面が同じ描画で使う
（GR8。関数は移設しない — 既存静的テストが本体を固定しているため）:

- `filterByLayer(graph, filter)` — 層フィルタの純粋版（`lsGraphForCurrentLayer` は
  これへの薄い委譲に変更） / `layerOptions(nodes)` — 層トグルの選択肢+件数
- `layoutPositions(nodes, edges)` / `displayEdges(edges)` — レイアウト・エッジ集約
- `visNodeSpec(node, index, layoutPositions, pathOrder)` /
  `visEdgeSpec(edge, index, pathOrder)` / `networkOptions()` —
  vis-network 構築の正本（`lsInitComponentGraphNetwork` 内のインライン実装を
  名前付き関数へ抽出し、スタジオ側もこれを通す）
- `semanticLabel` / `detailHeading` / `nodeTooltip` / `roleLabel` /
  `sourceBackingLabel` / `reviewReasonLabel` / `edgeLabel` — ラベル語彙

原稿スタジオはツールバー・詳細ペイン（ElementCard）・読み順パス・D層反実仮想
デコレーションを自分に残す（挙動不変のリファクタ）。グラフレビュー画面の
ノード詳細ペインはレビュー専用の投影（承認状態・backing claims・操作）で、
ラベル語彙は上記 graphView から引く（辞書の二重化をしない）。

## 7. UI アンカー・マニュアル（3点セット）

- `ADMIN_UI_ANCHORS` 追加（値の正確な数はテストが正）: `materials.row-graph-review` /
  `graph-review.modal` / `graph-review.layer` / `graph-review.filter-unreviewed` /
  `graph-review.next-unreviewed` / `graph-review.approve` / `graph-review.reject` /
  `graph-review.claim-approve` / `graph-review.chat` / `graph-review.graph-chat`
  （+ §11.1 で `graph-review.open-deliberation` / `graph-review.new-chat`、
  §12 で `graph-review.voice`）。
- マニュアル: `docs/manual/teacher/26-admin-graph-review.md`（新設。操作要素1つ=1節、
  無効化され得る要素は「無効になっている場合」の節を持つ）。
- フロントの `data-ui-anchor` は上記と1:1（双方向網羅テストが落ちないこと）。

## 8. テスト・ガードレール

- `test_graph_review_core.py`: grounding の決定論構築（層分割・display_order 順・
  未レビュー抽出）/ テキスト整形（語彙・上限の省略注記・confidence 非漏洩）/
  grounding の最初の user メッセージ限定注入 / degraded 縮退。
- `test_graph_review_api.py`: approve のサーバ側ゲート（inputs/outputs・出典欠落 → 422）/
  遷移が内容フィールドを触らないこと / claim review の語彙 422 / 副作用の共通化
  （監査・却下伝播・R層フック）/ graph-session の get-or-create・所有者ゲート・
  グラフ未構築 422・CostGate 429（数値非漏洩）。
- `test_graph_review_guardrails.py`: `core/deliberation/graph_dialogue.py` が FastAPI 非
  import / グラフ全体対話が注釈を書かない（`element_annotations` への INSERT 不在）/
  AI 応答経路から承認 API を呼ぶコードが無い / プロンプトの仮説文体・断定禁止の契約
  フレーズ / confidence 生値非漏洩 / CostGate 相乗り（専用 env 定数を作っていない）。
- `test_graph_review_ui_static.py`: モーダル構造 / 承認・却下・claim 承認ボタンと
  data-ui-anchor / ES5（アロー関数・const 不使用）/ graphView への委譲
  （レイアウト・spec 生成・オプションは graphView から取得。`vis.DataSet` /
  `vis.Network` の**構築自体**は各画面が行う — スタジオも同じ分担。スタイル定数の
  二重実装が無いことを検証する）。
- 既存網羅: `test_admin_help_ui_anchors.py`（アンカー3点セット）・
  `test_docs_registry_guardrails.py`（migration 075・本設計書の索引）・
  スタジオ回帰（既存 studio ui_static テスト群）。

## 9. 非スコープ（v1）

- **一括承認**（説明レビューキューの bulk-review 同型の拡張は Phase 2 候補。v1 は
  1件ずつ + 「次の未レビューへ」ナビ）
- **edge の承認操作**（edge の review_status は導出値 — 元の component / claim /
  derivation 側を確定するのが筋）
- **equation / evidence / derivation ノードの承認**（承認ステータスを持つのは
  component / claim のみ）
- **G層 To-Do ルール（`material.components_unreviewed`）** — 解析直後は常に全件
  未レビューで恒常点灯するため、運用実測後に判断（カテゴリギャップ §4.6 と同じ姿勢）
- **学習者向け表示** / グラフ全体対話からの候補注釈生成 / レビュー担当の分担・割当

## 10. 設計判断の記録

- **なぜ承認をフル PUT でなく遷移専用 API にするか**: グラフレビュー画面は component の
  編集フォームを持たない。フル PUT を使うと「画面が読み込んだ時点のオブジェクト」で
  上書きすることになり、スタジオでの同時編集を巻き戻す。`/reject` の前例に合わせ、
  遷移だけを行う API に分離した。
- **なぜグラフ全体対話は注釈なしか**: 候補注釈（element_annotations）は要素単位の
  commit ルーティング（C層 explanation / summary / teacher_notes）を前提にしており、
  「グラフ全体」には返す先がない。全体対話は見取り図の検討に限定し、確定につながる
  操作は必ずノード（要素）単位に降りてから行う。
- **なぜ CostGate を W層と共有するか**: 本画面の対話は W層対話の別入口であり、同一
  教員の同一作業文脈で消費される。上限を分けると「どちらの上限に当たったか」の説明
  コストが増えるだけで保護は増えない。U層 feature の分離で計測上の区別は保たれる。

## 11. 実装記録（2026-08-29）

同日、本設計のとおり v1 を全実装した。設計からの変更点・確定事項:

- **GR8 の実装方式変更**（§2/§6 に反映済み）: 新ファイルへの描画ロジック抽出ではなく、
  原稿スタジオが `window.LectureStudio.graphView` として純関数群を公開する方式にした。
  既存の静的ガードレール群がスタジオ内の関数本体を固定していたため（単一実装という
  不変条項の実質は同じ）。スタジオ側は `lsGraphForCurrentLayer` →
  `lsGraphFilterByLayer(graph, filter)` の純粋化と、`lsInitComponentGraphNetwork` からの
  `lsGraphVisNodeSpec` / `lsGraphVisEdgeSpec` / `lsGraphNetworkOptions` の抽出のみ
  （挙動不変。studio 系静的テスト全通過で確認）。
- **API パスは `graph-sessions`（複数形）**: W層ガードレール
  （`test_deliberation_guardrails.py` の POST 許可リスト）に整合させた。
- **既存 `/reject` の権限ゲートを是正**: 従来は `_ensure_editable(course_id)` のみで、
  course_id を持たないパイプライン生成 component が常に 404 だった。新設の
  `_ensure_component_editable`（document 単位が主経路・course フォールバック付き）を
  approve / reject の両方が使う。
- **`reference_index.claims` に `review_status` を追加**（additive）: レビュー画面の
  claim 行が承認ボタンの活性判定に使う。
- **U層/M層登録**: `deliberation:graph_chat` を `KNOWN_FEATURES` +
  `_FEATURE_ENV_SETTINGS`（`deliberation_llm_model` / fast）に登録。scene は
  `deliberation:` prefix で SCENE_DELIBERATION に自動的に束ねられる。
- **実装ファイル**: migration `backend/db/075_graph_dialogue_sessions.sql` /
  `backend/core/deliberation/graph_dialogue.py` / `routes/deliberation.py`
  （graph-sessions 2本）/ `routes/theory_components.py`（approve・claim review・
  `_apply_claim_review_side_effects` 抽出）/ `frontend/public/js/admin-graph-review.js`
  （ES5・`window.GraphReview`）/ admin.js（行ボタン・DI・アンカー解決）/
  admin.html / styles.css（テーマ変数のみ）。
- **3点セット**: マニュアル `docs/manual/teacher/26-admin-graph-review.md` +
  `ADMIN_UI_ANCHORS`（graph-review.* + materials.row-graph-review。正確な件数は
  `backend/tests/test_admin_help_ui_anchors.py` が正）+ data-ui-anchor。
- **テスト**: `test_graph_review_{core,api,guardrails,ui_static}.py` +
  既存網羅（anchors / docs registry / studio 静的群）。
- **残課題**: docker 実機 E2E（グラフ描画・対話・承認の通し確認）。原稿スタジオ側の
  論理要素カードの承認（フル PUT 経路）は非改変のまま — 将来 approve エンドポイントへ
  寄せるかは別判断。

### §11.1 実装レビューと是正（2026-08-29 同日・Opus 3系統の敵対的レビュー）

初回実装（8cdead7）に対し独立レビュー（backend / frontend / integration）を実施し、
確認された欠陥を全て是正した:

- **[critical] approve/reject を遷移専用 UPDATE に変更**（`_transition_component_review`）:
  旧実装の `_dump_model(existing)` → `_update_component` 往復には ①`_row_to_out` が
  `component_type` に自由語彙（component_type_text）を投影するため CHECK 制約違反で
  500 ②`TheorySourceScope` が extra を落とすため `source_scope.legacy_ids` /
  `figure_id` / `figure_key` を破壊、の2欠陥があった。遷移は status / review_status
  （承認時は maturity_source / validation_warnings も）だけを UPDATE し、監査は
  **実行者 user_id 付き**で記帳する（`_update_component` 内の記帳は changed_by=NULL
  だった）。却下伝播は従来同等。
- **[critical] 集約 main ノード（graph-native ID）の 500 防止**: `theory_op_0001` 等は
  theory_components 行を持たず `CAST(:id AS uuid)` が DataError → 500 だった。
  `_get_component` / `review_claim` に UUID 事前判定（`_is_db_uuid`）を追加して 404 化し、
  UI は非 UUID ノードに承認/却下ボタンを出さず事実文で案内（設計 §3 の規定どおり）。
- **[critical] レビューループの閉塞是正**: stored graph の焼き込み `review_status` が
  常に優先され、承認しても再取得に反映されなかった。**人間の判断**
  （approved/rejected/needs_revision 系）は live の `theory_components.review_status`
  を優先する（`_normalize_stored_component_graph` + core 側
  `merge_live_review_statuses` — 導出語彙 source_backed 等は焼き込み値を保つ）。
- **[major] narrative キー是正**: 永続キーは `graph_summary`（旧 `summary` 参照は本番で
  常に空 — テスト fixture のキー誤りが隠していた）。
- **[major] セッション上限後の再開手段**: graph-sessions に `force_new=true` を追加し、
  UI はグラフ全体対話の 429 時のみ「新しい対話を開始」（`graph-review.new-chat`）を出す
  （旧対話の記録は残る = GR7）。
- **[minor 一式]**: CostGate 消費を全 422 経路の後へ移動 / `_resolve_graph_document` に
  SYSTEM_ADMIN 分岐（編集ゲートと対称）/ 承認可能性チェックに name・source_chunks を
  追加（PUT 経路 `_validate_for_review` と同基準）/ `_NODE_CLAIM_ID_KEYS` を UI と同じ
  4キーに拡張 / claim レビューの TOCTOU 404 化。
- **[frontend 一式]**: チャット送受信のセッションキャッシュ書き戻し（タブ往復で会話が
  消えない）/ 送信時のモード・ノード固定（作成中の切替で別対象へ grounding される競合の
  解消）/ グラフ未ロード時の null ガード / vis 不在の事実文 / 操作結果メッセージの
  再描画持ち越し / 未解決 claim の内部 ID 非表示 / 承認後再読み込みでのズーム・パン維持 /
  「深く検討」にアンカー付与。アンカーは +2（`graph-review.open-deliberation` /
  `graph-review.new-chat`）。

## 12. 音声対話追補（2026-08-29）

同日、グラフレビュー画面のチャットをハンズフリー音声で進められるようにした
（migration なし・新テーブルなし・新しい判断経路なし）。

- **目的**: 学習画面のハンズフリー音声会話と同等の操作感を、教員のレビュー画面にも
  持ち込む。グラフを見ながら発話し、応答を聞きながら次を考える — キーボードから手を
  離してレビューを進めるための入出力手段であって、判断の経路ではない。
- **学習側資産を流用しない判断**: 学習画面の音声ループは `app.js` にベタ書きで、既存の
  静的テストが本文の文字列を直接検査している（学習側の改変は回帰の危険が大きい）。
  そこで **DOM 非依存のエンジン `frontend/public/js/admin-voice-chat.js`**
  （ES5・`window.AdminVoiceChat`・VAD 定数とセグメント方式は学習側と同一）を新設し、
  画面・API・文言はすべて呼び出し側がコールバックで注入する形にした。**学習側
  （`app.js` / `index.html` / `routes/learning.py`）は非改変**。
- **API 2本**: `POST /api/admin/deliberation/voice/transcribe`（multipart・1発話分・
  上限10MB）/ `POST /api/admin/deliberation/voice/speak`（MP3 base64）。いずれも
  `_require_teacher` の fail-closed で、**DB を変更しない**。読み上げ前に
  `core.tts.strip_text_for_speech` で LaTeX・markdown 記号・出典マーカーを除去する。
  openai プロバイダ以外では STT が 503（未対応を正直に返す）。
- **コスト上限（day-only CostGate）**: 正本は
  `core/deliberation/dialogue.py::check_and_count_voice_call`。STT / TTS 共通の
  1ユーザー日次カウンタ1本で、env は `DELIBERATION_VOICE_MAX_CALLS_PER_DAY`（既定
  200）。**GR5 の対話上限（W層 `DELIBERATION_MAX_CALLS_*`）とは独立**  — 音声は
  入出力の手段であって対話ターンそのものではないため、セッション単位の上限は持たない。
  上限到達は 429 + 数値を含まない日本語事実文。
- **U層 feature の分離**: `deliberation:voice_stt` / `deliberation:voice_tts` を
  `KNOWN_FEATURES` に追加（学習側 `learning:voice_*` と混ぜない）。scene は
  **読み取り専用の音声場面**へ束ねる（STT は `settings.llm_transcribe_model` 直参照・
  TTS は provider 固定で、どちらも policy の解決経路を通らない。設定できるのに何も
  起きない場面を増やさない — M4/M5）。`llm_policy.scene_for_feature` の音声分岐は
  `deliberation:` prefix の分岐より前に置く。
- **GR1 の維持**: 音声からできるのはチャットの送受信だけで、承認・却下 API を呼ぶ経路は
  作らない。文字起こし結果は既存のテキスト送信経路（`sendChatText`）にそのまま渡り、
  表示・セッション・上限の扱いはテキスト入力と同一になる。確定は従来どおり教員の
  ボタン操作のみ。
- **停止条件と数値非表示**: 429（音声上限・対話上限のいずれも）を受けたらループを止め、
  事実文だけを残す（回し続けない）。画面を閉じたとき・再度開いたときも必ず停止して
  マイクを解放する。残回数・上限値・秒数などの数値は UI に出さない（GR3）。
- **実装ファイル**: `frontend/public/js/admin-voice-chat.js`（新設）/
  `frontend/public/js/admin-graph-review.js`（🎤 トグル・状態表示・`sendChat` →
  `sendChatText(content, cb)` の分離・音声配線）/ `frontend/public/js/admin.js`
  （`getAuthToken()` 新設と DI — multipart は JSON 前提の `apiFetch` を通せないため
  素の fetch に Bearer を載せる）/ `frontend/public/admin.html`（読み込み順は
  lecture-studio < voice-chat < graph-review < admin.js）/ `css/styles.css` /
  `backend/api/routes/deliberation.py` / `backend/core/deliberation/dialogue.py` /
  `backend/core/config.py` / `backend/core/llm_usage/schema.py` /
  `backend/core/llm_policy.py` / `.env.example`。
- **3点セット**: マニュアル節
  `docs/manual/teacher/26-admin-graph-review.md#voice-chat`（音声で対話する）+
  `ADMIN_UI_ANCHORS` の `graph-review.voice` + フロントの
  `data-ui-anchor="graph-review.voice"`（正確な件数は
  `backend/tests/test_admin_help_ui_anchors.py` が正）。
- **テスト**: `test_graph_review_voice_api.py`（権限 fail-closed・空/過大音声・上限 429 の
  数値非漏洩・`strip_text_for_speech` 適用・U層 feature の帰属・DB 非変更）+
  `test_graph_review_ui_static.py::TestVoiceChat`（エンジンの ES5・`window.AdminVoiceChat`・
  DOM 非依存＝`data-ui-anchor` 不在・読み込み順・音声区画から承認/却下 API を呼ばない
  こと・429 でのループ停止・close での停止）+ 既存網羅（anchors / docs registry）。
- **残課題**: docker 実機 E2E（マイク許可・無音区切り・読み上げの通し確認）。

## 13. 「深く検討」の要素解決の是正（2026-09-01）

グラフレビューでノードを選び「深く検討」を押すと、ほぼ全てのノードで
「この要素の指定が不正です（equation は document_id が必要です）」と表示され、
W層モーダルが開けなかった。原因と是正:

- **根本原因（ID の層のずれ）**: 理論操作グラフのノード ID は **graph-native**
  （`normalizer.py` が付ける `theory_op_0001` = 集約 main ノード / `eq_op_0001` =
  式の詳細層）で、`persistence.persist_component_graph` の
  `component_id_map.get(agent_id, agent_id)` にも一致しないため、そのまま
  `theory_component_graphs.graph_json` に残る。つまり main / equation_detail の
  **全ノードは theory_components の行を持たない**（DB UUID になるのは、derivation が
  空でフォールバックした component ノードと debug 層の component ノードだけ）。
  旧 `openDeliberation` はこのノード ID を `theory_component` の element_id として
  そのまま渡していたため、overview（厳格な `refs.resolve`）が
  `ElementResolutionError(kind="invalid")` → 422 を返し続けていた。
- **表示の二重の誤り**: フロント `deliberation.js::_renderError` は 422 を一律
  「equation は document_id が必要です」と表示していたため、原因と無関係の文言が出ていた。
- **是正1（受け口）**: `overview` / `annotations`（一覧）/ `sessions`（作成）を
  `refs.resolve_with_agent_id` に切り替え、theory_component / theory_claim の
  **agent 側 ID（`comp_003` 等）を `document_id` スコープで解決**できるようにした
  （context ルートと同じ規約。document_id が無ければ解決しない fail-closed）。
  永続化・後続呼び出しに載るのは常に解決後の DB UUID なので
  `POST .../sessions/{id}/messages` は厳格な `resolve` のまま。
- **是正2（渡す ID）**: `admin-graph-review.js` に `deliberationTargetId(node)` を追加し、
  DB UUID → `representative_component_id` → `linked_component_ids[0]` の順で代表要素へ
  解決する。どれも無いノードでは **ボタンを出さず事実文で案内**する（承認・却下ボタンの
  `isDbUuid` 分岐と同じ流儀。422 の裏に隠さない）。集約ノードでは「深く検討は集約元の
  代表要素を開く」ことを事実文で明示する。ノード対話（W層 sessions）も同じ解決規則を
  使い、解決できないノードではリクエストせず事実文だけを出す。
- **是正3（事実文）**: `_http_from_resolution_error` の detail を日本語の事実文に統一し
  （内部 ID・英語の例外メッセージを教員 UI に出さない。原因は logger 側へ）、
  `_renderError(status, detail)` はサーバの detail をそのまま表示する（無いときだけ
  status 由来の汎用文言へ縮退）。agent 側 ID のときは `_documentIdQuery` が
  `document_id` を必ず載せ、overview の応答 `ref` で `chatState.ref` を正準化する。
- **テスト**: `test_graph_review_api.py`（agent 側 ID の解決・document スコープ外は 404・
  document_id なしは DB を触らず 404・graph-native ID は解決しない・detail の事実文性と
  内部 ID 非漏洩・sessions/annotations が正準 DB UUID を使うこと）+
  `test_graph_review_ui_static.py::TestDeliberationTargetResolution` +
  `test_deliberation_ui_static.py`（旧固定文言の不在・detail 優先・agent 側 ID の
  document スコープ・ref の正準化）。
- **非変更**: A層（`src/episteme_graph/agents/`）・承認 API・グラフ描画・stored graph の
  ノード ID（既存グラフを書き換えない）。集約 main ノードそのものを承認対象にはしない
  （v1 の非スコープ）。

### §13 追補（同日・統合時）

- **原稿スタジオの同根修正**: `admin-lecture-studio.js` のグラフ詳細カード
  （`lsGraphNodeCardOpts`）も生ノード ID を `openElement("theory_component", ...)` に
  渡していたため、同じ規則の `lsGraphDeliberationTargetId(node, nodeId)`
  （DB UUID → 代表 component → linked 先頭）に切り替え、解決できないノードでは
  「深く検討」ボタンを出さない。回帰は
  `test_graph_review_ui_static.py::TestStudioDeliberationTargetResolution`。
- **review_reasons の表示是正（グラフレビュー詳細ペイン）**: サーバの読み時射影
  （`theory_components.py::_normalize_stored_component_graph`）に合わせて、
  ①レビュー要求（従来どおり「要確認の理由: 」・警告色）②`source_backed` ノードの
  warning（「解析メモ（参考）: 」・非警告色）③承認済みノードの解析時点メモ
  （`review_reasons_at_analysis` を「解析時点のメモ（承認済みのため確認は不要です）: 」・
  非警告色）を見出しと色で区別する。ヘッダーに `graph_updated_at` の事実文
  「（YYYY-MM-DD の解析結果を表示しています）」を常時表示し、焼き込みグラフの鮮度を
  隠さない。回帰は `test_graph_review_ui_static.py::TestReviewReasonPresentation`。
