# レイヤー索引表

[← ドキュメント目次](../README.md) ｜ [← アーキテクチャ概要](overview.md) ｜ 関連: [データモデル](data-model.md) / [ビジョンと思想](../vision.md)

CLAUDE.md・`docs/features/*_design.md`・実装コードを横断して積層してきた各レイヤー（層）の
名称・正本・実装場所・migration 番号を1枚にまとめた索引。
（初版は `docs/architecture/consolidation_survey_2026-07.md` Tier 0 の「レイヤー命名の混乱」
再発防止として作成。**2026-08-13 のドキュメント総点検で migration 067 まで全面更新**。）

## 0. 先に知っておくこと（命名の混乱への注記）

- **「第五の層」を3つの設計書が独立に自称している**: `reconstruction_loop_design.md`（R層）・
  `shared_versioning_design.md`（V層）・`exposition_layer_design.md`（E層）が、それぞれ他の2つを
  知らずに「A層・B層・C層・D層に続く第五の層」と書いている。実際の追加順は
  A→B→C→D→（Field Atlas/S）→R(036)→V(037)→状態通知基盤(038)→G(039)→L(041/042)→U(043)
  →W(048〜050)→L追補(051〜054)→二層説明(055/056)→S追補(057)→help_kb(058/059)
  →discuss観測(060)→M(061)→discuss開幕(062)→教材図(063)→W Phase5(064)
  →ランドスケープ(065)→カテゴリギャップ(066)→SL(067) であり、序数はどれにも一意に対応しない。
  序数を主張する文言は今後の設計書では避け、migration 番号ベースの参照に置き換えること。
