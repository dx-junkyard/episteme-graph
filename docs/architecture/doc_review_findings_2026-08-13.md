# ドキュメント総点検 — 不具合・不整合報告（2026-08-13）

**実施:** ドキュメント再整理タスク（ビジョン正本化）に伴う全 docs/ + CLAUDE.md + コード突合の総点検。
読解班7・突合班3・検証班5の多段レビューで抽出し、**全指摘を対象ファイル・コードの実読で
反証的に再検証したうえで CONFIRMED のみ**を本報告に載せた（検証で棄却された指摘は載せていない）。

**凡例（対応状況）:**
- ✅ **本整理で修正済み** — 2026-08-13 のドキュメント再整理コミット系列で修正
- 📝 **注記で緩和** — 本文の全面更新は行わず、冒頭に鮮度注記を追加して誤読を防止
- ⏳ **未対応** — 推奨対応を付記（多くは提案書 `feature_consolidation_proposals_2026-08-13.md` の恒久対策と対）

---

## 1. 正本ドキュメントの欠落（severity: high）

### 1-1. `field_atlas_overlay_spec.md` がリポジトリに存在しない ⏳

Field Atlas（分野の地図）の「**仕様の正本**」として CLAUDE.md:471 が明記し、
`field_atlas_skeleton.md`（§1.2/§9/§10/§16 を指定）・`field_atlas_detail_panel.md`（§6/§8）・
`field_atlas_correction_reports.md`（§7/§11）ほか **計8ファイル以上が§番号付きで参照**しているが、
`find` / `git log --all`（全ブランチ・全履歴）でも**一度もコミットされた形跡がない**。
リポジトリ外（一時的な検討場所）で書かれた仕様が、正式コミットされないまま恒久的に
「正本」として参照され続けている。

- **影響:** Field Atlas の設計判断（3層モデル・LIMITS・導線抑制ルール等）の根拠を追えない。
  §参照はすべて宙に浮いている。
- **推奨:** 原本を発掘してコミットするか、発掘不能なら実装済み各設計書（field_atlas_*.md 6本）
  から仕様を逆補完した統合正本を再作成し、CLAUDE.md の参照を差し替える。
- 同型リスク: `knowledge_landscape_design.md` が参照する入力仕様書
  `episteme_graph_knowledge_landscape_astrophysics_spec.md` も同様にリポジトリ外。

### 1-2. `episteme-graph_D層構想準備資料.md` が存在しない ⏳

`doubt_layer_issues.md:3` が「正本」として参照するが、リポジトリに存在しない（1-1 と同型）。
D層は実装済みのため実害は限定的だが、構想の一次資料が失われている。
（ヘッダの実装済み注記は ✅ 本整理で追加）

---

## 2. コード ⇄ ドキュメントの事実不一致（数値・実体）

