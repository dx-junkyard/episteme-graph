# 構造の降下路 設計書（Phase 3 — 足場ダイヤル・楽屋 v1）

**状態:** 実装済み（Phase 3 v1、2026-08-15。§6 精査記録・§7 実装記録）
**作成日:** 2026-08-15
**由来:** [討論記録](../architecture/ai_assistant_personalization_debate_2026-08-15.md) 提案3
「構造の降下路」v1 + 追補討論 EX-3a 裁定の小採録（宣言された留保の一行）。
実装計画は [personalization_implementation_plan.md](../architecture/personalization_implementation_plan.md)。

migration: **不要見込み**（楽屋の痕跡は `interest_traces` の新 kind — kind は TEXT・CHECK なしのため
DDL 変更不要。痕跡登録簿への登録が必須）。

---

## 1. 不変条項

- **SD1 段を引くのは常に本人** — 開示の速度は本人がダイヤルで握る。履歴・推定による自動開放・
  自動降下をしない（UC5/UC7 非抵触の要）。システムから「降りるべきだ」と誘導しない（G4）。
- **SD2 非LLM・決定論** — 梯子・降下路の生成は読み時決定論（P6）。LLM を呼ばない。
- **SD3 産出は無判定** — 1段目の想起プロンプトへの記入は任意で、書かれたかどうかすら
  判定・記録しない（UC2）。
- **SD4 楽屋は集計に入らない** — 楽屋での質問・閲覧は教員向け集約・digest・わたしの地図の
  対象にしない（既定除外）。記録は本人にだけ残る（台帳には出る）。
- **SD5 数えない** — 使用数・的中数・較正率の集計クエリを作らない（P7/UC9。ガードレールで固定）。
- **SD6 宣言された留保** — 出し惜しみが働く opt-in 枠には「いまは答えを配らない対話です」の
  宣言一行を常設する（EX-3a 裁定・今井の「宣言された留保」）。

## 2. 降下エンジン（単一の正本モジュール）

`backend/core/descent/`（FastAPI / LLM 非 import）。**別実装禁止** — 足場ダイヤル・楽屋・
（v2の）点検口はすべてこのエンジンを通る。

- 入力: 要素参照（equation / component / claim。W層 ElementRef と同じ解決規則）。
- 読み: `theory_component_graphs`（main / equation_detail 層・`THEORY_STAGES` /
  `classify_operation`）・symbol_registry artifact（記号の定義・単位・スコープ）・
  derivation_chain artifact・二層説明の generic 層。
- 出力（梯子）:
  1. **想起プロンプト**（問いの形。「この一手はどの理論段階にあたるか、まず自分の語で」型 —
     事実文を先に出さない。橋本の pretesting 修正）
  2. **stage 骨格事実文**（「理論段階では近似にあたります」型 — THEORY_STAGE_LABELS 使用）
  3. **記号の定義・スコープ・表記ゆれ**（symbol_registry 由来。定義の逐語引用・
     `SYMBOL_SCOPE_LABELS`・notation_variants。定義が無い記号は「論文中に明示的な定義が
     見つかりません」の事実文で正直に出す — §6 精査記録①）
  4. **出典リビール**（既存の出典表示に合流 — 権威は常に出典・UC2）
- 段データは全段を一度に返してよい（開示順制御はフロント。**サーバは開示履歴を記録しない**）。

## 3. 足場ダイヤル（UI）

- 式チップ・要素チップの展開内に「ヒントを一段引く」。押すたびに次の段が開く。
  産出欄（自分の語で書く欄）は既定畳み・無判定（SD3）。
- 宣言一行「いまは答えを配らない対話です」を枠の先頭に常設（SD6）。
  精読モードの Elicit にも同じ宣言一行を追加する（既存プロンプト契約フレーズは非改変）。
- 既存チップ文法に統一し新部品を作らない（白瀬の要求。ElementCard / element-vocab を使う）。

## 4. 楽屋モード

- 「楽屋へ降りる」で画面トーンが変わり、一行だけ表示:
  「ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります」。
- 降下路の最初の段は cartridge の `notation_patterns`（規約差）。以降、記号定義 → 前提概念の
  generic 説明へ降りる。「本流に戻る」で元の要素位置へ復帰。
- 楽屋からの質問は既存 learning_chat に `backstage` フラグで送り、痕跡は新 kind
  `backstage_question` で記録する。**痕跡登録簿（Phase 1）に露出宣言必須**:
  `learner_trajectory=True`（本人には見える）/ `teacher_dashboard=False` /
  `personal_map=False`（v1）。A方式（許可リスト）の全消費者からは自動除外され、
  B方式（除外リスト）の2箇所は登録簿ガードレールが追記漏れを検出する。
- tension / anchor worker の解析対象にもしない。anchor worker・digest は kind 許可リストで
  自動除外される。**tension worker は payload_flag 方式（kind 条件なし）のため自動除外されない**
  — 送信側（learning.py）で backstage のとき `tension_hint` を立てない・tension mining を
  スケジュールしない明示ガードで除外する（§6 精査記録②）。
- 集約への切替（本人が後から「以降は集計に入れてよい」と選ぶ）は「以降のみ」既定 — v1 では
  切替 UI を作らず、楽屋は常に除外（dynamic consent の実装は主権台帳 v2 と同時に判断）。

