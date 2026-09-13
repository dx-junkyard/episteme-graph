> **調査記録（2026-09-10）**: ビジョン×UXギャップ調査「六つのレンズ」のレンズ6（立ち上がりと分野）詳細報告。統合と優先付けは [../vision_ux_gap_six_lenses_2026-09-10.md](../vision_ux_gap_six_lenses_2026-09-10.md)。読み取り専用調査で、file:line は HEAD d5ac3cb 時点。「推測」と明記した箇所は未確認。

# 立ち上がり（cold start）・分野の拡張・研究室運用の1年 — ギャップ調査

観点担当: lens-coldstart / 基準は `docs/vision.md` §1〜§6 のみ（§7 以降・§9 は未読）
調査日: 2026-09-10 ｜ 読み取り専用（リポジトリ無変更）

凡例: **事実** = コードまたは設定ファイルで確認したもの（`path:line`）。**推測** = コードから
演繹したが実データ・実機で確かめていないもの。行番号は調査時点の作業ツリー。

---

## 0. 要旨（先に結論）

新しい研究室が `.env.example` をコピーして起動した瞬間、システムは **素粒子物理の研究室として
立ち上がる**。

- `.env.example:263` に `EPISTEME_DEFAULT_CARTRIDGE_ID=particle_physics` が入っている（**事実**）。
- 教材アップロード API に分野を指定する引数が無く（`backend/api/routes/admin.py:551-557`、
  `_accept_material_source` が `process_material_background` へ `cartridge_id` を位置引数
  `None` で渡す `backend/api/routes/admin.py:525-529`）、パイプラインは env を直読みして
  cartridge を決める（`backend/core/document_pipeline/orchestrator.py:425-426`）。したがって
  **凝縮系だろうと経済学だろうと、取り込んだ全論文が `document_analysis_runs.cartridge_id =
  "particle_physics"` として記録される**（**事実**。列は `backend/db/015_document_pipeline.sql:99`）。
- 起動時シードは素粒子物理の骨格・ライブラリと宇宙物理の骨格を DB に入れる
  （`backend/api/main.py:152-177`）。これらは学習者の「論文の海」のドメインボタンに**無条件で
  並ぶ**（`backend/core/corpus_view.py:180-213` が凍結骨格を持つ active ドメインを全部返し、
  `frontend/public/js/corpus-sea.js:264-278` が `has_visible_papers=false` でもボタンを消さない）。
- 論文配置ステージは **active な凍結ドメイン全部**を配置候補として LLM に見せる
  （`backend/core/landscape/builder.py:231-266`）。凝縮系の1本目は「素粒子物理には置けません」
  「宇宙物理には置けません」を毎回生む。

一方で **A層 agent 自体は分野中立に作られている**（12 agent すべて
`if not cartridge_id: return None` で綺麗に縮退する。例
`src/episteme_graph/agents/component_assembly/agent.py:263-270`）。つまり
**「cartridge 無しでも単独動作する」という設計原則は agent 層では守られているのに、
その経路が既定設定のせいで実運用では踏まれない**。これが本観点で見つかった最大のねじれである。

そして分野を育てる側では、骨格（atlas domain）は UI から作れるのに、**分野の語彙
（ontology / component_types / relation_types / validation_rules）は UI から一切作れない**
（`backend/api/routes/cartridges.py` は GET のみ、`:45-101`）。教員は「地図は描けるが語彙は
書けない」状態に置かれる。

---

## 1. 分野依存の棚卸し表

### 1-a. 機構 × カートリッジ依存の種類 × 無し/別分野での挙動

