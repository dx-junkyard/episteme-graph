# discuss 開幕画面が伝えるべきことを揃える（投影の是正 → 不足分の生成 → 教員添削）

- 状態: **実装済み（Phase 0 / 0b / 1 / 2 / 3、2026-07-30）**。2026-07-26 起票 → 同日 全面改訂
  （下記「改訂の経緯」）→ 2026-07-30 全 Phase 実装（§12 実装記録。migration は 061 ではなく **062**）。
- 親文書: `discussion_mode_design.md`（DM1〜DM8。特に §3.3 開幕・§9.5 可読性改修）
- 直接の前例: `hierarchical_context_explanation_design.md`（二層説明 + 説明レビューキュー）
- 関連: `element_deliberation_workspace_design.md`（W層 candidate→commit）、
  `guidance_layer_design.md`（G層 To-Do）、`image_pipeline_knowledge_library_design.md` §15
  （#496: AI 候補列と教員確定列の分離）

## 改訂の経緯（初版からの方針転換）

初版は「開幕素材を AI 生成して教員が添削する」ことを主題にしていた。オーナーとの討議で
2点が確定し、主題を入れ替えた。

1. **「話す理由は投影では作れない」は言い過ぎだった。** 必要な素材のうち3つは
   thesis artifact に**既に生成済みで、投影されていないだけ**である（§3）。生成が本当に
   必要なのは1つだけ。したがって本設計の主軸は生成ではなく**投影の是正**である。
2. **情報を選別しない。** 「学習者に出せる情報／出せない情報」という区分は設けない。
   出所（何についての言明か）と裏付けの強さをラベルで示して**全部出す**。既存の設計
   （P4 情報を落とさない・`review_reasons` を必ず付ける・`source_backing_status` の4値）も
   もともとこの流儀であり、初版の「一部は教員だけに見せる」案は撤回した。

---

## 0. 問題

discuss モードの開幕画面（`core/discuss/opening.py` → `discuss.js`）には3種類の別の欠陥がある。
2026-07-26 の改修（`discussion_mode_design.md` §9.5）で直したのは3番目だけである。

| # | 欠陥 | 実際に画面に出ているもの | 直し方 |
|---|---|---|---|
| 1 | **主語の混在** | 「最も脆い一手」に、論文についての言明（「未検証の前提です」）と**システムについての言明**（「レビュー待ちの箇所です: 裏付けとなる atomic claim が見つかっていません」）が混ざって並ぶ | 見出しを分け、内部用語を平易にする。**非LLM** |
| 2 | **投影の欠落** | 中心命題として claim の生テキスト（論文原文・英語）。問いは無い | artifact にある `central_question` / `central_thesis.text` 等を投影する。**非LLM** |
| 3 | 見せ方 | 行動導線が最下部、命題が長文ボタン、stage 名が英語 | 済（§9.5） |
| 4 | **議論の火種が無い** | 反応できる対象（立場を求める問い・著者の選択）がどこにも存在しない | 生成が必要。**唯一 LLM を要する部分** |

1 は誤りである（論文の弱点ではないものを論文の弱点として読ませている）。2 は保存済みの
情報を使っていないだけである。4 だけが「誰も書いていない」ので新規生成を要する。

### 0.1 生成部分は新しい仕組みではない

「AI が候補を作る → 人間が確定する → 確定済みだけが学習者に届く」は既に反復実装されている
（二層説明 `element_explanations` / C層 `component_explanations` / W層 `element_annotations` /
D層の前提候補 / B層 tension・structure_anchor / R層の出題 item）。§5 はこの既存機構の
適用範囲を document 単位の散文へ広げるだけで、新機構を足さない。

---

## 1. 不変条項（OA1〜OA8）

DM1〜DM8 / E1〜E8（二層説明）を継承したうえで、本件固有に課す。

- **OA1 A層非改変**: `src/episteme_graph/agents/` の既存 agent は読むだけ。新ステージの追加は
  `_PIPELINE_STEPS` への登録のみ（Tier 3-19 方式）。
