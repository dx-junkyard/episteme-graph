# 機能整備提案 — 大枠の機能として整備すべき箇所（2026-08-13）

**位置づけ:** ドキュメント再整理タスク（[vision.md](../vision.md) 新設・
[不具合報告](doc_review_findings_2026-08-13.md)）の過程で、読解班・突合班が発見した
「機能の重複・境界の曖昧さ・統合した方がよい大枠」を統合した提案書。
**本書は提案であり、着手時は通常どおり専用設計書を切ること**（討論 → 設計書 → 実装 → 実装記録の型）。

優先度の見取り図:

| 優先 | 提案 | 理由 |
|---|---|---|
| ★★★ | §2-1 candidate→confirm 共通プリミティブ / §3 機械ガードレール / §1 ドキュメント運用 | 再実装が6系統以上に達し増殖中 / 今回の不具合30件の根本原因を構造的に断つ |
| ★★ | §2-2 段階ラベル正本化 / §2-5 要素文脈API統合 / §2-9 LLMステージ判定の単一正本化 | 二重管理が既に実バグ・食い違いを生んでいる |
| ★ | §2-3〜2-4 UI統合面 / §2-7〜2-8 図・レビューキュー / §2-10〜2-13 | 増殖の予兆段階。次の追加が来る前に方針だけ決めておく |

---

## §1 ドキュメント運用の整備（即効・低コスト）

> **実施記録（2026-08-14）:** 1-1〜1-5（+ §3-6 のカウント記法）を
> [開発運用チェックリスト](../development_checklist.md) §5
> 「ドキュメント運用規約」として明文化した（機能解説の同時更新表 / 状態ヘッダ 5 語彙 /
> レビュー文書への解消注記 / 想定 migration 番号の禁止 / リポジトリ外正本の禁止 / カウント記法）。
> 機械検証は §3 の `backend/tests/test_docs_registry_guardrails.py` が担う。

### 1-1. 「機能解説ドキュメント」の同時更新を3点セットに組み込む

設計書（`*_design.md`）は「実装後に §実装記録を追記」する運用が徹底されている一方、
**機能解説・索引系**（`features/learning.md` / `backend/rag-chat.md` / `backend/api.md` /
`pipeline/overview.md` / `pipeline/agents.md` / `frontend/overview.md`）には更新規約が無く、
discuss モードのような大型機能が丸ごと欠落した（不具合報告 §4）。

**提案:** CLAUDE.md の「管理UI 3点セット」（マニュアル節 + ADMIN_UI_ANCHORS + data-ui-anchor）と
同様に、**「学習者向け機能の追加 = learning.md/rag-chat.md への節追加」「パイプラインステージの
追加 = pipeline/overview.md 表 + agents.md 節の追加」「ルーター追加 = api.md への追加」** を
`development_checklist.md` に明文化する。§3-1 の網羅テストで機械的に守る。

### 1-2. 設計書ライフサイクルの明示（凍結 vs 生きたリファレンス）

`docs/features/` には「実装後は歴史記録として凍結される設計書（大半）」と「実装に追随して
更新される生きたリファレンス（admin.md / auth-visibility.md）」が区別なく混在しており、
ステータスヘッダの更新漏れ（不具合報告 §3）の温床になっている。

**提案:** 各文書の冒頭に `**状態:**` 行を必須化し、語彙を
`設計中 / 実装済み（正本・凍結） / 生きたリファレンス / 提案（実装対象外） / 調査記録（完了）`
に統一する。調査記録には「本調査以降の変更は未評価」の定型文を含める。

### 1-3. レビュー・調査文書の解決追記規約

`*_review.md`・issue レビューが解決後も「未修正」の体裁で残り、読者が既知バグと誤認する
（不具合報告 §5、今回3文書に解消注記を追加済み）。

**提案:** レビュー指摘が解消されたら**レビュー文書側に解消注記を追記する**（設計書側 changelog
だけに書かない）。あるいは W層方式（レビュー指摘は設計書内に統合）にプロジェクトとして統一する。

### 1-4. migration 番号は実装後にのみ記載

設計書に「migration 0NN 想定」を書く運用が、E層の 034 衝突・W-β の 046→048 ずれ・
G層/状態通知の相互矛盾する因果説明を生んだ（一次情報は常に `backend/db/0NN_*.sql` の実ファイル名）。

**提案:** 設計書には想定番号を書かず「次の空き番号（実装時に採番）」とだけ書く。
空き番号の案内は layer_registry から自動導出（max+1）にする。

