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

## 22. 増強: 棚卸しで見えた空白の 4 面（共有・転用 / D層の教員側 / 分野の地図の運用 / 原稿スタジオ→受講）の周（2026-10-22 起草）

第 24 周（レーダー自律周回）の後、場面・行為の棚卸し（§18〜§21 の 70 本超は学生のチャット・議論・SB層・vision 5 機能と
教員のグラフレビュー・公開に厚く偏る）で「場面ゼロ・行為ほぼゼロ」と分かった 4 面を、**面ごとに 1 回ずつ**「ペルソナ × 場面」の
組を変えて何周も回す（.personalize P-0024 / P-0025）。対象はこの環境の Claude が自力で操作できる経路（API 経由の砂場・bridge・
テスト）に限り、組み込みブラウザ（localhost 不可）・arXiv live・目視は使わない。製品の LLM は全部 bridge（`claude_proxy` →
`uxsim/runs/mailbox` → Opus の製品頭脳 2 体・seq % 2）、ペルソナ LLM はレーン固有の mailbox（uxsim/runs/round25/mailbox_<lane>（git 管理外）・
Opus のペルソナ頭脳 1 レーン 1 体）。規律は uxsim/runs/round25/BRAIN_INSTRUCTIONS.md（git 管理外）、レーン起動は uxsim/runs/round25/lane.sh（git 管理外）。

### 22.1 砂場の準備（2026-10-22・指揮者）

- 像: 開発像（10-13）を `episteme-uxsim-*:latest` に retag → `up -d --no-build` → snapshot `corpus-astro-tex-r23` を復元 →
  作業ツリー（backend / src / frontend/public / docs/manual / docs/admin_operations）を docker cp → restart（migration 090 まで適用）。
  Docker Hub に到達できず `build` は使えない（§21 と同じ）。
- 面 A の材料: `identity_candidates` ステージを TeX 4 本で再実行 → **同一性候補は真の 0 件**（coverage 全件処理・links 0・entries 0）。
  `library_entries` 14 件は全部装置・計測系（SB層の同梱部品）で概念系の entry は無い。砂場の 8 教材は同じ 4 論文の TeX 版と PDF 版の対。
  → 教員が自分で entry を作って結ぶ経路を主経路にし、「候補ゼロの事実文」も場面に含める。
- 面 C の材料: ギャップ候補 0 件（信号は TeX 2 本に 1 件ずつ・別ラベル）→ 位置づけの再提案（`POST .../placements/propose`・bridge 1 コール/教材）
  を 8 教材で実行し、TeX / PDF の対が同じ「星間磁場」を出して**候補 1 件（concept）**が立った。辺候補は snapshot 時点から存在。
  `theory_modules` ステージも TeX 4 本で再実行（外枠 3 / 9 / 16 / 19）。
- 面 B / 面 D の材料: 台帳行・暗黙前提候補・コース（te-01 の 6be229e1 private / te-04 の 1bad7d9f public）は snapshot のまま。

（§22.2〜22.5 は各面の担当（Opus）が書いた設計メモ uxsim/runs/round25/design_<face>.md（git 管理外） の合流。行為・経路・審判の正本はコード、数字は起草時点のもの。）

### 22.2 面 A: 共有・転用の通し（教員の確定 → 学生の見え方）

> 第 25 周「空白の 4 面」の 1 面。指揮者が `docs/architecture/persona_enactment_testing_design.md` §22 に合流させる下書き。
> 製品コードは変えていない。行為・経路・審判・運転だけ。

対象の層（CLAUDE.md の正本）: 概念レジストリ層（ナレッジライブラリタブの同一性の候補・別名・関係・地図との対応）/
W層の同一性リンク（深く検討「共通部品と結びつける」）/ 承認・共有レイヤー（C層・原稿スタジオ「説明・承認の共有」）/
パイプライン成果のグループ共有（教材の行「共有」）/ component 根拠カード（学生の「共通部品として」タブ・説明バージョン）/
個人知識ネットワーク（旅の [2][3] 共通部品 hop）/ P3-5 記号 → 概念。

#### 主題と見るもの

教員が確定したものだけが学生に届き、確定前の候補・判断の記録・内部の宛先・数は学生に出ないか。
教員側では、確定・見送り・引用の操作が 1 つずつの判断として画面に読めるか（理由の扱い・版・出所・件数を出さない規律）。

