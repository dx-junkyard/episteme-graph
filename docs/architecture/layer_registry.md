# レイヤー索引表

[← ドキュメント目次](../README.md) ｜ [← アーキテクチャ概要](overview.md) ｜ 関連: [データモデル](data-model.md)

CLAUDE.md・`docs/features/*_design.md`・実装コードを横断して積層してきた各レイヤー（層）の
名称・正本・実装場所・migration 番号を1枚にまとめた索引。`docs/architecture/consolidation_survey_2026-07.md`
の Tier 0（バグ・整合性）で指摘された「レイヤー命名の混乱」の再発防止のために作成した。

## 0. 先に知っておくこと（命名の混乱への注記）

- **「第五の層」を3つの設計書が独立に自称している**: `reconstruction_loop_design.md`（R層）・
  `shared_versioning_design.md`（V層）・`exposition_layer_design.md`（E層）が、それぞれ他の2つを
  知らずに「A層（構造化）・B層（学習）・C層（承認）・D層（疑義）に続く第五の層」と書いている。
  実際の追加順は A→B→C→D→（Field Atlas/S）→R(036)→V(037)→状態通知基盤(038)→G(039)→L(041/042)→U(043)→W(048〜050)
  であり、序数「第五」は実装時点のどれにも一意に対応しない。序数を主張する文言は今後の設計書では
  避け、本表のような migration 番号ベースの参照に置き換えることを推奨する。
- **E層の migration 番号も実は衝突している**: `exposition_layer_design.md` §5 は
  「migration 034」を提案しているが、034 は既に Admin Copilot の `assistant_actions`
  （`backend/db/034_assistant_actions.sql`）に割り当て済み。E層は未実装のため実害はまだ無いが、
  実装に着手する際は次の空き番号（**054 以降**。2026-07-17 時点で 044〜053 は使用済み — §3 参照）
  へ採番し直す必要がある。着手前提の更新4点（migration / worker 方式 / UI 差し込み先 /
  学習者の視覚語彙前提）は `exposition_layer_design.md` 冒頭の 2026-07-17 注記を参照。
- **設計時想定と実装後の migration 番号がずれている組が複数ある**: 状態管理・通知基盤の設計書は
  「039 想定」と書いて実装は **038**、ガイダンス層（G層）の設計書は「038」と書いて実装は
  **039**（039 を先に G層が使う予定だったが、実装順の都合で入れ替わった）。両設計書は本タスクで
  注記を追記済み（`docs/features/status_notification_design.md` / `docs/features/guidance_layer_design.md`）。
  また W層（要素検討ワークスペース）は起草時に Phase W-β / Phase 2 とも「migration 046（同乗）」を
  想定したが、046/047 が先に他機能（atlas_report_incorporation / topic_lecture_audio）へ割り当て
  られたため、実装は **W-β=048 / Phase 2=049**（設計書冒頭に補正注記あり。一次情報は
  `backend/db/048_*.sql` / `049_*.sql` の冒頭コメント）。
  **migration 番号の一次情報は常に `backend/db/0NN_*.sql` の実ファイル名**であり、設計書の文中表記
  ではない。
- **Field Atlas（分野の地図）内部の「S/C/P」3層モデルは、本表の A〜U のアルファベット層とは別の粒度**。
  Field Atlas の設計書は自分自身の中を S（骨格 Skeleton）/ C（状態導出キャッシュ）/ P（個人層
  `interest_traces`）の3層に分けて呼んでおり、この「S」は本表で便宜上あてた「S層」ラベルとは
  由来が異なる（CLAUDE.md 自体も Field Atlas を「S層」と呼ばず「分野の地図（Field Atlas, Stage 2,
  issue A〜F 実装済み）」と表記している）。混同注意。
- **`doubt-atlas.js`（D2-3 前提の地図 / Assumption Atlas）は Field Atlas と別機能**。ファイル名が
  紛らわしいが D層のドキュメントで明記の通り、コード・API・UI 文言とも `doubt-` / `assumption-`
  プレフィックスで衝突回避されている。
- **アーキテクチャ整理 Tier 3（migration 044/045）はレイヤーをまたいで既存テーブルを統合した**:
  `object_group_permissions`（044）は C層とは無関係の「共有の仕組み」全般（レガシー単一グループ共有＝
  migration 009 とは別）に属する `course_group_permissions`（010）と、本表「L層」以前の
  「パイプライン成果のグループ共有」（`document_group_permissions`, 035）を統合したもの。
  `user_notifications`（045 で拡張）は「状態通知基盤」（038）と V層（037 `share_notifications`）の
  通知インボックスを統合したもの。**統合してもレイヤー自体の migration 番号（037/038 等）は
  変更されていない** — テーブル統合は独立した後続 migration（044/045）として追加されており、
  各層の「主 migration 番号」は本表のまま参照してよい。詳細は
  `docs/architecture/data-model.md` §1 と `consolidation_survey_2026-07.md` 第4部（Tier 3-14/15）
  を参照。
