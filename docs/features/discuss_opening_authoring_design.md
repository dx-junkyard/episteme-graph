# discuss 開幕素材のオーサリング（生成 → 教員添削 → 配信）

- 状態: **設計（未実装）**。2026-07-26 起票。
- 親文書: `discussion_mode_design.md`（DM1〜DM8。特に §3.3 開幕・§9.5 可読性改修）
- 直接の前例: `hierarchical_context_explanation_design.md`（二層説明 + 説明レビューキュー）
- 関連: `element_deliberation_workspace_design.md`（W層 candidate→commit）、
  `guidance_layer_design.md`（G層 To-Do）、`image_pipeline_knowledge_library_design.md` §15
  （#496: AI 候補列と教員確定列の分離）

---

## 0. 問題

discuss モードの開幕画面（`core/discuss/opening.py` → `discuss.js`）は、A層成果を
**決定論的に投影するだけ**の画面である。DM8（同期パスに LLM を入れない）を守るための
正しい制約だが、その結果として学習者に届いているのは「A層の内部表現そのもの」だった。
2026-07-26 の可読性改修（`discussion_mode_design.md` §9.5）で見せ方は直したが、
**中身の問題は残っている**。

| 残る問題 | 理由 |
|---|---|
| 中心命題が論文原文（英語）の長文 | 投影できるのが claim の生テキストだけで、平易な言い直しがどこにも存在しない |
| 答え（結論）から始まる | 「この論文が答えようとした問い」が投影されていない |
| 議論の火種が無い | 反応できる対象（立場・別解釈・争点）が画面に無く、要約だけが並ぶ |
| 誰が開いても同じ | 教員がこのコースで何を議論させたいかを表現する場所が無い |

**この画面に必要なのは要約ではなく、話す理由である。** そして「話す理由」は投影では
作れない — 誰かが書く必要がある。書き手を LLM だけにすると出所の正直さ（DM1）と
断定禁止に触れ、教員だけにすると運用が回らない。**AI が候補を書き、教員が確定する**という
この Repo の既定パターンに載せるのが解である。

### 0.1 この設計は新しい仕組みではない

「AI が候補を作る → 人間が確定する → 確定済みだけが学習者に届く」は既に反復実装されている:

| 層 | 候補 | 確定 | 配信 |
|---|---|---|---|
| 二層説明 | `ContextualExplanationAgent` | `element_explanations.status` + 一括承認 | 承認済みのみ |
| C層 | `component_explanations` (kind=standard) | endorsement / review_status | teacher_approved のみ |
| W層 | `element_annotations` | commit ルーティング | committed のみ |
| D層 | 前提候補・スコープ候補 | 教員確定 | 確定済みのみ |
| B層 | tension / structure_anchor | 本人 confirm | 本人のみ |
| R層 | 出題 item (auto) | 教員の事後監査 | auto から配信 + 回収 |

開幕画面だけがこの背骨の外にいた。本設計は**新機構の追加ではなく、既存機構の適用範囲を
document 単位の学習者向け散文へ広げること**である。

---

## 1. 不変条項（OA1〜OA8）

DM1〜DM8 / E1〜E8（二層説明）を継承したうえで、本件固有に課す。

- **OA1 A層非改変**: `src/episteme_graph/agents/` の既存 agent は読むだけ。新ステージの追加は
  `_PIPELINE_STEPS` への登録のみ（Tier 3-19 方式）。
- **OA2 candidate-only**: LLM 出力は常に `status='candidate'`。教員の明示操作なしに学習者へ
  露出しない。一括承認でも起点は必ず人間のボタン押下（E2 継承）。
- **OA3 同期パスに LLM を入れない**: 生成は解析パイプライン（非同期・document 1本につき数回）。
  `GET /discuss/opening` は従来どおり **LLM 0 回**の読み出しのみ（DM8 不変）。
- **OA4 未承認でも画面を殺さない**: 承認済み素材が無ければ現行の決定論投影へ **fail-soft**。
  「承認されるまで開幕画面が出ない」設計にはしない（運用が止まり、E2 が形骸化する）。
- **OA5 情報を落とさない**: 却下は `dismissed`、再解析は `superseded` の状態遷移で保持。
  行削除 API を作らない（P4）。
- **OA6 数値を見せない**: confidence 生値は API に出さない。段階ラベルのみ（DM6 / E6 継承）。
- **OA7 出所を偽らない**: 承認済み素材は「担当教員が確認した説明」として、未承認時の
  決定論投影は従来どおり無署名で出す。AI 生成物を無条件に論文の言葉として提示しない（DM1）。
