> **調査記録（2026-09-10）**: ビジョン×UXギャップ調査「六つのレンズ」の既知の課題リスト棚卸し（設計書の非スコープ・残課題側、130 項目）。統合と優先付けは [../vision_ux_gap_six_lenses_2026-09-10.md](../vision_ux_gap_six_lenses_2026-09-10.md)。読み取り専用調査で、file:line は HEAD d5ac3cb 時点。「推測」と明記した箇所は未確認。

# 既知の課題リスト棚卸し（設計書側）— docs/features + docs/pipeline + CLAUDE.md

- 作成: 2026-09-10 / 読み取り専用調査（リポジトリ非改変）
- 対象: `docs/features/*.md`（80本）・`docs/pipeline/*.md`（6本）・`CLAUDE.md`
- 収集方法: 見出しレベルでの「非スコープ / スコープ外 / 残課題 / 既知の限界 / ロードマップ /
  Phase N / v2 / やらないこと」節の全抽出（2,881行）→ 項目単位に分解 → コード grep で現状判定
- 判定の基準: CLAUDE.md の各層の節を「実装済みの正本に近い記述」として扱い、疑わしいものは
  `backend/core` / `backend/api/routes` / `frontend/public/js` の grep で確認した（判定根拠列）

規模感の目安: **S** = 1ファイル〜数十行・既存経路の拡張 / **M** = 新 core モジュールか新 API 数本 +
UI + テスト（1〜2日規模の実装単位）/ **L** = 新テーブル・新層・専用設計書が要る

---

## A. パイプライン / A層（構造・図・数式・グラフ）

| ID | 課題/提案 | 種別 | 出典 | 文書上の位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| A-01 | DocumentStructureAgent の「曖昧ブロックのみ LLM 補助」が未実装（実態は完全決定論） | 拡張 | pipeline/agents.md §2 | 既知の限界 | 未着手 | `_PIPELINE_STEPS` の宣言が `llm_kind="none"`（文書に明記）。実装なし | M | 決定論のままで良いか（誤型付けの実害があるかの実測が先） |
| A-02 | DocumentUnitBoundaryAgent がパイプライン未統合 | 拡張 | CLAUDE.md（agents 一覧） | 未実装 | 未着手 | `grep document_unit_boundary backend/core/document_pipeline/orchestrator.py` → 0件 | M | そもそも統合するか（作ったが繋いでいない） |
| A-03 | claim → 図（`claim.figure_ids`）の populate | 是正 | figure_concept_linking §3・§7 | 非スコープ（設計判断） | 棄却（意図的） | 正本は `FigureRecord.linked_claim_ids` 側の一方向に固定済み（artifact 冪等性のため） | — | — |
| A-04 | `Figs. 5.2 and 5.3` 型の列挙参照の2番目以降の展開 | 拡張 | figure_concept_linking §7 | 非スコープ v1 | 未着手 | crosslink.py は単一番号のみ | S | — |
| A-05 | 図クロスリンクの span 粒度化（v1 は block 粒度） | 拡張 | figure_concept_linking §7 | 非スコープ v1 | 未着手 | 同上 | M | — |
| A-06 | `figure_missing_linked_claim_ids` 警告の実データ減少率が定量未評価 | 文書 | figure_concept_linking 残課題 | 残課題 | 未着手 | 評価ハーネスなし | S | — |
| A-07 | TheoryOperationGraph への図・装置ノード組み込み | 新機能 | figure_concept_linking §7 / image_pipeline §13 | 非スコープ v1（式 backing なし） | 未着手 | graph 構築は derivation 由来のみ | L | 「式 backing の無いノードを入れない」規律を緩めるか |
| A-08 | 正解付き図セットでの定量評価ハーネス（反復照合解析の ablation） | 文書 | contextual_figure_analysis §v1 の既知の限界 | 既知の限界 | 未着手 | 評価コードなし | M | — |
| A-09 | バッチ経路の再スキャンでの focus crop（現在は guided 経路のみ） | 拡張 | contextual_figure_analysis 同上 | 既知の限界 | 未着手 | iterative.py はテキスト領域ヒントのみ | M | — |
| A-10 | 類似図（過去図・他文書）横断の prior | 新機能 | contextual_figure_analysis 同上 | 非スコープ | 未着手 | 同一図内の反復モチーフのみ | L | — |
| A-11 | 周辺本文ブロックの明示選択（`context_block_ids`） | 拡張 | guided_figure_reanalysis §9 | Phase 2 候補・別 issue | 未着手 | UI/API なし | M | — |
| A-12 | label↔説明文リンクの永続化（annotation kind='link'） | 拡張 | guided_figure_reanalysis §9 | Phase 2 候補 | 未着手 | 同上 | M | — |
| A-13 | 複数 focus 領域の指定（v1 は1矩形） | 拡張 | guided_figure_reanalysis §9 | Phase 2 候補 | 未着手 | — | S | — |
| A-14 | バッチパイプラインへの guidance 適用（v1 は単図再解析のみ） | 拡張 | guided_figure_reanalysis §9 | Phase 2 候補 | 未着手 | — | M | — |
| A-15 | 表（table）の画像解析（v1 は figure のみ） | 拡張 | image_pipeline §13 | 非スコープ v1 | 未着手 | apparatus は figure のみ | M | — |
| A-16 | 画像埋め込みモデル（CLIP 等）の導入 | 新機能 | image_pipeline §13 / §14-6 | 非スコープ v1 | 未着手 | retrieval はテキスト embedding | L | 費用対効果 |
| A-17 | `CartridgeContext.extraction_hints` への L層凍結版の汎用合成 | 拡張 | image_pipeline §6-6 | Phase 3 | 未着手 | 注入は apparatus_semantics の retrieval のみ | M | — |
| A-18 | `get_drawings()` によるベクター図の領域推定改善 / Gemini vision | 拡張 | image_pipeline §14-6 | 非スコープ（原 issue の Phase 3） | 未着手 | — | M | — |
| A-19 | パーツ単位の承認 API（確定はライブラリ昇格導線に委ねている） | 新機能 | image_pipeline §14-6 | 非スコープ | 未着手 | candidate-only 原則の帰結 | M | 承認オブジェクトを増やすか |
| A-20 | derivation の位置決め synth id が別の式へ再割当された場合を検出できない | 是正 | pipeline/theory-graph.md | 既知の限界 | 未着手 | canonicalization 自体の限界 | M | — |
| A-21 | 合成 claim は `equation_backed` 止まりで強い backing にならない（`missing_atomic_claim` が残る） | 是正 | pipeline/theory-graph.md | 限界（意図的） | 棄却（意図的） | #306 の意図した帰結 | — | 意図の再確認のみ |
| A-22 | ClaimObjectBuilder の atomic 子 claim / 式由来合成 claim が `theory_claims` に永続化されない | 是正 | CLAUDE.md（グラフ対話レビュー §14）/ memory | 別件（オーナー判断） | 未着手（読み時 artifact フォールバックで暫定対応済み） | `persist_qualified_claims` は qualified_spans のみ | M | **永続化するか**（承認オブジェクトの母集合が変わる） |