- **docs/README.md の3層モデルとの対応**（別粒度・ユーザー向けジャーニーの3段階であり、下表の
  アルファベット層とは目的が異なる）: ① 知識の構造化 ＝ A層そのもの / ② 適応的学習 ＝
  B・C・D・G・S(Field Atlas)・U層 + 横断(Admin Copilot) + 状態通知基盤（学習者支援・教員運用支援の
  判定・通知基盤群） / ③ 没入型講義 ＝ 講義内容作成〜配信（原稿スタジオ・レクチャースライド同期
  migration 040 等。独自のアルファベット層名を持たない）。L層（画像+ライブラリ）は主に①の入力強化
  （PDF 内図版の取り込み・装置候補化）に位置し、③には直接属さない。

## 1. レイヤー一覧

| 層 | 正式名 | 正本設計書 | 主実装ディレクトリ | migration | 実装状態 |
|---|---|---|---|---|---|
| **A層** | 構造化パイプライン（PDF解析Agentパイプライン） | 専用の1枚設計書なし。`docs/pipeline/*.md`（overview / agents / cartridges / theory-graph）が解説 | `src/episteme_graph/agents/`（実働17 agent）+ `backend/core/document_pipeline/orchestrator.py`（26ステージ） | 013, 014, 015, 016, 017（理論コンポーネント・パイプライン基盤） | 実装済み（旧 `core/extractor.py` 系はデッドコードとして残存 — 別途 Tier1 課題） |
| **B層** | 学習者体験レイヤー（関心痕跡・違和感マイニング・構造帰属・カジュアル対話 等） | 機能ごとに分散: `docs/features/learning.md` / `docs/features/structure-anchored-questions.md`（B層本体の統一設計書は無い） | `backend/core/tension/`、`backend/core/structure_anchor/`、`backend/api/routes/learning.py`（RAGチャット・casual・音声） | 020（interest_traces）, 022（tension）, 025（structure_anchor） | 実装済み |
| **C層** | 承認・共有レイヤー | `docs/features/endorsement-sharing.md` | `backend/api/routes/theory_components.py`（explanations/endorsements/citations） | 021 | 実装済み |
| **D層** | 疑義・認識的地位台帳（Doubt Layer） | `docs/features/doubt_layer_issues.md` | `backend/core/doubt/` + `backend/api/routes/doubt.py` | 029〜033 | 実装済み |
| **E層** | 段階的翻訳レイヤー（Exposition Layer） | `docs/features/exposition_layer_design.md` | なし | 034 を設計書は提案（**既に Admin Copilot が使用済みで衝突。着手時は 054 以降へ採番し直し**） | **未実装**（設計のみ・実装コードゼロ。着手前提の更新4点は設計書冒頭の 2026-07-17 注記） |
| **G層** | ガイダンス層（次にやることバッジ + 状態導出型To-Do） | `docs/features/guidance_layer_design.md`（設計書表記は「038」だが実装は039） | `backend/core/admin_assistant/next_steps.py` + `frontend/public/js/admin-next-steps.js` | 039 | 実装済み |
| **L層** | 画像読み取りパイプライン + 分野別ナレッジライブラリ | `docs/features/image_pipeline_knowledge_library_design.md`（§14 装置図理解拡張・§15 図分類 #496 の追補含む） | `backend/core/document_pipeline/figure_images.py`、`src/episteme_graph/agents/apparatus_semantics/`、`backend/core/library/` + `routes/library.py`、`backend/core/figure_presentation.py` + `routes/figure_presentation.py`（#496） | 041（画像）, 042（ライブラリ）, 051（図中ラベル）, 052/053（図分類 + 教員レビュー #496） | 実装済み |
| **R層** | 再構成ループ（Reconstruction Loop） | `docs/features/reconstruction_loop_design.md` | `backend/core/reconstruction/` + `routes/reconstruction.py` + `reconstruction.js` | 036 | 実装済み |
| **S層**（便宜上のラベル。CLAUDE.md 自体はこの呼称を使わない） | 分野の地図（Field Atlas） | `docs/features/field_atlas_*.md`（skeleton / binding / correction_reports / db_managed_skeleton / detail_panel / skeleton_editor_upgrade の計6ファイルに分割） | `backend/core/atlas*.py`（7ファイル）+ `routes/atlas.py` / `routes/atlas_view.py` + `frontend/public/js/atlas-*.js`（9ファイル） | 023, 024, 026, 027, 028, 046（骨格・キャッシュ・導線計測・DB管理化・ドメインメタ・修正報告の取込） | 実装済み（Stage 2 まで。調査全体で最も設計品質が高い領域） |
| **U層** | LLM トークン使用量推計（Usage Metering） | `docs/features/llm_usage_metering_design.md` | `backend/core/llm_usage/` + `routes/llm_usage.py` | 043 | 実装済み |
| **V層** | 共有物のバージョン管理 + 更新通知 + 削除猶予 | `docs/features/shared_versioning_design.md` | `backend/core/versioning/` + `routes/versioning.py` + `versioning.js` | 037（通知インボックス `share_notifications` は 045 で `user_notifications` に統合済み） | 実装済み（CLAUDE.md には本タスクで追記済み。従来欠落していた） |
| **W層** | 要素検討ワークスペース（Element Deliberation Workspace） | `docs/features/element_deliberation_workspace_design.md`（親文書 `docs/features/knowledge_network_vision.md`） | `backend/core/deliberation/` + `routes/deliberation.py` + `deliberation.js` | 048（Phase W-β の `element_identity_links`）, 049（Phase 2 の `deliberation_sessions`/`element_annotations`）, 050（Phase S の `library_entries.standardization_status`） | 実装済み（Phase 0/1/W-β/2/S。CLAUDE.md に専用セクション「要素検討ワークスペース（W層）」を追記済み 2026-07-17） |
| **横断ユーティリティ層** | Admin Copilot（統合AIアシスタント） | `docs/features/admin_assistant_design.md` | `backend/core/admin_assistant/`（`capabilities.py` / `knowledge.py` / `intent.py` / `actions/`）+ `routes/admin_assistant.py`（G層と同居） | 034 | 実装済み |
| **状態通知基盤**（レター無し） | Status Projection + 遷移イベント + 統合通知インボックス | `docs/features/status_notification_design.md`（設計書表記は「039」だが実装は038） | `backend/core/status/`（`projector.py` / `watcher.py` / `notification_rules.py`）+ `routes/status.py` | 038（`user_notifications` は 045 で V層 `share_notifications` を統合し `source`/`release_id`/`acted_at` 列を追加） | 実装済み（G層の土台。CLAUDE.md に専用セクションはまだ無い — 別途整備候補） |
| **個人知識ネットワーク**（レター無し。設計書は「Phase P」と呼び「P層」とは呼ばない） | Personal Knowledge Network（個人知識ネットワーク） | `docs/features/personal_knowledge_network_design.md`（親文書 `docs/features/knowledge_network_vision.md`） | `backend/core/personal_graph/` + `routes/personal_map.py` + `personal-map.js` / `personal-map-home.js` | 不要（既存テーブル `interest_traces` 等の読み取りのみで新規テーブルは無い） | 実装済み（Phase P-0〜P-3。CLAUDE.md に専用セクションはまだ無い — 別途整備候補） |

