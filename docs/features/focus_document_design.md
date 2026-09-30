> **状態:** 実装済み（2026-09-30。migration なし）。以後は §5 実装記録の追記のみ。

# 焦点論文（Focus Document）

## 1. 問題

「いまの会話・操作はどの論文についてか」が会話の状態として存在しなかった。そのため
次の経路がそれぞれ独自の規則で論文を選び、少しずつ食い違っていた。

| 経路 | 旧来の規則 |
|---|---|
| トピック学習の RAG | トピックの論文を先に（discuss では無効） |
| discuss の RAG | 直前の回答が引用した論文を先に（トピック段とは排他で第3段なし） |
| 前提知識の説明 | トピックの論文のみ（議論中の論文を受け取っていない — IK-0556） |
| 画面文脈の要素解決 | 優先なし（`eq_5` がコースの先頭の論文に当たる — IK-0553） |
| 記号の直前定義 | タップ位置の論文 → トピックの論文（議論中は無し — IK-0552） |
| 要素文脈（claim / equation） | トピックの論文 → コース内で一意 |
| 部品文脈（component） | 優先なし・コース全体の `LIMIT 1`（IK-0554 の残り） |
| 降下路 | 優先なし・先頭の論文 |
| 「いまここの周り」範囲表示 | トピック⇄claim が無ければコース全体（議論の痕跡は常にコース全体） |
| 構造帰属 worker | 概念はコース全体から |

比較も `>`（別論文を残す）と `>=`（採用の下限）が混在していた。根は1つなので、
解決器を1つ・優先規則を1つにし、各経路はその値を受け取るだけにする。

## 2. 不変条項

- **FD1 焦点は1関数で決める。** `core/focus_document.py::resolve_focus_document` が段順
  ①明示（document 直付け discuss の単一 document・タップ位置のチャンクの論文・画面の明示）
  → ②トピックの論文（`services.topic_source_document_ids`）→ ③直前の回答が引用した論文
  → ④画面で選んだ論文（discuss 開幕の起点チップ・トピック教材の論文）で決める。
  最初に空でない段が勝つ。「どの論文か」を決める条件式を他所に新しく書かない。
- **FD2 スコープを広げない。** 各段は呼び出し側のスコープ（`allowed_document_ids`）との積だけを
  採る。焦点は並べ替えと衝突解決にだけ使い、検索範囲を変えない（DM1 継承）。
- **FD3 優先規則は1つ。** `prefer_focus`: `(採用の下限未満, 焦点の外, -score)` の安定ソート →
  切り詰め → 焦点に採用の下限（`>= 0.30`）を満たす行があれば、焦点の外は**焦点内の最良より
  厳密に高い（`>`）**ものだけ残す。焦点に採用できる行が無ければ何も外さない。
- **FD4 衝突は fail-closed。** `resolve_in_focus`: 焦点の段を**まとまり**として見て、候補を持つ
  焦点の論文がちょうど1つでその候補が1つのときだけ採る（トピックが2本の論文を束ね、両方に
  `eq_5` / `comp_001` があれば `None` — 段の中の並び = UUID 順で勝者を決めない）→ 焦点のどの論文にも
  候補が無ければスコープ内でちょうど1論文に1候補のときだけ → それ以外は `None`。推測で
  「最初の論文」を選ばない（`LIMIT 1` をコース横断で使わない）。
- **FD5 記録は enum のみ。** 痕跡・ログに焦点の document_id の列や件数を焼かない。
  `FocusDocument.source` の語彙は `explicit / topic / previous_citation / opening / none`。

### FD-note（段を入れる条件 — 呼び出し側が持つ）

- **FD-note 1 明示の広いスコープを狭めない。** document 直付け discuss で学習者が
  `discuss_scope="all_visible"` を明示したときは、その単一 document を明示の段に入れない
  （入れると `prefer_focus` が学習者の選んだ広い範囲を当該 document へ狭めてしまう）。
- **FD-note 2 直前の引用の段は議論だけ。** chat core は `_is_discuss` のときだけ③段を渡し、
  `services.learner_focus_document` は `topic_id == "_discussion"` のときだけ保存済みの直前の引用を読む。
  通常のトピックで束ねる論文がスコープに無いときに③段を入れると、前回引用した論文へ往復が張り付く。

## 3. 経路と入力