## B. 分野マップ（Atlas / Landscape / Gap / Vector / Edges）

| ID | 課題/提案 | 種別 | 出典 | 位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| B-01 | 橋渡し概念（Bridge Concept）の一級ノード化・`SkeletonRegion.domain_type` | 新機能 | knowledge_landscape §12 Phase 2 | Phase 2 | 未着手 | `grep bridge_concepts backend/` → 0件 | L | 骨格スキーマ進化の是非 |
| B-02 | EmergentRegion（自動クラスタ）/ コーパス別地図 / MapSnapshot 版比較 | 新機能 | knowledge_landscape §12 Phase 3 / corpus_roaming §9 | Phase 3 | 未着手 | `grep EmergentRegion` → 0件 | L | 「地図は正解でない」原則との整合 |
| B-03 | 問いビュー（central_question 集約）/ 方法ビュー / 系譜ビュー | 新機能 | knowledge_landscape §12 Phase 2 | Phase 2 | 未着手 | 該当 route/core なし | L | — |
| B-04 | W層 positioning「分野の地図」レンズへの landscape 配置行の合流 | 拡張 | knowledge_landscape v1 非スコープ / §12 | Phase 2 | 未着手 | `positioning.py` は atlas 骨格マッチのみで landscape 非参照 | M | — |
| B-05 | G層 `material.landscape_unreviewed` ルール | 新機能 | knowledge_landscape §12 / release_review §5 | Phase 2（優先度低下） | 未着手 | `grep landscape_unreviewed backend/` → 0件 | S | 恒常点灯の懸念（graph review と同じ姿勢） |
| B-06 | 学習者の配置異議申し立て（atlas_report 同型） | 新機能 | knowledge_landscape v1 非スコープ / corpus_roaming §9 | Phase 2 | 未着手 | 該当 API なし | M | 学習者に地図を直させるか |
| B-07 | 新版凍結後の過去論文の自動再配置バッチ | 拡張 | knowledge_landscape §12 Phase 4 / category_gap §7 | Phase 4 | 未着手 | — | M | — |
| B-08 | 出典タブ → chunk への evidence 遡及（claim_id→chunk 解決） | 拡張 | knowledge_landscape §12 Phase 2 | Phase 2 | 未着手 | — | M | — |
| B-09 | `GET /api/admin/landscape/overview` の UI 配線（API 実装済み・UI ゼロ） | 拡張 | release_review §5 | 非スコープ（次の候補） | 未着手 | `grep landscape/overview frontend/` → 0件 | S | — |
| B-10 | 教材一覧・コース一覧の行インジケータ（未確認件数の常時可視化） | 拡張 | release_review §5 | 非スコープ（次の候補） | 未着手 | — | S | 件数表示は LS5/PN-4 に抵触しないか |
| B-11 | カテゴリギャップ: 削除・改名・統合の候補化（現在 additive-only） | 新機能 | category_gap §7 | 非スコープ v1 | 未着手 | patching は op=add のみ | L | 再編を人手の年次改版に留めるか |
| B-12 | カテゴリギャップ: 学習者信号の入力混合 | — | category_gap §7 / KN-4 | 非スコープ（恒久） | 棄却 | 不変条項 | — | — |
| B-13 | 浮遊アンカー / EmergentRegion（gap 由来） | 新機能 | category_gap §7 | Phase 2/3 | 未着手 | B-02 と同族 | L | — |
| B-14 | VA層: date 順検索への着地予測 | 拡張 | atlas_vector_anchoring §11 | 非スコープ v1 | 未着手 | 候補ベクトルを作らない経路のため追加 embedding が要る | M | 追加 embedding を許すか |
| B-15 | VA層: ベクトル近傍からの alias candidate 行の自動生成 | 拡張 | atlas_vector_anchoring §11 | 非スコープ v1（実測後に検討） | 未着手 | 現状は読み時注記のみ | S | 注記の的中率の実測待ち |
| B-16 | 配置共起のグラフ埋め込み（node2vec / PMI）由来の骨格エッジ提案 | 新機能 | atlas_vector_anchoring §11 / atlas_relation_edges §10 | 非スコープ v1（将来の別設計書） | 未着手 | — | L | — |
| B-17 | 辺候補: co_occurrence 由来の糸の学習者表示 | 拡張 | atlas_relation_edges §10 | 非スコープ v1 | 未着手 | 学習者は vector のみ | S | — |
| B-18 | 辺候補: region を含む辺・辺の削除/改名候補 | 拡張 | atlas_relation_edges §10 | 非スコープ v1 | 未着手 | additive-only 継承 | M | — |
| B-19 | 糸のホバー詳細・クリック遷移 / L1・L3 への描画 | 拡張 | atlas_relation_edges §10 | 非スコープ v1 | 未着手 | L2 のみ | S | — |
| B-20 | 辺候補・gap 候補の G層 To-Do ルール | 新機能 | atlas_relation_edges §10 / category_gap §7 | 非スコープ（運用実測後） | 未着手 | — | S | 実測してからの判断 |
| B-21 | atlas: グループ限定・非掲載ドメイン / スチュワード制（凍結権限の所有モデル） | 新機能 | atlas_binding_lifecycle §10 | 非スコープ v1 | 未着手 | 現状「誰でも凍結 + 監査 + 通知」 | M | 権限モデルの是非 |
| B-22 | atlas: `atlas_domain_key` の cartridge_id からの分離実装 | 構造 | atlas_binding_lifecycle §10 | 非スコープ v1（明文化のみ） | 未着手 | 名前空間は cartridge_id のまま | M | — |
| B-23 | atlas: stale バインドの自動修復・改版の学習者通知・報告者への通知 | 拡張 | atlas_binding_lifecycle §10 | 非スコープ v1 | 未着手 | 検出と提示まで | S | 学習者に改版を告げるか（静かに変える現方針） |
| B-24 | 章末サマリー画面ができたら地図導線②を移設する | 拡張 | field_atlas_binding gap5 | 将来対応 | 未着手 | 章末画面自体が無い | S | — |
| B-25 | 修正報告の議論スレッド・k-匿名集計・自動クラスタリング | 新機能 | field_atlas_correction_reports 非スコープ | 非スコープ（issue D のまま） | 未着手 | — | M | — |