### 1-5. リポジトリ外正本の禁止

`field_atlas_overlay_spec.md`（8文書が§番号付きで参照する「正本」）が一度もコミットされて
いなかった（不具合報告 §1-1）。

**提案:** 「正本」と呼ばれる文書は必ず `docs/` 配下にコミットされていることを条件とする。
§3-3 のリンク検査（バッククォート参照含む）で機械的に検出する。

---

## §2 機能の大枠統合（アーキテクチャ提案）

### 2-1. candidate → confirm ワークフローの共通プリミティブ化 ★★★

> **実施記録（2026-08-14）:** `backend/core/candidate_flow.py`（`CandidateVocabulary` /
> `CandidateFlow` / `select_supersedable`）+ 正本設計書 `docs/features/candidate_flow_design.md`
> + ガードレールテストを追加。語彙・SQL・トリガはドメイン側に残す方針も明文化した
> （CLAUDE.md 横断基盤節 + layer_registry 横断基盤行）。
> **既存8系統の巻き取りは提案どおり非実施**（次の新系統からアダプタ接続を義務化）。

「非LLM prefilter → 非同期 LLM 候補 → 人間 confirm/dismiss → 状態遷移で保持・監査記帳」という
同型パイプラインが、少なくとも **8系統**で個別に再実装されている:
tension / structure_anchor / D層 scope_candidates / assumption_nodes / W層 element_annotations /
C層 explanations / ランドスケープ placements / カテゴリギャップ decisions
（さらに SL層 反証条件・修正報告・図スタジオ提案も同型）。

LLM 呼び出し側は `core/llm_worker/` に共通化済みだが、**確定側のワークフロー**
（status 語彙 candidate/confirmed/dismissed/superseded・却下理由・監査 entity_type・
再解析時の supersede セマンティクス・k-匿名集約）は各層がコピーしている。

**提案:** `core/revision_store.py`（draft/freeze の共通制御フロー）と同じ流儀で、
`core/candidate_flow.py` のような**制御フローだけの共通プリミティブ**を切り出す
（語彙・粒度・トリガはドメイン側に残す）。新系統はコピペせずアダプタで接続する —
CLAUDE.md 横断基盤ルールへの追記が本体。既存8系統の巻き取りは急がず、
**次の新系統から適用**が現実的。

### 2-2. 段階ラベル辞書の正本化 ★★

「数値を見せない」原則の実装として、生値→日本語段階ラベル（低/中/高、レンジ 3-5/6-10/11+ 等）の
変換表が D層・SL層・G層・R層などで**サーバ側とフロント側に二重管理**されている
（SL層設計書 §14 自身が「二重ラベル表の一本化は別 issue」と明記）。

**提案:** `core/privacy.py`（k=3 正本）と同じ発想で、段階ラベル変換の正本モジュール
（例: `core/label_vocab.py`）+ フロントへは API レスポンスで**ラベル済み文字列を返す**方針に
統一する（フロント側変換表を廃止する方向）。

### 2-3. 学習者の「振り返りハブ」統合 ★

「今日の理解を振り返る」体験が、discuss 着地画面・tension digest・anchor digest・
personal-map-home 振り返りタブ・理解サイクルの LEAVE/REVISIT と**機能別に別 UI/API** で
実装されている。学習者からは同種の体験に見える。

**提案:** 新機能の追加はいったん止め、**着地（LEAVE）と再訪（OPEN/REVISIT）を単一の
「振り返りハブ」として UI 統合する**設計検討を切る。理解サイクル設計書が既に
「着地・開幕という一等地を三機能が奪い合う UI 渋滞の解消」を目的に据えており、その延長。

### 2-4. ガイダンス3入口の統合面 ★

「機能の説明・道案内・次の一歩」という同じ関心が、🤖 Admin Copilot / 📋 G層バッジ /
？ help_kb インスペクトの**3つの別入口**で提供されている（capability registry は共通基盤化済み、
UI 面は未統合）。ヘッダー UI 要素（🔔📋🤖？+ cue pulse）も個別増殖しており、
**合計の密度を誰も見ていない**。

**提案:** ヘッダー情報アーキテクチャの一度の棚卸し + 3入口の役割境界の明文化
（どれが说明・どれが誘導・どれが実行か）。統合そのものより先に「これ以上入口を増やさない」
ルールを置く。

### 2-5. 要素文脈 API の2系統統合 ★★

