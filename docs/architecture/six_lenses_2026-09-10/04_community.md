> **調査記録（2026-09-10）**: ビジョン×UXギャップ調査「六つのレンズ」のレンズ4（共同体・可視性・出口）詳細報告。統合と優先付けは [../vision_ux_gap_six_lenses_2026-09-10.md](../vision_ux_gap_six_lenses_2026-09-10.md)。読み取り専用調査で、file:line は HEAD d5ac3cb 時点。「推測」と明記した箇所は未確認。

# 観点レンズ: 共同体・可視性・システムの出口

対象: `docs/vision.md` §1〜§6（§7 以降は未読）と現物コード。読み取り専用調査。
凡例: **[事実]** = ファイルを読んで確認した / **[推測]** = 未確認の解釈。

---

## 1. 境界の現状

### 1.1 可視性6軸（§5.4 が「一軸に畳まない」と要求する軸）の現状

§5.4 は可視性を「閲覧できる audience / 名前を見られる audience / 引用・再利用できる条件 /
評価に利用できるか / 外部 index・外部 AI に渡るか / いつ撤回・凍結できるか」の6軸に分けよと
書く。現行の `private / group / public` は**第1軸だけ**を実装している。

| # | 軸 | 現行の扱い | 固定 / 選択可 | 利用者への明示 | 根拠 |
|---|---|---|---|---|---|
| 1 | 閲覧できる audience | **教材・コースのみ** 3値（private / group / public）+ `object_group_permissions(viewer\|editor)`。学習者の産出物にはこの軸自体が無い（常に本人のみ） | 教員は選択可 / **学習者は選択肢なし（private 固定）** | 教員: あり（`docs/features/auth-visibility.md` §3）。学習者: グループ参加の説明のみ（`docs/manual/student/02-student.md:1017-1040`） | `backend/api/services.py:1075-1095`, `:1453-1470`, `backend/db/044_*`（object_group_permissions） |
| 2 | 名前を見られる audience | **常に開示・分離不能**。①学習者の未回答質問は表示名＋文面のまま教員に出る ②学習者の地図修正報告は「匿名にはできません」と UI に明記 ③教員の説明は `author_name` として学習者に出る ④グループ詳細はメンバーの **display_name と email** を全メンバーに返す | **固定（全経路で開示 = 帰属と開示が融合）** | ①はマニュアルに明記あり（`docs/manual/student/01-specification.md:167-172`）。②は UI に明記。③④は明示なし | `backend/api/routes/admin.py:3343-3382`, `frontend/public/js/atlas-report.js:121,127`, `backend/api/routes/learning.py:4196-4226`, `backend/api/routes/groups.py:204-235` |
| 3 | 引用・再利用できる条件 | C層のみ。`component_explanations.shared` の **1ビット**（インスタンス内の全 TEACHER に対して on/off）。group 粒度・条件付き（改変可否・帰属義務）は無い。引用は `component_citations` に帰属＋版固定で記帳 | 教員は on/off のみ選択可。**条件の指定は不可** | `docs/features/endorsement-sharing.md` §6 に API 表はあるが「誰に共有されるか」の粒度は書かれていない | `backend/db/021_endorsement_sharing.sql:32`, `backend/api/routes/theory_components.py:4286-4300` |
| 4 | 評価に利用できるか | **「使わない」で固定**。IndicatorSpec が非利用4項目（ranking / grading / recommendation / auto_gate）を全 spec に構造強制。学習者痕跡は k=3 集約のみ | 固定（設計として正しい固定） | **よく明示されている**（`docs/manual/student/01-specification.md:145-185` の2表 + `GET /api/indicators`） | `backend/core/indicator_catalog.py:205-244` |
| 5 | 外部 index・外部 AI に渡るか | **暗黙に「常に渡る」で固定**。学習者の発話は RAG チャット・tension mining（`role == "learner"` の発話を逐語で LLM 入力に組む）・structure_anchor 帰属・cycle Elicit/Diff で外部 LLM プロバイダへ送られる。学習者向けドキュメントに**この事実の記述が一箇所も無い**（`docs/manual/student/` 全文を grep して 0 件）。同意フロー・利用規約画面も存在しない（`consent` / `同意` / `利用規約` を frontend・student マニュアルで grep して 0 件）。IndicatorSpec にも「外部転送」フィールドが無い | **固定・opt-out 不可・不告知** | **無し**（最大のギャップ） | `backend/core/tension/input_builder.py:43-46,97`, `backend/api/routes/learning.py:2965-3000`, `backend/core/indicator_catalog.py:205-221`, `docs/manual/student/*.md`（grep 0 件） |
| 6 | いつ撤回・凍結できるか | 部分的。学習者: `map-exclude`（地図に出さない）・書き直し / 削除（truncate + `superseded`）・「わたしの記録」の閲覧と持ち出し。**封印（内容を読めなくする）は未実装とマニュアルが正直に告知**。アカウント削除は **SYSTEM_ADMIN のみが予約可能**で、学習者本人が起動できる撤回・退出の経路が無い。教員: V層の削除予約（猶予付き）・retire・凍結 | 学習者は限定的に選択可、**退出は不可**。教員は選択可 | 学習者向けに明記あり（`docs/manual/student/02-student.md:812-845`） | `backend/api/routes/my_records.py:43-79`, `backend/api/routes/learning.py:3941`, `backend/core/account_lifecycle.py:107-130`, `backend/api/routes/versioning.py`（全 endpoint `_require_teacher`） |

**まとめ [事実]**: 6軸のうち**独立に扱えているのは第1軸と第4軸のみ**。第2軸（名前）は全経路で
開示に固定され、原則14 が言う「帰属記帳と開示の分離」がどこにも実装されていない。第5軸
（外部 AI）は固定かつ**不告知**で、これは原則8（出所の正直さ）が自分自身の情報フローに
適用されていないことを意味する。第6軸は学習者側が「見る・持ち出す」までで、「止める・出る」が無い。