| 機構 | 依存の種類 | cartridge 無し（None）のとき | particle_physics へ縮退したときに混入するもの | 根拠 |
|---|---|---|---|---|
| **パイプライン全体の cartridge 決定** | 単なるキー（全 stage へ配布） | env 未設定なら `None` のまま全 stage へ | **env 既定が particle_physics なので常に縮退する**。結果は `document_analysis_runs.cartridge_id` に永続化され、コース→分野導出・landscape の cartridge 解決に伝播 | `orchestrator.py:425-426` / `.env.example:263` / `db/015_document_pipeline.sql:99` |
| `claim_qualification` | **検証（hard error）** + プロンプト語彙注入 | 許可 claim_type が core fallback のみ → 語彙外は `severity="error"` → repair の LLM 再呼び出しが増える | concept_types 8 種（Theory / Observable / CorrectionTerm / Operator / Parameter / DecayProcess …）が許可集合に加算。**notation_patterns（`R_[A-Za-z0-9*Λ_]+` / `C_[A-Za-z0-9]+`）がプロンプトに JSON で直接注入される** | `validator.py:190-196, 313-329` / `prompt.py:212-214` |
| `equation_semantics` | 語彙注入 + 検証（warning のみ） | `_check_cartridge_terms` が空を返す（無害） | notation_patterns がプロンプト注入（`prompt.py:192-194`）。経済学の `C_t`（消費）が Wilson 係数の型に誘導されうる（**推測**） | `validator.py:537-551` / `prompt.py:192-194` |
| `component_assembly` | 語彙注入 + 検証（許可語彙の拡張） | core 語彙のみ。**`required_fields` 検査が丸ごと消える**（検証が緩む方向） | `Domain*Component` / `Paper*Component` 11 種 + `ASSUMES` / `APPROXIMATES` / `CORRECTS` / `UNCERTAIN_DUE_TO` 等 15 種が許可語彙に加算 | `validator.py:926-929` / `input_builder.py:93, 105-126, 324-330` |
| `component_graph` | 検証（edge 型の拡張） | `VALID_EDGE_TYPES` のみで検証 | relation_types 15 種が edge 語彙に加算 | `component_graph/validator.py:123-130` |
| `apparatus_semantics` | 語彙注入 + **L層 retrieval キー** | `build_cartridge_hints` が `{}`。validator は cartridge を一切見ない | component_types 30 件 + aliases がプロンプトへ。さらに **`domain_key=cartridge_id` で L層ライブラリ retrieval が走り、`cartridges/particle_physics/library/apparatus_seed.json` の凍結エントリが vision プロンプトに候補として入る**（生物物理の顕微鏡図に素粒子検出器の候補が提示される） | `input_builder.py:152-157` / `prompt.py:235-242` / `orchestrator.py:3455-3477` |
| `landscape_placement` | 語彙注入のみ | 「分野語彙」節をプロンプトに出さない | alias（HQET / Standard Model）が補助語彙として末尾に入る | `landscape_placement/prompt.py:190-193` |
| **`concept_normalizer`（claim の concepts 正規化）** | **語彙による置換** | — | **cartridge_id を渡さない呼び出しが2箇所あり、必ず particle_physics の alias 表で正規化される**。`merged["name"] = concept.canonical` で**元の名前を上書き**する。分野を問わず `theory_claims.concepts` に永続化 | `concept_normalizer.py:60, 78-86, 88-100` / 呼び出し `api/routes/theory_components.py:1084, 2985` |
| `descent/engine.py`（楽屋の notation_patterns 段） | 語彙注入 | **明示 cartridge が空なら `load_cartridge` を呼ばず `[]`**（是正済み・docstring に理由明記） | 縮退しない | `core/descent/engine.py:412-430` |
| `learning.py::_cartridge_content_terms` | 語彙 | **空なら呼ばない**（docstring に「`load_cartridge(None)` は既定へ縮退する」と明記） | 縮退しない | `api/routes/learning.py:823-830` |
| `atlas_state.resolve_course_cartridge` | 単なるキー | — | **最後に必ず `_fallback_cartridge_id()` = particle_physics を返す**（`atlas_state.py:850`）。`atlas_view` と `personal_graph/provisional` は `course_has_skeleton_anchor` ゲートで 404 に畳むが、**`api/routes/learning.py:2273-2285` はゲート無しで particle_physics 骨格に topic を照合する**。`match_topic_to_concept` が None を返す限り実害は無駄な照合だけ（**推測**） | `atlas_state.py:824-862` / `learning.py:2273-2285` |
| `core/library`（L層） | 単なるキー + **厳密フィルタ** | シードされないだけ | `search.py:67` が `e.domain_key = :domain_key` の**完全一致**。particle_physics でスタンプされた run は particle_physics のライブラリしか引かない（＝上の apparatus 混入の実体） | `core/library/search.py:44-70` / `seed.py:76-79` |
| `core/atlas_store` | 単なるキー | `load_learner_skeleton` は空 key で None（fail-soft） | 縮退しない。cartridge が無い分野は `atlas_domains/<key>/skeleton.yaml` へフォールバック | `atlas_store.py:93-115, 195` |
| `core/paper_discovery/vocab.py` | 検索語彙の供給 | 供給元1系統が例外なら fail-soft で抜けるだけ | `domain_key` を particle_physics で呼べば HQET 等が検索チップ候補に出る。ただし**採否は教員**なので自動購読はされない | `vocab.py:74-90, 186-228` |

**結論（Q1）:** 「cartridge 無しでも単独動作する」は **A層 agent の実装としては真、体験としては
偽**。既定 env が particle_physics を指すため cartridge-less 経路が踏まれず、しかも
`concept_normalizer` だけは**引数ガードが抜けていて**、他の呼び出し元（descent / learning /
vocab）が全部入れている「空なら呼ばない」を持たない。混入の量そのものは小さい
（`cartridges/particle_physics/ontology.json` は alias 2 件・notation 2 件・concept_type 8 件）が、
**混入する場所が「教員が承認して DB に確定する claim の概念名」**であるため、原則7
（リンクであってマージではない）と §2.5（正規化は追加であって置換ではない）に正面から当たる。

### 1-b. コーパス規模しきい値の表

