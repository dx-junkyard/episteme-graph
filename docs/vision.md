# episteme-graph ビジョンと思想（正本）

**作成日:** 2026-08-13
**位置づけ:** 本書は、これまで `knowledge_network_vision.md`・各層の設計書・CLAUDE.md に
分散していた **システム全体のビジョン・認識論的スタンス・横断設計原則** を1枚に統合した正本である。

- 個別機能の仕様・テーブル定義・API は各設計書（`docs/features/*.md`）が正本のまま。
- 各層の不変条項（P1〜P7 / KN-1〜4 / DM / UC / SL …）の**逐条の正本**も各設計書に残る。
  本書はそれらが反復する**共通の思想**に名前を付け、出典を索引する。
- 機能の一覧・migration 対応は [レイヤー索引表](architecture/layer_registry.md) を参照。
- 本書の思想が**どのような現状認識・課題・gap を経て UX に展開されるか**（外部
  ステークホルダー向けの因果整理）は [サービスデザイン](service_design.md) を参照。

---

## §1 ミッション

**大学院生（研究の入り口に立つ学習者）の学習プロセスを支援する知識グラフ管理システム。**

研究者・大学院生が直面する2つの課題 —

1. **散在する先行研究の統合** — 論文は読めるが、分野の中でどこに位置づくか・他の論文と
   どの部品を共有しているかが見えない
2. **前提知識の体系的習得** — 「式変形は追えるが物理が見えない」まま論文の表面をなぞってしまう

— に対し、PDF 論文を**構造化された知識**（概念・主張・数式・導出・理論操作グラフ）へ変換し、
その構造を土台に**対話型・産出型の学習体験**を提供する。

ゴールは「答えを配る」ことではない。**学習者が自分の理解ネットワークを、論文と分野の
公共ネットワークに重ねながら育てていける環境**を作ることである。

---

## §2 知識観 — このシステムが前提とする認識論

システムの設計判断はバラバラの UX 選好ではなく、一貫した知識観から導かれている。

### 2.1 知識は近傍との関係でのみ成り立つ — 全体の完成形は無い

知識ネットワークは「全体で一つの完成形」があるのではなく、近傍との関係でのみ成り立つ
小さなネットワークが幾重にも重なり、全体として堅牢な「一つに**見える**」状態を目指す
（[knowledge_network_vision.md](features/knowledge_network_vision.md) §1-6）。

- だから **踏破率・カバー率を数値にしない**（Field Atlas）。
- だから **全体グラフを一枚に描く画面を作らない**（KN-1「神の視点を作らない」。
  すべてのネットワーク表示は論文起点・パーツ起点・学習者起点の egocentric）。
- だから 分からない場所へは数値やランキングではなく「**旅**」（journey: 論文ローカルグラフ →
  同一性リンク → 共通部品ハブ → 分野骨格 → 本人の別ノード、の事実文による経路提示）で向かう。

### 2.2 検証と合意は別の軸 — 検証を単一ブールにしない

D層（認識的地位台帳）は「みんなが認めている」（合意の強さ）と「確かめられている」
（検証の強さ）をデータ構造レベルで分離した（[doubt_layer_issues.md](features/doubt_layer_issues.md)）。
検証は真偽の一bitではなく **`verification_scopes` の配列**（どの条件・領域・精度で
確かめられたか）で持ち、全称検証（「証明済み」）を構造的に禁止する。

SL層（賭け金の台帳）はこれを反証側へ双対拡張した: 「何が起これば覆るか」（反証条件）・
「覆れば何処まで届くか」（観測反実仮想）・「どこが一点吊りか」(独立支持経路) ・
「どこで確かめられていないか」（晴れ間）（[stakes_ledger_design.md](features/stakes_ledger_design.md)）。

### 2.3 スコープの空欄は失敗ではなく発見