| # | 箇所 | 記述 | 実際 | 対応 |
|---|---|---|---|---|
| 2-1 | docs/pipeline/overview.md:26 ほか | 「パイプライン 26 ステージ」表 #1〜26 | `_PIPELINE_STEPS` は31要素 = **named 29 ステージ** + between-stage フック2件（+ 終端マーカー `completed`）。`contextual_explanation` / `discuss_opening` / `landscape_placement` の3ステージが表に無い | 📝 冒頭注記 + docs/README.md は ✅ 修正 |
| 2-2 | docs/pipeline/agents.md | ContextualExplanationAgent / DiscussOpeningAgent / LandscapePlacementAgent の節が無い（実装は `src/episteme_graph/agents/` に実在） | 3 agent 分の解説欠落 | 📝 冒頭注記 |
| 2-3 | docs/backend/core-engine.md:125 | 「23 ステージを順次実行」 | named 29 ステージ（2-1 と同根。26 とも 23 とも書かれ文書間でも不一致） | ⏳ 数値の機械固定を提案 |
| 2-4 | docs/backend/api.md:35 | 「admin 系子ルーター（**14本**）」＋自らを「エンドポイントの正本」と宣言 | main.py の `prefix="/api/admin"` 登録は **19本**・自前 prefix 直接登録は **22本**。**節ごと欠落が計47エンドポイント**（cycle 4 / landscape 7 / teaching_figures 9 / llm_models 7 / atlas_gaps 4 / element_explanations 5 / discuss_observation 3 / help_kb 8）+ 既存節内の未記載 22本（学習8・atlas管理6・doubt7 ほか） | ⏳ 網羅テスト導入を提案 |
| 2-5 | CLAUDE.md（複数箇所） | admin 系子ルーター「**13本**」 | 実19本。main.py 自身のコメント（71-73行・328-330行「13個の子ルーター」）も同値で凍結 | ⏳ CLAUDE.md 改訂時に修正 |
| 2-6 | CLAUDE.md:878 ほか | 管理UIアンカー「228件」/ 他所で「カウント244 / 248 / 255」と4値が併存 | `test_admin_help_ui_anchors.py:101` が **260** で等値固定（テストが正） | ⏳ 「時点付きカウント」記法を提案 |
| 2-7 | docs/architecture/data-model.md | migration 一覧が 053 で終端 | 実在は **067 まで**（054〜067 の14ファイルが表に無い） | ✅ 本整理で追補 |
| 2-8 | docs/architecture/layer_registry.md:20,91,134 | 「次の空き番号は 054」「migration 帰属一覧（init〜053）」 | 空き番号は **068**。この案内のまま E層を 054 で採番すると既存 migration と衝突する | ✅ 本整理で更新 |
| 2-9 | README.md:336,351 | 「SQLマイグレーション（init.sql, 002〜053）」 | init + 002〜067 | ✅ 本整理で修正 |
| 2-10 | docs/features/field_atlas_skeleton.md:48 | 改版契機の閾値「修正報告 **10件**」 | 実装は `atlas_reports.py:51 REVISION_TRIGGER_THRESHOLD = 5`。D-1 正本 `field_atlas_correction_reports.md:78` も「5件」 | ⏳ skeleton.md 側の修正推奨 |
| 2-11 | docs/features/lecture_slide_sync_design.md:7 / CLAUDE.md:949 | 表示ソース判定の正本は `_lecture_uses_topic_material(topic)` | 現在は `core/lecture.py:854` の **`lecture_uses_topic_material`**（アンダースコア無し・core へ移設済み） | ⏳ 文書側の関数名貼り直し推奨 |
| 2-12 | docs/frontend/overview.md:23 | 「JSモジュール 22本」（表は21本で内部矛盾） | 実在は **31ファイル**（element-vocab.js / element-card.js / discuss.js / landscape-layer.js 等が未掲載） | 📝 docs/README.md に注記 |
| 2-13 | docs/features/personal_knowledge_network_design.md:102 | 「W-β（migration **046** 同乗）」 | element_identity_links は **048**（046 は atlas_report_incorporation） | ⏳ 該当行の修正推奨 |
| 2-14 | docs/pipeline/agents.md:143 | 「graph_narrative/ — 空ディレクトリ（レガシー）」 | 該当ディレクトリは既に存在しない | 📝 冒頭注記 |
| 2-15 | docs/pipeline/theory-graph.md:144 | `extract_theory_components_from_dsl()` を現行パイプラインの一部として記述 | 現行 A層パイプラインのチャンクは smiles_dsl を生成せず、実質レガシー経路（ComponentAssemblyAgent 系が本流） | ⏳ legacy 明示 or 退役判断を提案 |
| 2-16 | docs/backend/core-engine.md:42 | `compute_structure_diff()` / `evaluate_and_merge_proposals()` を現存関数として記載 | `extractor.py` の実体は `extract_tei_xml_from_pdf_bytes()` のみ（旧 diff/merge は 2026-07 削除済み。CLAUDE.md は削除済みと明記しており一致） | ⏳ core-engine.md の該当節削除推奨 |
| 2-17 | docs/architecture/deployment.md:119 | 「`init.sql`〜**`022_*.sql`** を冪等に適用」 | **事実誤り**。init〜067 を**毎起動・番号順に全ファイル再実行**（`core/migrations.py`・pg_advisory_lock）。同節は起動時処理も3項目のみで、atlas/L層/M層のシード取込・help_kb validator・V層スイーパ・status watcher が未記載 | ⏳ deployment.md §4 の書き直し推奨 |
| 2-18 | docs/architecture/data-model.md:30 | `learning_courses` の列に `cloned_from` を列挙 | **事実誤り**。`cloned_from` は migration 011 で DROP 済み（同ファイル L269-272 の記述と自己矛盾していた） | ✅ 本整理で修正 |
| 2-19 | CLAUDE.md（監査カタログ節） | 「`AUDIT_ENTITY_TYPES`（**30語彙**）」 | 実測 **35語彙**（landscape_placement / category_gap / teaching_figure / llm_model_policy 等が追加済み。正本はコード） | ⏳ CLAUDE.md 改訂時に「正本はコード」表記へ |
| 2-20 | docs/architecture/deployment.md:57 | nginx プロキシ対象の列挙に **`/api/atlas` が無い**（「など」で丸め） | CLAUDE.md が「欠落すると SPA フォールバックが index.html を 200 で返して事故る」と明記する必須プロキシ項目。明示列挙すべき | ⏳ |
| 2-21 | docs/architecture/overview.md:117 ほか | 「26 ステージ」「db/（init.sql, 002〜053）」「主要サブシステム表8行」 | named 29 ステージ / 002〜067 / D・R・V・W・L・U・G・M・SL 層等が表に無い | ⏳ overview.md の更新推奨（索引は layer_registry / vision.md が正） |

