# 図・画像と概念構造の接続 — 問題整理と修正方針

- 作成日: 2026-07-18
- 対象: PDF 解析パイプライン（A層）における「図 ⇄ 上位概念（claim / component / thesis）」
  「図 ⇄ 下位概念（パーツ・装置候補）」の接続
- 関連文書: `docs/features/image_pipeline_knowledge_library_design.md`（L層）/
  `docs/features/element_context_lens_design.md`（W層 #498）/
  `docs/features/contextual_figure_analysis_iterative_verification.md`（#499）

## 0. 背景

図・画像がパイプラインで処理されたとき、上位概念と下位概念が適切に図に接続されるかを
調査した（2026-07-18）。結論: **スキーマ上の接続フィールドは存在するが、populate 経路が
構造的に空振り・未実装・死にフィールドのいずれかで、実質機能しているのは
「図 → セクション」のみ**。本書はその問題整理と修正方針の正本である。

## 1. 現状の接続マトリクス（調査結果）

| 接続 | 状態 | 原因 |
|---|---|---|
| 図 → セクション | ✅ 動作 | caption_block_id 経由（context_lens `_build_figure`） |
| 図 → claim（`FigureRecord.linked_claim_ids`） | ⚠️ 実質常に空 | §2.1 |
| claim → 図（`ClaimObjectRecord.figure_ids`） | ❌ 未実装スタブ | §2.2 |
| 図 → component（`linked_component_candidates`） | ❌ 死にフィールド | §2.3 |
| 図 → thesis | ⚠️ 連鎖不通 | §2.4（claim リンク経由のため） |
| 図 → パーツ候補（apparatus parts） | ⚠️ 表示のみ | 非ナビゲーブル項目（仕様どおり、v1 維持） |
| 図 → 装置 theory_component | ⚠️ document 単位に縮退 | §2.5（figure_id 喪失） |
| 図 → TheoryOperationGraph | ➖ 意図的非組込 | v1 仕様（式 backing なし）。変更しない |

## 2. 問題点の詳細

### 2.1 図 → claim リンクが構造的に空振りする

`_run_figure_table_semantics`（`backend/core/document_pipeline/orchestrator.py`）は
`claim_link_index`（claim の source span **block_id** → claim_id 群）を作り、
`FigureTableSemanticsAgent` は **caption block の block_id** で index を引いて
`linked_claim_ids` に入れる（`agent.py` の `_build_figure_record`）。

ところが claim スパンの供給源である rhetorical_role は
`_TARGET_BLOCK_TYPES = {"body_paragraph"}`（`rhetorical_role/input_builder.py`）に
限定しており、**caption block が claim スパンになることはない**。よって caption
block_id で index を引いても常に空で、validator の `figure_missing_linked_claim_ids`
警告がほぼ全図で立つ。

### 2.2 claim → 図（figure_ids）が未実装スタブ

`ClaimObjectBuilder._link_figures_tables`（`claim_object_builder/builder.py`）は常に
`([], [])` を返すスタブ。コメントは「Downstream FigureTableSemanticsAgent populates
this via cross-link pass」と言うが、**そのクロスリンクパスはどこにも実装されていない**
（figure_ids への書き込み箇所はパイプライン全体に存在しない）。

### 2.3 図 → component が死にフィールド

`FigureRecord.linked_component_candidates` は agent が常に `[]` をセットし、以降どこも
populate しない。context lens（#498）は読む側の実装（`related_component_candidate`
upper 項目）を持つが、入力が常に空。

### 2.4 図 → thesis も連鎖的に不通

context lens の図→thesis 接続（`supports_thesis`）は linked claim 経由なので、
§2.1 が空である限り発火しない。

### 2.5 装置 theory_component の figure_id が persist 時に喪失する

`build_apparatus_components`（`component_assembly/apparatus_components.py`）は
`ComponentRecord.source_scope` に `figure_id` / `figure_key` / `match_status` 等を
正しく載せている。しかし `persist_components`（`document_pipeline/persistence.py`）が
source_scope を `{"document_id", "legacy_ids"}` で**全上書き**するため DB 行から
図対応が失われ、context lens は「装置・部品候補は論文単位の一覧です（図ごとの厳密な
対応付けは未対応）」の縮退表示になっている（`_load_apparatus_components` docstring）。

## 3. 修正方針（3本柱）

### 設計判断: リンクの正本は `FigureRecord.linked_claim_ids` の一箇所に置く

claim 側 `figure_ids` は populate **しない**。理由:

1. パイプラインのステージ artifact は各ステージ実行直後に `ctx.save_artifact` で保存
   される。figure_table_semantics（後段）の結果で claim_object_builder（前段）の
   artifact を書き戻すと、resume（`should_use_artifact`）の冪等性と「artifact =
   そのステージの出力」という不変条件を壊す。
2. 読み側（context lens `_build_claim`）には既に figure_table_semantics の
   `linked_claim_ids` を逆引きする fallback が実装済みで、正本が populate されれば
   claim → 図の lower 項目は追加実装なしで発火する。

`_link_figures_tables` スタブは削除せず、**正本が figure_table_semantics 側である旨に
コメントを訂正**する（誤誘導の解消）。

