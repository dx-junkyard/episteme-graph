# ペルソナ通し受講テスト（Persona Enactment）— UX 検証の刷新 全体設計

[← ドキュメント目次](../README.md) ｜ [改善サイクル](improvement_cycle.md) ｜ [課題ナレッジ](../issue_knowledge/README.md) ｜ [開発チェックリスト](../development_checklist.md)

> **状態:** 設計中（2026-09-27 起草・実装未着手）。本書は**製品の機能ではなく開発基盤**の設計書で、
> 利用者から見える変化は無い。製品側の不変条項（vision §6・各層）を一切緩めない。
> 実装に入るときは §13 の段階ごとに本書へ実装記録を足す。想定 migration 番号は書かない（製品 DB への
> migration は本設計では発生しない）。

---

## 0. 位置づけ — 何を刷新するのか

現在の UX 検証は次の 3 つで成り立っている（2026-09-27 時点の現物調査）。

| 現状の手段 | 何を確かめられるか | 確かめられないもの |
|---|---|---|
| `*_ui_static.py`（約 90 本）— フロント JS・HTML を文字列として検査 | ES5 規律・公開 API の形・禁止フィールド・読み込み順・アンカーの網羅 | **動く画面で人が迷うか**。ボタンが押せるか。文が読めるか |
| TestClient + monkeypatch（約 72 本）— LLM を差し替えた API 契約 | ルート・権限・DTO の契約 | **本物の LLM 応答を受けた学習者が何を感じるか**。CostGate 429 の見え方 |
| オーナーの目視・「六つのレンズ」型の読み取り調査（`six_lenses_2026-09-10/01_learner.md` = 大学院生の一日） | 原則と実装の照合・経路の欠落 | **繰り返し・人数・立場の違い**。24 本の設計書が「docker E2E 未実施」のまま |

欠けているのは「**立場の異なる複数の利用者が、実物の教材とコースを最初から最後まで使う**」検証である。
ブラウザ E2E・LLM モック・デモデータ・サンプル PDF はいずれも存在しない（調査結果 §16）。

本設計は、**宇宙物理を専攻する大学院生の集団**と**宇宙物理を教える教員の集団**を LLM で演じさせ、
教員ペルソナが実物の教材からコースを作り、学生ペルソナ数名がそれを通しで受講し、その過程で見えた
問題を課題ナレッジに候補として記帳し、改善サイクル（`improvement_cycle.md`）に接続する仕組みである。
改善サイクルの「発見」段に**新しい観点（通し受講）**を足すものであって、サイクルの段や自律と人の
線引き（同 §4）は変えない。

命名: 本書では仕組み全体を「ペルソナ通し受講テスト」、コードの置き場を `uxsim/` と呼ぶ。
製品側に既に `core/personas.py`（応答の語り口設定）があるため、製品コード内で「persona」を新たに
使わない。

---

## 1. 目的と非目的

**目的**

1. 教材 → コース → 受講 → 再訪 の実経路で、**画面・文言・応答・縮退**の問題を人の手を借りずに見つける。
2. 見つけた問題を再現可能な形（transcript・再現スクリプト）で残し、課題ナレッジの候補として記帳する。
3. 修正後に同じ経路を再演して**症状が消えたこと**を確かめる（`reproduction_rerun`）。
4. 直した問題を回帰シナリオとして蓄積し、検証の網が増えていくようにする。

**非目的**

- 学習効果・教育的妥当性の測定（合成ペルソナに学習効果は無い）。
- 製品の集約計器（制度指標カタログ）の代替。数値を目標値・KPI にすること。
- ガードレール・ユニットテストの代替（それらは前提であって完了条件ではない — 改善サイクル §2.5）。
- 製品側の変更（新テーブル・新 API・LLM モックプロバイダの追加）。**製品を変えずに外から使う**。

---

## 2. 不変条項（PE1〜PE10）

| # | 条項 | 根拠・帰結 |
|---|---|---|
| **PE1** | **砂場でしか走らせない** | 専用 compose overlay・専用 DB / MinIO・専用 env・専用アカウント。本番・開発共用 DB に接続する経路を作らない。`uxsim/` は `backend/Dockerfile` に COPY しない（製品イメージ非同梱） |
| **PE2** | **ペルソナは製品の正規経路だけを使う** | 状態を作るのは常に HTTP API かブラウザ操作。DB 直書きで教材・コース・痕跡を作らない（作れば UX の欠陥が隠れる）。砂場 DB の**読み**は審判にだけ許す |
| **PE3** | **審判は仮説を出し、確定は人** | 発見は課題ナレッジの `classification.review: candidate`。修正は枝（branch）と PR まで。merge・分類の確定・体系の変更は人（改善サイクル §4 と同じ線） |
| **PE4** | **外部 API の予算を campaign 単位で宣言し、超えたら止まる** | 製品側 OpenAI（ペルソナの操作が呼ぶ）とペルソナ側 LLM の両方。arXiv はコーパス作成時に 1 回だけ（P-0007） |
| **PE5** | **再演可能** | 全 campaign は transcript（各ステップの意図・操作・観測・反応）を全保存し、ペルソナ側 LLM 出力を記録して replay できる。発見には再現スクリプトを必ず付ける |
| **PE6** | **期待の正本を新しく作らない** | 「どう振る舞うべきか」の正本は `docs/manual/`（利用者マニュアル）・vision §6・各層の不変条項・サーバの固定事実文。審判はこれらと突き合わせる |
| **PE7** | **情報を落とさない** | run は失敗した run も全保存。発見は `open → filed / duplicate / not_reproduced / wont_fix` の状態遷移で消さない。数値は run の生ログとレポートにだけ置き、課題エントリ・設計書に書かない |
| **PE8** | **実人間のデータを混ぜない** | 砂場に本番のダンプ・実在ユーザーの痕跡を入れない。ペルソナ痕跡は合成データなので審判が読んでよいが、その扱いを実人間に一般化しない |
| **PE9** | **ペルソナの行為空間は UI の affordance から導く** | API runner に「UI に無い操作」を持たせない。行為レジストリの各行為は画面の要素（`data-ui-anchor` か manual の節）に対応づけ、対応の無い行為はガードレールで弾く |
| **PE10** | **製品の原則に反する検査項目を作らない** | 例: 学習者に数値・内部 ID・分野外の断言が出たら**欠陥**として扱う（原則 4・8・SL1）。「もっと督促すべき」「進捗率を出すべき」のような発見は原則違反の提案として自動で却下する |

---

## 3. 全体構成

```
                    ┌──────────────────────────── uxsim/（リポジトリ内・製品イメージ非同梱）──────────────────────────┐
                    │                                                                                             │
  ①砂場 Sandbox     │  docker-compose.uxsim.yml ─▶ postgres(uxsim) / minio(uxsim) / grobid / api-server / frontend │
                    │           ▲ 復元                                                                            │
  ②材料 Corpus      │  snapshots/<name>/ (pg_dump + MinIO mirror + manifest)  ◀── 一度だけ解析（OpenAI・arXiv 各1回）│
                    │                                                                                             │
  ③役者 Personas    │  personas/{students,teachers}/*.yaml  +  campaigns/*.yaml  +  scenarios/{goal,regression}/  │
                    │                                                                                             │
  ④実行器 Runners   │  runner/api（httpx+JWT・行為レジストリ）  runner/browser（Playwright・data-ui-anchor）        │
                    │  ペルソナ LLM（think-aloud → 行為選択）  ── transcript.jsonl / screenshots / replay cache      │
                    │                                                                                             │
  ⑤審判 Oracles     │  A 契約 / B 原則 / C 行動 / D 文書 / E 観測差分   ── findings.jsonl（指紋つき）                │
                    │                                                                                             │
  ⑥記帳・改善       │  findings → 課題ナレッジ候補（IK-NNNN, candidate）→ 着手の地図の下書き                         │
                    │           → 改善サイクル（設計・実装・検証）→ 回帰シナリオ scenarios/regression/IK-NNNN.yaml   │
                    └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

データの流れは一方向で、**製品側には何も書き戻さない**（製品が書くのは砂場 DB の自分のテーブルだけ）。

---

## 4. ①砂場（Sandbox）

### 4.1 構成

- `docker-compose.uxsim.yml` を `docker-compose.yml` に重ねる第 3 の overlay（`local` と併用しない）。
  - `postgres`（`pgvector/pgvector:pg16`、volume `uxsim_pg`）と `minio`（volume `uxsim_minio`）を**専用 volume 名**で持つ。
  - `api-server` の env: `DATABASE_URL` を砂場に向ける、`ADMIN_PASSWORD` / `JWT_SECRET` は砂場専用値、
    `LLM_PROVIDER=openai` のまま（製品の実挙動を見るため）、`EPISTEME_DEFAULT_CARTRIDGE_ID` は空。
  - 公開ポートは `frontend:3000` だけ、という製品の規律をそのまま守る（runner は nginx 経由で叩く。
    nginx の proxy 欠落＝SPA フォールバックの事故形（`test_nginx_routes.py` が検査する型）も通し受講で踏める）。
  - CostGate の既定は**上げる**（例 `LEARNING_CHAT_MAX_CALLS_PER_DAY`）。ただし「上限に当たったときの見え方」を
    見る専用 campaign では**既定値のまま**走らせる（§8.3）。CostGate は in-memory・プロセス単位なので
    api-server 再起動で消える事実も campaign の記録に残す。
  - `LEARNING_CHAT_STREAMING_ENABLED` は campaign ごとに on/off を切り替える（両経路を踏む — 改善サイクル §2.5）。
- 起動時の `Administrator` bootstrap（`main.py::_lifespan`）を砂場の SYSTEM_ADMIN として使い、教員・学生
  アカウントは **`POST /api/admin/users/teacher` / `/student`** で作る（PE2。学生作成は教員ペルソナの操作の一部にできる）。
- ペルソナ用アカウントの `username` は `uxsim-<population>-<persona_id>` の接頭辞で統一し、
  審判が砂場 DB を読むときの絞り込みキーにする。

### 4.2 砂場から読むもの（審判専用）

| 表・API | 何が分かるか | 読み方 |
|---|---|---|
| `GET /api/admin/error-logs`（SYSTEM_ADMIN・in-memory リング 1000 件） | 500 とスタックトレース | campaign 開始時刻で `minutes=` を絞り、run ごとに差分を取る。**リングは再起動で消える**ので campaign 中は api-server を再起動しない |
| `llm_usage_events`（`success=false` / `error_type`） | 製品側 LLM 呼び出しの失敗と feature 帰属 | SYSTEM_ADMIN の `/api/admin/llm-usage/metrics?group_by=feature` と DB 直読み |
| `discuss_metric_events` | discuss・cycle の UI 遷移、`stance_corrected`、`structured_grounding_present` | `observation-dump`（tar.gz）を run 末尾で取る |
| `interest_traces`（`help_usage` の `no_hit`、`misconception`、`tension` 候補） | 使い方が分からなかった要素、AI が学習者について書いた候補 | ペルソナ本人の JWT で `GET /api/me/records`（本人可視の経路を使う = 製品の主権モデルの検証も兼ねる） |
| `auth_events` / `theory_review_events` / `status_events` | ログイン拒否・確定操作・解析完了 | DB 直読み（砂場限定） |
| `atlas_cue_events` | 導線カードの表示・開封 | **読み API が無い**（調査で判明した欠落）。DB 直読み |

CostGate の 429 は**どこにも記録されない**（調査結果）。runner が HTTP 429 を自分で数えて transcript に残す。

---

## 5. ②材料（Corpus）— テスト用教材

### 5.1 方針

- 分野は**宇宙物理**。骨格専用ドメイン `backend/atlas_domains/astrophysics`（10 領域・49 概念・凍結版）が既に
  同梱されており、カートリッジ無しで地図・配置・着地予測が動く。cartridge は「指定しない」で走らせ、
  分野中立経路（2026-09-10 是正 F9）を踏む。
- 論文は arXiv の astro-ph から **CC-BY ライセンスの論文だけ**を選ぶ（arXiv の既定ライセンスは再配布を許さない —
  §14 D2）。目安 6 本: レビュー 1・観測 2・理論 2・手法 1（コースが章立てできる厚み）。
- **一度だけ**解析する（GROBID + OpenAI パイプライン。P-0007）。以後は snapshot を復元する。
- 束の import（`POST /api/documents/{id}/import-bundle`）は chunks・embedding を運ばない（非スコープ）ため、
  RAG が動く状態を作れない。よって材料の正本は **snapshot（`pg_dump` + MinIO のバケット mirror）**とする。

### 5.2 snapshot の形

```
uxsim/snapshots/<name>/            ← リポジトリには manifest だけ。実体はリポ外（§5.3）
  manifest.yaml                    # name / created_at / git_commit / migration_head / papers[{arxiv_id, license, sha256}]
                                   # / accounts[] / courses[] / notes
  db.dump                          # pg_dump -Fc（砂場 DB 全体。実人間データを含まない = PE8）
  minio/                           # raw-papers / figure-images の mirror
```

段階的に 2 種類を持つ。

| snapshot | 中身 | 作り方 |
|---|---|---|
| `corpus-astro-v1` | 論文 6 本を解析済み（教材のみ。コース無し） | 教員ペルソナ（または人）が URL 取得 / アップロードで投入し、解析完了を `GET /api/admin/tasks/{id}` で待つ |
| `course-astro-v1` | 上に加えて教員ペルソナが作った公開コース 1〜2 本 | §8.2 の教員 campaign を `corpus-astro-v1` から走らせ、成功した run の末尾を snapshot 化 |

学生 campaign は既定で `course-astro-v1` から始める（教員段の LLM 費用を毎回払わない）。教員段そのものを
検証したいときだけ `corpus-astro-v1` から始める。

### 5.3 置き場と再現性

- snapshot 実体はリポジトリに入れない（サイズと権利）。置き場は砂場 MinIO の別バケット `uxsim-snapshots` か、
  オーナーの手元ディレクトリ。`manifest.yaml`（arXiv ID・ライセンス・sha256・作成時のコミット）だけを commit する。
- **migration が進んだら snapshot は古くなる**。復元後に api-server を起動すれば毎起動の migration ランナーが
  番号順に当てるので、追随は自動。ただし意味論が変わる migration（supersede・掃除）の後は「snapshot 由来の行が
  新しい規律と食い違う」ことがあり得る。manifest の `migration_head` と現在の先頭番号が違う run は
  レポートに「snapshot は版 N で作られた」と事実文で残す。
- コーパスを増やす（v2）ときは新しい snapshot 名にし、既存 campaign の比較可能性を壊さない。

---

## 6. ③役者 — ペルソナ集団

### 6.1 定義の形（YAML・1 ペルソナ = 1 ファイル）

```yaml
id: st-m1-obs                       # uxsim-students-st-m1-obs がアカウント名
population: students                # students | teachers
display_name: 佐伯 悠斗
language: ja                        # ペルソナが打つ言語。en は 1 名だけ置く
background: |
  修士 1 年。学部は物理。卒研は電波観測のデータ処理。宇宙論の理論は講義で一度聞いただけ。
knowledge:                          # 事前知識（審判が「前提知識ゲート」の妥当性を見る材料）
  knows: [赤方偏移, 黒体放射, ドップラー効果]
  vague: [宇宙マイクロ波背景放射の異方性, 摂動論]
  unknown: [バリオン音響振動, 線形成長率]
misconceptions:                     # 仕込む誤解（誤解検出・誤解メモの異議の経路を踏むため）
  - "赤方偏移は銀河が空間の中を動く速度だけで決まる"
goals:                              # この受講で何をしたいか（campaign が上書きできる）
  - "ゼミ発表のために論文 A の主結果と前提を自分の言葉で言えるようになる"
habits:                             # 行動の癖（行為選択の重み。数値ではなく段階語）
  reads_manual: never               # never | when_stuck | first
  patience: low                     # 1 操作に 2 回失敗したら諦める
  asks_questions: often             # チャットを使う頻度
  uses_voice: no
  device: desktop                   # desktop | narrow（375px 相当。browser runner のみ）
  prefers_mode: sequential          # sequential（順番に学ぶ）| discuss（論文と議論）| mixed
voice: |                            # 話し方（think-aloud と質問文に使う）
  丁寧語。分からないと「〜ってどういう意味ですか」と短く聞く。長文は書かない。
```

教員ペルソナは `knowledge` の代わりに `teaching`（担当科目・受講者像・コースに求める章立て）、`habits` に
`uses_copilot` / `follows_next_steps`（G層の To-Do に従うか）/ `reviews_before_publish`（リリース前の確認で
実際に読むか「次へ」を連打するか）を持つ。

### 6.2 初期の 2 集団

**大学院生（6 名）** — 立場の違いが UX の違う面を踏むように選ぶ。

| id | 像 | 踏ませたい面 |
|---|---|---|
| st-m1-obs | M1・観測出身・理論に弱い | 前提知識ゲート・記号の直前定義・降下路 |
| st-m2-theory | M2・理論寄り・自信あり・反論する | discuss の歩調合わせ・違和感・再構成の異議 |
| st-d1-adjacent | D1・隣接分野（素粒子）から来た | 別名・概念レジストリ・分野の地図の当てはまらなさ |
| st-en | 留学生・英語で打つ | 言語混在・英語原文チャンクの読みやすさ |
| st-night | 社会人院生・夜に短時間・途中で離脱して翌日戻る | 帰還の扉・持ち越し・レクチャー再開 |
| st-skeptic | 検証状態を気にする・出典を必ず開く | 台帳の事実文・閉世界語彙・出典ポップアップの 404 縮退 |

**教員（3 名）**

| id | 像 | 踏ませたい面 |
|---|---|---|
| te-rookie | 初めて使う助教。To-Do に素直に従う | G層の道案内・Copilot・マニュアルの節の過不足 |
| te-veteran | ベテラン。画面を読まず勘で押す。確認は「次へ」連打 | リリース前の確認の一括確定の記帳・破壊的操作の確認ゲート |
| te-meticulous | 全部読んでから公開する。グラフを承認し、説明を添削する | グラフレビュー・説明レビューキュー・原稿スタジオの往復 |

campaign は各集団から**数名をサンプル**する（学生 3〜4・教員 1）。全員を毎回走らせない（予算 §12）。

### 6.3 ペルソナの「演じ方」の規律

- ペルソナは**画面に見えるものだけ**を知る。マニュアルは `reads_manual` の癖に従って画面の「使い方」（インスペクト）
  から引く。設計書・不変条項・API 名を知らない（知っていると発見が減る）。
- 各ステップで think-aloud を必ず残す: `intent`（何をしたい）/ `expectation`（何が起きると思う）/ `action`（何をした）/
  `observation`（何が見えた）/ `reaction`（どう感じた）/ `friction`（`none | confused | misread | blocked | gave_up`）。
  `friction` は段階語で、点数を付けない（PE7・PE10）。
- 「諦める」条件は `patience` から決まり、諦めたら理由を書いて次の目標に移る。諦めは重要な発見であり、
  runner が強制的に続行させない。
- ペルソナは**製品に対して原則違反の要求をしてよい**（「進捗率を出して」「他の学生と比べたい」）。
  それに製品がどう応じるか（原則どおり断るか・事実文で返すか）が検査対象で、要求の存在自体は欠陥ではない。

---

## 7. ④実行器（Runners）

### 7.1 二段構え

| runner | 用途 | 動かすもの |
|---|---|---|
| **API runner**（Python・`httpx`・JWT） | 多人数・多セッション・長い受講。回帰シナリオの再演 | `frontend` の nginx 経由で製品 API を叩く。行為レジストリ（§7.2）で UI の affordance に縛る |
| **Browser runner**（Playwright・Python） | 画面そのものの問題（配置・可読性・押せない・縮退表示・narrow 幅）。サンプルした少数ペルソナ | 実ブラウザ。要素の特定は **`data-ui-anchor`**（学習 30・管理 355 — 件数の正本は `test_help_ui_anchors.py` / `test_admin_help_ui_anchors.py`）と aria を第一に使い、CSS セレクタは最後の手段 |

両 runner は同じ transcript 形式を書き、同じ審判にかける。Browser runner は各ステップでスクリーンショットと
アクセシビリティツリーを保存し、コンソールエラー（製品にクライアント側エラー捕捉が無いため、ここが唯一の捕捉点）
を transcript に足す。

### 7.2 行為レジストリ（PE9 の実体）

`uxsim/actions/registry.py` に「ペルソナができる行為」を宣言する。

```python
Action(
  id="learning.chat.ask",                  # ペルソナ LLM に見せる行為名
  label="チャットで質問する",               # 画面の日本語（P-0001）
  screen="learning",
  affordance="material.ai-chat",           # data-ui-anchor か manual の {#anchor}
  api=("POST", "/api/learning/courses/{course_id}/topics/{topic_id}/chat"),
  browser=("fill", "#chat-input", "click", "[data-ui-anchor='chat.send']"),
  args={"message": str, "selection_text": Optional[str]},
  precondition="course_enrolled and topic_selected",
  llm_cost="product",                      # product | none（製品側 LLM を呼ぶか。予算集計用）
)
```

- ガードレール（`uxsim/tests/test_action_registry.py`）: 全 `affordance` が既知アンカー集合か manual の実在アンカーに
  解決すること・`api` が FastAPI のルート表に実在すること（`iter_app_routes` 経由。CI 差の教訓 `ci-env-parity`）。
  製品にルートが増えてもレジストリに無い行為はペルソナに見えない = 「UI に無い操作」を構造的に排除。
- レジストリの網羅はマニュアルの節（学習側 64 見出し・管理側タブ別リファレンス）から起こす。**未登録の
  affordance 一覧**をレポートに出す（数値ではなく名前の列挙）。

### 7.3 ペルソナ側 LLM と行為選択

- ペルソナは 1 ステップ = 1 コール（think-aloud + 行為選択を structured output で同時に得る）。入力は
  「ペルソナ定義 + 目標 + 直近 N ステップ + いま見えている画面の**投影**」。画面の投影は API runner では
  レスポンス DTO をペルソナ向けに整形したもの（内部 ID を隠さない — 隠すと内部 ID 露出の欠陥が見えなくなる）、
  Browser runner ではアクセシビリティツリーの要約。
- ペルソナ側 LLM は**製品と別プロバイダ**を既定にする（製品 = OpenAI、ペルソナ・審判 = Anthropic Claude）。
  同じモデルが出題と採点をすると盲点が重なる。`uxsim/llm.py` はプロバイダを差し替え可能にし、製品の
  `core/llm.py` を import しない（製品の U層計測に混ざらないため。ペルソナ側の呼び出し回数は uxsim が自分で数える）。
- **replay cache**: ペルソナ LLM の (入力ハッシュ → 出力) を run ごとに保存し、`--replay <run_id>` で同じ行為列を
  LLM 無しで再演する（製品側だけ本物）。修正後の回帰確認（`reproduction_rerun`）はこれで行う。
  製品の応答が変われば画面の投影が変わり cache miss が起きるので、miss した地点から先は live に切り替え、
  「どこから分岐したか」を transcript に残す。

### 7.4 時間の扱い

- 「翌日戻る」「20 分無活動でセッション終了」「猶予 14 日」など時間依存の経路は、実時間を待たない。
  砂場の api-server に時刻注入の口は無い（製品非改変）ので、v1 は**セッション境界の再ログイン**で近似し、
  時間経過が必要な検査（tension worker のセッション終了トリガ・削除猶予）は「未実施」と記録する（§15）。

---

## 8. 演目（Campaign と Scenario）

### 8.1 campaign 定義

```yaml
id: c-astro-first-course
snapshot: course-astro-v1
personas:
  teachers: [te-rookie]                 # 教員段を走らせるときだけ
  students: [st-m1-obs, st-m2-theory, st-night, st-skeptic]
flags: {LEARNING_CHAT_STREAMING_ENABLED: "true"}
runner: api                            # api | browser | both（both は学生 1 名を browser で並走）
sessions_per_student: 2                # 2 回目は「翌日」として再ログイン
budget: {product_llm_calls: 400, persona_llm_calls: 600, wall_clock_minutes: 90}
scenarios:
  teacher: [t-build-course-from-corpus]
  student: [s-enroll-and-first-topic, s-ask-until-confused, s-discuss-one-paper, s-return-next-day, s-free-wander]