- **OA8 教員の負担に上限を置く**: 1 document あたりの生成件数に上限を設け、一括承認を前提とする。
  レビュー負荷が上限を超える設計は、E2 のゲートを事実上無効化する（二層説明 §13.0 の教訓）。

---

## 2. 何を作るか（素材の役割語彙）

document 単位。**role** で区別する。

| role | 内容 | 上限 | 生成元 |
|---|---|---|---|
| `central_question` | この論文が答えようとした問いと、それまで何が分かっていなかったか（2〜3文） | 1 | thesis artifact の `central_question` / `paper_goal` + skeleton の `problem_setting` ブロック |
| `thesis_restatement` | 中心命題の平易な言い直し（原文の置き換えではなく併記用。2〜3文） | 1 | `central_thesis.text` + claim 本文 |
| `alternative_reading` | 「この論文はこう読むこともできる」 | 0〜2 | thesis artifact の `alternative_theses` |
| `discussion_seed` | 立場を求める形の火種（「この論文は〜を仮定している。受け入れるか?」） | 2〜3 | fragile points（review_required / D層 open assumptions）+ derivation の operation（`linearize_*` / `eliminate_*` = 著者の選択） |

合計 ≤ 7（上限 `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT`、既定 8）。**これ以上増やさない** — 開幕画面は
論文の目次ではなく、3つ見せて1つ挑発する場所である（§0）。

`discussion_seed` は D層の思想（未検証を事実文で併記）と同一線上に置く。煽り文句・
「疑え」等の禁止語彙は D層ガードレールと同じものを適用する。

### 2.1 コース文脈の層

役割語彙は document 単位（＝論文の属性）だが、コースごとに変えたい素材が1つある。

- `course_focus`（**コース単位**、教員の任意入力・AI 生成なし）: 「このコースでは、この論文の
  何を議論してほしいか」。`learning_courses.data` に持ち、開幕画面の先頭に1つだけ出す。

document 単位の素材を複数コースで共有し、コース固有の意図だけを別に持つ。二層説明の
generic / contextual と同じ切り方である。

---

## 3. 生成（Phase 1）

### 3.1 新ステージ `discuss_opening`

- 実装場所: `src/episteme_graph/agents/discuss_opening/`（標準 agent 構成）。
- 登録: `_PIPELINE_STEPS` の **`contextual_explanation` の後・`course_mapping` の前**。
  この時点で thesis / graph / derivation / narrative / 図解析が揃っている。
- 入力（input_builder。**不透明 ID を渡さず解決済みテキストに展開**する — 二層説明 §5.1 の規約）:
  thesis artifact（`central_question` / `paper_goal` / `central_thesis.text` /
  `alternative_theses` / `support_structure[].text`）+ main graph の stage backbone +
  fragile 候補（review_required ノード・D層 open assumptions）+ derivation の operation 列。
- 出力: role ごとに `body` / `evidence_quote` / `reason` / `confidence`。
- LLM は **1 document = 1 コール**（role をまとめて structured output で取る）。
  `core/llm_worker/`（BaseJSONLLMClient + run_with_repair + CostGate）への 15〜20 行アダプタで
  接続する（8 系統目。コピペ禁止）。
- 縮退（P4）: thesis artifact が無い document は**生成しない**（`skipped_reason='no_thesis'` を
  `stage_outputs` に正直に記録）。修復2回失敗の role は生成なし。根拠の無い問い・火種を創作させない。
- コスト: `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT`（既定 8）/ `DISCUSS_OPENING_MAX_CALLS_PER_DAY`
  （既定 20）/ `DISCUSS_OPENING_LLM_MODEL`（fast tier 既定）。

### 3.2 生成言語

学習者向け散文なので**日本語**で生成する。`lecture_language`（コース単位設定）は document 単位の
生成には使えない（同じ論文が言語設定の違うコースに載りうる）ため、v1 は
`DISCUSS_OPENING_LANGUAGE`（既定 `ja`）の**環境変数1つ**で決める。多言語化は非スコープ（§8）。

---

## 4. 格納庫（Phase 1, migration 061）

**`element_explanations`（migration 056）を再利用する。** 新テーブルを作らない。

理由: 開幕素材は「document スコープ・AI 生成・学習者向け散文・candidate→approved・
再解析で superseded・権限は document editable・レビューは同じキュー」で、既存台帳の性質と
完全に一致する。違いは**係留先が要素ではなく document 全体**という一点だけである。
別テーブルにすると、レビュー UI・一括承認 API・監査語彙・権限ゲートを二重に持つことになる。
教員から見ても「AI が書いた学習者向け文章のレビューは1箇所」であるほうがよい。

migration 061 で加える変更（すべて冪等・`DO $$` ガード）:

1. `element_type` の CHECK に **`'document'`** を追加（`element_id` は `document_id` と同値を入れる）。
2. **`role TEXT`** 列を追加（NULL 許容。既存行は NULL のまま＝二層説明の説明本文）。
   CHECK は `role IS NULL OR role IN ('central_question','thesis_restatement',
   'alternative_reading','discussion_seed')`。
3. `kind` は既存の CHECK（`generic` / `contextual`）のまま、開幕素材は **`contextual`** を使う
   （この論文についての説明であり、分野一般の説明ではない）。語彙を汚さない。
4. 部分ユニーク制約は張らない（`alternative_reading` は複数行、改訂は superseded で履歴が積まれるため）。

CHECK 制約の変更は drop → re-add を `DO $$` で囲む（migration 041 の `component_type` 拡張が先例）。

---

## 5. レビュー導線（Phase 2）

### 5.1 既存キューに相乗りする

`GET /documents/{id}/element-explanations?status=candidate` と
`POST /documents/{id}/element-explanations/bulk-review`（二層説明 §13.1）を**そのまま使う**。
新 API を作らない。`role` をレスポンスに含めるだけで済む。

UI（`deliberation.js` の説明レビューキュー）に加える変更:

- `element_type='document'` の候補を**先頭に独立グループ**「この論文の議論用素材」として表示する。
  要素グループ（図・component・claim・式）より上に置く — 論文全体の話だからである。
- このグループのカードは「深く検討」モーダルを持たない（係留先の要素が無い）。
  代わりに **role ラベル + 本文のインライン編集**（承認前に教員が文言を直せる）+ 承認 / 却下。
- 編集は既存の PATCH（旧行 `superseded` → `created_by=user_id` の新行 INSERT）に乗せる。
  教員が直した瞬間、その素材の書き手は教員になる（OA7 の帰属はここで決まる）。

### 5.2 コース作成時に気づける導線（G層）

生成は document 単位だが、**添削の動機はコースを作るときに生まれる**。G層に1ルール追加する:

- `course.discuss_opening_unreviewed`（severity: **recommended**、capability は
  `course.discuss_opening_review` を新設して `KIND_GUIDANCE_ONLY`）
- 点灯条件: 自分が所有するコースのソース document に `element_type='document'` かつ
  `status='candidate'` の行がある。
- 消滅条件: 全件が approved / dismissed になる（G1: 完了フラグを持たない・状態から毎回導出）。
- 事実文（G6: 煽らない）: 「このコースのソース論文に、未確認の議論用素材があります。」
- `locate_plan`: 教材管理 → 当該 document → 要素インベントリ → 説明レビューキュー。

コースビルダーの登録直後にもこのバッジが更新される（既存の再取得トリガーに相乗り）。

---

## 6. 配信（Phase 0 + Phase 3）

### 6.1 Phase 0（非LLM・先行実施可能）— 投影を広げる

生成もレビューも無しに、**今日の制約のまま**取れる改善がある。`project_thesis` が artifact の
以下を捨てているため、まずこれを投影する:

- `central_question` / `paper_goal` — 「問いから始める」を成立させる（§0 の2番目の問題）。
  export のバリデーションゲートは `central_question` 不在を error 扱いにしているのに、
  学習者向け画面では使われていない。
- `central_thesis.text` / `support_structure[].text` — エージェントが合成した命題文。
  現在は捨てられ、`claim_ids` → claim の生ラベル（論文原文）だけが出ている。
- `alternative_theses` — 別の読み方。そのまま §2 の `alternative_reading` の素材でもある。

**注意**: これらは A層が生成した英語のテキストである可能性が高い（thesis_reconstruction の
プロンプトに言語指定は無い）。Phase 0 で改善するのは**構成**（問いから始まる・別解釈が出る）
であって、**言語ではない**。言語の解決は Phase 1 の生成を待つ。この区別を実装時に混同しない。

### 6.2 Phase 3 — 承認済み素材の配信

`build_opening` が document ごとに承認済み素材を読み、role 単位で差し替える。

- **role 単位の部分適用**: `thesis_restatement` だけ承認済みなら、その1つだけ差し替え、
  残りは決定論投影のまま出す。「全部揃うまで出さない」にしない（OA4）。
- **承認済みが1件も無い document**: 現行の投影のまま（＝今と同じ画面）。劣化しない。
- **出所表示（OA7）**: 承認済み素材を含む区画には一行 —
  「この説明は、論文の解析結果をもとに担当教員が確認したものです。」
  未承認の決定論投影には何も足さない（従来どおり）。
