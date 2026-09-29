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

- 2026-09-27 起草。前提となる現物調査（テスト基盤・CI・compose・シード・fixture・アカウント／コース API・
  学習側 API 全ルート・観測テーブル・指標カタログ・フロント計測・固定事実文・CostGate・G層・課題ナレッジの規約・
  マニュアル構成）は本書 §16 に要約。以後の実装記録は Phase ごとに本節へ追記する。