### 1.2 学習者の産出物はどこに留まるか（問い1への答え）

**[事実] 学習者が private → group → public と段階的に引き受ける操作は存在しない。**
学習者が書き込める経路を全部たどると:

| 学習者の産出 | 保存先 | 誰に届くか | 本人の選択 |
|---|---|---|---|
| tension / 帰属付きの問い / 予測 / 意図 / 印 | `interest_traces` | **本人のみ** + 教員向け k=3 集約 | 地図に出す/出さない だけ |
| 着地画面の「今日の理解を自分の言葉で」 | `interest_traces`（`status='articulated'`） | **本人のみ** | 無し |
| 再構成の産出・自己確認 | `learner_reconstructions` | 本人 + 教員向け出題健全性の集約 | opt-out の汲み取りのみ |
| 教材から答えられなかった質問 | `unanswered_query_logs` | **教員に表示名＋文面のまま**（唯一の非匿名個票） | 無し（楽屋で聞けば記録されない、が「隠す」経路であって「引き受ける」経路ではない） |
| 地図の修正報告 | `atlas_correction_reports` | **教員に名前付きで**。凍結時に `report_credits` として名前が出る | 匿名不可 |
| 「この先を知りたい」 | `interest_traces(frontier_interest)` | 教員向け k=3 集約 | 取り消し可 |

**学習者同士には何も届かない [事実]**: `frontend/public/js/app.js` と
`backend/api/routes/learning.py` を peer / 他の学習者 / classmate 等で grep して 0 件。
学習者が他の学習者を知覚できるのは `GET /api/groups/{id}` のメンバー一覧
（`backend/api/routes/groups.py:204-227`。display_name と **email** が返る）だけで、
そこに学習の中身は一切乗らない。つまり**共同体の中で名簿だけが見え、産出物は誰にも見えない**。

これは §5.2「産出物は本人が選ぶまで group を越えない」を満たしてはいるが、
**「本人が選ぶ」という選択肢自体が存在しない**という形で満たしている（fail-closed の
過剰適用）。§1.1 の「どこまでを自分の判断として公に引き受けるかを**選べる**」は未実装。

### 1.3 出口の一覧（システム外へ出るもの）

| 出口 | 誰が | 何が出るか | 権限ゲート | 監査 | 来歴（誰が確定・どの版・どの根拠） |
|---|---|---|---|---|---|
| `POST /api/courses/{id}/export-bundle` / `POST /api/documents/{id}/export-bundle`（ZIP。UI は「外部レビュー用に書き出し」） | TEACHER 以上 | claims / components / 理論操作グラフ / **PDF 逐語 evidence スニペット** / 数式 / 導出 / LLM 生出力（オプション） | **`_require_teacher` のみ。対象オブジェクトの owner/editor 判定が無い** → 同一インスタンスの任意の TEACHER が任意のコース・教材の全解析成果を吸い出せる | **無し** | 部分的。`artifact_run_id` / `export_source_policy` / `fallback_sources` は入るが、**誰が承認したか・shared_release の版・decision_context は入らない**。README は英語の機械可読仕様 | `backend/api/routes/export.py:2828-2832, 2992-2996`, `:2583-2613`, `:2640-2663`; `docs/manual/teacher/11-admin-materials.md:1225-1238` |
| `GET /api/me/records/export`（JSON） | 学習者本人 | 本人の全痕跡（payload 全文・無加工） | 本人固定（user_id パスパラメータを持たない） | **意図的に記帳しない**（設計判断として明記） | 出所コース・日付は入る。引用可能な形ではない | `backend/api/routes/my_records.py:57-79` |
| `GET /api/admin/discuss/observation-dump`（tar.gz/zip） | SYSTEM_ADMIN | 観測イベント（**本文非含有・仮名化**） | `_require_system_admin` | **あり**（`AUDIT_ENTITY_DISCUSS_OBSERVATION`） | 該当せず | `backend/api/routes/discuss_observation.py:118-153` |
| `GET /api/admin/materials/{id}/pdf`（inline） | 教員 | PDF 原本 | document owner/editor | — | — | `backend/api/routes/admin.py:1352` |
| 画面のコピー&ペースト | 全員 | 任意 | — | — | 無し | — |

**存在しない出口 [事実]**（grep で 0 件）:
- BibTeX / RIS / CSL-JSON / 引用文字列の生成（`bibtex` の唯一のヒットは
  `backend/core/document_pipeline/tex_archive.py` の**入力側** `.bib` 解析）
- 印刷用スタイル・`window.print`（`@media print` を frontend 全体で grep して 0 件）
- Zotero / Obsidian / LaTeX / Markdown への書き出し
- 承認済み説明・claim・図・グラフの単体書き出し（L層ライブラリにも export endpoint 無し —
  `backend/api/routes/library.py` の endpoint 一覧に export/download 系が無い）
- **取り込み（import）の経路**。`export-bundle` は一方通行で、別インスタンスから受け取る
  弁が存在しない（`import` / `import-bundle` を routes 全体で grep して 0 件）

**非対称の指摘 [事実]**: 本文を含まず仮名化された観測ダンプは SYSTEM_ADMIN 限定＋監査付きなのに、
**PDF 逐語引用と LLM 生出力を含む export bundle はロールだけで通り監査も無い**。
原則11（fail-closed）と原則14（監査必須）が、いちばん外へ出る経路にだけ適用されていない。

### 1.4 異議のコスト（問い3への答え）

| 誰が | 何に対して | 経路 | 帰属 | 開示 |
|---|---|---|---|---|
| 学習者 | 分野の地図の配置・状態 | 「修正を報告」 | 必須・**匿名不可** | 教員へ即時。凍結時に名前が `report_credits` に出る |
| 学習者 | 機械判定（再構成の DIFF） | self-check `verdict_wrong` | 本人のみ | 教員へは出題健全性の集約のみ |
| 学習者 | **教員が承認した説明** | **無し** | — | — |
| 学習者 | **教員が確認した論文配置（landscape）** | **無し**（マニュアルは「何かをする必要はありません」と書く） | — | — |
| 学習者 | **確定済み claim / 台帳の検証状態** | **無し**（学習者向け台帳 API は読み取りのみ） | — | — |
| 教員 | claim / 前提 / 台帳 | D層 `challenges`（`challenge_type` 4種・理由必須・withdraw は状態遷移） | 必須 | 一覧に `challenger_name` が出る |
| 教員 | 別の教員の説明 | 対抗する説明バージョンを並置（マージされない） | 必須 | shared にすれば全 TEACHER |

