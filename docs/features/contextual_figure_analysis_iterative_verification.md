# 画像解析を文脈仮説と反証型再スキャンの反復パイプラインに改善する

GitHub Issue: [#499](https://github.com/dx-junkyard/episteme-graph/issues/499)

## 背景

要素コンテキスト画面で、図の上位構造（Why）と下位構造（How）が十分に取得されない問題を調査した。

当初確認された直接的な要因は以下だった。

- 対象解析runで `analyze_images=false` となっており、`apparatus_semantics` が実行されていなかった
- PDF構造解析でfigure captionが欠落し、図とキャプション・節の対応が作られていないケースがあった
- `document_figures` のUUIDと、`figure_table_semantics` が生成する `fig_2.7` 形式のIDが一致せず、生成済み情報をコンテキスト画面が参照できないケースがあった

これらの構造取得・ID対応を改善し、画像解析を有効にして実行できる状態にしても、実際の解析精度は十分ではなかった。

## 現状の実装と限界

現在の画像解析は、次の情報を1回のVision LLM呼び出しへまとめて渡している。

- 図画像
- キャプション
- キャプション前後の本文
- `Fig. N` / `Figure N` の参照段落
- 同一セクション本文
- 図中ラベルと略語辞書
- 既存ライブラリ候補

周辺文章自体は `backend/core/document_pipeline/figure_context.py` で収集され、`src/episteme_graph/agents/apparatus_semantics/prompt.py` から画像と一緒に渡されている。

一方、現状は基本的に「画像と文章を同時に見て、1回で分類・構造・関係を回答する」方式である。最大2回のrepairも、主にJSON形式、参照ID、evidence quoteなどの検証エラーを修復するもので、意味仮説を反証したり、見落とした領域を再観察したりするループではない。

この方式には次の問題がある。

- 図の論文中での役割を十分に推定する前に局所要素を同定してしまう
- 最初の誤認識を後続の説明が補強し、自己整合して見える可能性がある
- 文章に書かれている要素を、画像に存在すると誤って「発見」する確証バイアスが起きる
- 全体仮説が成立するために必要な要素・関係から、見落としを逆算できない
- 不整合や未解決点が、ユーザーに確認可能な形で残らない
- 同じ形状・表現の反復を活用できていない一方、単純な形状一致を機能一致と誤認する危険もある

## 目的

画像の直接観察と周辺文章による意味仮説を区別し、両者の整合性を反復的に検証することで、根拠が追跡可能で首尾一貫した図解釈を生成する。

Vision由来の結果は引き続きcandidate止まりとし、人間のレビューなしに確定情報へ昇格させない。

## 提案する解析方式

### 1. 文脈から期待モデルを生成する

画像を見せず、キャプション、Figure参照段落、同一節の目的、定義、略語から以下を抽出する。

- 図が論文中で果たす役割
- 図全体が表す対象・処理・主張
- 期待される主要要素
- 期待される要素間関係、方向、入出力
- 図に現れる可能性が高いラベル・形状・視覚的手掛かり
- 明示されていない点
- 仮説を否定する条件

### 2. 画像を独立に観察する

期待モデルを与えない、または意味情報を最小化した状態で、画像から直接確認できる事実を抽出する。

- パネルと領域
- OCRラベル
- 箱、線、矢印、軸、凡例
- 接続方向
- 色・線種・形状の反復
- 読み取れない領域
- 意味を決められない要素

この段階では「見えるもの」と「意味の解釈」を分離する。

### 3. 期待モデルと画像観察を照合する

要素・関係ごとに次の状態を持つ。

- `supported_by_both`: 文章と画像の両方で確認
- `visual_only`: 画像にはあるが文章上の意味が未確認
- `text_only`: 文章にはあるが画像で未確認
- `contradicted`: 文章と画像が矛盾
- `unresolved`: どちらからも十分に決められない

各判断には、本文引用、画像領域、図中ラベルなどのprovenanceを付ける。

### 4. 複数の全体仮説を保持する

曖昧な場合は1つに即決せず、2〜3個の候補仮説を保持する。

各仮説について以下を記録する。

- 仮説の説明
- 支持する文章・視覚的証拠
- 反証となる証拠
- 仮説成立に必要だが未確認の条件
- 現時点のconfidence

### 5. ギャップ駆動で再スキャンする

毎回図全体を同じ指示で読み直すのではなく、`text_only`、`contradicted`、`unresolved` の項目から次の検証課題を作る。

例:

- 特定領域を拡大してOCRする
- 矢印の方向と接続先を確認する
- 凡例と色・線種の対応を確認する
- パネル間で同じ形状が同じラベル・接続・位置関係を持つか確認する
- 仮説に必要な要素が本当に存在するか、存在しないかを確認する

再スキャンには検証対象、対象領域、成立条件、反証条件を明示する。同じ入力・同じ問いを繰り返すだけのiterationは行わない。

### 6. 収束または人間への引き継ぎ

既定の最大試行回数は3回程度とし、設定可能にする。

以下を満たした場合に収束とする。

- 高重要度の矛盾がない
- 主要要素と主要関係が根拠付きで説明できる
- 前回iterationから仮説・要素・関係の差分が閾値以下
- 未解決点が明示されている

最大回数で収束しない場合も結果を破棄しない。以下を保存し、ユーザーの助けを求める。

- 不整合が残る要素・関係
- 文章側の根拠
- 画像側で確認できた事実
- 競合している仮説
- 追加確認が必要な領域
- ユーザーに答えてほしい具体的な質問
- iterationごとの変更履歴

## 類似形状・類似図の扱い

同じ形状や表現は、同じ種類・機能を持つ可能性を高めるpriorとして利用する。ただし、形状一致だけで機能一致を確定しない。

以下を別々に評価する。

- 視覚的類似度
- ラベルの一致
- 接続・位置関係の一致
- 本文上の役割の一致
- 同一文書・同一分野での使用実績

過去図や同一図内の反復要素を参照する場合も、類似例を証拠そのものではなく、再確認箇所を選ぶための手掛かりとして扱う。

## 出力モデル案

既存の `analysis_profile` に加え、少なくとも以下を保持できる構造を検討する。

- `context_hypothesis`
- `visual_observations`
- `alignment_items`
- `alternative_hypotheses`
- `verification_iterations`
- `unresolved_conflicts`
- `review_questions`
- `convergence_status`
- assertion単位の `evidence_sources` と `confidence`

最終説明では、直接観察、本文根拠、推論、未確認事項を区別して表示する。

## 受け入れ条件

- [ ] 文脈仮説生成と画像の直接観察が、独立した中間成果として保存される
- [ ] 文章にあるという理由だけで画像要素を検出済みにしない
- [ ] assertion単位で本文引用または画像領域へ根拠を追跡できる
- [ ] 不整合・未確認条件から、次の対象領域と検証質問が生成される
- [ ] 最大試行回数と収束条件が設定可能である
- [ ] 同一入力を無目的に再実行せず、各iterationに新しい検証課題がある
- [ ] 収束しない場合、競合仮説と具体的なユーザー質問を残して終了する
- [ ] LLM失敗、画像欠落、OCR失敗、コスト上限でも途中結果を失わない
- [ ] Vision由来の同定はcandidateのままで、教員レビューなしに確定しない
- [ ] 既存のguided figure re-analysisとreview UIから、未解決箇所を指定して再解析できる
- [ ] iteration回数、モデル、入力根拠、変更差分を監査可能に記録する

## 評価方法

少数でもよいので正解付きの図セットを用意し、現行one-shot方式と提案方式を比較する。

測定候補:

- 主要要素のprecision / recall
- 関係・矢印方向のprecision / recall
- 本文根拠のない断定率
- 主要要素の見落とし率
- 人間による修正量
- 未解決質問の有用性
- 収束率と平均iteration数
- 図1件あたりのコストと所要時間

以下のablationも行う。

- 周辺文章なし
- 文章と画像を最初から同時投入
- 独立観察と照合のみ
- ギャップ駆動再スキャンまで含む
- 類似図・類似形状priorあり／なし

## 主な変更候補

- `backend/core/document_pipeline/figure_context.py`
- `backend/core/document_pipeline/orchestrator.py`
- `src/episteme_graph/agents/apparatus_semantics/agent.py`
- `src/episteme_graph/agents/apparatus_semantics/prompt.py`
- `src/episteme_graph/agents/apparatus_semantics/schema.py`
- `src/episteme_graph/agents/apparatus_semantics/validator.py`
- `src/episteme_graph/agents/apparatus_semantics/repair.py`
- 図解析結果・反復履歴を保持するDB migration
- 教員レビュー／深く検討UI

## 結論

精度改善の中心は、one-shotプロンプトを強化することではない。

`文章からの期待仮説 → 独立した画像観察 → 対応・矛盾検出 → ギャップ駆動再スキャン → 収束判定／人間への引き継ぎ`

という反証可能な状態機械へ画像解析を変更する。これにより、精度だけでなく、説明の首尾一貫性、根拠の追跡可能性、未解決点の透明性を改善する。

---

## 実装記録（2026-07-18 実装決定事項）

以下は実装時に確定した設計。本節が実装の正本。

### 呼び出し構成（図1枚あたりの状態機械）

| 段階 | 画像 | 入力 | 出力 |
|---|---|---|---|
| ① hypothesis（文脈仮説） | なし | caption / nearby_text / 略語辞書 / figure_record | `ContextHypothesis`（期待要素・期待関係・視覚手掛かり・未明示点・反証条件。本文verbatim引用つき） |
| ② observation（独立観察） | あり | inner_labels のみ（**caption・nearby_text・ライブラリ候補は渡さない** — 確証バイアス遮断） | `VisualObservationSet`（panels/elements/connections/OCR/反復モチーフ/判読不能領域/`visual_mode_guess`） |
| ③ alignment（照合・統合） | **なし** | ①+②の JSON + caption / nearby_text / 略語 / inner_labels / ライブラリ候補 / cartridge / guidance | 最終レコード（既存スキーマ）+ `alignment_items` + `alternative_hypotheses`（2〜3）+ `verification_tasks` + `review_questions` |
| ④ verification（再スキャン）× N | あり | 検証課題（question/成立条件/反証条件/region_hint）+ alignment スナップショット | task findings + alignment 更新 + record deltas（追加 parts は観察根拠必須） |
| ⑤ 収束判定 | — | 決定論（Python） | `convergence_status` |

- ③に画像を渡さないことで「文章にあるから画像で発見した」ことにする経路を**構造的に**遮断する。
  parts は observation_refs / label_ref による視覚根拠が必須（validator `part_without_visual_support` = error）。
  さらに視覚根拠は**実在の observation_id / inner_labels に追跡可能**であることが必須
  （`alignment_visual_support_untraceable` = error。自由記述 `visual_evidence` 単独は根拠にならない —
  説明専用）。照合修復が尽きた場合、追跡不能な item は決定論的に降格する
  （supported_by_both + text_evidence あり → text_only / それ以外 → unresolved）。
  text_only の期待要素は alignment item として保持され parts には决して入らない。
- ④の課題は `(target_item_ids, 正規化question)` で重複排除し、既実行課題の再実行を engine が拒否する
  （新課題ゼロなら打ち切り）。iteration 記録の `executed_task_ids` 空は validator error（無目的再実行の禁止）。
  **毎 iteration のマージ直後に `_enforce_part_support()` が parts↔alignment の整合を決定論的に回復**する
  （再スキャンで item が降格されたら、LLM の `parts_to_remove` 出力に依存せず該当 part と関連
  connection を除去）。finding が返らなかった実行済み課題は `unresolved` へ強制遷移する
  （「実行済みなのに open」の状態を構造的に排除）。
- 収束条件（決定論）: severity=high の contradicted が無い、`status='open'` の task が無い
  （実行済み・未回答の課題は unresolved に遷移済みのため全 task を対象に判定）、直前 iteration での
  alignment 状態変化が 0。未解決の課題・矛盾は**収束状態にかかわらず** `review_questions` /
  `unresolved_conflicts` として必ず明示する（LLM 由来が無ければ決定論テンプレートで合成）。
- 類似形状 prior: ②の repeated_motifs → ④の検証課題生成の手掛かりに限定。プロンプトで
  「形状一致は機能一致の証拠にしない」を明記（証拠には使わない）。
- 段階失敗時の縮退（P4 情報を落とさない）: ①失敗→仮説なしで続行 / ②失敗→parts を出さず
  text_only アイテムのみで `aborted_error` / ③失敗→①②の成果物を保持した fallback record。
  すべて `stage_failures` に正直に記録。コスト枯渇は `aborted_cost_limit`（部分結果保持）。

### 語彙・スキーマ（`src/episteme_graph/agents/apparatus_semantics/schema.py`）

- `ALIGNMENT_STATUSES = (supported_by_both, visual_only, text_only, contradicted, unresolved)`
- `CONVERGENCE_STATUSES = (converged, max_iterations_reached, no_progress, aborted_error, aborted_cost_limit, not_run)`
- 新 dataclass: `ExpectedElement` / `ExpectedRelation` / `ContextHypothesis` / `ObservedElement` /
  `ObservedConnection` / `VisualObservationSet` / `AlignmentItem` / `AlternativeHypothesis` /
  `VerificationTask` / `VerificationIterationRecord` / `IterativeAnalysisRecord` / `IterativeConfig`
- `ApparatusRecord.iterative_analysis: IterativeAnalysisRecord | None`（旧 artifact からの from_dict は None 縮退）
- `IterativeAnalysisRecord` は監査要件を内包: `llm_calls` / `vision_calls` / `model` /
  `verification_iterations[].changes`（iteration ごとの変更差分）/ `stage_failures`

### エンジンと後方互換

- 状態機械は `src/episteme_graph/agents/apparatus_semantics/iterative.py` の
  `IterativeFigureAnalyzer`。`ApparatusSemanticsAgent(iterative_config=IterativeConfig(...))` で
  オプトイン。**config 未指定（enabled=False）は従来 one-shot のまま**（既存テスト・呼び出し互換）。
- 段階別 repair 上限: hypothesis/observation/verification=1、alignment=2（既存 repair パターン踏襲）。
- 決定論グラウンディング（`_attach_label_grounding` / `_attach_profile_grounding`）は反復後も最後に必ず適用。

### コスト制御

- 新設定（`backend/core/config.py`）:
  `APPARATUS_ANALYSIS_MODE`（`iterative`|`one_shot`、既定 `iterative`）/
  `APPARATUS_VERIFY_MAX_ITERATIONS`（既定 3）/
  `APPARATUS_REANALYZE_MAX_ITERATIONS`（既定 1 — 同期 API の応答時間を守る）
- `APPARATUS_MAX_CALLS_PER_DAY` の意味は「vision 呼び出し数」のまま（①③はテキストコールで対象外、
  U層では全コール計測）。orchestrator は日次残数を `IterativeConfig.vision_call_budget` として
  agent に渡し、engine が動的に消費（枯渇時は rescan を止め部分結果を保持）。事前フィルタは
  図あたり `1 + max_iterations` の保守的見積りで頭数を絞る。`stage_outputs.vision_calls` には
  実測値を記録（日次集計の正確性を維持）。

### 保存・API・UI

- migration **054**: `document_figures.iterative_analysis JSONB NOT NULL DEFAULT '{}'`
  （AI 提案層。再解析で置換・再抽出でリセット。教員確定列は追加しない — 確定は既存の
  candidate 注釈 commit 経路のみ）。`persist_suggestions` / `_save_figure` リセットに追随。
- 全記録は従来どおり `stage_outputs._artifacts.apparatus_semantics` にも保存（run 単位監査）。
- `presentation_payload` に `iterative_analysis`（投影）を追加。投影は
  `iterative_analysis_payload()` が confidence 生値を除去し `confidence_label` 段階ラベルへ変換
  （W8。deliberation 注釈の既存ラベル関数を再利用）。
- reanalyze API 拡張: `FigureReanalyzeRequest.unresolved_item_ids: list[str] | None`。
  保存済み `iterative_analysis` の alignment item / review question の id を指定すると、
  該当項目の question / region から hint_text・focus_bbox を決定論合成して既存 guided 経路に
  乗せる（未知 id は 422）。コストゲートは日次残数を `IterativeConfig.vision_call_budget`
  （= 計上済みの1回 + 残数）としてエンジンに渡し、完了後に実測 `vision_calls` を日次カウンタへ
  事後計上する（`CostGate.daily_remaining` / `count_extra_daily`。修復・検証を含む全 vision
  コールが `APPARATUS_MAX_CALLS_PER_DAY` の対象になる）。session 上限（図×教員あたり3回）は不変。
- UI（`deliberation.js`）: 図ワークスペースに照合サマリー（区分別 alignment 表示: 両方で確認 /
  画像のみ / 文章のみ・未確認 / 矛盾 / 未解決）、収束ステータス、レビュー質問カード
  （「この箇所を再解析」→ `unresolved_item_ids` 付き reanalyze）、iteration 履歴の折り畳み。
  生 confidence 非表示・ES5・candidate-only 導線は既存規約のまま。

### v1 の既知の限界（非スコープ）

- バッチ経路の再スキャンは全体画像+テキスト領域ヒント（focus crop は guided 経路の既存機構のみ）
- 正解付き図セットでの定量評価ハーネス（測定・ablation は本書「評価方法」に従い別途）
- 類似図（過去図・他文書）横断の prior（同一図内の反復モチーフのみ）
