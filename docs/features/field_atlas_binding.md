# 分野の地図 — 個人層 binding とデプロイ運用 (Issue: 個人層 binding の整備)

仕様書: `field_atlas_overlay_spec.md` / 関連: `field_atlas_skeleton.md`

## 背景

issue F 完了後のレビューで、地図は描けているが「いまここ (現在地の実感)」が
ほとんど動かないことが判明した。原因は個人層 (`interest_traces.payload.atlas.node_id`) へ
値が入る経路が細く、地図内アクション経由でしか書かれなかったこと。本 issue はこの
供給経路 (topic ↔ 骨格概念 binding) を整備する。

## topic → 骨格概念の解決 (gap1 / gap2)

決定論的・LLM 非依存。以下の優先順で解決する。

1. **明示 binding**: `course_data.topics[].atlas_node_id` (または `atlas_concept_id`) が
   骨格の既知 id なら採用 (authoring-time で最強)。
2. **コーパス binding**: topic の `material_chunk_ids` → `theory_components` →
   骨格の `concept_bindings` 経由で骨格概念へ。既存の corpus binding を再利用する。
   hot path (チャット) では使わず、focus 解決など on-demand の経路でのみ使う。
3. **ラベル一致 (fallback)**: 正規化した `topic.title` と概念 label/id の一致 → 包含。
   最後の縮退先は region ラベル。gap2 のクライアント文字列一致 (`atlas-cues.js`) を
   サーバ側の決定論的一致で置き換えた。

実装:
- `core/atlas.py:match_topic_to_concept` (純粋・DB 非依存)
- `core/atlas_state.py:resolve_topic_concept_via_corpus` / `build_component_concept_map`
- gap1 記録: `api/routes/learning.py:_atlas_topic_attribution` が通常学習の往復で
  topic → 概念を解決し `interest_traces.payload.atlas` に焼き込む (best-effort・
  地図アクション由来は上書きしない)。これで `now`/足跡/隣接の光が通常学習で動く。
- gap2 focus: `GET /api/atlas?course=&topic=` がサーバ側で focus を解決し、
  応答トップレベル `focus` と `initial_selection` に反映する。フロントは
  `data.focus` を優先し、ラベル一致は縮退にのみ使う。

## コース ⇄ カートリッジ対応 (gap3)

`core/atlas_state.py:resolve_course_cartridge` が以下で導出する。

1. `course_data.cartridge_id` の明示指定
2. `course_data.sources[].material_id` → `document_analysis_runs.cartridge_id`
   (status='completed' の最頻カートリッジ)
3. 既定カートリッジ (`Settings.default_cartridge_id`) へ縮退

フロントは `window.AtlasContext = {courseId, topicId, topicLabel}` (app.js の
`selectTopic` で設定) を配線し、`atlas-data.js` が `course`/`topic` 付きで取得する。

## データソースの運用ガード (gap4)

データソース既定を **`api`** に倒した (以前は `fixture` 既定で、本番切替を忘れると
モック地図が全ユーザーに出るリスクがあった)。

- 設定: `ATLAS_DATA_SOURCE` (env, 既定 `api`)。`backend/core/config.py:Settings.atlas_data_source`。
- 公開: `GET /api/atlas/runtime-config` が `{"data_source": ...}` を返す。
- フロント (`atlas-data.js`) の解決順:
  `window.ATLAS_DATA_SOURCE` > `localStorage` > runtime-config > 取得不能時は `api`。
- **デプロイ手順**: `.env` に `ATLAS_DATA_SOURCE=api` を設定する (`.env.example` 参照)。
  `fixture` はローカル確認用にのみ明示的に使うこと。

## gap5 (将来対応・本 issue では注記のみ)

導線②(章末)は、本アプリに明示的な「章末サマリー画面」が無いため、章境界を跨ぐ完了
(`app.js:showAtlasCueAfterAdvance`) とレクチャー完了バナー (`app.js` の chapter_end 提示)
で代替している。将来サマリー画面ができたら、その画面へ移設するのが本来形。