- **OA2 candidate-only**: LLM 出力は常に `status='candidate'`。教員の明示操作なしに学習者へ
  露出しない。一括承認でも起点は必ず人間のボタン押下（E2 継承）。
- **OA3 同期パスに LLM を入れない**: 生成は解析パイプライン（非同期）。
  `GET /discuss/opening` は従来どおり **LLM 0 回**の読み出しのみ（DM8 不変）。
- **OA4 未承認でも画面を殺さない**: 承認済み素材が無ければ投影のまま **fail-soft**。
  「承認されるまで開幕画面が出ない」設計にはしない（運用が止まり、OA2 が形骸化する）。
- **OA5 情報を落とさない**: 却下は `dismissed`、再解析は `superseded` の状態遷移で保持。
  行削除 API を作らない（P4）。
- **OA6 数値を見せない**: confidence 生値は API に出さない。段階ラベルのみ（DM6 / E6 継承）。
- **OA7 選別せずラベルで区別する**（改訂で差し替え）: 生成・抽出した情報を「学習者向け／
  教員向け」で選別しない。**何についての言明か**を見出しで分け、**裏付けの強さ**をラベルで
  添えて出す。唯一の硬い制約は「論文の内容として提示するものは論文まで辿れること」で、
  辿れないものは出さないのではなく**そう表示する**（DM1 の実装形）。
  ただし confidence の生数値（DM6）と学習者個人の痕跡（P3 k-匿名）は対象外。
- **OA8 教員の負担に上限を置く**: 1 document あたりの生成件数に上限を設け、一括承認を
  前提とする（二層説明 §13.0 の教訓）。

---

## 2. 画面に出すもの（主語で分ける）

見出しは主語ごとに固定する。混ぜない。

| 見出し | 主語 | 内容 | 出所 |
|---|---|---|---|
| この論文が答えようとした問い | 論文 | 問い + それまで何が分かっていなかったか | `central_question` / `paper_goal`（**投影**） |
| この論文の主張 | 論文 | 中心命題（合成文）+ 中心となる式 | `central_thesis.text`（**投影**） |
| この論文が確かめていないこと | 論文 | 未検証の前提 | D層台帳（**投影**・現行あり） |
| まだ確認できていないところ | **システム** | 解析が裏付けを取れていない箇所 | `review_required` ノード（**投影**・現行は主語を偽って混在） |
| 別の見方（AI の提示） | **AI の推測** | 中心命題の別の定式化 | `alternative_theses`（**投影** + 出所ラベル） |
| 議論のきっかけ | 論文 | 立場を求める問い / 著者の選択を問いに変えたもの | **生成が必要**（§5） |
| このコースで議論したいこと | 教員 | 任意入力 | `learning_courses.data`（AI 生成なし） |

- 「別の見方」は `alternative_theses` を使う。この artifact は `text` / `reason` /
  `confidence` のみで **出典（claim_ids / evidence_block_ids）を持たない**ため、
  「AI が提示した別の定式化（出典との対応は未確認）」と明示する。教員が承認したものは
  署名付き（担当教員が確認した説明）に昇格する。
- 「まだ確認できていないところ」の本文は内部用語を平易化する（`_REVIEW_REASON_FACT_PHRASES`
  の言い換え。例: 「裏付けとなる atomic claim が見つかっていません」→「根拠となる文をまだ
  特定できていません」）。
- 開幕画面に一度に並べるのは少数（問い・主張・きっかけ）。残りは同じ画面の
  「くわしく見る」から到達できるようにする。**この画面に出ない情報がどこにも無い状態は
  作らない**（OA7）。

---

## 3. Phase 0 — 投影の是正（非LLM・単独出荷可・本設計の主軸）

`project_thesis` が artifact の以下を捨てている。まずこれを投影する。

- `central_question` / `paper_goal` — 問いから始める。export のバリデーションゲートは
  `central_question` 不在を error 扱いにしているのに、学習者向け画面では使っていない。
- `central_thesis.text` / `support_structure[].text` — エージェントが合成した命題文。
  現在は捨てられ、`claim_ids` → claim の生ラベル（論文原文）が表示されている。