| 機構 | 何本から動くか | 定数・判定 | 満たさないとき学習者/教員に見えるもの |
|---|---|---|---|
| landscape 配置（論文を地図に置く） | **1本目から**。ただし**凍結骨格が1つ必要** | `builder.py:436, 46-48` | 骨格なし → `skipped_reason="no_frozen_skeleton"`、手動 propose は 422「凍結済みの分野マップ骨格がありません。」（`routes/landscape.py:67`） |
| カテゴリギャップ候補（地図を育てる） | **distinct 2 論文**（信号自体は1本目から保存） | `core/atlas_gaps/schema.py:137` / `store.py:587` | **沈黙**。「いまレビューする候補はありません。」（`admin.js:5819`） |
| 関係（辺）候補の共起由来 | **distinct 2 論文** | `core/atlas_edges/schema.py:35` / `derive.py:232` | 沈黙。全滅時「いまレビューする関係の候補はありません。」（`admin.js:6268`） |
| 暗黙前提マイニング 経路A | **2 論文 or 2 導出**（OR） | `doubt/assumption_mining/schema.py:13-14, 39-44` | 沈黙（ログのみ） |
| コーパス横断監査 経路B | **コーパス 3 論文**（＋依存元 2 論文） | `assumption_mining/schema.py:17` / `corpus_audit.py:80, 138` | 200 + `{"skipped": true}` を返すが、**UI が skipped を読まないので教員には無言**（`doubt-atlas.js:1118`。**推測**を含む） |
| 標準化判定の `emerging_common` | **distinct 2 論文** | `standardization/input_builder.py:72` / `aggregate.py:78-80` | 沈黙ではなく**別ラベル**（1本なら `novel` か `unknown`） |
| 発見レンズA「地図の薄い領域」 | 逆向き。配置 distinct **1本以下**が「薄い」 | `config.py:608-611` / `complement.py:142, 169` | 薄いノードゼロなら事実文（`NOTE_NO_THIN_NODES`） |
| 発見レンズC「基盤論文」 | 引用元シード **2本以上**、実行にシード1本以上 | `config.py:613-617` / `foundation.py:239-244` | シード0 → `available:false` + 事実文 |
| 関連度ランキング / レーダー帯 | **1本目から**（その1本が重心） | `paper_discovery/ranking.py:201-222, 440-441` | 0本 → `available:false` +「関連度の基準にできる取り込み済み論文がまだありません。」 |
| 面②コーパス横断レンズ（W層） | 自論文以外に**1件**近傍ヒット | `deliberation/positioning.py:768, 901-903` | 区画ごと非表示 |
| 個人地図の「旅」 | 論文数ではなく **confirmed 同一性リンク1件 + active な L層エントリ** | `personal_graph/journey.py:255-260, 760-771` | 事実文「ここから先はまだ道が無い（この要素の同一性リンクが未確定）」 |
| 論文の海 | 論文数のしきい値なし。ゲートは凍結骨格と可視 document | `corpus_view.py:358, 423` / `routes/corpus.py:49, 101` | 骨格なし → 404。論文0 →「この分野で閲覧できる論文はまだありません。」 |
| 教員向け k-匿名集約（関心ダッシュボード等） | **学習者 3 人**（k=3、`core/privacy.py` が正本） | `api/services.py:2943` | n<3 セル非表示 |

**「2 論文」が独立に4箇所で定義されている**（atlas_gaps / atlas_edges / assumption_mining /
standardization）。**推測**: 体感の立ち上がり順は 1本目＝個人地図の暫定ノードのみ →
2本目＝ギャップ候補・辺候補・前提マイニング・`emerging_common` が一斉点灯 →
3本目＝コーパス横断監査、となる。

---

## 2. 問いへの回答（要点）

**Q2 最初の1本 / 「空欄は発見」は1論文で成立するか。**
1本目でも成立する面はある（**事実**）: 論文直付け discuss（`_doc:` センチネル）、D層台帳、SL層の
反証条件・支持経路・晴れ間、R層再構成、W層の内訳・文脈レンズ、個人地図の暫定ノード。
しかし **教員が見る「候補キュー」系はすべて 2 論文から**で、1本目の画面は
「いまレビューする候補はありません。」という**空文字列に近い縮退**になる（`admin.js:5819, 6268`）。
これは §2.3 の「空欄は発見」ではなく「何も無い」に読める。閉世界の事実文
（「このコーパスの中では検証記録がありません」`core/doubt/support_paths.py:51` など）は
1本でも正しく成立する良い実装だが、**それが置かれているのは D層 / SL層の画面だけ**で、
1本目の教員が最初に開く教材管理・分野の地図タブには出ない。

**Q3 分野を育てる手順は UI だけで完結するか。**

| 対象 | UI から作れるか | 根拠 |
|---|---|---|
| atlas ドメイン + 骨格 | **○**（分野の地図タブ「新しい分野マップを作る」→ 管理ID・分野名・説明で LLM 生成 → レビュー → 凍結） | `frontend/public/admin.html:153-165` / `admin.js:6758-6779` / `api/routes/atlas.py:331-433` |
| 骨格の別名（VA層） | **○**（gap カードから「別名として登録」） | `core/atlas_vectors/store.py:413, 471` |
| L層ライブラリのエントリ | **○**（domain_key は自由入力テキスト） | `admin.js:1772` / `api/routes/library.py` |
| **cartridge の語彙**（ontology aliases / notation_patterns / concept_types / component_types / relation_types / validation_rules） | **×（ファイルデプロイのみ）** | `api/routes/cartridges.py:45-101` が **GET だけ**。書き込みルート無し。`backend/Dockerfile` が `cartridges/` を COPY するのでコンテナ再ビルドが要る |
| 骨格生成時の `concept_vocabulary` | **×**（API は受け取るが UI フォームに欄が無い） | `admin.html:153-165` は key / name / desc の3欄のみ |

**三重管理（Q3 後半）: 事実として存在する。** 分野の語彙は3つの場所に分かれ、
**相互に還流していない**:

1. `cartridges/<id>/ontology.json` の `aliases` / `notation_patterns` — ファイル正本。
   → `concept_normalizer`（claim の概念名を置換）と agent プロンプトへ流れる。
2. `atlas_skeletons` の region / concept ラベル — DB 正本・UI 編集可。
   → 地図表示・キーフレーズ供給へ流れる。