## C. ディスカバリー / コーパス回遊

| ID | 課題/提案 | 種別 | 出典 | 位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| C-01 | 学習者向け表示（ディスカバリー §7 の3機能） | 新機能 | paper_discovery §7・§8 | Phase 4 / v2 構想 | **済** | corpus_roaming（migration 073）で Phase A〜D 実装済み（CLAUDE.md 該当節） | — | — |
| C-02 | 引用グラフ候補の関連度ランキング | 拡張 | paper_discovery Phase 3 実装記録の非スコープ | 非スコープ | 未着手 | `/search` の order のみ | S | — |
| C-03 | OpenAlex 等の第3の供給源 / arXiv 以外（bioRxiv・HAL） | 拡張 | paper_discovery §8 | 非スコープ v1 | 未着手 | client は arXiv + Semantic Scholar | M | — |
| C-04 | 引用グラフの許可ドメイン管理 UI（現在は env オプトイン） | 拡張 | paper_discovery Phase 3 記録 | 非スコープ | 未着手 | `DISCOVERY_CITATION_SOURCE_ENABLED` のみ | S | — |
| C-05 | 購読の個人化（現在は分野単位の共同財） | 拡張 | paper_discovery §8 | 非スコープ v1 | 未着手 | — | M | 共同財の原則を崩すか |
| C-06 | 手動アップロード論文との重複判定 | 是正 | paper_discovery §8 | 非スコープ v1 | 一部（レーダーの出所後付け登録で緩和） | `source_url` 空は判定不能のまま。radar provenance で個別登録は可能 | M | — |
| C-07 | レーダー: 比較文の保存・レビューキュー化（現在は非保存） | 拡張 | paper_radar §8 | 非スコープ v1 | 未着手 | compare は非保存 | M | 保存するなら candidate-only 設計が要る |
| C-08 | レーダー: 見送り（dismiss）対応 / seed 複数指定 / 距離帯閾値の UI 調整 | 拡張 | paper_radar §8 | 非スコープ v1 | 未着手 | — | S | — |
| C-09 | レーダー: seed 1件指定の引用照会（引用グラフとの合成） | 拡張 | paper_radar §8 | 非スコープ v1 | 未着手 | citation-search は分野単位 | M | — |
| C-10 | コーパス補完: LLM による「読むと何が足されるか」の一段落説明 | 新機能 | corpus_complement §9 | 非スコープ v1 | 未着手 | 3レンズは非LLM | M | 実測後（非LLM で足りるか） |
| C-11 | コーパス補完: レンズC の被引用（citations）方向への拡張 | 拡張 | corpus_complement §9 | 非スコープ v1 | 未着手 | references のみ | M | — |
| C-12 | コーパス補完: 参照キャッシュの起動時バックフィル・定期更新 | 拡張 | corpus_complement §9 | 非スコープ v1 | 未着手 | read-through のみ | S | — |
| C-13 | コーパス補完: 外部被引用数（citationCount）の利用・表示 | — | corpus_complement §9 / CC4 | 非スコープ | 棄却（数値非表示） | 不変条項 | — | — |
| C-14 | レンズ結果の購読条件（キーフレーズ）への自動還流 | — | corpus_complement §9 / PD3 | 非スコープ（恒久） | 棄却 | 条件を広げるのは教員 | — | — |
| C-15 | コーパス回遊: document 直付けセッションでの tension/anchor confirm・着地画面・再構成 | 拡張 | corpus_roaming §9 | v2 | 未着手 | Phase B は opening/chat/history のみ・縮退で null 化 | M | コース外に確定弁を開くか |
| C-16 | 学習者への引用グラフ表示・未取り込み候補のタイトル開示 | 新機能 | corpus_roaming §9 | 非スコープ v1 | 未着手 | 外の輪は存在のみ | M | 好奇心の文法との整合 |
| C-17 | 可視性の既定・一括変更ツール（大量取り込み論文の public 化運用） | 拡張 | corpus_roaming §9 | 非スコープ（別マター） | 未着手 | — | M | 既定を public 寄りにするか |
| C-18 | URL 取得: arXiv API によるメタデータ自動補完 / 一括取得 / 非同期化 | 拡張 | url_material_upload §9 | 非スコープ v1 | 一部 | 一括取り込みは discovery のキューで実現。単発 URL は同期のまま | S | — |
| C-19 | URL 取得: Admin Copilot capability 登録 / 取得先ごとのレート上限 | 拡張 | url_material_upload §9 | 非スコープ v1 | 未着手 | — | S | — |

## D. 学習体験（discuss / 理解サイクル / R層 / 個人地図 / E層）