「検証スコープが空欄」「地図に配置できない論文」「コースと骨格の対応がゼロ」は、
エラー表示や警告色の対象ではなく**正常な状態であり発見**である
（D層 / [knowledge_landscape_design.md](features/knowledge_landscape_design.md) LS「配置不能は信号」 /
[atlas_binding_lifecycle_design.md](features/atlas_binding_lifecycle_design.md) AB1「一致ゼロは正常」）。
反復する「置けなかった」だけが候補として浮上し、地図を育てる入力になる
（[category_gap_candidates_design.md](features/category_gap_candidates_design.md)）。

### 2.4 コーパスは分野ではない — 閉世界の正直さ

システムが知っているのは高々数十〜数百論文のコーパスであり、分野そのものではない。
検証記録の不在について言えるのは「**このコーパスの中では**検証記録がありません」だけで、
「この分野では未検証」「誰も検証していない」という語彙は構造的に禁止する（SL1）。
晴れ間は発見の候補地であって発見ではない — 空の穴だと確かめるのは望遠鏡ではなく人間の仕事。

### 2.5 表現の多様性は情報 — 正規化は追加であって置換ではない

論文ごと・分野ごとの表記の違いを潰すと、その論文との接続が悪くなる。だから:

- `theory_claims` は原文 `text` と `normalized_text` を**両方**保持する。
- 「実は同じもの」は**マージではなくリンク**（`element_identity_links`）で表現し、
  各論文側のインスタンスは何も書き換えない（KN-2）。共通部品ハブ（L層 `library_entries`）が
  「この分野ではこう呼ぶ / この論文ではこう書く」の対応表を持つ側になる。
- 標準化判定は LLM 単独主張を幻覚とみなし、LLM 事前知識 + 分野ライブラリ凍結版 +
  コーパス内反復の**三角測量**で行う。コーパス内で反復するのに外部標準が無い
  `emerging_common` こそ本システムの発見的価値の在り処。

### 2.6 知識は時間の中で変化する生き物

式・概念は完成品ではなく、適用範囲を持ち、破れ、派生する。V層（共有物のバージョン管理）の
不変スナップショット、`theory_review_events` の全監査記録、versioned な地図骨格は、
「共同体がいつ何を受け入れたか」を後年たどれる一次史料としての側面を持つ
（[vision_expansion_proposals_2026-08.md](features/vision_expansion_proposals_2026-08.md) 提案4「時間レンズ」が将来形）。

---

## §3 学習観 — 理解は産出から始まる

### 3.1 ELICIT-first — 開示の前に学習者自身の産出を挟む

2026-08 の7分野専門家パネル討論で、脳科学（予測誤差が符号化をゲートする）・学習科学
（pretesting effect）・宇宙物理（研究者は式を自分で導いてから紙面と照合する）・ゲームデザイン
（地図を描かせる）の4分野が**独立に同じ構造**を提案した（独立収束は実装価値の強い証拠）。

- R層（再構成ループ）: ELICIT → CAPTURE → DIFF → REVEAL → SELF-CHECK → REVISE/DESCEND
  （[reconstruction_loop_design.md](features/reconstruction_loop_design.md)）
- 理解サイクル: **OPEN → ELICIT → DIFF → REVEAL → UPDATE → ANCHOR → LEAVE → REVISIT**
  を一級の体験とし、AI をその補助レイヤーとして再配置する
  （[understanding_cycle_design.md](features/understanding_cycle_design.md)）

ただし **opt-in**（UC1）: 予測を挟む読み方は「精読モード」の明示トグルのみ。通覧という
読書実践に摩擦税をかけない。自分で選んだ縛りは没入を生み、押し付けられた縛りは離脱を生む。

### 3.2 DIFF は採点しない — 権威は常に出典

構造照合の判定は「食い違いの可能性」の**仮説文体**で提示し、正解/不正解・点数・正答率を
出さない（UC2）。判定を authoritative に見せず、権威は常に出典リビール（原文）に置く。
自由記述の予測は判定せず**並置**のみ（機械にできない判定を偽装しない）。