3. `atlas_anchor_aliases`（教員が UI で確定した別名） — DB 正本・UI 編集可。
   → **消費者は2つだけ**: アンカーのプロトタイプ埋め込み（`atlas_vectors/builder.py:263`）と
   ディスカバリーのキーフレーズ（`paper_discovery/vocab.py:122`）。
   **`concept_normalizer` にも agent プロンプトにも一切届かない**（**事実**。全消費者を grep 済み）。

つまり **教員が UI で確定した別名は、論文解析の語彙には一生反映されない**。反映されるのは
ファイルに書かれた素粒子物理の別名だけである。

**Q4 研究室の1年に「年次の構造」はあるか。→ 無い（事実）。**
- `cohort` / `semester` / `academic_year` / `term` に相当する概念・列・パラメータは
  コードベース全体に存在しない（grep 済み。`cohort` は k-匿名集計の「母集団サイズ」の意味で
  `api/services.py:2815` に出るのみ）。
- `learning_states` は `UNIQUE (user_id, course_id)`（`backend/db/011_course_states_separation.sql:32`）
  なので、**同じ学生が同じコースを翌年もう一度受け直すことができない**。
- 教員向け集約に**時間窓が無い**（`api/services.py:2815-2830` の `WHERE course_id = :cid` のみ）。
  同じコースを3年使えば、3世代分の痕跡が1つの分母に混ざり続ける。今年のゼミの信号は
  過去2年に薄められる。
- **コースの複製が無い**（grep で duplicate / clone 系ルート無し）。年度ごとに教材構成を変えたい
  場合、同じコース行を書き換えるか、コースビルダーで最初から作り直すかの二択。
- 卒業は **アカウント削除（墓標化）しか無い**。削除すると `interest_traces` は purge される
  （`core/account_lifecycle.py:116-118`）。「卒業したが記録は残す」「在籍しないが自分の地図は
  見られる」という状態が無い。本人の持ち出しは「わたしの記録」の JSON export
  （`api/routes/my_records.py:57-77`）だけで、**取り込み口は無い**。
- 学生アカウントは**教員が1件ずつ、パスワードを手で決めて作る**
  （`api/routes/admin.py:3737-3771`）。自己登録エンドポイントは存在しない
  （`api/routes/auth.py` は login と me のみ）。招待コードは**グループ参加**であって
  アカウント作成ではない（`api/routes/groups.py:517`）。新入生12人なら12回この操作をし、
  平文パスワードを配る。パスワードリセットは SYSTEM_ADMIN 専用。
- 骨格の版更新だけは年次に耐える設計がある（`freeze-impact` / `course.atlas_binding_stale` /
  `atlas_skeleton_frozen` 通知）。**年次の構造でまともに存在するのは地図の版だけ**。

**Q5 前提知識（コーパス外）への接続はあるか。→ 事実上ない（事実）。**
`check_prerequisites` は topic の `prerequisites` 文字列を、**同じコース内の topic.title と
小文字完全一致**でしか照合しない（`api/services.py:2168-2178`）。習得判定は
「その topic にチャット履歴があるか」（`:2157-2166`）。したがって:

- **コーパス外の教科書的前提（「グリーン関数」「測度論」）は topic として存在しないので、
  永久に「未習得」と判定され続ける**。学習者は毎回ゲートに当たり、「理解している」と答えて
  スキップする運用に落ちる（**推測**だが、判定ロジックからほぼ確実）。
- 「いいえ」を選ぶと `LEARNING_ADVICE` 分岐に入り、`_generate_learning_advice_response` の
  **RAG を通らない LLM 説明**が返る（`api/routes/learning.py:2840-2881`）。この分岐の
  `LearningChatResponse` には `content_grounding` も `sources` も設定されない
  （`api/schemas.py:382-385` のコメントが「意図分類・前提知識確認等のシステム応答では
  None のまま」と明記）。フロントは `content_grounding` が無ければバッジを描かない
  （`app.js:1737-1739`）。
- つまり **§1 の課題2（式変形は追えるが物理が見えない）に応える唯一の出口が、
  出所ラベルの付かない場所になっている**。原則8「出所の正直さ」の穴。
- なお `documents.doc_type` には既に `'textbook'` という値が入る（`_accept_material_source` は
  全アップロードを `'textbook'` で登録している `admin.py:502`）が、**この型が前提知識の解決に
  使われている箇所は無い**。

**Q6 複数分野の同居。→ 名前空間は「同一と宣言されている」が、実装は3系統に割れている（事実）。**
- 宣言: `db/042_knowledge_library.sql:18`「domain_key は cartridge_id と同一名前空間（atlas と同じ）」。
- 実装: (a) atlas ドメインは DB に自由に作れる（cartridge ファイル不要）
  (b) cartridge はファイルのみ (c) library の domain_key は自由入力テキストで、
  **atlas ドメインとの整合検査が無い**（`admin.js:1772` は datalist 補完だけ）。
  → 教員が `condensed_matter` と `condensed-matter` を打ち分けると、地図とライブラリが
  静かに分離する。
- 分野横断（同じ数学が別分野に出る）は **表現できない**: `library_entries.domain_key` は
  単数の `TEXT NOT NULL`（`db/042:18`）で、L層検索は全経路が
  `domain_key = :domain_key` の完全一致（`core/library/search.py:67, 165`）。
  フーリエ変換が凝縮系と機械学習の両方に出ても、共通部品ハブは2つの独立エントリになり、
  同一性リンク（`element_identity_links`）は片方にしか刺さらない。旅（journey）の
  [3] L層ハブ経由の hop は分野の境界で必ず止まる。これは §2.1（近傍の重なり）が最も
  効くはずの場所である。