| ID | 課題/提案 | 種別 | 出典 | 位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| D-01 | **E層（Exposition Layer / 段階的翻訳レイヤー）本体** | 新機能 | exposition_layer_design §10 | 設計中（未実装） | 未着手 | `backend/core/exposition` / `routes/exposition.py` とも不在（2026-09-03 再確認） | L | 着手可否そのもの（層としては最大の空白） |
| D-02 | discuss Phase 3（コース非依存の正面突破） | 新機能 | discussion_mode §6.4 | Phase 3（v2） | **済（別層として）** | corpus_roaming Phase B（センチネル `_doc:` 方式）で実装済み。ただし §6.4 が挙げた「教員向け k-匿名集約」「音声版」は未実装 | — | 残差（下記 D-03）だけ判断 |
| D-03 | discuss の教員向け k-匿名集約（どの構造要素に問いが集まるか）・音声版 | 新機能 | discussion_mode §6.4 | Phase 3 の残差 | 未着手 | anchor-insights は既存だが discuss 経路の集約は無い | M | — |
| D-04 | 専用 `DISCUSS_MAX_CALLS_PER_DAY` の要否（現在は learning_chat に相乗り） | 拡張 | discussion_mode 裁定#9 | U層実測後に判断 | 未着手 | env なし | S | 実測を見て判断 |
| D-05 | discuss 対話のサーバ側局面状態機械・局面 DTO / UI 局面チップ | 新機能 | discuss_dialogue_alignment §8 | 保留（オーナー確認済み） | 未着手（保留） | プロンプト自己管理のまま | M | **保留解除の判断**（E2E で逸脱が観察されたら） |
| D-06 | 学習チャットの履歴ウィンドウ拡張（20/2000） | 拡張 | discuss_dialogue_alignment §8 | 保留 | 未着手 | `window_history` 既定のまま | S | 同上 |
| D-07 | discuss 観測基盤への新イベント（応答の質の近似判定） | 拡張 | discuss_dialogue_alignment §8 / discuss_observation §7 | 保留 | 未着手 | — | S | 本番データで判断したくなった時点 |
| D-08 | R層 構造 DIFF と discuss の接続（言い直し vs central_thesis） | 新機能 | discuss_dialogue_alignment §8 | 構想（実装予定リスト外） | 未着手 | — | L | 別 issue 起票の是非 |
| D-09 | 429（CostGate 拒否）の計測 | 是正 | discuss_observation §7・課題#5 | 既知の限界 | 未着手 | CostGate は in-memory で events に載らない | S | — |
| D-10 | 開幕素材: 学習者ごとの個人化 / 多言語 / course_focus の AI 生成 | 拡張 | discuss_opening_authoring §9 | 非スコープ v1 | 未着手 | — | M | 個人化は UC5/UC7 と衝突しうる |
| D-11 | 開幕素材・二層説明が V層 freeze に含まれない（document 側資産のため） | 是正 | discuss_opening_authoring §7.2 | 既知の限界 | 未着手 | V層の保護範囲は course 側資産のみ | L | V層の範囲拡張の是非（説明文も同時） |
| D-12 | approved stale 行を編集しても旧指紋を引き継ぐため stale 表示が残る | 是正 | discuss_opening_authoring §12.3 | 既知の限界 | 未着手 | 指紋更新は core 側変更が要る | S | — |
| D-13 | 理解サイクル: 白地図スケッチ（地図スケール ELICIT） | 新機能 | understanding_cycle §12 | 非スコープ v1（atlas と合同設計） | 未着手 | — | L | — |
| D-14 | 理解サイクル: 時間レンズ（Chronicle Lens・W層第5レンズ） | 新機能 | understanding_cycle §12 / vision_expansion Phase 2 | 別設計書 | 未着手 | — | L | — |
| D-15 | 理解サイクル: 音声・casual 経路への精読モード適用 | 拡張 | understanding_cycle §12 | 非スコープ v1 | 未着手 | — | M | — |
| D-16 | 対比ペア出題（contrasting cases）・論文骨格予測の support_structure 対応付き提示 | 拡張 | understanding_cycle §15 残作業 | Phase 2 の optional・未実装 | 未着手 | AI Diff が部分代替 | M | — |
| D-17 | 理解サイクル Phase 4（集合知・橋の生態系・静かな開通・欲望の小径） | 新機能 | understanding_cycle §7 / vision_expansion Phase 4 | 参照仕様のみ | 未着手 | 着手条件＝ガードレール5条件を先に書けること | L | **着手条件の充足判断** |
| D-18 | 学習者モデル・習熟度推定による適応 | — | understanding_cycle §12（UC5/UC7） | 恒久排除 | 棄却 | 不変条項 | — | — |
| D-19 | 帰還の扉 v2: 想起リンク+二列並置 / 減衰表示 / leave_note の時間層表示 | 拡張 | return_door §6 | 非スコープ v2 | 未着手 | — | M | — |
| D-20 | 構造の降下路 v2: フェルミの点検口・宣言された揺さぶり群・ダイヤル上限の本人設定 | 新機能 | structure_descent §8 | 非スコープ v2 | 未着手 | — | M | — |
| D-21 | 楽屋の「以降は集計に入れてよい」動的同意 UI | 新機能 | structure_descent §4 | v1 では作らない（主権台帳 v2 と同時判断） | 未着手 | — | M | dynamic consent の設計判断 |
| D-22 | R層: 導出並べ替え・反実仮想予測などの上位段タスク | 新機能 | reconstruction_loop §2.3 | スコープ外 | 未着手 | — | L | — |
| D-23 | R層: 言い直し（自由記述）の LLM 採点（candidate 提示に留める拡張） | 拡張 | reconstruction_loop §2.3 | 将来 | 未着手 | DIFF は非LLM のまま | M | 採点語彙を持ち込む是非（P7） |
| D-24 | R層: 間隔反復スケジューラ | 新機能 | reconstruction_loop §2.3 | 将来別 issue | 未着手 | `grep spaced backend/core/reconstruction` → 0件 | M | UC4「間を埋めない」と衝突 |
| D-25 | R層: 学習者の再構成を C層で共有する機能 | 新機能 | reconstruction_loop §2.3 | スコープ外 | 未着手 | — | M | 本人のみ可視の原則 |
| D-26 | 個人地図: コース横断の統合ビュー / 誤解ノード | 新機能 | personal_knowledge_network §13 | 非スコープ v1 | 一部（P-0.5〜P-3 で `/api/me` 横断は実装済み。誤解ノードは未着手） | CLAUDE.md 個人知識ネットワーク節 | M | 誤解ノードの confirm フロー設計 |
| D-27 | 個人地図: `derive_person_network` のコース単位 N+1 | 是正 | personal_knowledge_network §17.4 | 残課題 | 未着手 | コース数で線形悪化 | S | — |
| D-28 | 個人地図: コーススコープ版 journey への topic 範囲エントリ適用 | 拡張 | personal_knowledge_network §17.4 | 残課題 | 未着手 | 正本 API 側のみ | S | — |
| D-29 | 個人地図: 削除コース痕跡の空ラベルの中立表示 / kind 視覚記号のタブ間統一 | 拡張 | personal_knowledge_network §17.4 | 残課題（磨き込み） | 未着手 | — | S | — |
| D-30 | 供給側の根治（confirmed 同一性リンク・atlas binding の充実を促す G層 To-Do） | 新機能 | personal_knowledge_network §17.4 | 別 issue | 未着手 | `grep topics_unlinked backend/` → 0件 | M | — |
| D-31 | 個人地図 nearby: エッジへの動詞ラベル付与 / 点ビューの excerpt 描画 | 拡張 | personal_map_nearby §12.4 | 非スコープ（磨き込み） | 未着手 | — | S | — |
| D-32 | 個人地図 nearby: concept / chunk / segment / graph_edge アンカーの中心解決 | 拡張 | personal_map_nearby §8 | 非スコープ v1 | 一部（topic は §10 範囲モードで解決済み） | 設計書 §10 | M | — |
| D-33 | 個人地図 nearby: 教材の順序上の前提（`topics[].prerequisites`）との合流 | 拡張 | personal_map_nearby §8（R4） | 非スコープ v1 | 未着手 | — | M | — |
| D-34 | 好奇心装置: k-匿名の「他の学習者の橋」提示 | 新機能 | personal_map_curiosity §6 | 非スコープ v1 | 未着手 | PN-1 の再検討が前提 | M | **PN-1（本人のみ可視）の例外を作るか** |
| D-35 | 教材オープン時の「過去の理解との接続」事実文の自動掲出 | 拡張 | personal_knowledge_network v1 の限界 | 未実装 | 未着手 | 旅カードの cross_course_hint まで | S | 自動で開かない原則との折り合い |
| D-36 | 学習チャット: 誤解記録（`misconceptions_by_topic`）が message_id 紐づけを持たず個別 supersede できない | 是正 | CLAUDE.md（書き直し・削除） | 既知の限界 | 未着手 | payload に message_id リンク無し | M | — |
| D-37 | 欄外の印の素材位置への正確な対応付け | 是正 | features/learning.md | 非スコープ v1（帰属ラベルの近似） | 未着手 | — | M | — |
| D-38 | レクチャー: 学習者側の音声言語選択（ja/en 並存配信）/ ja・en 以外 | 拡張 | lecture_slide_sync §10 | 非スコープ v1 | 未着手 | コース単位単一言語 | M | — |
| D-39 | レクチャー: `word_timestamps` による単語レベル精密ハイライト | 拡張 | lecture_slide_sync §10 / learning_ui_inspect §14 | 非スコープ（既知の未実装） | 未着手 | 生成経路が timestamps を書かない | M | — |
| D-40 | 通常閲覧ビュー（非レクチャー）のスライド化 / display_text の翻訳 | 拡張 | lecture_slide_sync §10 | 非スコープ v1 | 未着手 | — | M | — |
| D-41 | カジュアル音声会話（`/voice/speak`）の言語切替 | 拡張 | lecture_slide_sync §10 | 非スコープ（別機構） | 未着手 | — | S | — |
| D-42 | 学習 UI: モバイル（ホバー不在デバイス）対応 | 拡張 | learning_ui_inspect §14 | 非スコープ v1 | 未着手 | — | M | — |
| D-43 | 学習者向け要素文脈: `notes` に運用語彙（「旧 run のため artifact が無く」）が出る余地 | 是正 | learner_element_context §8.3 | 残課題（保留） | 未着手 | 教員向け文面をそのまま通している | S | — |
| D-44 | 学習者向け要素文脈: 内部 ID が事実文に埋め込まれたラベルの置換 | 是正 | learner_element_context §4・§8.3 | 既知の限界 | 未着手 | `導出「der_001」…` は置換対象外 | S | — |
| D-45 | 学習者向け要素文脈: レーン上限の適用順序で表示件数が 20 未満になり得る | 是正 | learner_element_context §8.3 | 残課題 | 未着手 | W層 cap（candidate 込み）→ 学習者フィルタの順 | S | — |
| D-46 | 学習者向け要素文脈: equation の document 走査が線形・artifact の二度読み | 是正 | learner_element_context §5・§8.3 | 既知の限界 | 未着手 | — | S | — |
| D-47 | component 文脈 API と V層版ピンの整合（現状 HEAD 読み） | 是正 | component_evidence_redesign §8.4 | 既知の限界 | 未着手 | D-11 と同根 | L | — |
| D-48 | component_assembly の `teaching_takeaway` 日本語化（英語露出の根治） | 是正 | component_evidence_redesign §8.4 | 中期課題 | 未着手 | A層プロンプト変更が要る | M | A層非改変の原則を破る唯一の候補 |
| D-49 | 教材本文の決定論注入（「この節で使う数式」行）が `headline` を使っていない | 是正 | element_context_presentation §10.6 残作業 | 残作業 | 未着手 | `_ensure_required_equations_in_material` は `label or equation_id` | S | — |
| D-50 | `_ensure_standard_explanation` が approved contextual を standard 初期値に使う拡張 | 拡張 | hierarchical_context_explanation §5.2 | v1.1 / 未決 | 未着手 | C層と element_explanations の二重化回避のため保留 | S | 正本の二重化を許すか |
| D-51 | 汎用×固有の結線（context_lens の generic ブロック・identity link UI の脱 UUID・journey への説明運搬） | 拡張 | hierarchical_context_explanation §6（Phase 3） | Phase 3（P2 Phase 3 とは別フェーズ） | 未着手 | 指示書 §1.1 が「今回のスコープ外」と明記 | M | — |
| D-52 | コースビルダーのコンテキストに図の件数・caption を渡す | 拡張 | hierarchical_context_explanation §7.4 | 任意・v1.1 | 未着手 | — | S | — |
| D-53 | 学習者への bbox オーバーレイ / L層ライブラリ本文の学習者開示 / 図キャプションの TTS | 拡張 | hierarchical_context_explanation §9 | 非スコープ v1 | 未着手 | — | M | — |
| D-54 | 図メンションが無い図への上位接続の推測補完（crosslink 検出強化） | 是正 | hierarchical_context_explanation §9 | 非スコープ（別課題） | 未着手 | 正直な縮退を維持 | M | — |
| D-55 | ホバー: 図・claim・component のホバー内容の見直し | 拡張 | equation_hover §7 | 非スコープ | 一部（Phase 1 の raw_text 除去は共通関数経由で全種別に波及） | 設計書 §7 | S | — |
| D-56 | W層 `_equation_label` の 80 字切り詰めの修正 / `plain_text`（読み下し）の生成 | 是正 | equation_context_panel §5.3 | スコープ外・別 issue | 未着手 | — | S | — |