学習者向け要素文脈が component 用（`/components/{id}/context`、instance/shared_part/graph 構造）と
claim・equation 用（`/elements/{type}/{id}/context`、focus/upper/lower 構造）の **2系統の DTO・
実装**（`core/component_context.py` / `core/element_context.py`）に分かれ、フィルタ実装が重複。
表示規則は `element_context_presentation_redesign.md` の4区画モデルで既に統一済みなので、
**バックエンドだけが2系統のまま**になっている。

**提案:** element_context 側（W層 context_lens の射影）に component も合流させ、
component_context.py は互換ラッパー化 → 廃止の2段階で一本化する。

### 2-6. 「深く検討」入口の共通配線 ★

W層モーダルへの入口が図モーダル・revisions・原稿スタジオ（チャンク/claim/component）・
要素インベントリ・ライブラリ詳細など**6箇所以上に分散実装**。`element-card.js` の
`onDeliberate` フックが定義済みなのに配線は一部のみ。

**提案:** 「要素を表示する画面は ElementCard を使い、deliberation 入口は onDeliberate 経由」を
規約化（`admin-ux-unified-parts` の完成形）。独自ボタン実装を静的テストで検出する。

### 2-7. 図機能の統合リファレンス ★

図関連は5設計書（image_pipeline 本体 + iterative verification + guided reanalysis +
figure_concept_linking + teaching_figure_studio）に分散し、`document_figures` の現行スキーマ
全体像・`POST .../figures/{fid}/reanalyze` の完全なリクエスト形を追うには5ファイルを跨ぐ。
`figure_kind`（図スタジオの生成意図）と `FIGURE_MODES`（抽出図の提示モード）という
近接語彙も対比表が無い。

**提案:** 図の**現行リファレンス1枚**（スキーマ全体像・reanalyze API 完全形・語彙対比表・
5設計書への索引）を `docs/pipeline/` か `docs/features/` に新設する。設計書自体は凍結のまま。

### 2-8. 教員レビューキューの一般化 ★

分野の地図タブに骨格レビュー・修正報告・カテゴリギャップ候補の3キューが同居し、
説明レビューキュー・R層 item 健全性キュー・ランドスケープ確認と合わせると
**レビューキューが7種以上**。それぞれが一覧・却下理由・監査・フィルタを再実装している。

**提案:** Admin Copilot capability registry と同じ抽象化（レビュー対象タイプ・アクション・
監査ラベルのレジストリ化）を検討する。§2-1 の確定プリミティブとセットで効く。

### 2-9. 「LLM を呼ぶステージか」の単一正本化 ★★

component_graph が LLM を併用するのに orchestrator コメント・`LLM_STAGE_NAMES`・
`llm_usage` の pipeline:* 語彙・`report_start(unit="llm_call")` の**3〜4箇所で扱いが食い違う**
（不具合報告 §7-1）。M層のステージ別モデル選択もこの集合に依存している。

**提案:** `PipelineStageDef` にステージ定義として `llm: bool`（または `llm_kind`）を持たせ、
LLM_STAGE_NAMES・usage 語彙・report unit をそこから導出。3者の一致をテストで固定する。

### 2-10. モード語彙の enum 一元化 ★

`intent_mode`（on_path/explore/casual/discuss）・`cycle_mode` 等が自由文字列のまま増えており、
許容値と説明の単一の真実源が無い。

**提案:** `core/schema.py` に Literal/Enum + 説明の一覧を置き、schemas.py の検証と
ドキュメントをそこから参照する。

### 2-11. domain_key と cartridge_id の分離 ★

ランドスケープの「骨格専用ドメイン」（`backend/atlas_domains/` — カートリッジ無し）の導入で、
atlas_binding_lifecycle_design.md §7 が「将来課題」とした両者の分離の必要性が実質化した。

**提案:** 地図専用ドメインがもう1つ増える前に、`atlas_domain_key` を cartridge_id から
独立させる設計検討に着手する。

### 2-12. CostGate の DB 化検討の集約 ★

CostGate は in-memory・プロセスローカルという制約を複数設計書が「非スコープ」として
繰り返し言及している。マルチワーカー化の際に一斉に破綻する類の負債。

**提案:** 散在する言及を1つの issue（検討文書）に集約し、判断（許容し続ける/DB化する）を
一度だけ下す。

### 2-13. E層（Exposition Layer）着手前の整備 ★

