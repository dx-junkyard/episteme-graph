# uxsim — ペルソナ通し受講テスト

合成したペルソナ（学生・教員）に製品を通しで使わせ、詰まった箇所・原則に反する表示・マニュアルとの
食い違いを**仮説として**拾うハーネスです。正本の設計は
[`docs/architecture/persona_enactment_testing_design.md`](../docs/architecture/persona_enactment_testing_design.md)。
製品イメージには同梱しません（`backend/Dockerfile` は `uxsim/` を COPY しない — PE1）。

## 構成

| 場所 | 中身 |
|---|---|
| `archetypes/` `domains/` `scenarios/` `campaigns/` | データ（行動型・分野パック・経路・演目）。形はそれぞれの `README.md` |
| `actions/registry.py` | 行為レジストリ（ペルソナができる操作 = 画面の部品 + 製品の実ルート — PE9） |
| `runner/` | `client.py`（httpx・JWT）/ `state.py`（既知 ID と画面の投影）/ `actions_exec.py` / `scenario.py` / `api.py`（campaign 実行 CLI） |
| `persona/` | `compose.py`（行動型 × 分野パック）/ `prompt.py` / `agent.py`（1 ステップ 1 コール） |
| `llm.py` | ペルソナ・審判側 LLM（OpenAI / Anthropic / replay。製品の `core/llm.py` は import しない） |
| `oracles/` | 審判 A 契約 / B 原則 / C 行動 / D 文書 / E 観測差分 + `findings.py` + `run_all.py` |
| `report/` | `campaign_report.py` / `issue_knowledge_bridge.py`（課題ナレッジ候補）/ `map_draft.py`（着手の地図の下書き） |
| `runs/` | run の生ログ（git 管理外）: `<campaign_id>/<run_id>/{meta.json, transcript.jsonl, cache/, logs/, findings.jsonl, report.md}` |

## 走らせ方

1. 砂場を立てる（開発スタックとプロジェクト名を分ける — PE1）:

   ```bash
   docker compose -p episteme-uxsim -f docker-compose.yml -f uxsim/sandbox/docker-compose.uxsim.yml up -d --build
   ```

2. 設定を `uxsim/sandbox/.env.uxsim`（git 管理外）か環境変数に置く:

   | 変数 | 意味 |
   |---|---|
   | `UXSIM_BASE_URL` | 砂場の frontend（既定 `http://localhost:3000`。overlay は 3100 を公開するので `http://localhost:3100` にする） |
   | `UXSIM_ADMIN_PASSWORD` | 砂場の `Administrator` のパスワード（ペルソナのアカウントを管理者の作成 API で用意する） |
   | `UXSIM_PERSONA_PASSWORD` | ペルソナ共通のパスワード（砂場専用。既定値あり） |
   | `UXSIM_PERSONA_LLM_PROVIDER` | `anthropic`（既定・製品と別プロバイダ）/ `openai` / `replay` |
   | `UXSIM_PERSONA_LLM_MODEL` / `UXSIM_PERSONA_LLM_API_KEY` | ペルソナ・審判の LLM |
   | `UXSIM_RUNS_DIR` | run の置き場（既定 `uxsim/runs`） |
   | `UXSIM_SANDBOX_DATABASE_URL` | 審判 E が**読む**砂場 DB（任意。無ければ E は「未実施」） |

   `anthropic` パッケージは製品の requirements に無いので、Anthropic を使うときは別途入れる。

3. campaign を走らせる（リポジトリルートで）:

   ```bash
   backend/.venv/bin/python -m uxsim.runner.api campaigns/astrophysics/c-first-course.yaml \
       [--persona st-01-m1-radio] [--scenario s-ask-until-confused] [--max-steps 20] [--no-oracles]
   ```

   教員段 → 学生段の順に走り、`sessions_per_student` が 2 以上なら 2 回目は再ログインで「翌日」を近似する（§7.4）。
   終わると決定論の審判（A・B・C の前処理・D の組・E）が自動でかかる。

4. 再演（replay）: `--replay <run_id>` で同じ campaign の `cache/persona_llm.jsonl` を使い、ペルソナ LLM を呼ばずに
   同じ行為列を再演する（製品側だけ本物）。製品の応答が変わって cache miss したら、provider が live なら
   そこから live に切り替え、分岐点を `meta.json` の notes と各ステップの `replay_divergence` に残す（PE5）。

5. 審判・報告:

   ```bash
   backend/.venv/bin/python -m uxsim.oracles.run_all uxsim/runs/<campaign>/<run_id> [--judge]   # --judge は C・D の LLM 審判
   backend/.venv/bin/python -m uxsim.report.campaign_report uxsim/runs/<campaign>/<run_id>
   backend/.venv/bin/python -m uxsim.report.map_draft uxsim/runs/<campaign>/<run_id>
   backend/.venv/bin/python -m uxsim.report.issue_knowledge_bridge uxsim/runs/<campaign>/<run_id>
   ```

   課題ナレッジ候補は `runs/.../ik_candidates/IK-XXXX-<slug>.md` に出る。`docs/issue_knowledge/` へは自動で書かない。
   番号は `backend/scripts/issue_knowledge_index.py --new <slug>` で振り、返却されたファイルに内容を写す（分類の確定は人）。

## 予算（PE4）

campaign の `budget` の 3 キー（`product_llm_calls` / `persona_llm_calls` / `wall_clock_minutes`）を超えたら止まり、
「予算に達したため止めた」と `meta.json` に残す。製品側の LLM 呼び出しは `llm_cost: product` の行為の実行回数で
近似する（実数は審判 E の `llm_usage_events` と U層の計器が正）。campaign 中に arXiv を呼ぶ行為は持たない。

## 禁止事項（PE1〜PE10 の要約）

- **砂場でしか走らせない**。本番・開発共用 DB の URL を設定しない（PE1）。
- 状態を作るのは製品の HTTP API だけ。DB 直書きで教材・コース・痕跡を作らない。砂場 DB は審判が**読む**だけ（PE2）。
- 審判は仮説を出すだけ。発見は `classification.review: candidate`、merge・分類の確定は人（PE3）。
- 予算を超えたら止まる（PE4）。全 run の transcript とペルソナ LLM の出力を残し、replay できる（PE5）。
- 期待の正本は `docs/manual/`・vision §6・各層の不変条項。審判用の新しい期待表を作らない（PE6）。
- 失敗した run も残す。数値は run の生ログとレポートにだけ置く（PE7）。実在の人のデータを入れない（PE8）。
- ペルソナの行為は行為レジストリの id だけ。画面に無い操作は選べない（選ぼうとしたら `unsupported:` として記録 — PE9）。
- 「もっと点数を出すべき」「督促すべき」のような原則違反の提案は発見にしない（PE10）。

## テスト

```bash
cd uxsim && ../backend/.venv/bin/python -m pytest tests -q
```

外部 API は呼ばない（LLM は台本、製品は `httpx.MockTransport`）。`test_data_contract.py` は実データの経路・
ペルソナ・campaign がハーネスの読み方で全部読めることと、偽の製品で最後まで回ることを確かめる。

## v1 で未実施のもの

browser runner（Playwright）/ 回帰経路の一括再演（`c-regression-all`）/ SSE の順序検査 / 審判 B の閉世界語彙
（SL1）・督促語彙の検査 / `error-logs` の差分 / 時刻注入が要る経路（tension worker のセッション終了・削除猶予）。