根拠: `backend/api/routes/atlas.py:1735-1786`, `backend/core/atlas_reports.py:9,183-188,226-249`,
`backend/api/routes/reconstruction.py:451-455`, `backend/api/routes/doubt.py:1569-1636`（
`admin_router` = `_require_teacher` のみ）, `backend/api/routes/doubt.py:2247`（学習者向けは
`GET .../ledger/...` の読み取りのみ）, `docs/manual/student/02-student.md:745-751`。

**[事実] 「帰属記帳と開示の分離」で成立する異議経路はゼロ。** 全ての異議は
「台帳に帰属を書く」＝「相手にいま名前が見える」と等価になっている。§5.3 が挙げる
補助策（役割化・帰属記帳と開示の分離・ゼミ横断レビュー）のうち、**システム側で実装できる
はずの2つ目が未着手**。

### 1.5 研究室の外（問い5への答え）

**[事実] 来歴付きで交換する手段は存在しない。**
- V層（`shared_versions` / `shared_version_state` / `shared_version_subscriptions`）は
  発行版・ピン留め・削除猶予・adopt を実装しているが、全 endpoint が `_require_teacher` で
  **同一インスタンス内の教員間**に閉じている（`backend/api/routes/versioning.py:16,170-355`）。
- release snapshot は `published_by` / `version_no` / `note` を持つ
  （`backend/core/versioning/releases.py:116-207`）が、**export bundle はこの release_id を
  参照しない**。つまり「どの発行版か」を外に持ち出せない。
- 骨格（atlas skeleton）とカートリッジは `backend/atlas_domains/<domain>/skeleton.yaml` /
  `backend/cartridges/<id>/` として**ファイルで**配布でき、起動時に冪等シードされる。
  これは実質的な唯一の越境路だが、**devops 作業であって UX ではない**し、来歴（誰が凍結した・
  どの根拠で）は yaml に乗らない [推測: yaml の中身は未確認]。
- 別インスタンスから受け取る弁が無いため、仮に ZIP を渡しても受け側は手作業になる。

### 1.6 砂場で完結する学習（問い6への答え）

**[事実]** 修了カードは「全トピックを学習しました」の事実のみ（点数・祝祭なし。
`docs/manual/student/02-student.md:797-810`）。「わたしの記録」は全痕跡の羅列と持ち出し
（`backend/api/routes/my_records.py`）。**分野の外へ持ち出せる判断能力を本人が確認する手段は無い。**

問題は「未完に見えるか」の形で現れる [推測]: 学習者から見える「先」は、
①コースの修了 ②論文の海（もっと読む）③地図の端（もっと外がある）の3つで、いずれも
**外へ広がる方向**にしかラベルが無い。「ここで終えてよい」「ここまでを自分は持てるように
なった」を本人の言葉で確定する場所が無く、記録は時系列の羅列のまま残る。
§1.2 が「未完ではない」と宣言している状態に、UX 上の着地点が対応していない。

---

## 2. 提案

---

### 提案1: 引き受けの段（Ladder of Owning） — 学習者の産出物を private → group へ、本人の操作だけで

**近づける vision**: §1.1（どこまでを自分の判断として公に引き受けるかを**選べる**）/
§5.2（産出物は本人が選ぶまで group を越えない ＝ 選べば越えられる）/ §5.3（重なるが同じでは
ない理解の並存を、教員の説明だけでなく学習者にも）

**現状**: 学習者が書き込める全経路（tension / articulated reflection / 再構成 / 意図 / 印）は
`interest_traces` と `learner_reconstructions` に落ち、露出は `trace_registry` の3宣言
（`learner_trajectory` / `teacher_dashboard` / `personal_map`）で固定される
（`backend/core/trace_registry.py:44-66`）。**この3宣言に「本人が選んで共有する」次元が無い**。
学習者が他者に何かを渡す唯一の経路は、名前付き強制の地図修正報告と、本人が選んでいない
`unanswered_query_logs` である（`backend/api/routes/admin.py:3343-3382`）。

**なぜギャップが体験を損なうか**: 「引き受ける」は、書いたものが他人の目に触れて反論されうる
状態に自分の意思で置くことでしか練習できない。現行の砂場は完全に密閉されているので、
学習者が経験するのは「安全に書く」までで、「安全に**公に**する」の一段が抜ける。その結果、
公開の練習が一度も無いまま、修士論文や学会発表で初めて名前付き公開に直面する — つまり
このシステムが最も守ろうとした能力（異議を受けて壊れない）を育てる機会を、保護の名で
削っている。しかも密閉のせいで、同じゼミの学習者の理解が並存する光景（§5.3 の学習者版）が
一度も現れない。

**機能のかたち**:
- データ: 新テーブル `learner_publications`（`user_id` / `trace_id` or `reconstruction_id` /
  `scope ∈ {private, group}` / `group_id` / `name_disclosure ∈ {named, handle}` /
  `withdrawn_at` / `created_at`）。**public は v1 で作らない**（§5.2「未検証の産出物に本人の
  名前が付いて public に永続することを既定にしない」）。migration **要**（1枚）。
  痕跡本体には触れない（原則13）。
- API: `POST /api/me/publications`（本人のみ・対象は本人の痕跡に限定・group は本人が member の
  group のみ = fail-closed）/ `DELETE`（撤回 = `withdrawn_at` の状態遷移、行削除しない）/
  `GET /api/learning/groups/{gid}/shared-notes`（同 group のメンバーのみ・**時系列のみ・
  件数バッジなし・通知なし・未読なし**）。