### 3.3 違和感と問いは理解の証 — そして構造に帰属する

- tension（違和感）は「未理解の穴」ではなく「**理解した上での引っかかり**」であり、
  それを生成するのは人間。LLM は候補を出すだけで、本人の確定（そう、これ / 違う）を
  経てのみ痕跡になる（B層 TensionMining, P1）。
- 問いは質問文の保存ではなく「提示された情報構造の**どこに・どう**引っかかったか」
  （anchor × doubt_type）として記録する（構造帰属型の問い記録）。
- 誤解は除去すべきバグではなく「スコープを誤って拡張された局所的に正しい信念」であり、
  洗練の素材（knowledge in pieces）。D層の scope 語彙は学習者の理解にもそのまま適用できる。

### 3.4 セッションの間は埋めない

問いを持ち越して終え（LEAVE）、次回その問いから再開する（REVISIT）。その間、
**システムは何もしない**（UC4）: 督促・プッシュ通知・連続日数・未消化バッジ・忘却曲線を
作らない。「間」を埋めないこと自体が設計である（オフラインの統合に処理を委ねる）。

### 3.5 学習を演技化させない

バッジ・ランキング・進捗率・的中数を出さない（P7 / UC9）。学習者に見せるのは
数値ではなく段階ラベルと事実文。教員に対しても同じ（多くの数値は教員にも見せない —
評価装置化・監視化の防止）。

### 3.6 沈黙適応をしない — 学習者モデルの推定で挙動を変えない

能力・理解度・スキーマ距離の推定で提示内容・提示順・対話方針を暗黙に変えない（UC5）。
初回利用でも過去データからの推定入口分岐を作らない（UC7）。個人化はすべて
「本人の産出物を本人に見せる」形か、本人が選ぶ提案型でのみ行う。
見えない並べ替えは「誰も宣言せず誰も異議を申し立てられない正典形成」である。

---

## §4 AIの役割 — 「AIは候補まで、確定は人間」

全レイヤーで最も強く反復される原則。AI（LLM）の出力は**常に candidate** であり、
確定という行為は人間（学習者本人 or 教員）に留保される。

| 側面 | 規約 |
|---|---|
| 出力の地位 | 常に `status='candidate'`（tension / anchor / 前提 / 疑義スコープ / 同一性リンク / 配置 / 説明 / 図解析 / 反証条件 … すべて）。AI 出力で `source_backed` や confirmed を自動付与しない |
| 根拠 | evidence-based: 逐語 `evidence_quote`（verbatim 検査＝捏造ガード）+ `reason` + `confidence` を必ず付与。根拠が無ければ生成しない（無理に配置・創作しない） |
| 文体 | 断定しない。「〜の可能性があります」の仮説文体。AIに疑わせない・「反証不可能」と断定させない（SL2） |
| 幻覚対策 | 三角測量（LLM 単独主張は `unknown`）・閉世界語彙の固定・スキーマ検証 + repair（2回失敗は情報を落とさず `unclassified` で保持） |
| 配置 | 同期パスに LLM を入れない（P6/UC8）。体験の骨格は非LLM・決定論で完結し、LLM は非同期の候補生成か明示操作のみ。LLM 失敗時は degraded 縮退で体験を止めない |
| 役割 | チャット（回答装置）を主役にせず、**理解行為を主役に**。AI は Elicit（問いを引き出す）/ Diff（差分候補）/ Explain（要求時の説明）/ Reflect（言語化の補助）に分解されて配置される |
| コスト | すべての LLM 呼び出しに日次上限（CostGate）と U層計測（usage_context）。モデル選択は M層の単一正本（`llm_policy`） |

「モック」や「とりあえず自動確定」を許さないこの流儀は、初期の EPISTEME_MOCK 台帳
（MOCKS.md）が candidate/status 遷移パターンへ発展的に統合されたものと読める。