## E. 教員支援（W層 / graph review / studio / library / triage）

| ID | 課題/提案 | 種別 | 出典 | 位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| E-01 | W層: 学習者向け表示・学習者の対話 | 新機能 | element_deliberation §14 | 非スコープ v1（別 issue） | 未着手 | 教員のみ（`_require_teacher`） | L | 地位勾配との兼ね合い |
| E-02 | W層: 要素粒度 embedding テーブル（現在 chunk-proxy 近似） | 拡張 | element_deliberation §14 | Phase 3 まで近似 | 未着手 | — | M | — |
| E-03 | W層: evidence / derivation の位置づけ4レンズ・注釈 commit・共通部品化 | 拡張 | element_deliberation §16 | 不可（v1） | 未着手 | 422 で明示拒否 | M | — |
| E-04 | W層: 文脈上の役割の再利用（人間確定注釈の正規格納先ルーティング） | 拡張 | element_context_lens Phase 3 | Phase 3 | 一部 | commit ルーティングは3経路実装済み・`positioning_note` は 422 のまま | M | — |
| E-05 | 要素インベントリ: 複数教材・コーパス横断 / 全文検索・pgvector 類似 / symbol・derivation step の型追加 | 拡張 | element_inventory §12 | 非スコープ v1 | 未着手 | — | M | — |
| E-06 | 要素インベントリ: equation の LaTeX レンダリング / figure サムネイル | 拡張 | element_inventory §12 | 非スコープ v1 | 未着手 | — | S | — |
| E-07 | グラフ対話レビュー: 一括承認（bulk-review 同型） | 拡張 | graph_dialogue_review §9 | Phase 2 候補 | 未着手 | v1 は1件ずつ | M | 一括確定は `decision_context` 記帳が必須（F-14 と連動） |
| E-08 | グラフ対話レビュー: edge / equation / evidence / derivation ノードの承認 | 新機能 | graph_dialogue_review §9 | 非スコープ v1 | 未着手 | 承認対象は component / claim のみ | M | 承認オブジェクトを増やすか（PL5 と衝突） |
| E-09 | G層 `material.components_unreviewed` ルール | 新機能 | graph_dialogue_review §9 | 非スコープ（運用実測後） | 未着手 | `grep components_unreviewed` → 0件 | S | 恒常点灯の懸念 |
| E-10 | 論文層: LLM による章推定・一段落説明（Phase 1） | 新機能 | graph_paper_layer §5・§6 | Phase 1 | 未着手 | v1 は contextual 説明の join のみ | M | まず非LLM で足りるかの実測 |
| E-11 | 論文層: 被覆の下流（G層・候補化 = Phase 2）/ 図画像のインライン表示 / 論文層の保存・版管理 | 拡張 | graph_paper_layer §5・§6 | Phase 2 / 非スコープ | 未着手 | — | M | — |
| E-12 | 教材図スタジオ Phase 3: ラスター生成 / 学習者起点の図リクエスト / data_plot への実データ接続 / 図 DSL / アニメーション | 新機能 | teaching_figure_studio §9 Phase 3 | v2 候補・非スコープ | 未着手 | — | L | FG5（実測値捏造禁止）を緩める設計判断 |
| E-13 | 教材図: 版ピン中の学習者への図スナップショット配信 | 是正 | teaching_figure_studio §7 | 既知の限界（D-11 と同格） | 未着手 | — | L | — |
| E-14 | 教材図: 学習者配信の `svg_source` フォールバック | 是正 | teaching_figure_studio §13.1 残課題 | 残課題 | **済** | `routes/learning.py:1949-1959` で MinIO 失敗時に `svg_source` へ縮退済み | — | — |
| E-15 | 教材図: G層 optional ルール `topic.figure_gap_pending` | 新機能 | teaching_figure_studio Phase 2（任意） | 任意・未実装 | 未着手 | `grep figure_gap_pending` → 0件 | S | — |
| E-16 | 教材図: W層「深く検討」/ #496 モード分類の対象化 | 拡張 | teaching_figure_studio §9 | 非スコープ | 未着手 | — | M | — |
| E-17 | L層: グループ限定ライブラリ / 学習者向けライブラリ表示 | 新機能 | image_pipeline §13 | 非スコープ v1 | 未着手 | 教員全体の共同財 | M | — |
| E-18 | 教員トリアージ v2: 配置キュー・カテゴリギャップキューへの拡張 / 分割候補の点線提示 / 音声一括生成モーダルへの見通し展開 | 拡張 | teacher_triage §7 | 非スコープ v2 | 未着手 | — | M | — |
| E-19 | 教員トリアージ: 通りすがりの開封・便り（📮） | 新機能 | teacher_triage §7 | 非スコープ v2 | 未着手 | 連続上限・撤退条件の事前定義が前提 | L | — |
| E-20 | 教材の密度の学習者向け射影（WM レンズと同一エンジン） | 新機能 | teacher_triage §7 / structure_descent §8 | v2（EX-1 裁定） | 未着手 | — | M | — |
| E-21 | WM レンズ: トピック教材由来スライドの記号照合が textual に縮退（事実文で正直表示） | 是正 | teacher_triage §3.2 | v2 で拡充 | 未着手 | — | S | — |
| E-22 | ゼミ前ブリーフ: 第4区画（学習者からの手渡し）の実配信・手渡しチャネル本体 | 新機能 | seminar_brief_mirroring §5 | 非スコープ v2（**P3 改正の例外設計書必須**） | 未着手 | v1 は空欄予約 | L | **P3（学習者を監視しない）の例外を開くか** |
| E-23 | 鏡面化: 構造分離検査（案B）・鏡への訂正の専用 UI | 拡張 | seminar_brief_mirroring §5 | 非スコープ v2 | 未着手 | — | M | — |
| E-24 | D層: LLM 事前知識への三角測量照会（前提×領域の検証知識） | 新機能 | stakes_ledger §13 | 非スコープ v1（実測後判断） | 未着手 | — | M | — |
| E-25 | D層: コーパス横断の認識的ストレステスト（identity link を渡る伝播） | 新機能 | stakes_ledger §13 | Phase 4 の領域 | 未着手 | — | L | KN-3 の弁の設計が前提 |
| E-26 | D層: Assumption Atlas への支持線・反証条件の表示 / 「前提×領域」二次元晴れ間マップ | 拡張 | stakes_ledger §13 | 非スコープ v1 | 未着手 | 点の情報過多を避ける判断 | M | — |
| E-27 | D層: 学習者の反実仮想操作・晴れ間閲覧・条件への異議 | 新機能 | stakes_ledger §13 / doubt §8-3 | 非スコープ v1 | 未着手 | 地位勾配を理由に意図的に閉じている | L | **学習者に疑義を開くか**（運用観察後の判断と明記済み） |
| E-28 | D層: 反証条件の自動再生成トリガー（パイプラインフック相乗り） | 拡張 | stakes_ledger §13 | 非スコープ v1 | 未着手 | — | S | — |