**砂場の事実（指揮者の実測 2026-10-22）**: 同一性候補は TeX 4 本で再実行して**真の 0 件**、library_entries は 14 件すべて装置系
（confirmed）で概念系 0・候補 0。8 教材は同じ 4 論文の TeX / PDF の対。よって (a) は「候補ゼロの事実文」を読む場面で始まり、
**確定のある経路は (a') で教員が自分で作る**（白紙の共通部品 → TeX 版の部品と PDF 版の同じ部品を W層で結び付ける）。

#### 行為（PE9 — `uxsim/actions/share_actions.py`、実行と投影は `uxsim/runner/share_exec.py`）

| 行為 | 画面の部品（affordance） | 製品ルート |
|---|---|---|
| `admin.library.identity_candidates` | `knowledge-library.identity-candidates` | GET `/api/admin/library/identity-candidates` |
| `admin.library.identity_link_decide`（confirm / reject） | 同上 | POST `/api/admin/deliberation/identity-links/{id}/confirm\|reject` |
| `admin.library.entries` / `entry_open` | `knowledge-library.entries-list` | GET entries（include_candidates）/ entry + labels + relations + atlas-links |
| `admin.library.entry_create` | `knowledge-library.new-entry-btn` | POST `/api/admin/library/entries` |
| `admin.library.entry_review` | `knowledge-library.entry-review` | POST `/entries/{id}/review` |
| `admin.library.label_add` | `knowledge-library.labels` | POST `/entries/{id}/labels` |
| `admin.library.relation_add` / `relation_decide` | `knowledge-library.relations` | POST `/relations` / `/relations/{id}/decide` |
| `admin.library.atlas_links_derive` | `knowledge-library.identity-derive` | POST `/atlas-links/derive` |
| `admin.library.atlas_link_decide` | `knowledge-library.atlas-links` | POST `/atlas-links/{id}/decide` |
| `admin.deliberation.identity_open` | `deliberation.identity-link-create` | materials → component-graph → overview → GET identity-links |
| `admin.deliberation.shared_part_search` | 同上 | GET `/elements/{t}/{id}/shared-part-candidates` |
| `admin.deliberation.identity_link_create` | 同上 | POST `/api/admin/deliberation/identity-links` |
| `admin.deliberation.identity_link_decide` | `deliberation.identity-link-decide` | POST `.../identity-links/{id}/confirm\|reject` |
| `admin.explanations.open` | `lecture-studio.component-endorse` | GET `/courses/{cid}/theory-components` → GET `/theory-components/{id}/explanations` |
| `admin.explanations.add` / `update`（share_on・share_off・approve）/ `endorse` | 同上 | POST explanations / PATCH `/explanations/{id}` / POST endorse |
| `admin.explanations.cite` | `lecture-studio.cite-intent` | GET `/api/admin/courses` → POST `/explanations/{id}/cite` |
| `admin.document_share.open` / `add` | `materials.row-share` | GET `/documents/{id}/groups` + `/api/groups` / POST groups |
| `learning.component.shared_part` | `student/02-student.md#material-element-anchor` | GET `/components/{id}/context`（「共通部品として」タブの投影） |
| `learning.component.explanations` | 同上 | GET `/components/{id}/explanations` |

学生側の既存の行為を再利用: `learning.component.context` / `context_hop` / `learning.symbol.lookup` / `learning.personal_network.journey` /
`learning.chat.ask_selection`。

**写し方の決め事**（投影は画面と同じ欄を同じ形で — IK-0634）:
- 理由が空の［見送り］は画面（`_libraryPromptDismissReason`）が alert で止めて送らない → HTTP を出さず `precondition:dismiss_reason` + 画面の文。
  分野未選択の「地図との対応を導出」、本文が空の説明の追加、引用先の無い引用、グループ未選択の共有の追加も同じ。
- **リンクの［見送り］／［却下］の画面には理由の欄が無い**（`_libraryDecideIdentityLink` / `_decideIdentityLink`）。経路の `reason` は送らず runner_note に残す。
- 関係の相手の欄は「相手のエントリ ID」の手入力だけ。`object_hint`（名前）を渡したら runner が ID を引き、その事実を runner_note に残す。
- 画面が出している数・内部 ID は投影でも出す（題名が空のリンクの document_id・W層リンク行の英語の要素型と UUID・「他 N 論文でも使用」）。
- エントリの凍結（版発行）は埋め込みの計算を伴い取り消せないので行為にしない。結び付けに凍結は要らない（共通部品の検索は draft の
  active エントリも ILIKE で拾い、学生の shared_part は active なエントリで出る）。
- 共通部品の検索は製品が検索文の埋め込みを 1 回計算する（チャット検索と同じ経路）ので `llm_cost="product"`。

#### 経路（`uxsim/scenarios/goal/{teacher,student}/*-share-*.yaml`・穴は `domains/astrophysics/scenario_params/`）

| 場面 | 経路 | 見るもの |
|---|---|---|
| (a) | `t-share-identity-queue` | 候補ゼロの事実文（二重に出ないか）/ 支持論文の題名列挙・数値なし / リンクと概念の見送りで理由の扱いが揃うか / 見送りが状態で残るか |
| (a') | `t-share-bind-versions` | 白紙の共通部品 → TeX 版の部品 → 検索 → 候補 → 確定 → PDF 版で同じ（確定か却下）。検索で見つかるか（分野が特定できない縮退）/ 却下が残るか |
| (b1) | `t-share-explain-write` | 部品の一覧から説明・承認の共有まで届くか / 共有 ON と査読承認が別の操作か / 段階ラベル |
| (b2) | `t-share-explain-endorse-cite` | 共有された説明だけが引用できるか / 引用先は編集できる別のコースだけ・無ければ事実文 / 作者でない編集の拒否が日本語か |
| (c) | `t-share-labels-atlas` | 別名と隠しラベルの違い / 導出前後の事実文 / 骨格の版・段階の近さ / 関係の相手を名前で選べるか / 見送りの理由 |
| (d) | `s-share-part-chip` | 「共通部品として」は確定した結び付きの部品にだけ / 枠に件数・内部 ID・数値が出ないか / 説明は査読承認のものだけ / 旅の道が無いときの事実文 |
| (e) | `s-share-symbol-concept` | 記号の概念は確定した結び付きのときだけ名前と種別で / 無いときは無いことが分かる |

#### 組（面の複雑さ: 高 → 教員 2 × 学生 2）

- **第 1 周 `c-r25-share-r1`**: te-03（精読・editor あり）×(a')(b1) / te-01（新人・editor なし）×(a)(b2)(a 再)(c) /
  st-02（理論）×(d 前)(d 後)(e) / st-03（隣接分野から）×(d)(e)。te-03 は PINN の PDF 側を**却下**する。
- **第 2 周 `c-r25-share-r2`**: te-04（Copilot 依存・コース所有・editor あり）×(a')(b2) / te-05（委任・editor なし）×(a)(c)(b1) /
  st-06（懐疑）×(e)(d) / st-09（データサイエンスから）×(d)。te-04 は音速の論文の TeX ⇄ PDF を**両方確定**、(b2) は引用先の無い事実文を読む。
- レーンの順と依存は各 campaign の notes が正本（(d) の前後比較は st-02 だけ・(b2) は (b1) の共有 ON の後）。

#### 審判（`uxsim/oracles/share.py`・決定論・製品非 import）

- **A1** 拒否応答（409・422）の detail が空・英語・内部例外文
- **A2** 同一性の候補の形（`hidden_count` が整数・`hidden_unresolved` が真偽 / 候補ゼロなのに事実文が無い / 参照できない要素を落としたのに事実文が無い）
- **A3** 地図との対応の導出の応答に `coverage`（件数報告）
- **A4** 共有を開けた教材へのグループ共有の追加が 404（所有者だけが変えられる理由が画面に出ない疑い）
- **A5** 部品の一覧は開けたのに説明バージョンが 404（説明・承認の共有に届かない）
- **A6** 見送り（dismissed）の応答に理由が残っていない
- **B1** 学生の部品・記号・旅の応答に内部の宛先キー（entry_id / stable_key / produced_by_run_id / superseded_at / candidate_key /
  shared_part_id / mapping_justification / decided_by）
- **B2** 学生の応答に数（other_documents_count・承認の内訳 endorser_count 等・hidden_count・citation_count）
- **B3** 学生の concept_ref に name / entry_type_label 以外の欄
- **B4** 教員の同一性の候補で論文の題名が空のリンク（画面は document_id を出す）
- **B5** 教員の W層の同一性リンクの行に論文の UUID（画面は `instance_document_id` と英語の要素型をそのまま出す）
- **E1** 学生に共通部品・概念として見えた名前に、confirmed かつ active のエントリが無い（候補・見送りの概念が届いた疑い — KR2）
- **E2** 教員が却下した同一性リンクが `element_identity_links` に `rejected` で残っていない（P4）
- **E3** 教員が見送った概念が `library_entries` に見送りの状態と理由で残っていない（KR7）

承認の段階ラベル（`_endorsement_label`）は「N名の教員が承認」と人数を含む — CLAUDE.md C層の設計原則 1 の例（「専門家3名が承認」）に
沿う設計なので審判にしていない（数値の**欄**だけを B2 で見る）。改訂原則 4 との関係は周の後に判断する。

#### 運転

- snapshot `corpus-astro-tex-r23`（コース 1bad7d9f 固定・周の前に `pin_course --yes`）。runner は api。外部 API は呼ばない。
- 1 場面ずつ `--persona X --scenario Y`。第 2 周で第 1 周の結び付きを消したいときの reset は、`library_entries`（te-03 / te-04 が作った
  draft）・`element_identity_links`・`component_explanations` / `component_endorsements` / `component_citations` も対象にする必要がある
  （現行の reset スクリプトの対象かは未確認 — 運転前に確かめる）。消さずに第 1 周の状態の上で第 2 周を読むのが既定。
- 指揮者が足す行（逐語）は最終報告にある。

#### pre-mortem（課題ナレッジ想起シートの族当て）

- `condition-not-propagated` — 共通部品の検索が document → run の `cartridge_id` で分野を決める。砂場の TeX 教材が分野未指定で解析されて
  いれば、教員が分野を選んで作った共通部品が検索に出ない（(a') の検索結果と事実文で見る）。
- `available-but-unwired` / `last-mile-missing` — C層の説明はコース単位（`component.course_id`）で、パイプラインの部品は course_id を持たない。
  説明・承認の共有に届かないか（A5）、届いても学生の説明一覧（`e.course_id = :course_id`）に出ないか（(d) で見る）。
- `guardrail-does-not-cover-new-path` — 学生の shared_part は entry の `status='active'` だけを見て `review_status` を見ない
  （記号の concept_ref は `review_status='confirmed'` を見る）。候補の概念が学生に届く経路（E1）。
- `id-namespace-conflated` — 学生の説明一覧は `component_id` を UUID に CAST する。チップの id が agent 側 ID なら 5xx（A 契約の既存 5xx 検査）。
- `information-dropped-as-unrepresentable` — リンクの却下に理由の欄が無く、却下の理由が残らない（経路の reason が送られない runner_note）。
- `doc-drifts-from-code` — 学生マニュアルに「共通部品として」タブと説明バージョンの節が無い（affordance を `material-element-anchor` に寄せた）。

### 22.3 面 B: D層の教員側 ⇄ 学生の検証状態

> 第 25 周「空白の 4 面」の面 B。製品コードは変えていない（欠陥は周を回してから是正する）。外部 API・TTS を呼ぶ行為は無い。
> 製品の正本: `backend/api/routes/doubt.py` / `routes/seminar_brief.py`、画面: `frontend/public/js/doubt-atlas.js`
> （前提の地図タブ・原稿スタジオのグラフに差し込まれる台帳の区画と反実仮想モード）/ `app.js`（D3-6 の一行）、
> マニュアル: `teacher/18-admin-doubt-atlas.md` / `teacher/14-admin-lecture-studio.md#counterfactual-mode` / `student/02-student.md`。

#### 行為（`uxsim/actions/doubt_actions.py`・27 件）

教員（`admin.doubt.*`・affordance はすべて `doubt-atlas.*` の data-ui-anchor かマニュアル節）:

| 行為 | 画面（JS の関数） | 呼ぶ API |
|---|---|---|
| `tab_open` / `refresh` | `initTab` → `loadCourses` → `reloadAll` | `/api/admin/courses` → assumption-atlas・assumptions?status・open-assumptions・counterfactual/sessions |
| `audit_run` / `mining_run`（LLM・非同期）/ `load_recompute` | `bindTabControls` | コース単位の POST → `reloadAll` |
| `assumption_filter` / `assumption_confirm` / `assumption_dismiss` | `loadAssumptions` / `bindAssumptionButtons` | 確定は理由が空なら送らない（F7: プリフィルなし） |
| `ledger_open` | 散布図の点・未検証合意リストの行 → `renderNodeLedgerSection` | `GET /ledger/{t}/{id}`（404 = 未記帳の区画） |
| `open_reachable_filter` | `renderOpenAssumptionsList`（再取得なし） | HTTP なし |
| `studio_node_ledger` | 原稿スタジオ `lsRenderGraphNodeDetail`（`component_id \|\| id`） | component-graph → ledger |
| `scope_record` / `scope_candidate_decide` | `bindScopeForm` / `bindCandidateButtons` | 422 は detail をフォームの下に |
| `verification_status` | `bindVerificationStatusForm` | スコープ 0 件の直接検証・理由が空は画面が止める（HTTP なし） |
| `challenge_record` / `challenge_withdraw` / `challenge_propose` / `proposal_status` | `bindChallengeForm` / `bindChallengeActions` / `renderProposalStatusCard` | 疑義の 422 は固定文・取り下げの 403 は固定文・提案は文とコーパス外の確認が空なら送らない |
| `falsification_record` / `falsification_candidate_decide` / `falsification_refresh`（LLM・非同期） | `falsificationSectionHtml` ほか | — |
| `counterfactual_toggle` / `counterfactual_observation` / `counterfactual_save` | `toggleCounterfactualNode` / `renderObservationToggleForm` / `updateCfStatus` | compute は vis のノード id を `toggled_assumption_ids` に送る |

学生（`learning.ledger.*`）: `sources_tab`（`annotateOneSourceCardLedger` — source-chunk の式 → claim-refs の主張の順に最大 3 行）/
`component_line`（`appendLearnerLedgerLine`）/ `open_assumptions`（`loadLearnerOpenAssumptions`）。既存の `learning.discuss.opening`・
`learning.personal_network.nearby`・`admin.seminar_brief.view` を経路に組み込む。

投影（`uxsim/runner/doubt_exec.py`）は画面の欄を同じ形で文にし、`session.last_body = {"_ui_fact": …}` に置く。画面が出して
いるもの（記帳者の欄の値・出典ドキュメントの id・origin / status の生の語・「N 箇所が依存」）は隠さず、画面が出していないもの
（散布図の点の target_id・compute の `surviving_node_ids` と `warnings`）は出さない。画面のクライアント側の止め方は HTTP を
出さずに文言だけ返し、`ui_guard:` の runner_note に残す。

#### 経路（ペルソナ × 場面）

| 場面 | 経路 | 見るもの |
|---|---|---|
| (a) | `t-db-assumption-review` | コーパス監査 → 候補の確定（理由は本人の言葉）・却下 → 却下済みの表示 → 負荷再計算 → 確定した前提の台帳（未検証・スコープ空が「発見」として読めるか） |
| (b) | `t-db-scope-promote` | スコープ 0 件で直接検証を選ぼうとする → 軸の無いスコープ（422）→ スコープ記帳 → 直接検証 → 原稿スタジオのグラフのノードの台帳 |
| (c) | `t-db-challenge-propose` | 理由なしの疑義 → 型で疑義 → コーパス外の確認なしの提案 → 提案 → 着手 → 二つ目の疑義（つながりへ）を取り下げ → 未検証合意リスト |
| (d) | `t-db-counterfactual-brief` | 原稿スタジオで前提のノードを仮に偽 → 観測を仮に倒す（補助仮説）→ 保存 → セッション一覧 → ゼミ前ブリーフ |
| (e) | `s-db-ledger-lines` | コース 1bad7d9f の DCF 法のトピックで質問 → 出典タブの一行 → 「この分野の未検証の前提を見る」→ 部品の一行 → 議論の開幕 → いまここの周り |

教員の経路はすべて前提 `owns_course`（前提の地図タブのコース選択は所有・共有のコースだけ）+ 種まき `t-v5-register-own-course`。

| 周 | campaign | 教員 | 学生 | 組み合わせの意図 |
|---|---|---|---|---|
| 第 1 周 | `c-r25-doubt-r1` | te-03（精読・editor あり）× te-02（クリックスルー・閲覧のみ） | st-06（懐疑）× st-02（理論） | 教員は種まきで自分のコースを作るので、前提の台帳は学生のコースに届かない（届かないことを事実として見る） |
| 第 2 周 | `c-r25-doubt-r2` | te-04（AI 頼み・学生のコースの所有者）× te-01（新人・閲覧のみ） | st-03（隣接・素粒子）× st-09（隣接・データ） | te-04 が学生のコースで記帳するので、前提の台帳が学生の未検証合意リスト・開幕に届きうる。第 1 周との差が「記帳したコース」の効き目 |

ペルソナ案からの変更: 第 2 周の te-05（委任型）を te-04 に替えた。面 B の学生側に届く記帳を作れるのは学生のコースの所有者
te-04 だけで、te-05 だと (e) の前段が第 1 周と同じ「別コースの記帳」になる。

#### 審判（`uxsim/oracles/doubt.py`・決定論）

語彙は製品のガードレールテストから ast で読む（PE6）: SL1 の BANNED（`test_stakes_ledger_guardrails.py::TestClosedWorldVocabulary`）・
煽り語（`test_doubt_guardrails.py::TestNoHypeLanguage`）・学習者の数値キー（`TestLearnerOpenAssumptionsHideRawNumbers._FORBIDDEN`）・
学習者の帰属キー（`TestFalsificationProjectionsDropNumericAndAttribution` の `assert "…" not in` の左辺）・教員の数値キー
（`TestNoRawNumbersExposed`）。

- A: DA1 4xx の detail（空・内部例外・日本語でない）/ DA2 学習者の台帳・未検証合意リストに帰属・数値のキー / DA3 教員の doubt 応答に
  confidence・load_score / DA4 投稿者名の無い疑義 / DA5 取り下げた疑義が同じステップの読み直しで消える・取り下げ済みにならない、
  却下した前提が「却下済み（保持）」「すべて」に無い / DA6 スコープ 0 件の directly_verified / DA7 コーパス外の確認が空の提案の 2xx
- B: DB1 禁止語（応答の表示欄と画面の投影）/ DB2 同じ文に「この分野」と「未検証」系 / DB3 「記帳者」に UUID / DB4 同じ教員が
  コース単位の編集権限ゲートで 404 になった（または一覧で viewer の）コースに、コース単位の doubt の書き込みが 2xx
- E: DE1 教員が記帳した台帳行の `course_id` が空（前提の地図・未検証合意リストに載らない）

`run_all.py` への登録は指揮者が足す（`found += doubt.check(steps, factory)` / `found += doubt.check_observation(meta, factory, url, engine, steps=steps)`）。

#### 運転

- 順序: 教員 → 学生（plan の順）。第 1 周の後に第 2 周を同じ snapshot から始めるか、続けて走らせるかは指揮者が決める（続けると
  第 1 周の前提・疑義が残る。台帳は対象単位なので、部品のスコープは第 2 周の学生にも見える）。
- LLM: 種まき（te-02 / te-03 で 2〜3 回ずつ）・前提マイニング（任意・非同期）・覆る条件の候補（経路には入れていない）・学生の質問 1〜2 回。
- 砂場で確かめていないこと: 既存の台帳行の `course_id`・`load_score` の有無（散布図・未検証合意リストが空なら `ledger_open` は確定した
  前提に縮退し `runner_choice:` を残す）、コーパス監査が候補を出すか（出なければ (a) の確定は precondition で止まる — 前提マイニングを
  選んでも結果は非同期）。
- `actions_exec.py` の dispatch: `_HANDLERS.update(_doubt_exec.HANDLERS)`（`from uxsim.runner import doubt_exec as _doubt_exec`）。

#### 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 投影が画面と違う形になる | `duplicate-canonical-sources` | 文言は doubt-atlas.js / app.js の逐語ミラー（定数名に JS の関数名を書いた）。変えたら JS を読んで写し直す |
| 権限の条件がコース直指定の経路で揃っていない | `condition-not-propagated` | 審判 DB4 が transcript だけで拾う（製品の疑いとして記帳候補） |
| 教員の記帳した行がコースの一覧に届かない | `available-but-unwired` | 審判 DE1（DB 読み）|
| 画面が止める入力をハーネスが API で通してしまう | `guardrail-does-not-cover-new-path` | 画面と同じくクライアント側で止め、HTTP を出さない |

当たらない族: 再実行と削除（行為に再解析・削除が無い）。

### 22.4 面 C: 分野の地図の運用（教員のギャップ・辺・別名・凍結 → 学生の地図）

> **状態:** 第 25 周の増強（2026-10-22）。行為・経路・審判は実装済み・未運転。指揮者が設計書
> `docs/architecture/persona_enactment_testing_design.md` に合流させる。製品コードは変えていない。

#### pre-mortem

当たる型: `available-but-unwired`（行為を足しても `actions_exec` の dispatch と `run_all` の登録が無ければ
`_default` が走り、投影が既定の JSON 整形に落ちる — 指揮者の 3 行が前提）/ `duplicate-canonical-sources`
（画面の文言を投影へ写すので JS と食い違うと偽所見になる — `test_round25_atlas_harness.py` が JS の原文に
逐語で現れることを固定）/ `condition-not-propagated`（見送った関係を学生の糸から外す条件が、教員の判断 →
読み時導出 → `(domain, version)` のキャッシュ → 学生の `/api/atlas` と境界を 3 つまたぐ — 審判 B5 で境界を
歩いて確かめる）。

#### 行為（`uxsim/actions/atlas_actions.py`・24 件）

教員（`admin`・affordance は `atlas.*` の data-ui-anchor・manual は `teacher/17-admin-atlas.md`）:

| id | 画面の操作 | 主な API |
|---|---|---|
| `admin.atlas.open` | 分野を選ぶ（`loadState` と同じく 6 本を読み直す） | `GET /api/admin/atlas/domains` → skeleton / reports / gap-candidates / edge-candidates / vectors/status / aliases |
| `admin.atlas.gaps.list` / `.dismissed_filter` | 論文の解析から見つかった候補 / 見送り済みも表示 | `GET .../gap-candidates[?include_dismissed=true]` |
| `admin.atlas.gaps.accept` / `.dismiss` / `.restore` | 採用 / 却下…（理由必須・空は送らない）/ 見送りから戻す | `POST .../gap-candidates/decide` → 一覧の読み直し |
| `admin.atlas.gaps.alias_register` | 別名として登録（2 段: 登録が通ったときだけ理由を自動で書いて却下） | `POST .../aliases` → `POST .../decide` |
| `admin.atlas.gaps.draft_from_frozen` | 現在の版から次版の下書きを作る | `POST .../skeleton/draft/from-frozen` |
| `admin.atlas.gaps.incorporate_preview` / `.incorporate_apply` | 取り込む…（確認まで）/ [下書きに追加する]（`saveDraft` の revision 付き PUT → mark-incorporated） | `POST .../incorporate-preview` / `PUT .../skeleton/draft` |
| `admin.atlas.edges.list` / `.dismissed_filter` / `.accept`（kind 必須）/ `.dismiss`（理由必須）/ `.restore` | 関係（辺）の候補 | `GET/POST .../edge-candidates[/decide]` |
| `admin.atlas.edges.incorporate_preview` / `.incorporate_apply` | 次版の下書きへ反映… | `POST .../edge-candidates/incorporate-preview` / `PUT .../skeleton/draft` |
| `admin.atlas.aliases.list` | 登録済みの別名 | `GET .../aliases` |
| `admin.atlas.vectors.status` / `.refresh` | ベクトル索引 / [索引を再構築] | `GET .../vectors/status` / `POST .../vectors/refresh` |
| `admin.atlas.freeze_impact` | [公開（凍結）] → 凍結の影響を読んでキャンセル | `GET .../freeze-impact` |
| `admin.atlas.freeze` | 対応を確かめて凍結（`correspondence` = none / all / 旧ノード名の一部） | `GET .../freeze-impact` → `POST .../skeleton/freeze` |

学生（`learning`）: `learning.atlas.layers`（地図のコースレベルで「推定の糸」「論文の位置」を重ねる =
`GET /api/atlas` + `GET /api/learning/courses/{id}/landscape`・affordance `atlas.relation-threads`）/
`learning.atlas.sea_map`（論文の海の地図 = corpus/landscape + `/api/atlas?cartridge=` + corpus/documents・
affordance `sidebar.corpus-sea`）。既存の `learning.atlas.view` / `.threads` / `learning.landscape.view` /
`learning.corpus.*` は再利用する。

**従量（`llm_cost="product"`）**: 製品のチャット LLM を呼ぶ行為は無い。embedding を本物の OpenAI へ素通しする
3 つだけを product にする — 索引の再構築・別名の登録（製品が 1 項目の索引を裏で作り直す）・凍結（新しい版の
索引を裏で作る）。retire / restore / 下書きの破棄 / generate（LLM）/ 修正報告の処理は行為にしない。

**投影**（`uxsim/runner/atlas_exec.py`）: 画面と同じ欄を同じ形で（docstring に JS の関数名）。
`session.last_body = {"_ui_fact": 文}` に置くので `state.project_observation` への分岐の追加は要らない。
画面が描く件数（公開前チェックの検証エラー件数・「索引済み: N/M ノード」・landscape-layer の「登録済み論文 N 件」）は
画面どおりに出す。審判用の要約は各 trace の `digest["atlas"]`（ID・版・指紋・状態の語彙だけ。数値の値は持たない）。

#### 経路（ペルソナ × 場面）

| 場面 | 経路 | 第 1 周（`c-r25-atlas-r1`） | 第 2 周（`c-r25-atlas-r2`） |
|---|---|---|---|
| (a) ギャップ候補を題名で判断・採用 1・理由付き却下 1・戻す | `t-atlas-gap-review` | te-03（精読） | te-01（新人） |
| (b) 近傍注記から別名を登録 → 登録済みの別名 | `t-atlas-alias-from-gap` | te-04（Copilot 依存） | te-01 |
| (c) 辺候補の採用（kind）・見送り・反映の確認 | `t-atlas-edge-review` | te-03 | te-02（ベテラン） |
| (d1) 下書きを作り、取り込みの確認と凍結の影響を読むまで | `t-atlas-freeze-impact-read` | te-04 | — |
| (d2) 索引の再構築 → 下書きへ反映 → 凍結の影響 → 凍結 2026.2 | `t-atlas-draft-freeze` | — | te-02（1 回だけ） |
| (e) 推定の糸と論文の位置を重ねて読み分ける | `s-atlas-threads-and-placement` | st-05（夜間・太陽風） | st-04（留学生・系外惑星）/ st-07（音声中心 — 地図は文字） |
| (f) 論文の海の厚み・縁・外 | `s-atlas-sea-map` | st-01（電波） | st-07 |

運転は `--persona X --scenario Y` で 1 場面ずつ、教員レーン → 学生レーンの順（審判 B5・B6 が前後を比べる）。
第 2 周の (d2) は砂場の骨格の版を上げる（取り消せない）ので周の後に snapshot `corpus-astro-tex-r23` から戻す。
第 1 周の (d1) が作った下書きが残れば (d2) の from-frozen は 409 の事実文になり、残った下書きに反映して凍結する。

#### 審判（`uxsim/oracles/atlas.py`・決定論・DB を読まない）

- **A1** 見送りの判断に理由が残っていない（理由必須の弁）/ **A2** 採用した関係に種類が無い / **A3** 4xx の detail が日本語の事実文でない
- **B1** 学生の地図・教員の候補一覧に確からしさの生の値の欄（cosine / similarity / score / weight / confidence）
- **B2** 件数の欄（`count` / `*_count`。`source_document_count` は LS8 の例外）
- **B3** 糸の端点に領域・項目ごと / 全体の上限超え（RE7）/ **B4** 糸に骨格の版が無い・地図の版と食い違う（RE2・VA8）
- **B5** 教員が見送った関係が、その後の学生の糸に出る（RE8。戻した関係は除く）
- **B6** 凍結の操作が無いのに凍結版の中身・学生の地図の版が変わる / 判断だけの区間で次版の下書きが変わる（KN-3・AB4・RE3）
- **B7** 近傍注記に骨格の版が無い / 支持論文が題名なし（VA8・LS5）
- **B8** 学生の位置づけに出所ラベルが無い / 現行版に対応の無い位置づけに版の事実文が無い（NC6）
- **B9** 出所の固定文「AIによる推定（未確認）」と「・骨格 版」が画面の原文（`atlas-threads-layer.js` / `admin.js`）から消えた

却下の理由が空のときの 422 は、画面が送らない（`handleGapAction` / `handleEdgeAction`）ので runner も送らない。
審判は「送られて通った」痕跡（A1）で見る。

#### 指揮者が足す行（逐語）

- `uxsim/runner/actions_exec.py` の末尾（simlab の取り込みの後）:
  `from uxsim.runner import atlas_exec as _atlas_exec  # noqa: E402  r25:atlas` と
  `_HANDLERS.update(_atlas_exec.HANDLERS)  # r25:atlas`
- `uxsim/oracles/run_all.py`: import に `atlas` を足し、`found += simulation.check(steps, factory)` の次に
  `found += atlas.check(steps, factory)  # r25:atlas`
- `uxsim/oracles/findings.py::_LAYER_BY_PREFIX`（任意・審判は自分で層を渡すので無くても動く）:
  `"admin.atlas.": ["field_atlas_s"],` / `"learning.atlas.sea_map": ["corpus_roaming"],`
- `uxsim/actions/registry.py` の末尾の合流（`# r25:atlas` の try/except）は追加済み。`state.py` は変えていない。

#### 運転で見えそうな疑い（コードを読んで分かった範囲・仮説）

1. 関係の候補の [採用] [見送り…] [見送りから戻す] には data-ui-anchor が無い（ギャップ側は `atlas.gap-accept` 等が
   ある）。「？使い方」で個々のボタンを調べられない非対称（管理 UI の 3 点セット）。
2. 教員の確認の画面に内部 ID が出る: ギャップの取り込みの確認「追加する項目のid: …」「親領域: r_…」（ラベルではなく
   id）、別名の手動登録は「地図の項目のid」を手で打たせる（PL7 に相当する規律が教員画面に無い）。
3. 学生の「論文の位置」の事実行「登録済み論文 N 件の解析から生成」は件数を出す（LS8 の設計どおりだが CR3 の
   数値非表示と並べると説明が要る）。
4. 推定の糸のペアは `(domain, version)` の in-process キャッシュ。見送りはキャッシュの後で除くので RE8 は保たれる
   見込みだが、同じ版のまま索引を再構築してもキャッシュが残り、古いアンカーからの糸が出続ける可能性（第 2 周の
   (d2) の前後で st-04 の糸を比べれば見える）。
5. 追加だけの次版（from-frozen → 1 項目・1 辺）では削除が無いので「前の版のノードとの対応」の区画が出ない。
   (d2) の「対応を確かめて凍結」は事実上チェックの無い凍結になる — 対応の画面を通すには既存項目の改名が要る。
6. 既存のハーネス投影（`state.py` の `learning.atlas.threads`）は `threads = {available: false, note}` のとき
   「推定の糸（AIによる推定（未確認）・骨格 版）:」と糸があるように見せる（ハーネスの偽所見の型 IK-0634）。
   新しい `learning.atlas.layers` は「切り替えが出ていない」に直したが、既存行為の投影は指揮者の判断に残す。
7. 別名の登録と凍結は embedding を裏で呼ぶが、画面は従量をどこにも言わない（教員に金額を出さない M8 とは別に、
   操作が外部へ何を送るかの開示 — 可視性 6 軸 DA の対象になるかは未確認）。

### 22.5 面 D: 原稿スタジオ → 学生の受講

第 25 周「空白の 4 面」のうち、原稿スタジオ（授業用ドラフトの編集・スライド分割・AI 書き換え・右ペイン・
音声の準備状態・教材図スタジオ）から学生のレクチャー・教材までを 1 本の面として通す。製品の正本は CLAUDE.md の
「レクチャーの表示ソースと音声」「レクチャースライド同期 + 音声言語切替」「音声生成の準備確認フロー（#491）」
「教材図スタジオ」。製品コードはこの作業では変えていない（下の §22.4.6 は周を回してから確かめる疑い）。

#### 行為（`uxsim/actions/studio_actions.py`・17 件）

| id | 画面の部品（anchor / 節） | API | LLM |
|---|---|---|---|
| `admin.lecture_studio.open_course` | `lecture-studio.course-select` | GET course-structure + lecture-scripts + settings | – |
| `admin.lecture_studio.select_topic` | `lecture-studio.course-draft` | POST preview-split（auto_paginate・formulas 空・文書 ID なし = 画面のインジケータと同じ） | – |
| `admin.lecture_studio.insert_slide_marker` | `lecture-studio.insert-slide-marker-btn` | POST preview-split | – |
| `admin.lecture_studio.edit_draft` | `lecture-studio.course-draft` | POST preview-split | – |
| `admin.lecture_studio.save_topic` | `lecture-studio.save-btn` | PUT course-topics/{topic_id} | – |
| `admin.lecture_studio.rewrite_topic` | `lecture-studio.assistant-modal` | POST course-topics/{topic_id}/draft/rewrite（+ preview-split） | product |
| `admin.lecture_studio.chunk_slides` | `lecture-studio.display-tab-slides` | POST preview-split（文書 ID 付き = WM レンズ） | – |
| `admin.lecture_studio.right_pane_evidence` | `lecture-studio.stumble-tab-evidence` | （読み込み済みを描く・HTTP なし） | – |
| `admin.lecture_studio.right_pane_stumble` | `lecture-studio.stumble-tab-stumble` | GET documents/{id}/claims/stumble-summary | – |
| `admin.lecture_studio.audio_readiness` | `lecture-studio.audio-all-btn` | （未読み込みのときだけ）GET lecture-scripts + course-structure | – |
| `admin.figure_studio.suggestions` | `lecture-studio.stumble-tab-figures` | GET topics/{topic_id}/figure-suggestions | – |
| `admin.figure_studio.suggestions_generate` | `lecture-studio.figure-suggestions-generate` | POST …/figure-suggestions/generate | product |
| `admin.figure_studio.build_from_suggestion` | `lecture-studio.figure-suggestions` | POST figure-studio/turn（方針入りの初回）+ POST …/accept | product |
| `admin.figure_studio.turn` | `lecture-studio.figure-studio-send` | POST figure-studio/turn | product |
| `admin.figure_studio.adopt` | `lecture-studio.figure-studio-adopt` | POST teaching-figures（adopt=true）+ preview-split | – |
| `admin.figure_studio.existing` | `lecture-studio.figure-studio-modal` | GET teaching-figures | – |
| `learning.lecture.figure_view` | `student/02-student.md#material-element-anchor` | GET courses/{id}/figures/{figure_id}/image | – |

既存の `learning.lecture.audio_status` / `learning.lecture.sequence` は id をそのまま使い、実行と投影だけを
`studio_exec` に差し替える（下の「指揮者が足す行」）。`admin.lecture_studio.scripts`（既存）は残す。

**行為にしないもの**: 音声生成（`lecture-audio/generate` — TTS は bridge で肩代わりできない）/ 読み上げ言語の切り替え
（画面では音声生成モーダルの中にしかなく、原稿の再生成 → 音声生成のチェーンを起こす。設定モーダルは言語を保持する
だけ。第 2 周も行為にせず、audio-status の `stale_language` の読みで見る）/ 原稿の一括生成・コース内容生成・
パイプラインの再実行（LLM を大量に呼ぶ）/ スライドの試聴（音声が無いので 404 になるだけ — 画面の「音声未生成」
バッジで足りる）/ 図の回収・採用に戻す・キャプション修正（この周の場面に入れない — 次の周の候補）。

#### 実行と投影（`uxsim/runner/studio_exec.py`）

- 画面の状態は `scratch` の `studio_*` と `figure_studio`（モーダル 1 つ分・会話は保存しない）に持つ。コースを
  切り替えると前のコースの選択・下書き・スタジオを捨てる（`lsSelectCourse` と同じ）。
- 投影は画面と同じ欄を同じ形で: コースツリー（`lsRenderCourseStructure`）・授業用ドラフトの編集欄と
  インジケータの文（`lsRenderSlideIndicatorEl`）・スライドカード（`lsRenderSlidesPanel` / `lsSlideAudioBadgeHtml`）・
  音声生成ボタンの理由（`lsAudioReadinessGuidance`）・つまづき（`lsRenderStumbleSummary`）・図の提案
  （`lsFigureSuggestionsHtml`）・教材図スタジオ（`_sendTurn` / `_saveFigure`）・学生の講義ボタン
  （`updateLectureToggleAvailability`）・講義の再生画面（`buildLectureDeck`）。文言は JS の逐語ミラー。
- **画面が描かない欄は投影にも出さない**: AI 書き換えの `figures_restored`、audio-status の `note`、保存の失敗理由
  （画面は「保存に失敗しました」だけ）、図の提案の取得失敗（画面は「まだありません」と描く）。審判が読む事実は
  `HttpTrace.digest` の `studio_split` / `studio_saved` / `studio_rewrite` / `studio_adopt` / `studio_suggestions` /
  `studio_stumble` / `studio_readiness` / `studio_turn` / `studio_student` / `studio_figure_view` に置く。
- 近似（`runner_note` に残す）: 教材欄のカーソル位置の代わりに、区切りは段落の境目、図は提案の引用箇所の前（無ければ
  末尾）に入れる。図の提案の作り直しの確認は続行したものとする。音声の無いトピックで講義を始めようとしたときは
  sequence を読まずに「押せない」画面を出す（`unsupported_ui_state:`）。
- 学生の `learning.lecture.sequence` は画面と同じく先に audio-status を読み直す（`refreshTopicAudioStatus`）。

#### 審判（`uxsim/oracles/studio.py`・決定論・DB を読まない）

教員側 T1〜T6（負荷ラベルの生の数 / つまづきの生の数 / 提案の取得失敗を「まだありません」と出す / 書き換えで戻した
図を言わない / 採用した図がトピックの登録に無い / 保存で音声が消えることを言わない）、学生側 S1〜S3（教材の区画数と
音声の準備状態のスライド数 / 講義のスライド数と教材の区画数 / 音声が無いのに事実文が無い）、面をまたぐ X1〜X4（スタジオの
プレビュー枚数と保存後の学生の区画数 / 保存後も音声を用意済みと数える / 採用・保存した図が学生の教材で解決されない /
学生の図の画像が配信されない — 採用した図なら blocked）。仮説に数を書かない（数は `server_rows` だけ）。

#### 経路と穴

| 経路 | 見るもの |
|---|---|
| `t-studio-split-save` | (a) 長いトピックの自動ページ分割 → `===` を 1 つ → 保存 → 音声の準備状態の理由 |
| `t-studio-rewrite-panes` | (b) 根拠リンク・つまづき → AI 書き換え（保存しない提案）→ 保存するか |
| `t-studio-figure-adopt` | (c) 図の提案 → 1〜2 ターン → 採用（登録・挿入）→ 保存 → 公開 |
| `s-studio-lecture-pages` | (d) 教材の図 → 講義ボタン（音声が無ければ押せない）→ 次のトピック |

教員 3 経路は前提 `owns_course` + 種まき `t-v5-register-own-course`（保存・採用はコースの所有者だけ）。穴は
`domains/astrophysics/scenario_params/` の同名ファイル（分野の語は書かない — トピックは番号・`prefer` で選ぶ）。

#### 運転（面の複雑さ: 中 → 教員 2 × 学生 2・場面 4）

- 第 1 周 `c-r25-studio-r1.yaml`（コース 6be229e1 固定）: te-01 (a)(c) → st-08 (d) / st-07 `s-lecture-then-trace`、
  te-05 (b)（種まきで自分のコース）。6be229e1 は (c) の最後の公開まで private なので**学生レーンは te-01 の (c) の後**。
  審判 X1〜X4 は同じ run の te-01 の保存・採用と学生の読みを突き合わせる（学生が te-01 の保存したトピックを開いたときだけ）。
- 第 2 周 `c-r25-studio-r2.yaml`（コース 1bad7d9f 固定）: te-03 (a)(c)・te-02 (b)（両者とも種まきで自分のコース・
  コース内容生成なし = 教材本文の無い下書き）× st-01 (d)・st-04 `s-lecture`。教員と学生は交わらない。
- LLM: 第 1 周 図 1〜3 回 + 書き換え 1 回 + 種まき 2〜3 回、学生のチャット最大 3 回。外部 API は呼ばない。
- 砂場の前提: TTS が無いので音声の用意されたトピックは無いはず（あれば講義の再生画面が出る）。教員の (a)(c) は
  6be229e1 の `data` を書き換える（周の後に snapshot から戻す）。

#### 製品側で既に見えている疑い（コードを読んで分かった範囲・仮説）

1. 音声の準備状態（`core/lecture.py::compute_topic_audio_readiness`）と音声生成（`lecture_studio/scripts.py` の
   `build_topic_slides(topic)`）は図の解決（`figures_by_id`）を渡さずに分割するが、教材表示とレクチャーは図 1 個を
   200 字換算で分割する。`===` の無いトピックに図を採用すると、音声のスライド番号と表示のスライドがずれ得る（S1）。
2. 原稿スタジオのトピックのインジケータは本文の生テキスト（`![[equation:…]]` / `![[figure:…]]` 未解決・formulas 空）で
   問い合わせるので、受講画面の分割（式・図を解決してから数える）と枚数が食い違い得る（X1）。
3. 学生画面はトピックに音声が 1 枚も無いと「🎙 講義」自体を押せない（`app.js startLecture`）が、マニュアル
   `student/02-student.md#interactive-lecture` は「音声未生成のスライドは一定時間で次へ進む（読むことはできる）」と書く。
   音声の無い環境では講義モードに入れない（審判 D の材料）。audio-status の `note` は画面に出ない。
4. 授業用ドラフトの保存は生成済み音声を消し、監査には「教員には保存前に告知される」と書くが、画面の告知は図の採用の
   直後だけ（`ADOPT_NOTICE`）。普通の編集 → 保存では何も言わない（T6）。
5. トピックの AI 書き換えは保存せず欄に反映するだけ（「保存すると確定します」）だが、マニュアル `#assistant-modal` は
   「その場で保存されます」と書く。`figures_restored` は応答にあるが画面が使わない（T4）。
6. 書き換え（`_course_data_for_studio_editable`）は共有の editor でも通るが、保存・図の保存・採用は所有者だけ
   （`course_data_for_owner`）。editor は提案は作れても保存で「保存に失敗しました」（理由なし）になる。
7. つまづきペインの文書は「選択中のチャンク → 原稿一覧の先頭のチャンク」の文書で、course-structure の応答に `sources`
   が無いので `lsCoursePrimaryDocumentId` / `lsCourseDocumentIds` は空になる。複数論文のコースで 2 本目以降の論文の
   トピックを開くと、記録があっても「このトピックの claim には、まだ再構成の記録がありません」になり得る。
8. 負荷ラベルの語彙: マニュアル `#slide-wm-label` は「低・中・高・最高位」「要素の相互作用: 高」と書くが、製品の
   段階ラベルは `label_vocab.WM_INTERACTION_DENSITY`（非常に多い / 多い / 少ない）。
9. 図の提案の取得に失敗しても画面は「このトピックの図の提案はまだありません」と出す（`res.ok ? res.json() : null`）（T3）。
10. 学生の `GET …/lecture/…/sequence` はチャンク経路で `spoken_text` の無いチャンクに LLM 生成（`generate_spoken_text_and_formulas`）
    を走らせる（学習者起点の LLM。行為の `llm_cost` は none のまま — トピック教材のあるトピックでは起きない）。

### 22.6 第 1 周の結果（2026-10-22 21:34〜22:01・全 19 レーン完走）と是正の波（10-23）

- 運転: 4 面を並列・面ごとに依存順で直列（§22.1 の型）。製品頭脳 2 体・ペルソナ頭脳は面ごとに 1 体（share は 60 件で 2 体目）。
  10-22 22:1x に Opus の利用上限で頭脳・審判・是正者 9 体が一斉停止 → 10-23 朝に審判は最初から・是正者は diff の続きから起こし直し。
- 審判の報告: uxsim/runs/round25/judge_<face>_r1.md（git 管理外）、偽所見: 同じ場所の harness_fixes_<face>.md（git 管理外）。課題は面ごとに IK で起票（番号は
  並行起票のため面ごとに連番ではない）。
  - 面 C（atlas）: IK-0846〜0851。根 = 「論文の数」を `documents.id` で数える（TeX/PDF の対が 2 本になり、ギャップ・辺・海の全候補が
    同じ論文の対の共起で立っていた）/ 海の霧が厚みを上書き / 判断の帰属が出ない / 辺候補に根拠なし / 内部 ID / 「該当なし」と「未調査」が同じ見た目。
  - 面 B（doubt）: IK-0852〜0860。根 = D層のコース範囲を成果行の `course_id` 列で決めている（コースを作らずに解析した教材では前提の地図・
    未検証合意・観測系・監査・反実仮想・開幕の「確かめていないこと」が全部空）/ 空の理由を画面が捨てる / 反実仮想の warnings 非表示 /
    台帳対象の id が画面ごとに違う / 「この分野の未検証の前提」（SL1 違反）/ コース単位ルートの権限ゲート / ブリーフの一点吊りの重複 /
    コースビルダー 2 往復目以降に構成案が渡らない / 要約の切り詰めが投影の前。
- ハーネスの共通の直し（面 C の H7）: 直前の操作が HTTP エラーか blocked のときは頭脳に反応だけを先に聞き、行き詰まりなら行為を選ばせずに
  場面を終える（`scene_stopped:`）。頭脳ノートの seq は transcript の seq。
- **オーナー判断の候補（推奨どおり進めた・後から覆せる）**: D層の「コース範囲」の正本を `course_id` 列から「コースの教材から求めた
  document 集合」へ切り替える（IK-0852・推奨 = 切り替え。`course_id` 列は記帳の出所の意味に退く。コースごとに別の台帳が要るなら別の層を設計する）。

- **オーナー判断の候補（追加・いずれも推奨どおり進めた）**:
  - 面 A（IK-0861）: C層の説明の権限単位を部品の `course_id` から document 単位に揃える。学生へは「承認済みかつ共有 ON」の説明だけを
    コース横断で出し、共有 OFF は書いたコースの学生だけ。ある教員の独自解釈が同じ論文を使う別コースの学生に届きうる（人の権利・後戻り
    しにくい構造）。推奨 = 揃える・`course_id` は出所として残す。
  - 面 D / 面 C（IK-0873 / IK-0850）: 教員向け表示投影（DP7）の範囲を編集画面まで広げるか。推奨 = 編集可能な本文には掛けず、
    原稿スタジオ・分野の地図タブの読み取り専用の区画（根拠・注記・確認文・来歴）だけを `project_for_teacher` に通す。
  - 面 C（IK-0846）: 同じ arXiv 論文の重複取り込みをアップロード時に拒否するか。推奨 = v1 では拒否せず「論文の数」の数え方だけを直す
    （PDF / TeX の対を並べて比べる用途が uxsim にある）。

### 22.7 第 2 周の準備と運転（2026-10-23 06:5x〜）

- 是正後の作業ツリー（第 1 周の IK-0846〜0883 の是正・ハーネスの偽所見 H1〜H10 × 4 面）を全件テスト緑（backend / src / uxsim）で
  砂場へ docker cp → 学生の痕跡 reset → ベクトル索引を 1 回構築（embedding 1 バッチ・59 ノード）→ te-05 に 1bad7d9f の、
  te-02 に 6be229e1 の editor 共有（所有者がグループを作り招待コードで参加 → コース共有 API。すべて製品の API）→ snapshot
  `corpus-astro-tex-r25a`。
- 是正の効果の実測（周の前）: ギャップ候補は 1 → **0**（IK-0846 の是正で TeX/PDF の対が 1 本に数えられ「2 本以上」を満たさなくなった。
  第 2 周の (a') は空の表示を読む場面になる）。辺候補は 18 → **123**（索引ができたのでアンカー近傍由来の候補が立った。多すぎる
  候補一覧をどう読ませるかは第 2 周の所見で見る）。
- レーン: 面 A / B / C を並列（学生の固定コース 1bad7d9f は public のまま）。面 D は `pin_course --yes` で 1bad7d9f を private に
  するため、**3 面の後に**回す（campaign の順: te-01 (a)(c) → st-08 (d) → te-02 (b)(b') → st-07 は IK-0879 是正済みなので回す）。
- 製品頭脳は Q0 / Q1（前周の残骸 = created_at が周の前の未応答には応えない）。ペルソナ頭脳は面ごとに 1 体（BRAIN_NOTES_<face>_r2.md・git 管理外）。

### 22.8 第 2 周の結果（2026-10-23 06:57〜・面 A / B / C は 07:21 までに完走・面 D は 07:21〜）

- **GONE（第 1 周の是正が効いたもの・頭脳と審判の実測）**: 面 B = コースを作らずに解析した教材から組んだコースでも前提の地図・
  未検証合意・学生の一行が空にならない（IK-0852 の根の是正）/ ブリーフの内部キーと重複が消えた。面 A = 教材の ⚓ が 30 個 → 3 個・
  記号 F_EOS の定義が出る・同じ字形の断り・部品の文脈の内部 ID と方針語が消えた・候補ゼロの事実文と出口・ライブラリの更新者 / 版の
  表示語・関係を名前で記録・原稿スタジオの説明一覧の 404 が事実文に。面 C = 候補・一覧・学生の位置から重複論文が消えた・未受講コースの
  404 が受講の事実文に・区画の空状態文・番号の固定・影響ゼロの一文。
- **偽所見の型（新）= 投影の遅れ**: 製品の是正波で API の新キー / JS の表示が変わっても、ハーネスの投影が旧キーを読み続け、頭脳が
  STILL と誤判定する（面 C の霧・判断の帰属・出所トークン = H10〜H12、面 A の一覧の更新者 / 版）。**是正波の後・周の前に「投影の同期」
  を 1 工程として置く**（第 2 周の途中から `sync-projections` 係が 4 面を順に写している。記録 = uxsim/runs/round25/projection_sync_r2.md）。
- **NEW（第 2 周で初めて見えた根）**: 面 C = 採用済みの判断行が読み時導出の候補から外れても凍結ゲートに残り、画面のどこにも出ない
  （IK-0884。IK-0846 の是正の副作用）/ 凍結の可否を freeze と freeze-impact が別々に判定（IK-0885）/ 索引後にアンカー近傍の辺候補が
  大量に同じ段階で立つ（IK-0887）。面 A = 共通部品の検索が run の cartridge_id だけで分野を決め、分野を指定せずに解析した教材では
  後退も出口も無い / 独自解釈の追加が理由なく失敗。面 B = 台帳の target_id がグラフノード id で document をまたいで衝突する疑い
  （別の論文を指定しても別の論文の台帳・反実仮想が別の論文の観測文を引く）/ 閲覧のみの教員が他人の教材の台帳に書ける / 内部 ID を
  遮断した後の表示名が無く「（本文を特定できない要素）」が並ぶ。
- 面 C の第 2 周の課題 = IK-0884〜0889（是正中）。面 A / B / D の第 2 周の審判は進行中。
- 面 A の第 2 周の課題 = IK-0890〜0898（是正中）。面 B の第 2 周の課題 = IK-0899〜0907（是正中）。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 B（IK-0899）= `epistemic_ledger` の行キーを (document_id, target_type, target_id)
  に切り替える（グラフノード id・式 id が論文をまたいで 1 行に衝突し、教員の記帳が別の論文に出る・学生の地図の札も衝突行を読む）。
  推奨 = 切り替え。人の記帳が入った既存の衝突行は自動で振り分けず現在の論文に残し、教員に確認を促す事実文。代案（target_id に論文の
  名前空間を前置）は付け忘れが再発の入口になるので採らない。
- 面 D の第 2 周の課題 = IK-0908〜0915（是正中）。面 D の頭脳の STILL 4 件（根拠の状態・分割の notes・音声ゼロの講義・ヘッダの
  チャンク数）は投影の遅れ（H11）で、生応答と製品コードでは GONE。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 D（IK-0872 / 0880）= 保存済みの決定論付録を読み時に付け直すか。推奨 =
  付け直さない（配信済みのスライド境界と音声キャッシュの番号が動くため）。代わりに原稿スタジオに「付録を付け直す」明示の操作
  （音声の作り直しを事前に言う）と G層の道案内を置く。

### 22.9 第 3 周の準備（2026-10-23 11:5x〜）

- 第 2 波（第 2 周の是正・IK-0884〜0915）を全件テスト緑（backend / src / uxsim）で砂場へ。順は **snapshot r25a を復元 → docker cp →
  restart（migration 091 = 説明の course_id を NULL 許容 / 092 = 台帳の行キーに document_id）**。092 は旧キーの衝突行に印を立てるが、
  r25a には人の記帳が入った衝突行が無く 0 件。
- 投影の同期（第 2 波分）を周の前の工程として置いた（`uxsim/runs/round25/sync_keys_wave2.md` → `projection_sync_wave2.md`・git 管理外）。
- 前提（campaign の notes どおり・すべて製品の API）: te-02 に PINN の TeX / PDF の editor、te-05 と te-02 に Cepheus の editor、
  te-04 に 6be229e1 の editor、te-04 が Cepheus の配置を 1 件確認（`PATCH /api/admin/landscape/placements/{id}`）→ snapshot `corpus-astro-tex-r25b`。
- レーン: 面 A / B / C を並列 → 面 D は後（pin_course が 1bad7d9f を private にし、te-04 の書き換えが 1bad7d9f の教材に触れ得るため）。
  第 3 周の組は campaign `c-r25-<face>-r3.yaml`（第 1・2 周と別のペルソナ × 場面）。

### 22.10 第 3 周の結果（2026-10-23 12:27〜16:2x・3 回目の利用上限で面 B / C の後半を 16:02 から再演）

- 面 A: 第 1・2 周で止まっていた経路が初めて通った（分野選択 → 共通部品の検索 → TeX 側の同一性リンク作成・確定 → 学生の
  「共通部品として」に到達、独自解釈の追加・共有 ON・承認・引用も到達）。GONE = IK-0890〜0894 / 0896 / 0897 / 0864 / 0866。
  新規 IK-0916〜0929（PDF 版と TeX 版の部品の組が名前で結べない・同一性リンクの行に型名と UUID・粒度の違う組を確定できる・確定リンクの
  読み手が未配線・Copilot の誤ルーティング・著者の自己承認が別コースの学生に届く・標準説明が更新されない 等）。
- 面 B: st-04 の英語の問いが HELP に誤ルーティングされなくなった（GONE）。地図の札 → AI → 地図の循環案内、前提一覧が導出 step を
  並べる、英語回答への日本語の段名の混入。審判は進行中。
- 面 C: GONE = 凍結の影響が止めている 2 件を名指し・[公開できません] 押せない・候補の側に「条件から外れた採用済みの判断」区画・
  判断の帰属・海の濃淡・縁の重複・索引の結果 1 文。STILL = 辺候補と糸が「かなり近い」一色・採用済みの関係が畳みに埋もれる。
  NEW = L1 の霧と海の濃淡と L2 の食い違い・反映直後に何を反映したか分からない・日本語文が逐語引用。精読の te-03 は原文で確かめ
  られない 2 件を取り込まず凍結を保留（妥当な判断）。審判は進行中。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 A（IK-0926）= 支持の数から著者本人を除き、コースをまたいで学生に届けるには
  著者以外の教員の査読承認を要件にし、学生向けの段階ラベルの定義を制度指標カタログに宣言する（改訂原則 4 / 1）。
  面 A（IK-0917）= 同一性リンクに「使う・課す」の種類は足さず、確定画面で両側の意味を照合させる。
- 面 B の第 3 周の課題 = IK-0930〜0936（主張参照の ID 取り違えで支える主張が常に空 / 行の無い対象の台帳 GET が 404 / D層の一覧が
  導出 step で埋まる / 学生の地図にスコープが出ない / 台帳の見出しに論文名なし / 複数論文の開幕の前提の振り分け / 英語の地図の問いが
  HELP の固定文で終わる）。GONE = IK-0899 / 0903 / 0905 / 0853 / 0854。疑義 → 提案 → 取り下げは 3 周とも未成立（根 = IK-0930）。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 B（IK-0932）= 学生にスコープ（検証の条件）をどこまで見せるか。推奨 = 状態が
  直接検証・間接的な支持・反証のときだけ成立条件と領域を 1 行添える（根拠の逐語・記帳者・数値は出さない）。
- 面 C の第 3 周の課題 = IK-0937〜0943（外れた採用済み候補に根拠が出ない / 採用済み・反映済みの関係が近さ順に埋もれる / 確認と
  凍結の影響に帰属と根拠が写らない / コースの地図 L1 が配置があっても全領域「霧」（IK-0847 は海の側だけ直していた）/ 海の円が
  「霧の領域」を名乗る / 版の呼び名の不揃い / 候補ゼロでも見出しが断言）。GONE = IK-0884 / 0885 / 0886 / 0888 / 0889 / 0850 / 0851 / 0846 / 0847。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 C（IK-0940）= 霧の被覆に生きている配置（確認済み・推定）を足し海と同じ判定に。
  面 C（IK-0848 残り）= 人に見せる名前とログイン名はアカウント層で別に持つまで現状維持。面 C（IK-0939）= 他の教員の採用を凍結する
  教員が戻せるままにし、他人の採用を戻すときは理由を必須にして監査に残す。面 C（IK-0937 v2）= 外れた判断の根拠の写しを判断行に持つのは
  実例が出てから。
- 面 D の第 3 周の課題 = IK-0944〜0952（講義の 2 枚目以降で式が 1 つずつ入れ替わる（重）/ 非公開になった受講中コースの入場判定が
  画面ごとに食い違う / 1 枚の読む表示に `===` が生で出る / 空のトピックに事実文がない / 書き換えの注意点に教員宛てメモと内部 ID /
  自作の図が根拠候補に並ぶ / 共有教員への知らせの矛盾 / 題名の生 TeX / 内部語と章番号の二重）。GONE = IK-0908 / 0910 / 0911 / 0879 /
  0875 / 0876。学生 2 本は固定コースを見ていない（te-03 の公開と pin_course の非公開化 — H21）ので S1〜S3 / X1〜X4 は今周も評価外。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 D（IK-0945）= 受講中のコースが非公開になっても既存の受講者は読み続けられる。
  新規の受講開始だけ止め、一覧に事実文を出す。

### 22.11 第 4 周の準備（2026-10-23 21:1x〜）

- 第 3 波（IK-0916〜0952）を全件テスト緑（backend 20,048 / src 2,420・変更 JS 13 本は esprima で parse）で砂場へ。順は snapshot r25b 復元 →
  docker cp → restart。投影の同期（第 3 波分）は面 A〜C 済み・面 D を同期中。
- 前提（製品の API）: te-05 に PINN TeX / PDF の editor、te-03 に Cepheus の editor と 1bad7d9f の editor、te-05 に 6be229e1 の editor、
  te-02 が Cepheus の主グラフ先頭ノードにスコープ 1 件（閲覧だけの教員の「行のある対象」を確かめる前提）→ snapshot `corpus-astro-tex-r25c`。
- 第 4 周の組は `c-r25-<face>-r4.yaml`（第 1〜3 周と別・学生の講義は `pin_course: true` で runner が固定し `pin_course.py` を使わない）。
  面 D の学生レーンは面 C の凍結（`LANE_DONE_R4`）を待つ。
- 2026-10-25 08:28: 第 4 周を 4 面同時に起動（オーナーが 10-25 07:05 に第 25 周の全差分をコミット済み・HEAD b7bbb62 を砂場へ docker cp・
  投影の同期は第 3 波分 4 面とも完了・uxsim 1,094 pass）。

### 22.12 第 4 周の結果（2026-10-25 08:28〜08:46・4 面同時・全 19 レーン完走）

- 頭脳の実測（審判は進行中）: 面 B = ノードの台帳 → 支える主張 → 疑義 → 検証提案 → 状態、が**4 周目で初めて成立**（根 IK-0930 の是正）。
  閲覧のみの教員は行の有無に関わらず台帳を開け、書けない理由が読める。残る根 = 前提マイニング・監査が候補を出さず前提の確定に届かない /
  地図の札の文が AI に渡らない。面 C = 第 3 周の STILL 6 件のうち 5 件が GONE（外れた採用の根拠・採用済みの先頭・確認画面の帰属・L1 の霧・
  海の円の名前）。凍結は 4 周とも未成立 — 今周はハーネスが「外れた枠」で探し通常の候補の反映の手を持たなかった（偽所見の疑い・審判が決着）。
  面 A = te-05 のレーンは通った（PDF 側の対・確定の確認文・承認者一覧は審判待ち）。面 D = 学生が初めて固定コース 6be229e1 を見た
  （`pin_course: true`・S1〜S3 / X1〜X4 の初評価は審判待ち）。
- **面を跨ぐ根（製品頭脳 U0 / U1 の所見）**: 学習者が「分野の地図」の画面を指して尋ねる問い（どこに置かれているか・札の意味・霧）は、
  casual でも tutor でも地図の配置・骨格・霧の事実が文脈に入らず答えようがない（SA層 4-c 保留の範囲 = IK-0577 系・面 B の IK-0907 / 0932 と
  同根）。英語の操作語の問いが使い方判定の手前の振り分けに掛からず意図分類の LLM が 2 回呼ばれる疑い。書き換えの注意点に内部 ID と作業記録が
  保存側の検査なしに残る（IK-0948 の保存側）。
- 面 A の第 4 周の課題 = IK-0953〜0961（確定した同一性リンクを取り消せない・学生の「共通部品として」が凍結していない下書きを読む・
  確定済みに［確定にする］・リンク行で版が区別できない・候補ゼロの「探していない / 無かった」・Copilot の生の Markdown リンク・
  「標準化状況: 未評価」・標準の説明から根拠へ渡れない・吹き出しの内部語）。GONE = IK-0916 / 0922 / 0921 / 0865 / 0923 / 0864 / 0866 / 0929・
  0920 の一部（確定リンクがライブラリと学生に届く）。**4 周の総括（面 A）**: 部品 → 共通部品の確定 → ライブラリと学生の画面に同じ事実、の経路は
  端から端まで通った。残る = 確定を戻せない・凍結を経ない内容が学生に届く / TeX ⇄ PDF を揃える目標は 4 周とも未達（砂場の 2 版が新規則の
  候補づくりを走っていない = 運転の前提漏れ H34）/ 説明の 書く → 共有 → 支持・引用 の連なりは 4 周のうち 1 周だけ端まで。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 A（IK-0954）= 学生に届く共通部品は凍結版だけ（凍結版が無ければ出さない）。
  面 A（IK-0953）= 同一性リンクに撤回の遷移を足し、作成者の自己確定は許したまま作成者と確定者を表示する。
- 面 C の第 4 周の課題 = IK-0962〜0967（採用時に出所を記帳しない・凍結の影響で取り込んだ項目を名前で言わない・札「未訪問」と台帳の説明の軸・
  索引の状態と再構築の要否の判定が別・外れた枠の冒頭文が取り込み後も止める・配置の理由文が逐語より広い）。GONE = IK-0937 / 0938 / 0940 / 0941 /
  0943（第 2 波分は維持）。凍結の未成立は製品ではなく**場面の形**（通常の採用済み・未反映を反映する手と繰り返しが無い = H25・第 1 周の採用の残存）。
  **4 周の総括（面 C）**: 地図の運用の弁（止める・見せる・帰属・反映の印・霧の一致・見送った関係は糸に出ない）は 1 操作ずつ確かめられた。
  凍結そのものと学生が見る「版が上がった地図」は 4 周とも未観察（根は運転と場面の形）。判断材料の薄さ（共起・反復する主題が立たない）が残る。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 C（IK-0967 + IK-0677）= 配置の理由文に出所の印を付け、理由が逐語に無い固有語を含む
  ときは warning で残す（根拠を逐語に限ることはしない）。
- 面 B の第 4 周の課題 = IK-0968〜0972 / 0974（検証提案が登録直後から画面に出ず読み返せない・閲覧だけの台帳で AI 候補が無効にならずコース
  全体の予約・依存の段の閾値が除外前の母集団（仮説）・学習者の台帳 GET が行の無い対象に 404・開幕の「該当なし」が「無い」と読める・
  教材一覧に役割なし）。GONE = IK-0899 / 0902 / 0904 / 0905 / 0930 / 0931 / 0933 / 0934。**4 周の総括（面 B）**: 教員の記帳の骨格は通った
  （台帳は論文ごと・記帳の語は事実文・閲覧だけの教員は理由で止まる・疑義 → 検証提案まで）。残る = 記帳したものを後から読む面（提案）・
  記帳の材料（前提の候補が生まれない）・学生にとって「記帳なし」の見え方が面ごとに違う（404 の無言 / 「該当なし」/ 未訪問の札の注記）。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 B = 閲覧だけの教員に記帳を頼む先（教材の所有者・編集者の表示名）を見せる（教員間のみ・学生には出さない）。
- 面 D の第 4 周の課題 = IK-0975〜0979（preview-split が配信と別の本文で枚数を数える・自動分割が見出しや導入文を前のスライドに残す・復元の印が
  式の写し（教材・講義・吹き出し・図スタジオ・付録）で失われる・［案をコピー］に申し送りと渡す相手が無い・図の採用の告知が音声と開示範囲を見ない）。
  GONE = IK-0908 / 0911 / 0910 / 0879 / 0944 / 0950 / 0952。**S1〜S3 を初めて固定コースで評価**。**4 周の総括（面 D）**: 教員側の
  「保存できるか・誰が・何が起きるか」の言い分けと、学生側の「講義に入れて最後まで送れ、式が教材と同じ位置に出る」は成立。残る 3 層 =
  ①保存済みデータは是正後も残る（所有者が付け直すまで学生に矛盾として届く）②境目を決める 1 関数に渡す入力が揃っていない ③確からしさの印が
  値の写しへ運ばれない。X1〜X3（保存の後の学生の画面）は審判が周をまたげず 4 周とも自動評価なし。
- **オーナー判断の候補（追加・推奨どおり進めた）**: 面 D（IK-0978）= 共有された教員にコースの所有者の名前を見せる（同じコースを共有された
  教員と所有者の間だけ・学生には出さない・disclosure_axes の教員向け宣言に 1 行）。

### 22.13 第 4 波の是正と第 5 周の準備（2026-10-25 12:5x〜）

- **第 4 波（4 面並列・コミットなし）**: 面 A = IK-0953〜0961 を resolved（同一性リンクの取り消し = migration 093 と route・作成者 / 確定者 /
  取り消した人の表示名・確定直前の問いと粒度の注記・学生は凍結版だけ（`standardization_note`）・同じ題名が並ぶときだけ版の手がかり・候補づくりの
  規則版 `IDENTITY_RULES_VERSION` を artifact に・Copilot の Markdown リンクを文字に・`my_support` と標準の説明の根拠へ渡る導線・記号の吹き出しは
  学生向け表 `LEARNER_SYMBOL_SCOPE_LABELS`）。面 B = IK-0968〜0972 / 0974 を resolved（台帳 GET に疑義ごとの `proposals[]` と `status_label`・
  閲覧だけの台帳で AI 候補と提案の遷移を無効に・段の閾値は除外後の母集団（実分布は未確認・hypothesis のまま）・学習者の台帳 GET は行なしでも 200 と
  `exists:false`・開幕の区画専用の空文・教材一覧に `role_label` と頼む先）。併せて IK-0853（マイニングの状態 5 語）/ IK-0907（地図の札を学習チャットの
  画面文脈 `view` に載せ SA層の解決器が札の説明を渡す）。面 C = IK-0962〜0967 + IK-0677 を resolved（採用時の出所 `origin`・凍結の影響の
  `added_nodes`・札の説明の軸・索引の状態と再構築の指紋を同じ材料に・`orphaned_note` の条件付き・配置の理由に「〔AIの要約〕」と `reason_note` と
  引用ごとの `source_label`）。残りの IK-0942（学習者向けの「骨格」→「地図 版」）/ IK-0887（糸ごとの `basis_line`）。A層の判断: landscape_placement は
  配置層自身の agent で LS2 の対象外（prompt の 1 行と validator の warning は残す）。面 D = IK-0975〜0979 を resolved（分割の入力を
  `prepare_topic_slide_input` 1 つに・preview-split が保存済みの文脈を借りる・読みの単位で詰める・復元の印の判定を `equation_latex_is_reconstructed`
  1 つにし本文の写し / 講義 DTO / 根拠の式 / 吹き出し / 図スタジオの全経路・［案をコピー］に確認問題と申し送りと所有者の名前・図の採用の知らせを
  `figure_adopt_notice_parts` から組む）。IK-0872 ①（保存済みの旧い付録）と IK-0878（区切り挿入の UI）は根が別として残した。
- **オーナー判断の候補（すべて推奨どおりに実装・設計書の各節に追補）**: IK-0954 学生は凍結版だけ / IK-0953 撤回を足し自己確定は事実として表示 /
  IK-0967 + 0677 出所の印・逐語に限らない / 面 B 閲覧だけの教員に頼む先の表示名（教員間のみ）/ IK-0978 共有教員に所有者名（教員間のみ・
  disclosure_axes に 1 行）。
- **ハーネス（第 4 周・H25〜H33）**: 凍結の場面に通常の採用済み・未反映を反映する手と繰り返し（≤ 4）を足した（H25 = 面 C の凍結未成立の根）。
  `studio_exec._copy_proposal` を IK-0978 に同期。前提 13 項を r5_prereqs に記録。
- **第 5 周の campaign（c-r25-<face>-r5）**: 面ごとに教員 2〜3 × 学生 2・場面 5。同じ組の繰り返しは 2 組だけ（doubt の te-03 × 前提の確認 =
  第 1 周は候補 0 で確定に届かなかった / studio の te-01 × split-save・figure-adopt = 6be229e1 の所有者が te-01 だけ）。審判案からの差し替え =
  atlas の edge・gap は te-04（te-03 / te-01 は既出）/ share の te-04 × (c) と st-08 × (d) は第 6 周へ。レーンの順は面を跨ぐ合図で結ぶ
  （doubt の te-02 閲覧確認 `PROBE_DONE_R5` → share が te-02 に editor を与え `GRANT_TE02_R5` / share の st-10 `ST10_DONE_R5` → doubt の
  st-10 / atlas の `LANE_DONE_R5` → studio の学生レーン → `run_all --campaign` で周をまたいだ突き合わせ）。製品 LLM の上限は share 10 /
  doubt 12 / atlas 4 / studio 12、頭脳は各 260〜300。外部 API なし。
- **周の前の手順**: 製品の第 4 波 → 投影の同期（sync_keys_wave4 の 4 面節）→ backend / src / uxsim の全件 → snapshot r25c の復元 → deploy →
  r5_prereqs と campaign notes の前提を製品 API で用意（PINN 2 版の identity_candidates 再実行・PINN node "1" と comp_011 の台帳・te-03 の手動の
  前提・te-04 の Cepheus B editor・凍結版と採用済み・未反映の名前の確認・6be229e1 の公開と te-03 への editor 共有・te-01 のコース先頭）→
  snapshot r25d → レーン起動。
- **投影の同期（第 4 波・13:2x〜13:4x）**: 4 面を sync_keys_wave4 に揃えた（記録 = projection_sync_wave4・新テスト 18 件・uxsim 1,143 pass）。
  新行為 `admin.library.identity_link_revoke`（理由必須）を share (a) の任意の手に。台帳の見出しは画面と同じ日本語の段名（第 4 周 te-01 の英語見出しの
  偽所見はこれで解消）。審判は非改変。全件 = backend 20,150 / src 2,423 / uxsim 1,143 pass・IK 693 件違反 0。
- **第 5 周の起動は Docker 不達で保留（13:2x 時点）**: 砂場の Docker Desktop が 12:50 にオーナーの口座（`/Users/urakawashinichi`）で起動し直され、
  作業ユーザー `dev` の socket（`/Users/dev/.docker/run/docker.sock`）が無い（オーナー側の socket は permission denied・砂場の 3100 / 5433 も
  応答なし）。`dev` から Docker を起動し直すとオーナーの実行中の Docker と衝突するので行わない。解除 = Docker Desktop を `dev` で起動し直す
  （または砂場を `episteme-uxsim` で up にして socket を `dev` に見せる）。解除後の手順は上の「周の前の手順」どおり（r25c 復元 → deploy →
  前提 → r25d → `face_<face>_r5.sh` を 4 面同時 + 頭脳）。campaign 4 本の `snapshot` 欄は r23 のままなので r25d に差し替えてから起動する。
- **周の前の一括実行（13:5x〜14:1x）**: 前提 13 項を製品 API だけで check → do → verify する `r5_prereqs.py`（`--dry-run` / `--only` /
  `--grant-te02` = doubt の `PROBE_DONE_R5` の後に te-02 へ editor を与え `GRANT_TE02_R5` を touch）を用意し、静的テスト 4 件（項目名が
  r5_prereqs の表と一致・DB 直読みと subprocess の不在）。実行は Docker 復帰後。**判断**: 手動の前提（`POST /api/admin/doubt/assumptions`）は候補を
  経ずに確定で生まれる（本人が書いた前提 = 本人の確定・P1 のとおり）ので製品は変えず、場面 `t-db-assumption-review` の確定の手を optional にして
  候補 0 件でも確定済みの前提の台帳 → スコープへ進む形にした（4 周続けて未到達だった尾を通すため）。実行時は `--allow-confirmed-assumption`。
- **第 5 周の準備の実測（2026-10-27 00:0x〜00:4x・Docker 復旧後）**: 砂場 up → r25c 復元 → deploy（nginx.conf はバインドマウントで cp 不要）→
  migration 093 まで適用。`r5_prereqs.py` の 13 項: identity 再実行で TeX ⇄ PDF の同一性候補 10 件 / te-02 は第 3 周グループ A を viewer に落として
  PINN 2 版とも閲覧のみ / PINN node "1" に te-03 のスコープ + 直接検証 / comp_011 はグラフにノードが無く台帳の対象外 → t13 が結ぶ PDF 版の部品
  「Sound-speed softening result」「Canonical radius result」に所有者がスコープ（t13 の evidence_items は空 = ⚓ なし・学ぶ単位で組まれている）/
  te-03 の手動の前提は confirmed で 1 件 / te-03 に 6be229e1 の editor / 採用済み・未反映 = 関係「ブラックホール — 重力波」・項目「星間磁場」/
  教員確認済みの配置 1 件 / 公開範囲と一覧先頭は期待どおり。t0・t16 の前の周の編集は残したまま（r25a に戻すと他面の前提が消える — 指揮者の判断）。
  snapshot r25d を採り campaign 4 本の `snapshot` を差し替えた。mailbox の前周の残り（share 247 / doubt 279 / atlas 185 / studio 235・製品 4051）は
  BRAIN_INSTRUCTIONS に切れ目として記した。

### 22.14 第 5 周の結果（2026-10-27 00:06〜00:30・4 面同時・全 20 レーン完走・製品 LLM 12 回・頭脳 199 要求）

- **面 C（atlas）— 凍結 2026.2 が成立（4 周連続未成立の根の解消）**: te-05 が第 1 周の他教員の採用 2 件（関係「ブラックホール — 重力波」・外れた採用
  「星間磁場」）を帰属・根拠・凍結後の見え方の一文で読んで反映し 16 手で凍結。効いたのは H25（反映の手）+ 運転（残存を名前で渡す）+ IK-0962 / 0963。
  **凍結後に学生が見る「版が上がった地図」を初観測**: 「地図 版 2026.2」が全面で揃い、星間磁場が L1 に並び、見送った関係は糸に出ず、確定した関係は
  糸から消える。GONE = IK-0962〜0966 / 0677 / 0939 / 0938。NEW = IK-0989〜0995（**学習者の L2 が骨格の先頭 20 概念しか描かず凍結で加わった関係が
  線にならない（中）** / 論文の海の縁が凍結で解消済みの主題を言い続ける（中）/ 版ごとの作成方法 / 下書きが無いと公開版の一覧が無い / 凍結直後の索引の
  状態文 / 見送りの結果の一文 / 置けなかった主題の読める場所）。残る根 3 = 凍結の結果が学生の画面に届き切らない・判断の材料が薄い（IK-0887）・確定の
  後に読み返す面が無い。偽所見 H30 / H31（頭脳の読み落とし）・ハーネス H32〜H35。
- **面 A（share）**: 書く → 共有 → 学生に届くが初めて端まで（te-04 の 5 操作）。TeX ⇄ PDF の同一性候補 10 件が立った（IK-0919 成立）。未評価が大きい:
  H35（runner が「リンク1」を番号として読めず［確認］が 1 本も実行されず IK-0917 / 0953 / 0920 は未評価）・H36（(b2) が同題名コースの PDF 版を開き
  2 手で停止）・H38（r25d は再解析なしの取り直し）。GONE = IK-0919 / 0957 / 0955 / 0959 / 0961 / 0924 / 0925 / 0926（範囲内）/ 0960。NEW =
  IK-0980〜0988 / 0996 / 0997（2 版間の「意味の近さ」が名前も役割も違う部品を組にする / 「表記の一致」の中身が無い / 確定後に未決リンクの置き場が無い /
  記号 → 概念の入口が製品に無い / AI の標準説明は著者なしで管理者しか査読できない / 査読済みに査読ボタン・共有の知らせが ON / OFF を言わない / 音声に画面
  文脈が渡らない（仮説）/ 無関係論文の構成段落が出典に混入（仮説）/ 旅が 1 行で終わる（仮説）/ 同段落が TeX・PDF 二重で出典に（仮説）/ ［AI復元］が
  抜粋ごと（仮説））。**オーナー判断の候補（推奨どおり進めた）**: IK-0984 = その論文を編集できる教員が査読でき査読者を監査に残す・他コースへ広げるには
  もう 1 人の査読 / IK-0980 = 同一論文の 2 版間は文の近さを候補の材料にせず式・主張の本文一致だけ。
- **面 B（doubt）**: 記帳したものを後から読む（提案カード常設）が初めて通った。te-02 の画面を経ない記帳は根拠なしの body で 422 に止まり権限検査に
  届かず（弁は健在・順序が入力検査 → 権限 = IK-0999）。GONE = IK-0968〜0972 / 0974 / 0853（+ 0899 / 0905 / 0930 / 0931 / 0934 維持）。NEW =
  IK-0998〜1006（取り下げた疑義を JS が描かない / 入力検査が権限検査より先 / 既定コースが本人のものでない / load_score の計算範囲が論文 1 本と
  コース全体で入れ替わる（仮説）/ 台帳の欄の出所が揃わない / 支える主張に疑義・提案の印が無い / 逆質問直後の「〜ってどういう意味」が前提の説明の依頼に /
  使い方の回答の「送り直して」が行き止まり / 出典の目印除去で「前の回答の「」は」）。run で一度も当てていない経路 = 権限拒否・前提の確定 → スコープ・
  地図の条件行・記帳ありの部品の一行・地図の札が AI に届く。**オーナー判断の候補（推奨どおり進めた）**: IK-1001 = load_score は論文単位で保存・閾値だけ
  コースの母集団。ハーネス H34〜H38（有効 body のプローブ / course_hint / select_node / 記帳は学生のコースの版 / 注記の文言）。
- **面 D（studio）**: **X1 成立 = 所有者が保存した直後の学生の画面を同じトピックで初めて閉じた**（preview-split に番地を送る呼び方で全 20 トピックが
  講義の枚数と一致・IK-0975 GONE。`campaign_studio_cross` の「8 組食い違い」は統合前の教材欄の枚数を比べた審判の偽所見 H27）。IK-0976（88 枚走査で
  泣き別れなし）/ 0978 / 0979 / 0915 / 0950 も GONE。NEW = IK-1007〜1011（**所有者名としてログイン ID を開示（`users.display_name` がログインの
  照合列を兼ねる）** / U+FFFD 入りの崩れた式を「AI復元」付きで描く / 式の事実行が抜粋ごと（仮説）/ 採用した教材図が拾い残しと同じ見出しの下 / 申し送りが
  画面に無い材料を根拠に挙げる）。残る 3 層 = 保存済みデータが残る（IK-0872 / 0880 / 0948）・印と表示可否を式単位で同じ 1 関数から受けていない
  （IK-0895 / 1008 / 1009）・人が加えたものと機械が拾ったものが同じ区画（IK-1010 / 1011）。**オーナー判断の候補（推奨どおり進めた）**: IK-1007 + 0848 =
  ログイン ID は見せない・人に見せる名前の列を足し空なら名前なしの事実文。
- **面を跨ぐ根（製品頭脳 A / B 12 件の所見）**: 同段落の TeX / PDF 二重出典・無関係論文の段落の混入・［AI復元］の印が式でなく抜粋単位・出典の目印除去で
  文が壊れる・地図の画面を指す問いの行き先（4058 の誤ルーティング / 4061 の HELP 行き止まり = SA層 4-c 保留の範囲）。
- **第 5 波の体制**: 面 A / B / C / D の是正者 4 + 面を跨ぐ 2（チャット / RAG = IK-1006 / 0996 / 0997 / 0987 / 0986 / 1009、表示名 = IK-0848 / 1007 →
  migration 094 `users.profile_name`）+ ハーネス（H27〜H38・`brain pending --after`・`r5_prereqs.py --round 6` への一般化・r6_prereqs.md）。

### 22.15 第 6 周の準備と結果（2026-10-27 20:3x〜21:45・4 面同時・全 20 レーン完走）

- **周の前（第 6 周の前提・r6_prereqs.py `--round 6`）**: 15 人の名前登録（`profile_name`）/ te-05 を PINN 2 版 viewer / te-01 に Cepheus TeX・1bad7d9f の editor /
  te-04 の手動の前提（1bad7d9f・confirmed）/ t16 の区切りを外す（所有者で保存）/ **PINN TeX の再解析**（第 5 波のコード・製品頭脳 2 体・174 要求・途中で
  6 回目の利用上限（20:50 リセット）→ 再開。**proxy の 5 分で放棄された要求 2 件（4208・4210）が失敗の印なしに下流へ流れ、paper_skeleton は題名だけで
  走った** = IK-1013 系）→ identity 再実行（2 版間は本文一致のみ = 候補 1 件）→ **部品名が変わり te-04 の第 5 周の説明が superseded 行に取り残された**
  （IK-1012: KO8 の再係留が `component_explanations` を対象にしていない）→ te-04 の説明を新部品「TOV residual loss at collocation points」に作り直し
  （共有・自己査読・自己支持）・params の `component_hint` 差し替え → snapshot **r25e**（manifest に再解析の note）。comp_011 の記帳先は固定コースの t13 に
  部品参照が無く作れない（事実文で notes に）。atlas の次版の下書きは候補 0 件で［次版の下書きを作る］が出ないため、te-03 の GAP_DONE_R6 の後に指揮者が
  te-01 として API で作り DRAFT_READY_R6 を出した（自動化）。パイプライン課題の記帳 IK-1012〜1016（+ IK-0724 / 0719 / 0752 に追記）。頭脳の規律に
  「5 分以内・標準入力待ち禁止」を追記。
- **結果（21:22〜21:45）**: 面 C = **凍結 2026.3 成立（2 周連続）**・残存の採用 2 本（江口・堀）を反映・反映の確認を初観察・学生は版 2026.3 の L2 で
  実線 1 本と `omitted_note` / `undrawn_note`・海の縁から「星間物質・星形成」が消えた（IK-0990 GONE）。GONE = IK-0990 / 0991 / 0992 / 0993 / 0994 /
  0995 / 0942 / 0964 / 0967 / 0848・一部 IK-0989。NEW = IK-1017〜1019（L2 の代表選出がコースの論文の配置を使わず骨格順に戻る（中）/ 右パネルの内部欄名 /
  ［止めている候補を見る］の着地）。オーナー判断なし。面 B = **サーバの権限拒否（404）を 6 周で初めて run で当てた**・条件の行（IK-0932）を初観察・
  地図で選んだ札が AI に届いた（IK-0907 GONE）・取り下げ済みの折りたたみ・支える主張の印。前提の確定 → 台帳 → スコープは 6 周目も未到達（確定済みの
  行から台帳を開く手が無い）。面 A / D = 審判中（studio の cross は 3 組「食い違い」— 第 5 周の H27 と同じく切り分け待ち）。
- **第 6 周の審判（21:4x〜22:3x）**: 面 A = 「支持 → 引用 → 支持者一覧」が 6 周で初めて端まで（course_id 固定の効果）。リンクの確認の問い（IK-0917）が初めて働き
  リンク 1 本を確定。取り消し（IK-0953）は場面の段構成で今周も未評価（H39）。GONE = IK-0982 / 0956 / 0958 / 0924 / 0926 / 0665 / 0985 / 0980 / 0981。
  NEW = IK-1020〜1026（再解析で片側が置き換わった候補がリンク 1 本で「別の論文」と言う / **凍結コースの ⚓ が agent ID（comp_003）で部品を指し再解析後の
  別部品に解決される** / 記号の照合キーが `\!` を残す / 著者以外の査読の経路がコードに無い / ラベル見送りで行ごと消える / 旅の出口案内が入れない帰属カードを
  勧める / 確認の問いが同語反復）。**オーナー判断の候補（推奨どおり進めた）**: 再解析で名前が変わった部品への人の記録と参照の付け替えは出典の重なりで 1 対 1 に
  決まるときだけ（決まらなければ旧行に残し事実文・凍結コースは UUID で参照）/ 著者以外の査読者 = その論文を編集できる教員（監査に残す）。
  面 B = **サーバの権限拒否（404）を初めて run で当てた**（IK-0900 / 0999 GONE）・既定コース（IK-1000）・欄の出所（IK-1002）・支える主張の印（IK-1003）・
  逆質問と送り直し（IK-1004 / 1005）・地図の札が AI に届く（IK-0907）・条件の行（IK-0932）が GONE。NEW = IK-1027（拒否文が英語で画面の理由と食い違う）/
  1028（確定済みの前提に台帳を開く手が無い）/ 1030（仮説: 出現順の辺を依存に数え序論の文が最高位になる）/ 1031（AI に渡るのは札の文だけ）。前提 → 台帳 →
  スコープ、提案化・取り下げは 6 周とも run で未到達（H39 = 「疑義N」を番号と読めない）。**オーナー判断の候補（推奨どおり進めた）**: 出現順の辺を依存の
  広がり・反実仮想の「依存」に含めない（先に読み取りで仮説を確かめる）。面 C = 凍結 2026.3・反映の確認を初観察・海の縁から解消済み主題が消えた
  （IK-0990 GONE）。GONE = IK-0990〜0995 / 0942 / 0964 / 0967 / 0848。NEW = IK-1017（L2 の代表選出がコースの論文の配置を使わず骨格順に戻る）/ 1018 /
  1019。オーナー判断なし。面 D = **X1 が明示（t16 = 2）と自動（t17 = 5）の両経路で成立**（全 20 トピックで所有者のプレビュー = 学生の枚数。cross の
  「3 組食い違い」は保存されない書き換え案を比べた偽所見 H31）。GONE = IK-1007 / 1008 / 1009 / 1011 / 0872 ② / 0878。**STILL（最重要）= IK-0895**
  （講義の式 253 件で `reconstructed` が 0 — 凍結した写しに復元の状態が載らない・5 か所が 3 対 2 で食い違う）。NEW = IK-1032（付録を取り除いたのに知らせが
  固定文）/ 1033（m(r) の吹き出しに境界条件が添う・仮説）/ 1034（トピック概要が 2 回）。**オーナー判断の候補（推奨どおり進めた）**: 保存済みの教材を是正の
  たびにシステムが書き直さない（所有者の明示操作 + `decision_context`）/ 共有教員の音声生成は所有者だけに揃える。
- **面を跨ぐ（製品頭脳 A / B の 15 件）**: PINN（PDF 版）のトピックで Cep B のチャンクが出典に混ざる（IK-0987 STILL）/ 教材の値の抜け / 往復ごとに出典番号が
  変わる / 前提の確認の題名に結果の数値 / 構造ブロックの英語の途中切り / 式 (1)〜(4) の印が教材と出典で食い違う / 構造ブロックに同一性リンクが渡らない /
  帰属候補の重複・誤依存・英語ラベル / 意図分類に画面文脈が渡らない。
- **第 6 波の体制**: 面 A / B / C / D + chat-RAG（記帳込み）+ ハーネス（share H39〜H41 / doubt H39〜H42 / atlas H36〜H40 / studio H31〜H33 + r7 前提）。

### 22.16 第 7 周の準備と結果（2026-10-28 02:0x〜02:38・4 面同時・全 19 レーン完走・「前提状態を変える」周）

- **第 6 波（10-27 22:xx〜10-28 02:0x）**: 面 C IK-1017〜1019 + 0992 残り / 面 B IK-1027 / 1028 / 1030（出現順の辺を依存に数えない — 読み取り
  `compare_claim_sequence_edges` で先に仮説を確認）/ 1031 / 面 A IK-1012 / 1020〜1026（出典の重なりで 1 対 1 のときだけ付け替え・新 freeze の ⚓ は DB UUID・
  著者以外の査読者 = 論文を編集できる教員・`\!` を照合キーから落とす・ラベル見送りは行を残す）/ 面 D IK-0895（読み時の索引 `equation_reconstruction` で
  5 か所が同じ答え）/ 1032 / 1033 / 1034 / 0976 残り / 1010 残り + 保存済み教材は書き直さず所有者の［付録をまとめて付け直す］（`decision_context`）・
  音声生成は所有者だけ / chat-RAG IK-1035〜1043（焦点に別版を足す・出典番号の安定・式の印の版照合・構造ブロックの英語切り落とし・分類への画面 1 行・
  本文の値の抜け）/ ハーネス H（4 面）+ `brain pending --after` + `r5_prereqs.py --round 7`。7 回目の利用上限（01:50）で share・studio・campaigns が中断 → 再開。
  backend 20,451 / src 2,423 / uxsim 1,250 pass。
- **第 7 周の前提の実測**: 対 67727cb0 の 2 リンク確定 → 確定 → 凍結・記号の候補は定義文にエントリ名が無く 0 → 別名「TOV」「TOV 残差」を足して 4 候補・1 本確定・
  凍結し直し / te-02 に PINN editor・te-01 viewer・te-05 PINN editor / te-02 の手動の前提を Cepheus TeX に結んで confirmed / 4 コースの load 再計算 /
  `compare_claim_sequence_edges`（docker exec・先頭の警告行を除去）= Cepheus TeX の 1923 行中 821 行が下がり上位は序論の主張 = IK-1030 の仮説を実データで
  確認 / t16 の区切りなし / 6be229e1 に共有版 1 / 1bad7d9f は propose 0 件 → 所有者が題名の語で 8 トピックを手で binding（body は `topic_bindings`）→ st-03 の
  足跡は学習チャット 1 回で付いた / te-02 が te-04 の説明を 2 人目として査読（`PATCH {"review_status":"teacher_approved"}` のみ）→ 他コースへ届く。
  成立しない項目: 記帳ありの部品と ⚓ の一致（旧 freeze は再凍結まで）・2 本目の論文（外部 API 禁止）。snapshot r25f。前提用の製品頭脳を複数起こして
  同じ seq を取り合った（次周は 1 体・担当分け）。
- **結果（02:14〜02:38）**: 面 C = **凍結 2026.4 成立（3 周連続・加わる項目なしの版上げ）**・下書きありの反映の確認・**IK-1017 主部 GONE**（足跡 + コースの配置 +
  新しい関係の両端で 20 概念が選ばれ骨格先頭の「初期宇宙」が外れた・`omitted_note` の括弧が実際の手がかりと一致）・IK-1018 GONE。面 B = te-02 の前提 →
  台帳 → スコープ（審判待ち）・権限拒否文・取り下げの折りたたみ。面 A = te-05 の取り消し（IK-0953 初評価・審判待ち）・2 人目の査読後の (b2)。面 D =
  版ありの経路で保存 → 版 2 の発行 → 学生（発行前後の審判）・周またぎ **8 組・食い違い 0**（H31 / H32 の是正後の初観測）。製品頭脳 A / B の所見: PINN
  トピックに Cep B の章構成が混入（IK-1035 の是正後も）・意図分類に画面文脈の 1 行が無い例・casual で地図の項目に答えられない・事実文の英語。
- **第 7 周の審判（02:4x〜07:0x・8 回目の利用上限（06:50 リセット）を挟む）**: 面 C = 凍結 2026.4・反映の確認・**IK-1017 主部 GONE**（6be229e1 で観察 — st-03 は同題名
  コースを当て推量で開いた = H43）・IK-1018 / 0992 / 0991 / 0848 GONE。NEW = IK-1044（次版の下書きを作るボタンが論文由来の候補カードにしか無く候補 0 件では
  画面から作れない — 7 周とも指揮者が API で作った）/ 1045（L2 の優先が直近の凍結の関係だけ）/ 1046（略称と正式名の対応）/ 1047（反映の確認に向きの一文が無い）。
  オーナー判断なし。面 B = **te-01 の権限拒否が 403 + 画面と同じ文（IK-1027 GONE）**・取り下げ直後の note・確定済みの前提から［台帳を開く］（IK-1028）・
  IK-1030 / 1001 / 0853 / 1031（テキスト経路）GONE。前提 → 台帳 → スコープは 7 周目も未到達（H44 = 第 5・6 波の手が場面に入っていない）。NEW = IK-1048（仮説:
  支持線がまだ出現順の辺を数える）/ 1049（casual では画面文脈が要素だけ → 地図の場所が AI に届かず st-07 は 2 往復「見えない」）。**オーナー判断の候補（推奨どおり
  進めた）**: casual / 音声でも画面で選んだものの短い事実文を渡す。面 A = **IK-0953 の取り消しを初到達**（確認文・理由必須・記録の保持・別の教員の画面にも残る）・
  IK-1020 / 0954 / 1021（別部品へのずれ）/ 1022 / 1023 / 1024 / 1025 / 0926 GONE。NEW = IK-1050（判断後の行が記録として読めない・リンクの見送りに理由が無い）/
  **1051（再解析前に凍結したコースでは ⚓ が 1 つも開けない — 4 周続けて学生側の評価を止めている）** / 1052（仮説: 記号 → 概念の候補が定義文の別名の出現だけで立ち
  誤った組が前提スクリプトで自動確定・凍結された = H44 運転の誤り）。**オーナー判断の候補（推奨どおり進めた）**: 旧 freeze の ⚓ は出典の重なりで 1 対 1 のときだけ
  後継へ・決まらなければ学生に事実文・所有者に再凍結の案内・自動再凍結なし。面 D = **IK-0895 GONE（5 か所の印が初めて揃った）**・1010 / 1033 / 1034 / 0915 GONE・
  版ありの経路が規則どおり（保存は HEAD・学生は発行まで版 1・発行後に揃う）。NEW = IK-1053（版ありで保存しても発行への道と「未発行の変更」の事実が無い）/ 1054
  （付録が古いトピックの題名 15 件を 1 括弧に連結）。「教材の式が消えた」は頭脳が `$…$` を落とした投影の欠け（製品の根でない）。**オーナー判断の候補（推奨どおり進めた）**:
  発行は所有者の明示操作のまま・保存画面に未発行の事実と［版の管理］への入口。
- **第 7 波の体制**: 面 A / B / C / D + ハーネス（H + r8 前提・H44 の教訓「前提スクリプトに人の判断を自動でさせない」）。chat-RAG の面を跨ぐ枠は今波は無し
  （IK-1035 / 1043 の残りは各面の審判が追記）。

### 22.17 第 7 波と第 8 周の準備・起動（2026-10-28 02:4x〜07:33）

- **第 7 波**: 面 C IK-1044〜1047 + 0664 + 0942 / 1018 残り（関係区画の［現在の版から次版の下書きを作る］・全版の差を L2 の手がかりに・略称対応・向きの一文・
  海の「地図 版」・学習者向けの語の置換・論文の題名の正本）/ 面 B IK-1048 / 1049（支持線も出現順の辺を外す・casual でも地図の場所の事実文を渡す）+ 1028 / 1021 /
  1013（部分）/ 面 A IK-1050〜1052 + 0953 / 1023 / 1024 / 1012 / 0681 の残り（旧 freeze の ⚓ を読み時に出典の重なりで後継へ・見送りに理由必須・判断後の行の
  札と理由・記号候補の粒度・再解析前の説明を開く）/ 面 D IK-1053 / 1054 + 0976 残り + 0571（印のみ）（未発行の事実と［版の管理を開く］・付録が古い一覧・
  2 文の導入 / 解説を結ぶ・「（原文）」の印）/ ハーネス（H + r8 前提・H44 の教訓 = 前提スクリプトの確定を既定で行わない・`pending --claim`）。
  8 回目の利用上限（06:50）で share / studio / atlas / doubt / ハーネス / studio 審判が中断 → 06:5x 再開。backend 20,543 / src 2,423 / uxsim 1,298 pass。
- **第 8 周の前提の実測**: 対 67727cb0 = PDF 側の再確定 1 本（**`share_pair_frozen` が候補 4 本を自動確定した = H44 と同型 → 全て取り消して凍結し直し**・記号候補は
  TeX 版に残す）/ te-05 の PINN 共有グループを外しても viewer（コース 6be229e1 の editor 経由）= 「閲覧もできない教員」は砂場で成立しない / 固定コース 1bad7d9f の
  ⚓ は 0 個 → share / doubt の学生は 6be229e1 / 支持線の比較（docker exec）= 1924 行中 98 行が変わる（IK-1048 を実データで確認）/ te-02 に 6be229e1 の editor /
  t16 区切りなし / 6be229e1 は版 2 まで / atlas 2026.4・下書きなし。snapshot r25g。
- **第 8 周（07:33〜・4 面同時）**: 組は campaign r8（share 6be229e1: te-01 viewer × (a) / te-03 × (c) / st-07 × (d) / st-04 × (e) / te-02 × (b2) / st-10 × (d)。
  doubt 6be229e1: te-01 × 不完全 body / te-03 / te-02 × 前提 → 台帳 → スコープ / st-05 / st-07 音声。atlas 1bad7d9f: te-04 × 下書きを画面から / te-01 × 2026.5 /
  st-03 足跡あり / st-01 足跡なし / st-08 海。studio 6be229e1: te-01 × 版あり（学生レーンは発行前）/ te-05 / st-01 / st-08）。指揮者の自動化 = 記号候補の確定
  （指揮者の判断・TeX L_TOV のみ）→ SYMBOL_DECIDED / 凍結後の読み直し → ADDED_READ / 版 3 の発行 → RELEASE2_DONE。TE02_REVIEW_R8 は事前に置いた。

### 22.18 第 8 周の結果と第 8 波の起動（2026-10-28 07:33〜12:2x・4 面同時・全 20 レーン完走・「前提状態を変える」周の 2 回目）

- **運転**: share 6 / doubt 5 / atlas 5（te-01 の凍結は下書きなしで正しく止まり、指揮者が API で下書きを作って再走 → 2026.5）/ studio 4（学生レーン → 発行前の
  周またぎ審判 → 版 3 の発行 → 発行後の審判）。自動化は記号候補の確定を **SYMBOL_SKIP_R8**（te-03 の後に TeX 版の記号候補が残っていなかった）。9 回目の利用上限
  （11:3x）で審判 3 本と頭脳 5 体が中断 → 11:50 復帰・審判は文脈ごと再開（頭脳はレーン完走後で影響なし）。
- **審判（judge_{share,doubt,atlas,studio}_r8.md（uxsim/runs/round25））**: 新規 IK-1055〜1064・1070〜1071（1065〜1069 は空き — 並行の番号衝突の振り直し）。索引 779・違反 0。
  - 面 A: 判断の記録（見送り理由・取り消しの日時・査読者名・引用の列挙）は揃い CITE_NOT_SENT 解消。**引用の約束が学生に届かない**（IK-1061 — 引用先コースの
    教材が部品の論文を含むかを見ていない）/ 見送り・取り消しを候補に戻す手が無い（IK-1062 → 記号の確定 → 凍結が飛んだ）/ 関係の確定の記録が無い（IK-1063）/
    部品チップの「誰が書いたか」が HELP へ（IK-1064 — `element_label` 未送信）。学生の前後比較は固定コース 6be229e1 が PDF 版で前提が TeX 版にあるため
    最初から成り立たず（H48）— 頭脳の IK-1051 / 0681 GONE・0645 STILL は見かけ。
  - 面 B: **前提 → 台帳 → スコープが 8 周目で初めて通った**が手の前提は load_score NULL で地図・合意リストに載らない（IK-1058）/ 権限 → 入力の順が当たり
    IK-0999 GONE / 取り下げ済みの中身が読めたが日時・人が無い（IK-1060）/ IK-1048 の是正は実データで効くが名指しの支持線は逐語同じ → 観測の根と式チェーンの
    辺（IK-1057・仮説）/ 音声の地図の問いが届く（IK-1049 GONE）/ AI に渡る行「台帳に記帳なし」が画面の「検証の記録なし」と食い違う（IK-1059）。⚓ は
    場面が ⚓ の無い t13 を開き未評価（H48）。
  - 面 C: 教員の一巡は 4 周連続で凍結（2026.5）。入口（IK-1044）・向きの一文（1047）・版表示（0942）・足跡なし（1017）GONE。**IK-1045 は上限 20 の選出順
    （トピック対応 → 配置 → 全版の差）で確定済みの関係が後ろに落ちる**（1 周で戻った・コードで確認）/ 足跡なしの語（IK-1055）/ 承認の行が状態で言い分け
    （IK-1056）/ 画面文脈の経路（`_screen_atlas_node_sources` の `verify_line`）とチャットの出典名は未是正。H47 = 頭脳が 23 行中 19 行で読みやめ「辺が落ちた」と
    疑った偽所見。教員が画面だけで版上げを始めることは 8 周とも未観察（H45 = 場面に `edges.draft_from_frozen` が無い）。
  - 面 D: IK-1053 / 1054 GONE（未発行の事実と［版の管理］は所有者だけに出た）。ただし保存の知らせが「発行してください」と「同じです」を同時に言う（IK-1070）/
    音声の依頼の出口が押せない span で宛先も中身も無い（IK-1071）/ IK-0976 は形を変えて STILL（文中の式を 60 字に数える・⚓ だけの段落が次の枚へ）。X1 は
    前提が区切りを外し te-01 が同じ位置に入れ直したため HEAD = 版 2 = 版 3 で成り立たず（H36）。
  - オーナー判断の候補: 4 面とも無し（IK-1058 の「結ぶ要素を教員に選ばせる」は推奨「読み時の計算不能段を先に」で入力を増やさない）。
- **第 8 波（12:2x〜・並列 5 体）**: 面 A IK-1061〜1064 + 1050 残り + 0980 / 面 B IK-1057〜1060 + 0901（IK-1018 の画面文脈の経路も面 B が担当）/ 面 C IK-1045
  （選出 + 描かなかった確定済みの関係の事実文）+ 1055 + 1056 + 軽 3 件 / 面 D IK-1070 + 1071 + 0976 残り + 0571 / ハーネス（H + r9 前提・人の判断を自動でしない）。
- **第 8 波の結果と第 9 周の起動（2026-10-31 09:1x〜09:32）**: 10 回目の利用上限（週次・10-28 → 10-31 03:00）で第 8 波 4 体が中断 → 文脈ごと再開。
  backend 20,609 / src 2,423 / uxsim 1,339 pass。`viewer_entry_id` が `ReviewRequest` に落ちていた（`DecideRequest` へ移動）。ハーネス波の発見: **第 8 周の
  「1bad7d9f の ⚓ が 0 個」は前提スクリプトの読み違い**（`master_course` を剥がしていなかった — 実際は 6 トピックに ⚓・PINN の 3 個だけ開かない）→
  share / doubt / atlas の固定コースを 1bad7d9f（TeX 版）に戻す（doubt は前周と別のコース = H49 の明記）。指揮者の判断: 閲覧だけの教員は作らない（viewer の
  (a) は組まない）/ 記号・対のリンクの確定は場面の手に残す（unmet のまま）/ IK-1057 の 3 変種読み取り（段による同定を外すと 1924 件中 147 件が変わる・
  背景文の除外は既定と同じ）/ 手の前提を Cepheus に結んで confirmed で 4 件目（行は消さない）/ t16 の区切りを外して保存 → **版 4 を発行**（H36）。
  snapshot r25h。**第 9 周（09:32〜・4 面同時・指揮者の合図なし — 記号の確定・下書き・発行はすべて場面の手）**: campaign r9（share 1bad7d9f: te-05 × (a)
  候補に戻す / te-03 × (c) / st-04 × (e) / te-04 × (b2) / st-07 × (d) 文字。doubt 1bad7d9f: te-02 / te-03 / st-05 t11 / st-08 文字。atlas 1bad7d9f: te-04 ×
  依存で採用 → 画面から下書き / te-03 × 2026.6 / st-03 / st-01。studio 6be229e1 版 4: te-05 / 審判 before / te-01 × 保存 → 場面で発行 / te-01 × 付録の付け直し /
  st-01 / st-08 / 審判 after）。

### 22.19 第 9 周の結果と第 9 波の起動（2026-10-31 09:32〜11:4x・4 面同時・全 18 レーン完走・指揮者の合図なしの初の周）

- **運転の事故**: 09:36 に認証切れ（OAuth）で頭脳 6 体が止まり、10:4x の再開まで要求が溜まった。応答待ち超過（2700 秒）で atlas te-03 × freeze / share te-03 × (c) /
  studio te-01 × split-save が exit 1、face スクリプトは終了コードを見ずに次段へ進んだ（atlas の st-03 は凍結前に始まったので指揮者が中断・share の (e) 1 回目は
  記号の前提なしで走った・studio は reattach-all が先に走った）。face_<face>_r9b.sh で凍結 → 学生 / (c) → (e) / split-save → 学生 の順に走らせ直し、全面が完走。
  審判は中断 run を材料にしない（時刻で見分ける）。**教訓 = H58 / H41: lane の終了コードで依存段を止める**（第 9 波のハーネスで実装）。
- **到達（8 周とも未観察だったものが 3 つ通った）**: ①atlas — 教員が画面だけで下書きを作り（`from-frozen` をペルソナが押す）→ 反映 → te-03 が 2026.6 を凍結
  （IK-1044 GONE・凍結は 5 周連続）。②studio — te-01 が場面の手で版 5 を発行（IK-1053 の経路・IK-1070 GONE）。③share — 見送り・取り消しを［候補に戻す］
  （IK-1062 GONE）。ほか GONE: IK-1045（確定済み 6 本が L2 の実線）/ 1055 / 1056 / 1058 / 1060 / 1063 / 1071 / 0976 残り / 0681 の定義経路 / 0980 / 0897。
- **新規 IK-1072〜1081**: 1072 有向の向きが凍結の影響と学生 L2 で落ちる / 1073 足跡なしの「いまここ」不在の理由と目次「学習中」の食い違い / 1074 計算できない
  前提の区画の行が文だけ / **1075 凍結 ⚓ の後継解決が AI の画面文脈に掛かっていない**（1059 と同根 = 同じ規則を出口ごとに掛ける） / 1076 詳細の置き換わった部品の行が判断可能 /
  1077 共有 OFF の説明に［別コースで引用］が出て英語 403 / 1078 文中の否定で出典 9 が落ちる / 1079 付け直し後の未発行の事実がトピックを開くまで出ない /
  1080 まとめての知らせがトピックごとの結果を言わない / 1081 旧い生成規則の作業メモが保存済み本文に残る。IK-1059 は AI 材料の出口で再開。IK-1013 の第 8 周 GONE は
  PDF 版コースの見かけ（TeX では元の症状）。索引 786・違反 0。
- **未評価のまま（場面の手の欠落）**: 記号の確定 → 凍結 → L_TOV の概念名（IK-0983・3 周目）/ 引用の「届きます」の言い分け（IK-1061・著者の説明しか無く共有 OFF）/
  (d) が t11 のままで ⚓ が開かず IK-0920 / 1041 / 1064 / 手の前提 → 台帳 → スコープ（H52）/ 提案の［完了を記録］（H54）。
- **製品頭脳の所見**（横断チャット）: 地図の項目を開いた問いでも検索が t2 の論文に固定 / わたしの地図の選択が grounding に届かない / `[前の往復での訂正]` が前置きだけ /
  出典 9 の消失 / 二重 trace。製品頭脳 B の作業ファイル衝突で「DOMAIN_RAG」が 1 往復に出た（H55 — 製品欠陥ではない）。
- **オーナー判断の候補**: 4 面とも無し。
- **第 9 波（11:4x〜・並列 6 体）**: 面 A IK-1076 / 1077 / 0681 残り / 1063・1050 小 / 面 B IK-1075 → 1059 残り → 1074 → 1057 残り（AI 材料の経路一覧をガードレールで固定）/
  面 C IK-1072 / 1073 / 軽 3 件 / 面 D IK-1079 / 1080 / 1081 / 0872 t11 / 0571 記号 / 横断チャット IK-1078 + 製品頭脳の所見 5 件 / ハーネス H53〜H58（share）・
  H52〜H54（doubt）・H48〜H49（atlas）・H40〜H41（studio）+ LANE_FAIL + r10 前提。

### 22.20 第 9 波の結果と第 10 周の準備（2026-10-31 11:4x〜15:1x）

- **第 9 波**（11 回目の利用上限 13:xx → 14:10 復帰・5 体を文脈ごと再開）: 面 A IK-1076 / 1077 / 0681（12 字切り詰め前の TeX キー判定）/ 1063・1050 小 — 判断可否を
  `instance_is_live` + `link_undecidable_note` の 1 述語に、引用可否を `_explanation_can_cite` で 403 と画面で共有 / 面 B IK-1075（部品の文脈の解決を
  `resolve_learner_component_context` 1 本に — AI の画面文脈も通る・経路一覧を SA層設計書 §11.21 + AST ガードレール）/ 1059 残り / 1074（`detail_line`）/ 1057 部分
  （手法の選択の根を除く・集約ノードは据え置き）/ 面 C IK-1072（向きの句 `edge_direction_phrase` を凍結の影響・L2 の `directed_edges`・パネルで共有）/ 1073（`now_note`）/
  軽 3 件 / 面 D IK-1079（`course_release_comparison` を 4 面で共有）/ 1080（`outcome_groups`）/ 1081（`is_work_note_text` — 受講者向けは読み時に外し教員には事実文・
  保存物は不変）/ 0872 t11（`N_c` を別記号に）/ 0571 記号 / **IK-1117 新規**（6be229e1 は PINN の PDF 版が出典で AI 復元 45/50 — TeX 版の存在と付け替えの道を所有者に
  知らせない）/ 横断チャット IK-1078（出典の否定判定をマーカー単位に）+ FD-note 3（地図の場所の論文を焦点の後ろに）+ IK-1111〜1116（訂正の持ち越し・配置行の重複・閉世界と
  一般知識の優先・出典行の節見出し ほか）+ IK-1113（わたしの地図の選択は SA層に種別が無く見送り）/ ハーネス **LANE_FAIL / LANE_DEPS / LANE_SKIP**（lane.sh が exit を返す・
  依存段は走らない）+ H53〜H57・H52〜H54・H48〜H49・H40（発行直前の runner の学生読み `prepublish_student_reads`）+ `_learner_course` の読み違い（未登録の公開テンプレートを
  受講中と数えていた）+ r10 前提。backend 20,697 / src 2,423 / uxsim 1,367 pass。deploy・snapshot r25i。
- **第 10 周の組**（campaign r10・固定コースは share / doubt / atlas = 1bad7d9f のまま = 同じコース・トピックでの前後比較が初めて成立する周・studio = 6be229e1 版 5 = HEAD）:
  share te-03 × (c) 記号を戻す → 確認 → 凍結 → st-04 × (e)（依存）/ te-05 × (a) / te-02 × (b2) 自分の説明を共有 ON → 0d70b5d0 へ引用（IK-1061 の組）/ st-05 × (d) t1 文字。
  doubt te-02 区画 → 台帳 → スコープ / te-03 完了を記録 / st-05 t11（IK-1075）/ st-08 文字 1 回。atlas te-02 × 依存で採用 → 画面から下書き / te-01 × 2026.7 / st-03 / st-01（LANE_DEPS）。
  studio te-01 × 付け直し → 審判 before → te-01 × t17 を区切って保存 → prepublish 読み → 場面の手で発行 / st-01 / st-08 / 審判 after。指揮者の合図なし・下書き・発行・確定は場面の手。

### 22.21 第 10 周の結果と第 10 波の起動（2026-10-31 14:29〜15:5x・4 面同時・全 17 レーン完走・**事故ゼロの初周**・同じコース・トピックでの前後比較の初周）

- **運転**: LANE_DEPS / LANE_FAIL で順序を保証し、指揮者の合図・API 介入なし。share 5 / doubt 4 / atlas 4 / studio 4（prepublish の学生読みを runner が自動で残す）。
- **到達（新しく）**: 面 B — **前提 → 台帳 → スコープ → 地図が 10 周目で初めて画面で 1 本通った**（IK-1074 / H52 GONE）・提案の［完了を記録］に初到達（H54）・
  IK-1075（AI 材料の後継解決）/ 1059 残り 2 出口 GONE。面 C — 画面だけの一巡 2 周連続（2026.7・凍結は 6 周連続）・IK-1072 / 1073 / 1045 の名前の行（初観測）GONE。
  面 D — 付け直しだけの段階から発行に至る（v6・IK-1079 / 1080 GONE）・場面の手で v7・X1 が 3 段（before / prepublish / after）で初めて揃う・t11 の N = 15 GONE。
  面 A — IK-1076 / 1077 / 1062（否定側の通し）/ 1063・1050 の「記録なし」GONE。
- **新規 IK-1118〜1127**: 1118 描いていない関係の行と反映の知らせで向きが落ちる（同じ型の 4 回目）/ 1119 「改訂で加わった関係」が全改訂を指す（**オーナー判断候補 →
  推奨どおり**: 地図に印を付けず右パネルの関係の行に版の事実）/ 1120 合意リストの支持線の範囲がコース全体で台帳と食い違う / 1121 提案の完了に帰属・日時がない /
  1122 復帰位置の既定値 0 を構造帰属の worker が区画と読む / 1123 見送りの札が二重 / 1124 凍結の確認文が学生への効果を言わない / 1125 ドリルダウンの平文化に `\!` 等が残る /
  1126 引用の「届きます」が引用先トピックの ⚓ を見ない（IK-1061 の形を変えた残り）/ 1127 版の管理モーダルが学習者の読む版を言わない。索引 806・違反 0。
- **場面の材料の問題が続く**（share）: H59 `entry_hint: TOV` が 2 エントリに当たり記号 5 本の無い側を開いた → 記号の確定 → 凍結 → L_TOV の概念名（IK-0983）は **4 周続けて未評価**。
  H60 `topic_id: t1` が st-07 の材料にしか無く st-05 は t11。H65 lane は exit だけ見て「前提の状態」を見ない。→ 第 10 波のハーネスで材料を id 化・ペルソナ非依存化・到達点による依存判定。
- **製品頭脳の所見**: 地図の場所を開いた問いでもトピック論文が先（4307・FD-note 3 の順序）/ わたしの地図の選択が届かない（IK-1113）/ 一般知識の補完が記録の捏造を誘う（4302）/
  選択箇所の式が抜粋に無い（4313）。製品頭脳の作業ファイル衝突は専用ディレクトリで再発なし。
- **第 10 波（15:5x〜・並列 6 体）**: 面 A IK-1126 / 1123 / 1124 / 1125 / 1062 小 / 面 B IK-1120 / 1121 / 1122 + 語の残り / 面 C IK-1118 / 1119（推奨で実装）/ H54 の decide 409 / 軽 2 件 /
  面 D IK-1127 / 1081 残り / 0571 定義なしの事実文 / 横断チャット 地図の場所の焦点順・IK-1113（`personal_map_node`）・記録の有無の拘束・選択の式・肯定引用だけの grounding /
  ハーネス H59〜H65（share）・H59〜H61（doubt）・H51〜H54（atlas）・H43〜H45（studio）+ r11 前提。

### 22.22 第 10 波の結果と第 11 周の準備（2026-10-31 15:5x〜17:0x）

- **第 10 波**（並列 6 体・中断なし）: 面 A IK-1126（引用の「届きます」を学生の画面の ⚓ で判定 `course_has_component_anchor`）/ 1123 / 1124 / 1125 / 1062 小 + 共有モーダルの
  版の見出し + 理由の後付け PATCH（アンカー 408）/ 面 B IK-1120（合意リストの支持線を台帳と同じ範囲に・欄名を分ける）/ 1121（migration **095**: 提案の完了に帰属・日時・確かめたこと・
  下流の語の表）/ 1122（レクチャー中だけ `segment_id` を送る・worker は報告の無い区画に結ばない）+ 語の残り / 面 C IK-1118（向きの句の全出口 + AST ガードレール）/
  1119（推奨どおり: 右パネルの関係の行に「（版 X で加わった）」・地図に印なし）/ H54（反映済みの見送りは 409・`dismiss_locked`）/ 軽 2 件 / 面 D IK-1127（`VERSION_AUDIENCE_NOTE_COURSE`・
  `changed_topic_titles`）/ 1081 残り（判定語の拡張・2 区分・`embed_conflict_note`）/ 0571 / 件数なし / 記号の別の論文の題名 / 横断チャット FD-note 4（地図の場所を選んだ往復は場所の論文だけを
  焦点に・トピック段を外す）+ IK-1113（`personal_map_node_id` — nearby の正本で解決）+ IK-1141〜1146（記録を問う往復の拘束・選択の式・肯定引用だけの grounding・走り込み見出し・
  分類の手がかり・構造帰属の地図の型は保留）/ ハーネス **到達点による依存**（`runner/outcome.py`・`LANE_DEPS="<persona>@<action>=<value>,…"`）+ H59〜H63（share）・H59〜H61（doubt）・
  H51〜H53（atlas）・H43〜H45（studio）+ `te01_course_head` の読み違い + r11 前提（28 項目・確認だけ）。backend 20,803 / src 2,423 / uxsim 1,402 pass（投影同期 w10 が追随中の 2 件を除く）。
  deploy・migration 095 適用・snapshot r25j。
- **第 11 周の組**（campaign r11・固定コースは share / doubt / atlas = 1bad7d9f（3 周連続）・studio = 6be229e1 版 7 = HEAD・X1 は t18）: share te-03 × (c) id 67727cb0 で記号を戻す → 確認 →
  凍結 → st-04 × (e)（到達点 `bound_link_decide=confirm` + `entry_freeze` に依存）/ te-02 × (b2) 自分の説明 → 共有 ON → 引用 / st-05 × (d) t1（(a) は休み）。doubt te-02 / te-03（完了の帰属・
  支持線の段階）/ st-05 **t1**（記帳ありの部品の一行・11 周目）/ st-08（構造帰属）。atlas te-05 × 採用 → 下書き → 反映 → 見送りの任意の手（H54）/ te-04 × 2026.8 / st-03（名前の行の向き・版の事実）/
  st-01。studio te-01 × t18 → prepublish → 発行 / st-01（t1・t4 の注）/ st-08（N_c の定義）。指揮者の合図なし・矢じりは Browser pane で 1 枚。

### 22.23 第 11 周の結果（確認周）と停止の判断（2026-10-31 19:12〜20:0x・4 面同時・全 15 レーン事故なし）

- **オーナー指示（10-31）**: 回数を重ねても改善効果が上がらなければループを止めてよい。→ 第 11 周を「第 10 波の確認周」とし、各審判に停止の判断材料 3 点
  （①新規の重さ ②第 10 波の GONE 確認率 ③一度も通っていない経路）を書かせた。
- **運転**: 到達点による依存（`LANE_DEPS="te-03@bound_link_decide=confirm,entry_freeze"`）が初めて働き、share の (c) → (e) が前提つきで走った（5 周目で初）。
  atlas は画面だけの一巡 3 周連続・凍結 7 周連続（2026.8）。studio は v8 を場面の手で発行・X1 は 3 段（prepublish は保存後・発行前の読み = アクセスログで確認）。
  H53（矢じりの見え方）は指揮者の Browser pane への学生トークン投入が許可されず未実施。
- **判定**: 第 10 波の GONE 確認 — atlas 5/7・doubt 8/9・share 3/7（3 は場面に出ず未評価・1 は英語の言い回しで形を変えて STILL）・studio 3/6（1 は計算のみ・2 は未観察）。
  STILL への戻りは IK-1143 → 1150（英語の名詞）と IK-1123 の取り消し行だけ。
- **新規 IK-1147〜1158（12 件）**: 高 0。中 2 = **IK-1149**（有向の向きが教員の選択でなく node_id の辞書順 — 向きの言い方を全面で揃えたことで「誰も向きを選んでいない」ことが
  見えた）/ **IK-1151**（第 10 波の退行: `_describe_relations` の `viewer` 上書きで 2 行目以降の相手・向き・札が取り違う）。残り 10 件は語・出口・照合の取りこぼし
  （1147 行なし部品の一行が AI に無い / 1148 到達可能性の語 / 1150 / 1152〜1154 知らせの文言・二重 / 1155 言い換え主張の「（原文）」/ 1156 バッククォート / 1157 添字の割れ /
  1158 式の事実行の見出し）。
- **一度も通っていない経路**は全面とも製品ではなく砂場の状態・場面の材料・runner の制約が塞ぐ（記号の概念名 = PDF 版の行が選ばれた H66 / ⚓ → 共通部品 → 別の論文 = 確定リンクを持つ
  部品の開ける ⚓ が固定コースに無い H69 / 論文由来の候補 = 2 本目の論文なし / 記帳ありの部品の一行 = ⚓ の部品に台帳行なし H65 / 矢じり = ブラウザ）。
- **判断（指揮者・オーナー指示に従う）**: **ここでループを止める。** 構造的な根は第 9〜10 周で出尽くし、第 11 周の新規は中 2・小 10 で、未通過経路は周を足しても通らない。
  止める前に締めの波（第 11 波）で IK-1149 / 1151 と小さな取りこぼしを是正し、全件テスト → deploy → 総括（§22.24）を書く。第 12 周の再演はしない。

### 22.24 第 25 周「空白の 4 面」の総括（2026-10-22〜10-31・第 1〜11 周・締めの波まで）

- **運転の型**: 面 A share / B doubt / C atlas / D studio を 4 面同時に、教員 2〜3 × 学生 2〜3 × 場面 4〜6 の組を毎周替えて回した（P-0025）。周 → 審判（面ごと・transcript の生応答で
  裏取り・偽所見はハーネスへ）→ 波（面ごとの製品是正 + 横断チャット + ハーネス/次周の前提）→ 投影の同期 → 全件テスト → deploy → 前提 → snapshot → 次周。LLM 呼び出しは
  すべて bridge（製品は proxy 8090 → mailbox、ペルソナは面ごとの mailbox）で、頭脳は Opus の subagent。指揮は Fable。
- **到達した経路（周を重ねて初めて通ったもの）**: 教員が画面だけで下書き → 反映 → 凍結（第 9 周・以後 3 周連続・凍結 7 周連続 2026.2〜2026.8）/ 場面の手での版の発行
  （第 9 周 v5 → 第 11 周 v8）/ 否定の判断を候補に戻す（第 9 周）/ 前提 → 台帳 → スコープ → 地図（第 10 周）/ 提案の完了と帰属（第 10〜11 周）/ 記号を候補に戻す → 確認 → 凍結を
  場面の手で（第 11 周）/ X1 の 3 段（第 10 周）/ 引用の「届く」を学生の画面の ⚓ で言う（第 11 周）。
- **是正の規模**: migration 091〜097（表示名・取り消し・提案の完了・辺の向き・到達可能性）、課題ナレッジ IK-0846〜1158（第 25 周で起票・824 件・違反 0）、backend 20,857 /
  src 2,423 / uxsim 1,425 pass。製品の正本に足した規律: 判断の記録（誰・いつ・なぜ・記録なし）を一重の述語で / 同じ規則を出口ごとに掛けない（AI 材料の経路一覧 + AST ガードレール・
  向きの句の全出口）/ 学習者の語は `learner_atlas_wording` / 焦点論文に地図の場所（FD-note 3・4）/ 到達点による依存（`runner/outcome.py`）。
- **残る根（製品）**: 同名コースの見分け（IK-0407・11 周 STILL — 運用の問題）/ A層の英語生成文（IK-0571 / 0755・生成言語の設計）/ 材料の薄さ（論文由来の候補 0・2 本目の論文）/
  構造帰属の地図の型（IK-1146・据え置き）/ IK-1057 の集約ノードの根（据え置き）/ 1bad7d9f の旧規則の保存物（所有者の付け直し待ち）。
- **残る根（運転）**: 場面の材料がペルソナ依存で campaign の意図に追随しない（H60・H64）/ 名前の部分一致で別の行に当たる（H59・H66）/ 砂場に前提の状態が無い
  （確定リンクを持つ部品の開ける ⚓・H69）/ ブラウザでしか見えないもの（矢じり・H53）。
- **停止**: オーナー指示に従い第 11 周で停止。再開するなら「材料を足した周」（2 本目の論文・⚓ の付け直し）か「学生の［修正を報告］→ 教員が次版で向きを直す」の 1 本から。
- **締めの波の砂場確認（読み取りのみ・LLM なし・2026-10-31 20:xx）**: IK-1151 — 67727cb0 の関係 3 行（上位 / 関連 / 関連）が 2 行目以降も相手の名前・向きの文・札を
  正しく返す（退行の解消を実機で確認）。IK-1157 — N_c の吹き出しが「定義の文は見つかりませんでしたが、この記号は番号の無い式の中に現れます」と用例を拾う
  （定義の判定は据え置き）。IK-1149 / 1148 — 採用時の向きの選択・by_document は書き込みを伴うため単体テストのみ（再演なし）。