唯一の未実装層。設計書は (a) migration 034 の自己矛盾（DDL 本文に旧番号が残存）、
(b) 実装後に整備された横断基盤（llm_worker アダプタ・privacy.py・course_data.py・M層 scene・
U層 usage_context・G層 capability・help_kb）との接続点が未記載、の2点で
**そのまま着手すると事故る**状態。

**提案:** 着手時は「migration 068 以降へ再採番 + 横断基盤接続の追補節」を先に書く。
それまでは索引で「未実装・着手時に要追補」と明示する（layer_registry / README は対応済み）。

---

## §3 機械ガードレール（docs ⇄ 実在物の網羅テスト）

> **実施記録（2026-08-14）:** `backend/tests/test_docs_registry_guardrails.py` に **7系統・10テスト**を
> 追加: ①migration ファイル名 ⇄ data-model 表 ②migration 番号 ⇄ layer_registry §3（「013〜015, 017」の
> 範囲表記を展開）③空き番号案内 = max+1 の整合 ④ルーター ⇄ api.md（`<name>.py` 形の出現を要求）
> ⑤パイプラインステージ ⇄ overview.md ⑥設計書 ⇄ 索引の孤児検出 ⑦相対リンク実在・バッククォート
> `.md` 参照実在・状態ヘッダ（ラベル行 + レガシー凍結 allowlist）。実装が提案本文と異なる点2つ:
> **§3-6 のカウント記法は機械検査せず** development_checklist.md §5-6 の規約に留めた（全文の数値
> 表現の機械判定は誤検知が多い）/ **空き番号の手書き案内は廃止せず** max+1 整合テストで固定した（③）。
> 併せて help_kb validator に `docs/manual` 内リンク実在検査を追加（6-1 の番号ズレの再発防止。
> テキスト辞書ベースのため files 起動時検証と DB draft の freeze ゲートの両方で効く。なお
> validate_manual 違反時は設計方針どおりベクトル補助層の再同期も fail-closed で停止する —
> 検査を1つ増やした分、停止条件も広がる）。運用側の規約は §1 → development_checklist.md §5。

今回の不具合30件の大半は「テストが固定していない集計値・一覧」で起きた
（テスト固定済みのアンカー260件はズレていなかった）。既存ガードレールテスト様式の再利用で塞げる。

1. **migration 網羅:** `backend/db/*.sql` の全番号が `data-model.md` の表と
   `layer_registry.md` §3 に現れることをテストで固定（空き番号の手書き案内は max+1 整合テストで固定）。
2. **ルーター網羅:** `backend/api/routes/*.py` の全ルーターが `api.md` に現れること。
3. **ステージ網羅:** `orchestrator.PIPELINE_STAGES` の全ステージが `pipeline/overview.md` の
   表に現れること。
4. **設計書 ⇄ 索引:** `docs/features/*_design.md` の各ファイルが layer_registry か
   docs/README.md の索引から参照されていること（孤児ドキュメントの構造的防止）。
5. **リンク検査の常設化:** 今回の点検で使った相対リンク実在検査をテスト化
   （docs/manual は help_kb validator にリンク存在チェックを追加 — 6-1 の番号ズレは
   validator の検査範囲外だった）。
6. **カウント記法:** ドキュメント内の「N件」「N本」は (a) テスト固定値を参照するか
   (b) 「2026-08-13 時点 N」と時点を添える、のどちらかに統一（CLAUDE.md 内で
   228/244/248/255 の4値が併存した事故の再発防止）。

---

## §4 実施順の推奨

1. ✅ **実施済み（2026-08-14）:** §1 の運用規約を
   [development_checklist.md](../development_checklist.md) §5 に明文化 /
   索引の更新（vision.md・README・layer_registry）
2. ✅ **実施済み（2026-08-14）:** §3 の網羅テスト（`test_docs_registry_guardrails.py` +
   help_kb validator のリンク検査）
3. ✅ **基盤のみ実施済み（2026-08-14）:** §2-1 candidate_flow（`core/candidate_flow.py` +
   設計書 + テスト。既存8系統の巻き取りは非スコープ、次の新系統からアダプタ接続を義務化）。
   **§2-2 label_vocab は未着手** — 次の新機能から適用する
4. **設計検討を切る（未着手）:** §2-3 振り返りハブ / §2-5 文脈API統合 / §2-9 LLMステージ正本化
5. **将来の増設前に判断（未着手）:** §2-4 入口凍結 / §2-11 domain_key 分離 / §2-12 CostGate /
   §2-13 E層