## F. 基盤（V層 / U層 / M層 / help_kb / G層 / アカウント / 統治 / SA層）

| ID | 課題/提案 | 種別 | 出典 | 位置づけ | 状態 | 判定根拠 | 規模 | オーナー裁定 |
|---|---|---|---|---|---|---|---|---|
| F-01 | V層: document 成果物4読み取り API のピン凍結ブラウズ | 是正 | shared_versioning 既知の限界 | follow-up | 未着手 | `stage_outputs` からの DTO 完全再構築はリスク大として保留 | L | D-11 / D-47 / E-13 と束ねて「V層の保護範囲」を決める |
| F-02 | U層: run 単位の実測内訳 API（`/analysis-runs/{run_id}/llm-usage`） | 新機能 | llm_usage_metering §7-3 | Phase 2 | 未着手 | `routes/llm_usage.py` の route は metrics / estimate / forecast の4本のみ | S | — |
| F-03 | U層: enforcement（トークン予算による拒否・スロットリング） | 新機能 | llm_usage_metering §12 | 非スコープ v1 | 未着手 | forecast は fail-open で enforcement 非使用 | M | 観測から制御へ移るかの判断 |
| F-04 | U層: ストリーミング応答の usage 計測 / 請求書突合 / transcribe の秒数算出 / 保持期間 aging | 拡張 | llm_usage_metering §12 | 非スコープ | 未着手 | 非ストリーミングのみ | M | — |
| F-05 | M層: 再解析モーダルの「前回の解析結果を再利用するステージ」告知（m8） | 拡張 | llm_model_selection §6.1-D・m8 | 未実装（既知ギャップ） | 未着手 | `initReanalyzePanel(el, lastOpts, documentId)` は実装されたが用途はコスト見通し。`_stage_models` を読む API・表示なし | S | — |
| F-06 | M層: 教材一覧の「解析モデル」列（表示のみ） | 拡張 | llm_model_selection §6.1-C | 未実装 | 未着手 | `grep 解析モデル frontend/js/admin.js` に一覧列なし | S | — |
| F-07 | M層: `rewrite_lecture_studio_course_topic` の `requested_model or params["model"]` バイパス | 是正 | llm_model_selection 同種の残課題 | 残課題（担当範囲外） | 未着手 | 既存テストが挙動を固定 | S | — |
| F-08 | M層: グループ scope / 自動フォールバック / ステージ別選択のユーザー既定保存 | 拡張 | llm_model_selection §9 | 非スコープ v1 | 未着手 | scope は system / user のみ | M | — |
| F-09 | help_kb Phase 3（ベクトル補助 / DB draft-freeze / content-hash 監査） | 拡張 | manual_help_kb §5 Phase 3 / security_directive §4 | 実装済みだが**裁定保留** | 済（実装）+ 未裁定 | migration 058/059 実装済み。指示書 P1 は「維持・既定 OFF・撤去の明示判断が未了」 | S（裁定のみ） | **維持 / 既定 OFF / 撤去の3択** |
| F-10 | G層: 学習者向けバッジ / To-Do 自動実行 / 外部通知 / 進捗率 | — | guidance_layer §12 | 非スコープ（決定事項） | 棄却 | 不変条項 | — | — |
| F-11 | 状態通知: メール・プッシュ等の外部配信 / 学習者向け通知 | 新機能 | status_notification §7 | 非スコープ（決定事項） | 棄却（v1）/ 構造だけ用意 | fan-out の先に足すだけの構造 | M | 外部配信を解禁するか |
| F-12 | アカウント: 本人パスワード変更・「忘れた」フロー・初回変更強制 | 新機能 | account_lifecycle §13 | 非スコープ v1 | 未着手 | `grep change-password / must_change_password routes/` → 0件（メール基盤なし） | M | メール基盤を持つかどうか |
| F-13 | アカウント: OAuth / 外部 IdP / セッション単位失効 / ログイン失敗のレートリミット・自動ロック | 新機能 | account_lifecycle §13 | 非スコープ v1 | 未着手 | `auth_provider` は 'local' 固定 | L | — |
| F-14 | アカウント: ロール変更 API（learner ⇄ instructor ⇄ admin） | 新機能 | account_lifecycle §13 | 非スコープ v1 | 未着手 | AL10「降格できない」を先行制約として置いてある | M | 降格の可否（AL10 の解釈） |
| F-15 | アカウント: 一括操作（CSV インポート・一括停止）・有効期限 | 拡張 | account_lifecycle §13 | 非スコープ v1 | 未着手 | — | M | — |
| F-16 | アカウント: NO ACTION FK 17列の整理・`learning_courses.owner_id` 死列の DROP・`groups.created_by` CASCADE 是正 | 構造 | account_lifecycle §13 | 別 issue（負債返済） | 未着手 | AL1 により実害は消えている | M | — |
| F-17 | アカウント: 学生・教員本人への自分の利用実績表示 | 新機能 | account_lifecycle §13（U5 / AL6） | 非スコープ v1 | 未着手 | 数値は SYSTEM_ADMIN のみ | M | 本人開示の可否（U5 の例外） |
| F-18 | `decision_context` の段階適用の残り（単発承認 / 骨格凍結 / コース公開 / 学習者側の確定） | 是正 | decision_context §6 | 段階適用中・未適用 | 一部（一括2経路のみ） | §6 に列挙。`basis` 定数は2本 | M | 学習者側の確定に記帳を入れるか（PN-1 / P3 との兼ね合い） |
| F-19 | 指標カタログ: 値の履歴・独立監査者ロール・副作用レビューの自動化・学習者向け閲覧 UI | 新機能 | indicator_governance §8 | 非スコープ v1 | 未着手 | v1 は定義のみ | M | 独立監査者ロールを作るか（vision §6.1） |
| F-20 | SA層 Phase 2〜4（W層要素モーダル / Admin Copilot / 学習チャットへの画面文脈） | 拡張 | assistant_screen_adapter §6 | 予約（着手時に §追加） | 未着手 | 第1適用先はグラフレビューのみ | M | Phase 4（学習者側）の可否 |
| F-21 | Copilot 共通基盤: 音声 transcribe/speak の回数上限 / CostGate の DB 化 / 会話本文の不変監査ログ | 拡張 | assistant_common_infra §8 | 非スコープ | 一部（W層 voice は day-only CostGate あり・学習側は無し） | CLAUDE.md グラフ対話レビュー §12 | S | 会話本文の監査は P3 と衝突 |
| F-22 | デッドコード削除（`chat_sessions`/`chat_messages`・`core/chat.py`・`_llm_retry_policy`） | 構造 | assistant_common_infra §8 / CLAUDE.md | 別タスク・削除候補 | 未着手 | `backend/core/chat.py` 現存・`chat_sessions` は init.sql に現存 | S | — |
| F-23 | コースビルダーのドラフト注入のサーバ側移設 | 構造 | assistant_common_infra §8（§2-2） | 非スコープ | 未着手 | フロントが履歴先頭に疑似ターン2件を注入し続けている | M | — |
| F-24 | Copilot: 応答文の LLM 生成化・画面コンテキストの全タブ展開・casual モードのテキスト UI | 拡張 | assistant_common_infra §8 | 非スコープ | 未着手 | — | M | — |
| F-25 | `candidate_flow` への既存8系統の巻き取り | 構造 | candidate_flow §6 | 非スコープ（次の新系統から適用） | 未着手 | 既存コードは1行も変更していない | L | 巻き取りコストを払うか |
| F-26 | レビューキューのレジストリ化 | 構造 | candidate_flow §6（提案 §2-8） | 別提案 | 未着手 | キューが層ごとに散在 | M | — |
| F-27 | 訳語の統一（「AI推定（未確認）」/「AIによる推定（未確認）」等）と語彙エンドポイント | 是正 | label_vocab §7 | オーナー判断事項として繰り延べ | 未着手 | allowlist で分裂を可視化するところまで | S | **出力文字列を変える判断**（静的テストが原文を固定） |
| F-28 | フロント側の段階ラベル表の全廃（API が段階ラベルを返す形への移行） | 構造 | label_vocab §7 | 非スコープ | 未着手 | ミラー規律で二重管理中 | M | — |
| F-29 | 知識ネットワーク: `element_identity_links` を instance ↔ instance にも開くか | 構造 | knowledge_network_vision §8 未決事項2 | 未決事項 | 未決 | ハブ経由限定のまま | M | **未決（推奨はハブ経由限定）** |
| F-30 | 知識ネットワーク: 旅の経路探索の提示条件（いつ・どこで出すか） | 拡張 | knowledge_network_vision §8 未決事項3 | 未決事項 | 一部（旅カードは実装済み・提示条件は暗黙） | — | S | — |
| F-31 | 個人ネットワークの他者公開・共有 / 自動マージ・自動確定 / 全体グラフ可視化 | — | knowledge_network_vision §8 | 非スコープ（KN-1/KN-3 違反） | 棄却 | 不変条項 | — | — |
| F-32 | 構造帰属型の問い: 教員向け集約（§7 Stage 3 / §8-5） | 新機能 | structure-anchored-questions 冒頭注記 | 当時未実装 | **済** | `GET /api/admin/courses/{id}/anchor-insights`（`routes/admin.py:4604`） | — | — |