- UX: 着地画面の reflection 欄と「わたしの記録」の各行に「このゼミに置く」。既定は置かない。
  置いたものには常に「いつでも取り下げられます」の事実文。**取り下げても何も失わない**ことを
  文面で明示（§5.2）。閲覧側は静的リスト（更新通知・バッジ・ポーリングなし）。
- LLM: **無し**（原則9）。
- 既存正本への積み方: `core/trace_registry.py` に4番目の露出宣言 `learner_publication` を追加し、
  「この kind は本人の明示操作で group へ出しうるか」を kind ごとに宣言する
  （既存3宣言と同じく必須フィールド化すればガードレールが未宣言の追加を止める）。

**14原則との照合**: 原則12（押し付けない）— 既定 off・通知なし・督促なしで**強める**。
原則5（監視しない）— 抵触リスクあり: group に置いたものが教員に見えると評価に流れうる。
**回避**: 教員は「置かれたもの」を group メンバーとしてしか見られず、
`aggregate_interest_dashboard` / G層 To-Do / 個票のどの経路にも入れない（`trace_registry` の
`teacher_dashboard=False` を維持し、publication は k-匿名集約の入力にしない）。
原則11（fail-closed）— group 所属と本人所有を SQL 内で強制、どちらか欠けたら 404。
原則4 — 件数・被閲覧数を誰にも出さない（置いた本人にも）。原則3 — 撤回は状態遷移。

**規模感: L**（migration 1枚 + API 3本 + 学習UI の新区画 + trace_registry の宣言拡張）

---

### 提案2: 可視性6軸カタログと開示カード — 特に「外部 AI に渡るか」を実装する

**近づける vision**: §5.4（可視性を一軸に畳まない・6軸を独立に扱う）/ 原則8（出所の正直さ）/
原則4 改訂（定義・用途・保持を全当事者が読める形で公開する）/ 原則4「本人の資源・履歴は
本人にだけ」

**現状**: 制度指標については `core/indicator_catalog.py` が定義・宛先・粒度・非利用を宣言し
`GET /api/indicators` で全ロールに公開する、という良い前例がある
（`backend/core/indicator_catalog.py:205-244`）。しかし**可視性そのものには同種のカタログが無い**。
とりわけ第5軸は、学習者の発話が逐語で外部 LLM プロバイダへ渡っている
（`backend/core/tension/input_builder.py:43-46,97` が `role == "learner"` の発話をそのまま
入力に組む）にもかかわらず、`docs/manual/student/` 全文に記述が無く、同意画面も存在しない
（frontend + student マニュアルを `同意` / `consent` / `利用規約` で grep して 0 件）。
IndicatorSpec にも外部転送のフィールドが無い（`:205-221`）。

**なぜギャップが体験を損なうか**: このシステムは「AI は候補まで・確定は人間」「監視しない」
「k=3」を非常に丁寧に学習者へ説明し、その説明で信頼を得ている。その同じ画面で、
発話が第三者企業のモデルへ送られる事実だけが語られない。学習者が後からそれを知ったとき、
壊れるのは1つの機能ではなく**すべての事実文の信用**である（「他も黙っているのでは」）。
原則8 を外向きには徹底し内向きには適用しないという非対称は、この製品で最も高くつく種類の
不正直である。

**機能のかたち**:
- データ: 新テーブル**不要**。宣言は新正本 `backend/core/disclosure_axes.py`
  （`indicator_catalog.py` と同型の純宣言モジュール。FastAPI / sqlalchemy 非 import。
  1オブジェクト種別 × 6軸 の `DisclosureSpec` を持ち、**値は持たない**）。
  本人向けの通過点履歴は既存 `llm_usage_events`（`user_id` / `provider` / `model` /
  `feature` / `occurred_at` を持ち、**本文は持たない**。`backend/db/043_llm_usage_events.sql:14-39`,
  `069_llm_usage_user_index.sql:16`）をそのまま読む → migration **不要**。
- API: `GET /api/disclosure`（全ロール・値なしのカタログ。`GET /api/indicators` と同じ
  `_get_current_user` 依存で、`_require_teacher` にしない）/
  `GET /api/me/ai-touchpoints`（**本人のみ**・自分の user_id 固定・
  「いつ・どの機能で・どのモデルに渡ったか」の事実行のみ・**トークン数と金額は返さない**）。
- UX: ①学習者の「わたしの記録」に第2の区画「外部のモデルに渡った経路」。②各機能の事実文
  1行（例: 「この対話の本文は、回答の生成のために外部のAIモデルへ送られます」）。
  ③`docs/manual/student/01-specification.md` の2表に**第3の表**として6軸を追加し、
  ガードレールで「全 DisclosureSpec の label がマニュアルに逐語で現れること」を固定する
  （`test_indicator_catalog_guardrails.py` に既にある手法の転用）。
- LLM: **無し**。

**14原則との照合**: 原則8 を**強める**（この提案の本体）。原則4 — 本人にだけ本人の履歴、
制度指標との混同を避けるため他者比較・ランキングのキーを持たない。原則6（egocentric）—
全ユーザーの通過点を一覧する画面は作らない（SYSTEM_ADMIN の U層計器は既存のまま）。
**抵触リスク**: 「外部に送られる」と書くことで学習者が萎縮し発話が減る可能性。
**回避**: 事実文のみで警告色・確認ダイアログを付けない（原則12）。opt-out を作らない代わりに、
LLM を通さない経路（HELP ルート・非LLM の DIFF・楽屋）が既に存在することを同じ表で示す。

**規模感: M**（新 core モジュール1本 + API 2本 + UI 区画1 + マニュアル1節 + ガードレール）

---

### 提案3: 保護された異議 — 帰属は台帳に、開示は勾配に