```

### 8.2 教員段のシナリオ（`corpus-astro-v1` から）

教員の一連の操作は管理側マニュアルのタブ順と G層 To-Do の連鎖（教材登録 → コース作成 → binding / 公開）に沿う。

1. ログイン → 「次にやること」を見る（`follows_next_steps` なら道案内に従う）。
2. 教材管理: 教材一覧を読む → 1 本をグラフレビューで開き、承認・却下を数件行う（`te-meticulous` のみ）。
3. コースビルダー: 教材を選んで対話（`POST /api/admin/course-builder/chat`）→ 学ぶ単位の候補を見て章立て → 登録
   （`POST /api/learning/courses` `is_template: true`）→ セッションを published に。
4. リリース前の確認: 学習マップの割り当て → 論文の位置づけ（`te-veteran` は「次へ」連打、`te-meticulous` は却下を混ぜる）
   → 公開。
5. 原稿スタジオ: 1 トピックの原稿を確認、音声生成の準備確認（`lsCanGenerateAudio` の理由表示を読めるか）。
6. 学生アカウントを 1 名作る（学生段の入口）。

### 8.3 学生段のシナリオ（`course-astro-v1` から）

`six_lenses_2026-09-10/01_learner.md` の「歩いた経路」をそのまま骨にする。

| id | 経路 | 見たい面 |
|---|---|---|
| s-enroll-and-first-topic | 受講登録 → 最初のトピック → 教材を読む → ⚓ を開く | 受講の入口・埋め込み解決・要素文脈 |
| s-ask-until-confused | 質問を続け、分からない語で止まる → 記号タップ → 降下路 → 楽屋 | 様相の推定と訂正チップ・前提知識ゲート・記号定義 |
| s-check-and-object | 確認問題 → 自己確認 → 「判定がおかしい」 | 採点しない規律・異議の口の有無（01_learner の指摘） |
| s-discuss-one-paper | 二枚看板 → 開幕画面 → 予想 → 議論 → 着地 → 持ち越し | discuss 開幕の可読性・鏡面化・着地の帰属カード |
| s-lecture | レクチャーを再生 → 中断チャット → 短く聴く | スライド同期・音声未生成の表示・省略の告知 |
| s-return-next-day | 再ログイン → 扉 → 持ち越しの問いに答える → わたしの地図 → 旅 | 帰還の扉・帰り道の景色・旅の縮退文 |
| s-maps-and-records | 分野の地図 → 論文の位置づけ → 論文の海 → わたしの記録 | fail-closed（地図が出ない）と事実文・閉世界語彙 |
| s-quota-edge | 既定 CostGate のまま質問を続ける | 429 の見え方（学習チャット UI は detail を汎用文に潰す — 調査で判明） |
| s-free-wander | 目標だけ与えて自由に歩く（ステップ予算内） | 想定外の経路・押せるが意味の無い要素 |
| s-narrow（browser のみ） | 375px 幅で s-enroll-and-first-topic | 1 画面レイアウトの崩れ |

### 8.4 回帰シナリオ

直した課題ごとに `uxsim/scenarios/regression/IK-NNNN.yaml` を置く。中身は「起票時の transcript から切り出した
行為列 + 消えるべき症状の審判ルール」。campaign `c-regression` が全件を replay で走らせ、`reproduction_rerun`
の記録を課題エントリの `resolution.verification` に書く材料にする。

---

## 9. ⑤審判（Oracles）— 何をもって「問題」とするか

審判は 5 系統。**A・B・E は決定論**（LLM 0 回）、C・D は LLM 審判（ペルソナと同じプロバイダ・別プロンプト）。

### A. 契約（決定論）

| 検査 | 出所 |
|---|---|
| HTTP 5xx / タイムアウト（nginx `proxy_read_timeout 120s`）/ 接続断 | runner |
| `GET /api/admin/error-logs` の run 中差分（スタックトレース） | 砂場 |
| ブラウザ console error / unhandled rejection（製品に捕捉点が無いので Playwright が唯一） | browser runner |
| DTO の形（`LearningChatResponse` の必須キー・`stance` が 3 キーだけ・`degraded` と 200 の組）| runner の pydantic 検証（製品の schemas を**読み**で使う） |
| SSE の `start / delta / final / error` 順序、`final` が `model_dump()` と一致 | runner |
| 429 の後に UI が示す文言（汎用文か・事実文か） | runner + browser |

### B. 原則（決定論・製品の正本を再利用）

| 検査 | 再利用する正本 |
|---|---|
| 学習者向けテキストに**数値・パーセント・件数**が出ていない | vision 原則 4・`test_*guardrails` の数値非漏洩の規則を関数として切り出す（新しい表を作らない） |
| 内部 ID・生 TeX の露出 | `core/learner_context_common.py` の `contains_internal_id` / `text_excerpt.looks_like_tex_math` |
| 学生向け文言の禁止語（`ADMIN_PASSWORD` / `/api/admin` 等） | `core/help_kb/validator.py::STUDENT_DENYLIST` |
| 閉世界語彙の逸脱（「この分野では未検証」「世界初」等） | SL1 denylist（`test_stakes_ledger_guardrails.py` の正本） |
| AI の断言（「あなたは理解していない」型）・督促語彙 | D層・UC4 の禁止語彙（既存ガードレールの語彙を import） |
| 制御文字・ANSI 残骸 | `core/text_hygiene.py::strip_control_sequences` を掛けて差分があれば欠陥 |

### C. 行動（LLM 審判 + 決定論の前処理）

- `friction != none` のステップを候補にし、審判 LLM が「製品の欠陥か・ペルソナの前提知識由来か・原則どおりの拒否か」を
  3 択で仮説づけ、根拠（transcript のステップ参照）を付ける。**欠陥と断定しない**（PE3）。
- 決定論の前処理: 同じ行為の 3 回連続失敗・`gave_up`・目標未達のまま campaign 終了・`help_usage` の `no_hit`（マニュアルの穴）。

### D. 文書（LLM 審判）

- 各ステップの `screen` と `affordance` からマニュアルの節（`docs/manual/**/*.md#anchor`）を引き、
  「マニュアルは X と書いているが観測は Y」を仮説として出す。出口は 2 つ: 実装の欠陥（`doc_code_diff` で
  発見された bug）か、文書の欠陥（`doc_correction`）。判定は人。
- 配信中のマニュアルは DB 版（`manual_kb_state.serving_source=db`）のこともある。砂場は `files` 固定にする。

### E. 観測差分（決定論・砂場 DB）

- `llm_usage_events.success=false` の行、feature `unattributed` の行（帰属漏れ）。
- `interest_traces` の `kind='misconception'` がペルソナの `misconceptions` と一致するか／仕込んでいない誤解を AI が
  書いたか（後者は「AI が学習者について確定を書く」型の候補）。
- `discuss_metric_events` に `stance_corrected` が出た回（様相の誤推定の追跡）。
- 教員ペルソナの一括確定に `decision_context` が記帳されているか（`theory_review_events.metadata`）。
- ペルソナの `GET /api/me/records` が、自分の痕跡を全部（合成なので答え合わせできる）返しているか。

### 発見レコードの形

```yaml
finding_id: f-<run_id>-<seq>
fingerprint: sha256(oracle + screen + affordance + 症状の正規化文)   # 重複判定に使う
oracle: A|B|C|D|E
severity_label: blocked | confused | inconsistent | principle    # 段階語。点数なし
reproducibility: reproduced_in_replay | observed_once
persona: st-m1-obs
evidence:
  transcript_steps: [12, 13]
  screenshots: [...]
  server_rows: {error_logs: [...], llm_usage_events: [...]}
suspected_layer: [rag_chat, frontend_learning_ui]   # docs/issue_knowledge/layers.md の語彙のみ
hypothesis: "429 の detail が学習チャット UI で汎用文に置き換わり、上限に達したことが伝わらない"
status: open        # open | filed(IK-NNNN) | duplicate(f-...) | not_reproduced | wont_fix(reason)
```

---

## 10. ⑥記帳と改善ループ — 改善サイクルへの接続

| 改善サイクルの段 | 本仕組みが担うこと | 人が担うこと |
|---|---|---|
| 発見 | campaign 実行 → findings。同一 fingerprint は 1 件に束ね、`reproduced_in_replay` を優先 | — |
| 記録（起票） | findings → 課題ナレッジ候補 `IK-NNNN`（`backend/scripts/issue_knowledge_index.py --new`）。`discovery.perspective` は §14 D3 の裁定まで `reproduction` + `symptom_report` を使う。`sources` に run の transcript パスを書く | `classification.review` の確定 |
| 選別・裁定 | 着手の地図の**下書き**（前提とする利用状況の 1 行 + 6 観点の事実文。点数なし）を生成 | 第 1 波 / 第 2 波 / 保留の選択・3 種の判断 |
| 設計・実装 | Claude Code のワークフロー（第 1 波 = UI なし並列 / 第 2 波 = UI・アンカー・マニュアル 1 体）で枝を作る。サブエージェント禁止事項（`git stash`・実 DB 破壊・外部 API live）を継承 | — |
| 検証 | ガードレール green + 回帰シナリオの replay で `reproduction_rerun` + campaign 再実行 | — |
| 是正・記録 | PR 作成・課題エントリの `resolution` 記入・回帰シナリオ追加 | **merge**・分類確定 |

**自律の範囲（推奨、§14 D1）**: 発見 → 起票 → 修正枝 → 再演 → PR までを自律で回し、merge と分類確定で止まる。
一晩（夜間 cron）で「campaign 1 本 → 発見の束 → 修正 PR 数本」が出る形。ループの停止条件は予算（§12）・
同じ fingerprint の再発 2 回・修正が別の回帰シナリオを赤にしたとき。

**振り返り**: campaign を閉じるたびに改善サイクル §3 の起票条件に照らし、該当すれば `cycle_verification` 層の
振り返りエントリを起票する（例: browser runner を走らせずに閉じた = 「未実施」）。

---

## 11. リポジトリ配置 — ペルソナ・シナリオ・教材の管理

### 11.1 分け方の原則

ペルソナは**分野**（宇宙物理・素粒子物理…）と**立場**（学生・教員）の 2 軸で増える。「懐疑派の学生」「勘で押す
ベテラン教員」のような**行動型は分野に依らず**、分野が変わって変わるのは**知識・誤解・目標・教材**だけである。
そこで次の 3 層に分け、campaign が束ねる。

| 層 | 分野依存 | 置き場 | 例 |
|---|---|---|---|
| **行動型（archetype）** | しない | `uxsim/archetypes/{students,teachers}/` | 懐疑派・夜間の社会人・新任教員・「次へ」連打 |
| **分野パック（domain pack）** | する | `uxsim/domains/<domain_key>/` | 宇宙物理の知識表・誤解・受講目標・コース依頼文・コーパス manifest |
| **経路（scenario）** | しない（分野の穴を持つ） | `uxsim/scenarios/{goal,regression}/` | 受講登録→最初のトピック、質問を続けて詰まる、翌日戻る |

ペルソナ = **行動型 × 分野パックの 1 行**。ファイルは分野パック側に置き、`archetype:` で行動型を参照する。
分野が増えたら `domains/<domain_key>/` を 1 つ足すだけで、行動型と経路はそのまま使い回す。
`<domain_key>` は製品の `backend/atlas_domains/<domain_key>` / `backend/cartridges/<id>` と同じキー
（`astrophysics` / `particle_physics`）にし、地図・cartridge との対応を名前で保つ。

### 11.2 ディレクトリ構成

```
uxsim/                                   ← 製品イメージ非同梱（backend/Dockerfile は COPY しない）
  README.md                              # 使い方・予算・禁止事項・この構成の説明
  pyproject.toml                         # httpx / playwright / pyyaml / anthropic（製品 requirements と分離）

  archetypes/                            # 行動型（分野非依存）。1 型 = 1 ファイル
    students/
      newcomer.yaml                      #   入門者: patience low・質問多い・マニュアル読まない
      confident_theorist.yaml            #   自信あり・反論する・discuss を好む
      adjacent_field.yaml                #   隣接分野から来た・別名で躓く
      non_native.yaml                    #   英語で打つ
      night_parttime.yaml                #   短時間・途中離脱・翌日戻る
      skeptic.yaml                       #   出典と検証状態を必ず開く
    teachers/
      rookie_follows_todo.yaml           #   To-Do と Copilot に従う
      veteran_clicks_through.yaml        #   読まずに押す・「次へ」連打
      meticulous_reviewer.yaml           #   グラフ承認・説明添削まで行う
    README.md                            # 行動型の欄（habits / patience / reads_manual …）の語彙

  domains/                               # 分野パック（分野依存）。<domain_key> は atlas_domains と同じ
    astrophysics/
      domain.yaml                        #   表示名・想定する受講者像・cartridge_id（空なら分野中立）・atlas domain_key
      corpus/
        manifest.yaml                    #   論文 [{arxiv_id, license, sha256}]・snapshot 名と migration_head（実体はリポ外）
        course_briefs/                   #   教員ペルソナに渡す「こういうコースを作って」の依頼文
          seminar_prep.md
      knowledge/
        concepts.yaml                    #   分野の概念語彙（knows / vague / unknown に配る候補。骨格の概念名を流用）
        misconceptions.yaml              #   仕込む誤解の一覧（誤解検出・異議の経路を踏むため）
        goals.yaml                       #   受講目標の候補（ゼミ発表・レポート・研究の前提を埋める…）
      personas/
        students/
          st-m1-obs.yaml                 #   archetype: newcomer + 観測出身の知識表 + 目標
          st-m2-theory.yaml              #   archetype: confident_theorist
          st-d1-particle.yaml            #   archetype: adjacent_field（素粒子から）
          st-en.yaml                     #   archetype: non_native
          st-night.yaml                  #   archetype: night_parttime
          st-skeptic.yaml                #   archetype: skeptic
        teachers/
          te-rookie.yaml
          te-veteran.yaml
          te-meticulous.yaml
      scenario_params/                   #   経路の「分野の穴」を埋める値（質問の種・注目する式や図・言い換え）
        s-ask-until-confused.yaml
        s-discuss-one-paper.yaml
    particle_physics/                    # 2 つ目以降の分野は同じ形をコピーせず「足す」
      domain.yaml
      corpus/manifest.yaml
      knowledge/…
      personas/…
      scenario_params/…

  scenarios/                             # 経路（分野非依存）。分野固有の値は {{param}} で穴にする
    goal/
      teacher/
        t-build-course-from-corpus.yaml
        t-review-graph-then-publish.yaml
      student/
        s-enroll-and-first-topic.yaml
        s-ask-until-confused.yaml
        s-check-and-object.yaml
        s-discuss-one-paper.yaml
        s-lecture.yaml
        s-return-next-day.yaml
        s-maps-and-records.yaml
        s-quota-edge.yaml
        s-free-wander.yaml
        s-narrow.yaml                    #   browser のみ
    regression/                          # 直した課題ごと。ファイル名 = 課題 ID
      IK-0360-chat-429-detail-hidden.yaml
    README.md                            # 経路の記法（steps / goals / stop_when / expects）

  campaigns/                             # 束ねる単位。分野 × snapshot × ペルソナ × 経路 × flags × 予算
    astrophysics/
      c-first-course.yaml
      c-quota-edge.yaml
      c-narrow-browser.yaml
    particle_physics/
      c-first-course.yaml
    c-regression-all.yaml                # 全分野の regression を replay
    README.md

  actions/registry.py                    # 行為レジストリ（PE9）。分野非依存
  runner/{api.py, browser.py, transcript.py, replay.py}
  persona/{compose.py, prompt.py, think_aloud.py}   # compose = archetype × 分野パック の合成
  llm.py
  oracles/{contract.py, principle.py, behavior.py, document.py, observation.py, findings.py}
  report/{campaign_report.py, issue_knowledge_bridge.py, map_draft.py}
  sandbox/{docker-compose.uxsim.yml, env.uxsim.example, snapshot.py}

  runs/                                  # git 管理外。<campaign_id>/<run_id>/{transcript.jsonl, screenshots/, cache/, findings.jsonl, report.md}
  tests/                                 # uxsim 自身のガードレール（§11.4）
```

### 11.3 ファイルの形（合成の契約）

**行動型**（`archetypes/students/skeptic.yaml`）— 分野の語を一切書かない:

```yaml
id: skeptic
population: students
habits: {reads_manual: when_stuck, patience: high, asks_questions: sometimes, uses_voice: no,
         device: desktop, prefers_mode: mixed, opens_sources: always, checks_verification: always}
voice: |
  出典と検証状態を毎回確かめる。断定されると「それはどこに書いてありますか」と聞き返す。
```

**ペルソナ**（`domains/astrophysics/personas/students/st-skeptic.yaml`）— 行動型に分野の中身を重ねる:

```yaml
id: st-skeptic
archetype: students/skeptic            # 必須。無い型はガードレールで弾く
display_name: 高梨 澪
language: ja
background: |
  M2。宇宙論の観測的検証がテーマ。BAO の解析パイプラインを触っている。
knowledge:                             # 語は domains/astrophysics/knowledge/concepts.yaml に実在すること
  knows: [バリオン音響振動, 赤方偏移, 宇宙マイクロ波背景放射]
  vague: [線形成長率]
  unknown: [修正重力理論のパラメータ化]
misconceptions: [cosmic_variance_is_measurement_error]   # knowledge/misconceptions.yaml のキー
goals: [seminar_present_main_result]                     # knowledge/goals.yaml のキー
overrides: {habits: {device: narrow}}                    # 行動型の値を局所的に上書きしてよい欄
```

**経路**（`scenarios/goal/student/s-ask-until-confused.yaml`）— 分野固有の値は穴:

```yaml
id: s-ask-until-confused
population: students
runner: [api, browser]
goal: "{{goal_text}} のために、教材 {{topic_hint}} を読み、分からない語が出るまで質問を続ける"
params_required: [goal_text, topic_hint, seed_questions]   # domains/<key>/scenario_params/ が埋める
steps:
  - do: learning.topic.open
  - repeat: {until: "friction in [confused, gave_up] or steps > 12", do: learning.chat.ask, with: {message: "{{seed_questions | next}}"}}
  - do: learning.symbol.lookup          # 行為レジストリの id だけを使う（PE9）
  - do: learning.descent.ladder
stop_when: [gave_up, budget_exhausted]
expects:                               # 審判 D の期待の引き先（マニュアルの節）
  - manual: student/02-student.md#ai-chat
  - manual: student/02-student.md#symbol-lookup
```

**campaign**（`campaigns/astrophysics/c-first-course.yaml`）:

```yaml
id: c-astro-first-course
domain: astrophysics                    # domains/<key> が存在すること
snapshot: course-astro-v1               # domains/astrophysics/corpus/manifest.yaml に載っていること
personas: {teachers: [te-rookie], students: [st-m1-obs, st-m2-theory, st-night, st-skeptic]}
scenarios:
  teacher: [t-build-course-from-corpus]
  student: [s-enroll-and-first-topic, s-ask-until-confused, s-discuss-one-paper, s-return-next-day]
runner: api
flags: {LEARNING_CHAT_STREAMING_ENABLED: "true"}
sessions_per_student: 2
budget: {product_llm_calls: 400, persona_llm_calls: 600, wall_clock_minutes: 90}
```

### 11.4 uxsim 自身のガードレール（`uxsim/tests/`）

- `test_action_registry.py`: affordance の実在（`data-ui-anchor` か manual の `{#anchor}`）・API ルートの実在
  （`iter_app_routes` 経由）・`llm_cost` の宣言漏れ。
- `test_persona_composition.py`: 全ペルソナの `archetype` が実在・`knowledge` の語が分野の `concepts.yaml` に実在・
  `misconceptions` / `goals` のキーが実在・行動型ファイルに分野語（`domains/*/knowledge/concepts.yaml` の語）が現れない。
- `test_scenarios.py`: `steps[].do` が行為レジストリの id・`params_required` を各分野の `scenario_params/` が全部埋める・
  `expects[].manual` の節が実在・regression のファイル名が実在する `IK-NNNN` と一致。
- `test_campaigns.py`: `domain` / `snapshot` / `personas` / `scenarios` の参照が全部解決する・`budget` の 3 キー必須。
- `test_isolation.py`: `uxsim/` が `backend/core/llm.py` を import しない・compose overlay の volume 名が製品と重ならない・
  `backend/Dockerfile` に `uxsim` が現れない（PE1）。
- `test_oracle_vocab.py`: 審判の禁止語彙・数値検査が製品の正本（`STUDENT_DENYLIST` 等）を**参照**していて独自の表を持たない（PE6）。
- `test_findings_schema.py`: `suspected_layer` が `layers.md` の語彙・`severity_label` が段階語・点数キーの不在。
- 製品側 CI には**乗せない**（外部 LLM を呼ぶ）。uxsim の tests だけを製品 CI の別 job（LLM 不要）で流す。

### 11.5 運用規則

- **分野を足す**: `domains/<domain_key>/` を作り、`domain.yaml` → `corpus/manifest.yaml`（snapshot を作ってから）→
  `knowledge/` → `personas/`（行動型を選んで重ねる）→ `scenario_params/`（穴を埋める）→ `campaigns/<domain_key>/` の順。
  行動型・経路・行為レジストリは触らない。
- **行動型を足す**: 2 分野以上で同じ癖のペルソナが要るときだけ。1 分野だけなら `overrides:` で済ませる。
- **経路を足す**: 分野の語を書かず `params_required` で穴にする。回帰は `regression/IK-NNNN-<slug>.yaml` で、起票時の
  transcript から切り出した行為列と「消えるべき症状」の審判ルールを持つ。
- **ペルソナと経路は課題エントリから参照される**（`sources` に `uxsim/runs/.../transcript.jsonl` と campaign id）。
  ペルソナを改名・削除しない（`status: retired` を付けて残す — PE7）。

## 12. 予算表（campaign 1 本の目安・実装時に実測で置き換える）

| 費目 | 呼ぶ側 | 目安（学生 4 名 × 2 セッション + 教員 1 名） | 上限の置き場 |
|---|---|---|---|
| 製品側 OpenAI（チャット・確認問題・course builder・discuss 開幕は 0） | 製品 | チャット 1 セッション 10〜15 往復 × 8 = 120 / course builder 10 / 確認問題 8 / 非同期 worker（tension・anchor）は既定上限 | campaign の `budget.product_llm_calls`。U層 `llm_usage_events` で事後実測 |
| ペルソナ側 LLM（think-aloud + 行為選択） | uxsim | 1 ステップ 1 コール × 学生 40 ステップ × 8 + 教員 30 = 350 | `budget.persona_llm_calls`。replay では 0 |
| 審判 LLM（C・D） | uxsim | friction 候補 + 文書突合で 30〜60 | 同上に含める |
| Vision 審判（スクリーンショット） | uxsim | browser 併走 1 名 × 40 = 40 | 同上 |
| arXiv | 人 | snapshot 作成時の 6 本のみ。campaign 中は 0（論文レーダー・ディスカバリーのシナリオは**ブロック中の見え方**だけを検査し live で叩かない） | manifest |
| GROBID | 砂場 | snapshot 作成時のみ | — |

数値は run のレポートと manifest にだけ残す（PE7）。

---

## 13. 段階導入

| Phase | 内容 | 出口（事実文） |
|---|---|---|
| **0 砂場と材料** | compose overlay・snapshot make/restore・CC-BY 論文 6 本の一度きり解析・`corpus-astro-v1` | 復元した砂場で `Administrator` がログインでき、教材 6 本が「解析完了」で一覧に出る。migration 先頭番号が manifest と一致する |
| **1 API runner と最小 campaign** | 行為レジストリ（学習側 30 アンカー分 + 教員の 8.2 の経路）・ペルソナ 2 名（st-m1-obs・te-rookie）・審判 A/B/E・transcript・replay | 教員段で `course-astro-v1` が作れ、学生 1 名が s-enroll-and-first-topic と s-ask-until-confused を通しで終える。findings.jsonl が出る。同じ run を replay して行為列が一致する |
| **2 集団と審判 C/D、記帳** | 9 ペルソナ・8.3 の全シナリオ・LLM 審判・課題ナレッジ橋・着手の地図の下書き | campaign 1 本から候補エントリが起票され、`issue_knowledge_index.py --check` が green。マニュアル突合の発見が少なくとも 1 件、実装の欠陥と文書の欠陥に分かれて出る |
| **3 Browser runner** | Playwright・`data-ui-anchor` 特定・スクリーンショット・console 捕捉・narrow 幅・vision 審判 | s-narrow が通り、API runner では見えなかった種類の発見（配置・押せない・縮退表示）が出る |
| **4 自律ループ** | 夜間 cron の campaign → 起票 → 修正枝 → 回帰 replay → PR。停止条件・予算の実効 | 人が触らずに「campaign → PR」が 1 周し、PR の説明に再現スクリプトと `reproduction_rerun` の記録がある。merge は人 |

各 Phase の終わりに本書へ実装記録を足し、飛ばした検査は「未実施」と書く。

---

## 14. オーナー判断（3 種に当たるものだけ・推奨付き）

| # | 問い | 種別 | 推奨 |
|---|---|---|---|
| **D1** | 自律ループはどこまで進めてよいか（PR まで／merge まで） | ③後戻りしにくい構造・改善サイクル §4 の線 | **PR まで**。merge・分類確定・体系変更は人。理由: 製品の「AI は候補まで」をサイクルにも適用している現行規律（§4）を崩さず、失敗した修正が本流に入る経路を持たない |
| **D2** | 論文の権利。arXiv 論文を snapshot に含めてよいか | ②人の権利・制度・法 | **CC-BY 表示の論文だけ**を使い、snapshot 実体はリポジトリ外（manifest に ID・ライセンス・sha256）。arXiv 既定ライセンスの論文は使わない |
| **D3** | 課題ナレッジの発見観点に `persona_enactment`（通し受講で見えた）を足すか | 体系の変更（改善サイクル §4 は人の裁定） | **足す**。理由: 既存の `reproduction` / `symptom_report` は「誰が言ったか」を剥がす規律に合うが、「立場の異なる合成利用者が通しで踏んだ」という観点は既存語彙で表せず、§14 の観点分布で偏りを見る材料にならない。裁定まではエントリの `discovery.perspective` を `reproduction` + `symptom_report` にし、`note` に「通し受講」と書く |

次は 3 種に当たらないので**決定として宣言**する（異論があれば一言で止められる）:

- 合成ペルソナの痕跡を製品の観測テーブル（`interest_traces` 等）に**砂場限定で**書く。実人間のデータは混ぜない（PE8）。
- ペルソナ・審判の LLM は製品と別プロバイダ（Anthropic）。製品側は OpenAI のまま。
- 製品側にモック LLM プロバイダ・時刻注入・テスト用 API を足さない（製品非改変）。時間依存の経路は v1「未実施」。

---

## 15. 非スコープ（v1）

- 学習効果の測定・ペルソナ間の比較・スコア化（PE7・PE10）。
- 時間経過が要る経路（tension worker のセッション終了 20 分・削除猶予・版の削除予定バナー）の実時間検証。
- 音声（STT/TTS）の実音声品質。runner は `voice/transcribe` にテキスト起こし済みの音声ファイルを送る経路だけ踏む。
- 論文ディスカバリー・レーダーの live arXiv 呼び出し（P-0007。ブロック中の見え方だけ検査）。
- 製品 CI への組み込み（外部 LLM を呼ぶため。夜間 cron の別枠）。
- 複数教員の同時編集・グループ共有の多人数競合（Phase 4 の後に campaign を足す）。

---

## 16. 現物調査の要約（2026-09-27・設計の前提）

**再利用する既存資産**

| 資産 | 用途 |
|---|---|
| `POST /api/admin/users/{teacher,student}` / `POST /api/auth/login` / groups API | ペルソナのアカウント・グループ |
| 教材 upload / URL 取得 / `GET /api/admin/tasks/{id}` / course-builder sessions & chat / `POST /api/learning/courses` / visibility / enroll | 教員段・学生段の入口（§8） |
| `routes/learning.py` ほか学習側 API（cycle・reconstruction・corpus・descent・my_records・personal_map） | 行為レジストリの `api` 欄 |
| `data-ui-anchor`（学習 30・管理 355）+ `ui_anchors.py` / `admin_ui_anchors.py` | browser runner の要素特定・affordance の正本 |
| `docs/manual/{student,teacher,system_admin}`（見出しごとの `{#anchor}`）| 審判 D の期待・行為レジストリの網羅元 |
| `six_lenses_2026-09-10/01_learner.md` / `02_teacher.md` | シナリオの骨・既知課題の照合 |
| `GET /api/admin/error-logs` / `llm_usage_events` / `discuss_metric_events` + `observation-dump` / `interest_traces` / `auth_events` / `theory_review_events` | 審判 A・E |
| `STUDENT_DENYLIST` / SL1 denylist / `contains_internal_id` / `looks_like_tex_math` / `strip_control_sequences` | 審判 B（新しい表を作らない） |
| `backend/atlas_domains/astrophysics`（凍結骨格） | 宇宙物理の地図・配置・着地 |
| `improvement_cycle.md` / `issue_knowledge_index.py --new` / `layers.md` / taxonomy §8 | 起票・着手の地図・検証記録 |

**無いもの（本設計で足す・または回避する）**

| 欠落 | 扱い |
|---|---|
| ブラウザ E2E（Playwright 等）・node の CI 依存 | Phase 3 で uxsim 側に持つ。製品 CI には乗せない |
| LLM モック／フェイクプロバイダ | 作らない（実 UX を見るのが目的）。replay cache はペルソナ側だけ |
| デモデータ・サンプル PDF・アカウントのシード | snapshot（§5）と API 経由のアカウント作成（PE2） |
| クライアント側エラー捕捉 | browser runner の console 捕捉が唯一。製品への捕捉追加は**別の課題**として起票候補 |
| CostGate 429 の記録 | runner が数える。製品側の記録は別課題 |
| `atlas_cue_events` の読み API | 砂場 DB 直読み。別課題 |
| 学習チャット UI が 429 の detail を汎用文に潰す | s-quota-edge の最初の検査対象（既知） |

### 16.1 pre-mortem（課題ナレッジ README §3.3b の族当て）

本設計に含まれる変更の性質と、先に当てる辞書の型:

| 性質 | 型 | 当て方 |
|---|---|---|
| 外部呼び出し（OpenAI・Anthropic・arXiv） | `external-budget-exceeded` | §12 の予算表と PE4 の停止 |
| AI の判定（審判 C/D・ペルソナの行為選択） | `ai-decides-instead-of-human` | PE3。findings は仮説・分類は candidate・merge は人 |
| 段階間の受け渡し（runner → transcript → 審判 → 起票） | `contract-changed-one-side` / `context-lost-across-execution-boundary` | transcript の形を 1 つに固定し、両 runner と全審判が同じ形を読む。replay の分岐点を記録 |
| 既定値（CostGate を上げる・streaming フラグ） | `default-hides-choice` | campaign ごとに flags を明示し、レポートに書く |
| 派生物（snapshot が migration から遅れる） | `stale-derivative-served` | manifest の `migration_head` と現在値の不一致を事実文で出す |
| ID（fingerprint・run_id・persona id） | `id-unique-only-within-inner-scope` | fingerprint に oracle・screen・affordance を含める。run_id は時刻 + git commit |
| 語彙表の新設（friction・severity_label・status） | `duplicate-canonical-sources` | 製品の語彙は再利用し、uxsim 固有の語彙は `uxsim/oracles/findings.py` の 1 箇所に置く |

---

## 17. 記録

### 17.1 Phase 0/1 の実装記録（2026-09-27・同日着手）

- **砂場の代替経路（ネイティブ）**: この開発機では別ユーザーの Docker Desktop が動いていて本セッションから
  docker socket に到達できない（`/Users/dev/.docker/run/docker.sock` 不在・別ユーザーの GUI プロセスが lingering）。
  一方、オーナーの開発スタックが公開している Postgres(5432) / MinIO(9000) / GROBID(8070) には到達できるため、
  **専用 DB `episteme_uxsim` を同じ Postgres に作り、api-server を `backend/.venv` からネイティブ起動**する経路を
  `uxsim/sandbox/run_native_api.py`（8011 番・`.env` → `.env.uxsim` の順に読む）として置いた。docker 経路は
  `uxsim/sandbox/docker-compose.uxsim.yml`（プロジェクト名 `episteme-uxsim`・frontend 3100・postgres 5433）として
  併置し、docker が使える環境ではそちらを正とする。**PE1 からの逸脱**: MinIO のバケット名が製品側で固定
  （`raw-papers` / `raw-texts` / `figure-images`）のため、ネイティブ経路では MinIO を開発スタックと共有する
  （オブジェクトキーは document_id 由来で衝突しない）。nginx を経由しない（API runner が 8011 を直接叩く）。
- **コーパス投入**: `uxsim/sandbox/bootstrap_corpus.py` が製品の正規経路だけで投入する（Administrator →
  `arxiv.org` を許可リストへ → 取り込み用教員 `uxsim-bootstrap-teacher` → 「URLから取得」× 論文数 → tasks 完了待ち）。
  論文の選定は arXiv OAI-PMH（`oaipmh.arxiv.org`・`metadataPrefix=arXiv`・1 回）で `license` が CC BY 4.0 の
  astro-ph 論文を列挙し、4 本を選んだ（D2 の推奨どおり CC-BY のみ・snapshot 実体はリポ外）:
  `2606.00411`（宇宙論・動的暗黒エネルギー）/ `2606.02318`（連星ブラックホールの遅延時間分布）/
  `2605.31198`（中性子星の状態方程式・PINN）/ `2605.26810`（Cepheus B の磁場と星形成）。
  arXiv 到達は OAI 1 回 + PDF 4 回。
- **砂場の新規ビルドで見つかった第 1 の発見（IK-0360）**: docker overlay で api-server を新規ビルドしたところ、
  SQLAlchemy 2.1 が `postgresql://` の既定ドライバを psycopg（3 系）へ変えたため `psycopg2-binary` しか無い
  イメージが起動できなかった（開発スタックは旧版のイメージで動き続けるため未発見だった）。砂場は overlay 側で
  `postgresql+psycopg2://` を明示して進め、製品側の是正（版の上限・CI でのイメージ起動検査）は改善ループの
  1 件目として課題ナレッジに起票した。
- **ペルソナ側 LLM**: この機には Anthropic の鍵が無いため、第 1 周は製品と同じ OpenAI を**別モデル**
  （`gpt-5.4-mini`・製品の解析は `gpt-5.4`）で使う。§14 で宣言した「別プロバイダ」は鍵が用意でき次第に切り替える
  （`UXSIM_PERSONA_LLM_PROVIDER`）。

### 17.2 第 1 周の記録（2026-09-27・c-astro-corpus-to-course・run 20260927T033148Z）

**前提とする利用状況**: 教員 1 名が公開済みの教材 4 本から入門コースを作り、学生 2 名が受講する。ペルソナ側 LLM は
**台本モード（scripted）**（§7.3 の provider に追加。LLM を呼ばず各ステップの最初に許された行為を提案引数のまま実行）。
理由: 実行直前に OpenAI の課金残高が尽き（解析パイプラインの終盤で失敗が始まっていた）、ペルソナ側・製品側とも
LLM を呼べなくなったため。**実ペルソナ（think-aloud・行為選択）の周回は未実施**で、残高が戻り次第 同じ campaign を
`UXSIM_PERSONA_LLM_PROVIDER=openai`（または anthropic）で回す。

**通ったこと**: アカウント作成（Administrator → 教員 1・学生 2）→ 教員段 20 ステップ（次にやること・Copilot・
教材一覧/詳細・コースビルダー・以降はコース不成立で precondition スキップ）→ 学生段 16 ステップ × 2 名（コース無しで
受講系はスキップ、論文の海・document 直付け discuss・わたしの記録は実行）→ 審判 A/B/E → findings 6 件 →
report.md / ik_candidates / map_draft.md。

**発見と記帳（課題ナレッジ）**:

| 発見 | 起票 | 是正 |
|---|---|---|
| 新規ビルドのイメージが SQLAlchemy 2.1 の既定ドライバ変更で起動しない（砂場構築時） | IK-0360 | **解決**: requirements の上限 `<2.1` + `test_requirements_db_driver_pin.py`。再ビルドで起動を確認（reproduction_rerun / docker_e2e） |
| 教材の詳細 API が `document_id` を返さない（一覧は返す） | IK-0361 | **是正済み・検証中**: `get_material` の SELECT に `d.id` を足して組み立て |
| 解析中に提供元の課金が尽きても run と教材が「解析完了」になる（縮退・repair_failed に吸収） | IK-0362 | 未着手（完了判定と縮退語彙の設計判断が要る） |
| G層 `materials.none` が本人所有だけを数え、公開教材でコースを作れる教員に「教材なし」を案内 | IK-0363 | 未着手 |
| RAG 検索の質問文埋め込みが `unattributed`（usage_context の外） | IK-0364 | **是正済み・検証中**: 検索を当該ターンの feature の内側へ + `test_llm_usage_attribution` に固定 |
| 課金切れでも縮退文が「しばらくしてからもう一度」（一時障害の文面） | IK-0365 | 未着手 |

**ハーネス側で分かったこと（製品の課題ではない）**: コース不成立時に学生段を走らせると precondition スキップが
並ぶ（campaign の notes どおり「未実施」として畳む方がよい）/ `learning.atlas.view` はコース未選択で 422 を返すので
precondition に course を要求する / 6 件の findings のうち 5 件は同じ原因（提供元の課金切れ）で、fingerprint の
束ね方に「原因の共有」の軸が要る（IK-0362 に集約）。

**未実施（事実）**: 実ペルソナの周回 / browser runner / 審判 C・D（LLM 審判）/ `course-astro-v1` の作成（コースが

### 17.3 第 2 周・第 3 周（実ペルソナ・Claude Code の頭脳で肩代わり・2026-09-27）

**LLM の肩代わり（P-0011）**: OpenAI の残高が無いまま、①ペルソナ側は `UXSIM_PERSONA_LLM_PROVIDER=mailbox`
（`uxsim/mailbox.py`。1 要求 = 1 ファイル `req-N.json` → `res-N.json`）、②製品側は OpenAI 互換の proxy
`uxsim/sandbox/claude_proxy.py`（砂場の api-server に `OPENAI_BASE_URL=http://host.docker.internal:8090/v1`。
製品コードは非改変）で、**この Claude Code セッションの子エージェント（brain）が両方の要求に応えた**。
埋め込みは Claude では作れないので本物の OpenAI へ素通し（残高ゼロの間は RAG 検索が空 = fail-soft）。
vision・ストリーミングは proxy v1 で 400（製品は縮退）。`claude -p`（`claude_cli` プロバイダ）も置いたが、
この機の CLI は未ログインで使えない。1 brain が 30〜40 件に応え、campaign は mailbox を最大 45 分待つので
brain を順に継ぐ運用（第 2 周 = 19 件、第 3 周 = 40 件 + 続き）。

**第 2 周（run 20260927T044913Z・ハーネス修正前）**: 教員ペルソナは「教材なし」の案内（IK-0363）と
arXiv 番号だけの題名（IK-0366）に混乱し、コースビルダーが依頼文（宇宙論入門）と教材（混合主題）の不一致で
草案を出さず `blocked` で停止。学生 2 名はコース無しで諦め／停止。findings 9。
**ハーネス側の修正**: 観測の切り詰め 1500 → 6000 字（一覧の行が空に見えた）/ 教員 te-04 の依頼文を
コーパスに合う「論文紹介ゼミ（混合主題）」へ / brain への指示を「seed は既定・自分の言葉が勝つ」に。

**第 3 周（run 20260927T052839Z）**: 教員ペルソナがコース `900588b4` を登録・地図に紐付け・公開まで到達。
学生 2 名が受講登録 → コース → 地図 → 論文の海 → わたしの記録を歩いた。46 ステップ・findings 20
（審判 C の friction 由来）。**ただし登録されたコースは章 4・トピック 0** — 原因はハーネスの
`admin.course_builder.register` が草案を素通しし、画面（`admin.js::approveCourse`）が行う「章の中の
トピックを平らにして `topics[]` に写す」変換を写していなかったこと（PE9 の違反。`_draft_to_course_create`
で是正し `test_register_mirrors_ui.py` で固定）。同じく「ペルソナの入力文が台本文に置き換わる」
（`_one` の seed 優先）と「選んだ教材が全件に広がる」（`_cb_chat`）もハーネス側で是正した。
これらは**製品の課題として起票しない**（起票元は本節）。

**製品側の発見（起票）**: IK-0366 題名が arXiv 番号のまま（構造化は題名を持つ場合がある）/ IK-0367 詳細 API の
has_pdf 欠落（**解決**）/ IK-0368 地図の「検証: 原文0本に裏付け」の自己矛盾 / IK-0369 受講登録の応答が既定値の
フラグ / IK-0370 覆えなかった「章」に図の目盛・ヘッダ・参考文献が並ぶ。brain の観測で**UI で要確認**のもの
（API runner は DTO を見ているので UI に出るとは限らない）: 学習者向け DTO の `uncovered_sections`・教員向け
`decision_context` の生表示・`particle_physics` の未翻訳・discuss プロンプトの「即答」と「未踏ガード」の同居・
原稿スタジオの生チャンク（`Unsectioned chunk group`）・リリース前の確認で所有しない公開教材が全て
`editable:false`（確認 0 件）・enroll 直後の一覧の食い違い。
作れなかった）/ 回帰シナリオの replay（v1 は note だけ）。

**第 4 周（run 20260927T061823Z・ハーネス是正後・snapshot 復元から）**: 教員ペルソナがコース `7d74c7ae`
（2 章・16 トピック）を登録・公開し、**学生 2 名が受講登録 → コースを開く → トピックを読む → document 直付け
discuss で質問 → わたしの記録 → 進捗**まで到達した（42 ステップ・findings 21 = 審判 C）。学生が踏んだ製品側の事実:
①受講登録の応答が「非公開・受講不可」（IK-0369）②トピックの題と本文が別の論文（IK-0371: 位置番号の handle が
登録時に別の候補表で解決）③本文に ⚓ が無く要素文脈が開けない（②の帰結）④教材中に列挙された式番号「6」を
要素文脈の element_id として開くと 404（式の印字番号と要素 ID の取り違え — ペルソナの操作を UI が許すかは
browser runner で要確認）⑤分野の地図が同じコースで 200（st-03）と 404（st-04）（要調査）⑥論文の海がコースの
論文しか含まない（4 本投入したが配置は 2 本）⑦進捗が「学んだ概念 0・誤解 0」の数値（学習者向けの数値表示 —
UI で要確認）⑧全画面が日本語で英語話者は読めない（製品の前提。記録のみ）。
ハーネス側では「ターンごとに教材の選択が揺れる」を `cb_selected` で固定した（IK-0371 の再現条件を消す
ものではなく、再現条件を**教員の実操作**に限る是正）。

**LLM の肩代わりの実測**: brain 6 便で合計 117 要求（第 2〜4 周）。1 要求あたり 10〜60 秒で、course builder の
草案（16 トピック・handle・前提付き）も brain が書けた。製品側の OpenAI 消費はゼロ（埋め込みは残高ゼロで失敗し
RAG 検索は空 → 回答は「出典を追えない AI の説明」の帯が付いた）。

**第 6 便の追記**: 学生のトピック内チャット（`learning.chat.ask`）は第 4 周でも**一度も実行されていない**。
⚓ が無く要素文脈を開けなかった直後にペルソナが `blocked` を記録し、経路の `stop_when: [blocked]` が
その時点で経路を終えたため（チャットの一歩手前）。ハーネスの停止条件が厳しすぎる（blocked 1 回で経路終了）
— 次周までに「HTTP エラーか連続 2 回の blocked」に緩める。分野の地図は同じコースで前半 200・後半 404 で、
コース内容の生成完了後に binding の導出が変わった可能性がある（要調査）。教材の要素文脈は式の印字番号を
element_id と取り違えて 404（UI がその操作を許すかは browser runner で確認）。

**未実施（第 4 周時点）**: browser runner / 審判 C・D の LLM 判定（friction の前処理だけ）/ `course-astro-v1` の
snapshot 化（IK-0371 の影響でコースの中身が正しくないため作らない）/ 回帰シナリオの replay。

### 17.5 第 5 周・第 6 周（台本 5・ペルソナ倍増・Fable 指揮 + Opus 実装／頭脳・2026-09-27〜28）

**編成**: 台本 5（教員 1 + 学生 4）、教員 2 名（te-01・te-04）・学生 4 名（st-01・03・04・06）。campaign は
`c-corpus-to-course-x2`。実行は `uxsim/runs/run_x2.sh` が教員 2 → 学生 4 を**プロセス並列**（mailbox はプロセス
ごと・run_id に pid を足した）で回し、頭脳は mailbox ごとに 1 体 + 製品用 1 体（Opus）。製品 proxy は当初
要求を直列化していたため `asyncio.to_thread` で並行化。頭脳は使用量の上限で全滅することがあり（第 6 周の途中）、
再投入で続行した。

**第 1 波（実施前に既知の問題を並列修正・Opus 3 体）**: IK-0362（run 末尾に U層の失敗を要約し `stage_outputs.llm_failures`
+ G層 `material.analysis_llm_failed`）/ 0363（可視教材で判定 + `materials.shared_available`）/ 0365（課金・上限の事実文）/
0366（構造化の題名を documents.title へ書き戻し）/ 0368（0 本のときの検証行）/ 0369（enroll 応答の実値）/
0370（節名の妥当性検査）/ 0371（handle に stable_key を同梱・登録は key 解決）。
**第 5 周（run 20260927T135738Z 教員・141026Z 学生）の発見** → 第 2 波（Opus 3 体）: IK-0373 共有教材の案内が
コース作成後も残る（解決）/ **IK-0374 内容生成の書き戻しが地図の紐付けを消す**（解決: FOR UPDATE 再読・併合・
processing/failed の記帳）/ IK-0375 生成前の生チャンクに告知なし（解決: preparation_notice + app.js）/ IK-0376 リリース前
確認の飛ばし理由（解決: skipped_note + admin-release-review.js）。製品頭脳がプロンプトの実物を読んで見つけた
**IK-0377 式・根拠 ID の文書横断衝突**（第 3 波で解決: (document_id, local_id) の索引と `_BundleScope`）/ IK-0378
チューター規則 1 と未踏ガードの矛盾（仮説・未着手）/ IK-0379 生成文脈の切り詰め・端点空の依存行（仮説・未着手）。
**第 6 周（run 20260927T144156Z 教員・145343Z 学生・第 2 波後）で確認できたこと**: 教員 2 名とも 4 章・20 トピック・
出典 4 本のコースを公開し、内容生成中も紐付けが残って学生全員が地図と位置づけを開けた（0374）/ 受講登録の応答が
実値（0369）/ 共有教材の案内が公開後に消えた（0373）/ 飛ばし理由が応答に載った（0376）/ 教材 DTO に「準備中」が載った
（0375。ただしハーネスの投影が描いておらず学生には見えなかった → 投影を修正）。**学生のチャットはまだ未確認**:
ハーネスの HTTP タイムアウトが別設定（130 秒）で切れていた（930 秒に統一）。第 7 周（学生 2 名のチャット経路のみ）で確かめる。

**ハーネス側の欠陥（この 2 周で直したもの）**: 草案の投影が章の中のトピックを数えない（te-04 が 4 ターン混乱して諦めた）/
一覧の投影が 3 件目以降を空行にする（教員が 4 本中 2 本しか選べなかった）/ 教材の選択を「詳細を開いた 1 本」に
推定していた / 停止条件 `blocked` 1 回で経路終了 / run_id の衝突 / 製品 proxy の直列化 / タイムアウト 2 系統。
**教訓**: API runner の「投影」は画面の代わりなので、画面と同じ情報量・同じ構造で描かないと、ペルソナの混乱が
製品の欠陥と区別できない（第 5 周の教員段の friction の多くが投影由来だった）。

### 17.6 第 7 周（学生 2 名のチャット経路・第 3 波後・2026-09-28）

第 3 波（IK-0377 = 式・根拠の文書スコープ化）を入れて再ビルドし、コース `6be229e1` の内容を再生成
（製品側 LLM を肩代わりした頭脳 2 体が 20 トピックの文脈を読み、各トピックの式・主張・抜粋が自分の論文に閉じたことを
確認 — IK-0377 の実機確認）。その後 st-01・st-06 に「受講登録 → 最初のトピック → 詰まるまで質問」を歩かせた。
**学習チャットの経路が初めて端から端まで通った**: 回答 200（出典 8 件・content_grounding=course_material）、書き直し
（当該メッセージ以降の破棄）、楽屋、記号の直前定義、出典原文（source-chunk）まで。1 応答 25〜65 秒（頭脳の遅延）。
学生の引っかかりから起票: IK-0380 記号定義の相反する 2 文 / IK-0381 出典チップが全て同じ表示。ハーネス側: 教材と
出典原文の投影を 500 字で切っていたため「途中で切れている」という偽の friction が出ていた（切り詰めを撤廃）/ 同名の
コースが 2 本並ぶ（教員 2 名が同じ依頼文から同じ題で登録 — 分野パックの依頼文の題を教員ごとに変える）。
埋め込みは依然 OpenAI 残高ゼロで失敗するため RAG は「教材セクションが見つからない」経路のまま（回答は出典付きで
返るが、これはコースの sources 由来の文脈による）。**残高が戻ったら同じ campaign を再演して、検索ありの経路を確かめる。**

**第 7 周・頭脳（st-06）の追加観測（起票候補・未確認）**: ①追いの質問に直前の回答が逐語で返った（再送か
キャッシュか要調査）②回答で「使っていない」と言った出典（別論文）が出典一覧に残る（採用と提示の区別）③チャット
履歴の DTO に score の生値・tier・content_grounding が載る（学習者向けの数値 — UI に出るかは browser runner で確認）
④楽屋の質問が通常履歴に印なしで混ざる（記録面の私有化は済んでいるが履歴表示での区別）。製品頭脳（第 7 周）の観測:
⑤**出典番号がターンごとに振り直され、履歴に残る過去の回答の `[出典N]` が別のチャンクを指す**（起票候補・根拠が強い）
⑥同じコースの別論文のチャンクが出典に混ざる（コース範囲の検索の設計どおりだが、学習者は「この論文」と読む）
⑦第 7 周のチャットには出典 6〜8 件が付いていた（埋め込みの失敗が続く中で出典が付く経路 = 表示中教材の
チャンクと構造 grounding。検索ありの経路との違いは残高回復後に確認）。

**未実施（この波の時点）**: browser runner / 審判 C・D の LLM 判定 / IK-0362・0365・0366 の砂場での再現（提供元の失敗・
再解析が要る）/ 課金ありの RAG 経路 / IK-0378・0379・0380・0381 の是正。

### 17.7 第 8 周（埋め込みあり・学生 4 名 × 7 経路・第 4〜6 波・2026-09-28）

OpenAI の残高が戻ったので、製品側 LLM は引き続き proxy → Claude Code の頭脳で肩代わりしつつ、**`/v1/embeddings`
だけを本物の OpenAI に素通し**する構成で `c-full-x2` の学生段（st-01 / 03 / 04 / 06 × 7 経路 × 2 セッション）を回した。
api-server ログに埋め込み失敗ゼロ・チャット要求に出典ブロック 8 件、で **RAG 検索ありの経路が初めて実ペルソナに当たった**。
頭脳は学生用 4 体（30〜45 件で交代）+ 製品用 2 体（seq の奇偶で分担 — 学生 4 名が 1 つの製品 mailbox に集中し、
1 体では待ち行列が伸びた）。運転の前に、ハーネス側で審判 dialogue（出典番号の振り直し・区別できない出典・古い回答の
再送・学習者向け応答の数の欄・precondition の連鎖 = 「ハーネス:」）、応答本文の digest、セッション開始時のコース一覧の
先取り（course_id / topic_id の既定）、審判 C の原因（行為 × 詰まり方）束ねを足した。

