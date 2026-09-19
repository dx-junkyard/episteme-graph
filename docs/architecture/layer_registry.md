# レイヤー索引表

[← ドキュメント目次](../README.md) ｜ [← アーキテクチャ概要](overview.md) ｜ 関連: [データモデル](data-model.md) / [ビジョンと思想](../vision.md)

CLAUDE.md・`docs/features/*_design.md`・実装コードを横断して積層してきた各レイヤー（層）の
名称・正本・実装場所・migration 番号を1枚にまとめた索引。
（初版は `docs/architecture/consolidation_survey_2026-07.md` Tier 0 の「レイヤー命名の混乱」
再発防止として作成。2026-08-13 のドキュメント総点検で migration 067 まで全面更新し、
**2026-09-03 の実装照合で migration 076 と新規レイヤー行まで更新**。）

## 0. 先に知っておくこと（命名の混乱への注記）

- **「第五の層」を3つの設計書が独立に自称している**: `reconstruction_loop_design.md`（R層）・
  `shared_versioning_design.md`（V層）・`exposition_layer_design.md`（E層）が、それぞれ他の2つを
  知らずに「A層・B層・C層・D層に続く第五の層」と書いている。実際の追加順は
  A→B→C→D→（Field Atlas/S）→R(036)→V(037)→状態通知基盤(038)→G(039)→L(041/042)→U(043)
  →W(048〜050)→L追補(051〜054)→二層説明(055/056)→S追補(057)→help_kb(058/059)
  →discuss観測(060)→M(061)→discuss開幕(062)→教材図(063)→W Phase5(064)
  →ランドスケープ(065)→カテゴリギャップ(066)→SL(067)→アカウントライフサイクル(068/069)
  →URL教材取得(070)→論文ディスカバリー(071/072)→コーパス回遊(073)→VA(074)
  →グラフ対話レビュー(075)→RE(076)→コーパス補完(077) であり、序数はどれにも一意に対応しない。
  序数を主張する文言は今後の設計書では避け、migration 番号ベースの参照に置き換えること。
- **E層の migration 番号は衝突している**: `exposition_layer_design.md` §5 は「migration 034」を
  提案しているが、034 は Admin Copilot が使用済み。E層は未実装のため実害はまだ無いが、
  着手時は次の空き番号（2026-09-14 時点で **086 以降**。044〜085 は使用済み — §3 参照。
  採番前に必ず `ls backend/db/` で確認する）へ採番し直すこと。
  また設計書は「設計時に migration 番号を書かない」運用を推奨する（下記のずれの再発防止）。
- **設計時想定と実装後の migration 番号がずれている組が複数ある**: 状態管理・通知基盤
  （設計書表記 039 → 実装 038）/ G層（038 → 039）/ W層 W-β（046 → 048）/
  discuss 開幕オーサリング（061 → 062）。**migration 番号の一次情報は常に
  `backend/db/0NN_*.sql` の実ファイル名**であり、設計書の文中表記ではない。
- **レター（層の1〜2文字名）は便宜的な実装単位のラベル**で、体系的な命名規則は無い
  （SL層が唯一の2文字レター＝ Stakes Ledger の頭字語）。新レイヤーにレターを新設するかは
  「CLAUDE.md・監査語彙・ガードレールテスト名で繰り返し参照する必要があるか」で判断し、
  無理に付けない（理解サイクル・help_kb・ランドスケープ等はレター無しで運用している）。
- **Field Atlas（分野の地図）内部の「S/C/P」3層モデルは、本表のアルファベット層とは別の粒度**。
  Field Atlas の仕様書は自分自身を S（骨格）/ C（キャッシュ）/ P（個人層）に分けて呼ぶ。
  本表の便宜ラベル「S層」とは由来が異なるので混同注意。
- **`doubt-atlas.js`（D2-3 前提の地図）は Field Atlas と別機能**（`doubt-` / `assumption-`
  プレフィックスで衝突回避済み）。
- **アーキテクチャ整理 Tier 3（migration 044/045）はレイヤーをまたいで既存テーブルを統合した**:
  `object_group_permissions`（044 = 010 + 035 の統合）と `user_notifications`（045 = 038 + V層
  `share_notifications` の統合）。統合してもレイヤー自体の主 migration 番号は変更されていない。
  なお **054〜077 に統合系 migration は無い**（すべて機能追加。新テーブルを作らず既存表への
  列追加・CHECK 拡張・索引追加だけで済んだ回（**064** = CHECK 拡張のみ / 067 / **069** =
  索引追加のみ / 073 / 075）も含む）。
- **索引とレイヤーの相互欠落は双方向に起きる**: かつては「実装済みなのに CLAUDE.md に無い層」
  （V層）が問題だったが、2026-08 の点検では「CLAUDE.md にあって本表・README に無い層」が
  11件見つかった。**新しい層を追加したら、①専用設計書 ②本表 ③CLAUDE.md ④docs/README.md
  （と [vision.md](../vision.md) §8）を同時に更新する**こと。
- **docs/README.md の機能7群との対応**: 本表のアルファベット層は実装単位であり、利用者から
  見た大枠は [vision.md](../vision.md) §8 の7群（構造化 / 対話と講義 / 産出と痕跡 / 地図 /
  疑いと検証 / 教員と共同体 / 運営基盤）を正とする。

## 1. レイヤー一覧