## 5. テスト

- `test_descent_core.py`（エンジン決定論・非LLM・4段の構成・stage 語彙が正本由来）
- `test_descent_guardrails.py`（core 非 FastAPI/LLM・使用数集計クエリ不在・
  `backstage_question` の登録簿宣言・誘導語彙 denylist（「降りるべき」「今すぐ」等）・
  宣言一行の逐語存在）
- `test_descent_api.py` / `test_descent_ui_static.py`

## 6. 実装前精査記録（2026-08-15）

① **段③の読み替え**: SymbolRegistry の `unit` はスキーマに存在するが決定論ビルダーが常に
None をセットし（抽出経路自体が未実装）、「典型スケール」に相当するフィールドは存在しない。
v1 の中段は「定義・スコープ・表記ゆれ」で構成する。単位・スケールの抽出は SymbolRegistry 側の
将来拡張（A層非改変のため本フェーズでは行わない）。
② **tension worker の除外方式**: `_fetch_pending_hints` は kind 条件を持たない payload_flag
方式のため、除外は送信側ガード（backstage 時に tension_hint を立てない）で実現する。
③ 語彙注意: R層の既存「点検口: 記号を確認」（descend・DB 記録あり＝集計対象）とは別物。
楽屋は記録が本人のみに閉じる私的降下であり、UI 文言・関数名を混同させない。

## 7. 実装記録（2026-08-15）

- 実装: `backend/core/descent/`（`engine.py` の `build_ladder` / `build_backstage_path` +
  純粋合成部 `compose_*`、`resolve.py` の要素→document 解決）/ `routes/descent.py`
  （GET `ladder`・`backstage-path`、受講ゲート・element_type 422・fail-closed 縮退）/
  `core/deliberation/refs.py` に `symbol_records` ヘルパ新設 / 痕跡 kind `backstage_question`
  （登録簿宣言 + `_dashboard_excluded_kinds` 追記 + learning.py の `_trace_kind` 分岐と
  tension mining 送信側ガード）/ フロントは要素文脈パネルへの mount 後差し込み
  （`.descent-frame` 足場ダイヤル・産出欄は無送信の details・`.backstage-panel` 楽屋 +
  `sendPrompt({backstage:true})`）/ 宣言一行「いまは答えを配らない対話です」を降下路枠・
  discuss Elicit・R層 renderElicit の3箇所に常設 / アンカー `material.descent-ladder`。
- テスト: `test_descent_{core,guardrails,api,ui_static}.py`（バックエンド3本は計72）。
  全スイート 9,715 passed / 0 failed（2026-08-15）。
- 実装判断: 楽屋の応答様式は通常 RAG のまま（楽屋は記録面の私有化であって応答の変更ではない）。
  楽屋の宣言文はフェッチ前にローカル定数で先出し。ladder の開示順制御はクライアントのみで
  サーバは開示履歴を一切記録しない。
- **レビュー是正（2026-08-15）**:
  1. 楽屋の notation_patterns 段は**明示 cartridge のみ**出す — `engine._notation_pattern_items`
     は `course_cartridge_id` が空のとき `load_cartridge` を呼ばず空リストを返す
     （`load_cartridge(None)` は既定カートリッジ particle_physics へ黙って縮退するため。
     G層 Phase 0 の DEFAULT_CARTRIDGE 撤去と同じ原則）。
  2. discuss モード中の楽屋質問の痕跡に `entry_mode: 'discuss'` を焼き込まない
     （`core/discuss/observation.py` は kind フィルタなしで `payload->>'entry_mode'='discuss'`
     を数えるため、焼き込むと SD4 に反して discuss 観測基盤に混入する。observation.py は非改変）。
  3. 送信側ガードの前倒し — `_is_backstage` の判定を learning_chat ハンドラ冒頭
     （EXPLAIN_GRAPH_ELEMENT typed action・atlas mind/learn の early-return 記録経路より前）
     へ移し、backstage のとき `body.action` / `body.atlas_context` を無効化して常に通常の
     楽屋質問として処理する（現行フロントは送らない組合せのサーバ側防御。非 backstage の
     挙動は不変）。
  4. 楽屋では帰属確認カード（`anchor_confirm`）を出さない — 付与条件に
     `not _is_backstage` を追加（「集計に入りません」と宣言した枠で帰属確定 UI を出さない）。
  5. 問いの軌跡の表示語 — `learning_experience._TRACE_KIND_WORD` に
     `"backstage_question": "楽屋の質問"` を追加（既定「問い」への縮退で楽屋であることが
     読めなくなるのを防ぐ）。
  - 回帰テスト: `test_descent_core.py`（cartridge_id 無しで load_cartridge 非呼び出し・
    明示 id のみ段が出る）+ `test_descent_guardrails.py`（entry_mode ガード・前倒しガードの
    位置検査・anchor_confirm ガードのソース構造固定）。

## 8. 非スコープ（v2）

- フェルミの点検口（桁出題・JOLチップ・DIFF→楽屋導線）
- 宣言された揺さぶり群（R層 distractor の discuss 持ち込み・台帳異議チップ — EX-3c 裁定分）
- ダイヤル上限の本人設定・楽屋トレイの「わたしの地図」表示粒度
- 教材の密度（提案5 v2 の学習者向け射影）からの合流導線 — Phase 4 実装後に配線
