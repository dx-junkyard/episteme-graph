# 学習機能（学生UI）

[← ドキュメント目次](../README.md)

> **更新注記（2026-08-14）:** discuss モード（§3.8）・理解サイクル（§3.9）・
> 検証状態の事実併記（§3.10）を追補した。他の節は 2026-07-18 時点の記述で、
> 残る差分は CLAUDE.md の該当節を参照。
> **更新注記（2026-08-15）:** わたしの記録（§6.5、主権台帳v1）と帰還の扉（§3.11）を追補した。

学生向け学習 UI の機能を、画面と裏側の API の両面から解説します。
実装: `frontend/public/index.html` + `frontend/public/js/app.js`（ES6+ SPA）。
バックエンドは `/api/learning/*`（[API](../backend/api.md)）。

---

## 1. 3 パネルレイアウト

| パネル | 内容 |
|---|---|
| 左サイドバー（260px） | 学習パス（章→トピックのツリー、進捗ドット: 完了🟢/進行中🔵/ロック⚫）、概念マップ（習得/学習中/未来） |
| 中央 | RAG チャット UI + レクチャーコンテンツ表示領域。トピック教材チャンクをチャット上部に表示 |
| 右パネル（300px） | Context / Progress / Sources タブ（前提知識・誤解・学習支援ヒント、章進捗、PDF 出典） |

主要 state（`app.js`）: `token / role / courseId / course(マスター) / personalLayer(誤解・アンカー) / currentTopicId / chatMessages / topicMaterial / learningSupport` に加え、`lastGrounding / lastSources / lastOverallTier`（出所・出典表示）、`interestTraces`（問いの軌跡）、`tensionDigest / tensionDeferred`（違和感ダイジェスト）、`topicHasAudio`（レクチャー音声の有無）。

---

## 2. コース受講と進捗

| 機能 | 仕組み | API |
|---|---|---|
| コース一覧 | 「自分のコース」と「受講可能なコース（公開テンプレート）」を分けて表示 | `GET /api/learning/courses` |
| 受講登録 | 公開テンプレートをクローンせず、`learning_states` に1行 INSERT して個人の学習状態を作成 | `POST /api/learning/courses/{id}/enroll` |
| コース読込 | マスター + 個人レイヤーを取得 | `GET /api/learning/courses/{id}` |
| 進捗 | 章ごとの完了状況、セッション履歴 | `GET /api/learning/courses/{id}/progress` |
| トピック教材 | コンテキスト用にチャンクを取得 | `GET .../topics/{tid}/material` |