---

## §5 人間の役割 — 教員・学習者・共同体

### 5.1 教員は「確定の弁」であり監査役

生成は AI が大量に行い、**共有された知識になる直前に必ず人間の弁がある**:
claim 承認・説明の査読（C層）・同一性リンク確定（W層）・骨格の凍結（Atlas）・
配置の確認（ランドスケープ）・カテゴリギャップの採用・反証条件の確定（SL層）。
弁の操作はすべて `theory_review_events` に**帰属付きで監査記帳**される（匿名の疑義・
匿名の承認は存在しない）。承認と疑義は対等の一級市民である（C層 ⇄ D層）。

### 5.2 学習者は主権者 — 監視しない・評価に使わない

- 学習者の痕跡（問い・違和感・予測・意図）は**本人のみ可視**（PN-1）。
- 教員へ渡るのは **k-匿名集約のみ**（k=3 の正本は `core/privacy.py`、n<3 セル非表示、
  人数はレンジ表示）。個別履歴の閲覧経路を作らない。**評価利用禁止**（P3）。
- 学習者の重ね合わせから浮かぶのは教育的知識（どこで橋を架けるか・どこでつまづくか）で
  あって、ドメイン知識ではない。コーパス / 専門家 / 学習者の3系統の重ね合わせを
  混ぜない（KN-4）。学習者の素朴な連想をドメイン知識候補に自動昇格させない。
- 削除ではなく状態遷移: dismiss・書き直し・撤回も履歴として保持される（P4）。
  本人による「地図には反映しない」（map_exclude）のような**訂正主権**を提供する。

### 5.3 共同体 — 重なるが同じではない理解の並存

同じ論文の同じパーツへの各人の理解は「重なるが同じではない」。だから一つのコンポーネントに
複数の説明バージョン（標準 + 教員の独自解釈）が**並存**し、マージされない（C層）。
承認は説明バージョン単位で、重みは段階ラベル（「専門家3名が承認」）であり数値スコアではない。
引用は帰属付きで記録され、versioned な共有（V層）が「所有者の一方的な更新・削除」から
消費側を保護する（発行版へのピン留め・削除猶予・adopt の明示同意）。

---

## §6 横断設計原則カタログ

各層の不変条項（P1〜P7, KN-1〜4, DM1〜8, LS1〜10, RR1〜7, FG1〜9, OA1〜8, W1〜9,
U1〜8, M1〜10, G1〜8, UC1〜10, SL1〜10, DA, DO, AB, LE, PN …）は、実質的に以下の
**14の共通原則**の各層への具体化である。新しい層を設計するときはまずこの表に照らすこと。