- group（可視性）と domain（分野）の対応は**まったく無い**。1インスタンスに凝縮系ラボと
  生物物理ラボが同居すると、両者の学習者に両方のドメインボタンが出る
  （`corpus-sea.js:264-278`）。

---

## 3. 提案

### 提案1: 教材の入口に「分野」を置き、既定の素粒子物理を外す

- **近づける vision**: §2.4（コーパスは分野ではない）/ 原則8（出所の正直さ）/ 原則11
  （fail-closed）/ §6.2 候補「入口の正直さ」
- **現状（証拠）**: `.env.example:263` が `particle_physics` を出荷。upload API に分野引数なし
  （`admin.py:551-557`）、`_accept_material_source` が位置引数 `None` で渡す（`admin.py:525-529`）、
  orchestrator が env 直読み（`orchestrator.py:425-426`）。`ReanalyzeRequest`（`admin.py:773-777`）
  にも分野が無い。フロントの分野セレクタはライブラリ編集と骨格エディタにしか無い。
  一方 A層 agent は `None` で綺麗に縮退する（`component_assembly/agent.py:263-270` 他11件）。
- **なぜ体験を損なうか**: 教員は「この論文は凝縮系だ」と知っているのに、それをシステムに
  伝える場所が無い。結果、システムは全論文を素粒子物理と信じ、その信念が
  `document_analysis_runs.cartridge_id` に永続化されて、地図の導出・L層 retrieval・
  landscape の cartridge 解決へ静かに伝播する。**「分からない」と言えるはずの場所で
  システムが黙って断定している**ので、原則8 が入口で破れている。しかも A層は正しく
  「分からない」を扱える実装になっているのに、既定設定がその経路を塞いでいる。
- **機能のかたち**: アップロードゾーンの「解析モデル: … [変更]」の1行サマリと同じ形で
  「分野: 指定しない [変更]」を1行足す。選択肢は既存の
  `GET /api/admin/cartridges` + `GET /api/admin/atlas/domains` の合成（admin.js:6647-6690 に
  同じ合成コードが既にある）。値は `_accept_material_source` → `process_material_background`
  → `run_document_pipeline(cartridge_id=...)` へ通し、既存列
  `document_analysis_runs.cartridge_id`（`db/015:99`）に入るだけ。**migration 不要**。
  `ReanalyzeRequest` にも同名フィールドを足し、前回 run から継承（`models` と同じ流儀）。
  **「指定しない」を一級市民にする**（cartridge を読まない = A層の分野中立経路が実際に走る）。
  `.env.example` の既定を空にし、コメントで「素粒子物理の検証用。他分野では空にする」と書く。
  LLM 呼び出しなし。
- **14原則との照合**: 原則8（出所の正直さ）を入口で回復。原則11（fail-closed）＝
  不明なら語彙を注入しない。原則13（層は積層）＝A層非改変、通す引数を1本足すだけ。
  原則12（押し付けない）＝必須入力にしない（既定は「指定しない」）。
- **規模感**: S

### 提案2: 概念正規化を分野に係留し、教員の別名を還流させる

- **近づける vision**: §2.5（正規化は追加であって置換ではない）/ 原則7（リンクであってマージ
  ではない）/ 原則1（確定は人間 — 教員が確定した別名が効かないのは弁の空振り）
- **現状（証拠）**: `normalize_concepts()` は `api/routes/theory_components.py:1084` と `:2985` から
  **cartridge_id 引数なし**で呼ばれ、`concept_normalizer.py:60` の `load_cartridge(None)` が
  particle_physics へ縮退する。`normalize_concepts` は `merged["name"] = concept.canonical`
  （`concept_normalizer.py:98`）で**元の名前を上書き**する。他の呼び出し元は全部
  「空なら呼ばない」ガードを持つ（`descent/engine.py:416-418`、`learning.py:826-828`）のに、
  claim 保存経路だけ抜けている。さらに教員が UI で確定した `atlas_anchor_aliases` は
  `atlas_vectors/builder.py:263` と `paper_discovery/vocab.py:122` の2箇所しか読まず、
  正規化にも agent プロンプトにも届かない（全消費者を grep で確認）。
- **なぜ体験を損なうか**: 「同じもの」の判断は本システムで最も重い人間の弁のひとつ（W層の
  同一性リンク、L層の共通部品、C層の承認）。その弁の手前で、**誰も宣言していない語彙表が
  概念名を書き換えている**。しかも書き換えているのはファイル同梱の他分野の表で、教員が
  UI で登録した別名は無視される。教員から見ると「別名を登録したのに何も変わらない」
  ＝弁が空回りする体験になり、原則1 の「確定は人間」が実質的に成立しない。
- **機能のかたち**: (a) `normalize_concepts(items, cartridge_id=...)` の呼び出し2箇所に
  document の cartridge_id を渡し、**空なら正規化しない**（`raw` のまま返す。
  descent / learning と同じガード）。(b) `_alias_index` の供給源に
  `atlas_vectors.store.confirmed_aliases_by_node(session, domain)` を第2供給源として足し、
  出所を `normalization_source="teacher_alias"` で区別する（`ontology_alias` と混ぜない＝
  原則8）。(c) `merged["name"]` の上書きをやめ、`raw` を残して `canonical` を併記に留める
  （§2.5 の「置換ではなく追加」を文字どおりに）。**migration 不要**、LLM 呼び出しなし。
  正本は `core/concept_normalizer.py` のまま（新モジュールを作らない）。