- `alternative_theses` — 出所ラベル付きで出す（§2）。

あわせて `project_fragile_points` の**主語の混在を解消**する。現在は D層台帳由来
（`kind='assumption'`）と backbone 由来（`kind='backbone_node'`）を同じ「最も脆い一手」に
積んでおり、`discuss.js` は `fact_line` を並べるだけなので画面上で区別が消える。
`kind` は既に付いているので、**フロントで2区画に分けるだけで直る**。

**注意**: これらは A層が生成した英語テキストである可能性が高い（thesis_reconstruction の
プロンプトに言語指定が無い）。Phase 0 で直るのは**構成と主語**であって、**言語ではない**。
実装時に混同しない。

---

## 4. Phase 1 — 生成が必要な唯一の素材（議論のきっかけ）

### 4.1 新ステージ `discuss_opening`

- 実装場所: `src/episteme_graph/agents/discuss_opening/`（標準 agent 構成）。
- 登録: `_PIPELINE_STEPS` の **`contextual_explanation` の後・`course_mapping` の前**。
  thesis / graph / derivation / narrative / 図解析が揃っている。
- 入力（**不透明 ID を渡さず解決済みテキストに展開**する。二層説明 §5.1 の規約）:
  D層の未検証前提 + derivation の operation 列（`linearize_*` / `eliminate_*` =
  著者が選んだ手）+ thesis の合成文。
- 出力: `discussion_seed`（立場を求める問い）を 2〜3 件、各件に
  `body` / `evidence_quote` / `reason` / `confidence`。
- LLM は **1 document = 1 コール**。`core/llm_worker/`（BaseJSONLLMClient + run_with_repair +
  CostGate）への 15〜20 行アダプタで接続する（8 系統目。コピペ禁止）。
- 縮退（P4）: 未検証前提も operation も無い document は**生成しない**
  （`skipped_reason` を `stage_outputs` に正直に記録）。根拠の無い火種を創作させない。
- 言語: 学習者向け散文なので日本語。`DISCUSS_OPENING_LANGUAGE`（既定 `ja`）1つで決める
  （`lecture_language` は使えない — 同じ論文が言語設定の違うコースに載りうる）。
- コスト: `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT`（既定 4）/
  `DISCUSS_OPENING_MAX_CALLS_PER_DAY`（既定 20）/ `DISCUSS_OPENING_LLM_MODEL`（fast tier 既定）。

### 4.2 中心命題の日本語要約（任意・後続）

Phase 0 でも中心命題は英語のままである。日本語化するなら同じステージで
`thesis_restatement`（平易な言い直し）を生成し、**原文と併記**する（置き換えない）。
必要性は Phase 0 の画面を見てから判断する。**Phase 1 に含めるかは着手時の判断**であり、
含めない場合は §5 の `role` CHECK から `'thesis_restatement'` を落としてよい
（格納庫側だけ先に許可しても害はないが、使わない語彙を残さない方が良い）。

---

## 5. 格納庫（migration 061 想定）

**`element_explanations`（migration 056）を再利用する。** 新テーブルを作らない。

開幕素材は「document スコープ・AI 生成・学習者向け散文・candidate→approved・再解析で
superseded・権限は document editable・レビューは同じキュー」で既存台帳と性質が一致する。
違いは係留先が要素ではなく document 全体という一点だけである。別テーブルにすると
レビュー UI・一括承認 API・監査語彙・権限ゲートを二重に持つことになる。教員から見ても
「AI が書いた学習者向け文章のレビューは1箇所」であるほうがよい。

migration 061 で加える変更（すべて冪等・`DO $$` ガード。CHECK 差し替えは migration 041 の
`component_type` 拡張が先例）:

1. `element_type` の CHECK に **`'document'`** を追加（`element_id` は `document_id` と同値）。
2. **`role TEXT`** 列を追加（NULL 許容。既存行は NULL＝二層説明の説明本文）。
   CHECK は `role IS NULL OR role IN ('discussion_seed','thesis_restatement')`
   （投影で足りる role は生成しないので格納庫にも入らない）。