---

## 所見

1. **層をまたいで最も繰り返し現れる未着手テーマは「学習者向け表示」** — W層（E-01）・L層ライブラリ
   （E-17）・D層の疑義・晴れ間（E-27）・図の bbox（D-53）・論文層（E-11）・グラフレビュー（E-08 周辺）・
   コーパス補完（C-10 の学習者版）と、少なくとも7層が同じ理由（権限モデルと地位勾配、B層プライバシー
   規定の別途整備が要る）で同じ壁の手前に止まっている。**「教員向け成果物を学習者へ開くときの共通
   射影規約」を1本決める**のが、7つの別々の issue を1つにたたむ最短路。`learner_context_common.py`
   がその原型としてすでに存在する。

2. **次に多いのが「G層 To-Do 化」の保留** — `material.landscape_unreviewed`（B-05）/ 辺候補・gap
   （B-20）/ `material.components_unreviewed`（E-09）/ `topic.figure_gap_pending`（E-15）/
   `course.topics_unlinked`（D-30）の5本がすべて「解析直後に恒常点灯するため運用実測後」で止まって
   いる。判断は個別ではなく**「恒常点灯するルールを G層に載せる作法（抑制条件の型）」を一度決める**
   問題に見える（discuss_opening の「全却下抑止」が先例）。