**近づける vision**: §5.3（異議のコストは設計で下げられる。役割化・**帰属記帳と開示の分離**・
ゼミ横断のレビュー）/ 原則14 改訂（「匿名とは台帳に帰属が無いことであり、帰属の開示が
可視性勾配に従って遅れることは匿名ではない」）/ §5.1（弁の後には再審の経路が残る）

**現状**: 学習者の唯一の異議路である地図修正報告は、UI が「あなたの名前とともに記録されます
（匿名にはできません）」と明示し（`frontend/public/js/atlas-report.js:127`）、
`atlas_reports.py` の冒頭も「送信は帰属つき(匿名不可)」と書く（`:9`）。教員には
`reporter_name` が即時に見え（`:79-85`）、凍結時には `report_credits` として名前が出る
（`backend/api/routes/atlas.py:742,864`）。教員が承認した説明・確認した論文配置に対する
学習者の異議路は**存在しない**（`backend/api/routes/learning.py:4178-4226` は読み取りのみ、
学習者向け landscape も同様）。D層 `challenges` は `admin_router` = `_require_teacher` 限定
（`backend/api/routes/doubt.py:1569`）。

**なぜギャップが体験を損なうか**: 単一の指導教員が学位・推薦・研究アクセスを握る環境で
「名前を出さないと異議を言えない」は、実質「異議を言わない」と同義である。§5.3 自身が
「制度構造の上でのみ機能する」と正しく限定しているが、システムが**制度構造の側でできること
（開示の遅延・宛先の選択）を一つもやっていない**ため、コストは丸ごと学習者の側に残っている。
結果として、システムが最も欲しい信号（教員の確定が間違っているという学習者の観察）が、
最も届かない。

**機能のかたち**:
- データ: 既存 `challenges` テーブルを再利用し、①`target_type` に `explanation` /
  `landscape_placement` / `atlas_node` を追加 ②`disclosure ∈ {immediate, deferred_to_freeze,
  via_third_party}` と `disclosed_at` / `disclosure_recipient_id` を追加。
  migration **要**（列追加 + CHECK 拡張、1枚）。**帰属列（`challenger_id`）は常に NOT NULL のまま**
  — 匿名の疑義は作らない（原則14 / §5.1）。
- API: `POST /api/learning/targets/{type}/{id}/challenges`（学習者向け。理由必須・
  `disclosure` を本人が選ぶ）。`deferred_to_freeze` は、凍結・版の発行など**判断が終わった後に**
  提出者名を教員へ開示する（それまで教員には内容と「学習者からの異議1件」だけが見える）。
  `via_third_party` は同 group の別の教員 / SYSTEM_ADMIN を宛先にする
  （`notification_recipients.py` の既存 JOIN プリミティブに相乗り）。
- UX: 承認済み説明・確認済み配置のカードに「ここは違うと思う」。押すと**開示の選び方が
  事実文で並ぶ**（「いますぐ名前を伝える / 判断が終わってから名前を伝える / 別の先生に伝える」）。
  §5.3 の限界も併記する（「この選択はシステムの中でだけ働きます」）。
- LLM: **無し**（同期・非LLM。原則9）。

**14原則との照合**: 原則14 の改訂文そのものの実装で、**強める**。原則5 — 学習者の異議は
「観察されたもの」ではなく「本人が出したもの」なので監視ではない。ただし**異議の件数を
学習者ごとに数えられる状態を作らない**（原則4「学習者個人の切断された回数は測ってはならない」に
直撃するため、集計 API を作らず、`GET /api/indicators` にも指標を追加しない）。
原則11 — 対象への閲覧権が無ければ 404。原則3 — withdraw は状態遷移（既存 `challenges` の
挙動を継承）。**抵触リスク**: 開示の遅延が「隠れた告発」に見えうる。**回避**: 遅延は最大でも
「次の凍結・版発行まで」に限り、無期限にしない。台帳には最初から帰属が入っている旨を
提出者にも教員にも明示する。

**規模感: M**（migration 1枚 + API 1本 + 学習UI のカード操作 + 通知の宛先合成）

---

### 提案4: 出口の弁 — export に対象権限・来歴・監査を付ける

**近づける vision**: §1.1（他者が検査・再解釈・再審できる**来歴付きの主張**として共有される）/
原則11（fail-closed を「公開・外部 index・研究転用にも適用」）/ 原則14（監査必須）/
原則8（一括承認の来歴を偽らない）

**現状 [事実]**: `POST /api/courses/{id}/export-bundle` と
`POST /api/documents/{id}/export-bundle` は `Depends(_require_teacher)` のみで、
`_load_course` / `_load_document` は所有者・共有関係を一切見ない
（`backend/api/routes/export.py:2828-2846, 2992-3005`）。`docs/features/auth-visibility.md` §4.5 が
挙げる「オブジェクトスコープの権限を適用済みのエンドポイント」表にも入っていない。
バンドルには PDF 逐語の evidence スニペットと（オプションで）LLM 生出力が入る
（`:2640-2663`）。`record_review_event` の呼び出しは export.py に無い（grep 0 件）。
比較対象として、**本文を含まず仮名化された** discuss 観測ダンプは SYSTEM_ADMIN 限定かつ
監査記帳付きである（`backend/api/routes/discuss_observation.py:118-148`）。
権限を検証するテストも見当たらない（`backend/tests/test_export_bundle.py` に 403/404 の
権限系アサーションが無い）。

**なぜギャップが体験を損なうか**: これは体験の問題であると同時に、システムが自分の原則を
守れていない一点である。UI 上の名前が「**外部レビュー用に**書き出し」であることが致命的で、
この経路の出力は定義上システムの外へ出て行き、戻ってこない。誰が何を持ち出したかの記録が
無ければ、後から「その主張はどの版に基づくのか」も「誰が持ち出したのか」も再構成できず、
§1.1 の「来歴付きの主張」が出口の一歩手前で断ち切られる。持ち出した側にとっても、
承認者・版・根拠が付いていない ZIP は**ゼミや論文で使えない**（引用できない）ので、
現状この出口は誰の役にも立っていない可能性が高い [推測]。