- **14原則との照合**: 原則7・原則3（情報を落とさない＝raw を残す）・原則8。
  原則13＝既存モジュールへの引数追加と供給源1本追加のみ。
- **規模感**: S

### 提案3: 分野語彙の共同編集（cartridge を DB draft/freeze に載せる）

- **近づける vision**: §2.5 / §5.1（教員は確定の弁）/ §5.3（共同体）/ 原則13
- **現状（証拠）**: `api/routes/cartridges.py:45-101` は GET のみ。ontology / component_types /
  relation_types / validation_rules の書き込み API は存在しない。骨格生成フォームには
  `concept_vocabulary` 欄が無い（`admin.html:153-165` は key/name/desc の3欄）。したがって
  UI だけで作った新分野は**語彙が永久に空**で、`claim_qualification` の許可 claim_type は
  core fallback のみ、`component_assembly` の `required_fields` 検査は消えたまま
  （`component_assembly/validator.py:926-929`）走り続ける。
- **なぜ体験を損なうか**: 分野を育てる作業のうち、地図（形）は教員に開かれているのに、
  語彙（意味）は開発者にしか開かれていない。素粒子物理だけが「検証が効き、語彙が効く」
  一等分野で、他分野は永久に二等分野になる。これは「分野が増えるほど価値が増す」という
  §2.1 の期待と逆向きで、実際には**分野を増やすほど検証の薄い成果物が積み上がる**。
  さらに教員は語彙の不足を UI から直せないので、原則1 の「十分な情報・拒否権を持つ人間」
  という確定条件も満たしにくい。
- **機能のかたち**: help_kb と atlas が既に使っている `core/revision_store.py` の
  draft/freeze + revision 楽観ロックをそのまま流用し、**分野語彙の draft/凍結**を作る。
  v1 の対象は効果の大きい3つに絞る: `aliases` / `notation_patterns` / `component_types`
  （validation_rules は非スコープ）。**パイプラインが読むのは凍結版のみ**（L層と同じ規律）。
  ファイル同梱（`cartridges/<id>/*.json`）は起動時シードのまま残し、DB があれば DB が勝つ
  （atlas_store と同型）。UI は分野の地図タブに「この分野の語彙」区画を1つ足す（新タブを
  作らない）。読み手は `core/cartridges.py::load_cartridge` の内側で DB → ファイルの順に
  解決させ、**呼び出し側は一切変えない**。migration 1本（`domain_vocabularies`）。
  LLM は任意（骨格生成と同じく「AI に下書きさせて教員が凍結」を再利用できるが、v1 は
  手入力だけでも成立させる）。
- **14原則との照合**: 原則1（AI が書いても凍結は教員）・原則3（凍結版は append-only、
  削除 API を作らない）・原則13（load_cartridge の内側だけを差し替え、agent を触らない）・
  原則14（凍結を `AUDIT_ENTITY_*` に記帳）。
- **規模感**: M〜L

### 提案4: 1本目から成立する「1論文の空欄」画面

- **近づける vision**: §2.3（空欄は発見）/ §2.4（閉世界の正直さ）/ 原則10（完了フラグを
  持たない＝毎回導出）
- **現状（証拠）**: 教員向けの候補キューはすべて 2 論文しきい値（`atlas_gaps/schema.py:137`、
  `atlas_edges/schema.py:35`、`assumption_mining/schema.py:13-17`、
  `standardization/input_builder.py:72`）。1本目の画面は
  「いまレビューする候補はありません。」（`admin.js:5819, 6072, 6268, 6499`）。
  corpus_audit の skip は API が `{"skipped": true}` を返すのに UI が読まないため**無言**
  （`routes/doubt.py:1471-1482` / `doubt-atlas.js:1118`。UI 側は**推測**を含む）。
  一方 1論文で成立する事実文は既に実装済み（`doubt/support_paths.py:51`、
  `personal_graph/nearby.py:135`、`paper_discovery/complement.py:64`、
  `doubt/seminar_brief.py` の4区画）が、**それらはコース単位・D層画面に閉じている**。
- **なぜ体験を損なうか**: 「空欄は発見」は本システムの認識論の核だが、**1本目の教員が
  最初に触る画面ではそれが「候補なし」という不在の文字列にしか見えない**。導入初日の
  印象は「まだ何も動いていないシステム」になり、2本目・3本目まで価値が繰り延べられる。
  実際には1本でも言えることが山ほどある（この論文が確かめていないこと・支持が1本吊りの
  箇所・検証記録の無い前提）のに、それを見る導線が無い。
- **機能のかたち**: 既存 `core/doubt/seminar_brief.py`（4区画: 脆い前提 / 一点吊りの支持 /
  晴れ間 / 学習者からの引き継ぎ）を**document スコープでも呼べるようにし**、教材管理の
  行メニューに「この論文で分かっていること・確かめられていないこと」を追加する
  （📡 レーダー・グラフレビューと同じ位置）。第4区画は1本目には無いので、区画ごと出さない。
  併せて、2論文しきい値で沈黙する区画の空文言を **不在の宣言から閉世界の事実文へ**差し替える
  （例:「いまは1つの論文からの信号だけです。同じ主題が別の論文でも置けなかったときに、
  ここに候補として上がります。」— 件数・残り本数は書かない＝原則4）。corpus_audit の
  `skipped` も同じ文体で表示する。**非LLM・migration 不要**。
