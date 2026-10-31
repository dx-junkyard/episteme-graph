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

## 追補「主張の宛先」（2026-10-01・ID 体系の不一致の是正）

**症状**: 第 17 周、コース 1bad7d9f / トピック t11 の ⚓ `![[claim:claim_span_007]]` の文脈が「このコースの教材からは
特定できませんでした」になり、`claim_span_003_sub01` は解決した。rhetorical_role の `span_id` は block ごとに振り直され、
claim_object_builder の agent ID（`claim_{span_id}`）は**1論文の中でも**複数の主張に当たる。教材に書かれた素の番号は
それ自体が曖昧で、FD4 の fail-closed は正しく推測を拒んでいた（原因は書き手の宛先）。

**規則（書き手）**: 新しい freeze では `course_content_builder` が教材に書く主張の宛先（`evidence_links[].target_id` /
`topic.linked_claim_ids` / 部品投影の `claims` / 生成本文の `![[claim:…]]`）を論文内で一意な形にする
（`assign_claim_addresses` / `claim_address_for`。正本は `_BundleScope.claim_address`）。

1. DB UUID（`theory_claims.id`）— live 行（`theory_claims_live`、origin≠span）の legacy_ids に agent ID を持つ行が、
   出典 block（evidence → `source.block_id`）→ 親子の重複除去 の順でちょうど1行に絞れるとき。
2. `{block_id}:{agent_claim_id}` — UUID に着地できないが出典 block が分かるとき（live 行が曖昧 / artifact に同じ
   claim_id が複数ある）。P0-6 の `{block_id}:{span_id}` ではなく agent ID を後半に置くのは、atomic 子
   （`claim_span_003_sub01`）が親と同じ span を持ち、span キーでは親子を区別できないため。
3. 素の agent ID — live 行が読めず artifact の中でも一意なとき（従来どおり）。

保存済みの本文（下書きの保持・生成の縮退）にある素の番号は、トピックの中でちょうど1つの宛先に対応するときだけ
`rewrite_claim_embeds` で書き換える（2つの宛先に対応する番号は書き換えない）。

**規則（読み手）**: `core.element_context._resolve_claim` は3形を受け、UUID → block 付き → 素の番号 の順に狭める
（`claim_address_form`）。block 付きは「宛先そのものが legacy キーの行」→「同じ block で legacy_ids / span_id が後半に
一致する行」の順で見て、同じ block の span 行と主張オブジェクト行が両方当たるときは主張オブジェクト行を採る（同じ span の
2表現で、別の主張の推測ではない）。素の番号が**同じ論文の中で**複数に当たるときは、焦点の外の同番号へ落とさず
`{"available": false, "note": "同じ番号の主張が論文内に複数あるため特定できません", "reason": "ambiguous_claim_number"}`
を返す（ルートは `build_element_context` の戻り値をそのまま返す。論文をまたぐ曖昧さ・不在は従来の汎用文）。

**既存の教材**: 凍結済みのコースは素の番号のまま（読み手は従来どおり legacy_ids で解決し、曖昧なら上の事実文）。
宛先を一意にするにはコース内容の再生成（freeze のやり直し）が要る。component の agent ID（`comp_001`）は
component_assembly が論文ごとに一意に振り、legacy_ids は agent ID だけなので同じ問題は無い（変更なし）。

**教材の ID 体系（IK-0550）**: document には `documents.id`（UUID・解析成果の正本）と `documents.source_path`
（= `material_id`・アップロード時の ID。UUID に見えることがある）の2つがある。クライアントへ document を渡すキーは
常に `document_id`（UUID）で、`material_id` はアップロード時の ID を名指すところにだけ残す（学習者のコース詳細の
`sources[]` は `document_id` を併記 — `services.with_course_source_document_ids`）。受け付ける側の両形の突き合わせは
単数なら `services._resolve_document`、複数なら `core.document_refs.resolve_document_refs` だけが行う
（コース sources の document 解決・題名解決・コースビルダーの選択教材・R層スコープ・tension worker を移行。
ガードレール `test_document_refs.py::test_both_forms_disambiguation_only_in_known_places` は既存6箇所を凍結した
allowlist で、新しい突き合わせの直書きを拒む）。テストは `test_claim_address_resolution.py` / `test_document_refs.py`。