**機能のかたち**:
- 権限: 両 endpoint を既存の `_require_editable_document_or_404` /
  `_require_editable_course_or_404` に通す（`docs/features/auth-visibility.md` §4.5 の
  共通ゲートをそのまま使う。**新しい判定を書かない**）。不在と権限なしを同じ 404 に畳む。
  **副作用（DB 読み・ZIP 生成）より先に認可する**。
- 来歴: `manifest.json` に `provenance` ブロックを追加 —
  ①`shared_release`（`release_id` / `version_no` / `published_by` / `created_at`。
  `core/versioning/releases.py::get_state` を読むだけ）②`approvals`（含まれる component /
  claim / explanation の `review_status` と承認者の表示名・承認日。C層の既存ビュー
  `component_explanation_endorsement_summary` と `theory_review_events` を読む）
  ③`skeleton_version`（配置が入る場合）④`decision_context` の参照（一括確定を経た項目）。
  **数値スコアは載せない**（原則4）。README.md（現在は英語の機械可読仕様）に
  「この束の主張は誰がいつ確定したか」の日本語1節を足す。
- 監査: `record_review_event(AUDIT_ENTITY_..., action="exported", ...)` を
  discuss ダンプと同型で記帳（scope / options / 対象 id）。**新 entity_type を作らず**
  既存カタログ定数（`AUDIT_ENTITY_COURSE` 相当）に相乗りする。
- migration **不要**。LLM **無し**。

**14原則との照合**: 原則11・14 を**強める**（現在の唯一の穴を塞ぐ）。原則12 — 教員の
操作を増やさない（ボタンは同じ、通るゲートが増えるだけ。§5.1「弁の収支」に反しない）。
**抵触リスク**: 既存の運用で「他人のコースを書き出していた」教員が 404 になる。
**回避**: 権限外は 404 にしつつ、UI 側で「このコースの所有者または共有先の編集者のみ
書き出せます」の事実文を出す（存在は漏らさない）。

**規模感: S/M**（endpoint 2本のゲート差し込み + manifest 1ブロック + 監査1行 + テスト）

---

### 提案5: 引用のかたち — 安定ハンドルと来歴付きの引用書き出し

**近づける vision**: §1.1（権威ある完成品ではなく、他者が**検査・再解釈・再審できる**来歴付きの
主張）/ §5.3（引用は帰属付きで記録され、版で保護される）/ 原則8

**現状 [事実]**: 引用は**システム内部にしか存在しない**。`component_citations` は
「どのコースがどの説明を引用したか」を帰属＋版固定で記録するが（`backend/api/routes/
theory_components.py:4286-4300`, `docs/features/endorsement-sharing.md` §3）、
その引用を**外の文書で書ける形にする経路が無い**。BibTeX / RIS / CSL-JSON の生成は 0 件、
印刷スタイルも 0 件、承認済み説明・claim・図・グラフの単体書き出しも 0 件。
V層は `version_no` と `published_by` を持つのに（`core/versioning/releases.py:116-207`）、
その版を指す外部から解決可能な識別子が無い。

**なぜギャップが体験を損なうか**: 研究の入り口に立つ学習者と教員の実際の出口は、
ゼミのスライド・輪講のメモ・論文の脚注である。そこへ持ち出すとき、いま使えるのは
「画面をコピペする」だけで、**コピペした瞬間に来歴が全部落ちる**。落ちた来歴は復元できない
ので、「この一文は誰が確認したどの版の主張か」を後から誰も再審できない。
§1.1 が「完成品ではなく再審できる主張」と言っている当のものが、出口で完成品（ただの文）に
戻ってしまう。逆に言えば、来歴を持ったまま外へ出る一片があれば、このシステムの主張は
Zotero や LaTeX の中でも episteme-graph の主張であり続ける。

**機能のかたち**:
- データ: 新テーブル**不要**。安定ハンドルは既存 id と版から決定論導出
  （`eg:{instance_key}/{object_type}/{id}@v{version_no}`。`instance_key` は env の1値）。
  migration **不要**。
- API: `GET /api/admin/citations/{object_type}/{id}?format=bibtex|csl-json|markdown|latex&version=`
  （TEACHER・対象の閲覧権を既存ゲートで確認）と、学習者向けは
  `GET /api/learning/courses/{cid}/citations/{...}?format=markdown`（**受講ゲート + コース
  sources のみ**。既存 `source-chunk` と同じスコープ規律）。出力は必ず ①元論文の書誌
  （`document_boundary` の `authors` / `documents.source_url` から。**取れないものは
  取れないと書く**。捏造しない）②episteme-graph 側の来歴（承認者・承認日・版・骨格版）
  ③根拠の逐語引用1つ ④「AI推定（未確認）」等の出所ラベルを、フォーマット固有の
  note フィールドに載せる。
- UX: 学習者の出典タブ、教員の説明カード・グラフレビューのノード詳細に「引用をコピー」。
  押すと**来歴込みのテキストがクリップボードに入る**（1クリック・保存なし・監査なし＝
  読み取りのみ）。
- LLM: **無し**（決定論の整形のみ。原則9）。
- 既存正本への積み方: 出所ラベルは `core/label_vocab.py` の既存表から引く
  （**新しい訳語表を作らない**）。書誌の欠落は `label_vocab` の事実文で埋める。

**14原則との照合**: 原則8 を**強める**（外に出ても出所ラベルが剥がれない）。
原則2（evidence-based）— 引用に必ず逐語根拠を1つ同梱。原則4 — 承認人数は段階ラベルで、
数値を書かない。**抵触リスク**: 引用形式が「権威ある完成品」の見た目を与える。
**回避**: 未確認・AI推定の対象は**引用ハンドルを発行しない**か、note に必ず
「AIによる推定（未確認）」を含める（剥がせない位置に置く）。原則11 — 版が未発行なら
`@vHEAD` ではなく「未発行のため引用できません」で 422（fail-closed）。