| # | 原則 | 内容 | 代表的な出典 |
|---|---|---|---|
| 1 | **AIは候補まで・確定は人間** | LLM 出力は常に candidate。確定は本人（学習者の痕跡）か教員（共有物）。自動確定経路を作らない | P1, KN-3, W2, SL2/SL3 |
| 2 | **evidence-based** | 逐語 evidence_quote（verbatim 検査）+ reason + confidence。根拠のない生成をしない | 全Agent共通ルール, OA, FG8 |
| 3 | **情報を落とさない** | 不明は unknown/deferred で保持。dismiss・撤回は状態遷移。行削除 API・`DELETE FROM` を作らない | P4, UC6, SL5 |
| 4 | **数値を見せない** | スコア・件数・進捗率・confidence 生値を出さない（多くは教員にも）。段階ラベル・レンジ・事実文に翻訳する | P7, LS「数値非表示」, SL4, UC9 |
| 5 | **監視しない** | 学習者痕跡は本人のみ可視。教員へは k-匿名（k=3 正本 `core/privacy.py`）集約のみ。評価利用禁止 | P3, KN-4, PN-1 |
| 6 | **egocentric のみ** | 全体を一枚に描く神の視点の画面・API・ダッシュボードを作らない。表示は常に視点起点、移動は「旅」 | KN-1, SL6 |
| 7 | **リンクであってマージではない** | 局所表現を書き換えない。同一性・正規化は「追加」（並存・対応表・リンク）で表現する | KN-2, text/normalized_text 並存 |
| 8 | **出所の正直さ** | 回答の grounding（教材/別資料/モデル生成）、AI推定/教員確認済みのラベル、閉世界語彙（「このコーパスの中では」）、一括承認の来歴を偽らない | content_grounding, LS, SL1, RR3 |
| 9 | **同期パスに LLM を入れない** | 体験の骨格は非LLM・決定論・読み時導出。LLM は非同期候補生成か明示操作。失敗時は degraded 縮退 | P6, UC8, SL9 |
| 10 | **完了フラグを持たない** | 導出できる状態は保存しない。To-Do・status・候補キューは毎回サーバ状態から決定論導出（実施すれば自動消滅） | G1, PN-2, 状態通知基盤 S1 |
| 11 | **fail-closed** | 権限・可視性・地図表示は「閉じて壊れる」。判定はサーバ側、フロント表示を信頼しない。骨格が無ければ領域ごと非表示 | 可視性フィルタ, G3, Atlas fail-closed |
| 12 | **押し付けない** | opt-in・自動で開かない・督促しない・ポーリングしない・沈黙適応しない。セッション間は何もしない | UC1/UC4/UC5, G4, Atlas 導線抑制 |
| 13 | **層は積層し、下層を改変しない** | 新層は既存層を「読む側」として積む（A層非改変が原型）。拡張は kind/語彙の追加・optional フィールド・新モジュールに限る | A層非改変, W1, UC10, SL10 |
| 14 | **監査必須・帰属必須** | 状態変更（承認・疑義・確定・共有・凍結）は `theory_review_events` に帰属付きで記帳。匿名の弁は無い | D層, AUDIT_ENTITY_* カタログ |

実装面ではこれらを**ガードレールテスト**（`backend/tests/test_*_guardrails.py`）が構造的に
守る文化が確立している: 禁止語彙 grep・削除 API 不在・core の FastAPI 非 import・
数値キー非漏洩・k=3 正本参照・網羅テスト（アンカー3点セット等）。

---

## §7 ビジョンの進化（地層の年表）

| 時期 | 地層 | 主な出来事 |
|---|---|---|
| 〜2026-03 | **創業期** | A層パイプライン・RAGチャット・レクチャーモード・コース分離（Priority A） |
| 2026-04〜06 | **学習者体験と共同体** | B層（関心痕跡 020 / tension 022 / anchor 025）・C層承認共有（021）・Field Atlas（023〜028）・D層（029〜033）で P1〜P7 系の原則群が確立 |
| 2026-07 前半 | **制度化と基盤** | Copilot(034)・R層(036)・V層(037)・状態通知(038)・G層(039)・L層(041/042)・U層(043)・アーキテクチャ整理 Tier 0〜3（Neo4j 撤去・正本モジュール群・migration 一本化） |
| 2026-07-13 | **知識ネットワークビジョン** | KN-1〜4 確定。W層(048〜050)・個人知識ネットワーク（わたしの地図・旅）・標準化判定・橋候補集約として即実装 |
| 2026-07 後半 | **対話の深化** | discuss モード（論文と話す・開幕/着地・歩調合わせ）・help_kb・二層説明(055/056)・M層(061)・図スタジオ(063) |
| 2026-08 前半 | **位置づけと弁** | 知識ランドスケープ(065)・カテゴリギャップ候補(066)・リリース前確認 — 「配置できない」を信号として地図を育てる流路の整備 |
| 2026-08-13 | **理解サイクルと反証可能性** | 7分野パネル討論 → 理解サイクル UC1〜10（migration 0）→ SL層 賭け金の台帳(067)。学習観が「回答の受領」から「予測・産出・持ち越し・再訪」へ、認識観が「検証の記帳」から「反証可能性の航法」へ拡張 |