**製品の是正（第 4〜6 波・すべて host の pytest まで。砂場での再現は第 9 周）**
- 第 4 波（IK-0379 / 0380 / 0381）: コース内容生成の文脈が JSON の途中で切れる・依存関係の端点が空（原因は保存グラフの
  キー名 `source_component_id` を読んでいなかったこと — 「端点名の未解決」は誤診）/ 記号の定義なしに相反する 2 文 /
  出典チップが同一表示（`meta` に節見出し + 冒頭を載せる。断片見出し「K」「DE」は IK-0370 の判定で除く）。
- 第 5 波（IK-0382〜0393）: 表示中教材の注入だけで「教材に基づく」になる grounding（内容語の一致で判定）/ 前提知識
  ゲートが文の「いいえ」・楽屋の質問に同じ文を返すループ / 学習者向けコース DTO の内部件数・UUID・ゴミ見出し / 受講
  応答の成功事実文 / 記号 lookup の理由なし縮退・λ が Λ に当たり別論文の定義が返るスコープ漏れ / section_block 単位の
  トピックに根拠ゼロ（unit の section_ids を読んでいなかった）/ 本文の `[[FORMULA_N]]` 未解決 / 参考文献・データ入手先・
  脚注 URL のチャンクが出典に混入 / 検索チャンクの数式プレースホルダを LLM に未解決で渡していた / tension プレフィルタが
  4 文字の英単語で全発話にヒント / 構造ヒントの主張の語中切断と「et al.」での文分割（A層の文分割を正本化）。
- 第 7 波（IK-0412〜0421）: 地図の `ledger_status` と文の不一致（unrecorded を導入）/ 位置づけ・論文の海・記録・進捗・今日の言葉・
  帰還の扉の内部値と事実文 / 既存教材の題名バックフィル（起動時・placeholder の題名のみ）/ **document_structure の題名が
  PyMuPDF 経路で一度も取り出されない**（3/4 論文が GROBID 縮退。TEI → 1 頁目の最大フォント → なし、の決定論フォールバックを
  A層に置く。IK-0420）/ GROBID 縮退の理由が記録されず 503 も再試行されない（IK-0421。砂場の一括投入で 3 本が縮退した
  有力候補）。UI 側は確認問題の「違っていた」非完了・帰属の事実文・pill_note・論文の海の事実行。
- 第 6 波（IK-0394〜0411）: 未踏ガードと即答規則の衝突（= IK-0378 の実測確認: 原因は tier 集約 — 0.30〜0.45 の出典 1 件で
  `out_of_source` になりガードが付いた）・
  ⚠️ 注意書きと出典の矛盾と履歴への焼き込み・履歴の 2000 字切り・内容語の無い相づちで検索 0 件・前提ゲート後の元質問の
  再処理・点数要求の受け皿・進捗の食い違い（確認問題で完了しても course.open は in_progress）・discuss の鏡と本文・tension
  validator の日本語語尾必須・`_discussion` の内部名・レクチャーの数式・reconstruction の exhausted 事実文・確認問題の
  「同意しない」で完了・学習者 UI（受講の事実文・記号 lookup の facts・楽屋の印・日付の現地時刻・同名コースの副題）。

**ハーネス側の是正（第 8 周中）**: 教材投影の `[[FORMULA_N]]` を画面と同じ規則で数式に置換（引けないものは残して製品欠陥を
隠さない）/ course.open の投影が `_generic` の 8 件上限で第 4 章を「…ほか」に隠していた → 章とトピックを全部並べる /
トピックを開いたら確認問題の文を投影に添える（問題文を見ずに答えていた）/ 経路の任意材料 `topic_hint_terms`（分野に
無ければ渡さない）で材料の質問に合う章のトピックを開く（宇宙論の質問を Cep B のトピックで投げていた）/ 分野の
scenario_params を実コーパス（4 論文）に合わせて書き直し（`s-check-and-object` は第 4 章の音速の論文へ）/ 繰り返しの外の
`stop=true` は経路を終える（`persona_stop`）。

**起票せず残した観測**: 英語利用者への日本語固定部分（回答の調子・出所・確認チップ・ラベル・？使い方 = i18n の構造
課題）/ 生成教材の数値取り違え（SF と ACF の値）/ discuss の末尾必須問いが終了の発話にも付く / 保存系の応答が ok:true だけ /
locked 表示のトピックが API で開ける（UI のみのロック）/ 砂場を snapshot 復元せずに再演したため前周の痕跡（動機・
intention）が残った — 第 9 周は復元してから。

**未実施**: 第 4〜6 波の砂場再現（再ビルド → 第 9 周）/ browser runner / 審判 C・D の LLM 判定 / IK-0362・0365・0366。

### 17.8 第 9 周（是正の砂場再現・学生 3 名 × 5 経路・2026-09-28）

第 4〜7 波を入れて api-server / frontend を再ビルドし、コース `1bad7d9f` の内容を再生成（20/20 トピック・t0 に根拠 12 件。
主結果・未検証点の 3 トピックは根拠 0 のまま = thesis_support 単位の是正は未着手）してから `c-verify-wave456`
（st-01 / 06 / 04 × 受講・質問・確認問題・discuss・地図）を回した。**運転の誤り 2 つ**: ①同名コース 2 本のうち
runner が「受講中の先頭」で拾ったのは再生成していない `6be229e1` で、内容依存の是正（t0 の ⚓・生成時の数式）は
未検証のまま（→ campaign の `course_id` 固定を追加）②前の周の痕跡（今日の言葉・記録・持ち越しの問い）が混ざった
（→ `uxsim/sandbox/reset_learner_state.py` = 学生ペルソナの痕跡だけを戻す運転の手。auth_events / llm_usage_events は
消さない）。加えて frontend の再ビルドで api-server も再起動し走行中の内容生成が失われた（`--no-deps`）。

**砂場で消えたことを確認**: 出典付き回答の ⚠️ / 出典ゼロの「教材に基づく」/ 帰属確定応答の数値 / 確認問題の事前提示 /
学習者コース DTO の件数・UUID / 記号 lookup の事実文 / discuss の本文と鏡 / 出典ラベルの節・冒頭 / 進捗の `_discussion` /
点数要求の固定文 / 検索チャンクの数式解決。**残った**: 前提ゲートの「ループ」は s-ask-until-confused の繰り返しが
misread で終わり、ペルソナがゲートに答える手番を持てなかったハーネス起因（until を本人判断に変更。API で直接
再現すると履歴にゲートが残る通常経路は脱出できる）。書き直し（ゲートの往復が履歴から消える）と前提の内容を
言い換えて聞いた場合は再ゲート → 第 8 波 a（ゲートはトピックごと 1 回・言い換えの照合・英語の承認と英語の
ドリルダウン）。ほか: 感謝の一言に検索 8 件と立場表明 / 別論文の軸ラベル 1 件で「教材に基づく」/ 出典「冒頭」が語の
途中から（是正済み）/ 確認問題の問いの選択・自己確認の語（ハーネス是正）/ 数式縮退文「出典の区画」（言い換え）。
製品頭脳が読んだ生成プロンプトから: **他論文の式・図・部品が「この節で使う数式・図」の付録に混ざる**（IK-0377 の
是正が付録に及んでいない）/ 確認問題の要件に `eq_blk_…` / 図の軸目盛りが数式 / 同一プロンプトの二重送信 →
第 8 波 b。GROBID 縮退の理由なし（IK-0421）と PyMuPDF 経路の題名（IK-0420）は再解析まで砂場に効かない。

### 17.9 第 10 周（第 8〜10 波の砂場再現・コース固定・痕跡リセット・2026-09-28）

第 8 波 a/b・第 9 波を入れた最新ビルドで、campaign に `course_id: 1bad7d9f` を固定し、`reset_learner_state.py --yes` で学生
ペルソナの痕跡を戻してから `c-verify-wave456` を再演した（途中で使用量上限に当たり 1 回やり直し）。3 名とも exit 0。
審判: A 4 / B 9 / C 52（第 8 周の A 16 / B 11 / C 107 から）。**砂場で消えたことを確認**: 前提ゲートの反復（「いいえ」の
文字入力・言い換え・英語の承認とも脱出）と省略時の事実文 / 英語の `[Ask more about]` ボタン / 「違っていた」の非完了と
事実文 / 帰属確定の数値 / 感謝への出典なし短文 / 出典ラベルの語境界 / 前周の痕跡の混入 / 確認問題の事前提示。
**残った・新しく出た**: ①出典番号 — セッション内安定化（IK-0432）は効いているが（seq 16 で 9〜16）、同じチャンクが
[出典31]→[出典50] と付け直される事例と、ハーネスの投影が位置番号で描いていた誤り（是正済み）が重なって「一覧は 1〜8・本文
は 9〜56」に見えた。前者は第 11 波で原因追跡 ②参考文献・データ入手先・著者所属のチャンクが出典に残る（第 5 波の判定を
すり抜ける）③ゲート省略の事実文が以後の全ターンに付く ④t0 の ⚓ ゼロと `![[source:topic_summary]]` の生表示（配信時の
source 埋め込みが未解決）⑤inline 式の縮退文が J=3–2 のような単純記号にまで及ぶ（A層の inline 式候補に latex が無い）
⑥固定文（点数・承認・違っていた）の日本語固定（英語話者）⑦出典原文 DTO に review_reason / bbox / base64。
製品頭脳が読んだ生成プロンプトの残課題（参照本文なし・式の切り詰め・古い注記の残存・要約の重複・部品の注意点）は
第 10 波（IK-0438〜0443）で是正済み（砂場の再生成は未実施）。ハーネス側の是正: 出典番号の投影を DTO の index に /
出典番号で原文を開く / quick_label の言い換え / 経路の最後の画面を次の経路へ引き継ぐ / campaign の course_id 固定。

### 17.10 第 11〜12 波と第 11 周（2026-09-28 夕）

第 10 周の残りを host で是正した。**第 11 波**（学習チャット、IK-0444〜0451）: ①出典番号の付け直しの原因は保存経路 —
`persist_chat_history` が毎回クライアントの `body.history` で上書きし、`_rehydrate_history_sources` の本文照合が
ドリルダウンマーカーの有無でずれて sources が落ちていた（窓の外の往復も落ちる）。是正は assistant_meta の累積
`citation_map`（chunk_id → 初出番号）を全保存経路で持ち越し、学習者向け GET からは除く（IK-0444）②参考文献・所属・
謝辞のチャンクを `non_content_chunk_reason` の 3 判定（`[N]` 連なり・非本文見出し・所属行）で除外（IK-0445）③ゲート省略の
事実文は履歴に無いときだけ（IK-0446）④締めの返事は `content_grounding=None`（IK-0447）⑤出典原文 DTO を
`_learner_source_passage` で絞る（IK-0448）⑥前提説明の出所は経路①のため正しい — rejected（IK-0449）⑦固定文の英語版
（IK-0450）⑧ドリルダウン札の `$…$` 除去（IK-0451）。**第 12 波**: JSON 経路（確認問題レビュー・帰属・tension・R層・D層・
標準化）が `response_format` を送っていなかった → `generate_text` に `response_format` を足し `json_call` /
`BaseJSONLLMClient.complete_json` から `json_object` を要求（「JSON」語の無いプロンプトでは落として旧経路 — IK-0452）/
`thesis_support` 単位の topic が根拠ゼロ（unit 行に thesis の claim 参照が無く `_section_unit_refs` が section_block しか
見ない）→ `_thesis_support_unit_refs`（IK-0453）/ inline 式候補は `raw_text` しか持たず縮退文に落ちていた →
`inline_formula_text`（≤ 40 字・内部 ID を含まない）を本文へ、appendix の見出しから内部 ID を除く（IK-0454）。
host 全件 17,543 + 2,210 + 241 pass。**第 11 周**: 再ビルド → 痕跡リセット → 1bad7d9f 再生成 → `c-verify-wave456` を再演
（結果は下に追記）。

**第 11 周の結果（17:32〜19:45 JST・st-01 / st-04 exit 0・st-06 は頭脳の使用量上限で 53/55 歩で exit 1）**:
審判 A 3 / B 6 / C 50（第 10 周 A 4 / B 9 / C 52）。A 3 件は全て nginx 120 秒超過で、頭脳の応答遅延（使用量上限
直前）による砂場側の事象。B 6 件のうち 4 件は論文内容の百分率（68% CL・1% 精度）で審判の誤検出（同日、裸の百分率を
当てないよう審判 B を修正）、2 件は LS8 の登録数（設計どおり・allowlist 化）。**砂場で消えたことを確認**: 出典番号の
トピック内安定（3 名とも同一チャンクは同番号を維持 — 製品頭脳が全要求の指紋で確認）/ 参考文献・所属・謝辞チャンクの
引用（主論文では 0 件）/ ゲート省略の事実文の反復 / 英語話者への固定文（点数・承認・違っていた）/ ドリルダウン札の
`$…$` / JSON 経路の `response_format`（check-review・structure_anchor・tension が `json_object`）。
**残った**: ①番号はトピック・discuss を跨ぐと 1 から再開（設計＝トピック単位。判断は第 13 波で記録）②書き直し後に
[出典18] が別チャンクに再利用（往復が履歴から消える経路 — 第 13 波 b）③別論文（Cep B）のチャンクが暗黒エネルギーの
質問に混入・structure_anchor の候補も他論文に寄る（第 13 波 b で topic 出典 document を優先）④他論文の脚注 URL・
「Software citation…」断片・軸目盛りだけのチャンクが出典一覧に残る ⑤感謝への返事に出所行（日本語話者側で残存）
⑥ドリルダウン札が本文の `[98]` で壊れる・書き直し後の札に `\alpha_K` 生 TeX ⑦〔鏡〕の "" 引用・差し替え文の日本語
固定 ⑧確認問題の観点が設問と食い違う（第 13 波 a）⑨再構成の問いが出題ゼロ（砂場に教員承認済み claim が無い —
砂場準備に承認 + オーサリングの段を足す）⑩t18 の根拠ゼロ・`![[source:topic_summary]]`・読書モードの式が別の式に
解決（IK-0455〜0458、第 12 波 c/d で host 是正済み・砂場は次の再生成で確認）。

**第 12 波 c/d・第 13 波（09-28 夜、host）**: 第 12 波 c = `![[source:topic_summary]]`（生成プロンプトと配信で意味が
食い違う擬似 ID → `source:excerpt` に統一・未解決埋め込みは配信で落とす）/ t18 の根拠ゼロ（figure 単位の
`linked_figure_ids` を誰も読まない → `_figure_unit_refs`）/ **読書モードで式が別の式に解決**（`build_topic_slides` が
`[[FORMULA_N]]` を振り直すのに content_blocks の formulas をそのまま渡していた — 砂場再現で 214 個が縮退文・41 個が別式。
IK-0457）/ 第 12 波 d = レクチャーの埋め込み未解決カード（IK-0458）。第 13 波 a（生成プロンプト、IK-0459〜0465）=
ゴミ式の除外（理由付き・`excluded_equation_candidates`）/ 復元式の raw_text 併記と注意の全トピック発火 / 観点の水増し廃止 /
古い注記の除去 / claim 240 字 + 重複統合 / 原文抜粋の整形 / t0・t1 の根拠同一化（要旨ブロックが 5 単位に共有され上限を
占有 → 単位固有ブロック優先）。第 13 波 b（chat、IK-0466〜0478）= 書き直しで消えた往復の番号を予約（[出典18] 再利用の
真因 = `truncate_chat_and_supersede` 後の map 欠落）/ トピック単位の番号は設計として記録 / 鏡の英語引用 + 英語事実文 /
前提承認の語彙拡張 + トピック横断 / structure_anchor には実際に引用したチャンクだけ・感謝は流さない / 未踏ガードは
DOMAIN_RAG のみ / トピックの出典 document を優先する再ランク / discuss の未踏ガードは予測を求めない / tension 例示 ID
の衝突解消 / prejudge にトピック題名・概念名 / ドリルダウン札の入れ子 `[98]` と `\cmd` の平文化。IK-0475（前ターンの
引用チャンク再注入）は open。host 全件 17,653 + 2,210 + 241 pass。砂場準備に `uxsim/sandbox/approve_claims.py`
（教員として source_backed claim を承認 → 承認フックが R層 item をオーサリング）を追加し、R層の経路を初めて到達可能に
した（item 174 件）。第 12 周 = 再ビルド → reset → 再生成 → `c-verify-wave456`。

### 17.11 第 12 周の食い違いの切り分けと第 14 波（2026-09-29、host）

**第 12 周は検証になっていなかった**: ペルソナが t0 / t18 に `![[source:topic_summary]]` と観点の水増しが「まだある」と
報告したのに、DB と学習者 API は再生成後の内容を返していた。原因は投影ではなく**別のコースを歩いていた**こと — 痕跡リセットで
受講が外れ、runner は campaign の `course_id: 1bad7d9f` を「受講可能な候補」に置くだけだったため、同名コース 2 本を見た
ペルソナが上の `6be229e1`（未再生成）を受講した（transcript seq 4）。製品側に別経路・キャッシュは無い（material は
`learning_courses.data` のみ・版の発行もしていない）。是正（IK-0506）: `uxsim/sandbox/pin_course.py`（同名の他コースを
admin API 経由で非公開化・既定 dry-run）/ campaign の固定と違うコースを開いたら `pinned_course_mismatch` を
runner_note・meta・審判 A（「ハーネス:」）・レポート見出しに残す（ペルソナの選択は上書きしない）。あわせて教材投影が
`![[kind:id]]` を生のまま渡していた（画面はチップ・カードに描く）のを `render_material_text` で画面と揃えた（IK-0507）。

**第 14 波（UI なし backend を 4 並列・ファイル所有を分割）**: 生成プロンプト（IK-0484〜0489: 観点の水増しは前回の
key concepts の言い換え由来 / 短い値条件の断片・復元フラグ / 本体の無い式 / 原文抜粋の表・凡例・語中開始・重複 / 現在の
下書きの stale 埋め込み / 予算切り詰めの保護キー）、学習チャット（IK-0492〜0495・0475: 文脈チャンクの U+FFFD と arXiv
刻印 / 相づち付きの感謝の出所行 / 引用していない文脈チャンクの出典一覧（引用ゼロで grounding ありの回は grounding を
決めた文脈を出す）/ 偽の assistant 前置きターン / 前ターンの引用チャンクを番号付きで最大 3 件再注入）、再構成と画面文脈
（IK-0500〜0503・0483: 主張の種類「不明」の行を出さない / 同一引用の包含重複 / predict の下地を式の参照と信頼方針から
引く・restate の理由を記帳 / defined_symbols の dict 文字列化）。element_vocab に式由来の主張種別 4 語を追加。
host 全件 17,759 + 2,210 + 258 pass。**未実施**: 砂場（Docker にこのセッションの OS ユーザーから届かず停止）での再ビルド
（第 13 波 c・第 14 波）→ `reset_learner_state --yes` → `pin_course --yes` → 1bad7d9f 再生成 → `run13_verify.sh`。

### 17.12 第 13 周（第 12 波 c〜第 14 波の砂場再現・2026-09-30）

Docker Desktop を dev セッションで起動し直してもらい、砂場を再ビルド（第 13 波 c・第 14 波込み）→ `reset_learner_state --yes` →
`pin_course --yes`（同名の `6be229e1` を private に）→ 1bad7d9f 再生成（20/20・`source:topic_summary` 0 件）→ `run13_verify.sh`
（st-01 / 04 / 06 × 5 経路・頭脳 = ペルソナ 3 + 製品 2 で product 88 要求）。3 名とも exit 0・**`pinned_course_mismatch` は空**
（固定コースを歩いた最初の検証周）。審判 A 2 / B 6 / C 38（第 11 周 A 3 / B 6 / C 50）。

**A の 2 件 = 再構成の初回提出が全件 500（IK-0508）**: R層 item が初めて配信され、初めて submit に到達して露見。改訂元なしの
`CASE WHEN :rev = '' THEN NULL ELSE CAST(:rev AS uuid)` を PostgreSQL が計画時に畳み込んで落ちていた → `CAST(NULLIF(:rev,'') AS uuid)`
+ 同型 SQL を禁じるガードレール。api-server だけ再ビルドし、同じ item・同じ学習者で 200 と照合結果を確認した。

**砂場で消えたことを確認**: 教材の生の埋め込み・`eq_blk_` / `eq_eqcand` の画面露出 / 読書モードの式の別式解決 / 他論文チャンクの
出典一覧（st-06・st-04。st-01 は discuss で 1 件残存）/ 感謝の出所行 / 同じ番号の別チャンク再利用 / 確認問題の観点の水増し（生成
プロンプトの現在の下書きでも・学習者の確認問題まで）/「主張の種類: 不明」/ 再構成の出題ゼロ（`next_step` が出題された）/
前ターン引用の再注入（「（前の回答で引用した箇所）」で届く）。

**残った・新しく出た（第 15 波候補）**: ①再構成 next_step の問い文が「前の式」「次の式」だけで式を画面に出さない / claim_context に
`sec_runin_…`・`method_choice` ②式の短い断片が残る型（`w(a) = w0`・`Pclump = PB`・`t ≥ tmin`・節見出しの式化）— 学習者の数式欄に
見える ③教材に生成側の注記（「根拠候補の式は途中で切れているため…」）が出る・原文抜粋に「（数式）」・改行ハイフン ④要素文脈の
下位レーンに `ev_0115` / `fig_6` / `derivation_claim_0007` と英語の生成文、節名「25'」⑤出所の食い違い（前提説明経路は出典 0 で
「教材に基づく」・履歴の tier=out_of_source と grounding=course_material・点数拒否の固定文に「出典を追えない AI の説明」）⑥履歴の
窓が長い assistant 本文を**先頭から**切る（語中開始）/ discuss の no-hit 回で前ターンの論文を引き継がない / document 直付け discuss で
前ターンが引き継がれない疑い ⑦前提ゲートが各学生の t18 最初の質問・誤解の訂正質問にも出る ⑧UTF-8 の文字途中切断（位置づけの
理由・記録・discuss 本文）⑨わたしの記録で言い直しが「誤解」、進捗は誤解 0 ⑩記号 lookup が別論文を探す ⑪位置づけに件数・
`particle_physics`「名前が登録されていない分野」・理由なしの未配置 / locked の t18 が開ける ⑫英語話者への日本語固定（構造課題）。
**運転側の注意（製品の欠陥として扱わない）**: 製品頭脳を seq の奇偶で 2 体に分けたため、同じ学生の会話の往復を 2 体が交互に
書いた。st-06 の「存在しない過去の議論（Figure 1・比較表）を引く」「往復で助言が反転する」はこの交互応答と、1 体が複数の学生の会話を
捌いたことによる**頭脳側の持ち込み**（偶数番の頭脳が 00030・00066 で別の学生のスレッドの内容を持ち込んだと自己申告）の産物で、
起票しない。次周からは製品頭脳を「学生（mailbox の由来）ごと」に割り当てる（要求の messages から会話を識別できないため、
proxy が学生を区別できる手を足すまでは製品頭脳 1 体で直列に捌くか、学生を 1 名ずつ走らせる）。

- 2026-09-27 起草。前提となる現物調査（テスト基盤・CI・compose・シード・fixture・アカウント／コース API・
  学習側 API 全ルート・観測テーブル・指標カタログ・フロント計測・固定事実文・CostGate・G層・課題ナレッジの規約・
  マニュアル構成）は本書 §16 に要約。以後の実装記録は Phase ごとに本節へ追記する。

## 18. 増強: 論理構造の見やすさ・辿れる理解・対話支援の 30 場面（2026-09-30 起草）

第 13 周までの campaign は「受講の一通り」を歩く経路が中心で、**論文の論理構造がどう見えるか・分からなかったときに
関係を辿って理解を深められるか・AI との対話がそれを支えるか**は、各経路の一部として偶然踏まれるだけだった。本節は
この 3 つを主題にした 30 の場面（story）を定義し、campaign `c-astro-structure-30` として走らせる。場面は
「ペルソナ（行動型）× 経路（新設 13 本 + 既存 2 本）× 見るもの」の組で、1 行 = 1 場面。**経路は分野の語を書かず**
（§11.5）、分野固有の値は `scenario_params` に置く。

### 18.1 主題と見るもの