**規模感: M**（API 2本 + フォーマッタ + UI ボタン数箇所 + マニュアル）

---

### 提案6: 来歴付きの越境 — 可搬発行版と、取り込みの弁

**近づける vision**: §5.3（versioned な共有が消費側を保護する）/ §1.1（他者が再審できる主張
として共有）/ 原則1（確定は受け側の人間）/ 原則8（入口の正直さ — §6.2 の第15原則候補と同根）

**現状 [事実]**: V層は同一インスタンス内に閉じている（`backend/api/routes/versioning.py` の
全 endpoint が `_require_teacher`、対象は DB 内の `object_id`）。取り込み側の API は
存在しない（routes 全体に import 系 0 件）。骨格・カートリッジのファイル配布
（`backend/atlas_domains/` / `backend/cartridges/`）だけが実質の越境路だが、これは
デプロイ作業で、誰が凍結したかの来歴も、受け側での確定操作も伴わない。

**なぜギャップが体験を損なうか**: 「重なるが同じではない理解の並存」は、同じ研究室の中では
説明バージョンとして実装されているのに、**研究室が違うと一切重ならない**。別の研究室が
同じ論文を別の観点で配置した、という最も情報量の多い並存が、構造的に見えない。
また、越境路が無いことで、このシステムは「一つの研究室の閉じた台帳」に固定される —
§1.1 の「他者が検査・再解釈・再審できる」の他者が、事実上 group の中の人だけになる。

**機能のかたち**:
- 出す側: 提案4 の manifest 拡張の上に「可搬発行版」を作る。release snapshot +
  `origin`（`instance_key` / `published_by` の表示名 / `version_no` / `created_at`）+
  骨格版 + 根拠の逐語 quote を1ファイル。**署名（PKI）は v1 でやらない** — 検証できない
  署名は出所の正直さを損なう。代わりに「この束は受け側で検証されていません」を
  ファイル自身に書く。
- 入る側: `POST /api/admin/shared/import`（TEACHER・**取り込みの弁**）。取り込んだ全項目は
  `status='candidate'` / `review_status='review_required'` で着地し、`imported_from`
  （origin instance / version_no / 取り込んだ教員 / 取り込み日時）を保持する。
  **受け側の教員が確定するまで、学習者にも RAG にも出ない**（原則1・原則11）。
  データ: 既存の候補テーブル群に `imported_from JSONB` を足すのではなく、
  新テーブル `imported_releases`（1取り込み = 1行 + 生スナップショット）+ 既存の
  `core/candidate_flow.py` で確定フローに乗せる。migration **要**（1枚）。
- UX: 教員の「共有版」モーダルに「書き出す（他の研究室へ）」/「取り込む」。取り込み画面は
  **並置**が既定（既存のものと置き換えない = 原則7 リンクであってマージではない）。
- LLM: **無し**。

**14原則との照合**: 原則1・7・8・11 を**強める**。**抵触リスク**: 取り込みが
「別の研究室の権威を持ち込む」経路になり、受け側の確定がゴム印化しうる（原則1 改訂の
「ゴム印にしない条件」）。**回避**: 取り込み項目は個別に確定させ、一括承認の経路を
用意しない（`decision_context` を必須にするか、そもそも bulk を作らない）。
原則3 — 取り込んだが却下したものも状態遷移で残す。**§6.2「弁の収支」への配慮**:
新しい確定行為が増えるので、取り込みは「必要な分だけ取り込む」（対象を選んで取り込む）
形にし、束ごと取り込んで全部レビューさせない。

**規模感: L**（migration 1枚 + import API + 並置 UI + candidate_flow 接続 + ガードレール）

---

### 提案7: 砂場の着地 — 持ち出せる判断の自己編纂

**近づける vision**: §1.2（研究職へ向かわない学習者を外に置かない。砂場で完結する学習は
未完ではなく**正規の経路**。目的は分野の外にも持ち出せる判断能力）/ 原則4（本人の履歴は
本人にだけ）/ 原則12（押し付けない）

**現状 [事実]**: 修了カードは事実のみ（`docs/manual/student/02-student.md:797-810`）、
「わたしの記録」は全痕跡の新しい順の羅列＋JSON 持ち出し
（`backend/api/routes/my_records.py:43-79`）。学習者から見える「先」は、コース修了・
論文の海・地図の端のいずれも**外へ広がる方向**にラベルがある。
「ここまでを自分は持てるようになった」を本人の言葉で確定し、外へ持って出る場所は無い。

**なぜギャップが体験を損なうか**: 砂場が正規の経路であることをビジョンは宣言しているのに、
UX には砂場の**終わり方**が無い。羅列は「まだ整理されていない」ように見え、外向きの導線は
「まだ先がある」ように見える。両方合わせると、公開へ進まない学習者の画面には
恒常的に「未完」の顔が残る — §1.2 が最も避けたかった見え方が、督促を一切していないのに
情報設計だけで発生している。しかも本人の判断能力は誰にも（本人にも）可視化されないので、
研究へ行かない学習者がこのシステムから持って出られるのは JSON ファイル1つになる。

**機能のかたち**:
- データ: 新テーブル**不要**。編纂の対象は既存の本人痕跡（`interest_traces` の
  `articulated` / `confirmed` 系 + `learner_reconstructions` の終端 match + `intention` の
  書き置き）。選択の保存が要るなら `interest_traces` に kind `portfolio_pick` を1つ追加
  （`trace_registry` に露出3宣言を書く。3つとも False = 本人のみ）。migration **不要**。
- API: `GET /api/me/portfolio`（本人のみ・編纂候補を系統別に返す）/
  `GET /api/me/portfolio/export?format=markdown`（本人が選んだものだけを、本人の言葉と
  対応する論文・分野の地図の位置を添えて1枚に。**採点・件数・進捗率・所要時間を一切
  含めない**）。