- **E層の migration 番号は衝突している**: `exposition_layer_design.md` §5 は「migration 034」を
  提案しているが、034 は Admin Copilot が使用済み。E層は未実装のため実害はまだ無いが、
  着手時は次の空き番号（**068 以降**。044〜067 は使用済み — §3 参照）へ採番し直すこと。
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
  なお **054〜067 に統合系 migration は無い**（すべて機能追加）。
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
| **E層** | 段階的翻訳レイヤー（Exposition Layer） | `docs/features/exposition_layer_design.md` | なし | 設計書は 034 を提案（衝突。**着手時は 068 以降へ採番し直し** + 横断基盤接続の追補が必要） | **未実装**（唯一の設計のみ層） |
| **G層** | ガイダンス層（次にやることバッジ + 状態導出型To-Do） | `docs/features/guidance_layer_design.md`（表記 038 → 実装 039） | `backend/core/admin_assistant/next_steps.py` + `admin-next-steps.js` | 039 | 実装済み |
| **L層** | 画像読み取りパイプライン + 分野別ナレッジライブラリ | `docs/features/image_pipeline_knowledge_library_design.md`（§14〜16 追補含む）+ `contextual_figure_analysis_iterative_verification.md`（#499）+ `guided_figure_reanalysis_design.md` | `backend/core/document_pipeline/figure_images.py`、`src/episteme_graph/agents/apparatus_semantics/`、`backend/core/library/` + `routes/library.py`、`backend/core/figure_presentation.py` + `routes/figure_presentation.py` | 041, 042, 051, 052/053, **054（反証型反復照合 #499）** | 実装済み |
| **M層** | 場面別 LLM モデル選択（LLM Model Selection） | `docs/features/llm_model_selection_design.md`（M1〜M10） | `backend/core/llm_policy.py` / `llm_policy_store.py` + `routes/llm_models.py` + `admin-llm-models.js` | 061 | 実装済み（Phase 0〜4。ユーザー別保存が正・tier名/金額は UI 非表示） |
| **R層** | 再構成ループ（Reconstruction Loop） | `docs/features/reconstruction_loop_design.md` | `backend/core/reconstruction/` + `routes/reconstruction.py` + `reconstruction.js` | 036 | 実装済み |
| **S層**（便宜ラベル） | 分野の地図（Field Atlas） | `field_atlas_*.md`（6ファイル）+ `atlas_binding_lifecycle_design.md`（計7ファイル。※「正本」とされる `field_atlas_overlay_spec.md` は未コミットで欠落 — [不具合報告](doc_review_findings_2026-08-13.md) §1-1） | `backend/core/atlas*.py` + `routes/atlas.py` / `atlas_view.py` + `atlas-*.js` | 023, 024, 026, 027, 028, 046, **057（ドメイン lifecycle）** | 実装済み |
| **SL層** | 賭け金の台帳（Stakes Ledger）＝理解サイクル Phase 3 | `docs/features/stakes_ledger_design.md`（SL1〜SL10・§15 実装記録） | `backend/core/doubt/` 配下の SL 系モジュール + `routes/doubt.py` 拡張（D層の双対拡張・既存意味論は非改変） | 067 | 実装済み（SL-1〜SL-5） |
| **U層** | LLM トークン使用量推計（Usage Metering） | `docs/features/llm_usage_metering_design.md` | `backend/core/llm_usage/` + `routes/llm_usage.py` | 043 | 実装済み |
| **V層** | 共有物のバージョン管理 + 更新通知 + 削除猶予 | `docs/features/shared_versioning_design.md` | `backend/core/versioning/` + `routes/versioning.py` + `versioning.js` | 037（通知は 045 で `user_notifications` に統合） | 実装済み |
| **W層** | 要素検討ワークスペース（Element Deliberation Workspace） | `docs/features/element_deliberation_workspace_design.md`（親: `knowledge_network_vision.md`）+ `element_context_lens_design.md`（#498） | `backend/core/deliberation/`（context_lens.py 含む）+ `routes/deliberation.py` + `deliberation.js` | 048, 049, 050, **064（Phase 5: evidence / derivation の要素化）** | 実装済み（Phase 0/1/W-β/2/S/5） |
| 横断ユーティリティ層 | Admin Copilot（統合AIアシスタント） | `docs/features/admin_assistant_design.md` | `backend/core/admin_assistant/` + `routes/admin_assistant.py` | 034 | 実装済み |
| 状態通知基盤 | Status Projection + 遷移イベント + 統合通知インボックス | `docs/features/status_notification_design.md`（表記 039 → 実装 038） | `backend/core/status/` + `routes/status.py` / `routes/notifications.py` | 038（045 で V層通知を統合） | 実装済み |
| 個人知識ネットワーク | Personal Knowledge Network（わたしの地図・旅・橋候補） | `docs/features/personal_knowledge_network_design.md`（親: `knowledge_network_vision.md`） | `backend/core/personal_graph/` + `routes/personal_map.py` + `personal-map.js` / `personal-map-home.js` | 不要（既存テーブルの読み時導出のみ） | 実装済み（Phase P-0〜P-3 + B） |
| 理解サイクル | Understanding Cycle（OPEN→ELICIT→…→REVISIT） | `docs/features/understanding_cycle_design.md`（UC1〜UC10・§14/§15 実装記録） | `backend/core/cycle/` + `routes/cycle.py` + `app.js` | 不要（`interest_traces` の kind 追加のみ） | 実装済み（Phase 1+2。Phase 3 = SL層） |
| discuss（論文と話す） | ディスカッションモード + 歩調合わせ + 開幕素材 + 観測基盤 | `discussion_mode_design.md`（DM1〜8）/ `discuss_dialogue_alignment_design.md` / `discuss_opening_authoring_design.md` / `discuss_observation_design.md` | `backend/core/discuss/` + `routes/learning.py` + `routes/discuss_observation.py` + `discuss.js` + `agents/discuss_opening/` | 060（観測）, 062（開幕素材） | 実装済み（Phase 0〜2。Phase 3 は実測ゲート待ち） |
| 二層説明 | 階層文脈説明（generic / contextual）+ 説明レビューキュー | `docs/features/hierarchical_context_explanation_design.md`（E1〜E8） | `agents/contextual_explanation/` + `backend/core/element_explanations.py` / `element_context.py` + `routes/element_explanations.py` | 055, 056（+062 で document スコープ拡張） | 実装済み |
| 利用者マニュアル KB | help_kb（非ベクトル索引 + ベクトル補助 + DB draft/freeze）+ インスペクトモード | `docs/features/manual_help_kb_design.md` | `backend/core/help_kb/` + `admin-manual-editor.js` / `admin-help-inspect.js` | 058, 059 | 実装済み（Phase 1〜3。配信既定は files） |
| 知識ランドスケープ | Knowledge Landscape（論文→地図の多観点配置） | `docs/features/knowledge_landscape_design.md`（LS1〜LS10） | `backend/core/landscape/` + `routes/landscape.py` + `landscape-layer.js` + `backend/atlas_domains/` | 065 | 実装済み（v1） |
| カテゴリギャップ候補 | 分野マップを論文から育てる層 | `docs/features/category_gap_candidates_design.md`（§10 実装記録） | `backend/core/atlas_gaps/` + `routes/atlas_gaps.py` | 066 | 実装済み（v1-a〜v1-d） |
| 教材図スタジオ | Teaching Figure Studio（AI対話 SVG 生成） | `docs/features/teaching_figure_studio_design.md`（FG1〜FG9・§13 実装記録） | `backend/core/teaching_figures/` + `routes/teaching_figures.py` + `admin-figure-studio.js` | 063 | 実装済み（v1） |
| リリース前の確認 | Release Review Flow（3ステップウィザード） | `docs/features/release_review_flow_design.md`（RR1〜RR7） | `routes/landscape.py`（course-scoped）+ `admin-release-review.js` | 不要（既存 API の束ね） | 実装済み（v1） |
| 横断基盤（共有ユーティリティ） | 同型実装のコピペ増殖を止める正本モジュール群 | `docs/features/assistant_common_infra_design.md` + `docs/features/candidate_flow_design.md` + `docs/features/label_vocab_design.md` + `consolidation_survey_2026-07.md` | `backend/core/llm_worker/` / `privacy.py`（k=3 正本）/ `course_data.py` / `revision_store.py` / `candidate_flow.py`（候補→確定の共通制御フロー）/ `label_vocab.py`（段階ラベル・共有語彙表の正本）/ `learner_context_common.py`（学習者向け要素文脈の共通正本）/ `notification_recipients.py` / `schema.py` の `AUDIT_ENTITY_*` / `backend/tests/guardrail_helpers.py` | — | 実装済み（**新機能はコピペせずこれらに接続するのが規約**） |

## 2. 補足

- **migration 番号と層の対応の一次情報**は `backend/db/0NN_*.sql` の実ファイル名（設計書の
  文中表記だけを見ない）。
- **正本設計書が複数ファイルに分割されている層**（Field Atlas / B層 / discuss / L層図系）は、
  実装を追う際に1ファイルだけ読んで判断しないこと。
- **migration を伴わない実装済みレイヤーも本表に載せる**（理解サイクル / リリース前確認 /
  個人知識ネットワーク等。「migration が無い＝機能が無い」ではない）。
- 監査語彙（`AUDIT_ENTITY_TYPES`）の正本は `backend/core/schema.py`（2026-08-13 時点 35語彙）。
  ドキュメントに全列挙を書き写さないこと（陳腐化するため）。

## 3. migration 帰属一覧（init〜067、2026-08-13 時点）

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

次の空き番号は **068**（E層など新規レイヤーはここから採番する）。
番号の手書き案内は陳腐化しやすいため、採番前に必ず `ls backend/db/` で確認すること
（機械固定の提案は [機能整備提案](feature_consolidation_proposals_2026-08-13.md) §3）。