| 主題 | 見るもの（審判 B・C の watch_for に落とす平叙文） |
|---|---|
| A 構造の見やすさ | 中心命題→支持構造→式・図の順に読める / 主グラフ・式の詳細・理論モジュール・論文の順の切替で全体像が掴める / 内部 ID（`eq_op_`・`ev_`・`sec_runin`・UUID）や英語の生成文が学習者・教員の画面に出ない / 数（件数・confidence）が出ない |
| B わかりやすさ | 式は印字番号と記号で語られ、復元由来の式にはその印が付く / 記号の定義がタップ位置より前から引ける / 二層説明（一般・この論文での意味）が同じ要素で区別して読める / 固定文が読み手の言語で出る |
| C 辿って深める | ⚓ → 要素文脈 → 上位・下位レーン → 隣の要素 → 旅 の 1 hop 移動が途切れない / 出典チップ → 原文 → 主張への参照が往復できる / 降下路・楽屋・記号・近傍（いまここの周り）が「どこで分からなくなったか」を絞れる / 分野の地図 → 位置づけ → 論文の海 → document 直付け discuss が接続する |
| D 対話による支援 | 「この式はどこから」「前の回答のどこが根拠」に構造（主張・段階）で答える / 選択箇所の一致確認文が出る / 誤解の訂正で鏡（言い直し）が本人の言葉を引く / 足場かけは一点だけ / 教員のグラフ全体対話が論文の事実と処理の記録を分ける / ノード対話から根拠 claim の承認に閉じる |

### 18.2 新設する行為（PE9 — 画面の部品かマニュアル節に対応づける）

| 行為 id | 画面の部品 / マニュアル | 製品ルート |
|---|---|---|
| `learning.component.context` | `material-element-anchor`（⚓ の component チップ） | `GET /api/learning/courses/{course_id}/components/{component_id}/context` |
| `learning.component.context_hop` | 文脈 SVG の component ノードクリック（「旅」） | 同上（直前の `graph` の隣接 component を中心に再取得） |
| `learning.chunk.claim_refs` | 出典本文の主張参照 | `GET /api/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs` |
| `learning.chat.ask_selection` | `selection-ask`（テキスト選択→「ここについて質問」） | learning chat（`selection_text` + `screen_context`） |
| `learning.chat.cycle` | `understanding-cycle`（予想→並置） | learning chat（`cycle_mode: elicit|diff`） |
| `learning.atlas.threads` | `atlas.relation-threads` | `GET /api/atlas?...`（`threads` キーの有無を見る） |
| `learning.atlas.neighbors` | わたしの地図「いまの地図」の霧 | `GET /api/me/personal-network/atlas-neighbors` |
| `admin.paper_layer.view` | `graph-review.paper-view` | `GET /api/admin/documents/{document_id}/paper-layer` |
| `admin.theory_modules.view` | `graph-review.module-view` | `GET /api/admin/documents/{document_id}/theory-modules` |
| `admin.theory_modules.related` | 同・詳細ペイン | `GET .../theory-modules/related` |
| `admin.graph_review.node_chat` | `graph-review.open-deliberation`（ノード対話 = W層セッション） | `POST /api/admin/deliberation/sessions` + `.../messages`（`screen_context` に `graph_node`） |
| `admin.deliberation.overview` | `deliberation.open`（深く検討） | `GET /api/admin/deliberation/elements/{element_type}/{element_id}/overview` |
| `admin.graph_review.approve_claim` | `graph-review.claim-approve` | `POST /api/admin/claims/{claim_id}/review` |
| `admin.seminar_brief.view` | 教材行 ⋯「ゼミ前ブリーフ…」 | `GET /api/admin/documents/{document_ref}/seminar-brief` |

runner が API に送る引数は行為の `args` だけ。ペルソナは画面に出た ID（既知 ID）しか使えない。

### 18.3 30 場面

記法: `S` 学生 / `T` 教員。ペルソナは `domains/astrophysics/personas/`。経路 id は `scenarios/goal/`。「見るもの」は
経路の `watch_for` に平叙文で写す（点数・閾値を書かない）。

| # | 主題 | 誰 | 経路 | 場面（story） | 見るもの |
|---|---|---|---|---|---|
| 1 | A | S st-01 newcomer | `s-read-backbone` | 教材の ⚓ を順に開き「この論文は何を主張し何で支えているか」を掴もうとする | 要素文脈の在り方（中心命題での役割・位置）が読める / 内部 ID 非露出 |
| 2 | A | S st-06 skeptic | `s-read-backbone` | 同じ経路を出典と検証状態を確かめながら歩く | 検証の事実文が閉世界語彙 / 出典が開ける |
| 3 | A | S st-02 confident_theorist | `s-discuss-backbone` | discuss 開幕の中心命題→支持構造→「確かめていないこと」を読み、支持構造の一つを議論する | 開幕の 2 区画の主語が分かれる / 議論が構造に係留する |
| 4 | A | T te-03 meticulous | `t-structure-layers` | グラフレビューで主グラフ→式の詳細→理論モジュール→論文の順を切替えて全体像を掴む | 層ごとにラベルが短い stage 名 / 内部 ID 非露出 / 件数非表示 |
| 5 | A | T te-02 veteran | `t-structure-layers` | 同じ経路をクリックスルーで急いで歩く | 初期表示（モジュール図）で迷わない |
| 6 | A | T te-01 rookie | `t-paper-order-coverage` | 論文の順で章→ノードを追い「掛かっていない章」を見つける | 被覆の事実文 / 章見出しで語る |
| 7 | B | S st-10 newcomer | `s-symbols-chain` | 式の多いトピックで記号を順にタップし、定義→前提の説明へ進む | ScholarPhi 規則（前の定義）/ 定義なしの事実文 / 別論文を探さない |
| 8 | B | S st-04 non_native | `s-symbols-chain` | 英語で同じことをする | 固定文の言語 / 英語での記号説明 |
| 9 | B | S st-08 lecture_listener | `s-lecture-then-trace` | レクチャーを聴き、分からない箇所で止めて記号→⚓→質問 | スライド分割で論理の流れが追える / 復元式の印 |
| 10 | B | S st-01 newcomer | `s-two-layer-explanation` | 同じ component の一般説明とこの論文での意味を読み比べる | 二層が区別されて出る / 承認済みだけが出る |
| 11 | B | S st-09 adjacent_field | `s-two-layer-explanation` | データ科学の語彙で読み替えながら同じことをする | 一般説明が分野外の読者に通じる |
| 12 | B | S st-06 skeptic | `s-check-then-reconstruct` | 確認問題→違っていた→再構成 next_step で式が画面に出るか | 出題文が式そのものを示す / 採点語彙が出ない |
| 13 | C | S st-01 newcomer | `s-trace-one-hop` | 分からない語の ⚓ → 要素文脈 → 上位レーン → 隣の component → 旅、と 1 hop ずつ辿る | 各 hop で前の文脈が失われない / navigable の fail-closed |
| 14 | C | S st-03 adjacent_field | `s-trace-one-hop` | 素粒子の語彙で同じ hop を辿る | 上位関係ゼロが unidentified で出る（推測穴埋めなし） |
| 15 | C | S st-06 skeptic | `s-source-roundtrip` | 出典チップ→原文→主張参照→教材へ戻る往復 | 出典番号が安定 / claim-refs が原文の位置を指す |
| 16 | C | S st-05 night_parttime | `s-source-roundtrip` | 短時間で同じ往復（途中で止める） | 途中で止めても記録の欠落が事実文で分かる |
| 17 | C | S st-01 newcomer | `s-descend-and-nearby` | 降下路→楽屋→「いまここの周り」で分からなくなった場所を絞る | 依存の向きだけが描かれ推測辺が無い / 台帳ゼロの縮退 |
| 18 | C | S st-02 confident_theorist | `s-descend-and-nearby` | 同じ道具を「確かめられているか」の枠で読む | 検証ラベルが差分のときだけ出る |
| 19 | C | S st-06 skeptic | `s-map-to-sea-to-discuss` | 分野の地図→推定の糸→論文の位置づけ→論文の海→document 直付け discuss | 糸は点線 + 骨格版の事実文 / 位置づけの出所ラベル / discuss の縮退明示 |
| 20 | C | S st-04 non_native | `s-map-to-sea-to-discuss` | 英語で同じ道を歩く | 地図・海の固定文の言語 |
| 21 | C | S st-07 voice_first | `s-return-and-journey`（session 2） | 翌日、扉→持ち越し→わたしの地図→旅→「この場所の周りを見る」 | 旅の縮退が notice + 精密化の出口 / 別コースの兄弟の事実文 |
| 22 | D | S st-01 newcomer | `s-ask-where-from` | 「この式はどこから出てきた？」「前の回答のどこが根拠？」と構造を問う | 構造 1 hop（主張・段階）で答える / 出典番号の持ち越し |
| 23 | D | S st-06 skeptic | `s-ask-where-from` | 同じ問いを「それはどこに書いてありますか」で押す | 出所ラベルと本文が食い違わない |
| 24 | D | S st-03 adjacent_field | `s-select-and-ask` | 教材の一文を選択して「ここが分からない」 | 一致確認文 / 帰属カードの anchor_label |
| 25 | D | S st-06 skeptic | `s-misconception-mirror` | 自分の誤解（宇宙分散＝装置誤差）を前提に質問し、訂正を受ける | 鏡が本人の言葉を逐語で引く / 訂正が断定でなく出典を示す |
| 26 | D | S st-05 night_parttime | `s-stuck-scaffold` | 「わからない」と短く言い続ける | 足場かけが一点だけ / 前提ゲートが繰り返さない |
| 27 | D | S st-02 confident_theorist | `s-elicit-diff` | 予想を書いてから並置（elicit→diff）で自分の理解を確かめる | 正誤・点数の語彙が出ない / 並置が事実の並び |
| 28 | D | T te-03 meticulous | `t-graph-dialogue-to-approve` | グラフ全体対話「理論構成をおおづかみに」→ノード対話→根拠 claim の承認 | 論文の事実と処理の記録が混ざらない / ラベル二重出力なし / 承認は人の操作だけ |
| 29 | D | T te-04 copilot_dependent | `t-deliberation-figure` | 図を深く検討し、本文での言及→主張→component を読み、Copilot に手順を聞く | メンション文が主役 / ITEM→関係語の語順 / Copilot は道案内まで |
| 30 | D | T te-05 delegator | `t-seminar-brief` | ゼミ前ブリーフを開き、学生の詰まりの引き継ぎを読む | 閉世界語彙 / k-匿名 / 数値非表示 |

`s-return-and-journey` は `s-return-next-day` に旅の「この場所の周りを見る」を足した経路。既存の `s-check-and-object`・
`s-discuss-one-paper` は本 campaign では新経路に置き換える（重複を避ける）。

### 18.4 運転

campaign `c-astro-structure-30` は学生段のみ（教員場面は教材 `7ef9e487` / `7954db7d`（グラフ・論文層あり）を既存の
まま読む）。頭脳は P-0011 のとおり Claude Code の子エージェントが mailbox で肩代わりする。**製品頭脳は要求ごとに
その `messages` だけから答え、前の要求の内容を持ち込まない**（第 13 周の「頭脳側の持ち込み」の再発防止。学生ごとに
分けられない proxy の制約はこの規律で吸収する）。周の前に `reset_learner_state --yes` → `pin_course --yes`。
結果は審判 → 報告 → 課題ナレッジ候補 → 是正（波）→ 再演の順で、本節の末尾に周ごとの記録を足す。

### 18.5 第 14 周（2026-09-30）

- **運転の順**: `reset_learner_state --yes` → 受講登録の抜け（固定コース 1bad7d9f への受講が一部ペルソナで欠けていた）→ 砂場の再起動 →
  c-astro-structure-30 の 30 場面（§18.3）を api runner で運転。ペルソナの頭脳は mailbox の子エージェント 8 体（学生 6・教員 1・
  製品 proxy 1）、製品の LLM 呼び出しは proxy 経由で 56 要求。
- **審判の件数**: A 8 / B 15 / C 130。A の 8 件はすべて nginx の 120 秒超えで、製品の LLM 呼び出しが頭脳（mailbox）で待たされた
  遅延だった。
- **ハーネス側の是正**（uxsim/ のみ）:
  - 教材の投影に画面の ⚓ を番号付きで列挙し、`learning.element.context` に `element_ref`（番号か要素 id）を足した。
    これまでは常に最後に覚えた要素が開き、ペルソナが別の ⚓ を選べなかった。
  - `learning.source_chunk.open` に `source_no`（直前の回答の「出典N」）を足した。直前の回答に出典が無いときは製品を呼ばず
    precondition で止める。
  - `learning.personal_network.nearby` / `journey` / `learning.atlas.neighbors` は、ノードを覚えていなければ画面と同じく先に
    「わたしの地図」を読み込んでからノードを選ぶ。nearby に `center_component_id` を足した（範囲表示から点表示へ移る）。
  - 受講応答の投影を画面（`enrollCourse`）に合わせ、題名と notice だけにした（is_template / is_enrollable / visibility を出さない）。
  - `admin.graph_review.node_chat` は `deliberationTargetId`（DB UUID → 代表要素 → linked_component_ids）でノードから実体要素を
    解決し、解決できないノードでは製品を呼ばない（主グラフのノード id をそのまま送り 404 になっていた）。
  - `learning.chat.ask_selection` は教材に実在する文だけを送る（`selection_ref` = 区画番号:文番号、または本文に含まれる
    `selection_text`。無い文は precondition で止める）。
  - 場面パラメータの `topic_hint` と選択文をコース 1bad7d9f の実トピック・本文に合わせた（s-misconception-mirror を t18 へ、
    s-lecture-then-trace の題名、s-select-and-ask の選択文）。
  - 審判 A は、製品の LLM が頭脳で動いている周では、製品 LLM を呼ぶ行為の 120 秒超えを「ハーネス:」印で出す（数値の検査は残す = PE7）。
- 第 15 波の製品側の報告を受けたハーネスの追加是正:
  - `admin.graph_review.chat` も画面（`getScreenContext`）と同じ形の `screen_context` を送る（node_chat と組み立てを共有）。
  - s-elicit-diff の予想のあとの発話は、画面と同じ議論の会話（`learning.discuss.ask`）へ送る。
  - 教材一覧の投影に参照の健全性チップの文言（「参照: 問題なし」等）を出す。理論モジュール related の投影は題名と事実文だけにし、
    内部のモジュールキーを出さない。
  - 確認問題は、表示中の問いの本文とその `check_question` を対で送る（ペルソナの言い換えで先頭の問いと組み合わさっていた）。
    再構成の選択肢は label だけを投影し、ペルソナが選んだ label か番号を option_id に写す。
  - 「辿る」系の 6 場面（s-read-backbone / s-trace-one-hop / s-two-layer-explanation / s-descend-and-nearby / s-ask-where-from /
    s-source-roundtrip）を、部品とグラフのある 2 論文（PINN の中性子星 EOS・連星ブラックホールの DTD）のトピックへ向け直した。
    暗黒エネルギーの論文と Cep B の論文は部品が fallback だけでグラフも無い（A層の欠落）。
  - 部品 ⚓ を出すためのコースの再 freeze は、製品 API に内容再生成（`POST /api/admin/courses/{id}/course-content/generate`・
    LLM を呼ぶ）以外の経路が無い（content_blocks は `build_course_content` だけが作る）ため、`regenerate_content.py` を使う。
- 製品側の欠陥と是正は第 15 波（別記）。

### 18.6 第 15 波（製品側の是正・2026-09-30）

第 14 周（§18.5）の発見のうち製品側の欠陥を 6 担当で是正した。ハーネス側は §18.5 が正本で課題ナレッジに起票しない。
各課題は candidate 起票・検証はユニット / ガードレールのみで、砂場の再演は未実施（第 15 周で確かめる）。

- **学習チャット経路**: 前提の逆質問を 1 コース×トピック×学習者で一度だけにし、ゲートのターンでも元の質問に答える・内部都合の
  前置きを廃止（IK-0509）/ 予想（elicit）の入力から採用チャンク本文を外す（IK-0510）/ 引用ゼロ×教材無関係は model_generated（IK-0511）。
- **出典**: 番号順 + `cited` フラグ（IK-0512）/ トピック外・短すぎるチャンクの除外（IK-0513）/ 本文の途中始まり・終わりの「…」、
  行末改行の正規化、404 の日本語事実文（IK-0514）。同番号が別チャンクに見えた件は LLM の誤帰属（番号対応は正常）→ IK-0545 に残す。
- **要素文脈と部品 ⚓**: section_block トピックに主張経由の部品を束ねる（IK-0515）/ 主張の親子曖昧解消と未解決の `available:false`
  （IK-0516）/ ITEM v2 の agent ID 遮断を claim・equation に拡張（IK-0517）。
- **記号・降下路・近傍・糸**: lookup の字形正規化と `chunks.formulas` フォールバック（IK-0518）/ 降下路の target_label と空段の理由
  （IK-0519）/ `_discussion` の表示名（IK-0520）/ グラフ未構築論文の事実文（IK-0521）/ 糸の `{available:false, note}`（IK-0522）。
- **教員側構造**: related の FACT_RELATED_NONE（IK-0523）/ 式 ID の読み時マスク（IK-0524）/ モジュールの出所ラベル・subtitle・
  plain_math（IK-0525）/ 見出し誤認の畳み込み（IK-0526）/ SA層 document_graph に章・式の所在（IK-0527）/ grounding の二重出力
  （IK-0528）/ 集約ノードの事実文（IK-0529）/ Copilot capability `materials.deliberate_figure`（IK-0530）/ ゼミ前ブリーフの台帳合成
  （IK-0531）/ 次にやることの件数除去（IK-0532）。
- **再構成・受講・表示**: 確認問題の照合順（IK-0533）/ 既受講の文言（IK-0534）/ 名指せない step を出題しない（IK-0535）/
  今日の言葉の空の事実文（IK-0536）/ audio-status の note（IK-0537）/ カートリッジ骨格の domain.json（IK-0538）/
  壊れた復元式の非描画（IK-0539）。
- **未解決（open）**: A層の comp_fallback のみ（IK-0540）/ PDF 経路の主記号欠落（IK-0541）/ 自己確認 1 問で完了（IK-0542）/
  再構成 submit・descend の DTO（IK-0543）/ 出典の所在が書き出し（IK-0544）/ 引用と本文の一致検査なし（IK-0545）/
  主グラフ stage 名が英語（IK-0546）/ 1 手順の外枠モジュール（IK-0547）/ discuss 鏡の規則の矛盾（IK-0548）/
  target.material_id の ID 体系（IK-0550）。
- **オーナー判断候補**: 学習者向けの件数表示を LS8（コーパス事実行）と CR3（数値非表示）のどちらで読むか（IK-0549。
  不変条項の解釈の衝突に当たる）。

### 18.7 第 15 周（是正後の再演・2026-09-30）

- **運転**: 第 15 波（§18.6）の是正後、c-astro-structure-30 の 30 場面を同じ固定コースで再運転し、trace 系 2 場面は
  ハーネス是正のあとに再演した（無効になった run は `uxsim/runs/archive_run15_invalid_trace/` に退避）。第 14 周の run は
  `uxsim/runs/archive_run14_structure30/runs/`。頭脳は子エージェント 8 体（学生 6・教員 1・製品 proxy 1）+ trace 再演用 2 体。
  途中で使用量の上限に当たって中断し、同じ場面から復帰した。
- **審判**: 件数は run とレポートだけに置く。A はほぼ消え、B は審判 B の誤検出 3 種（「N 点」の列挙・viewBox とレクチャー契約・
  `$…$` 区切りの数式）を是正してから数え直した。C は減ったが残る。ハーネス印の所見は製品の課題に数えない。
- **GONE（第 15 波の是正が再演で効いた）**: 前提ゲートの繰り返しと質問の取りこぼし（IK-0509）/ 出典の番号順（IK-0512）/
  記号 lookup の字形・フォールバック（IK-0518・部分）/ 降下路の空段の理由（IK-0519）/ related の事実文（IK-0523）/ 式 ID の
  マスク（IK-0524）/ 見出し誤認（IK-0526）/ ブリーフの台帳合成（IK-0531）/ 既受講の文言（IK-0534）/ audio-status の note
  （IK-0537）/ カートリッジ骨格の分野名（IK-0538）。各エントリの `resolution.verification` に `reproduction_rerun` を足した。
- **STILL / NEW（第 16 波で是正したもの）**: 記号 lookup が別論文の定義を持ってくる / 式 ⚓ の論文またぎ衝突 / 部品の依存先が
  辿れない / 付録の無関係な式 / 議論中の論文が前提説明・痕跡に効かない / 否定文中の出典を引用に数える / 直前の出典の読み直しで
  別チャンク / 謝辞・題名・概要チャンクの採用 / ゲート後の口語の否定 / 相槌の帰属 / anchor_confirm の手掛かり欠落 / ブリーフの
  内部 ID / 番号なし式の副題 / grounding の同文重複 / 主ノードの日本語表示名 / 表の見出しの章誤認 / 相手のいない related /
  教材詳細の document_id → §18.8。
- **STILL / NEW（未解決・open 起票）**: 上位レーンが id=None（IK-0570）/ A層由来の英語生成文・英語 ⚓（IK-0571・構造課題）/
  ⚓ 一覧が全部区画 0（IK-0572）/ t12 の本文が Phase 1 だけ（IK-0573）/ 英語話者にも日本語のゲート（IK-0574）/ 鏡が同意の前置きを
  復唱（IK-0575）/ 訂正の再訂正（IK-0576）/ grounding にスライド位置・復元式の印なし（IK-0577・SA層 4-c 保留）/ 記号 lookup が
  式本文を返さない（IK-0578・設計判断）/ 内側モジュールの左辺同一の重複（IK-0551）/ 出典の場所が書き出し（既存 IK-0544）。
- **ハーネス側の是正**（uxsim/ のみ・起票しない）: 審判 B の誤検出 3 種 / ⚓ 番号を component_id に送っていた
  `_component_context` の解決 / element_type `theory_claim → claim` の写し / 記号・要素文脈の要求に `topic_id` / anchor_confirm の
  投影（発言全文の再掲をやめ prompt と選択肢）/ trace 場面の再演。
- **残るオーナー判断候補**: 学習者向けの件数表示を LS8 と CR3 のどちらで読むか（IK-0549）/ `target.material_id` の ID 体系
  （IK-0550。第 16 波で `get_material` は両形を受けるようにしたが、体系の一本化は未決）。

### 18.8 第 16 波（第 15 周で見えた欠陥の是正・2026-09-30）

3 担当で是正した。各課題は candidate 起票・検証はユニット / ガードレールのみで、砂場の再演は未実施（次の周で確かめる）。

- **範囲と 1 hop（g1）**: 記号 lookup を「この論文」（chunk の document → 表示中トピックの論文）に限定し `topic_id` を受ける・
  別論文の同記号と不在を事実文で返す（IK-0552）/ 式 ⚓ をトピックの論文で優先解決し複数なら未解決（IK-0553）/ 部品の文脈の
  依存先を同一論文の live component に解決（IK-0554）/ 付録から本文が触れない 1 文字記号＝数値の式を除く（IK-0555）。
- **チャットの検索と帰属（g2）**: 議論中の論文の優先（IK-0556）/ 否定文中の [出典N] を引用に数えない（IK-0557）/ 直前の引用
  チャンクを id で読み出す（IK-0558）/ 無内容チャンク 3 種の除外（IK-0559）/ ゲート後の口語の否定と前提トピックの教材本文
  （IK-0560）/ 相槌を帰属対象から外す（IK-0561）/ anchor_confirm の `excerpt` と `prompt`（IK-0562）。
- **教員側の漏れ（g3）**: ブリーフの内部 ID を表示ラベルへ（IK-0563）/ 番号なし式・内側モジュールの副題（IK-0564）/ grounding の
  同文・双方向の畳み込み（IK-0565）/ 主ノードの日本語 `display_label`（IK-0566）/ 表の見出しの章誤認と親の付け直し（IK-0567）/
  related は相手のいる外枠だけ + `checked_module_keys`（IK-0568）/ `get_material` が document_id でも解決（IK-0569）。

### 18.9 第 17 周（4 つの根の是正後の再演・2026-10-01）

- **運転**: 4 つの根の是正（表示投影 / 焦点論文 / TeX 既定 / 応答骨格 — `root_cause_consolidation_2026-09-30.md`）を含む
  HEAD 9e078b1 で砂場を再ビルドし、c-astro-structure-30 の 30 場面を固定コース 1bad7d9f で運転した。頭脳は子エージェント
  （学生 6・教員 1・製品 proxy）。部品 ⚓ の文脈を開く 6 場面（st-01 s-read-backbone / s-two-layer-explanation / s-trace-one-hop、
  st-03 s-trace-one-hop、st-06 s-read-backbone、st-09 s-two-layer-explanation）は、ハーネスが部品文脈の要求に `topic_id` を
  添えておらず（実フロントは添える）、論文またぎの agent ID の衝突で製品が設計どおり fail-closed（FD4）して 404 になった。
  ハーネスを是正して 6 場面を再演し、無効になった run は `uxsim/runs/archive_run17_invalid_component/` に退避した。
  **コーパスは PDF 経路で取り込んだ既存のまま**（再取り込みはしていない）。
- **審判**: 決定論の審判（A・B・C の前処理）のみで、LLM 審判（`--judge`）は使っていない。有効 run 30 本で A 10 / B 11 / C 101。
  A は 9 件が nginx の 120 秒超え（製品の LLM 呼び出しが mailbox の頭脳で待たされた遅延 — 第 14 周と同じ性質）、1 件が
  「出どころの問いに構造で答えていない可能性」。B は英語話者への日本語の固定文（IK-0574 系）・日本語話者への英語の生成文
  （IK-0571）・学習者向け応答の数の欄が主。件数は run とレポートだけに置く。