## 追補（2026-10-27・第 25 周 第 5 波）焦点と同じ論文の別の版・構成の段落

- **別の版**（IK-0996）: 検索結果の後処理（`services._collapse_search_paper_versions` → `core/source_versions`）が
  同じ論文の別の版の同じ箇所を 1 行に畳むとき、**焦点の論文の版を先に残す**（`search_chunks_with_metadata(...,
  prefer_document_ids=_focus.document_ids)`）。焦点の版を落として別の版を残すと、FD3 の除外で焦点の外として落ち、
  出所分類が「別の資料」に変わるため。焦点が無いときは AI が復元した式の少ない版（TeX）を残す。FD1〜FD5 は非改変。
- **構成の段落**（IK-0987）: 焦点の外の論文の「This paper is organized as follows」の段落が FD3 の相対比較を通って
  いた件は、FD3 の閾値を変えず、検索の非本文判定（`non_content_chunk_reason` の `outline`）で採用から外す
  （TeX の参照が落ちた番号の無い「Sec.」も節参照に数える）。焦点の内外を問わず、構成の段落は問いの材料にならない。

pre-mortem: `scope-widened-silently`（相対比較が汎用段落を通す）— 規則ではなく候補の母集合で止める。
`condition-not-propagated`（焦点が版の選択に伝わらない）— `prefer_document_ids` で渡す。

## 追補（2026-10-27・第 25 周 第 6 波）焦点を同じ論文の別の版まで広げる・出典番号の版

- **焦点の版**（IK-1035 / IK-0987 STILL）: 焦点の論文が PDF 版の document のとき、同じ論文の TeX 版のチャンクは
  焦点の外に数えられていた（第 5 波の畳み込みは同じ箇所だけを畳み、別の箇所の TeX 版の行は残る）。焦点内に採用の
  下限を満たす行が PDF 版に無いと FD3 は何も除外せず、無関係な論文の行が通った。`resolve_focus_document(` の直後に
  `core/focus_document.with_paper_versions(_focus, services.paper_version_siblings(...))` で焦点に別の版を足す。
  版の判定は IK-0919 の `same_paper` の 1 つ、版は**スコープ（`allowed_document_ids`）との積だけ**（FD2）、段（`source`）は
  変えない。`resolve_focus_document(` を呼ぶのは 1 回のまま（FD1 のガードレール）。前提の説明の検索（`prefer_document_ids`）
  も同じ関数を通る。スコープが `None` / 空なら版を足さない（fail-closed）。
- **出典番号の版**（IK-1037）: 版を畳むとき、会話で既に出典番号を持つチャンク（`_SessionCitationNumbers.mapping()`）を最優先で
  残す（`collapse_paper_versions(prefer_chunk_ids=)`）。焦点の版の優先（第 5 波）より前に置く — 前の往復で引用した版を
  選び直すと別のチャンクとして新しい番号が振られ、前の回答の [出典5] が今回 [出典12] になるため。

### 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 焦点を広げるとスコープを広げないか | `scope-widened-silently` | 版はスコープとの積だけ。スコープの外の版は足さない |
| 写しの 1 つで対象を表していないか | `same-name-different-referents` | 焦点を論文の版の集合にする（判定は `same_paper` の正本） |
| 代表の選び直しで番号が変わらないか | `id-not-stable-across-versions` | 番号を持つチャンクを代表に残す |
| 版の判定を別の面で再実装しないか | `duplicate-canonical-sources` | `same_paper` を呼ぶだけ。新しい同一視の規則を作らない |

当たらない族: 確定の担当（AI の判定で確定するものは無い）。

## 追補（2026-10-31・第 25 周 第 9 波）地図の場所に置かれた論文を焦点に加える（FD-note 3）

- **症状**（製品頭脳 B 4293）: 分野の地図で「暗黒エネルギー」を開いて尋ねても、焦点はトピックの論文（Cep B）だけで、
  FD3 がトピックの論文より類似度の低い地図の論文の行を落とした。地図の場所の参照（`view.atlas_node_id`）は画面文脈の
  事実文（SA層）にしか効いていなかった。
- **FD-note 3 地図の場所の論文は加えるだけ。** chat core は `resolve_focus_document(` の直後に
  `core/focus_document.with_screen_place_documents(_focus, 置かれた論文, allowed_document_ids=...)` を通す。置かれた論文は
  `learner_landscape_for_documents`（学習者向け位置づけ射影）で、コースの論文 ∩ スコープの中だけ（FD2）。段（`source`）と
  既存の焦点の論文は変えず、後ろに足す（焦点が無ければ④段 = 画面で選んだ論文）。discuss では足さない。