このプロジェクトの機能追加の型は「**討論/調査（発散）→ UX 検討 → 専用設計書（不変条項の
明文化）→ 実装 → 設計書末尾に実装記録を追記**」であり、設計書は実装後も正本として生きる。

---

## §8 機能群マップ — 思想がどこに実装されているか

アルファベット層（A〜W）は追加順の実装単位であり、利用者から見た機能の大枠は
次の **7群** に整理できる。詳細な migration・実装場所の対応は
[レイヤー索引表](architecture/layer_registry.md) を参照。

### 群1: 知の構造化 — 論文を構造にする（§2 の実装）

PDF → 文書構造 → evidence → atomic claim → 数式意味・導出チェーン → 中心命題 →
理論コンポーネント → 理論操作グラフ、の**A層パイプライン**（named 29 ステージ・
LLM-first + 決定論 validator/repair）。図・装置は **L層**（画像抽出・vision 解析・
反証型反復照合・分野別ナレッジライブラリ）。ドメイン語彙は**カートリッジ**が注入。

→ [pipeline/overview.md](pipeline/overview.md) / [pipeline/agents.md](pipeline/agents.md) /
[pipeline/theory-graph.md](pipeline/theory-graph.md) /
[image_pipeline_knowledge_library_design.md](features/image_pipeline_knowledge_library_design.md)

### 群2: 学びの対話と講義 — 構造の上で話す・聴く（§3/§4）

RAG チャット（tier / content_grounding / 可視性 fail-closed）・カジュアル/ハンズフリー音声・
**discuss モード**（論文と最初から議論する: 開幕素材・着地・歩調合わせ・生成的問い返し）・
**レクチャー**（スライド=表示と読み上げの同期最小単位・トピック音声・原稿スタジオ）・
**コーパス回遊**（コースの外の「論文の海」・コース無しの論文議論・地図の端）・
学習画面のインスペクト/ホバー係留。

→ [features/learning.md](features/learning.md) / [backend/rag-chat.md](backend/rag-chat.md) /
[discussion_mode_design.md](features/discussion_mode_design.md) /
[lecture_slide_sync_design.md](features/lecture_slide_sync_design.md) /
[corpus_roaming_design.md](features/corpus_roaming_design.md)

### 群3: 理解の産出と痕跡 — 学習者が作り、残し、再訪する（§3/§5.2）

**R層 再構成ループ**（構造照合＝仮説、権威＝出典）・**理解サイクル**（OPEN〜REVISIT、
intention/carryover 痕跡・帰り道の景色）・tension / 構造帰属の問い・
**個人知識ネットワーク**（本人確定痕跡からの毎回導出、「わたしの地図」、旅、橋候補の
k-匿名集約）・教材内の要素文脈（チップ・ホバー・文脈 API）・**主権台帳「わたしの記録」**
（痕跡 kind 登録簿と本人だけの一覧・持ち出し）・**帰還の扉**（帰還の三段）・
**構造の降下路**（足場ダイヤル・楽屋）。

→ [reconstruction_loop_design.md](features/reconstruction_loop_design.md) /
[understanding_cycle_design.md](features/understanding_cycle_design.md) /
[structure-anchored-questions.md](features/structure-anchored-questions.md) /
[personal_knowledge_network_design.md](features/personal_knowledge_network_design.md) /
[trace_registry_sovereignty_ledger_design.md](features/trace_registry_sovereignty_ledger_design.md) /
[return_door_design.md](features/return_door_design.md) /
[structure_descent_design.md](features/structure_descent_design.md)

### 群4: 地図と位置づけ — 分野の中の「いまここ」（§2.1/§2.3）