## 3. 実装済みなのに「未実装/Draft」表記のままの設計書

いずれも設計書本文（末尾の実装記録）と冒頭ステータスが矛盾していた。

| # | ファイル | 旧表記 | 実態 | 対応 |
|---|---|---|---|---|
| 3-1 | features/knowledge_network_vision.md:3 | 「2026-07-13 確定・**実装なし**」 | §7 の Phase W-β/S/P/B はすべて完了（2026-07-14〜16） | ✅ ヘッダ注記追加 |
| 3-2 | features/admin_assistant_design.md:3 | 「ステータス: **Draft**（設計提案）」 | Admin Copilot は実装済み（migration 034・routes 登録済み） | ✅ ヘッダ更新 |
| 3-3 | features/manual_help_kb_design.md:4 | 「Phase 0 **実装待ち**」 | Phase 0〜3 すべて実装済み（migration 058/059） | ✅ ヘッダ更新 |
| 3-4 | features/teaching_figure_studio_design.md:3 | 「ステータス: 設計（**未実装**）」 | §13 に Phase 0〜2 全実装記録あり（migration 063） | ✅ ヘッダ更新 |
| 3-5 | features/doubt_layer_issues.md | 実装状態ヘッダ無し + 消失した正本参照（1-2） | D層は実装済み（migration 029〜033） | ✅ ヘッダ注記追加 |

## 4. 主要機能が丸ごと欠落している機能解説ドキュメント（severity: high）

| # | ファイル | 欠落 | 対応 |
|---|---|---|---|
| 4-1 | docs/backend/rag-chat.md:86 | `intent_mode` の一覧が on_path/explore/casual の3値のみ。**discuss（4値目）が無い**（実装は learning.py に完備） | 📝 冒頭注記 |
| 4-2 | docs/features/learning.md | **discuss モードが文書のどこにも登場しない**（サイドバー二枚看板・開幕/着地・discuss.js）。理解サイクル（cycle_mode）・SL層の学習者向け表示も未反映 | 📝 冒頭注記 |

恒久対策（設計書は追記運用が徹底されているのに、**機能解説ドキュメント（learning.md /
rag-chat.md / admin.md / api.md）を同時更新する仕組みが無い**）は提案書 §1 を参照。

## 5. 解決済みレビュー・調査文書の放置（「未修正のバグ」と誤読される）

| # | ファイル | 状況 | 対応 |
|---|---|---|---|
| 5-1 | architecture/issue_494_implementation_review_2026-07-16.md | 「修正実施: なし」のまま。指摘6件（course_completed 表示・再構成キュー先頭教材・受講登録再試行 等）は**現行コードですべて解消済み**（検証班がコードで確認） | ✅ 解消済み注記を追加 |
| 5-2 | features/personal_knowledge_network_review.md | P1×2 + P2×2 の4指摘が未解決の体裁のまま。**4件とも修正済み**（`derive.py` の connected_refs 限定・journey の可視性ゲート・retired 除外・コース切替競合） | ✅ 解消済み注記を追加 |
| 5-3 | features/element_deliberation_workspace_review.md | P1/P2 の4指摘が未解決の体裁のまま。**4件とも解消済み**（048 の4列 UNIQUE・document 権限ゲート等）。加えてコード参照リンクが執筆者ローカルの絶対パス+行番号でリポジトリ外では無効（リンク8件） | ✅ 解消済み注記を追加（リンク記法は提案書 §3） |

## 6. リンク切れ・参照不良（機械検証）

| # | ファイル | 内容 | 対応 |
|---|---|---|---|
| 6-1 | docs/manual/teacher/14-admin-lecture-studio.md | リンク先 `10-admin-materials.md#overview` が不在 — 実ファイルは `11-admin-materials.md`（番号ズレ） | ✅ 修正 |
| 6-2 | features/element_deliberation_workspace_review.md | `/Users/.../file.py:行` 形式の絶対パスリンク8件（他クローンで無効） | ⏳ 記法統一を提案 |
| 6-3 | CLAUDE.md ほか | `field_atlas_overlay_spec.md` 参照（1-1 参照） | ⏳ |
| 6-4 | docs/features・architecture・pipeline | 他のどの .md からもリンクされない**孤児ドキュメント 23件**（当時） | ✅ docs/README.md の再編で全設計書を索引に結線 |