- **頭脳・ハーネスの割り引き**: ペルソナが見た UUID・`id=…`・`theory_op_0001`・`claim_span_*` は、ハーネスの画面投影が実画面に出ない
  宛先キー（id・*_id・stable_key・refs）を印字していたため（01513fa で是正）。学習者向け部品文脈の DTO の表示キーに内部 ID が無い
  ことは砂場の API で確かめた。製品頭脳は自身の規律違反を 3 件報告した（前の要求の内容の持ち込み 1 件 — 学習者向け回答が
  「論文の解析で再構成された前提」を論文の内容と並べた件はこれ / 末尾に確認の問いを 2 つ並べた 1 件 / 出典の弱い引用 1 件）。
  2 つ目は配信された応答でサーバが鏡 1 つに削っていたことを transcript で確かめた（応答骨格が効いた証拠として数える）。
- **4 つの根の砂場確認の位置**:
  - **応答骨格（根 4）— 対話で確認**: 前提ゲートでも先に答える・確認の問いは 1 つまで（2 つ目はサーバが落とす）・鏡は核心語で
    前置きを復唱しない・直前の訂正を次の往復で覆さない、を確かめた。早期 return 経路（学習相談・前提説明）は対象外のままで、
    そこから NEW が出た（IK-0586）。
  - **焦点論文（根 2）— 対話で確認**: 別論文の記号が定義に混ざらない・discuss で議論中の論文が優先される・式 ⚓ が表示中の論文で
    開く。衝突時の fail-closed が主張 ⚓ の文脈を開けなくしている疑い（IK-0589）と、早期 return 経路の焦点（IK-0586）が残る。
  - **表示投影（根 1）— ガードレールと API 確認のみ**: 学習者向け DTO は API で清浄を確かめたが、再演での確認はハーネス是正
    （01513fa）後の次の周。教員向けは `project_for_teacher` 未配線で、グラフレビュー・論文の順・理論モジュール・ゼミ前ブリーフの
    応答に内部 ID が残る（並行の是正は本記録では参照しない）。
  - **TeX 既定（根 3）— 再取り込みが必要**: 本周は PDF 経路のコーパスなので確かめられない。IK-0540 / IK-0541 / IK-0547 / IK-0551
    は想定どおり STILL。
- **GONE（第 15 周・第 16 波の欠陥で消えたもの）**: 前提ゲートが質問を落とす・2 度目の再提示（IK-0509）/ 鏡の規則の矛盾と末尾の
  問い返しの重なり（IK-0548）/ 鏡が同意の前置きを復唱（IK-0575）/ 訂正の再訂正（IK-0576）/ 別論文の記号が混ざる（IK-0552）/
  式 ⚓ の論文またぎ（IK-0553）/ 部品の依存先が辿れない（IK-0554・1 hop は移れる）/ 議論中の論文が優先されない（IK-0556）/
  DIFF に正誤・点数の語彙が出ない（UC2 継続）/ グラフ全体対話の返答に内部 ID が出ない / 式 ID のマスク（IK-0524・主グラフの
  ラベルは「番号なしの式」）/ モジュールの出所ラベル・閉世界の言い方 / 出典の場所が節名で出る（題名を持つ PINN の論文では「冒頭」でなく節名 — IK-0544 の部分）/ 部品 ⚓ の 404
  （ハーネス是正後）。各エントリの `resolution.verification` に `reproduction_rerun` を足した（IK-0509 / 0524 は既存。related の事実文 IK-0523 は本周の場面で観察できず再確認していない）。
- **STILL（A層由来・PDF 経路の損失）**: 英語の ⚓ 本文・部品の summary / teaching_takeaway / narrative_role（IK-0571）/ 論文の題名が
  arXiv 番号のまま（IK-0419 / IK-0420 の書き戻しは再解析が要る）/ 出典の所在が「冒頭」（IK-0544・PDF 論文側）/ 記号レジストリの
  主記号欠落（µ(a)・α_B・α_K・P・N_c — IK-0541）/ ⚓ 一覧が 25 件超で区画が読めない（IK-0572）/ 「この節で使う数式」に本文と
  無関係な N = 15（IK-0555 の除外条件に当たらない本文側の式）/ 部品が comp_fallback だけの論文にグラフが無く、議論の開幕・周りを見る
  から外れる（IK-0540）/ 切れた図キャプション・行末ハイフン（Interest-ingly）/ 主結果の主張が解釈・比較の主張の下位に置かれる・
  式 (6) が式 (5) から導かれる等の導出の向き（PDF 経路の導出）/ 部品の上位レーンが id=None（IK-0570）/ 回答経路に復元式の印と
  式番号が届かず、出典本文だけが「AI 復元」と言う（IK-0577）/ 回答の引用と本文の一致検査なし（IK-0545）。教員側は内部 ID が教員向け
  DTO に残る（`project_for_teacher` 未配線）・英語の stage 名が対話に出る（IK-0546。5ddc1e3 で 3 経路を訳したが砂場未確認）・
  表の見出し「Estimates (90% CI)」が章として残る（IK-0567 の判定が当たらない形）・ブリーフの脆い前提が「（本文を特定できない要素）」
  で中身が無い（IK-0563 の置き換え先が空）。
- **NEW（分類）**:

  | 症状 | 分類 | 根拠 |
  |---|---|---|
  | 降下路が `available: false` だけで理由なし | ハーネス + 製品 | ハーネスが表示名「式 (3)」を element_id に送った。解決不能の縮退に事実文が無いのは製品（IK-0585） |
  | 出所「出典が提示できない参考情報」で出典チップなし → 往復不成立 | 製品 | 表示中トピック t8 の教材を問う質問で製品の要求に教材コンテキストが無く未踏ガードが立った（IK-0583） |
  | 出典往復での claim_refs 404 | ハーネス | `topic:t8` を chunk_id に送った。実画面は topic 教材の claim-refs の失敗を黙って捨てる |
  | 音声ペルソナに画面向けの回答 | ハーネス | discuss では音声入力が製品側で無効（fail-closed）で、ハーネスは `screen_mode` を送らない。実画面では起きない経路 |
  | 「わからない」連発で長文化・【歓迎と目標】テンプレ | 製品 | 短い否定が前提ゲートの「いいえ」に、「どこから読めばいい？」が学習相談の固定構成に取られた。早期 return は応答骨格・焦点の対象外（IK-0586） |
  | session 2 の扉・持ち越しが空 | ハーネス | 場面の precondition（前のセッションの書き置き）を痕跡リセット後に満たしていない（session 1 の段が無い） |
  | 旅が空 | 製品（既知の縮退）+ ハーネス | `_discussion` に係留した 1 問だけで、事実文と精密化の出口は出ている。前日分が無いのはハーネス |
  | nearby が議論中の論文を外す | A層上流 | 論文 2606.00411v1 にグラフが無い（IK-0540）。外したことは事実文で出ている |
  | 〔出典〕英文の二重表示 | 製品 | source の evidence item で title に summary を入れ、カードが見出しと本文を両方描く（IK-0584） |
  | locked トピックが開ける・進捗が追随しない | 仕様 + ハーネス | locked は「前のトピックの確認が未完了」の柔らかい印で開ける（実画面はトーストで説明）。進捗は確認問題でだけ進む。ハーネスの投影がトーストを出さない |
  | レクチャー音声なし | 砂場の状態 | 砂場で TTS を生成していない（外部 API を呼ばない方針）。理由文は出ている |
  | 一般知識の但し書きが文中に割り込む | 製品（仮説） | discuss プロンプトのルール 7 が定型句を逐語で例示している（IK-0587）。実運用の LLM での再現は未確認 |
  | discuss 開幕に骨格なしの論文 | A層上流 | 部品が comp_fallback だけでグラフが無い（IK-0540） |
  | 子部品へ 1 hop 移ると行き止まり・戻れない | 製品（仮説） | 子部品の文脈の上位に親が出ない（IK-0588） |
  | 主張 ⚓ の要素文脈が「特定できませんでした」 | 製品（仮説） | 焦点の論文の中でも agent ID が曖昧で fail-closed になる疑い（IK-0589） |
  | 回答が自信を「9割以上」と数で出した | 判定保留 | 学習者の求めに製品頭脳が答えた 1 件。プロンプトのルール 8 は検索件数・一致度を禁じるが自己評価の数は書いていない |
  | Copilot が題名の問いに論文レーダーの手順で答え、空の「ボタン:」が残る | 製品（未確認） | マニュアル本文の素通しと KB 検索の当たり。原因を確かめていないので起票しない |

- **ハーネス側の是正**（uxsim/ のみ・起票しない）: 部品文脈と 1 hop の要求に表示中トピックの `topic_id`（c22f668）/ 審判 B が
  教材の埋め込みマーカー `![[kind:id]]` を内部 ID と数えない（c22f668）/ 画面の投影から宛先キー（id・*_id・stable_key 等）を
  外し、旅の手掛かりは移動できる行だけに残す（01513fa）。残る写し漏れ: 降下路に表示名を送る / topic 教材に claim-refs を呼ぶ /
  locked のトーストを投影しない / s-return-and-journey の session 1 を走らせない。
- **残るオーナー判断候補**: 学習者向けの件数表示を LS8 と CR3 のどちらで読むか（IK-0549・継続）/ `target.material_id` の ID 体系
  （IK-0550・継続）/ 既存コーパスを TeX 経路で取り込み直すか（根 3 の砂場確認の前提。再取り込みは外部取得を伴う）。

### 18.10 第 18 波（第 17 周の是正・2026-10-01）

検証はユニット / ガードレールのみで、砂場の再演は未実施（次の周で確かめる）。

- **教員向けの表示投影（8199668）**: グラフレビュー・論文の順・理論モジュール・related・ゼミ前ブリーフ・W層の概要 / 文脈 /
  グラフ全体対話を `project_for_teacher` に通した（DP7）。IK-0579 の教員向けの残りを配線した。
- **表示中トピックの出典と式の事実（3123f2d・cb301bb の learning.py）**: 表示中トピックの出典チャンクを問いの文面に依らず
  [出典N] として採用する（IK-0583 解消）/ 復元式の印・式番号を回答の材料に渡す（IK-0577 部分 — スライド位置の SA層 4-c は
  未着手）/ 学習相談の概念・トピックをトピック範囲に絞る（IK-0586 部分）。
- **応答の骨格と焦点（7b90670）**: 「か。」で終わる問いの検出と詰まりの区画（足場 1 つ・問い返しなし・前提ゲートは次の往復へ。
  IK-0586 部分 — 早期 return 経路で効くのは詰まりの区画だけ）/ 旅と近傍に焦点論文の層（引用した論文に絞り、グラフが無ければ
  事実文で止める・近傍は黙って広げない。IK-0588 部分）/ 降下路の不能理由と次の操作の事実文（IK-0585 解消）。
- **宛先の一意化（cb301bb）**: 主張 ⚓ の宛先を論文内で一意にし、UUID → block 修飾 → 素の番号の順で解決・曖昧は事実文
  （IK-0589 解消）/ document_id は常に documents.id、両形の判別は `_resolve_document` と `core/document_refs` に限定し
  ガードレールで固定（IK-0550 解消 — §18.9 のオーナー判断候補から外れる）。
- **生成言語（5ddc1e3・同日先行）**: 主グラフの stage 名の英語露出 3 経路を訳語表に通した（IK-0546 解消）/ 生成言語の run option を
  A層の生成 4 系統に通した（IK-0571 部分 — 既存 run は再解析が要る）。
- **ハーネス（f0108f8・01513fa・c22f668・起票しない）**: 音声ペルソナを実 UI と同じ casual + voice で送る・claim_refs は実在
  チャンクだけ・場面の前提（preconditions / seed_scenarios）・調子チップの表示条件を app.js に揃える / 宛先キーを投影から外す /
  部品文脈と hop に表示中トピックを添える。いずれも §18.9 の偽所見を防ぐ是正。
- **是正中（別担当）**: IK-0584（出典カードの見出しと本文の重複）/ IK-0588 の残り・IK-0570（部品の上位レーン）。

### 18.11 第 18 周（第 18 波の検証・2026-10-01）

- **運転**: HEAD e27ee17（第 18 波 8199668・3123f2d・7b90670・cb301bb・e27ee17・5ddc1e3 と、ハーネス f0108f8・01513fa・c22f668）で
  砂場を再ビルドし、c-astro-structure-30 のうち第 18 波の主張に当たる場面だけを固定コース 1bad7d9f で運転した（2026-10-01 14:32〜14:48
  JST）。有効 run 13 本（`uxsim/runs/c-astro-structure-30/20261001T05*/`）。頭脳は子エージェント 4 レーン（a = st-05 s-source-roundtrip /
  s-stuck-scaffold + st-07 s-return-and-journey、b = st-01 s-trace-one-hop / s-descend-and-nearby + st-03 s-trace-one-hop、c = st-02
  s-discuss-backbone / s-descend-and-nearby + st-06 s-misconception-mirror、te = te-03 t-structure-layers / t-graph-dialogue-to-approve・
  te-05 t-seminar-brief・te-01 t-paper-order-coverage）。頭脳を付けずに走った先行の試行は `uxsim/runs/archive_run18_no_brain/` に退避した
  （無効）。**コーパスは PDF 経路のまま**。
- **審判**: 決定論の審判のみ（`--judge` は使っていない）。有効 run 13 本で A 0 / B 2 / C 45（件数は run とレポートだけに置く）。B は日本語話者の
  議論の開幕に英語の生成文（IK-0571 系・A層）と、学習者に数値が見える 1 件（IK-0591）。第 17 周で A の大半だった nginx の 120 秒超えは出ていない。
- **観察の割り引き**: 01513fa 以降もハーネスの教員向け投影は**入れ子の dict を JSON で印字する**ため、`section_id`・`node_ids`・
  `evidence_claim_ids`・`step_refs` 等の宛先キーが入れ子の中に残って見える（教材一覧の `document_id=` も同じ）。実画面はこれらを描かない。
  頭脳メモの「内部 ID が見える」は transcript の観察文で表示文か入れ子の宛先キーかを確かめ、表示文に残るものだけを製品側に数えた（下の NEW）。
- **GONE（第 18 波の主張で消えたもの）**:
  - IK-0583 — 表示中トピックの教材を問う短い質問に出典チップが付き、出典を開く往復が始まった（st-05 s-source-roundtrip seq=6〜7）。
  - IK-0585 — 降下路が解決できない要素で、理由と次の操作の事実文を返した（st-02 s-descend-and-nearby seq=19）。
  - IK-0589 — 主張 ⚓ の要素文脈は、ハーネスの行為が主張 ⚓ を受けないため再演では開けなかった。同じ要求を砂場 API の GET で表示中
    トピックつきに再実行し、焦点論文で解決すること・素の番号が論文内で重複するものだけ「同じ番号の主張が論文内に複数あるため特定できません」の
    事実文になることを見た。
  - IK-0550 — コース画面・議論の開幕・出典の往復が documents.id 一本で通り、source_path 形の値が画面に出なかった。
  - IK-0584 — 出典カードの英文が見出しと本文に二重に出なくなった（st-01 / st-02 の教材）。再演で見出し欄に `ev_0030` が見えたのは、ハーネスの
    教材投影が見出しの無いカードで id を描く写し漏れ（`uxsim/runner/state.py` の `item.get('title') or item.get('id')`）。
  - IK-0588 — 子部品へ 1 hop した先の上位に元の部品と分割の親が出て、4 hop すべてに戻り先があった（st-01 s-trace-one-hop seq=7〜10）。親が
    「移動できる」にならないのは、分割の親が行として残らない（LLM 原案のラベルのみ）設計どおりの縮退。
  - IK-0570 — 部品文脈の DTO を砂場 API の GET で取り、中心命題・支持構造の項目に id キーが無く、`exits` に「議論の開幕画面で中心命題と
    支持構造を読めます」が付くことを見た（ハーネスの投影は `exits` を描かない）。
  - IK-0577（部分）— 通常チャット・議論の回答が「式 (9) は AI が文脈から復元した式で、原文の数式とは照合できていません」と述べた。スライド
    位置（SA層 4-c）は未着手のまま。
  - IK-0586（部分）— 詰まりの 1 往復目は足場 1 つ・問い返しなし・前提ゲートなしで、ゲートは次の往復へ繰り延べられた（st-05 seq=15〜16）。
  - 近傍（いまここの周り）が回答で引用した論文 1 本に絞られ、その理由を事実文で書いた（st-01 / st-02 seq=21。§18.10 の焦点論文の層）。
  - 教員側: グラフ全体対話の返答で段名が日本語（理論の土台・観測量の構成・整合関係）になり、英語の stage 名・内部 ID が消えた（IK-0546 —
    3 場面）。ゼミ前ブリーフの脆い前提が「操作: 主張本文」になり「（本文を特定できない要素）」と英語の operation 名が消えた（IK-0563 の
    置き換え先が空、の件）。消去ノードの表示ラベルの生 TeX が落ちた。
  - 応答骨格の継続確認: 前提ゲートで質問を落とさない・確認の問いは 1 つ・鏡は核心語で前置きを復唱しない・直前の訂正を覆さない。
  各エントリの `resolution.verification` に `reproduction_rerun` を足し、IK-0584 / IK-0588 / IK-0570 を resolved にした（IK-0577 / IK-0586 は
  open のまま history に記録）。
- **STILL**:
  - 製品（早期 return の経路）: 前提ゲート表示中の「むずい」が前提トピックの長い説明（【ひとことで】【しくみ】）になり、「どこから？」が学習相談の
    固定構成（【歓迎と目標】…）になる（IK-0586 の残り）。
  - A層・PDF 経路の損失（第 17 周と同じ）: 英語の ⚓ 本文・部品の summary / narrative_role（IK-0571）/ 論文の題名が arXiv 番号（IK-0419 /
    IK-0420）/ 出典の所在が「冒頭」（IK-0544）/ 「この節の式」に N = 15（IK-0555 の除外に当たらない本文側の式）/ 断片で始まる主張・切れた図
    キャプション・行末ハイフン / 部品が comp_fallback だけの論文に開幕の骨格が無い（IK-0540）/ 主張参照に参考文献の断片が「比較の主張」として
    紐づく / 論文の順の章（「Estimates (90% CI)」が章・2.2 の親が 2.1・Results の頁範囲 — IK-0567 系。論文 2605.26810v1 のグラフは 9/27 のまま
    再解析していない）。
- **NEW（分類）**:

  | 症状 | 分類 | 根拠 |
  |---|---|---|
  | 前置き「はい、そうですね」付きの新しい質問がゲートへの返事扱いになり、前提トピックの説明が足され、表示中トピックの教材を「前提トピックの資料」と呼ぶ | 製品 | st-06 seq=8・st-02 seq=22（IK-0590） |
  | 求めに応じて説明の確かさを「9割以上」と数で出す | 製品 | st-06 seq=10。第 17 周の判定保留が別ペルソナで再現（IK-0591） |
  | グラフレビューの辺に confidence 0.5（全辺同値）・根拠文が英語の定型文で式 ID が一部残る | 製品 | 射影の後に `ComponentGraphResponse` へ詰め直し、`ComponentGraphEdge.confidence` の既定値が戻る（IK-0592） |
  | ゼミ前ブリーフに同じ文面の項目が並び、自己支持に見える支持線がある | 製品（仮説） | te-05 seq=3（IK-0593） |
  | ノード対話が「この要素は見つかりませんでした」だけで止まる | ハーネス | node_id 引数なしのとき、ハーネスが代表部品ではなく直近に覚えた id（ノードの component_id）を送った。実画面は主グラフの「理論の土台」を代表部品 comp_001 に解決し（砂場 API の GET で解決を確認）、404 / 422 は「グラフ全体との対話をご利用ください」の事実文に変える（`admin-graph-review.js`）。起票しない |
  | 回答末尾の「（言い直しは、この回答の『AIによる言い直し』の枠に示しています）」が画面に無い枠を指す | ハーネス | 鏡のブロックは実画面で「AIによる言い直し」の見出し付きで描かれる（`MIRROR_MOVED_NOTE`）。ハーネスの投影が見出しを描かない |
  | 教材本文の〔出典: ev_0030〕 | ハーネス | 見出しの無い出典カードで投影が id を描く（上の IK-0584 の項） |
  | 降下路に ⚓ 番号を渡すと not_found | ハーネス | 実画面は ⚓ をタップした要素の id で開く。番号から要素を引くのはハーネスの写し漏れ |
  | 出典番号が 1・3 と飛ぶ、1 件だけで「出典3」 | 仕様 | 番号は会話の中で固定（IK-0432 / IK-0466 / IK-0467） |
  | locked のトピックが開け、質問後も locked のまま | 仕様 + ハーネス | §18.9 と同じ（柔らかい印・トーストを投影しない） |
  | 旅に要る node_id が画面に無い | ハーネス | 痕跡が地図のノードになる前に旅の行為を選んだ。旅の空の縮退は再演で確認できず |
  | st-07 s-return-and-journey が blocked_precondition | ハーネス（種まき） | 種まきの s-discuss-one-paper が course.open を引数なしで選んで 404・議論の開幕が course_id 不在で開けず、書き置き・持ち越しが残らなかった（run 20261001T054111Z-71651）。音声ペルソナの経路は本周では走っていない |
  | 降下路が開けたときの段の中身が対象（Phase 1 の事前学習の主張）と噛み合わない | 判定保留 | st-01 seq=19 の 1 件。段の導出の対象一致は確かめていない |

- **ハーネス側**（uxsim/ のみ・起票しない）: 教員向け投影の入れ子 dict の宛先キー・教材一覧の `document_id=` / 出典カードで見出しが無いとき id を
  描く / `exits` と鏡の見出しを描かない / node_chat の node_id 欠落時に直近 id へフォールバックする / 降下路に ⚓ 番号を送る / 主張 ⚓ を部品文脈の
  行為に渡せない / s-return-and-journey の種まきがコース一覧・受講を経ずに開幕する。
- **残るオーナー判断候補**: 既存コーパスを TeX 経路で取り込み直すか（根 3 の砂場確認の前提・外部取得を伴う。IK-0540 / IK-0541 / IK-0547 /
  IK-0551 と論文の順の章の STILL はこれ待ち）/ 学習者向けの件数表示を LS8 と CR3 のどちらで読むか（IK-0549・継続）。IK-0550 は第 18 波で
  解消し候補から外れた。

### 18.12 第 19 波（第 18 周の是正・2026-10-01）

第 18 周（§18.11）で起票・継続した所見を是正した。いずれもガードレールで確認し、砂場での再演は次の周で行う。

- **97994b9（製品・学習チャット）**: 前提ゲート周りの早期 return を応答の骨格に通した。ゲート表示中の素の詰まりは「いいえ」と読まず RAG 経路で
  足場 1 つ（逆質問は保留）、「はい、…」＋中身は肯定だけを記帳して中身に答える、学習相談・前提説明は `_shape_advice_answer` で【歓迎と目標】の
  固定構成をやめ表示中トピックの構造から短い平文に、tutor・casual のプロンプトに確からしさを数で示さない規則（IK-0586 / IK-0590 / IK-0591）。
  グラフレビューの辺 confidence は応答モデルの既定値で戻らずキーごと外れる（IK-0592。辺の根拠文の英語の定型文と式 ID の残りは未着手）。
  残り: 「いいえ、<無関係な中身>」は明示の否定として前提の説明に進む（IK-0586 の残りとして記録）。
- **823eeda（製品・ゼミ前ブリーフ）**: 同じ文面の項目を式の名前で見分けるか 1 行に畳み、自分の主張を自分の支持線に数えず、学習者の問いの区画に
  k-匿名の素朴な問いの集約（名前とレンジのみ）を出す（IK-0593）。
- **daeffeb（ハーネス）**: 前提の種まきを固定コースに受講・open してから歩かせ、書き置きと持ち越しの問いを必ず残す（第 18 周 st-07 の
  blocked_precondition）。
- **356f395（ハーネス）**: 見出しの無い出典埋め込みは引用文だけを描き、内部 ID を見出しに使わない（app.js 側の IK-0584 の写し）。
- **d3ecaef（ハーネス）**: 画面投影の宛先キーを入れ子まで外す、部品文脈の出口と鏡の見出しを描く、ノード対話は実画面と同じ代表部品の解決と
  ラベル指定、降下路は ⚓ 番号を受ける（第 18 周のハーネス所見）。
- **継続**: IK-0577 は復元式の印が配信済みで、表示中のスライド位置（SA層 4-c）は未着手のまま open。

## 19. 増強: 検証シミュレーション層（SB層・「🧪 動かして確かめる」）の 8 場面（2026-10-03 起草）

SB層 v1.1「主語は学習者」（`docs/features/simulation_bridge_design.md` §14）を出荷した直後の UX 検証。主題は §18 と同じく
構造の見やすさ・関係を辿る理解・対話支援（P-0013）で、SB 固有の問い — **何が入力で何が出力か読めるか / 自分で直して走らせられるか /
採点されていないと分かるか / 配線の差が見えるか・AI の提案が参考表示と分かるか / 共有の意味が押す前に分かるか / 失敗から復帰できるか /
教員側に個人情報が出ず確定の導線が分かるか / 復元式の印と音声中心の学生での縮退が正直か** — を場面 (a)〜(h) に置く。
LLM 生成は 1 ペルソナ 1 回・改訂案は 1 経路だけ（従量部分の宣言。P-0011 / P-0018）。