3. `kind` は既存 CHECK のまま `contextual` を使う（語彙を汚さない）。

---

## 6. レビュー導線

### 6.1 既存キューに相乗りする

`GET /documents/{id}/element-explanations?status=candidate` と
`POST /documents/{id}/element-explanations/bulk-review`（二層説明 §13.1、実装済み:
`routes/element_explanations.py:188`）を**そのまま使う**。新 API を作らない。`role` を
レスポンスに含めるだけで済む。

UI（`deliberation.js` の説明レビューキュー）の変更:

- `element_type='document'` の候補を**先頭に独立グループ**「この論文の議論のきっかけ」として
  表示する（要素グループより上）。
- このグループのカードは「深く検討」モーダルを持たない（係留先の要素が無い）。
  代わりに本文のインライン編集 + 承認 / 却下。編集は既存 PATCH（旧行 `superseded` →
  `created_by=user_id` の新行 INSERT）に乗せる。教員が直した時点で書き手は教員になる。

### 6.2 コース作成時に気づける導線（G層）

生成は document 単位だが、添削の動機はコースを作るときに生まれる。

- ルール `course.discuss_opening_unreviewed`（severity: **recommended**、capability
  `course.discuss_opening_review` を `KIND_GUIDANCE_ONLY` で新設）
- 点灯: 自分が所有するコースのソース document に `element_type='document'` かつ
  `status='candidate'` の行がある。
- 消滅: 全件が approved / dismissed（G1: 完了フラグを持たない）。
- **過去に全却下された document は再点灯させない**（却下履歴から導出。再解析のたびに同じ
  判断を求めるのは G4 に反する。新しい列は作らない）。
- 事実文: 「このコースのソース論文に、未確認の議論のきっかけがあります。」

---

## 7. 配信

`build_opening` が承認済み素材を読み、role 単位で差し替える。

- **role 単位の部分適用**。「全部揃うまで出さない」にしない（OA4）。
- 承認済みが1件も無い document は投影のまま（＝Phase 0 の状態）。劣化しない。
- **出所表示**: 承認済み素材を含む区画に一行 —
  「この説明は、論文の解析結果をもとに担当教員が確認したものです。」
  投影のみの区画には署名を付けない。
- DTO は既存契約に `authored` / `authored_by_label` を足すだけ。confidence 等の生値は
  従来どおり `_strip_numeric_keys` で除去（OA6）。
- `available` の判定は変えない。

### 7.1 鮮度（再解析との関係）

#496 の規約（migration 053）を適用する。再解析で `candidate` は `superseded`、
`approved` は**触らない**。生成時に `evidence.source_fingerprint`
（`central_question` + `central_thesis.text` + claim id 集合の決定論 sha256）を保存し、
再解析後に不一致ならレビューキューに事実文で並べる（「元の解析結果が変わっています」）。
**自動で非承認に落とさない** — 学習者に出ていたものが黙って消えるほうが有害である。

### 7.2 V層 freeze（既知の限界として明記）

コース発行時のスナップショットは**コース側の資産のみ**で、開幕素材は document 側の資産
なので入らない。したがって共有先の教員の画面では、コース本文が版で固定されていても
開幕素材は所有者の編集で変わる。

これは開幕素材固有の課題ではなく、**二層説明の説明文も同じ**である（V層の保護範囲が
document 側資産をカバーしていないという既存の構造的な穴）。開幕素材だけ特別扱いすると
かえって分かりにくくなるため、**v1 では同梱せず既知の限界として記録する**。V層の範囲拡張は
説明文も含めて別途判断する。

---

## 8. ガードレール（`backend/tests/`、`guardrail_helpers` 使用）

- Phase 0（新規 `test_discuss_opening_projection.py` 相当）:
  - `central_question` / `central_thesis.text` / `alternative_theses` が DTO に出る
  - fragile points が `kind` ごとに分離して返る / フロントが2区画に描画する
  - `alternative_theses` 由来の項目に出所ラベルが付く（無署名で論文の主張として出ない）