- **トピックの段を外さない理由**: `view.atlas_node_id` は app.js の「最後に開いた場所」で、地図を閉じた後も残る
  （`state.atlasLastNode`）。これでトピックの段を外すと、地図を一度開いた後のトピックの問いすべてで焦点が崩れる。
  加えるだけなら、トピックの問いでは地図の論文の行が FD3 を通るだけ（並べ替えは焦点の内外だけを見る）。
- 同じ理由で、前提の逆質問（A3）のゲートは `view.atlas_node_id` では飛ばさない — 地図の問いでゲートを出さない規律は
  明示の ↗（`atlas_context`）の経路のまま。

### 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 焦点を足すとスコープを広げないか | `scope-widened-silently` | 置かれた論文はコースの論文 ∩ `allowed_document_ids` だけ。スコープが空なら足さない |
| 持続する画面状態を「いま」の明示と読まないか | `condition-not-propagated` | 最後に開いた場所は「加える」にだけ使い、段の勝ち負け・前提ゲートは変えない |
| 「どの論文か」の条件式を他所に書かないか | `duplicate-canonical-sources` | 合成は `core/focus_document.py` の 1 関数。`resolve_focus_document(` は chat core で 1 回のまま |

当たらない族: 確定の担当（確定する操作は無い）。

## 追補（2026-10-31・第 25 周 第 10 波）今の往復で選んだ地図の場所は明示の焦点（FD-note 4）

- **症状**（製品頭脳 A 4307 / B 4312・前周 4293）: FD-note 3 は置かれた論文を焦点の**後ろに加える**だけで、トピックの論文
  （Cep B）は焦点に残った。FD3 の並べ替え・除外は焦点の内外しか見ないので、場所を開いて尋ねても Cep B の行が先に並び、
  検索の構造 1 hop（`retrieved_structure`）にも Cep B の主張・段階が載った。表示中の教材の出典（`_topic_source_rows`）も
  Cep B を番号付き出典として足していた。
- **FD-note 4 今の往復で選んだ地図の場所は明示の段。** 画面は分野の地図で場所を選んだ直後の 1 往復（地図はモーダルなので
  閉じてから尋ねる）と地図を開いている間だけ `selection.atlas_node_id` を載せ、その往復を送ったら落とす（トピックを移っても
  落とす。最後に開いた場所は `view.atlas_node_id` に残る）。chat core は `selection.atlas_node_id` があるときだけ
  `with_screen_place_documents(..., selected_now=True)` を呼び、置かれた論文（スコープ内）だけを `source="explicit"` の焦点にする
  （トピックの段は外す）。置かれた論文がスコープに無ければ従来の焦点のまま（材料の無い焦点を作らない）。同じ往復では
  表示中の教材の出典チャンクを番号付き出典に足さない（教材本文の注入は従来どおり）。`view` だけの往復は FD-note 3 のまま。
- 前提の逆質問のゲートは変えない（`atlas_context` の経路のまま）。`resolve_focus_document(` は chat core で 1 回のまま。
- 意図分類の手がかり（`_intent_screen_hint`）も同じ区別で、地図の場所の 1 行は `selection` にあるときを先に、`view` だけなら
  要素・選んだ一節・わたしの地図の後に回す（製品頭脳 B 4308）。

### 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 持続する画面状態を「いま」の明示と読まないか | `condition-not-propagated` | 明示は `selection`（送ったら落とす）だけ。`view` は FD-note 3 の「加える」にだけ使う |
| 焦点を替えるとスコープを広げないか | `scope-widened-silently` | 置かれた論文はコースの論文 ∩ `allowed_document_ids`。無ければ焦点を替えない |
| 「どの論文か」の条件式を他所に書かないか | `duplicate-canonical-sources` | 段の入れ替えは `core/focus_document.with_screen_place_documents` の 1 関数 |
| 画面の申告が送信のたびに残り続けないか | `stale-derivative-served` | app.js は `getScreenContext` で 1 回使ったら落とす・トピック移動で落とす |

当たらない族: 確定の担当（確定する操作は無い）。