### F1: mention ベースの claim ⇄ 図・表クロスリンク（決定論・非LLM）

- 実装場所: `src/episteme_graph/agents/figure_table_semantics/`（新モジュール
  `crosslink.py` + `agent.py` からの呼び出し）。
- 図番号の導出: `figure_id`（`fig_5.2` 形式）/ label から番号文字列を導出
  （`backend/core/document_pipeline/figure_context.py::_derive_figure_number` と
  同じ規約。agents は backend を import できないため src 側に同等実装を置き、
  regex は figure_context.py と字面パリティを保つ）。
- 参照メンション regex: `(?:Figs?\.?|Figures?|図)\s*{num}(?![0-9.])`（大文字小文字
  無視）。表は `(?:Tabs?\.?|Tables?|表)\s*{num}(?![0-9.])`。
- 対象ブロック: `block_type == "body_paragraph"` のみ（claim スパンの供給源と一致）。
  caption block 自身は除外。
- リンク付与: メンションを含む block の block_id で `claim_link_index` を引き、
  ヒットした claim_id 群を当該 figure/table の `linked_claim_ids` に追加
  （既存の caption 由来分を先頭に維持しつつ重複排除・文書順）。
- 既存の caption block_id ルックアップは無害なので残す。
- `figure_missing_linked_claim_ids` 警告はメンションが無い図には引き続き立つ（正直）。

粒度は **block 単位**（メンションと claim スパンが同一 block にあればリンク）。
span 単位の精密化は非スコープ（§7）。本文が図番号を明示引用している事実に基づく
リンクなので、context lens 上の status は既存どおり `explicit` 扱いで良い。

### F2: `persist_components` が agent の source_scope を保持する

- `persist_components` の source_scope 構築を「全上書き」から「**agent 側
  source_scope をベースに `document_id` / `legacy_ids` を上書きマージ**」に変更する。
  `legacy_ids = [component_id]` の既存セマンティクス（`_component_id_lookup_from_rows`
  が依存）は不変。
- これにより apparatus 候補コンポーネントの DB 行に `figure_id` / `figure_key` /
  `match_status` / `matched_library_entry_id` 等が残る。
- claim 由来コンポーネントの source_scope が空 dict の場合は従来と同一の形になる
  （後方互換）。source_scope の全読み手（legacy_ids 依存箇所）を grep で確認し、
  キー追加が壊さないことをテストで固定する。
- persistence.py 内の別経路（クエリ由来コンポーネント候補等）は対象外。

### F3: context lens の読み時導出（W層、読み取り専用のまま）

`backend/core/deliberation/context_lens.py` のみ変更（read-only 原則は不変）:

1. **図単位の装置コンポーネント対応**: `_load_apparatus_components` が source_scope
   も SELECT し、`_build_figure` は `source_scope.figure_id`（DB UUID）または
   `figure_key` が当該図と一致するコンポーネントを優先表示する。図対応キーを持つ
   コンポーネントが1つでもあれば「論文単位の一覧」note を出さない。キーを持たない
   legacy 行は従来どおり document 単位表示 + note（P4: 情報を落とさない）。
2. **図 → component（claim 交差）**: 図の `linked_claim_ids`（F1 で populate、
   agent-id）を `_claim_id_lookup` で DB UUID に解決し、コンポーネント行の
   `evidence_claims`（persist 時に DB UUID へ remap 済み）と交差するコンポーネントを
   upper レーンに `related_component_candidate` / status `inferred` で出す。
   既存の `linked_component_candidates` 読み出しは残す（将来 populate されれば併用）。
3. **図 → thesis**: F1 により linked claim が立てば既存実装がそのまま発火する
   （コード変更不要、テストで発火を固定）。
4. **claim → 図**: 既存 fallback（fig_records の linked_claim_ids 逆引き）が発火する
   （コード変更不要、テストで固定）。

## 4. 設計原則との整合

- **決定論・非LLM**: F1〜F3 とも LLM を使わない（figure_table_semantics の
  caption-first 原則、W6 同期パス軽量の維持）。
- **candidate-only / 確定は人間**: 新設リンクはメンション（本文の明示引用）由来の
  `explicit` と交差由来の `inferred` のみ。review_status / source_backed の自動昇格は
  一切しない。
- **P4 情報を落とさない**: 既存フィールド・既存行を削除しない。legacy 行の縮退表示を
  維持。警告は消さず、リンクが付いた図だけ警告が自然に消える。
- **A層改変について**: F1 は A層（`src/episteme_graph/agents/`）自体のパイプライン
  修正であり、「W層等の上位層は A層を読むだけ」という層間原則には抵触しない
  （上位層の都合で A層を触るのではなく、A層の未実装接続を A層内で完成させる）。
- **TheoryOperationGraph 非組込は不変**: 装置コンポーネントに式 backing が無い以上、
  main/equation_detail グラフへは入れない（v1 仕様のまま）。

## 5. 実装タスク分割