- **14原則との照合**: 原則4（数値を見せない — 「あと1本」と書かない）・原則8（閉世界語彙 SL1
  を継承）・原則9（同期・非LLM）・原則10（毎回導出）。
- **規模感**: M

### 提案5: 年次の構造 — 開講期・再受講・卒業

- **近づける vision**: §2.6（知識は時間の中で変化する）/ §5.2（学習者は主権者）/
  §5.4（三領域の分離）/ 原則5（監視しない）
- **現状（証拠）**: cohort / term / 年度の概念がコード全体に無い（grep）。
  `learning_states UNIQUE (user_id, course_id)`（`db/011:32`）で再受講不可。
  教員集約に時間窓なし（`api/services.py:2815-2830`）。コース複製なし。卒業は
  アカウント削除しかなく `interest_traces` は purge（`core/account_lifecycle.py:116-118`）。
  学生アカウントは1件ずつ手作成（`admin.py:3737`）、自己登録なし（`routes/auth.py`）。
- **なぜ体験を損なうか**: 研究室は年で回る。同じコースを3年使うと、教員の k-匿名集約は
  3世代の混合になり、**「今年のゼミで何が起きているか」が構造的に読めなくなる**（母集団が
  monotonic に増え、今年の信号が薄まる）。しかも唯一の是正手段が「コースを作り直す」で、
  作り直すと過去の教材構成が失われる。卒業側では「記録を残して去る」状態が無いため、
  §5.2 の主権（本人の記録は本人のもの）が「削除して消す」か「アカウントを放置する」の
  二択に潰れている。
- **機能のかたち**: (a) 受講に**開講期ラベル**（`term`）を1本入れる。`learning_states` に
  列を足すのが素直だが、`progress_data` JSONB のキーとして持てば **migration 不要**でも成立する
  （`course_data.py` と同じ「JSONB の正本スキーマ + アクセサ」流儀）。教員は
  コース管理から「新しい開講期を始める」を押す（既存受講は残り、以後の enroll が新 term に付く。
  UNIQUE は `(user_id, course_id)` のままにし、term は受講行の属性として更新する — 再受講の
  DB 表現を増やさない）。(b) 教員向け集約に**開講期フィルタ**を足す（k=3 は不変。
  母集団が小さくなる分 n<3 の非表示が増えるのは正しい挙動）。(c) 卒業導線を1画面にする:
  「本人が『わたしの記録』を export した」→「アカウントを停止」→「所有物を移管」→
  「削除予約」の既存4操作を、AL層の画面に**順序付きの1つの手順**として並べる
  （新 API を作らず、既存 `my_records/export` と `account_lifecycle` の道案内）。
  非LLM。
- **14原則との照合**: 原則4/原則5（term で切っても個人は見えない、k=3 正本は不変）・
  原則3の限定（人に関するデータの消去は認める — 卒業の削除経路は維持）・
  原則10（term は状態であって完了フラグではない）。
- **規模感**: M

### 提案6: 前提知識の入口 — コーパス外の知識を、出所を偽らずに扱う

- **近づける vision**: §1 課題2（式変形は追えるが物理が見えない）/ 原則8（出所の正直さ）/
  §2.4（閉世界の正直さ）
- **現状（証拠）**: `check_prerequisites` は同一コース内の topic.title との小文字完全一致だけを
  見る（`api/services.py:2168-2178`）。習得判定はチャット履歴の有無（`:2157-2166`）。
  コーパス外の前提は永久に未習得。「いいえ」の先は RAG を通らない LLM 説明で、
  `content_grounding` が None のまま返る（`routes/learning.py:2840-2881` /
  `api/schemas.py:382-385`）、フロントはバッジを描かない（`app.js:1737-1739`）。
  `documents.doc_type='textbook'` は全アップロードに一律で入るだけで（`admin.py:501`）、
  前提知識の解決には使われていない。
- **なぜ体験を損なうか**: システムが掲げる2つの課題のうち片方（前提知識の体系的習得）に
  対する唯一の出口が、**根拠の一線を通らない場所**にある。学習者はコーパス由来の回答には
  grounding バッジと tier が付くのに、前提知識の説明にだけ何も付かない — つまり
  **一番あやふやな回答が一番確からしく見える**。加えて、コースの外に前提資料があっても
  （教科書 PDF を別途アップロードしていても）そこへ橋が架からない。
- **機能のかたち**: 前提の解決を**段階的に**する（すべて既存部品の再利用）:
  ①同コースの topic（現行）→ ②本人が閲覧できる document のチャンク
  （`services.search_chunks_with_metadata(..., allowed_document_ids=...)` をそのまま使う。
  コース sources 外なので `content_grounding="other_material"` が自然に付く）→
  ③どこにも無ければ **LLM 説明を返すが `content_grounding="model_generated"` を必ず設定する**
  （DTO のフィールドは既存、設定するだけ）。加えて④解決できなかった前提を
  「このコーパスの中には、この前提を扱う資料がありません。」の閉世界事実文で学習者に告げ、
  教員側には G層ルール1本（`course.prerequisite_uncovered`、recommended・道案内のみ）を
  足す（`RULE_CATALOG` に1行 + evaluator。`core/admin_assistant/next_steps.py:85-139`）。
  **LLM の追加呼び出しなし**（②は既存 RAG 経路、③は既存 advice 経路）。migration 不要。