> マスター（不変）と個人進捗（可変）の分離は [データモデル](../architecture/data-model.md#重要な設計パターン) を参照。

### 2.1 受講登録の確認ダイアログ

コース選択で「受講可能なコース」を選んでも即時には受講登録しない。確認モーダル
（`app.js` の受講確認オーバーレイ。コースタイトル + `description` を提示し
「受講する / キャンセル」）を経てから `POST .../enroll` を呼ぶ。キャンセル時は
select を元の値へ戻し、失敗時はモーダル内にエラー表示して再試行できる。

### 2.2 コース完了カード（サーバー正本の完了判定）

確認問題（`POST .../topics/{tid}/check-question` → `.../check`）に合格すると、サーバーが
`services.record_topic_check_pass()` で **`learning_states.progress_data`** に永続化する:

- `progress_data.completed_topics`（topic_id → 合格時刻 ISO8601。既存タイムスタンプは上書きしない）
- 全トピック合格時に `progress_data.course_completed_at` を一度だけ設定

`/check` レスポンスの `course_completed` / `completed_topic_ids` が**サーバー正本**で、
フロント（`app.js` の `showCourseCompletionCard`）は `course_completed === true` のときだけ
「全トピックを学習しました」と断定する（「次のトピックが無い」ことだけで完走と断定しない。
未確認なら「まだ確認を終えていないトピックがあります」に縮退 — fail-closed）。カードは
事実文のみ（数値・スコア・祝祭演出なし）で、「他のコースを見る」「わたしの地図を見る」
（`PersonalMapHome.open()`）への導線を添える。

---

## 3. RAG チャット

- 送信ボタンは「質問」1 つに統合済み（旧「教材に沿って質問」「自由に質問」の 2 ボタンは廃止。
  `intent_mode` の on_path / explore は寄り道状態からフロントが自動判定して送る内部値）。
- 送信: `POST /api/learning/courses/{id}/topics/{tid}/chat`（`message` + `history` + 任意のアクション payload）
- 応答に含まれるもの:
  - `answer`（Markdown、末尾にドリルダウンリンク `[〇〇について詳しく聞く]`）
  - `next_actions`（「学習に戻る」「詳細を続ける」などをボタン化）
  - `support_mode` / `status_label` / `origin`
  - `content_grounding`（出所: 教材 / 別の資料 / モデル生成 — 下記）
  - `course_update.personal_layer`（`misconceptions_by_topic`, `chat_anchors`）
- **誤解検出**: 回答に訂正シグナルが含まれると個人レイヤーに記録され、トピックに誤解バッジが付く。
- **前提知識チェック**: 未習得の前提があれば逆質問（`mode="prerequisite_review"`）。
- **理解度チェック**: `POST .../topics/{tid}/check-question` で習得を確認し次トピックへ。

### 回答の出所表示（content_grounding）
回答バブル下部と出典タブのバナーに、回答が何に基づくかをバッジで表示します
（`app.js` の `GROUNDING_META` / `groundingBadge()`）。

| 値 | 表示 |
|---|---|
| `course_material` | 教材から回答 |
| `other_material` | 別の資料から回答 |
| `model_generated` | AI の一般知識（出典なし） |

`tier`（教員承認状況のバッジ）とは別軸です。判定ロジックは [RAG チャットフロー](../backend/rag-chat.md#4-出所判定content_grounding)。

裏側の流れの詳細は [RAG チャットフロー](../backend/rag-chat.md)。

---

## 3.5 ハンズフリー音声会話（カジュアル対話モード）

チャット入力欄の 🤖 ボタンで「気軽に話せる先生」との音声会話を開始します（`app.js`）。

1. MediaRecorder + WebAudio の無音検知（発話後 ~1.4 秒の沈黙）で発話を自動区切り
2. `POST /api/learning/voice/transcribe` で Whisper 文字起こし
3. `intent_mode='casual'` でチャット送信（雑談拒否・前提知識ゲート・誤解検出をバイパスし、
   短い会話調で応答。RAG 検索・tier・OutOfSourceGuard はそのまま → [RAG チャットフロー](../backend/rag-chat.md#3-インテントモードon_path--explore--casual--discuss)）
4. 応答を `POST /api/learning/voice/speak`（TTS, MP3）で再生（再生中はマイク停止、終了で聞き取り再開）
5. 応答の第 1 根拠チャンク（`sources[0].chunk_id`）を `GET .../source-chunk/{chunk_id}` で取得し、
   ボイスパネルに「いま話している題材」として教材表示

状態表示（「聞いています…」「文字起こし中…」「先生が話しています…」）付き。
interest_traces 記録と tension プレフィルタは通常どおり効きます（payload に `casual: true`）。

---

## 3.6 違和感（tension）ダイジェスト

対話ログから TensionMiningAgent（B層）が検出した「理解した上での引っかかり」の**候補**を、
進捗タブに「引っかかりの気配」カードとして提示します（`app.js` の `renderTensionDigestCard()`）。

- 取得: `GET /api/learning/courses/{id}/tension/digest`（本人のみ・candidate・最大 3 件。confidence 数値は見せない）
- 本人の操作: **そう、これ**（confirm → 自分の言葉で言い直すと articulated）/ **違う**（dismiss）/ あとで
- 確定した tension は「問いの軌跡」に昇格し、グラフ上の node/edge への接続（connect）もできる
- 教員・管理者は個別の tension 行にアクセスできない（k-匿名化された集計のみ）

検出パイプラインの詳細は [RAG チャットフロー](../backend/rag-chat.md#5-tension-プレフィルタと-tensionminingagentb層)。

---

## 3.7 チャットメッセージの書き直し・削除（truncate セマンティクス）

学習者は自分の入力メッセージを **書き直し（✏️）／以降削除（🗑）** できる（`app.js`。
id を持つ user バブルにのみボタンを出し、送信中は出さない）。どちらも
「そのメッセージ以降の往復を捨てる」**truncate セマンティクス**で統一されている。

- **書き直し**: ✏️ で本文を入力欄へ戻し `editingMessageId` を立てる。送信時に
  `LearningChatRequest.replace_message_id` を添えると、サーバーは
  `services.truncate_chat_and_supersede()` で正本履歴を当該メッセージ位置で切り詰めてから、
  新しい本文を同じ位置から通常フローで再処理する（誤解検出・tier・grounding は自然に再実行）。
  クライアント履歴も同位置で truncate する。トピック切替で編集状態は解除。
- **削除**: 🗑 で確認の上
  `DELETE /api/learning/courses/{id}/topics/{tid}/chat/messages/{message_id}` を呼ぶ
  （同じ truncate、再送なし。履歴が空になれば行削除）。
- **派生痕跡の後始末（P4）**: 取り除いたメッセージ由来の `interest_traces` は削除せず
  `status='superseded'` へ遷移し、以降の tension / anchor worker・ダイジェスト・
  問いの軌跡ビューから除外される。

---

## 3.8 「論文と話す」（discuss モード）

トピックを順にたどらず、コースのソース論文と最初から議論するモード
（`intent_mode='discuss'`。正本: [discussion_mode_design.md](discussion_mode_design.md)
DM1〜DM8 / 対話の進め方は
[discuss_dialogue_alignment_design.md](discuss_dialogue_alignment_design.md) DA1〜DA6）。
会話は予約疑似トピック `_discussion`（表示ラベル「論文との議論」）の上で行われ、
新テーブル・新チャットエンドポイントは持たない。

### 入口 — 二枚看板
サイドバー最上部に「**順番に学ぶ**」（現行の逐次型・無変更）と「**論文と議論**」を
同じ視覚的重みで並べたセグメントコントロール（`app.js` の `discuss-mode-switch`。
UI アンカー `sidebar.mode-sequential` / `sidebar.mode-discuss`）。入口はここに一本化されており、
チャット欄の常設リンク「もっと自由に話す」は重複のため廃止済み。discuss は
**寄り道（explore）ではない** — 復帰バナー・「寄り道」語彙は出さない。

### 検索スコープ 2 段（`discuss_scope`）
入力欄上部の discuss バー（`#discuss-scope-toggle`、UI アンカー `discuss.scope-toggle`）で

| 値 | 表示 | 検索対象 |
|---|---|---|
| `course_sources`（既定） | このコースのソース論文 | コースの `sources[]` が指す document のみ |
| `all_visible` | 閲覧できる周辺資料まで | 本人が閲覧可能な document 全体 |

を切り替える。**該当チャンクが 0 件でも他スコープへ無断で広げない**（DM1）。
選択状態そのものが出所の正直さの UI になる。判定・fail-closed の詳細は
[RAG チャットフロー](../backend/rag-chat.md#35-discuss-モードの分岐)。
音声会話（§3.5）は discuss では無効で、その旨を近傍に事実文で示す。

### 開幕画面 — 白紙のチャット欄で始めない
`GET /api/learning/courses/{id}/discuss/opening`（**LLM 0 回・読み取り専用**。
`core/discuss/opening.py`）を教材区画に描画する（`discuss.js`）。返るのは:

- `course_focus` — 教員が任意入力した「このコースで議論したいこと」（AI 生成なし）
- `documents[].thesis` — この論文が答えようとした問い / 中心命題 / 支持構造チップ /
  「別の見方」（出所ラベル「AI が提示した別の定式化（出典との対応は未確認）」付き）
- `documents[].backbone` — TheoryOperationGraph の main 層を theory stage 順に。
  ノードから対話を始められる
- `documents[].discussion_seeds` — 解析パイプラインが生成し**教員が承認した**
  「議論のきっかけ」（承認 0 件の document ではキー自体が付かない）
- `fragile_points` — 脆い箇所を**主語ごとに分けて**提示（「この論文が確かめていないこと」＝
  D層の未検証合意 / 「まだ確認できていないところ」＝解析が裏づけを取れていない箇所）

すべて事実文で、confidence・件数・網羅率などの数値は一切返らない（DM6）。
会話が始まると開幕カードは畳まれ、`開く / たたむ` トグル（`#discuss-opening-toggle`）で
開き直せる（対話ファースト）。開幕画面末尾には「最初の一手」の定型チップ（押すとその文が
そのまま送信される）、AI 応答のたびに分岐チップ「🔎 深掘り / 🧭 横展開」が付く。

### 対話 — 歩調合わせ（revoice / uptake）
discuss 専用のシステムプロンプト（`_get_discuss_system_prompt`）が発話タイプ別に応じる:

- **質問には即答**（出し惜しみせず、要約でなく完全な形で）
- **解釈・立場の表明には言い直し（revoice）から** — 解説で応じず、学習者の読みを
  言い直して確認 → 論文の主張との重なりとズレを事実として並べる → **どのズレから
  検討するかを学習者が選ぶ**
- **詰まりには一点だけの足場かけ**
- 回答末尾には、学習者の直前の発話を引用・組み込んだ固有の誘い（言い換え・予測・
  自己説明）か why / how / what-if の問い返しを**必ず 1 つ**添える（uptake。汎用の
  決まり文句は不可）

局面（係留 → ギャップの地図 → 共同検討）はプロンプトが自己管理し、**サーバに会話状態を
持たない**（migration 0）。

### 鏡面化（mirror） — 言い直しの視覚区別と訂正チップ

解釈表明・詰まりへの「言い直し」部分は、プロンプトが固定マーカー `〔鏡〕…〔/鏡〕` で
出力し、サーバ（`core/discuss/mirroring.py::extract_mirror`）が決定論抽出して
`LearningChatResponse.mirror = {text}`（optional）に正規化する（正本:
[seminar_brief_mirroring_design.md](seminar_brief_mirroring_design.md) §2/§3。
フロントに regex を書かせない — `extract_inline_actions` と同じ規律）。鏡文中の「」引用が
学習者の直前発話の逐語部分文字列でなければ鏡扱いせずマーカーだけ剥がして本文へ縮退
（再生成なし・P6）。フロント（`app.js` `renderMirrorBlock`）は AI 応答バブルの先頭に
`.mirror-block`（左ボーダー + 淡背景 + ラベル「AIによる言い直し」）で本人発話と視覚区別して
描画し（EX-3b⑤）、訂正チップ **[そのとおり] / [少し違う]** を添える。チップは入力欄への
文言プリフィル（「そのとおりです。」/「少し違います。」）+ フォーカスのみで **API は
呼ばない** — 送信は本人の通常発話として既存の tension/anchor digest の弁に流れる（§3
精査⑤・新しい確定経路を作らない）。鏡文は localStorage へ保存しない・そのまま再送信
しない（窓の外へ持ち出さない）。履歴に残ったレガシーマーカーは `renderAiContent` が
剥がして本文として表示する（鏡ブロックの再構成はしない＝許容劣化）。
「議論を終える」ボタン（`discuss.end`）／通常トピックへの切替／無活動 15 分
（ポーリングなし）で `discuss.js` が軽量パネルを出す。スキップ可・スキップしても痕跡は残る。

1. **今日の理解を自分の言葉で** — `POST .../discuss/reflection`（非LLM）。本人の一文を
   そのまま確定済み（`status='articulated'`）の tension として記録する。AI 候補を待たない
2. **今日話した内容を地図に置く（帰属カード）** — その日の tension / structure_anchor
   候補を confirm / dismiss。anchor カードは質問文の再掲ではなく「どこ（`anchor_label`）への
   **どの様相**（`doubt_type_label`）の引っかかりか」を提示し、違う様相へ 1 タップで
   訂正できるチップを添える。候補が 0 件でも「痕跡は残っており、後から『わたしの地図』で
   確認できます」の事実文を出す（接続（connect）は「わたしの地図」の既存導線から）
3. **持ち越し（LEAVE）** — §3.9
4. **再構成プローブ、あれば 1 問**（§7。出題対象の制約上「必ず 1 問」にはしない）

---

## 3.9 理解サイクル（Understanding Cycle）

「予測し → 差を見て → 理解を更新し → 問いを持ち越し → 時間をおいて再訪する」ループを
**opt-in の層**として既存機構の上に載せたもの（正本:
[understanding_cycle_design.md](understanding_cycle_design.md) UC1〜UC10。migration 0）。
既定の読書体験は変えない（UC1）・採点しない（UC2）・数値を見せない（UC9）・
セッション間に督促を作らない（UC4）。

- **精読モード**: discuss バーの `精読モード` トグル（既定 off。localStorage
  `eg_precision_reading:<courseId>`。サーバに学習者設定を持たない）。ON にすると
  「予想してから開く」が既定表示になるだけで、OFF でも小さなリンクから入れる。
- **OPEN**: 開幕画面の一枠（`discuss/opening` の optional キー `intention`）。
  初回は「この論文を、なぜ今開きましたか？」の任意一文、再訪時は**他の何よりも先に**
  前回の持ち越し問いを再提示して任意の再回答。記録は
  `POST /api/learning/courses/{id}/cycle/intention`（`role` =
  `opening_motive` / `carryover_question` / `revisit_answer`）。
- **ELICIT / DIFF**: タイトルだけを見て予想を書く →「あなたの予想」と「論文の骨格」
  （中心の問い ／ 中心命題）を**並置**し、任意の一行「予想と何が違いましたか？」を添える
  （判定・採点・一致度は出さない）。任意で
  「AI から問いをもらう」（`cycle_mode='elicit'`。解を提示せず問いを 1 つだけ返す）と
  「AI に違いの観点を出してもらう」（`cycle_mode='diff'`。断定しない候補提示）を使える。
  いずれも**既存チャットの 1 コールに相乗り**する内部値で、不正値は 422。
- **式スケール ELICIT**: R層カード内の選択式出題に `regime`（支配項・消える記号当て）と
  `next_step`（次の操作当て）を追加（§7）。出題は derivation_chain の近似・削減系
  operation の地点だけで、判定は従来どおり非LLM・決定論。
- **ANCHOR（軽量アンカー 4 ボタン）**: 教材区画下の常設ストリップ
  （`#quick-anchor-strip`、UI アンカー `material.quick-anchor`）とテキスト選択時の
  ポップオーバーに「気になる / まだ分からない / あとで戻る / 何かとつながりそう」。
  1 タップで確定（確認ダイアログを出さない）し、`POST .../cycle/anchor` が
  既存 structure_anchor の経路A（`learner_selected`・同期・非LLM）へ相乗りする。
- **LEAVE**: 着地画面の「次に持ち越すなら、どの問いにしますか？」。新規入力欄ではなく
  当日の本人の痕跡からの**選択リスト**（`GET .../cycle/landing-candidates`）＋自由入力 1 枠。
  旧持ち越しは行削除せず `superseded` へ遷移（UC6）。選ばなければ何も起きない。
- **REVISIT（帰り道の景色）**: 再回答の直後に、前回持ち越した時点の個人知識ネットワークと
  現在の導出結果の**構造差分**を肯定形の事実文（最大 3 件・数値なし）で返す
  （「あなたの地図に『…』が加わっています」など）。過去時点の不在は断言しない。

痕跡（`kind='intention'` / `'anchor_mark'`）は**本人のみ可視**で、問いの軌跡ビュー・
教員向け集約・tension / anchor の worker とダイジェストからは構造的に除外される（UC3）。

---

## 3.10 検証状態の事実併記（D層・SL層）

学習者側の D層／SL層は**読み取り専用**で、煽らず数値も出さない一行の事実として現れる。

- **出典タブの根拠カード**: そのチャンクに含まれる数式・claim について
  `GET /api/learning/courses/{id}/ledger/{target_type}/{target_id}` を引き、
  「この内容の検証スコープはまだ記帳されていません。」のような**一行の事実文**
  （＋記帳スコープ）を併記する。台帳未記帳（404）・取得失敗ならセクション自体を出さない
  （fail-closed。台帳を使っていないコースでは表示が一切変わらない）。記帳者 ID・
  生スコアは返らない。教材内の component チップから開く説明ポップアップにも
  同じ様式で併記する。
- **未検証の前提リスト**: 出典タブ末尾の「この分野の未検証の前提を見る」はプル型
  （開いた人だけが `GET .../open-assumptions` を読む）。疑義者の氏名は含まれない。
- **SL層（賭け金の台帳）**: 上記の台帳 API は「覆る条件」（反証条件）の事実文
  （種別・到達可能性の段階ラベル＋「教員の記帳」の出所ラベル）と支持線の一行も
  DTO に含めて返す（出典タブが併記する一行は `fact_line`）。
  discuss 開幕の「この論文が確かめていないこと」（§3.8）には、記帳状況が
  「何が起これば覆るかが記帳されている前提です。」「覆る条件はまだ定式化されていません。」
  のように一文で連結される。学習者からの反実仮想・晴れ間の閲覧・条件への異議は
  非スコープ（正本: [stakes_ledger_design.md](stakes_ledger_design.md) §8、
  D層の学習者導線は [doubt_layer_issues.md](doubt_layer_issues.md)）。

---

## 3.11 帰還の扉（書き置き・今日のあなたの言葉・欄外の印）

セッションの「間」を埋めずに、本人の再訪だけをトリガーとして前回の文脈へ戻す層
（正本: [return_door_design.md](return_door_design.md) RD1〜RD5。migration 不要）。
扉に出るのは**本人の言葉の逐語のみ**（RD1: AI 要約ゼロ）で、通知・督促・経過日数表示は
作らない（RD2）。書かなければ何も出ない（RD3）。すべて非LLM の読み時導出（RD4）・
件数や日数を数えない（RD5）。

- **書き置き（leave_note）**: discuss 着地画面の末尾に「未来の自分への書き置き」欄
  （任意・確認プロンプトなし）。保存は既存 `POST .../cycle/intention` に
  `role='leave_note'` で相乗り（`discuss.js`）。
- **「今日のあなたの言葉」トレイ**: 書き置き欄の脇の**既定畳み** `<details>`。開いたときに
  1回だけ `GET .../cycle/todays-words` を取得し、当日の本人発話（user ロール）の逐語だけを
  列挙する（「あなたの言葉」ラベル必須・AI 応答を混ぜない）。行タップでその一文が
  書き置き欄へ引用される。
- **扉（再入口インレイ `#return-door`）**: コースビュー最上部（教材区画より上）に
  `GET .../cycle/return-door` の結果を「書き置き → 持ち越しの問い → 最後に確定した
  引っかかり」の固定順・各1行で表示（`app.js`、描画は textContent のみ）。empty・取得失敗は
  インレイ自体を描画しない（fail-closed）。取得はコース読込時に1回のみ（ポーリング禁止）。
  ×で閉じるとそのセッション中は再表示しない（メモリ内フラグのみ・localStorage 非永続）。
  UI アンカーは `material.return-door`。
- **欄外の印 `#margin-marks`**: 教材区画の右余白に、本人の確定痕跡（`structure_anchor` を
  持つ確定済み問い + 確定 tension。既存 `GET .../interest-traces` 応答からの抽出）を
  「段差でつまずく人」の淡いアイコン（`app.js::createStumbleIcon` の SVG 線画。旧 ● の点は
  何の印か伝わらないため 2026-08-18 に図案化）で新しい順に最大12点表示。数は出さない・
  `map_excluded` の行は出さない。ホバー/タップで本人の text 逐語をツールチップ表示
  （帰属ラベルを添える近似 — 素材位置への正確な対応付けは v1 非スコープ）。表示トグルは
  教材ヘッダの「引っかかり」ボタン（同じアイコン付き）で、状態は
  localStorage `eg_margin_marks:<courseId>`（精読モードと同型の許容例外・既定 ON）。

ガードレールは `backend/tests/test_return_door_ui_static.py`（「あなたの言葉」ラベル・
経過日数語彙の禁止・textContent 描画・setInterval 禁止・アンカー4点セット）。

---

## 3.12 構造の降下路（足場ダイヤル・楽屋）

要素文脈パネル（⚓チップ・数式カードの「文脈を見る」）から、答えを配らずに段階的に
ヒントを開く「足場ダイヤル」と、集計に入らない私的な質問場所「楽屋」へ降りられる層
（正本: [structure_descent_design.md](structure_descent_design.md) SD1〜SD6。migration 不要）。

- **宣言された留保（SD6）**: 出し惜しみが働く opt-in 枠（降下路の枠・精読モードの
  Elicit・R層の出題カード）には「いまは答えを配らない対話です」の宣言一行を常設する
  （`app.js` の降下路枠 / `discuss.js` の予想欄 / `reconstruction.js` の出題カード）。
- **足場ダイヤル**: `GET .../descent/ladder` の段（想起プロンプト → stage 骨格事実文 →
  記号の定義・スコープ・表記ゆれ → 出典リビール）を「ヒントを一段引く」で 1 段ずつ開く。
  段を引くのは常に本人（SD1: 自動開放・誘導をしない）で、開示状況はクライアント側のみ
  （サーバへ送らない・記録しない）。`available:false` は枠ごと静かに消す。定義が無い
  記号は「論文中に明示的な定義が見つかりません」の事実文で正直に出す。
  産出欄（「自分の語で書いてみる」）は既定畳みで、どこにも送信・保存しない（SD3）。
- **楽屋（SD4）**: 「楽屋へ降りる」でパネルを落ち着いたトーンの楽屋に置き換え、
  「ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります」を先頭に常設。
  `GET .../descent/backstage-path` の記法の約束 → 記号の定義 → 前提概念の一般説明を
  順に表示し、質問は既存 learning_chat に `backstage: true` で送る（痕跡は楽屋扱い＝
  教員向け集約・digest・わたしの地図から除外）。「本流に戻る」は直前の DOM を
  そのまま復元する（再フェッチしない）。R層の既存「点検口: 記号を確認」
  （descend・DB 記録あり＝集計対象）とは別物（設計書 §6 精査記録③）。
- UI アンカーは `material.descent-ladder`（マニュアル節
  `student/02-student.md#descent-ladder`）。ガードレールは
  `backend/tests/test_descent_ui_static.py`（宣言一行の3箇所逐語・産出欄の無送信・
  誘導語彙 denylist・backstage フラグ・アンカー4点セット）。

---

## 4. インタラクティブ・レクチャーモード

論文チャンクを **セミナー形式の音声講義**に変換する没入型機能。「🎙️ レクチャー」ボタンで起動。

### シーケンス構築
- `GET /api/learning/lecture/courses/{id}/topics/{tid}/sequence`
- 返り値: `{segments: [{text, formulas: [{latex, spoken, is_display}], ...}]}`
- **適応的**: 習得済み概念のセグメントはスキップ、部分理解は要約版に変換（`lecture.py`、習得状態は `learner_mastered_concepts`）。

### 再生・カラオケ風ハイライト
- プレイヤーバー（前/再生/次、進捗バー、タイムスタンプ）
- 現在セグメント = 強調（青背景・左ボーダー）、過去 = 50% 不透明、未来 = 非表示
- 音声は `POST .../topics/{tid}/tts`（spoken_text → TTS、`lecture_audio_cache` にキャッシュ）

### 数式の扱い
- LaTeX 表示と、音声読み上げ用テキスト（例: `E=mc^2` → 「E イコール mc の二乗」）を自動生成（`lecture.py:generate_spoken_text_and_formulas`）。

### 中断チャット
- 「❓ 質問」で再生を一時停止し、別スレッドで質問 → コンテキストを保持して回答 → 「再開する」で続行。
- `POST .../topics/{tid}/interrupt`（再開位置を返す）。中断チャットの音声入力はブラウザの Web Speech API
  （ハンズフリー音声会話の Whisper 文字起こしとは別実装）。

教員側の原稿・音声の事前生成は [管理機能 — Lecture Studio](admin.md#4-lecture-studio) を参照。

---

## 5. 分野の地図（Field Atlas: オーバーレイ + ミニマップ）

学習中の箇所が**分野全体のどこに該当するか**を示す機能（CLAUDE.md「分野の地図」節が正本）。
宣言しない・煽らない・**踏破率を数値にしない**・リアルタイム LLM 生成をしない、が設計原則。

- **全画面オーバーレイ**: ヘッダの「地図」ボタン（`#atlas-btn`）や各導線カードから
  `AtlasOverlay.open()`（`atlas-overlay.js`）で開く。詳細パネルは `atlas-panel.js`、
  修正報告は `atlas-report.js`。
- **常設ミニマップ**（`atlas-minimap.js`）: 左パネル下・切手大。「いまここ + 状態ドット +
  霧ハッチ」のみで、数値・ラベル・凡例は描かない。更新はトピック遷移とオーバーレイ閉時のみ
  （ポーリング禁止）。
- **導線**（`atlas-cues.js`）: ①トピック完了直後 ②章末 ③寄り道復帰（戻った位置を
  ハイライト）④初回ログインの一度きり自動表示。①〜③はカード提示に留め自動では開かない。
  内部計測は `POST /api/learning/atlas/cues/events` / `GET .../cues/state`（数値をユーザーに
  見せる UI は無い）。
- **データ取得**: `GET /api/atlas`（`atlas-data.js`。状態判定はサーバー側のみ）。
  骨格の無いコース・カートリッジでは 404 → 地図領域ごと非表示（fail-closed。フィクスチャへの
  自動退避はしない）。

---

## 6. わたしの地図（個人知識ネットワーク）

本人の確定痕跡（tension / 帰属付き問い / 再構成成功 / connect した橋）から**毎回決定論的に
導出される**個人の知識ネットワーク（正本: `docs/features/personal_knowledge_network_design.md`）。
保存物ではなく導出・**本人のみ可視**・candidate は数えない・数値を見せない。

- **最上位パネル**（`personal-map-home.js`、ヘッダ「わたしの地図」ボタン `#my-map-btn`）:
  「いまの地図 / 問いからの旅 / 振り返り」の 3 タブ。データソースは正本 API
  `GET /api/me/personal-network`（+ `GET /api/me/personal-network/journey?node_id=`）のみ。
  常設注記「この地図はあなたにだけ表示されます。成績評価には使用されません。」
- **コースビュー**（`personal-map.js`）: Field Atlas オーバーレイに自分の記録を重ねる表示 +
  旅カード。コーススコープの旅 API は
  `GET /api/learning/courses/{id}/personal-network(...)`。別コースに同一アンカーの兄弟が
  あれば `cross_course_hint`（「以前の学習につながる道があります」）だけを返し、本人が
  開いたときのみコース横断版へ差し替える。
- **訂正操作**: `POST /api/learning/traces/{trace_id}/map-exclude` / `.../map-restore`
  （`payload.map_excluded` の状態遷移のみ。dismiss とは独立・行削除しない）。問いの軌跡の
  除外済み項目には「地図に戻す」チップが付く。

---

## 6.5 わたしの記録（主権台帳v1）

本人の学習痕跡（`interest_traces`）**すべて**を系統ごとに一望できる読み取り専用の台帳
（正本: [trace_registry_sovereignty_ledger_design.md](trace_registry_sovereignty_ledger_design.md)
TR1〜TR7）。ヘッダの「わたしの記録」ボタン（`#my-records-btn`、`my-records.js` /
`window.MyRecords`）からオーバーレイパネルで開く（ポーリング禁止・開いたときのみフェッチ）。

- 確定した引っかかりだけでなく、dismiss したまま保持されている候補・書き直しで
  superseded になった問い・学習の意図・軽量アンカーも消えずに並ぶ（P4 の一望）。
  各行に status ラベルと**公表状態の事実文**（「あなた以外には表示されません」など）が
  添えられる。件数バッジ・数値は出さない（TR6）。
- **本人のみ可視・読み取り専用**（TR4）。API は `GET /api/me/records` と
  `GET /api/me/records/export` のみで、台帳から行削除・確定・却下は行わない
  （訂正操作は既存画面のまま）。
- **持ち出し**: 「持ち出す」ボタンで自分の痕跡一式を JSON でダウンロードできる。
  一覧表示が最新分に省略されていても持ち出しは常に全件。
- **封印は準備中の正直表示**: 封印ボタンは置かず、「封印の仕組みは、封印したという事実を
  残したまま内容を読めなくする形で設計中です」の事実文を常設する
  （偽のボタン・先取りの約束を出さない。TR5）。
- 常設注記「この記録はあなたにだけ表示されます。成績評価には使用されません。」
  （わたしの地図と同文）。

---

## 7. 再構成ループ（Reconstruction Loop）

学習者に理論の再構成（予測 / 言い直し）をさせ、A層の `theory_claims` を答えキーとして
**非LLM の構造照合**でズレを事実として返す閉ループ（R層。正本:
`docs/features/reconstruction_loop_design.md`）。

- **導線**: トピック学習ビュー下部の「🧩 再構成に挑戦」ボタン（`reconstruction.js`、
  `window.Reconstruction`）。自動では開かない。カードには「この問いは AI が自動生成した
  ものです」を常に明示。
- **フロー**: ELICIT（出題 `GET .../topics/{tid}/reconstruction/next`）→ CAPTURE（提出
  `POST /api/learning/reconstruction/{item_id}/submit` → DIFF + 出典リビール）→
  SELF-CHECK（必須 `POST .../{recon_id}/self-check`。「そのとおり / 納得できない /
  判定が間違っている」）→ 再挑戦（`POST .../{item_id}/revise`）または記号葉への降下
  （`POST .../{recon_id}/descend`）。
- 判定は「食い違いの可能性」という仮説文体で表示し、権威は出典のリビールに置く。
  数値スコア・正答率は学習者に見せない（サーバー側でも返さない）。

---

## 8. 認証フロー

1. ログイン `POST /api/auth/login` → JWT 取得
2. `localStorage["eg_token"]` に保存
3. 以降のリクエストに `Authorization: Bearer {token}`

→ ロール・権限の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

[← 動的スキーマ進化](../pipeline/schema-evolution.md) ｜ 次へ: [管理機能 →](admin.md)