| タスク | 変更対象 | テスト |
|---|---|---|
| F1 クロスリンク | `src/.../figure_table_semantics/crosslink.py`（新規）/ `agent.py` / `claim_object_builder/builder.py`（コメント訂正のみ） | `src/tests/agents/figure_table_semantics/` |
| F2 source_scope 保持 | `backend/core/document_pipeline/persistence.py` | `backend/tests/`（persistence） |
| F3 読み時導出 | `backend/core/deliberation/context_lens.py` | `backend/tests/`（context lens） |

## 6. テスト計画

- F1: 図・表それぞれで (a) `Fig. 5.2` / `Figure 5.2` / `図5.2` / `Figs. 3` 変形の
  ヒット (b) `Fig. 5.21` への誤ヒットなし（`(?![0-9.])`）(c) caption block 自身の除外
  (d) claim の無い block はリンクなし (e) 重複排除 (f) メンション無し図は
  linked_claim_ids 空のまま + 警告維持。
- F2: apparatus ComponentRecord を persist → DB 行 source_scope に figure_id /
  figure_key / document_id / legacy_ids が共存。claim 由来（source_scope 空）は
  従来形。`_component_id_lookup_from_rows` 互換。
- F3: (a) 図 lens: figure_id 一致の装置コンポーネントのみ lower に出る + note 非表示
  (b) legacy 行のみなら従来表示 + note (c) claim 交差の component が upper に inferred
  で出る (d) linked claim 経由で thesis が upper に出る (e) claim lens: 図が lower に
  出る（既存 fallback の発火）。

## 7. 非スコープ

- `Figs. 5.2 and 5.3` の2番目以降の番号（列挙参照の展開）
- span 粒度のリンク（v1 は block 粒度）
- claim 側 `figure_ids` の populate（§3 の設計判断）
- TheoryOperationGraph への図・装置ノード組込
- apparatus_semantics の figure_id（DB UUID）と figure_table_semantics の
  figure_id（`fig_<label>`）の語彙統一（既存の `_matching_figure_record` /
  figure_key 突合で吸収済み）

## 8. 実装記録（2026-07-18 実装完了）

F1/F2/F3 に加え、実装中に **F1b（orchestrator の claim_link_index キー語彙バグ）** を
発見・修正した。全テスト green（backend 4,613 passed / 14 skipped、src 1,562 passed）。

### F1b: claim_link_index のキーが span_id だった（実装中に発見した第4の欠陥）

`_build_figure_table_semantics`（orchestrator.py）は claim_link_index を
`claim.source_span_ids` の **span_id**（`span_001` 形式）で構築していたが、コメントと
agent 契約は block_id キーだった。さらに rhetorical_role の span_id は **block ごとに
`span_001` から振り直され文書内で一意でない**（`repair.py` の fallback
`span_{i+1:03d}` 等）ため、単純な span→block 一意マップは成立しない。修正は2経路 join:

1. **evidence join（主経路・非曖昧）**: `claim.source_evidence_ids` → evidence record の
   block_id。builder は claim の evidence を必ず自 block からのみ解決するため衝突フリー。
2. **span map（副経路）**: `qualified.qualified_spans` の span_id → block_id を
   **単一 block に一意対応するときのみ**使用。
3. 未知・曖昧な span_id は raw キーのまま保持（P4。crosslink は body block_id で
   しか引かないため無害）。

`_stage_figure_table_semantics` が `ctx.qualified` を渡すようになった（resume 経路は
`ClaimQualificationResult.from_dict` が span_id/block_id を復元するためそのまま動く）。

### 変更ファイル

| 対象 | ファイル |
|---|---|
| F1 | `src/episteme_graph/agents/figure_table_semantics/crosslink.py`（新規）/ `agent.py` / `claim_object_builder/builder.py`（コメントのみ） |
| F1b | `backend/core/document_pipeline/orchestrator.py`（`_stage_figure_table_semantics` / `_build_figure_table_semantics` のみ） |
| F2 | `backend/core/document_pipeline/persistence.py`（`persist_components` の source_scope マージ） |
| F3 | `backend/core/deliberation/context_lens.py` |
| テスト | `src/tests/agents/figure_table_semantics/test_claim_crosslink.py`（新規10件）/ `backend/tests/test_document_pipeline.py`（+10件: F2 3件 + F1b 7件）/ `backend/tests/test_deliberation_context_lens.py`（+8件） |

### F2 の互換性確認結果

`theory_components.source_scope` の全読み手（context_lens / refs.py / inventory.py /
decomposition.py / personal_graph/queries.py / atlas_state.py / admin.py KPI /
routes/theory_components.py（Pydantic `extra='ignore'` でキー増加を無視）/
routes/export.py / lecture_studio / admin-lecture-studio.js）を確認し、キー**追加**で
壊れる読み手なし。`legacy_ids = [component_id]` セマンティクスは不変。

### 残課題（今後の改善候補）

- E2E（docker 環境での実 PDF 再解析）による発火確認は未実施。
- `figure_missing_linked_claim_ids` 警告の実データでの減少率は定量未評価。
- 図対応キーあり/なし混在時（F2 適用前後の部分再解析）は図対応キー側を優先する
  仕様（再解析は DELETE + 全 INSERT のため実運用では混在しない想定）。