- Phase 1（新規 `test_discuss_opening_authoring_guardrails.py`）:
  - 生成モジュールが FastAPI を import しない
  - `build_opening` の経路に LLM 呼び出しが無い（OA3）
  - 承認済み以外（candidate / dismissed / superseded）が DTO に混ざらない（OA2）
  - 承認済みゼロで Phase 0 の DTO と一致する（OA4 の回帰検知）
  - 行削除 API が無い（OA5）／ confidence 生値が出ない（OA6）
  - 禁止語彙（D層と同じ denylist）を validator が弾く
  - 生成上限と truncated の正直な記録（OA8）
- 既存 `test_next_steps_guardrails.py` に新ルール（capability 存在・fail-closed・
  全却下後の非点灯）を追加。

---

## 9. 非スコープ（v1）

- 学習者ごとの個人化（「前回ここで止まりました」・わたしの地図の兄弟ノード提示）。
- 他論文との比較の学習者開放（W層 positioning のコーパス横断レンズは教員限定のまま）。
- 多言語（`DISCUSS_OPENING_LANGUAGE` 1つで固定）。
- 開幕画面の A/B 実測（既存 `discuss_metric_events` の `opening_*` で足りる）。
- `course_focus`（§2 最下段）の AI 生成 — 教員の任意入力のみ。

---

## 10. 実装順序

| Phase | 内容 | migration | 規模 | 判断 |
|---|---|---|---|---|
| **0** | 投影の是正（問い・命題文・別の見方の投影 + 主語の分離 + 内部用語の平易化） | 不要 | 小 | **先に単独で出す** |
| 0b | `course_focus`（§2 最下段「このコースで議論したいこと」）の教員入力欄と開幕画面先頭への表示。非LLM・生成なし | 不要 | 小 | Phase 0 と独立。任意 |
| 1 | `discuss_opening` ステージ（議論のきっかけ。**§4.2 の命題の日本語言い直しを入れるかは着手時に判断**）+ 格納庫拡張 | 061 | 中 | Phase 0 の画面を見てから |
| 2 | レビュー導線（キューの document グループ + G層ルール） | 不要 | 小〜中 | Phase 1 と同時 |
| 3 | 承認済み素材の配信 + 出所表示 + 鮮度 | 不要 | 小 | Phase 1 と同時 |

Phase 0 は Phase 1 以降と独立に出荷でき、**「生成が本当に必要か」の判断材料になる**。
Phase 0b も独立で、生成を一切足さずに「教員がこのコースで何を議論させたいか」を
画面に出せる（§0 の欠陥「誰が開いても同じ」への非LLMの答え）。

---

## 11. 本設計の外にある同種の問題（2026-07-26 の横断調査で判明）

開幕画面と同じ「生成済みなのに要素に紐づけて表示していない」欠落が他にもある。本設計の
スコープ外だが、記録として残す（対処するなら別 issue）。

| 要素 | 生成済みだが表示されていない情報 | 影響 |
|---|---|---|
| 式 | **記号の意味**（SymbolRegistry の定義・表記ゆれ・スコープ） | 教員の内訳にも学習者にも出ない。式が読めない最大の理由 |
| claim | 判定理由・節/ブロック/スパンの出所（`theory_claims.source_scope` に**保存済み**） | 内訳を作る SQL が `source_scope` を SELECT していない |
| claim | atomicity（複数主張が混ざっているか） | DB に列が無く、解析後に失われる |
| 全ブロック | 論理役割（前提/主張/結果…、rhetorical_role） | 要素の属性としてどこにも出ない |
| 図 | 図中ラベル・装置パーツ・bbox・反復解析の結果 | 教員は図モーダルで全部見えるが、学習者は画像と caption のみ |

また `element_explanations` の component/claim 行は agent 側 ID で保存されるのに、学習者向けの
引き当ては DB UUID で行うため ID 体系が一致せず引けない可能性がある（`routes/learning.py:1127`
にコメントで「未修正・将来の課題」と明記。現状フロントは別経路を通るため未発火）。
**承認済みの説明が学習者に届かない**構造なので、対処の優先度は高い。