### 19.1 行為（PE9 — 画面の部品 + 実ルート）

`uxsim/actions/registry.py` の `_SIMLAB` / `_ADMIN_SIM`（13 本）。学習側は全て `material.simulation-lab` を担体に、マニュアル
`student/02-student.md#simulation-lab*` の節へ対応づける: `learning.simlab.{open, select_module, generate, edit, adopt, wiring_save,
predict, run, share, propose_revision}`。教員側は `admin.simulation.{open, review_shared_run, ledger_candidate_confirm}`
（`materials.row-simulation` / `simulation.run` / `simulation.ledger-candidate`）。`select_module` と `predict` は画面上の選択・入力で
API は overview の再読（画面を読み直す操作）。`ledger_candidate_confirm` は run の観測系の主張の台帳（D層 GET）から run_id の一致する
候補を引いて確定する。実装は `uxsim/runner/simlab_exec.py`（行為 + 投影を 1 箇所に。`actions_exec` が末尾で取り込み、
`state.project_observation` が冒頭で委譲する）。module の指定はラベル・対象の部分一致（TeX とギリシャ文字を `norm_tex` で平らにする）か
一覧の番号で、内部の `module_key` を台本に書かない。

### 19.2 場面（誰 × 経路 × 見るもの）

| 場面 | 経路 | ペルソナ | 論文 / トピック | 見るもの |
|---|---|---|---|---|
| (a) | `s-sim-first-open` | st-01（newcomer） | Cepheus B / DCF 法 | まとまりの一覧から入力→出力・前提が読めるか。生成なし |
| (b) | `s-sim-edit-adopt-run` | st-03（adjacent_field） | 音速 / Horndeski `L_2` | 生成 → 1 行直す → 採用 → ベクトル入力で実行 |
| (c) | `s-sim-predict-compare` | st-01 | Cepheus B / `σ_ξ` | 予想を書いて並べる。採点・判定が付かないか |
| (d) | `s-sim-rewire-revise` | st-06（skeptic） | 音速 / `ξ_±` | 配線 2 種で実行し見比べる。改訂案は参考表示か |
| (e) | `s-sim-share-meaning` | st-04（non_native） | 遅延時間分布 / `L(D|Λ)` | 共有の意味を聞いてから共有。台帳の候補・確定は教員 |
| (f) | `s-sim-failure-recovery` | st-10（受信機） | Cepheus B / `M_J` | 入力欠けの失敗 → 事実文 → 揃えて実行 |
| (g) | `t-sim-review-shared` | te-03 / te-04 | (e) の論文 | 共有 run の表示名以外の個人情報・台帳候補の確定導線 |
| (h) | `s-sim-reconstructed-voice` | st-07（voice_first・未受講） | PINN / `L_TOV` | 復元式の印が選択時と生成時に出るか・音声で意味を聞く |

campaign: `uxsim/campaigns/astrophysics/c-simulation-lab.yaml`（学生 6）と `c-simulation-lab-teacher.yaml`（教員 2・学生の共有の後）。
params は `uxsim/domains/astrophysics/scenario_params/{s-sim-*,t-sim-review-shared}.yaml`（ラベルの部分一致・部品名で引き、UUID を書かない）。

### 19.3 審判

`uxsim/oracles/simulation.py`（決定論・LLM 0 回・製品の語彙は `principle.py` 経由で import）。A = DTO の形（記号写像のリスト形・段階の語彙・
失敗 run の事実文・4xx の detail の空／内部例外文／英語・生成応答の形）。B = SB1 閉世界（「検証された」に FACT_NOT_VERIFICATION が伴うか）・
SB14（予想の当否キーが無い・labels は 3 キー）・UC2 採点語彙・SB12（教員応答に owner_user_id / email / username が無い）・SB10（復元式の印）・
SB5（改訂案の部品が目録内）・言語・内部 ID・件数。E = 痕跡 `verification_attempt` の有無と payload（数値なし・語彙内）・LLM 予算の実数・
run の所有者。層は `findings._LAYER_BY_PREFIX` に `learning.simlab.` / `admin.simulation.` → `simulation_bridge_sb`。

### 19.4 運転

砂場は `-p episteme-uxsim`（frontend 3100 / DB 5433）。sim-runner が砂場にも立ち、学習者 overview の `sandbox_configured` が true であることを先に見る。
教材は TeX 経路 4 本（snapshot `corpus-astro-tex-v1`・コース `1bad7d9f`）。arXiv は呼ばない（取り込み済みの教材で足りる — P-0007）。
1 場面 = 1 invocation（`uxsim/runs/run21_lane.sh <campaign> <persona> <mailbox> <scenario…>`）、ペルソナ頭脳はレーンごと 1 体・製品頭脳 1 体
（proxy の mailbox は既定の `uxsim/runs/mailbox`。前の周の seq 以前には応えない）。教員 campaign は学生の共有の後に走らせる。
**砂場の api-server 像が v1（教員ルートのみ）だったので、学習者ルートを含む像に差し替えてから始めた**（compose build が registry の
metadata 取得で止まったため、同じ commit から作った開発スタックの像を retag して `up -d --no-build`）。

### 19.5 第 21 周（2026-10-03・c-simulation-lab / c-simulation-lab-teacher・8 場面 + 共有だけの経路 1）

**走らせ方**: 砂場は TeX 4 本（snapshot `corpus-astro-tex-v1`）・コース `1bad7d9f`。学生 6 レーン（st-01 が (a)(c) の 2 場面・他は 1 場面）を 2 組に分けて並列、
その後 st-04 の共有だけの経路、最後に教員 2 レーン。ペルソナ頭脳はレーンごと 1 体・製品頭脳 1 体（Opus）。製品 LLM の実数は
`llm_usage_events`（`learning:simulation_codegen` が学生 1 人 1 回・`learning:simulation_revision` が 1 回）で宣言どおり。従量課金はチャットの RAG 検索の
embedding だけ（本物の OpenAI）。arXiv は呼んでいない。

**場面ごとの結果**（詳細は `uxsim/runs/BRAIN_NOTES_round21_*.md` と各 run の `findings.jsonl`）:
- (a) st-01: まとまりを 2 つ選んでも入力・出力が「— → —」で、前提は英語の長い列。**諦めた**。
- (b) st-03: 生成 → L_3 の符号を 1 行直す → 採用 → 11 個の生 TeX 記号をキーに実行まで通った。採用が本人の選択で教員の承認ではないことは画面に無い。
  チャットは計量の符号を出典付きで説明したが「実行結果は手元に無い」と断った。
- (c) st-01: 予想 → 配線なし実行 → 「未測定 / 未測定 / 未測定」の意味を 2 回聞いたが、2 回とも使い方マニュアルの同じ節が返り**諦めた**。予想に判定・点数は付かなかった（OK）。
- (d) st-06: 配線「雑音だけ」「雑音と平均化」で 2 回実行。出力の桁（1e-10）に対して sigma が絶対値で 8 桁大きく、どちらも「乖離 / 埋もれる」で配線の差が言葉に出ない。
  改訂案は「AI の提案（未確認）」として桁合わせを提案（OK）。チャットに実行結果が渡っていない。
