# 層の語彙（`feature_context.layers`）

[← 課題ナレッジの入口](README.md) ｜ [分類体系](taxonomy.md) ｜ [レイヤー索引表](../architecture/layer_registry.md)

> **状態:** 生きたリファレンス（2026-09-18 新設）。エントリの `feature_context.layers` に書ける値は
> この表の slug だけ（機械検査）。**場所の記録であって分類の根拠ではない。** 行を足すときは、
> [レイヤー索引表](../architecture/layer_registry.md) §1 に対応する行があるならその「正式名」を
> 対応列に書き、無い横断領域は「（索引表外）」と書く。

| slug | 対応（レイヤー索引表 §1 の層 / 正式名） |
|---|---|
| `pipeline_a` | A層 構造化パイプライン（PDF解析Agentパイプライン） |
| `learner_experience_b` | B層 学習者体験レイヤー（関心痕跡・tension・構造帰属・casual/voice） |
| `endorsement_c` | C層 承認・共有レイヤー |
| `doubt_d` | D層 疑義・認識的地位台帳 |
| `exposition_e` | E層 段階的翻訳レイヤー（未実装） |
| `guidance_g` | G層 ガイダンス層 |
| `image_library_l` | L層 画像読み取りパイプライン + 分野別ナレッジライブラリ |
| `model_selection_m` | M層 場面別 LLM モデル選択 |
| `reconstruction_r` | R層 再構成ループ |
| `field_atlas_s` | S層 分野の地図（Field Atlas） |
| `stakes_ledger_sl` | SL層 賭け金の台帳 |
| `usage_metering_u` | U層 LLM トークン使用量推計 |
| `account_lifecycle` | 運営基盤 アカウントライフサイクル管理 |
| `url_material_fetch` | 運営基盤 URL指定による教材取得 |
| `versioning_v` | V層 共有物のバージョン管理 |
| `deliberation_w` | W層 要素検討ワークスペース |
| `admin_copilot` | 横断ユーティリティ層 Admin Copilot |
| `status_notification` | 状態通知基盤 |
| `personal_network` | 個人知識ネットワーク |
| `understanding_cycle` | 理解サイクル |
| `discuss` | discuss（論文と話す） |
| `hierarchical_explanation` | 二層説明 |
| `help_kb` | 利用者マニュアル KB |
| `knowledge_landscape` | 知識ランドスケープ |
| `category_gaps` | カテゴリギャップ候補 |
| `vector_anchoring_va` | VA層 ベクトル係留 |
| `relation_edges_re` | RE追補 分野マップの関係表示 |
| `paper_discovery` | 論文ディスカバリー層 |
| `paper_radar` | 論文レーダー |
| `corpus_roaming` | コーパス回遊層 |
| `corpus_complement` | コーパスを補う論文 |
| `structure_descent` | 構造の降下路 |
| `graph_review` | グラフ対話レビュー |
| `graph_paper_layer` | グラフの論文層 |
| `screen_adapter_sa` | 画面文脈アダプター |
| `llm_streaming` | LLM 応答のストリーミング |
| `knowledge_objects` | 知識オブジェクト層 |
| `learning_units` | 学ぶ単位層 |
| `concept_registry` | 概念レジストリ層 |
| `knowledge_transfer` | 知識の転用層 |
| `teaching_figures` | 教材図スタジオ |
| `release_review` | リリース前の確認 |
| `teacher_triage` | 教員の弁と計器 |
| `seminar_brief` | ゼミ前ブリーフ・鏡面化 |
| `trace_registry` | 主権台帳（わたしの記録） |
| `indicator_catalog` | 制度指標カタログ |
| `disclosure_axes` | 可視性6軸の宣言 |
| `decision_context` | 確定文脈の記帳 |
| `shared_infra` | 横断基盤（共有ユーティリティ） |
| `rag_chat` | （索引表外）学習チャット・RAG 経路・前提知識ゲート・確認問題（`routes/learning.py` 中核） |
| `course_builder` | （索引表外）コースビルダー・コース登録・freeze（`course_content_builder`） |
| `lecture_studio` | （索引表外）原稿スタジオ・スライド分割・音声生成 |
| `lecture_player` | （索引表外）受講レクチャー・音声配信 |
| `auth_visibility` | （索引表外）認証・RBAC・document/course の可視性判定 |
| `frontend_learning_ui` | （索引表外）学習画面 SPA（`app.js` 系） |
| `frontend_admin_ui` | （索引表外）管理画面 SPA（`admin*.js` 系） |
| `cartridges` | （索引表外）カートリッジ定義と分野の選択 |
| `migrations_db` | （索引表外）migration ランナー・DDL・スキーマ |
| `export_bundle` | （索引表外）export bundle（`routes/export.py`） |
| `docs` | （索引表外）ドキュメント・索引・正本参照 |
| `tests_guardrails` | （索引表外）テスト・ガードレール機構そのもの |
| `deployment` | （索引表外）nginx・compose・Dockerfile・環境変数 |
| `theory_artifacts` | （索引表外）A層の成果テーブル（component / claim / 理論操作グラフの行）と、その承認・読み出し |
| `tts_voice` | （索引表外）読み上げ・音声入出力（`core/tts.py`・学習側 voice API・管理側の音声対話） |