| 消費者 | 焦点の入力 |
|---|---|
| `_learning_chat_core`（RAG・DIFF・画面文脈・痕跡の `cited_chunk_ids`） | `resolve_focus_document` を1回（明示 = 単一 scope document / トピック / `_carry_previous_cited_sources` の document / `screen_context.selection.document_id`） |
| 前提知識の説明 `_resolve_prerequisite_context` | `services.learner_focus_document`（明示 → トピック → 保存済み会話の直前の引用（`_discussion` のみ）→ `screen_context.selection.document_id`。chat core の焦点とは別の1回だが同じ段順） |
| 記号 lookup / 要素文脈 / 部品文脈 / 降下路 | 同上（`topic_id` / `document_id` クエリ）。記号 lookup はタップ位置のチャンクの論文を core 側の明示段として重ねる |
| 「いまここの周り」範囲表示 | トピック⇄claim → 痕跡の `cited_chunk_ids` の論文（③段）→ コース全体 |
| 構造帰属 worker | 回答が引用したチャンクの論文の概念（引用が無いときだけコース） |

直前の引用の段は、チャット内では前の回答が引用したチャンクの読み出し結果の
`document_id`、チャット外では `services.previous_cited_document_ids`（保存済み出典メタの
`document_id` — `_history_source_meta` が追記する additive キー — を優先し、旧履歴はチャンク id
から1 SQL で解決）を使う。上位の段が空のときだけ読む。

## 4. 画面からの参照

`app.js getScreenContext().selection.document_id` は、discuss では開幕で押した起点チップの
論文（`discuss.js` が `window.LearningScreen.setDiscussFocusDocument` で伝える）、それ以外は
トピック教材の論文（`TopicMaterialResponse.document_id` = トピックが束ねる論文がちょうど1つの
ときだけ）。部品文脈・降下路の要求は `topic_id` / `document_id` を添える。いずれも参照で、
サーバはスコープとの積でしか使わない（FD2）。

## 5. 実装記録

- `backend/core/focus_document.py`（新設。`FocusDocument` / `resolve_focus_document` /
  `prefer_focus` / `resolve_in_focus` / `unique_row_lookup` / `scope_chunks_to_focus`）
- `backend/api/routes/learning.py`: chat core の焦点1回化、`_prefer_topic_documents` /
  `_drop_off_topic_chunks` / `_scope_chunk_ids_to_documents` は薄い委譲、前提の説明・画面文脈
  （`_screen_element_sources` → `build_element_context` / `_component_context_with_explanation`）へ
  焦点を配線、`_adopted_source_entry` / `_history_source_meta` に `document_id`、記号 lookup /
  要素文脈 / 部品文脈ルート（`topic_id` / `document_id` 追加）、`TopicMaterialResponse.document_id`
- `backend/api/services.py`: `previous_cited_document_ids` / `learner_focus_document`
- `backend/api/routes/descent.py` + `core/descent/{resolve,engine}.py`: `topic_id` / `document_id` →
  `preferred_document_ids`、式・行の衝突解決を FD4 に
- `backend/core/element_context.py` / `core/component_context.py` / `core/symbol_lookup.py`:
  FD4 / FD1 に委譲（部品のコース横断 `LIMIT 1` を廃止）
- `backend/core/personal_graph/{nearby,queries}.py`: 範囲表示の③段（`FACT_RANGE_CITED_DOCUMENTS`）
- `backend/core/structure_anchor/worker.py`: 概念候補を引用チャンクの論文に限定
- `frontend/public/js/app.js` / `discuss.js` / `index.html`（`?v=`）
- ガードレール: `backend/tests/test_focus_document_guardrails.py` / `test_focus_document_core.py`
- 痕跡 payload への `focus.source` の記帳は見送り（payload の組み立ては生成後の区画で、
  別の変更と並走していたため。記帳するなら enum 1キーのみ = FD5）。

## 6. 課題ナレッジとの対応

- IK-0513（別論文の短いチャンクの混入）: FD3 の1規則に統合。
- IK-0552（記号 lookup の論文違い）: 議論中（`_discussion`）でも直前の引用の段が効く。
- IK-0553（式 ⚓ の論文間衝突）: 画面文脈経路と降下路にも FD4 を適用（旧来は要素文脈 API だけ）。
- IK-0554（部品文脈）: 依存先だけでなく部品本体の解決もコース横断 `LIMIT 1` をやめ FD4 に。
- IK-0556（議論中の論文が優先されない）: 前提の説明が直前の引用の段を受け取る。
- 2026-10-01 レビュー是正: FD4 を焦点の段のまとまり判定に（M5）、FD-note 1/2 を追加（M4）、
  前提の説明に④段（画面の選択）を渡す。回帰テストは `test_focus_document_core.py`
  （`TestFocusGroupAmbiguity` / `TestLearnerFocusPreviousCitationGate`）と
  `test_focus_document_guardrails.py::TestReviewGatesM4`。