- (e) st-04（英語話者）: 共有の意味を英語で聞いたらマニュアルの目次（日本語）が返り、共有せずに終えた。(e') 共有だけの経路で共有 → 台帳の候補 1 件。
  共有の意味は押した**後**の文でしか読めなかった。
- (f) st-10: 入力を 1 つ欠いた実行は「入力『…』の値が必要です」の事実文で止まり、聞かずに直せた（OK）。出力の記号の警告の意味が読めない。
- (g) te-03 / te-04: 共有 run は開けた（表示名だけ・共有されていない run は見えない = OK）。**台帳の候補を確定する導線が画面に無い**。Copilot の操作 KB が v1 の文
  （「学習者にはこの画面も結果も表示されません」）のまま。未測定の run がそのまま台帳の候補になっていた。どちらも確定しなかった。
- (h) st-07（音声）: 復元式の印は選択時・生成時の両方に出た（OK）。声の答えは復元式を原文と同じ扱いにしなかった（OK）。まだ動かしていないのに結果を前提にした問い返し。

**ハーネス側で直したこと**: 模擬観測の列を `value` で読んで「—」にしていた（画面は `mean ± std`）／巨大数の書式／改訂案に op・slot を出していた／
教員側の列名「あなたの予想」／共有ボタンの確認文が API runner では見えない → 投影に添えた／E2（LLM 予算）が並列レーンの時間窓で偽陽性 → run のペルソナの
user_id に限定／B7 がピリオド無しの英語断片を拾わない → `language_fragment` 規則。**ペルソナが「模擬観測が — なのに…」と言った所見は投影の誤りで、製品の
欠陥ではない**（配線なしで `observed` が空なのは製品の事実で R7 として別に残す）。

**着手の地図（この波が前提とする利用状況: 学習者が個人で「作って試す」段階。複数学生の共有 run を教員が並べて読む共同利用はまだ無い）**

| 項目（根） | 目的への寄与 | 利用状況での必要性 | 放置時に起きること | 負担 | 前提関係 | まだ分かっていないこと |
|---|---|---|---|---|---|---|
| R1 まとまりの入力→出力が空（成員の式を出す） | 「何を入力に何を出すか」を読む入口 | 初見の学生が最初に見る欄。全員が詰まった | 一覧から選べず諦める（(a) の再現） | core 2 箇所 + UI 1 箇所 | R2 と同じ DTO | 成員の式を何本まで見せると読めるか |
| R2 式の表示ラベルが英語要約 | 一覧の可読性 | 全場面 | 英語の断片で埋まる | 1 関数（教員側の論文層も同時に変わる） | — | latex 断片が長い式でどう見えるか |
| R5/R6/R7 比較の表示（軸名・ゼロの記号・配線なしの観測） | 結果の読み取り | 実行した全員 | 「未測定が 3 つ」「ゼロなのに…」で混乱 | comparator / service | — | 記載値を入れた場面は未検証 |
| R8/R9 生成の facts の内部語・重なりの文 | 生成コードの読み取り | 生成した全員 | role=output を学生が読む | codegen 1 箇所 | — | LLM が指示を守る割合（頭脳では守れた） |
| R12 チャットが実行結果を見ない | 対話支援の根幹 | 結果の意味を聞いた全員 | AI が「結果は手元に無い」と断る・マニュアル節が返る | SA層 解決器 1 本 + UI の文脈送信 + マニュアル 1 節 | SA層 4-c と同じ合流点 | 前提の逆質問との同居 |
| R13 採用の意味 | SB11 が画面の文で伝わるか | 採用した全員 | 承認と誤読 | 事実文 1 つ | — | — |
| R16 観測系の主張の候補が読めない | 共有の対象を選ぶ | 共有する学生 | 「本文を特定できない要素」から選ぶ | service 1 関数 | — | 候補ゼロの論文で共有の意味がどう変わるか |
| R17 教員の確定導線が無い | SB13 の「確定は教員」の実体 | 共有が起きた後 | 教員は API でしか確定できない | UI + マニュアル + アンカー | R16・未測定の共有 | 見送りの理由の置き場 |
| 未測定の共有を台帳候補にしない | 台帳の雑音 | 配線なしで共有した学生 | 何も確かめていない候補が台帳に並ぶ | share 1 箇所 | SB13 の読み替え（設計書に記録） | 教員が「未測定でも残したい」場合 |
| R10/R11/R15/R18 UI の読み取り（記号の描画・共有の説明・部品の単位・同名のまとまり） | 操作の迷いを減らす | 全場面 | 生 TeX を打つ・押してから意味を知る | UI | 第 1 波の DTO | — |
| Copilot KB / マニュアルの v1 文 | 教員の道案内 | 教員 | 画面と逆の説明 | docs 2 箇所 | — | — |
| R3 前提の英語（A層） | — | 全場面 | 英語の長い列 | 表示側では直せない | generation_language の対象化は別件 | 式語彙の enum 化との順序 |

オーナーに問う 3 種（不変条項の解釈・権利/制度・後戻りしにくい構造）に当たるものは**無い**。「未測定の共有を台帳候補にしない」は SB13 の読み替え
だが候補の弁（確定は教員）を狭めるだけで、設計書 §14 に記録して進める。

**第 1 波・第 2 波の後の再演（同日・(a) st-01 / (f) st-10 / (e) st-04 / (g) te-03）**: `uxsim/runs/run21r_lane.sh`（`--replay` で前回の run を
指定。画面が変わった地点で cache miss → live に切り替わり `replay_divergence` が立つ）。(f) は入力欠けの事実文・採用の意味・軸名付きの段階
ラベル・観測の列が揃い goal_reached。(e) は共有の意味を押す前に読んで共有し、未測定なので台帳の候補にならない事実文を読んだ（チャットの
英語の問いには目次が返る = STILL）。(a) は STILL（上記）。(g) は 1 回目の再演で台帳候補の区画がハーネスの投影に無く（画面にはある）、
`admin.simulation.review_shared_run` が主張の台帳も読むように直して 2 回目を走らせた。ハーネス側の修正: 再演で見つけた投影の不足 2 件
（台帳候補・改訂案の理由だけ）と E2 の教員だけの run での誤判定。

### 19.6 第 22 周（2026-10-04・c-simulation-lab-r22 / c-simulation-lab-teacher-r22・場面 (a')(d')(e'')(g')(i)(j)(k)(l) + 第 21 周 8 場面の再演）

**前提**: 砂場は第 21 周のまま（TeX 4 本・コース 1bad7d9f）。第 22 波の第 1 波を `docker cp`（core / routes / schemas / admin_operations）+
`docker restart api-server` で差し替え（解析 run なしを確認）。`reset_learner_state --exclude-persona st-06-skeptic-bao --yes`（SB層の
`simulation_runs` / `instrument_wirings` / `simulation_programs` と台帳候補の要素を対象に拡張。st-06 は場面 (l) のため第 21 周の run 2 本を残す）
→ `pin_course --yes` → snapshot `corpus-astro-tex-r22`。te-03 / te-04 は教材の編集権限が無く台帳候補の確定が 404 になるため、所有者
（bootstrap 教員）の共有 API で 4 教材を editor 共有（`uxsim/sandbox/grant_document_editors.py`）。従量の基線: proxy chat 1005 /
embeddings 497、製品 mailbox 最終 seq 435（第 22 周の製品頭脳は seq ≥ 436 にだけ応える）。

**場面と割り当て**（params は `scenario_params/`・UUID と module_key は台本に書かない）:

| 場面 | 経路 | ペルソナ | 論文 / トピック / まとまり | LLM 生成 |
|---|---|---|---|---|
| (a') | `s-sim-first-open-io` | st-01 | Cepheus B / DCF 法 / 番号 1〜3 → σ_ξ。受け取る記号 → 定める記号を読んで生成前に入出力を言えるか | なし |
| (l) | `s-sim-stale-runs` | st-06（reset しない） | 音速 / 観測量 / ξ_±。第 21 周の自分の run を日時・状態で見分けられるか | なし |
| (d') | `s-sim-rewire-scaled` | st-06 | 音速 / ξ_±・配線 3 本（既定 sigma → 桁合わせ → +平均化）。差が段階の言葉に出るか | 生成 1・改訂案 1 |
| (e'') | `s-sim-share-measured` | st-04（英語話者） | Cepheus B / σ_ξ・雑音+平均化・観測系の主張 2 つ（偏光角分散 / 13CO 速度分散）→ 共有 → 候補 2 件 | 生成 1 |
| (j) | `s-sim-english-meaning` | st-04（(e'') の後） | 最新の run を開いたまま英語で「未測定の意味」「共有の帰結」を聞く | なし |
| (i) | `s-sim-reference-value` | st-10 | Cepheus B / 内側 eq:mod_dcf・記載値 B_0 = 153 μG（ACF 解析。181 μG は修正 DCF の値で別の式） | 生成 1 |
| (k) | `s-sim-voice-result` | st-07（音声） | Cepheus B / 主結果 / M_J（T=15, n=1e4）。音声経路（casual）で結果の意味を聞く | 生成 1 |
| (g') | `t-sim-decide-candidates` | te-03 → te-04 | Cepheus B。te-03 が 1 件確定・1 件見送り（理由付き）、te-04 は判断済みの状態と Copilot の答えを読む | なし |

第 21 周の (e) は遅延時間分布の論文（d82c5f0a）だったが、読める観測系の主張が 1 件しかなく候補が 2 件立たないため (e'') は Cepheus B に変えた。
(k) は第 21 周 (h) の PINN の L_TOV が損失のまとまりで A3 の適性判定の対象外になるため M_J に変えた。

**行為・審判の増強**: 行為 `admin.simulation.ledger_candidate_dismiss`（理由が空なら HTTP を出さない = 画面の無効化と同じ）で 14 本。
`learning.chat.ask` に `simulation_run_id`（`latest` = 最新の自分の run）→ `screen_context`（app.js と同じ形）。審判 B10（選んだまとまりに
受け取る記号・定める記号が 1 つも無い。`simulable: false` は対象外）/ B11（教員向けの配線なし run に記号別の相対差が数で載る）/
E4（見送った候補が `status=dismissed` と理由付きで残る）/ E5（確定した主張が `directly_verified` に上がっていない）。投影は画面と同じ欄・同じ形
（「受け取る記号: … → 定める記号: …」・副題 1 行・`simulability_note` 1 行・run の日時と `ledger_decision.fact`・教員の候補は主張のラベル・
判断済みの候補と見送りの理由）。第 2 波の UI はこの形に合わせる（第 21 周の偽所見 4 件の再発防止）。

**結果（2026-10-04・学生 7 レーン + 教員 2 レーン + 再演 5 本。詳細は `uxsim/runs/BRAIN_NOTES_round22*_*.md` と各 run の `findings.jsonl`）**:

| 場面 | 到達 | GONE（第 21 周から消えた） | STILL / NEW（残った・新しく見えた） |
|---|---|---|---|
| (a') st-01 | goal_reached（ただしチャット経由） | 「— → —」は消え「受け取る記号 → 定める記号」が全行に出る | 記号が生 TeX / 5 件で定める記号が空（関係式）/ eq:mod_dcf が「B_0 を受け取り何も定めない」と読める（式は B_0 を求める関係）/ 単位 `\mu\mathrm{G}` が記号に混じる / Stokes Q と補正係数 Q が同名 / 外枠 1 番が DCF・ビリアル・偏光を束ね前提 50 件超が英文 1 段落 / 前提が英語（R3） |
| (l) st-06（reset なし） | gave_up | — | 開いた画面に実行の一覧が無かったのは**投影の切り詰め**（6,000 字・一覧の下にあった → 投影を一覧の上へ）/ 前の実行を問う発話が HELP に回り「何ができるか」が返る（→ 結果の語の拡張で是正） |
| (d') st-06 | 部分到達 | 桁合わせ（sigma ≈ 出力の 1 割）で「一致 / 分離できる」に変わり差が言葉に出た / 軸名付きの段階 / チャットが最新 run の数値で答える | **平均化 n=16 でばらつきが縮まない**（部品が何もしていなかった → 実装）/ 比のラベル「差と…の比」が中身と不一致（→ 改名）/ 改訂案なしに理由が無い（→ 理由の事実文）/ 作り直した版と前版の対応が画面に無い |
| (e'') st-04 | 1 回目は候補 0（主張名の不一致を黙って落とした = **ハーネス**）→ やり直し（`s-sim-share-measured-only`）で候補 2 件 | 採用の意味 / 軸名 / 共有の意味を押す前に読める / 配線ありでゼロ誤記なし | 配線なしの run で「理論の出力がゼロの記号」が全記号に出る（→ comparator 是正）/ 学習者画面に生の数値（§14.1 で許容・所見として残す）/ 選べる主張が観測量の定義文で結果の主張ではない |
| (j) st-04 | 到達 | 英語の「unmeasured の意味」「共有の帰結」とも**自分の実行の事実**に基づく英語の答え（目次・§19 冒頭の素通しは消えた） | 共有後に何が起きるかは「記録に無い」と答えた → 解決器に共有の意味と教員の判断の状態を足した（本周の差し替え後は未再演） |
| (i) st-10 | 到達せず（confused） | 「記載値との一致: 乖離」の軸が初めて測れた / 採用の意味 / チャットが実行結果を受け取る | ゼロ判定の誤り（上と同根）/ **記載値の単位が画面にも AI にも渡らず AI が換算の誤りを疑わせた**（→ `FACT_REFERENCE_INPUT` と解決器の行）/ 相対差の生数値 / mu・m_H の入力欄と単位系が読めない |
| (k) st-07（音声） | gave_up | 内部語なし / 採用の意味 / 復元式の扱いを声でも断定しない | casual 経路に実行の記録が渡らず「手元に無い」と正直に縮退（設計どおり・IK-0638 に判断を記録）/ ゼロ判定の誤り / AI の換算の誤り（太陽 5 個 ≠ 7 個） |
| (g') te-03 → te-04 | te-03 が 1 件見送り（理由付き）、te-04 が残り 1 件を見送り → **確定は 0 件**（確定の経路は未観察） | 確定・見送りの導線 / 候補は主張の本文 / 見送り理由が逐語で残り「判断済みの候補」で読める / Copilot が候補の条件・確定の帰結・学習者からの見え方・権限に答える | 候補の説明が主張ごとに同文（主張↔記号の対応が無い）/ 教員側の実行一覧に日時・候補の有無が無い / 判断済みの日時は DTO にあるが**投影が描いていなかった** / Copilot が同じ KB 本文を繰り返し「確定・日時」で atlas 凍結へ誤ルーティング / 共有者の表示名は砂場では username と同じ |

**第 21 周の再演（`--replay`）**: (a) st-01 = gave_up のまま（理由は「— → —」から「主題の量とずれる・定める記号が空」へ）/ (c) st-01 = 第 21 周の
諦めた地点より先へ進み、未測定の意味と負値の理由を読めた（マニュアル節の目次返し GONE・入出力 GONE・role= GONE・表と文の食い違い GONE）/
(b) st-03 = goal_reached。role= と生 TeX キーが消え cannot_compute が記号を名指し。ただし Horndeski `L_2` は `return {}`（計算不能）で「1 行直す」題材として
成り立たず、採用の意味は押す前には無い / (e') st-04・(g) te-03 = **再演不能**（周の前の reset で st-04 の第 21 周の共有 run が消え `run_id` 不在。
製品の欠陥ではなく運転の前提。(e'')(g') が同じ主題を代替）/ (d)(f)(h) は同じペルソナが本周で生成を使ったため再演せず（予算 1 ペルソナ 1 回）。

**審判**: B10（記号の無いまとまりの選択）は (a') で発火せず（記号は全モジュールに出た）。B11（配線なし run の数値）は発火せず。E4（見送り理由の保存）は
te-03 / te-04 の見送りで DB に `dismiss_reason` と `dismissed_at` を確認。E5（確定で `directly_verified` に上がらない）は確定が 0 件で**未観察**。
D（マニュアル ⇄ 画面）: `simulation-lab-levels` の「それぞれに『入力の式 → 出力の式』が出ます」は第 2 波で「受け取る記号 → 定める記号」の記述に直した。

**周の途中で直したもの（第 1 波の追補・設計書 §12.5）**: ゼロ判定 / 記載値の単位 / 平均化の実装 / 比のラベル / 改訂案の理由 / 開いている実行の問いの
RAG 化 / 定める記号が無いまとまりの文言。**ハーネス側**: 観測系の主張の一覧と選んだ主張の投影・一致しない hint の明示・実行履歴を一覧の上へ・
`io_contract.parameters`・`can_decide_ledger`・判断済みの日時・空状態の文言を画面と同文に（IK-0634 / IK-0628 の型）。

**従量と肩代わり**: 製品 LLM の肩代わり（Claude Code の頭脳 2 体）= proxy chat 35 回（seq 436〜470: 学習チャット 20・codegen 6・改訂案 1・意図分類と
Copilot 等 8）。本物の従量 = embedding 14 回（RAG 検索 11 + help_kb 3）。arXiv 0 回。llm_usage_events の feature 別: `learning:simulation_codegen` 6
（学生 6 名 × 1）・`learning:simulation_revision` 1 = 宣言どおり。

**残った課題・確認できなかったもの**: 確定の経路（E5）/ (j)(l) の差し替え後の再演（共有の意味・教員の判断の状態が grounding に載るか）/ 配線なし run の
ゼロ判定の是正の砂場再演 / 平均化の実装の砂場再演 / 関係式のまとまりの入出力の向き（A層の defined_symbols が空 — IK-0636）/ 前提の英語（R3・A層）/
単位の記法と同名記号（A層）/ Copilot の KB 素通し・誤ルーティング / 候補の説明が主張ごとに同文 / 共有者の表示名（砂場は username = display_name）/
browser runner は未使用（API runner のみ）。


## 20. 増強: vision 由来の 5 機能（F1 帰還の扉 / F2 撤回・訂正主権 / F3 精読・理解サイクル / F4 わたしの記録と開示 / F5 リリース前の確認と確定文脈）の周（2026-10-09 起草）

`docs/vision.md` の 5 つの約束が、画面の上で約束どおりに読めるかを通しで確かめる周。対応する設計書は
[帰還の扉](../features/return_door_design.md)・[理解サイクル](../features/understanding_cycle_design.md)・
[わたしの記録](../features/trace_registry_sovereignty_ledger_design.md)・[可視性6軸](../features/disclosure_axes_design.md)・
[リリース前の確認](../features/release_review_flow_design.md)・[確定文脈](../features/decision_context_design.md)。

### 20.1 主題と合格基準

| 機能 | vision の約束 | 見るもの |
|---|---|---|
| F1 帰還の扉 | §3.4・UC4（間を埋めない） | 書き置き・持ち越しが本人の言葉のまま扉と開幕に出るか / 何も残さなかった翌日に督促・日数が出ないか |
| F2 撤回・訂正主権 | §3.7・§5.2・P4 | 書き直し・🗑・「地図には反映しない」・候補の見送りの後、何が消えて何が状態を変えて残ったかが本人に読めるか |
| F3 精読・理解サイクル | §3.1・UC1/UC2 | 精読モードの入口・予想 → 並置に採点が付かないか・予想を途中でやめた翌日に続きを辿れるか |
| F4 わたしの記録と開示 | §5.2・§5.4 | 自分の記録の一望と持ち出し・入力欄の下の開示の 1 行とチャットの答えが一致するか |
| F5 リリース前の確認と確定文脈 | §5.1・改訂原則 1（DC1〜DC4） | 「次へ」の意味が押す前に読めるか / 確定の記帳から「何を見て何を選べたか」が再構成できるか |

合格基準は 3 つ: ①原則違反が無い（審判 A〜E）②**その画面で利用者が確認したいことが 1 操作以内で確認できる**
（各経路の `watch_for`。机上レビューの判定語は「確認できる / 遠い / 出ない」）③見た目（階層・空状態・重なり・
内部語・数）。③は API runner では読めないので、指揮者が内蔵ブラウザで砂場（幅 1280）を見た所見を視覚ベースラインとして
別に持つ（`uxsim/runs/round23/visual_baseline.md`）。周の前に UI を読む机上レビュー（`uxsim/runs/round23/desk_review.md`。
コード・ブラウザは触らず `file:line` を根拠に）を置いた。

### 20.2 行為（PE9）

`uxsim/actions/registry.py` に `learning.chat.delete_message`（🗑）/ `learning.traces.{list, map_exclude, map_restore}` /
`learning.records.export` / `learning.disclosure.view` / `learning.cycle.landing_candidates` / `learning.reconstruction.revise` /
`admin.courses.list` を足し、`learning.chat.rewrite`・`learning.cycle.intention`・`learning.{tension,anchors}.*` に番号・
後ろからの番号で対象を指す引数を足した（内部 ID を台本に書かない）。**`learning.cycle.precision_toggle` は
`unsupported=True`**: 精読モードは画面だけの切り替え（状態はブラウザの localStorage）で API が無く、API runner では
押せない。予想の枠は `learning.chat.cycle`（elicit → diff）で近似し、トグル自体の見え方は視覚ベースラインで見る。
行為の実装は `uxsim/runner/vision5_exec.py`。

### 20.3 経路

学生（`uxsim/scenarios/goal/student/s-v5-*.yaml`・params は `domains/astrophysics/scenario_params/`）:
F1 = `s-v5-leave-nothing` → `s-v5-empty-door`（何も残さず終える → 翌日の扉）/ `s-v5-leave-and-carry` → `s-v5-return-door`
（書き置き・持ち越しを残す → 翌日答える）/ `s-v5-revisit-rewrite`。F2 = `s-v5-rewrite-delete` / `s-v5-map-exclude-restore` /
`s-v5-recon-revise` / `s-v5-anchor-dismiss`。F3 = `s-v5-precision-elicit` / `s-v5-predict-pause` → `s-v5-predict-resume` /
`s-v5-predict-no-grading`。F4 = `s-v5-records-export` / `s-v5-ask-disclosure` / `s-v5-records-after-retraction`。
F5 の学習者側 = `s-v5-landscape-check`（教員が確認を飛ばして公開する前と後に 1 回ずつ）。
教員（`scenarios/goal/teacher/t-v5-*.yaml`）: `t-v5-register-own-course`（種まき — 砂場の教材から自分のコースを登録まで。
公開しない）/ `t-v5-release-skip-step2`（ステップ 2 を「あとで」→ 公開）/ `t-v5-release-click-through`（読まずに「次へ」→ 公開）/
`t-v5-release-meticulous`（根拠を読み、却下・再検討 → 次へ → 公開）。
campaign は `c-vision5-students.yaml`（st-02 / st-05 / st-06）と `c-vision5-teachers.yaml`（te-02 / te-03）で、レーンの順と
依存（(1b) は reset 直後・(5c) の前後に学生の landscape-check を挟む・te-03 の確定は学生の前後比較の後）は campaign の notes が正本。
session 2 は同じ起動の中での再ログイン（「翌日」の近似。砂場は同日なので日付の境界は検証していない）。

### 20.4 審判

- **E6 decision**（`uxsim/oracles/decision.py`）: run の窓に教員ペルソナが記帳した `theory_review_events`
  （`accept_on_release` と公開）の `metadata.decision_context` を読み、DC1（記帳がある）・`presented` / `applied` の形と
  `truncated` の整合・`presented_matches_applied` が集合比較と一致（DC2）・代替が空でない（DC3）・再審の経路・
  来歴申告の「根拠を開いた行」が申告した提示集合の部分集合（DC4）を検査する。DB が無ければ transcript の応答で同じ規則。
- **E7 differential**（`uxsim/oracles/differential.py`）: 同じコースの非 LLM 応答（コース・トピック教材・開幕・再構成の出題・
  位置づけ）の構造を学習者間で比べ、差を沈黙適応（UC5）の**仮説**として出す。値ではなく欄の道と値の指紋だけを比べる
  （`runner/digest.py`）。1 場面ずつ運転した run は `python -m uxsim.oracles.differential <run...>` で束ねて読む。
- **E8 retraction**（`uxsim/oracles/retraction.py`）: 🗑・書き直し・「地図には反映しない／戻す」・候補の見送りの後、
  派生痕跡が消えずに状態（`superseded` / `map_excluded` / `dismissed`）で残るかを DB か「わたしの記録」で確かめる。
- **B 督促語彙**（`uxsim/oracles/principle.py`）: 扉・今日の言葉・持ち越し・わたしの記録の応答に、督促・連続日数・件数の語が
  出ないか。語彙は製品のガードレール（`test_learner_streak_guardrails.py` / `test_return_door_ui_static.py`）の定数を
  そのまま読む（PE6）。本人の逐語が入る欄は対象外。

### 20.5 運転

- 砂場は `-p episteme-uxsim`。周の前に `reset_learner_state` で **st-02・st-05 の痕跡を空にし**（(1b) は扉が空であることが
  前提）、st-06 は第 21・22 周の run を残した → `pin_course --yes`（コース 1bad7d9f）→ snapshot **`corpus-astro-tex-r23`**
  （`uxsim/snapshots/corpus-astro-tex-r23/manifest.yaml`）。arXiv は呼ばない。
- 1 invocation = 1 ペルソナ × 複数場面（`uxsim/runs/run23_lane.sh`）。ペルソナ頭脳はレーンごと 1 体・製品頭脳 1 体。
- 第 1 波（core / routes）は第 22 周と同じく `docker cp` + api-server の再起動で砂場へ入れた（フロントの反映は視覚ベースラインの「第 1 波の反映後」で確認）。
- **te-02 は教材の閲覧権限だけ**（editor 共有は te-03・te-04）。権限は足さず、te-02 の「次へ」は「閲覧のみの教員が押したとき
  画面と記帳が何を言うか」を見る場面に使った。te-02 / te-03 はコースを所有していないので種まき経路で自分のコースを作った。
- **頭脳の規律（本周で足したもの）**: 頭脳どうしが共有の一時スクリプト名で応答を書いて**別の頭脳の応答を上書きする事故**が
  起きた。一時ファイル・スクリプトにはレーン固有の名前（ペルソナ id と mailbox 名）を付け、共有の固定名を使わない。
- **頭脳が一斉に落ちたときの立て直し**: 再演の途中で頭脳が利用上限で一斉に止まり、runner は mailbox の応答待ちの上限
  （`MailboxTimeout`）で場面ごと終わった（`uxsim/runs/run23_*_0*.log`）。その場面は結果に数えず、落ちた場面から
  レーンを起こし直す（`r2lane_*`）。落ちる前に書かれた所見（`BRAIN_NOTES_round23r_*.md`）はそのまま使う。

### 20.6 第 23 周の結果（2026-10-09〜10）

詳細は `uxsim/runs/round23/BRAIN_NOTES_round23*_*.md`・`product_findings_live.md`・各 run の `findings.jsonl`。
課題は IK-0649〜IK-0689 に記帳した。表の GONE / STILL / NEW は再演（第 1〜3 波の反映後）で見えたもの。

| 機能 | ペルソナ × 経路 | 到達 | GONE（是正後に消えた） | STILL / NEW |
|---|---|---|---|---|
| F1 | st-05 leave-nothing → empty-door | 到達 | — | 空の扉は出ず督促・日数も無い（OK・RD3）。NEW: 持ち越しの候補ゼロでも見出しが「どの問いにしますか？」（IK-0669）/ 切り上げの挨拶が「問い［未解決］」として記録・軌跡に入る（IK-0667）/ 翌日のコース画面に前日の議論の事実が出ない |
| F1 | st-05 leave-and-carry → return-door | 書き置きは到達。持ち越しは 1 回目は候補ゼロで書けず（ハーネス — §20.7） | — | 書き置きは扉の最上部に逐語で出る（OK）。NEW: 議論の AI が「持ち越しの問い、受け取りました」と言うが保存されていない（IK-0670）/ 予想（elicit）は扉にも開幕にも出ず会話の履歴でだけ見つかる / 同じ問いの言い換えで出典が揺れる（IK-0673） |
| F1 | st-02 revisit-rewrite | 到達 | — | 扉・開幕の持ち越しが本人の文ではなく印の表示文（是正前に保存された行。是正後の保存は再演で未観察）/ 持ち越しに答えても扉は同じ（IK-0668） |
| F2 | st-06 rewrite-delete | 到達 | 🗑 の直後の事実文（第 1 波）/ 問いの軌跡の〔誤答〕→「訂正あり」（第 3 波）/ 目次の locked →「まだ順番が来ていません（開けます）」（第 3 波） | わたしの記録で取り消し・書き直しが状態名で読める（OK）。NEW: ✏️ の後に一言が無い（IK-0682）/ 書き直した往復の誤解候補が生き残る（IK-0687 → 第 3 波で supersede。砂場の再演は頭脳の停止で未観察）/ 同じ論文が arXiv 番号と題名の 2 つで出典に並ぶ（IK-0664） |
| F2 | st-02 map-exclude-restore | blocked | — | わたしの地図が空で外す対象に届かず、空の事実文も無い |
| F2 | st-06 anchor-dismiss | 到達 | — | 外した直後の事実文は OK。外した候補が記録・軌跡・持ち出しに出ない / 見送った候補を戻す操作が無い（IK-0660） |
| F2 | st-05 recon-revise | blocked | — | 主結果のトピックに出題可能な問いが無く自己確認・再挑戦を観察できず（IK-0661 は机上レビューの所見） |
| F3 | st-02 precision-elicit | 到達（トグルは unsupported） | 開幕の JSON・内部語（第 3 波）/ トグルが見出し行へ・ON で開幕に予想の欄（第 1〜2 波・視覚） | 予想 → 並置は正誤語なし・出典付き（OK）。STILL: 予想より先にトピック教材が主結果を見せる（§20.9）/ 数式の記号欠落・生 TeX（IK-0665 / IK-0685） |
| F3 | st-05 predict-pause → predict-resume | 到達 | — | NEW: 予想の応答が問い 1 つだけで「残った」事実が無い / 翌日の扉・開幕から前日の予想に辿れない。並置は OK |
| F3 | st-06 predict-no-grading | 到達 | — | 正誤・点数・一致度の語なし（OK）。NEW: 問いを返すだけの elicit 往復に出典警告（IK-0671）/ 出典本文の途中切れ |
| F4 | st-06 records-export / records-after-retraction | 到達 | 空の系統の羅列・「設計中」の文・公表文の重複（第 1〜2 波・視覚） | NEW: 持ち出しファイルが内部名のまま（IK-0683）/ 外した帰属が記録に現れない |
| F4 | st-05 ask-disclosure | 到達 | 開示の 1 行を「わたしの記録」の先頭にも（第 2 波） | 入力欄の下の 1 行と使い方の答えが一致（OK）。NEW: 「どこに残る・いつ消せるか」が 1 行に無い（§20.9）/ 使い方の答えに生リンクと「[出典1]」（IK-0672） |
| F5 | te-02 skip-step2 | 到達 | 公開後の英語の 1 行（再演で「公開しました。」+ 要約） | STILL: 公開後も行の入口が「確認して公開」（IK-0678）。NEW: 範囲外の警告がコースビルダーの回答の中ほど（IK-0680）/ 登録結果が英語の項目名の羅列 |
| F5 | te-02 click-through（閲覧のみ） | 到達 | 「一致しています」+ 監査 0 行（IK-0688 → 第 3 波）。ステップ表示・状態チップ・「次へ」の意味をボタン直上へ（第 2 波） | 再演では te-02 のコースの唯一の論文が te-03 の確認で確認済みになっており未確認 0 件 → 「次へ」は POST せずステップ 3 へ → **skipped の経路は砂場の状態で再演不能**（サーバ側は `test_release_review.py` の閲覧のみケースで固定）。NEW: 確認する配置が 0 件のときの事実文が無い / ステップ 3 の「位置づけ N 件を受講者に表示します」が却下の行を含む / 閲覧権限だけの論文があることがステップ 2 で押す前に出ない（IK-0674） |
| F5 | te-03 meticulous（editor あり） | 到達（再演は種まきの途中で頭脳停止） | ステップ 3 の要約（「ステップ 2 で確認として記録したもの」・第 2 波）/ コースビルダーのセッション名の切り詰め（IK-0684・再演で確認） | NEW: 根拠に原文の逐語と解析の生成文が区別なく並ぶ（IK-0677）/ 却下の理由欄・その場の取り消しが無い（IK-0676）/ 提示集合がその場で判断した行を含まない（IK-0679）/ 無関係な分野の配置不能の文（IK-0675） |
| F5 | st-05 landscape-check（前・後） | 到達 | — | 各配置に出所ラベルが付き、誰が確かめたかが 1 操作で読めた（OK）。前後の差分審判（E7）を束ねた結果は本節に記録していない |

審判: E6 は te-03 の 1 回目で「根拠を開いた行が申告した提示集合の外」を出した — ハーネスの行番号ずれ（§20.7）の副産物だが、
提示集合がその場で判断した行を含まない（IK-0679）こととも重なる。E8 は取り消しのある run（rewrite-delete・anchor-dismiss・
revisit-rewrite）で発火しなかった。B の督促語彙は扉・今日の言葉・持ち越し・記録の面で発火しなかった（B で出たのは開幕の生 TeX・英語の生成文と、elicit の応答の制御文字 — 後者は製品頭脳のエスケープ事故で製品の欠陥に数えない）。

### 20.7 ハーネス側の偽所見と直し（`uxsim/runs/round23/harness_fixes.md`）

1. `learning.cycle.intention`: 頭脳が候補の `pick` と本文の `text` を両方渡したとき runner が `pick` を優先し、本人の言葉が
   保存されなかった（st-02 の「扉に印の文」の一部はこれ）。画面は「候補を選ぶ」か「自分で書く」かの二択なので、`text` が
   空でなければ `text` を優先し、両方あるときは runner_note に残す。
2. 投影が生の JSON（`ok: true` / `visibility: public` / `has_motive: true` / ISO 日時 / `in_progress|locked|future`）を出していた
   箇所を、画面の文（app.js / discuss.js / admin.js）に合わせた。頭脳が「投影かもしれない」と注記した所見は画面を確かめてから
   製品の欠陥に数える（IK-0634 の型）。
3. `admin.release_review.row_status`: 投影の行番号が「却下済みを除いた並び」で振られ、却下の後に番号がずれて別の行を再検討に
   した。画面は却下した行を残してボタンだけ消すので、投影も行を残し番号を固定した。
4. `learning.cycle.intention`（持ち越し・候補ゼロ）: 画面では候補ゼロでも「自分の言葉で書く」を押せるのに、runner が候補を
   前提にして blocked にしていた。`text` が空でなければ候補なしで送る。

### 20.8 是正の波（第 1〜3 波・同日）

- **第 1 波（core / route と機能ごとの JS を機能単位で並列）**: 持ち越しは候補の本文を保存（RD1）・保存の失敗を事実文で返す・
  欄外の印の読み元を登録簿の `margin_marks` 宣言へ（F1）/ 往復の取り消し理由 `superseded_reason`・🗑 の事実文・可視性の
  宣言の撤回欄を現物へ（F2）/ 精読 ON の開幕・押した直後の事実文（F3）/ 空の系統の畳み・系統名の画面語（F4）/ リリース前の
  確認の取り直し・状態名の正本の一本化（F5）。
- **第 2 波（UI・見た目を 1 体に集約）**: トップバーの折り返し・受講の事実文の位置・精読トグルを教材区画の見出し行へ・
  わたしの記録の行の 3 段と公表文の 1 回化・開示の 1 行の再掲・リリース前の確認のステップ部品と左罫・「次へ」の意味の位置・
  ステップ 3 の記録の要約。教材区画の帯は重なりではなく「境界の無いスクロール領域」だったので区切り線を付けた（IK-0662 の
  原因の訂正）。
- **第 3 波（再演前の残り）**: 閲覧のみの教員の「次へ」（提示集合を閲覧できる全論文の未確認に・`skipped`・0 件でも監査 1 行・
  `decision_note`）/ 書き直し・取り消しをした往復の誤解候補の supersede / 問いの軌跡の種別名（「誤答」→「訂正あり」）/
  目次の状態の表示語と本人が会話したトピックの「学習中」/ 開幕 DTO から内部語を落とす。
  各設計書の追補（2026-10-09）が正本。

### 20.9 オーナー判断の候補（推奨付きで裁定待ち）

| 候補 | 推奨 | 当たる 3 種 |
|---|---|---|
| 学習者が取り消した質問を、教員の「教材から答えられなかった質問」一覧から外すか（IK-0658） | 外さない（一覧は教材の欠けを示す教員の計器で、学習者の発言の記録ではない）。ただし学習者向けの宣言とマニュアルにはその事実を書く（第 1 波で記載済み） | ②人の権利・制度 |
| 精読モード ON のとき、トピック教材の主結果を伏せるか | 伏せない（UC1 は議論の開幕に予想を挟むところまで。教材本文の非表示は既定の読書体験を変え、教員の書いた教材を AI 側の都合で隠すことになる）。伏せるなら教員が教材ごとに選ぶ別機能として設計する | ①不変条項の解釈（UC1 の射程） |
| 入力欄の下の開示の 1 行に「どこに残るか・いつ消せるか」（保持・撤回）を足すか | 足さない（1 行は外部の AI に渡ることの告知に絞る）。保持・撤回は「わたしの記録」の先頭の事実文と student §18 で 1 操作以内に読める | ②人の権利・制度 |
| 見送った候補を戻す操作を足すか（IK-0660） | 足す（P4 で行は残っており、戻す操作が無いことだけが可逆性の穴）。足すまでは宣言とマニュアルに「戻す操作はありません」と書く（第 1 波で是正済み） | ①不変条項の解釈（§3.7 可逆） |

### 20.10 再演（第 1〜3 波を砂場に入れた後・2026-10-10 未明・指揮者の記録）

**運転**: 第 2・3 波を docker cp + restart で入れ、ハーネスの直し（§20.7）を当ててから、学生 3 名（st-02 / st-05 / st-06）と教員 2 名
（te-02 / te-03）のレーンを新しい mailbox 名で起こし直した。最初の再演は利用上限で頭脳（Opus）が一斉に落ち、レーンは
MailboxTimeout で止まった（放棄する製品側の要求には `{"error": …}` を置き、新しい mailbox 名で再起動）。

**GONE（砂場で消えたことを確認）**
- 自分の言葉で書いた持ち越しが保存され、翌日の扉に「昨日の自分の言葉がそのまま」出る（st-02 / st-05）。保存の事実文は画面の文
  （「次回の開幕で待っています」「残しました。次に開いたとき表示されます」）。
- 目次の状態語（「学習中」「まだ順番が来ていません（開けます）」）で迷わない（st-02 / st-06）。
- 問いの軌跡の印が「訂正あり」で採点に読めない / 🗑 の直後に取り消しの事実文（st-06）。
- 議論の開幕が「問い・骨格・主張」に分かれ、点線の凡例が付き、解析の内部語が出ない（st-02）。
- リリース前の確認: 行番号が押した行に効く（ハーネス側）/ 公開は「公開しました。」+ ステップ 3 の「ステップ 2 で確認として記録したもの」
  / セッション名の途中切れ・登録結果の英語項目名が消えた（te-03 / te-02）。

**STILL**: 持ち越しに答えても扉が変わらない（IK-0668）/ 挨拶・予想が「問い［未解決］」（IK-0667）/ ✏️ 書き直し直後は無言（IK-0682）/
「問い」観点の根拠が生成文で逐語と見分けられない（IK-0677）/ 却下理由・戻す操作・読んだ行の目印が無い（IK-0676）/ ⚓ の主張文の
記号欠落と英語（IK-0665 / 0685）/ 出典の論文名の揺れ（IK-0664）。

**NEW**: 配置は document 単位で共有され、別コースでの確認・却下・再検討の状態が新しいコースのウィザードに出るが、誰が・いつ・どの
コースで判断したかが行に無い（`reviewed_at` / `review_note` を UI が描かない）/ ステップ 3 の「N 件を受講者に表示」が却下を含む
（IK-0692）/ 確認する配置が 0 件のときの事実文が無い（IK-0691）/ 「今日のあなたの言葉」が同じセッションの発話の直後に空
（切り分け前）/ 取り消しの確認文「削除します」と結果「取り消しました」の語の不一致 / 同じ題名のコースが 2 つ並び区別できない。

**再演できなかったもの**: te-02（閲覧のみ）の「次へ」— te-03 が同じ論文の配置を別コースで確認したため pending が 0 になり、
skipped の経路は砂場では観察できない（サーバ側は `test_release_review.py` の閲覧のみケースで固定）。st-06 の帰属候補の見送りは番号付きの候補と見送りの事実文で成立したが、見送った候補のことは「わたしの記録」に出ない（NEW・IK-0660 の表示側）。

**従量**: 製品 LLM は頭脳の肩代わり（初回・再演とも）。本物の従量は embedding だけ（proxy 経由。api-server 再起動ごとの help_kb 再同期を含む）。arXiv 0 回。

**ハーネス側（第 3 の直し・`harness_fixes.md`）**: `selection_ref` が頭脳の `selection_text` を上書き / 出典本文の observation の切り詰め /
個人地図の空状態・「マイコース」見出しを投影が落とす / E7 の `STRUCTURE_PERSONAL_KEYS` に `status_label` / precondition 文の識別子 /
course.open の投影に扉 / 画面文に内部名 / **未対応**: ハーネスがチャットの `history` を窓（直近 N 往復）で送るため、サーバがクライアントの history で保存履歴を置き換え、st-05 の `_discussion` の session 1 の往復が DB から消えた（「今日のあなたの言葉」が空だった原因。画面は全履歴を送るので製品では起きないが、`persist_chat_history` がクライアントの history を信用する弱さは第 4 波候補）。選別は `uxsim/runs/round23/round23_report.md`、課題は IK-0649〜0694。


## 21. 論文レーダー自律周回（第 24 周相当・2026-10-16）

画面を通しで受講する周ではなく、**処理済みの教材を起点にレーダーで探し → 分野のペルソナ（審判）が関心を判定し → 関心の持てる
候補だけを取り込み → 解析後の構造で近さを確かめ → 次の起点へ連鎖する**を止めるまで回す周。ハーネスは製品の HTTP API だけを叩き
（PE2）、取り込みの弁は審判の decision ファイル（教員の明示操作の代行）に置いて製品に自動取り込みを足さない（PD1）。製品の LLM は
bridge（`uxsim/sandbox/claude_proxy.py` → mailbox → Opus の頭脳）で肩代わりし、embedding は本物、arXiv は 1 周 ≤ 2 回。

- driver: `uxsim/sandbox/radar_loop.py`（v2 = 浅い段 scan / 深い段 deep・decision の handshake・STOP・attach）、overlay:
  `uxsim/sandbox/docker-compose.bridge.yml`（開発スタックの api-server だけに重ねる）、記録: `uxsim/runs/radar_loop/`（git 管理外。
  規律は BRAIN_INSTRUCTIONS / JUDGE_INSTRUCTIONS、現状の棚卸しは UX_BASELINE）。
- 方式・運転手順・実測（scan-01 の縮退 → 手入力の参照 → 是正直後の失敗 → 是正後の初の取り込み判定）・見つかった根と是正・
  残課題の正本は [論文レーダーの自律周回](../features/paper_radar_autonomous_loop_design.md)。課題は IK-0695〜IK-0710。
- 本章の他の周との違い: 審判は E1〜E8 ではなく分野のペルソナによる関心判定（数値を材料にしない）で、審判の判定を**取り込みの前**に
  挟む（1 本の解析に 10 時間を超えるため — 同設計書 §3）。深い段の審判は本書の時点で未実施。