| 層 | 正式名 | 正本設計書 | 主実装ディレクトリ | migration | 実装状態 |
|---|---|---|---|---|---|
| **A層** | 構造化パイプライン（PDF解析Agentパイプライン） | 専用の1枚設計書なし。`docs/pipeline/*.md` が解説 | `src/episteme_graph/agents/`（22ディレクトリ、`agent.py` 保有19 + 非LLM builder）+ `backend/core/document_pipeline/orchestrator.py`（`PIPELINE_STAGES` 30 = named 29 + 終端マーカー） | 013, 014, 015, 016, 017 | 実装済み |
| **B層** | 学習者体験レイヤー（関心痕跡・tension・構造帰属・casual/voice 等） | 機能ごとに分散: `docs/features/learning.md` / `structure-anchored-questions.md` | `backend/core/tension/`、`backend/core/structure_anchor/`、`backend/api/routes/learning.py` | 020, 022, 025 | 実装済み |
| **C層** | 承認・共有レイヤー | `docs/features/endorsement-sharing.md` | `backend/api/routes/theory_components.py` | 021 | 実装済み |
| **D層** | 疑義・認識的地位台帳（Doubt Layer） | `docs/features/doubt_layer_issues.md` | `backend/core/doubt/` + `backend/api/routes/doubt.py` | 029〜033 | 実装済み |
| **E層** | 段階的翻訳レイヤー（Exposition Layer） | `docs/features/exposition_layer_design.md` | なし | 設計書は 034 を提案（衝突。**着手時は次の空き番号へ採番し直し**（2026-09-09 時点は 078）+ 横断基盤接続の追補が必要） | **未実装**（唯一の設計のみ層） |
| **G層** | ガイダンス層（次にやることバッジ + 状態導出型To-Do） | `docs/features/guidance_layer_design.md`（表記 038 → 実装 039） | `backend/core/admin_assistant/next_steps.py` + `admin-next-steps.js` | 039 | 実装済み |
| **L層** | 画像読み取りパイプライン + 分野別ナレッジライブラリ | `docs/features/image_pipeline_knowledge_library_design.md`（§14〜16 追補含む）+ `contextual_figure_analysis_iterative_verification.md`（#499）+ `guided_figure_reanalysis_design.md` | `backend/core/document_pipeline/figure_images.py`、`src/episteme_graph/agents/apparatus_semantics/`、`backend/core/library/` + `routes/library.py`、`backend/core/figure_presentation.py` + `routes/figure_presentation.py` | 041, 042, 051, 052/053, **054（反証型反復照合 #499）** | 実装済み |
| **M層** | 場面別 LLM モデル選択（LLM Model Selection） | `docs/features/llm_model_selection_design.md`（M1〜M10） | `backend/core/llm_policy.py` / `llm_policy_store.py` + `routes/llm_models.py` + `admin-llm-models.js` | 061 | 実装済み（Phase 0〜4。ユーザー別保存が正・tier名/金額は UI 非表示） |
| **R層** | 再構成ループ（Reconstruction Loop） | `docs/features/reconstruction_loop_design.md` | `backend/core/reconstruction/` + `routes/reconstruction.py` + `reconstruction.js` | 036 | 実装済み |
| **S層**（便宜ラベル） | 分野の地図（Field Atlas） | `field_atlas_overlay_spec.md`（オーバーレイ仕様の正本。原本消失のため **2026-08-14 の再構成版**が現行 — [不具合報告](doc_review_findings_2026-08-13.md) §1-1 は解消済み）+ `field_atlas_*.md`（骨格 / バインディング / 修正報告 / DB管理化 / 詳細パネル / 骨格エディタ）+ `atlas_binding_lifecycle_design.md` + 追補 [ノード版間対応](../features/atlas_node_correspondence_design.md)（NC1〜NC8。凍結時に教員が確定した `id_migrations` で版をまたいだ node_id を読み替える。migration なし） | `backend/core/atlas*.py` + `routes/atlas.py` / `atlas_view.py` + `atlas-*.js` | 023, 024, 026, 027, 028, 046, **057（ドメイン lifecycle）** | 実装済み |
| **SL層** | 賭け金の台帳（Stakes Ledger）＝理解サイクル Phase 3 | `docs/features/stakes_ledger_design.md`（SL1〜SL10・§15 実装記録） | `backend/core/doubt/` 配下の SL 系モジュール + `routes/doubt.py` 拡張（D層の双対拡張・既存意味論は非改変） | 067 | 実装済み（SL-1〜SL-5） |
| **U層** | LLM トークン使用量推計（Usage Metering） | `docs/features/llm_usage_metering_design.md` | `backend/core/llm_usage/` + `routes/llm_usage.py` | 043, **069（ユーザー別集計軸）** | 実装済み |
| **（運営基盤）** | アカウントライフサイクル管理（一覧・停止・削除・リセット・最終ログイン・利用実績照会） | `docs/features/account_lifecycle_management_design.md`（AL1〜AL10） | `backend/core/account_status.py` / `auth_events.py` / `account_lifecycle.py` + `routes/admin.py` User Management 節 + `routes/auth.py` | 068, 069 | 実装済み（Phase 0〜3） |
| **（運営基盤）** | URL指定による教材取得（取得先ドメイン許可リスト + SSRF ガード付きダウンローダ） | `docs/features/url_material_upload_design.md`（UF1〜UF6） | `backend/core/url_fetch.py` + `routes/admin.py` URL Fetch 節 + `admin.js`（URLモーダル / 許可ドメイン区画） | 070 | 実装済み（v1） |
| **V層** | 共有物のバージョン管理 + 更新通知 + 削除猶予 | `docs/features/shared_versioning_design.md` | `backend/core/versioning/` + `routes/versioning.py` + `versioning.js` | 037（通知は 045 で `user_notifications` に統合） | 実装済み |
| **W層** | 要素検討ワークスペース（Element Deliberation Workspace） | `docs/features/element_deliberation_workspace_design.md`（親: `knowledge_network_vision.md`）+ `element_context_lens_design.md`（#498） | `backend/core/deliberation/`（context_lens.py 含む）+ `routes/deliberation.py` + `deliberation.js` | 048, 049, 050, **064（Phase 5: evidence / derivation の要素化）** | 実装済み（Phase 0/1/W-β/2/S/5） |
| 横断ユーティリティ層 | Admin Copilot（統合AIアシスタント） | `docs/features/admin_assistant_design.md` | `backend/core/admin_assistant/` + `routes/admin_assistant.py` | 034 | 実装済み |
| 状態通知基盤 | Status Projection + 遷移イベント + 統合通知インボックス | `docs/features/status_notification_design.md`（表記 039 → 実装 038） | `backend/core/status/` + `routes/status.py` / `routes/notifications.py` | 038（045 で V層通知を統合） | 実装済み |
| 個人知識ネットワーク | Personal Knowledge Network（わたしの地図・旅・橋候補・いまここの周り・広がり装置） | `docs/features/personal_knowledge_network_design.md`（親: `knowledge_network_vision.md`）+ 近傍関係ビューは `docs/features/personal_map_nearby_design.md`（PMN-1〜PMN-7・§10 範囲モード）+ 広がり装置は `docs/features/personal_map_curiosity_design.md` | `backend/core/personal_graph/`（`nearby.py` / `atlas_fog.py` 含む） + `routes/personal_map.py` + `personal-map.js` / `personal-map-home.js` | 不要（既存テーブルの読み時導出のみ） | 実装済み（Phase P-0〜P-3 + B、近傍関係ビュー v1 = 2026-08-18、範囲モード = 2026-08-21、広がり装置 = 2026-08-22） |
| 理解サイクル | Understanding Cycle（OPEN→ELICIT→…→REVISIT） | `docs/features/understanding_cycle_design.md`（UC1〜UC10・§14/§15 実装記録）+ 帰還の扉は `docs/features/return_door_design.md`（RD1〜RD5・§5 実装記録） | `backend/core/cycle/` + `routes/cycle.py` + `app.js` | 不要（`interest_traces` の kind 追加のみ） | 実装済み（Phase 1+2。Phase 3 = SL層。帰還の扉 v1 = 2026-08-15） |
| discuss（論文と話す） | ディスカッションモード + 歩調合わせ + 開幕素材 + 観測基盤 | `discussion_mode_design.md`（DM1〜8）/ `discuss_dialogue_alignment_design.md` / `discuss_opening_authoring_design.md` / `discuss_observation_design.md` | `backend/core/discuss/` + `routes/learning.py` + `routes/discuss_observation.py` + `discuss.js` + `agents/discuss_opening/` | 060（観測）, 062（開幕素材） | 実装済み（Phase 0〜2。**Phase 3 = document 直付け入口は別層で実装済み** — [`corpus_roaming_design.md`](../features/corpus_roaming_design.md) Phase B（migration 073・センチネル `_doc:{document_id}` の正本は `core/discuss/context.py`）が予約を引き受けた。`discussion_mode_design.md` §6.4 が併記した残差（教員向け k-匿名集約 / 専用 `DISCUSS_MAX_CALLS_PER_DAY` / 音声版 / 学習者向け W層簡易パネル）のみ未実装） |
| 二層説明 | 階層文脈説明（generic / contextual）+ 説明レビューキュー | `docs/features/hierarchical_context_explanation_design.md`（E1〜E8） | `agents/contextual_explanation/` + `backend/core/element_explanations.py` / `element_context.py` + `routes/element_explanations.py` | 055, 056（+062 で document スコープ拡張） | 実装済み |
| 利用者マニュアル KB | help_kb（非ベクトル索引 + ベクトル補助 + DB draft/freeze）+ インスペクトモード | `docs/features/manual_help_kb_design.md` | `backend/core/help_kb/` + `admin-manual-editor.js` / `admin-help-inspect.js` | 058, 059 | 実装済み（Phase 1〜3。配信既定は files） |
| 知識ランドスケープ | Knowledge Landscape（論文→地図の多観点配置） | `docs/features/knowledge_landscape_design.md`（LS1〜LS10） | `backend/core/landscape/` + `routes/landscape.py` + `landscape-layer.js` + `backend/atlas_domains/`（版をまたいだ読み替えは追補 [ノード版間対応](../features/atlas_node_correspondence_design.md) の `core/atlas_correspondence.py`） | 065 | 実装済み（v1） |
| カテゴリギャップ候補 | 分野マップを論文から育てる層 | `docs/features/category_gap_candidates_design.md`（§10 実装記録） | `backend/core/atlas_gaps/` + `routes/atlas_gaps.py` | 066 | 実装済み（v1-a〜v1-d） |
| VA層 | ベクトル係留（アンカー埋め込み・別名レジストリ・配置プレフィルタ・着地予測） | `docs/features/atlas_vector_anchoring_design.md`（VA1〜VA9・§12 実装記録） | `backend/core/atlas_vectors/` + `routes/atlas_vectors.py` | 074 | 実装済み（v1） |
| RE追補 | 分野マップの関係表示（辺候補レビュー + 学習者向け「推定の糸」） | `docs/features/atlas_relation_edges_design.md`（RE1〜RE8・§11 実装記録）・親: [表示原則の討議記録](field_map_display_principles_2026-08-29.md) | `backend/core/atlas_edges/` + `routes/atlas_edges.py` + `atlas-threads-layer.js` | 076 | 実装済み（v1） |
| 論文ディスカバリー層 | arXiv 分野購読による論文発見と、教員の明示承認による取り込み（+ バッチ取り込みキュー・関連度ランキング・引用グラフ拡張口） | `docs/features/paper_discovery_design.md`（PD1〜PD8・§10 実装記録） | `backend/core/paper_discovery/` + `routes/paper_discovery.py` + `backend/api/ingest_worker.py` + `admin-paper-discovery.js` | 071, 072 | 実装済み（Phase 1〜3） |
| 論文レーダー | 教材（seed）起点の類似論文探索・距離3択・AI 比較分析・arXiv 出所の後付け登録 | `docs/features/paper_radar_design.md`（PR1〜PR8・§10〜§12 実装記録。PD1〜PD8 を全継承） | `backend/core/paper_discovery/`（radar.py / compare.py ほか）+ `routes/paper_discovery.py` + `admin-paper-radar.js` | 不要（既存表の読み + `documents.source_url` の記帳のみ） | 実装済み（v1 + 重なり・差分提示） |
| コーパス回遊層 | 論文の海（コーパス地図）・コース無しの論文議論・地図の端・関心信号 | `docs/features/corpus_roaming_design.md`（CR1〜CR10・§12 実装記録） | `backend/core/corpus_view.py` + `core/discuss/context.py` + `routes/corpus.py` + `corpus-sea.js` | 073 | 実装済み（Phase A〜D） |
| コーパスを補う論文 | 「近さ」ではなく「コーパスに何が足されるか」で候補を選ぶ第3の探し方（レンズA 地図の薄い領域 / レンズB 検証記録の無い前提 / レンズC 基盤論文）。3レンズとも決定論・LLM 0回で、embedding は既存の関連度バッチに相乗りする | `docs/features/corpus_complement_design.md`（CC1〜CC8・§11 実装記録。PD1〜PD8 を全継承） | `backend/core/paper_discovery/`（complement.py / foundation.py / reference_cache.py）+ `routes/paper_discovery.py`（complement/*）+ `admin-paper-discovery.js` | 077 | 実装済み（v1） |
| 構造の降下路 | 足場ダイヤル・楽屋（理解の粒度を降りる導線） | `docs/features/structure_descent_design.md` | `backend/core/descent/` + `routes/descent.py` | 不要 | 実装済み（v1。パーソナライズ実装計画 Phase 3） |
| グラフ対話レビュー | 教材行から開くグラフ起点のレビュー画面（承認/却下・claim承認・ノード対話・グラフ全体対話） | `docs/features/graph_dialogue_review_design.md`（GR1〜GR8・§11 実装記録） | `backend/core/deliberation/graph_dialogue.py` + `routes/deliberation.py`（graph-sessions）+ `routes/theory_components.py`（approve / claim review）+ `admin-graph-review.js` | 075 | 実装済み（v1） |
| グラフの論文層 | Paper Layer（理論操作グラフのフレームに論文の章・式・図表・claim・narrative を肉付けする読み時射影。フレーム→論文 / 論文→フレーム / 被覆） | `docs/features/graph_paper_layer_design.md`（PL1〜PL8・§10 実装記録） | `backend/core/graph_paper_layer/` + `routes/theory_components.py`（paper-layer）+ `admin-graph-review.js` | 不要（読み時導出・保存なし） | 実装済み（Phase 0） |
| 画面文脈アダプター | Assistant Screen Adapter（画面が参照だけを渡し、サーバが既存の権限ゲート付き core で解決して AI 対話の grounding に足す層。第1適用先はグラフレビューのノード対話・グラフ全体対話） | `docs/features/assistant_screen_adapter_design.md`（SA1〜SA7・§10 実装記録） | `backend/core/assistant_context/` + `routes/deliberation.py`（messages の `screen_context`）+ `admin-graph-review.js`（`getScreenContext`） | 不要（読み時解決・保存なし） | 実装済み（Phase 1） |
| LLM 応答のストリーミング | 学習チャット本文の逐次配信（SSE）と停止。`_learning_chat_core` を生成器にして前処理・生成・後処理を1本に保ち、転送方式だけを分岐する（表示の先行であって正本ではない） | `docs/features/llm_response_streaming_design.md`（ST1〜ST9・§12 実装記録） | `backend/core/llm.py::generate_text_stream` + `routes/learning.py`（`/chat/stream` / `/client-features`）+ `app.js`（逐次バブル・停止ボタン） | 不要（`llm_usage_events` は `operation='chat'` のまま・記録先は既存 `metadata`） | 実装済み（**Phase 3-a のみ**・`LEARNING_CHAT_STREAMING_ENABLED` 既定 off。3-b〜3-d は未着手） |
| 知識オブジェクト層 | Knowledge Objects（claim 親子 / component / equation / evidence / derivation step / symbol を内容由来の版非依存キー `stable_key` を持つ一級の行にし、再解析を DELETE ではなく supersede 遷移にする。artifact は 1 run × 1 stage 1 行の生成ログへ降格、`document_id` は UUID + FK、型語彙は `core/schema.py` 正本 + 語彙表 FK） | `docs/features/knowledge_objects_design.md`（KO1〜KO10・§12 実装記録。親: [知識構造の見直し提案](knowledge_structure_review_2026-09-12.md) Phase 1） | `backend/core/knowledge_objects/`（stable_key / sync / remap / backfill）+ `core/document_pipeline/persistence.py` + `routes/admin.py::delete_material` → `core/versioning/deletion.py::_purge_document` | 078, 079, 080 | 実装済み（Phase 1 v1。読み手は `theory_claims_live` / `theory_components_live` を読む） |
| 学ぶ単位層 | Learning Units（skeleton の論理ブロック / thesis の支持構造 / component の LLM 原案 / DSL ノード / 図を `stable_key` 付きの `learning_units` 行にし、コース topic を `topic.units` でその並びとして定義する。前提は同コース topic の ID 参照 + 非LLM の半順序検査、コース登録は `decision_context` 付きの一括確定、承認ゼロ配信は G層の事実文、blueprint の語りの弧を topic へ、学習者の選択要素は `learner_selected` アンカーに着地） | `docs/features/learning_units_design.md`（LU1〜LU9・§12 実装記録。親: [知識構造の見直し提案](knowledge_structure_review_2026-09-12.md) Phase 2） | `backend/core/knowledge_objects/learning_units.py` + `core/course_units.py` + `core/course_prerequisites.py` + `routes/course_prerequisites.py` + `core/structure_anchor/selection_segment.py` + `course_content_builder.py` / `routes/learning.py::create_course` | 081 | 実装済み（Phase 2 v1。unit の教員確定 UI は非スコープ） |
| 概念レジストリ層 | Concept Registry（`library_entries` を概念レジストリに拡張し、SKOS 相当の label 3 種 / 関係 4 種 / `mapping_justification` / レジストリ ↔ atlas node の版非依存リンク / 記号 → 概念の参照と学習者向け「直前の定義」/ 決定論の同一性候補 / cartridge の形の宣言と適合事実を積む。リンクであってマージではない — 行の統合・削除はしない） | `docs/features/concept_registry_design.md`（KR1〜KR10・§13 実装記録。親: [知識構造の見直し提案](knowledge_structure_review_2026-09-12.md) Phase 3）+ 追補 [主張の概念接地](../features/claim_concept_grounding_design.md)（CG1〜CG7・§10 実装記録。K-2: 主張の `concepts` へ分野の言葉を供給し、学習者の概念マップから記号を除く。migration 不要） | `backend/core/library/`（`schema` / `store` / `registry` / `atlas_links` / `identity_candidates`）+ `core/symbol_lookup.py` + `core/cartridge_shape.py` + `routes/library.py` / `routes/cartridge_shape.py` + `admin.js`（ナレッジライブラリタブの区画）/ `app.js`（記号ポップオーバー） | 082 | 実装済み（v1。確定は常に教員・LLM 0 回・embedding 呼び出しゼロ） |
| 知識の転用層 | Knowledge Transfer（束の往復 = export の RO-Crate 型 JSON-LD 化 + 各項目の `stable_key` と import（dry-run → 教員確定・承認は継承しない・取り込み先で stable_key 再計算）/ RAG への構造 1 hop（SA層 kind `retrieved_structure`・LLM 回数不変）/ DB live 行の参照の健全性（解析完了時の事実 + 教材行 + 詳細 API）/ 版の語彙（§4・PROV-O 2 語）/ D層・C層の表現語彙（疑義の向き・根拠の線・引用の意図）） | `docs/features/knowledge_transfer_design.md`（KT1〜KT8・T-1〜T-3・§14 実装記録。親: [知識構造の見直し提案](knowledge_structure_review_2026-09-12.md) Phase 4） | `backend/core/knowledge_import/` + `core/reference_health.py` + `core/assistant_context/resolvers/learning.py`（retrieved_structure）+ `routes/export.py`（import）/ `routes/reference_health.py` / `routes/doubt.py`（evidence-lines）+ `admin-knowledge-import.js` | 083（P4-5 の列追加のみ。P4-1〜P4-4 は不要） | 実装済み（v1。取り込み対象は claim / component / equation / evidence / derivation step / graph） |
| 教材図スタジオ | Teaching Figure Studio（AI対話 SVG 生成） | `docs/features/teaching_figure_studio_design.md`（FG1〜FG9・§13 実装記録） | `backend/core/teaching_figures/` + `routes/teaching_figures.py` + `admin-figure-studio.js` | 063 | 実装済み（v1） |
| リリース前の確認 | Release Review Flow（3ステップウィザード） | `docs/features/release_review_flow_design.md`（RR1〜RR7） | `routes/landscape.py`（course-scoped）+ `admin-release-review.js` | 不要（既存 API の束ね） | 実装済み（v1） |
| 教員の弁と計器 | 負荷順トリアージ + 静かな計器（コスト見通し・WMレンズ） | `docs/features/teacher_triage_instruments_design.md`（TT1〜TT6・§6 実装記録） | `backend/core/teacher_triage.py` + `core/llm_usage/forecast.py` + `core/lecture_wm.py` + 既存キュー2ルートの sort 拡張 | 不要（読み時導出とソートのみ） | 実装済み（Phase 4 v1） |
| ゼミ前ブリーフ・鏡面化 | 論文の賭け金の read-only 合成ビュー + discuss の鏡面化 move | `docs/features/seminar_brief_mirroring_design.md`（SB1〜SB4・EX-3b 逐条・§4 実装記録） | `backend/core/doubt/seminar_brief.py` + `routes/seminar_brief.py` + `core/discuss/mirroring.py` + discuss プロンプト拡張 | 不要（読み時合成とプロンプト move のみ） | 実装済み（Phase 5 v1） |
| 主権台帳（わたしの記録） | 痕跡kind登録簿 + 学習痕跡の主権台帳v1 | `docs/features/trace_registry_sovereignty_ledger_design.md`（TR1〜TR7・§4 実装記録） | `backend/core/trace_registry.py` + `core/trace_ledger.py` + `routes/my_records.py` + `my-records.js` | 不要（`interest_traces` の読みのみ） | 実装済み（Phase 1 v1。封印・包含来歴は v2） |
| 制度指標カタログ | Indicator Governance（集約計器の定義・用途・非利用を全当事者へ公開） | `docs/features/indicator_governance_design.md`（IG1〜IG5・§9 実装記録） | `backend/core/indicator_catalog.py` + `routes/indicators.py` + `admin-indicators.js` | 不要（カタログはコードが正本・値を持たない） | 実装済み（v1。vision §6 改訂原則4 の実装） |
| 可視性6軸の宣言 | Disclosure Axes（データ種別ごとの6軸宣言。とくに**外部 AI に渡るか**を全当事者へ公開 + 各対話 UI の常設事実文） | `docs/features/disclosure_axes_design.md`（DA1〜DA6・§7 実装記録） | `backend/core/disclosure_axes.py` + `routes/disclosure.py` + `disclosure-note.js` | 不要（カタログはコードが正本・値を持たない） | 実装済み（v1。vision §5.4 の実装。初回告知カードは D4 未決で非スコープ） |
| 確定文脈の記帳 | `decision_context`（一括確定を再構成・異議申立できる手続にする監査ブロック） | `docs/features/decision_context_design.md`（DC1〜DC4・§8 実装記録） | `backend/core/decision_context.py` + `routes/landscape.py`（リリース前の確認）/ `routes/element_explanations.py`（説明の一括レビュー） | 不要（既存 `theory_review_events.metadata` に1ブロック） | 実装済み（一括確定2経路。単発承認・凍結・公開は段階適用中） |
| 横断基盤（共有ユーティリティ） | 同型実装のコピペ増殖を止める正本モジュール群 | `docs/features/assistant_common_infra_design.md` + `docs/features/candidate_flow_design.md` + `docs/features/label_vocab_design.md` + `consolidation_survey_2026-07.md` | `backend/core/llm_worker/` / `privacy.py`（k=3 正本）/ `course_data.py` / `revision_store.py` / `candidate_flow.py`（候補→確定の共通制御フロー）/ `label_vocab.py`（段階ラベル・共有語彙表の正本）/ `trace_registry.py`（`interest_traces` kind 登録簿と消費者許可リストの正本）/ `learner_context_common.py`（学習者向け要素文脈の共通正本）/ `notification_recipients.py` / `schema.py` の `AUDIT_ENTITY_*` / `backend/tests/guardrail_helpers.py` | — | 実装済み（**新機能はコピペせずこれらに接続するのが規約**） |

## 2. 補足

- **migration 番号と層の対応の一次情報**は `backend/db/0NN_*.sql` の実ファイル名（設計書の
  文中表記だけを見ない）。
- **正本設計書が複数ファイルに分割されている層**（Field Atlas / B層 / discuss / L層図系）は、
  実装を追う際に1ファイルだけ読んで判断しないこと。
- **migration を伴わない実装済みレイヤーも本表に載せる**（理解サイクル / リリース前確認 /
  個人知識ネットワーク等。「migration が無い＝機能が無い」ではない）。
- 監査語彙（`AUDIT_ENTITY_TYPES`）の正本は `backend/core/schema.py`。
  ドキュメントに全列挙や語彙数を書き写さないこと（陳腐化するため）。
- UI アンカー表の正本は `backend/core/help_kb/admin_ui_anchors.py`（管理画面）/
  `ui_anchors.py`（学習画面）。**件数はドキュメントに書かない** — 正本は
  `backend/tests/test_admin_help_ui_anchors.py`（管理側の網羅・双方向整合は同テストと
  `test_admin_help_inspect_ui_static.py` が構造的に守る）。

## 3. migration 帰属一覧（init〜085、2026-09-14 時点）

`backend/db/` の実ファイルを正とした全 migration の帰属。

| migration | ファイル | 帰属レイヤー / 機能 |
|---|---|---|
| init | `init.sql` | 基盤（ユーザー・教材・チャンク・コース等） |
| 002 | `002_a1_a2_a3.sql` | Priority A（コースビルダー履歴永続化・コース公開・前提知識） |
| 003〜004 | `003_unanswered_queries` / `004_schema_evolution` | 動的スキーマ進化 |
| 005 | `005_background_tasks` | 非同期タスク基盤 |
| 006 | `006_lecture_mode` | レクチャーモード |
| 007 | `007_drop_arxiv_id` | 整理（列削除） |
| 008 | `008_display_text` | レクチャー原稿（display / spoken 分離） |
| 009 | `009_groups_visibility` | グループ・開示範囲 |
| 010 | `010_course_group_permissions` | コースのグループ共有（**044 に統合済み**） |
| 011 | `011_course_states_separation` | マスターコース / 学習状態の分離（クローン方式廃止） |
| 012 | `012_graph_suggestions` | スキーマ提案（グラフ） |
| 013〜015, 017 | `theory_components` / `section_assembly_status` / `document_pipeline` / `tex_references_mentions` | **A層** |
| 016 | `016_embedding_dim_3072` | 埋め込み次元変更 |
| 018 | `018_course_builder_session_status` | コースビルダー |
| 019 | `019_revision_runs` | リビジョンラン |
| 020, 022, 025 | `interest_trace` / `tension` / `structure_anchor` | **B層** |
| 021 | `021_endorsement_sharing` | **C層** |
| 023, 024, 026, 027, 028, 046 | `atlas_*` | **Field Atlas（S）** |
| 029〜033 | `epistemic_ledger` / `assumption_nodes` / `challenges` / `verification_proposals` / `counterfactual_sessions` | **D層** |
| 034 | `034_assistant_actions` | 横断ユーティリティ層（Admin Copilot） |
| 035 | `035_document_group_permissions` | パイプライン成果のグループ共有（**044 に統合済み・スタブ**） |
| 036 | `036_reconstruction_loop` | **R層** |
| 037 | `037_shared_versioning` | **V層** |
| 038 | `038_status_events_notifications` | 状態通知基盤 |
| 039 | `039_assistant_step_dismissals` | **G層** |
| 040 | `040_lecture_slides` | レクチャースライド同期 + 音声言語切替 |
| 041, 042 | `image_pipeline` / `knowledge_library` | **L層** |
| 043 | `043_llm_usage_events` | **U層** |
| 044 | `044_object_group_permissions` | アーキテクチャ整理 Tier 3-14（010 + 035 の統合） |
| 045 | `045_unified_notifications` | アーキテクチャ整理 Tier 3-15（038 + V層通知の統合） |
| 047 | `047_topic_lecture_audio` | レクチャー（トピック音声キャッシュ） |
| 048, 049 | `element_identity_links` / `deliberation_sessions_annotations` | **W層**（W-β / Phase 2） |
| 050 | `050_library_standardization_status` | **W層 Phase S**（L層 `library_entries` へのガバナンス列） |
| 051 | `051_figure_inner_labels` | **L層**（装置図理解拡張・図中ラベル） |
| 052, 053 | `figure_presentation_modes` / `figure_reviewed_analysis` | **L層 #496**（図分類 + 教員レビュー） |
| 054 | `054_figure_iterative_analysis` | **L層 #499**（反証型反復照合。`document_figures.iterative_analysis`） |
| 055 | `055_thesis_context_persistence` | **二層説明 Phase 1**（thesis 構造メタの DB 永続化） |
| 056 | `056_element_explanations` | **二層説明 Phase 2**（generic/contextual 説明台帳） |
| 057 | `057_atlas_domain_lifecycle` | **Field Atlas（S）**（ドメイン lifecycle: active/retired） |
| 058 | `058_manual_sections` | **help_kb Phase 3①**（ベクトル補助層・chunks 非汚染） |
| 059 | `059_manual_kb_store` | **help_kb Phase 3②**（DB draft/freeze ストア） |
| 060 | `060_discuss_metric_events` | **discuss 観測基盤**（DO1〜DO6・append-only） |
| 061 | `061_llm_model_policies` | **M層**（場面別モデル選択。scope=system\|user） |
| 062 | `062_discuss_opening_explanations` | **discuss 開幕素材オーサリング**（056 台帳へ相乗り。設計書の「061 想定」は誤り） |
| 063 | `063_teaching_figures` | **教材図スタジオ**（生成図 + ギャップ候補の2表） |
| 064 | `064_deliberation_evidence_derivation` | **W層 Phase 5**（element_type に evidence / derivation 追加） |
| 065 | `065_landscape_placements` | **知識ランドスケープ**（配置層） |
| 066 | `066_category_gap_signals` | **カテゴリギャップ候補**（信号 + 教員判断の2層分離） |
| 067 | `067_stakes_ledger` | **SL層**（反証条件・到達可能性・観測反実仮想・晴れ間昇格ゲート） |
| 068 | `068_account_lifecycle` | **アカウントライフサイクル管理**（users 状態列 + auth_events 台帳） |
| 069 | `069_llm_usage_user_index` | **U層拡張**（llm_usage_events のユーザー別集計インデックス） |
| 070 | `070_url_fetch_domains` | URL指定による教材取得（取得先ドメイン許可リスト） |
| 071 | `071_paper_discovery` | **論文ディスカバリー層**（arXiv 分野購読 + 見送り記録 + `documents.source_url`） |
| 072 | `072_paper_discovery_ingest_queue` | 論文ディスカバリー層 Phase 2（取り込みキュー `paper_discovery_ingest_items`。失敗は行を消さず `failed` 保持） |
| 073 | `073_corpus_roaming_search_state` | **コーパス回遊層** Phase C（地図の端 — 外の輪。`paper_discovery_subscriptions.last_search_found_new` の集約1ビットのみ） |
| 074 | `074_atlas_vector_anchoring` | **VA層（ベクトル係留）**（アンカー埋め込み + 別名レジストリの2表） |
| 075 | `075_graph_dialogue_sessions` | **グラフ対話レビュー**（`deliberation_sessions.element_type` に `'document_graph'` を追加。新テーブルなし） |
| 076 | `076_atlas_edge_decisions` | **分野マップの関係表示（RE追補）**（辺候補の教員判断 `atlas_edge_decisions` 1表。候補は読み時導出） |
| 077 | `077_paper_discovery_reference_cache` | **コーパスを補う論文**（Semantic Scholar 参照リストの外部事実キャッシュ1表。候補・判断は保存しない） |
| 078 | `078_knowledge_objects` | **知識オブジェクト層 M1**（既存2表への stable_key / supersede 列、新4表 + `element_id_remap`、型語彙表2表と CHECK→FK 置換、live ビュー2つ） |
| 079 | `079_analysis_artifacts` | **知識オブジェクト層 M2**（`document_analysis_artifacts` = 1 run × 1 stage 1 行。`stage_outputs._artifacts` blob の1回限りの移送） |
| 080 | `080_document_id_uuid` | **知識オブジェクト層 M3**（`document_id` の TEXT → UUID 統一 + `documents(id)` への FK CASCADE。適用時に到達不能な孤児行を1回掃除） |
| 081 | `081_learning_units` | **学ぶ単位層**（語彙表 `knowledge_unit_kinds` + 新表 `learning_units` + `learning_units_live`、`theory_components` の親参照列 `parent_component_id` / `parent_agent_component_id`。末尾で live ビュー2文を再作成） |
| 082 | `082_concept_registry` | **概念レジストリ層**（語彙表4表 `knowledge_entry_types` / `knowledge_label_kinds` / `knowledge_relation_kinds` / `knowledge_mapping_justifications` + `library_entries` のレビュー列群、新表 `library_entry_labels` / `library_entry_relations` / `library_atlas_node_links`、既存5表への `mapping_justification` の additive 追加、`element_identity_links` の instance 型に `symbol`、`knowledge_symbols_live` ビュー） |
| 083 | `083_doubt_citation_vocab` | **知識の転用層（P4-5）**（`challenges.challenge_mode` / `target_element_ref`・`epistemic_ledger.evidence_lines`・`component_citations.citation_intent` の列追加のみ。新テーブルなし） |
| 084 | `084_claim_chunk_fk_set_null` | **知識オブジェクト層の是正**（`theory_claims.chunk_id` の FK を `ON DELETE SET NULL` に張り替え = 再解析の chunks 掃除で claim 行が消えていた穴を塞ぐ。`chunks(document_id, chunk_index)` の一意索引つき） |
| 085 | `085_paper_discovery_arxiv_metadata_cache` | **論文ディスカバリー層 / 論文レーダー**（arXiv メタデータの外部事実キャッシュ1表。候補・教員の判断は保存しない。正本は `docs/features/paper_radar_design.md` §14） |

次の空き番号は **086**（E層など新規レイヤーはここから採番する）。
番号の手書き案内は陳腐化しやすいため、採番前に必ず `ls backend/db/` で確認すること
（機械固定の提案は [機能整備提案](feature_consolidation_proposals_2026-08-13.md) §3）。

## 4. 版の語彙（PROV-O の 2 系統 — revision と alternate を混ぜない）

「版」を担う構造が V層 / atlas 骨格 / L層凍結版 / 知識行の supersede / 同一性リンクに散っている
（[知識構造の見直し提案](knowledge_structure_review_2026-09-12.md) X-12）。それぞれが**どの意味の「版」か**を
PROV-O の 2 語で宣言する。**コード変更はゼロ**（宣言だけ）。export bundle の `ro-crate-metadata.json`
（[知識の転用層](../features/knowledge_transfer_design.md) §4.1 / §7）は同じ語を使う。
表の網羅は `backend/tests/test_version_semantics_docs.py` が固定する（版を持つ構造を足したらここに 1 行足す）。

- **revision（`prov:wasRevisionOf`）** = 同じものの**新しい状態**。旧版は残り、新版がそれを置き換える。
- **alternate（`prov:alternateOf`）** = 同じものの**別表現・別 ID**。どちらも生きていて、統合しない（KN-2 / 原則7）。

| 構造（表・列） | 語彙 | 意味 | 正本 |
|---|---|---|---|
| `shared_versions.version_no`（V層の発行版） | revision | 発行版の連鎖。`shared_version_subscriptions.pinned_release_id` と `component_citations.source_release_id` はその 1 点への pin（`prov:hadPrimarySource`）であって版ではない | `shared_versioning_design.md` |
| `atlas_skeletons`（`domain_key`, `version`）の凍結版 + `id_migrations` | revision | 分野の地図（骨格）の凍結版。`id_migrations` は revision 間の node 対応（K-6。`atlas_node_correspondence_design.md`）。`atlas_overlay_cache` / `atlas_anchor_embeddings.skeleton_version` はその版への刻印 | `field_atlas_overlay_spec.md` |
| `library_entry_versions`（L層の凍結版） | revision | ナレッジライブラリ エントリの凍結版。パイプラインが読むのは凍結版だけ | `image_pipeline_knowledge_library_design.md` §6 |
| 知識行の `produced_by_run_id` / `superseded_at` / `superseded_by_run_id`（`theory_claims` / `theory_components` / `knowledge_*` / `learning_units`） | revision | 解析 run による改訂。**同一性は `stable_key`、版は run**。live ビューは「現在の版」の射影 | `knowledge_objects_design.md`（KO3） |
| `element_explanations` / `landscape_placements` / `landscape_gap_signals` / `element_annotations` の `superseded` 遷移 | revision | 候補の改訂（再解析で inferred / candidate だけが倒れ、確定は残る） | 各層の設計書（LS3 / OA 系） |
| `document_analysis_runs`（同一 document の run 列・`documents.active_analysis_run_id`） | revision | 解析そのものの改訂。採用 run が「現在の版」。artifact は 1 run × 1 stage の生成ログ | `knowledge_objects_design.md`（KO6） |
| `element_id_remap` | revision 間の対応表 | 旧 ID → 新 ID（同一 stable_key の別 run）。「解決済み」フラグではなく事実の記録 | `knowledge_objects_design.md`（KO8） |
| `element_identity_links`（instance ↔ shared_part / symbol） | **alternate** | 同じ概念の別表現（論文側の局所表現と共通部品）。統合・書き換えなし | `knowledge_network_vision.md`（KN-2） |
| `library_entry_relations`（`exact_match` / `close_match`） | **alternate** | 2 つのレジストリ行を並存させたまま「同じ / 近いが別」を記録する SKOS の関係語彙（`broader` / `related` は版でも同一性でもない構造語） | `concept_registry_design.md`（KR3） |
| `library_atlas_node_links` | **alternate** | レジストリ行と骨格 node の同一概念関係。版非依存キー（`skeleton_version` を持たない） | `concept_registry_design.md`（KR9） |
| `atlas_anchor_aliases` | **alternate** | 骨格 node の教員確定別名（版非依存） | `atlas_vector_anchoring_design.md`（VA6） |
| `help_kb` の draft / freeze（`manual_sections` の凍結版） | revision | マニュアル KB の配信版 | `manual_help_kb_design.md` |

注記: `revision` の構造は「旧版を残す」ことが規約（行削除なし）。`alternate` の構造は「どちらも正」であることが規約
（片方を消して寄せない）。新しい層が「版」または「同一性」を持つときは、この表に 1 行足してからコードを書く。