（別件で解消済み: 出典タブの「本セッションで参照されたセクション」が内部 ID と agent
クラス名を学習者に出していた問題は 2026-07-26 に撤去し、教材本文の ⚓ チップと同じ供給元から
要素名と要旨を出す「このトピックの論理要素」に置き換えた。）

---

## 12. 実装記録（2026-07-30、全 Phase）

Fable 指揮 + Opus 5 並列サブエージェント体制（Wave 1: Phase 0+0b / Phase 1、Wave 2:
Phase 2 / Phase 3、Wave 3: UIアンカー3点セット + マニュアル追随）で実装。
backend 全スイート + src スイート green。docker E2E（migration 062 実適用・実 LLM 生成）は未実施。

### 12.1 Phase 0 / 0b（投影の是正 + course_focus）

- DTO 追加: `course_focus`（トップレベル、未入力は `""`）/ `documents[].thesis` に
  `central_question` / `paper_goal` / `central_thesis_text` /
  `alternatives: [{text, attribution_label}]`（最大3件、reason・confidence 非投影）/
  `support_sections[].entries: [{text, items}]`（既存 `items` は後方互換で維持、旧データは
  チップ表示へ縮退）/ `fragile_points[].subject: "paper"|"system"`。
- **`paper_goal` は thesis artifact に存在しない** — 正本は paper_skeleton artifact。同一
  `document_run_artifacts` dict から併読（追加 SELECT なし）。
- 主語分離はフロント（`discuss.js`）で2区画（「この論文が確かめていないこと」=論文 /
  「まだ確認できていないところ」=システム）。`_REVIEW_REASON_FACT_PHRASES` を平易化。
- **既存バグを是正**: D層台帳由来の fragile point（`document_id=null`）は旧
  `renderThesisSection` の document 一致フィルタ内で一度も表示されていなかった。course
  レベル区画へ移して到達可能に（OA7）。
- `available` は course_focus のみ存在する場合も true（§7 の「available 不変」は Phase 3 の
  承認済み素材の話と解釈。教員入力を A層成果の有無で黙って落とさない）。
- course_focus 保存: `PUT /api/learning/courses/{course_id}` body `{"course_focus": …}` →
  `learning_courses.data.course_focus`（migration 不要。600字超 422・空文字で解除）。読みは
  `core.course_data.course_focus()`。admin コース管理の所有行「議論テーマ」モーダル。
- 言語変換は未実装（設計どおり Phase 0 の対象外。central_thesis_text 等は英語のまま verbatim）。

### 12.2 Phase 1（生成ステージ + 格納庫）

- **migration は 062**（`062_discuss_opening_explanations.sql`。061 は M層 llm_model_policies が
  消費済み）: element_type CHECK に `'document'` 追加 / `role TEXT` + CHECK
  （**`'discussion_seed'` のみ — §4.2 thesis_restatement は v1 見送り**）/ 部分 index。
- agent: `src/episteme_graph/agents/discuss_opening/`（標準構成）。`core/llm_worker` への
  8系統目アダプタ（BaseJSONLLMClient 遅延 import + run_with_repair 1+2回 + CostGate day-only）。
  1 document = 1 コール。
- validator hard error: 疑問文でない / `evidence_quote` が素材への **verbatim 包含でない**
  （空白正規化のみ許容 — DM1 を機械で守る捏造ガード。設計書に明示は無いが採用）/
  ja 指定で日本語なし / D層 denylist。seed 件数の下限は hard error にせず 1件のみは
  warning 保持（P4）。上限は `truncated`/`truncated_count` で正直記録（OA8）。
- 縮退: 未検証前提も operation も無い document は LLM を呼ばず `skipped_reason='no_source_material'`
  （thesis だけでは生成しない）。修復2回失敗は生成なし。
- orchestrator: `_stage_discuss_opening` を contextual_explanation 後・course_mapping 前に登録。
  `LLM_STAGE_NAMES` + `llm_policy.PIPELINE_STAGE_LABELS["discuss_opening"]="議論のきっかけの生成"` +
  `_FEATURE_DIRECT_ENV["pipeline:discuss_opening"]=("DISCUSS_OPENING_LLM_MODEL","fast")` +
  U層 `KNOWN_FEATURES` 登録（M層/U層の相互整合テストの要求）。