**Field Atlas**（骨格の生成→教員レビュー→凍結、コース⇄地図バインディング、ミニマップ、
修正報告、ドメインライフサイクル）・**知識ランドスケープ**（論文を多観点で骨格に配置、
AI 配置は inferred 止まり）・**カテゴリギャップ候補**（「置けなかった」の反復から地図を
育てる、共有骨格への流路に人間の弁）・**VA層 ベクトル係留**（アンカーのプロトタイプ埋め込み・
別名レジストリ・配置プレフィルタ・着地予測。cosine 生値は見せず段階ラベルのみ）・
**RE追補 関係表示**（辺候補の教員レビューと学習者向け「推定の糸」— 地形は人間・関係は離散の辺）・
**リリース前の確認**（提示されたものが出る、を教員の1操作で承認とみなす）。

→ [field_atlas_overlay_spec.md](features/field_atlas_overlay_spec.md) ほか field_atlas_*.md /
[knowledge_landscape_design.md](features/knowledge_landscape_design.md) /
[category_gap_candidates_design.md](features/category_gap_candidates_design.md) /
[atlas_vector_anchoring_design.md](features/atlas_vector_anchoring_design.md) /
[atlas_relation_edges_design.md](features/atlas_relation_edges_design.md) /
[release_review_flow_design.md](features/release_review_flow_design.md)

### 群5: 疑いと検証の制度 — 認識的地位の台帳（§2.2/§2.4）

**D層**（epistemic ledger・暗黙前提マイニング・前提の地図・疑義・検証提案・反実仮想）・
**SL層**（反証条件レジストリ・観測の反実仮想・独立支持経路・晴れ間 — 出口は
verification_proposals に一本化）・**ゼミ前ブリーフと鏡面化**（論文の賭け金の read-only 合成ビュー）。
学習者へは読み取り専用の事実併記のみ。

→ [doubt_layer_issues.md](features/doubt_layer_issues.md) /
[stakes_ledger_design.md](features/stakes_ledger_design.md) /
[seminar_brief_mirroring_design.md](features/seminar_brief_mirroring_design.md)

### 群6: 教員の検討と共同体の合意（§5.1/§5.3）

**C層**（説明バージョン並存・承認・引用）・**W層 要素検討ワークスペース**（任意要素の
内訳・4+1レンズ・対話的検討・候補注釈のコミットルーティング・同一性リンク・標準化判定）・
**二層説明**（generic/contextual・レビューキュー）・**L層ライブラリ**（教員共同財への
人間による昇格のみ）・**教材図スタジオ**（AI 対話 SVG 生成・sanitizer が唯一の入口）・
**グラフ対話レビュー**（教材起点でグラフを見ながら承認・却下する。AI 応答から承認 API を
呼ぶ経路は作らない）と**グラフの論文層**（フレームに論文の章・式・図表を肉付けする読み時射影）・
**教員の弁と計器**（負荷順トリアージ・静かな計器）・要素インベントリ。

→ [endorsement-sharing.md](features/endorsement-sharing.md) /
[element_deliberation_workspace_design.md](features/element_deliberation_workspace_design.md) /
[hierarchical_context_explanation_design.md](features/hierarchical_context_explanation_design.md) /
[teaching_figure_studio_design.md](features/teaching_figure_studio_design.md) /
[graph_dialogue_review_design.md](features/graph_dialogue_review_design.md) /
[graph_paper_layer_design.md](features/graph_paper_layer_design.md) /
[teacher_triage_instruments_design.md](features/teacher_triage_instruments_design.md)

### 群7: 運営基盤 — 弁と観測装置のインフラ（§4/§6）