3. **「非スコープ」と書かれたが後に別層として実装済み**のもの: discuss Phase 3 →
   corpus_roaming Phase B（D-02）/ ディスカバリー §7 の学習者向け3機能 → コーパス回遊層（C-01）/
   VA層の radar 着地予測（B-14 の取り消し線部分）/ 構造帰属型の問いの教員集約（F-32）/
   教材図の `svg_source` フォールバック（E-14）。**設計書側の非スコープ節が更新されていない箇所が
   残る**ので、棚卸し時は必ず CLAUDE.md とコードで裏を取る必要がある（本表の判定根拠列が実測分）。

4. **V層の保護範囲が document 側資産をカバーしていない穴**（F-01 / D-11 / D-47 / E-13）は、4つの
   設計書がそれぞれ「既知の限界」として別々に記録している同一の構造欠陥。個別対応ではなく1つの
   設計判断（版に何を含めるか）として扱うべき最有力候補。

5. **E層（Exposition Layer）だけが「層まるごと未着手」**（D-01）。2026-09-03 に再確認済みで、
   設計書 §10 の issue 分割はそのまま使える状態。規模は L だが、他の未着手項目の多く
   （学習者向け段階表示・翻訳）が E層の存在を暗黙に前提しているため、優先順位判断の主軸になる。

6. **オーナー裁定が要る（技術では決められない）論点**は主に7件: ①学習者に疑義・反実仮想を開くか
   （E-27）②手渡しチャネル＝ P3 の例外を開くか（E-22）③本人の利用実績を本人に見せるか（F-17）
   ④help_kb Phase 3 の維持/既定OFF/撤去（F-09）⑤訳語統一と出力文字列の変更（F-27）⑥claim_objects
   の永続化（A-22）⑦PN-1 の例外としての「他の学習者の橋」（D-34）。いずれも実装量は小〜中だが、
   不変条項の解釈変更を伴う。

7. **運用タスク（リスト化せず層でまとめる）** — 「docker 実機 E2E 未検証」が明記されている層:
   component_evidence（§8.4）/ discuss 開幕オーサリング（§12.6）/ 理解サイクル Phase 2（§15 残作業）/
   figure_concept_linking（残課題）/ element_context_presentation Phase 3（§10.6 残作業）/
   知識ランドスケープ / 教材図スタジオ / VA層・RE追補 / ディスカバリー各 Phase / コーパス補完。
   実質「2026-07 以降に実装した層はほぼ全部が docker E2E 未実施」で、個別課題ではなく
   **一度まとまった実機検証セッションを取る**性質のもの。同様に memory 側に多い「未コミット /
   コミット分割 / push 未」は現時点では解消しており（HEAD は d5ac3cb まで積み上がっている）、
   残っているのは **push 未**の1点に見える。

8. **文書運用上の指摘**: 「Phase N」の番号が設計書ごとに独立していて（理解サイクル Phase 3 =
   stakes_ledger、discuss Phase 3 = corpus_roaming Phase B、W層 Phase 3 = 要素粒度 embedding、
   element_context_presentation Phase 3 = 二層説明の結線）、**同じ「Phase 3」が4つの別物を指す**。
   着手判断の会話では設計書名を必ず添える必要がある（security_directive §1.1 が既にこの混同を
   1回是正している）。