- 永続化行: `element_type='document'` / `element_id=document_id` / `kind='contextual'` /
  `role='discussion_seed'` / `status='candidate'`、evidence JSONB =
  `{evidence_quote, reason, confidence, grounded_in, language, source_fingerprint, opening_version:"v1"}`。
  再解析は同キー candidate のみ superseded（approved / dismissed 不変）。
- 指紋の正本: `core/discuss/authoring.py::compute_source_fingerprint(artifacts)` —
  `sha256(json({fingerprint_version:"v1", central_question, central_thesis_text,
  claim_ids(sorted set)}))`（thesis artifact 内の claim_ids + supporting_subclaim_ids を再帰収集）。
- 副産物のバグ修正: `insert_candidates` の supersede を「キー単位で全 INSERT 前に1回」へ再構成
  （従来実装のままだと 1 document 複数 seed で後続 INSERT が兄弟行を superseded にした）。
- 設定: `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT=4` / `DISCUSS_OPENING_MAX_CALLS_PER_DAY=20` /
  `DISCUSS_OPENING_LANGUAGE=ja` / `DISCUSS_OPENING_LLM_MODEL`（空 = fast tier）。

### 12.3 Phase 2（レビュー導線）

- 一覧 API（`GET /documents/{id}/element-explanations`）が全行に `role` を追加し、document ×
  discussion_seed 行に指紋突合で `stale: true` + `stale_notice`（事実文）を付与（document 単位で
  高々1回計算・artifact 読み出し失敗は判定スキップの fail-open）。**status は問わない** —
  approved の stale も立てるが status は変えない（§7.1）。鮮度の供給元は一覧 API のみ。
- レビューキュー UI: document グループを先頭に「この論文の議論のきっかけ」。カードは
  「深く検討」なし・インライン編集（既存 PATCH = 履歴保持の新行 INSERT）+ 承認/却下 +
  一括承認対応。approved stale 行はボタン無しの表示のみ。
- G層: `course.discuss_opening_unreviewed`（recommended）+ capability
  `course.discuss_opening_review`（KIND_GUIDANCE_ONLY）。**全却下抑止** = その document の
  seed 行に dismissed>0 かつ approved==0 なら candidate があっても点灯しない（履歴からの導出・
  新列なし）。
- 既知の限界: approved stale 行を編集しても evidence の旧指紋を引き継ぐため stale 表示は残る
  （指紋の更新は core 側変更が必要。事実としては「元の解析結果から変わっている」ため誤りではない）。

### 12.4 Phase 3（配信）

- `documents[].discussion_seeds: [{body, evidence_quote, authored: true, authored_by_label}]` —
  **承認済みが1件以上あるときだけキー自体を足す**（空配列も足さない = 承認ゼロで Phase 0 DTO と
  完全一致、OA4 をテストで `==` 固定）。evidence からの射影は evidence_quote のみのホワイトリスト。
- 並びは created_at **降順**（昇順で上限4件を切ると再解析後に承認した素材が永久に出なくなるため）。
- 署名行はサーバ側定数を `authored_by_label` で配信（alternatives の attribution_label と同方式。
  フロントに署名文リテラルを持たない）。`available` 判定は不変（§7）。配信側は指紋を突合しない
  （判断はレビューキュー側）。
- ガードレール: `test_discuss_opening_authoring_guardrails.py`（OA2/OA3/OA4/OA5/OA6 +
  生成側の denylist・skipped・truncated）+ `test_discuss_opening_projection.py` +
  `test_discuss_opening_stage.py`。

### 12.5 残課題

- docker 実機 E2E（migration 062 適用・実 LLM での生成・レビュー→配信の一巡）。
- §7.2 の V層 freeze 非カバー（既知の限界として維持）。
- §11 の同種の投影欠落（記号の意味・claim source_scope 等）は本実装のスコープ外のまま。