- **DTO**: 既存契約に `authored` フラグ（bool）と `authored_by_label` を足すだけ。
  confidence 等の生値は従来どおり `_strip_numeric_keys` で除去（OA6）。
- `available` の判定は変えない（承認済み素材の有無で画面の出し分けをしない）。

### 6.3 鮮度（再解析との関係）

`document_figures` の #496 規約（AI 候補列と教員確定列を分け、再解析が教員確定を消さない、
migration 053）をそのまま適用する。

- 再解析: 既存 `candidate` は `superseded` へ、`approved` は**触らない**。
- ただし承認済み素材が古い解析結果に基づいている可能性は残る。生成時に
  `evidence.source_fingerprint`（`central_question` + `central_thesis.text` + claim id 集合の
  決定論 sha256）を保存し、再解析後に不一致なら**レビューキューに事実文で並べる**
  （「元の解析結果が変わっています」）。**自動で非承認に落とさない** — 学習者に出ていた
  ものが黙って消えるほうが有害である。
- コース公開（V層 freeze）時点の承認済み素材がスナップショットに入るかは §9 の未決事項。

---

## 7. ガードレール（`backend/tests/`、`guardrail_helpers` 使用）

- `test_discuss_opening_authoring_guardrails.py`（新規）
  - 生成モジュールが FastAPI を import しない
  - `build_opening` の呼び出し経路に LLM 呼び出しが無い（OA3）— `core/llm` の import 不在で検査
  - 承認済み以外（candidate / dismissed / superseded）が DTO に混ざらない（OA2）
  - 承認済み素材ゼロで従来の投影 DTO と同一になる（OA4 の回帰検知）
  - 行削除 API が存在しない（OA5）
  - confidence / スコア等の生値が DTO に出ない（OA6）
  - 禁止語彙（「疑え」「〜すべき」等の煽り・D層と同じ denylist）が生成物 validator で弾かれる
  - 1 document あたりの生成上限（OA8）と truncated の正直な記録
- 既存 `test_discuss_opening.py` に role 投影のケースを追加。
- 既存 `test_next_steps_guardrails.py` に新ルール（capability 存在・fail-closed）を追加。

---

## 8. 非スコープ（v1）

- **学習者ごとの個人化**（「前回ここで止まりました」・わたしの地図の兄弟ノード提示）。
  素材が固定されてから次段で扱う。`cross_course_hint` が同型の先例。
- **他論文との比較の学習者開放**（W層 positioning のコーパス横断レンズは教員限定のまま）。
- **多言語**（`DISCUSS_OPENING_LANGUAGE` 1つで固定。コース単位の切替はしない）。
- **開幕画面の A/B 実測**（discuss 観測基盤 `discuss_metric_events` に既存イベントがあるので、
  必要になったら `opening_*` の内訳で見る。新規メトリクスは追加しない）。
- **`course_focus` の AI 生成**（§2.1 は教員の任意入力のみ）。

---

## 9. 未決事項（実装着手前に確定する）

1. **別解釈の出し方** — `alternative_reading` を複数併記するか、教員が1つ選ぶか。併記は
   「論文の読み方は1つではない」を伝える一方、初学者には負荷になりうる。
2. **V層 freeze との関係** — コース発行時のスナップショットに承認済み素材を含めるか。
   含めれば消費側の教員が安定した画面を見られるが、`element_explanations` は document 側の
   資産で course release には現状入っていない。
3. **`discussion_seed` の出典** — fragile points（D層台帳経由）と derivation operation の
   どちらを主にするか。前者は「未検証」、後者は「著者の選択」で、議論の質が変わる。
4. **教員が全却下した document** — 決定論投影に戻る（OA4）が、それを G層で再点灯させるか、
   「この論文は素材不要」と記録して黙らせるか。

---

## 10. 実装順序と規模感

| Phase | 内容 | migration | 規模 |
|---|---|---|---|
| 0 | 投影の拡張（`central_question` / `text` / `alternative_theses`） | 不要 | 小。`opening.py` + `discuss.js` + テスト |
| 1 | `DiscussOpeningAgent` + ステージ登録 + 格納庫拡張 | 061 | 中。agent 一式 + llm_worker アダプタ |
| 2 | レビュー導線（キューの document グループ + G層ルール） | 不要 | 小〜中。既存 API 再利用 |
| 3 | 承認済み素材の配信 + 出所表示 + 鮮度 | 不要 | 小 |

Phase 0 は Phase 1 以降と独立に出荷でき、しかも「生成が本当に必要か」の判断材料になる
（問いと別解釈が出るだけで十分読めるなら、Phase 1 の投資は小さくできる）。**Phase 0 を先に
出して評価することを推奨する。**