## 7. コード側の不具合・要修正コメント（ドキュメント突合で発見）

| # | 箇所 | 内容 | 対応 |
|---|---|---|---|
| 7-1 | backend/core/document_pipeline/orchestrator.py:78 | コメント「derivation_chain / course_mapping / **component_graph（いずれも非LLM・決定論的）**」— component_graph agent は自ら『hybrid deterministic/LLM edge-building pipeline』と明記し LLM クライアントを持つ。**「LLMを呼ぶステージか」の判定が LLM_STAGE_NAMES / llm_usage 語彙 / report_start(unit="llm_call") の3箇所で食い違う**根になっている | ⏳ 単一正本化を提案（提案書 §2-9） |
| 7-2 | backend/api/main.py:71-73, 328-330 | コメント「13個の子ルーター」（実19本）。ドキュメントと揃って陳腐化 | ⏳ コメント修正推奨 |
| 7-3 | サーバ/フロントの段階ラベル辞書 | D層・SL層等で日本語段階ラベル表が doubt.py / core/doubt/schema.py と doubt-atlas.js に**二重管理**（SL層設計書自身が §14 で別 issue と明記） | ⏳ 正本化を提案（提案書 §2-2) |
| 7-4 | guidance_layer_design.md:175 ⇄ status_notification_design.md | migration 038/039 の「なぜ入れ替わったか」の因果説明が**相互に矛盾**（最終番号自体は両方正しい） | ⏳ 「設計書に想定番号を書かない」運用を提案 |

## 8. 未検証の軽微指摘（読解班の指摘・検証班のコード実読は未実施）

以下は severity: low として記録のみ（対応要否は各正本の次回改訂時に判断）。

- understanding_cycle_design.md §4.4 の答えキー生成の記述が §15 実装記録の自己訂正と未同期
- vision_expansion_proposals_2026-08.md 提案3「四つの部品」と SL層設計書の SL-1〜5 の粒度ずれ
- knowledge_landscape_design.md の「配置不能の記録」完成度評価が category_gap_candidates_design.md の認識と食い違い
- release_review_flow_design.md の「AB1〜AB3」省略表記（AB4/AB7 が範囲外に見える）
- docs/backend/core-engine.md の document_pipeline/ 表に L層3ファイル（figure_images.py 等）欠落
- docs/pipeline/agents.md §3 共有モジュール表に figure_modes.py 欠落
- `classify_operation()` が component_graph/schema.py と theory_operations.py に同名別実装（ドキュメントは無修飾参照）
- admin_assistant_design.md のコスト上限既定値（10）が実運用値（20）と不一致
- CLAUDE.md が backend/core/chat.py を現役と説明（呼び出し元の無いデッドコードの可能性）
- exposition_layer_design.md 本文に旧 migration 番号（034）の DDL が残存（冒頭注記との混在）
- lecture_slide_sync_design.md §1 不変条項6 が撤去済み関数 `_topic_has_linkable_material` の維持を要求したまま
- MOCKS.md の EPISTEME_MOCK 規約は現在使用 0 件（candidate/status パターンに発展的統合済み — 位置づけの明記推奨）
- category_gap_candidates_design.md §10-4「未実施: コミット分割」等が現リポジトリ状態と不一致
- 2本のギャップ調査文書に「本調査以降の新層（UC/SL 等）は未評価」の明示が無い

---

## 統計

- 検証済み確定（CONFIRMED）: **30件**（high 12 / medium 18） + 機械検証リンク切れ 10件 +
  索引精査（migration 054〜067 帰属・API 欠落47本・事実誤り3件等の詳細列挙） + 統括確認 2件
- 検証で棄却された誤指摘: 0件（読解班の精度が高かった）
- 本整理で修正済み: 13件、注記で緩和: 7件、未対応（提案書で恒久対策）: 残件

**根本原因の類型**（恒久対策は [feature_consolidation_proposals_2026-08-13.md](feature_consolidation_proposals_2026-08-13.md)）:

1. **索引・カウント値に機械検証が無い**（アンカー260件のようにテスト固定された値はズレていない）
2. **設計書（追記運用が定着）と機能解説・索引（更新規約なし）の二層構造**で後者だけが取り残される
3. **リポジトリ外で書かれた正本**が未コミットのまま参照され続ける
4. **レビュー/調査文書に解決状態を追記する規約が無い**