- UX: 「わたしの記録」に第3の区画「持って出るものを選ぶ」。既定は何も選ばれていない。
  **コース修了時に自動で開かない**（原則12）。修了カードには「他のコースを見る」と
  並べて「ここまでをまとめる」を**同じ重さで**置く（外向きだけの導線をやめる）。
  文面は事実のみ（「あなたが自分の言葉で書いた記録が N 件あります」ではなく
  「あなたが自分の言葉で書いた記録を選べます」— 件数は出さない）。
- LLM: **無し**が既定。要約・清書の AI は入れない（本人の言葉のままであることが
  「持ち出せる判断」の実体。§3 の ELICIT-first と同じ理由）。

**14原則との照合**: 原則4・12 を**強める**。原則5 — 教員へは一切渡らない（このポートフォリオを
教員が見る API を作らない。`trace_registry` の `teacher_dashboard=False` を構造で固定）。
**抵触リスク**: 「まとめる」がゲーミフィケーション・達成演出に滑る（原則12 / §3.5 演技化）。
**回避**: バッジ・トロフィー・完成度・スタンプを一切置かず、出力は本人の文の羅列＋出所のみ。
提案1（引き受けの段）と接続する場合も、ポートフォリオから group への公開を**既定にしない**。

**規模感: M**（API 2本 + フォーマッタ + 学習UI の区画1 + 修了カードの導線1 + マニュアル1節）

---

## 3. 見送った案

### A. 学習者どうしの相互フィード（ゼミの共有タイムライン既定 on）

「同じゼミの人が今どこで引っかかっているか」を既定で流す案。**見送り理由**: 原則5（監視しない）
と §3.5（演技化させない）に正面から抵触する。見られていることが常態になると、産出は
「他人に見せる用の産出」へ変質し、砂場の保護（撤回しても何も失わない）が消える。
提案1 が扱うのは**本人が一つずつ選んで置く**形で、これは自動的に流れる形とは別物。
バッジ・未読数・通知を持たせない点も提案1 で明示している。

### B. 匿名の異議チャネル

学習者が名前を伏せて教員の確定に異議を出せる窓口。**見送り理由**: 原則14（帰属必須。
「匿名の疑義・匿名の承認は存在しない」— §5.1）に真正面から抵触する。原則14 の改訂文は
まさにこの誘惑への回答として「帰属の開示が可視性勾配に従って遅れることは匿名ではない」と
書いており、正しい実装は匿名化ではなく**開示の遅延・宛先の選択**である。提案3 がそれ。

### C. 公開ポータル / DOI 発行 / 外部 index への露出

承認済み claim・説明をインスタンス外に恒久公開し、外部検索から引ける状態にする案。
**見送り理由**: ①§5.2「未検証の産出物に本人の名前が付いて public に永続することを、既定に
しない」に抵触しやすく、既定を閉じたまま安全に運用する制度（撤回・訂正・異議の受付窓口）が
まだ無い ②§5.3 が言う「開放は読む側と砂場、成員資格は確定側」に照らすと、public 化は
確定側の成員資格の問題であってシステムの機能では決まらない ③現状では出口の権限ゲートすら
無い（提案4）ので、順序として先に閉じるべき。提案6（研究室間の**指名した相手への**来歴付き
交換）で、越境の必要は満たせる。

### D. 学習者の異議・貢献の集計指標（誰が何件異議を出したか）

提案3 の副産物として作りたくなる指標。**見送り理由**: 原則4 改訂の
「学習者個人の産出量・**切断された回数**は測ってはならない」に直撃する。§6.1 が
「測れないが書く」（学習者が共同体の基準を疑えるようになったか・異議を受けて壊れなかったか）
として明示的に代理指標を禁じている領域であり、提案3 では意図的に集計 API を作らず
`GET /api/indicators` にも登録しない設計にした。

---

## 付記: 調査中に見つかった、提案以前の不整合

1. **`export-bundle` にオブジェクトスコープの権限が無い** — `backend/api/routes/export.py:2832,
   2996` は `_require_teacher` のみで、`docs/features/auth-visibility.md` §4.5 の
   「ID 直指定エンドポイントは対象オブジェクトへの権限をサーバ側で確認する」規約から漏れている。
   PDF 逐語引用と LLM 生出力を含む ZIP が、同一インスタンスの任意の教員に対して開いている。
   監査記帳も無い。→ 提案4。
   **→ 2026-09-10 解消**（実装先: `backend/api/routes/export.py` の
   `_require_viewable_course_or_404` / `_require_viewable_document_or_404` +
   `manifest.provenance` + `entity_type='export'` の監査記帳。境界は提案4 が挙げた
   編集権ではなく**閲覧権**を採った — 束の中身が既存の閲覧 API で読める範囲と同じで、
   「読めるのに書き出せない」非対称を作らないため。理由は
   `docs/features/auth-visibility.md` §4.5 に記載。ガードレールは
   `backend/tests/test_export_governance.py`）。
2. **学習者向けドキュメントに外部 LLM への転送の記述が無い** — `docs/manual/student/` を
   `OpenAI` / `外部` / `同意` / `利用規約` で grep して該当 0 件。指標カタログでは
   「何が数えられるか」を極めて丁寧に開示している一方で、「本文がどこへ行くか」だけが空白。
   → 提案2。
3. **`GET /api/groups/{group_id}` がメンバーの email を全メンバーに返す**
   （`backend/api/routes/groups.py:204-227`）。学習者どうしが互いのメールアドレスを見られる
   唯一の経路で、可視性の第2軸（名前を見られる audience）としての宣言も、マニュアルの
   記述も無い。→ 提案2 のカタログ対象。
   **→ 2026-09-10 解消**（実装先: `backend/api/routes/groups.py::get_group_detail` —
   email はグループ admin / SYSTEM_ADMIN にのみ返し、一般メンバーには表示名・ロール・
   参加日のみ。管理 UI（`frontend/public/js/admin.js`）はサーバがメールを返した場合だけ
   列を出す。規約は `docs/features/auth-visibility.md` §4、ガードレールは
   `backend/tests/test_export_governance.py::TestGroupMemberEmailDisclosure`）。