認可・Visibility・グループ共有 / **アカウントライフサイクル管理**（停止・リセット・削除は
状態遷移と墓標化で行い users 行を物理 DELETE しない）/ **V層** 共有物のバージョン管理 /
状態通知基盤（導出 status + 遷移イベント + 統合インボックス）/ **G層** 次にやること
（完了フラグなしの導出 To-Do）/ **Admin Copilot**（capability registry を単一の真実源とする
説明・道案内・代行）/ **help_kb**（マニュアルの AI 知識源化・インスペクトモード）/
**U層** 使用量計測 / **M層** 場面別モデル選択 / discuss 観測基盤 /
**コーパスの成長ループ**（URL 指定の教材取得 = 許可リスト + SSRF ガード、arXiv 論文
ディスカバリーと論文レーダー = 発見は自動・取り込みは教員の明示承認のみ）。

→ [auth-visibility.md](features/auth-visibility.md) /
[account_lifecycle_management_design.md](features/account_lifecycle_management_design.md) /
[shared_versioning_design.md](features/shared_versioning_design.md) /
[status_notification_design.md](features/status_notification_design.md) /
[guidance_layer_design.md](features/guidance_layer_design.md) /
[admin_assistant_design.md](features/admin_assistant_design.md) /
[manual_help_kb_design.md](features/manual_help_kb_design.md) /
[llm_usage_metering_design.md](features/llm_usage_metering_design.md) /
[llm_model_selection_design.md](features/llm_model_selection_design.md) /
[url_material_upload_design.md](features/url_material_upload_design.md) /
[paper_discovery_design.md](features/paper_discovery_design.md) /
[paper_radar_design.md](features/paper_radar_design.md)

---

## §9 まだ実装されていないビジョン

| 構想 | 内容 | 状態 |
|---|---|---|
| E層（Exposition Layer） | 段階的翻訳レイヤー | 設計のみ（唯一の未実装層）。着手時は migration の再採番（**採番前に `ls backend/db/` で空き番号を確認**。2026-09-03 時点は 077 以降）と既存横断基盤への接続追補が必要 — [exposition_layer_design.md](features/exposition_layer_design.md) |
| 時間レンズ（Chronicle Lens） | 式・概念の伝記を歩く W層第5レンズ + journey 時間方向 hop | 提案（[vision_expansion_proposals_2026-08.md](features/vision_expansion_proposals_2026-08.md) 提案4）。着手時は専用設計書 |
| 橋の生態系 | 転移プローブ・独立再発見・つながりの弁による同一性リンクの自己持続ループ | 提案（同 提案5）。安全装置3点（判断前非開示・2名確定・来歴刻印）が前提 |
| 静かな開通と欲望の小径 | 匿名・非同期・数値ゼロの「他者の気配」の環境表現 | 提案（同 提案6）。討論で最も懸念が集中、5条件のガードレール確定が先 |
| 予測ファースト読解の残り | 論文骨格予測の照合提示・地図スケール白地図 | 式スケールのみ理解サイクル Phase 2 で実装済み |
| 世代間徒弟制アーカイブ | 理解の相転移記録を本人同意で遺贈する制度 | moonshot（討論記録の付録B） |

これらに着手する際も、§6 の 14 原則と「討論 → 設計書 → 実装 → 実装記録」の型を踏むこと。

---

## §10 本書の運用

- 新しい層・機能を追加したら: ①専用設計書（不変条項付き）②
  [レイヤー索引表](architecture/layer_registry.md) への行追加 ③ CLAUDE.md の節追加 ④
  本書 §8 の該当機能群への1行追記、を同時に行う。
- ドキュメント運用規約（機能解説の同時更新・設計書の状態ヘッダ・想定 migration 番号を書かない・
  リポジトリ外正本の禁止・カウント記法）の正本は
  [開発運用チェックリスト](development_checklist.md) §5 — 機械検証つき
  （`backend/tests/test_docs_registry_guardrails.py`）。
- 本書 §6 の原則に**例外を作る**設計をする場合は、例外であることと理由を設計書に明記する
  （黙って逸脱しない）。
- 本書は「なぜ」の正本であり、「どうやって」（テーブル・API・画面）は書かない。
  実装詳細が書きたくなったら、それは各設計書に置く。