- **14原則との照合**: 原則8（全分岐に grounding を付ける）・原則2（evidence-based）・
  原則11（不明を不明と言う）・原則12（G層は recommended、督促しない）。
  §3.6 との関係: 現行の「チャット履歴があれば習得」は暗黙の学習者モデルに近いので、
  この改修と同時に**判定を「履歴の有無」から「本人への明示的な問い」だけに寄せる**
  （現状も問いは出しているので、履歴による自動スキップを外すだけ）。
- **規模感**: M

### 提案7: 分野の同居 — 使わない分野を静かにしまい、分野横断の部品を繋ぐ

- **近づける vision**: §2.1（近傍の重なり）/ §2.4 / 原則7（リンクであってマージではない）/
  原則11（fail-closed）
- **現状（証拠）**: 起動時に particle_physics と astrophysics の凍結骨格がシードされる
  （`api/main.py:152-163` → `atlas_store.import_bundled_skeletons`、
  `atlas_store.py:669-730`）。`corpus_view.list_corpus_domains`（`:180-213`）は凍結骨格を持つ
  active ドメインを全部返し、`corpus-sea.js:264-278` は `has_visible_papers=false` でも
  ボタンを消さない。`landscape.builder.collect_placement_domains()`（`:231-266`）は
  active な全ドメインを配置候補として LLM に見せる。L層検索は `domain_key` 完全一致
  （`core/library/search.py:67, 165`）。group と domain の対応は無い。
- **なぜ体験を損なうか**: 生物物理の学生が「論文の海」を開くと、自分の分野の隣に
  「素粒子物理」「宇宙物理」という空の海が並ぶ。これは §2.4 の閉世界の正直さの逆で、
  **システムが知らない分野を「地図がある分野」として提示している**。教員側では、全論文に
  対して無関係な2ドメインの `unplaced` が毎回積まれ、レビュー画面のノイズになる。
  さらに、分野が増えるほど価値が出るはずの共通部品（同じ数学）が、domain_key の完全一致で
  分断され、旅（journey）が分野の境界で必ず止まる — §2.1 が最も効くはずの場所で効かない。
- **機能のかたち**: 3段階。(1) **バンドル骨格を `lifecycle='retired'` でシードする**
  （`atlas_store.py` の `_seed_bundled_domain_meta` を1箇所変えるだけ。既存 DB の行は
  上書きしない設計なので既存環境に影響しない）。使う分野は教員が既存の
  「復帰（restore）」ボタンで有効化する。retired は配置候補からも学習者表示からも外れる
  既存の挙動をそのまま使う＝**新しい仕組みを作らない**。migration 不要。
  (2) L層検索に `include_domains: list[str]`（既定は当該分野1つ）を足し、W層の同一性リンク
  候補提示でだけ「他の分野も探す」を明示操作で開く（**AI が自動で分野を跨がない**）。
  domain_key はマージせず、**リンクとして跨ぐ**（原則7）。(3) library の domain_key
  自由入力を、atlas ドメイン一覧からの選択 + 「新しい分野名」の2段にする
  （表記ゆれによる静かな分離を止める。`admin.js:1772` の入力欄の置き換えのみ）。
  LLM なし。
- **14原則との照合**: 原則11（既定は閉 = retired から始める）・原則7（跨ぐのはリンク）・
  原則12（分野を跨ぐ探索は明示操作）・原則13（retire/restore の既存機構を使い、新語彙を
  作らない）。
- **規模感**: (1) だけなら S / 全体で M

---

## 4. 見送った案

1. **学習者の習熟度に応じて前提知識ゲートの厳しさや説明の粒度を自動調整する** —
   §3.6（沈黙適応をしない）と UC5 / UC7 に正面から反する。現行の
   「チャット履歴があれば習得とみなす」も同種の推定なので、**増やすのではなく減らす**方向
   （提案6 の末尾）を採った。

2. **コーパスの成長ダッシュボード（何本集まった / 地図の何％が埋まった）** —
   §2.1「踏破率・カバー率を数値にしない」と原則4 に反する。立ち上がりの手応えは
   数値ではなく「1本でも言えること」（提案4）で作るべきで、代理指標を作ると
   「論文を増やすこと」が目的化する（§6.1 の「目標にせず、自動ゲートにしない」）。

3. **初回セットアップウィザードでデモ論文を自動取り込みし、地図が動く状態を作る** —
   PD1（発見は自動・取り込みは教員の明示承認のみ）と §6.2 候補「入口の正直さ」に抵触する。
   加えて、デモコーパスを既定で持つと「このコーパスの中では」という閉世界の言明が
   導入初日から嘘になる（他人の論文が自分のコーパスに混じる）。代わりに提案1（分野を
   自分で宣言する）＋提案4（1本で成立する画面）で立ち上がりを作る。

4. **卒業生の痕跡を匿名化して次年度の教材改善に自動還流させる** — §5.2 の五条件
   （opt-in / 断っても不利益がない / 独立の品質確認 / credit / 撤回経路）を満たさない自動化に
   なり、KN-4（学習者信号をドメイン知識へ昇格しない）にも触れる。年次の扱いは
   提案5 の「期で切る・本人が持ち出す」に留めた。