## 2. 補足

- **migration 番号と層の対応がずれている場合の一次情報**: 本表および `docs/architecture/data-model.md`
  §3 の一覧は `backend/db/0NN_*.sql` の実ファイル名を正として作成した（設計書の文中表記だけを見ない）。
- **正本設計書が複数ファイルに分割されている層**（Field Atlas / B層）は、実装を追う際に1ファイルだけ
  読んで判断しないこと。
- **「実装済み」であっても CLAUDE.md に記載が無い層があった**（V層。本タスクで追記済み）。同種の
  見落としを防ぐため、新しい層を追加した際は本表と CLAUDE.md の両方を同時に更新することを推奨する。
- 状態通知基盤（038）と個人知識ネットワーク（Phase P）は独自のアルファベット文字を持たない実装済み
  レイヤーである（前者は G層の土台として設計されたため「G層の一部」と誤認されやすいが、テーブル・
  コアモジュールは独立。後者は設計書自身が P1〜P7 不変条項との混同を避けるため「P層」と呼ばない
  ことを明示している）。

## 3. migration 帰属一覧（init〜053、2026-07-17 時点）

`backend/db/` の実ファイルを正とした全 migration の帰属。レイヤー行に載らない
migration（機能改善・統合系）もここで漏れなく追える。

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
| 013〜015, 017 | `theory_components` / `section_assembly_status` / `document_pipeline` / `tex_references_mentions` | **A層**（理論コンポーネント・パイプライン基盤） |
| 016 | `016_embedding_dim_3072` | 埋め込み次元変更 |
| 018 | `018_course_builder_session_status` | コースビルダー |
| 019 | `019_revision_runs` | リビジョンラン |
| 020, 022, 025 | `interest_trace` / `tension` / `structure_anchor` | **B層** |
| 021 | `021_endorsement_sharing` | **C層** |
| 023, 024, 026, 027, 028, 046 | `atlas_*` | **Field Atlas（S）** |
| 029〜033 | `epistemic_ledger` / `assumption_nodes` / `challenges` / `verification_proposals` / `counterfactual_sessions` | **D層** |
| 034 | `034_assistant_actions` | 横断ユーティリティ層（Admin Copilot） |
| 035 | `035_document_group_permissions` | パイプライン成果のグループ共有（**044 に統合済み・コメントのみのスタブ**） |
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
| 052, 053 | `figure_presentation_modes` / `figure_reviewed_analysis` | **L層 #496**（図分類 + 教員レビュー。「深く検討」UI 切替） |

次の空き番号は **054**（E層など新規レイヤーはここから採番する）。
