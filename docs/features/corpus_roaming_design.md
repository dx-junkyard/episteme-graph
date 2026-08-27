# コーパス回遊層（コース無し論文議論・コーパス地図・地図の端）

> **状態: 実装済み（正本・凍結）**（2026-08-27 起票・同日 Phase A〜D 実装。migration は
> **073** `corpus_roaming_search_state`（外の輪の1列のみ）で採番済み。論文ディスカバリー層の
> Phase 4 / v2 構想（`paper_discovery_design.md` §7）と discuss モードの Phase 3 予約
> （`discussion_mode_design.md`）を引き受けた専用設計書。以後は §12 実装記録のみ追記する）

**正本**: 本ドキュメント。
**親文書**: [論文ディスカバリー層](paper_discovery_design.md)（§7 が本書の起点。PD1〜PD8 を
継承）/ [「論文と話す」discuss モード](discussion_mode_design.md)（Phase 3 = document
直付け入口の予約を本書が引き受ける。DM1〜DM8 を継承）。
**関連**: [知識ランドスケープ](knowledge_landscape_design.md)（LS1〜LS10。§12 の Phase 2〜4
のうち最小スライスだけを本書が使う）/ [カテゴリギャップ候補](category_gap_candidates_design.md)
（縁の信号源 `landscape_gap_signals`）/ [広がり装置](personal_map_curiosity_design.md)
（好奇心の文法の正本）/ [discuss 観測基盤](discuss_observation_design.md)（着手判断の実測）/
[痕跡kind登録簿](trace_registry_sovereignty_ledger_design.md)（Phase D の新 kind 宣言先）。

---

## 1. 目的 — 育てたコーパスを、コースの外から歩けるようにする

論文ディスカバリー層（Phase 1〜3）で、教員がコーパスを継続的に育てる仕組みは揃った。
しかし学習者の入口は依然として**コースだけ**である。教員がコースに編むまで、取り込まれ
配置され承認された論文は学習者から見えない。

本層は、学習者に次の3つを与える。

1. **コーパス地図（Phase A）** — 分野の地図（atlas 骨格）の上に、閲覧可能な論文が
   浮かんでいる全体ビュー。コース非依存。
2. **コース無し論文議論（Phase B）** — 地図や論文リストから1本を選び、コースを経由せず
   その論文と議論する（discuss モードの document 直付け）。
3. **地図の端（Phase C/D）** — 歩いて端に到達したとき、その先に何が「ある」かが事実として
   見える。縁（取り込み済みだが地図に置けなかった主題）と外（教員の検索条件に一致する
   未取り込み論文の存在）。端への関心は本人の明示操作でのみ記録され、k-匿名集約で教員に
   届き、教員の承認で地図が育つ — ディスカバリー層の成長ループに**学習者の好奇心という
   駆動力**を接続する。

体験の原型は既存の語彙で言い尽くせる: 地図は投影（LS1）、霧は名前だけ（広がり装置）、
晴れ間は発見の候補地（SL1）、取り込みの弁は教員（PD1）。本層は新しい思想を発明せず、
既存の文法をコース外の空間に延長する。

---

## 2. 不変条項（CR1〜CR10）

| ID | 条項 | 意味 |
|---|---|---|
| **CR1** | **document 可視性が唯一のゲート（fail-closed）** | コース非依存の全ビュー・全議論は `user_can_view_document` / `list_visible_document_ids`（所有 / public / group / object_group_permissions）を通す。コース受講ゲートの代替であって緩和ではない。private 論文はコーパス地図にも議論にも一切出ない。 |
| **CR2** | **既存のコース学習を壊さない** | コース体験（受講・チャット・レクチャー・地図・版解決）の API・挙動は非改変。回遊はサイドバーに**並置される別の入口**であり、既存入口の置き換え・自動遷移をしない。 |
| **CR3** | **数値を見せない・地図は投影** | LS 系を継承: weight・confidence・件数・類似度を学習者に出さない。配置には出所ラベル（「AIによる推定（未確認）」「教員確認済み」）を必ず付ける。踏破率・カバー率を作らない。 |
| **CR4** | **閉世界の正直さ** | 縁は「このコーパスの中では地図に置かれていない」、外は「教員の検索条件（時点付き）では」しか言わない。「この分野にはまだ論文がない」「世界初」と読める表示を構造的に禁止（SL1 の同族。denylist をガードレール化）。 |
| **CR5** | **好奇心の文法 — 存在だけを見せ、詳細は明示操作まで伏せる** | 端・霧・未読論文はバッジ・督促・自動表示にしない。開いたときに見えるだけ（PD8 / G4 の同族）。ポーリング禁止。 |
| **CR6** | **学習者を監視しない** | 端への関心は本人の**明示タップのみ**を記録する（閲覧・滞在の暗黙計測を関心として扱わない）。教員へは k-匿名集約（k=3・`core/privacy.py` 正本・レンジ表示）のみ。個人の回遊履歴を教員に見せない。評価利用禁止（P3 継承）。 |
| **CR7** | **学習者起点で外部 API を呼ばない** | arXiv / Semantic Scholar への到達は教員操作起点のみ（PD7 の延長）。外の輪は教員の最終検索が残した事実から読み時導出し、学習者のアクセスが外部リクエストを発生させない。 |
| **CR8** | **情報を落とさない** | 関心記録・議論履歴は status 遷移で保持。行削除 API を作らない（P4）。 |
| **CR9** | **同期パスに LLM を入れない** | 地図・論文リスト・端の導出は非LLM・読み時導出。LLM が動くのは議論の応答（既存 discuss の1コール/往復）だけで、既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY` に相乗りする。 |
| **CR10** | **取り込みの弁は教員のまま** | 学習者の関心信号は需要の**提示**であって、取り込み・購読変更・骨格変更の自動トリガーにしない（PD1 / KN-3 の継承）。 |

---

## 3. 全体像と段階分け

```
[Phase A] コーパス地図     学習UI「論文の海」→ 凍結骨格 + 可視論文の配置（読み時導出）
     ↓ 論文を選ぶ
[Phase B] コース無し議論   document 直付け discuss（会話キーはセンチネル・migration 0）
     ↓ 歩いて端に着く
[Phase C] 地図の端         縁 = gap signals の学習者向け事実文 / 外 = 購読の最終検索事実
     ↓ 「この先を知りたい」（明示タップ）
[Phase D] 関心信号         k-匿名集約 → 教員のディスカバリーモーダルに表示 → 承認 → 地図が育つ
```

| Phase | 内容 | 依存 |
|---|---|---|
| **A** | コーパス地図 + 論文リスト（学習者・コース非依存） | なし |
| **B** | コース無し論文議論（discuss document 直付け） | なし（A と独立に価値がある） |
| **C** | 地図の端（縁・外の事実文表示） | A（表示面が地図） |
| **D** | 関心信号（明示タップ → k-匿名 → 教員表示） | C |

推奨実装順は A → B → C → D。A/B は並行可。

**着手前チェック（discuss 観測ゲート）**: `discussion_mode_design.md` は Phase 3 の着手判断
材料を discuss 観測基盤の実測と定めている。Phase B 着手前に
`GET /api/admin/discuss/observation-status` の実測（discuss の利用実態・着地到達率）を
オーナーが確認する。**オーナーの明示指示は本ゲートの裁定として優先する**（実測が乏しい
段階での着手判断もオーナーの権限。確認した事実を §実装記録に残す）。

---

## 4. Phase A — コーパス地図（論文の海）

### 4.1 API（読み取り専用・非LLM）

- `GET /api/learning/corpus/domains` — 凍結骨格を持つ active ドメインの一覧
  （domain_key / 表示名 / 「閲覧できる論文が配置されているか」の bool。**件数は返さない**）。
- `GET /api/learning/corpus/landscape?domain_key=` — 凍結骨格（`atlas_store.
  load_learner_skeleton`）+ その骨格アンカーへの `landscape_placements`（status ∈
  confirmed / inferred / review_required — 学習者向け既存投影と同じ語彙・同じ非漏洩規則）を、
  **`list_visible_document_ids(user_id)` と交差させて**返す。骨格なしは 404（地図領域ごと
  非表示 — atlas の fail-closed 流儀）。course スコープ版（`GET /api/learning/courses/{id}/
  landscape`）は非改変で並置。
- `GET /api/learning/corpus/documents?domain_key=` — 可視論文のリスト（title / authors /
  year / 配置済みか / 出所ラベル / 議論入口の可否）。ソートは新しい順のみ（数値スコアなし）。

実装は `backend/core/corpus_view.py`（FastAPI 非 import・読み時導出・保存物なし）+
`backend/api/routes/corpus.py`（main.py 直接登録・認証は学習者本人）。可視性交差は SQL 内
`ANY(:doc_ids)` で強制する（P0 是正と同じ形。route 層のフィルタ後付けにしない）。

### 4.2 UI

- 学習画面サイドバーに常設ボタン「**論文の海**」（コース選択と無関係に押せる）。
  既存 atlas オーバーレイ + `landscape-layer.js` の描画資産を**コース非依存の getData** で
  再利用する全画面ビュー。fail-closed（API 失敗・骨格なし = 何も出さない）。
- 論文ノードをタップ → 詳細パネル（タイトル・著者・出所ラベル・配置観点）→
  「この論文と議論する」（Phase B へ）/「本文の出典を見る」。
- knowledge_landscape_design §12 の EmergentRegion / コーパス別地図 / MapSnapshot は
  **使わない**（既存凍結骨格上への配置表示という最小スライスのみ。§12 の本格版はあちらの
  設計書の管轄のまま）。

---

## 5. Phase B — コース無し論文議論（discuss document 直付け）

### 5.1 会話コンテキスト（migration 0）

`learning_chat_history` は `(user_id, course_id TEXT, topic_id TEXT)` キーで FK が無い。
document 直付け会話は**予約センチネル** `course_id = "_doc:{document_id}"` +
`topic_id = "_discussion"`（既存の疑似トピック）で保存する。新テーブル・新会話機構を
発明しない（UCサイクルの「`_discussion` に載せる」判断のコース外延長）。

- センチネルの正本は `backend/core/discuss/` に置く:
  `DOCUMENT_CONTEXT_PREFIX = "_doc:"` / `document_context_id(document_id)` /
  `parse_document_context(course_id) -> str | None`。**他所で文字列組み立てをしない**。
- センチネル course_id は実在コースと衝突しない（コース id は UUID 由来）。
  `get_accessible_course_data` 等のコース解決に流れ込まないよう、入口で
  `parse_document_context` を最初に判定する。

### 5.2 ゲートとスコープ

- アクセスゲートは `user_can_view_document(user_id, document_ref)`（CR1）。
  受講ゲートの一切を経由しない。
- RAG スコープは**当該 document のみ**（`search_chunks_with_metadata(...,
  allowed_document_ids={doc})` — 既存の必須キーワード引数で SQL 内強制）。
  `discuss_scope='all_visible'` は本人可視集合（`list_visible_document_ids`）。
  該当チャンクゼロでの無断フォールバック禁止（DM1）は不変。
- コスト: 既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY` に相乗り（専用上限なし — discuss 本体の
  裁定 #9 と同じ）。U層 feature は `learning:chat_discuss` を流用し、観測イベント側で
  document 直付けを区別する（§5.5）。

### 5.3 API

- `GET /api/learning/documents/{ref}/discuss/opening` — 既存 `build_opening(
  document_context_id(doc), [doc_id], course_focus="")` を呼ぶだけ（開幕投影・
  discussion_seeds は元々 document 単位の artifact 由来なのでそのまま出る。LLM 0回）。
- `POST /api/learning/documents/{ref}/discuss/chat` — 実体は既存 `learning_chat` の
  discuss 経路との共通化。course_data 解決部を document ゲートに差し替えるファサードとし、
  discuss が元々バイパスする枝（意図分類・前提知識ゲート・detour 化）はそのまま。
  応答様式（学術ディスカッション調・歩調合わせ・末尾の必須問い = DA1〜DA6）・
  書き直し/削除（truncate セマンティクス）・tension プレフィルタは共通部をそのまま通す。
- 履歴取得・削除系も同じセンチネルキーで既存 API 形に合わせる。

### 5.4 縮退の正直な列挙（v1 で提供しないもの）

course 前提の派生機能は document 直付けセッションでは動かない。**黙って壊すのではなく、
仕様として提供しない**と明記する:

- tension / anchors の digest・confirm 導線（API が course 配下のため）。ただし
  `interest_traces` への記録自体は行われる（context はセンチネル。worker はコース非依存に
  動作するため候補は生成され、本人確定の入口が無いだけ — 「問いの軌跡」には
  「論文との議論（コース外）」ラベルで表示する）。
- 着地画面（landing candidates / reflection / 再構成プローブ — いずれも course 配下）。
  v1 の document 直付けは素の議論 + 開幕のみ。
- 版解決（V層）は document 成果物に対して従来どおり（コースの版ピンは無関係）。

これらの解禁は運用実測後の v2 判断（§9 非スコープ）。

### 5.5 観測

`discuss_metric_events`（DO1〜DO6 継承・本文非含有・payload は最小）に
`document_discuss_opened` / `document_discuss_turn` の2語彙を追加し、コース discuss と
分離集計できるようにする。学習者に数値を見せない・削除 API なしは既存のまま。

---

## 6. Phase C — 地図の端

コーパス地図（Phase A）のオーバーレイに、端の2つの輪を**事実文**として足す。

### 6.1 縁 — 取り込み済みだが地図に置けなかった主題

- 源泉は `landscape_gap_signals`（migration 066・active 信号）。domain / parent_region 単位に
  読み時集約し、該当領域の詳細パネルに1行:
  「**この領域の先に、まだ地図に置かれていない主題を扱う論文があります。**」
- 支持論文のタイトルは**本人が閲覧可能な document のみ**列挙（CR1）。件数・バッジは
  出さない（LS5 の学習者版）。教員のレビュー候補・判断（`atlas_gap_decisions`）は
  一切出さない（カテゴリギャップ設計 §5.6 の学習者非開示を維持 — 学習者に見せるのは
  「置けなかった論文が存在する」という事実だけで、共有候補の審議状況ではない）。

### 6.2 外 — 教員の検索条件に一致する未取り込み論文の存在

- 学習者起点で arXiv を呼ばない（CR7）ため、**教員の最終検索が残した事実**から導出する。
  migration（実装時採番）で `paper_discovery_subscriptions` に列を1つ足す:
  `last_search_found_new BOOLEAN`（教員が `POST /search` を実行するたびに
  「status='new' の候補が1件以上あったか」を上書き。候補スナップショットは持たない —
  PD5 と両立する**集約1ビット**のみ）。
- 表示（骨格を持つドメインの地図の外周部 / ドメイン詳細）:
  「**教員の検索条件では、まだ取り込まれていない論文が arXiv にありました（{日付} 時点）。**」
  （`last_checked_at` を併記。false / NULL / 購読なしのときは行ごと出さない）。
- 「この検索条件では」の限定を落とさない（CR4）。タイトル・件数は出さない
  （存在だけ — 好奇心の文法）。

---

## 7. Phase D — 関心信号（端から教員への需要）

- 端の事実文カードに任意の1タップ「**この先を知りたい**」を置く。押したときだけ
  `interest_traces` に kind **`frontier_interest`** で記録する
  （payload: `domain_key` / `region_id?` / `ring: "fringe"|"outer"`。本文・質問文なし）。
  取り消しは status 遷移（CR8）。
- **trace_registry への宣言が必須**: 露出3宣言 = 問いの軌跡に出さない（発話ではない）/
  教員向けは k-匿名集約のみ / わたしの地図に出さない。「わたしの記録」（主権台帳）には出す。
  ガードレール `test_trace_registry_guardrails.py` が消費面と一致することを固定する。
- 教員向け表示は**既存ディスカバリーモーダルの中**（新しいダッシュボードを作らない）:
  分野・領域単位の1行「学習者の関心: {レンジ}」（`core/privacy.py` の k=3・
  レンジ表示 3-5 / 6-10 / 11+。n<3 は非表示）。個人・時系列・順位を出さない。
- 信号は購読条件・取り込み・骨格の何も自動変更しない（CR10）。教員がそれを見て検索・
  承認するかは教員の判断。

---

## 8. DB・コスト・監査のまとめ

- **migration（実装時採番・シードなし・冪等）**: `paper_discovery_subscriptions.
  last_search_found_new BOOLEAN` の1列のみ（Phase C）。Phase A/B/D は migration 0
  （読み時導出・センチネル・interest_traces 相乗り）。
- **LLM**: Phase B の議論応答のみ（既存 CostGate 相乗り）。A/C/D は LLM 0回・embedding 0回。
- **監査**: 学習者本人の回遊・関心タップは監査記帳しない（本人行動の記帳は観察面の拡大 —
  主権台帳 v1 と同じ判断）。教員側の変更操作は本層には無い。

---

## 9. 非スコープ（v1）

- EmergentRegion・コーパス別地図・MapSnapshot（knowledge_landscape_design §12 の本格版）
- document 直付けセッションでの tension/anchor confirm・着地画面・再構成（§5.4 の解禁は v2）
- 学習者への引用グラフ表示・未取り込み候補のタイトル開示（外の輪は存在のみ）
- 学習者の配置異議・地図編集（LS の予約どおり）
- G層 next_steps ルール・通知・バッジ（CR5。運用実測後）
- 関心信号にもとづく自動検索・自動取り込み（CR10 で恒久禁止）
- モバイル最適化・公開範囲の既定変更（大量取り込み論文を public にする運用は本層の
  前提だが、可視性の既定・一括変更ツールは別マター）

---

## 10. ガードレール（実装時に `test_corpus_roaming_*.py` として固定する項目）

- `core/corpus_view.py` が FastAPI / LLM 非 import・可視性交差が SQL 内強制
- 学習者向け DTO に weight / confidence / score / 件数キーが無い（CR3。再帰走査）
- 閉世界 denylist（「世界初」「誰も」「この分野には論文がない」等）の非出現（CR4）
- corpus 系ルートから arXiv / Semantic Scholar client への import が無い（CR7）
- センチネル `_doc:` の組み立てが正本関数以外に無い / コース解決へ流入しない（grep 固定）
- document 直付け chat が `user_can_view_document` を通る・`allowed_document_ids` を渡す
- `frontier_interest` の trace_registry 宣言と消費面（digest / 集約 / 地図）の除外一致
- k=3 リテラルの再定義なし（`core/privacy.py` 委譲）
- ポーリング・自動表示の不在（UI 静的検査）
- 行削除 API の不在

---

## 11. 実装時の確認事項

- migration 採番は実装時に `ls backend/db/` で確認（本書は番号を予約しない — §5-4）。
- `learning_chat` の共通化点の精査: course_data 解決・受講ゲート・quota・履歴保存の分離面。
  ファサード追加で既存関数のシグネチャを変えないこと（CR2）。
- センチネル course_id が波及しうる消費者の総点検: interest_traces の教員向け集約
  （course 単位の k-匿名集約にセンチネルが混ざらないこと）、V層 `_apply_course_version_view`、
  G層ルール、状態投影（projector）、tension/anchor worker（コース非依存動作の確認）。
  「混ざらない」をガードレールに落とす。
- Phase A の描画資産（atlas-overlay / landscape-layer）のコース非依存化がどこまで素直か
  （`getData(courseId)` 前提の箇所の洗い出し）。
- Phase B 着手前に discuss 観測実測を確認（§3 のゲート。オーナー裁定で免除可 — 裁定内容を
  §実装記録に残す）。
- 学習者マニュアル（`docs/manual/student/`）への節追加と、学習UI インスペクト・モードの
  アンカー（`core/help_kb/ui_anchors.py` — 管理側とは別表）の追随。

---

## 12. 実装記録（Phase A〜D, 2026-08-27）

同日、同体制（Fable 5 指揮・Opus 5 並列4体 = corpus-backend / discuss-backend / fe-learner /
followup）で全 Phase を実装。backend フルスイート 11,483 pass・src 1,811 pass。

**着手ゲートの裁定（§3）**: discuss 観測実測の確認はオーナーの明示指示
（2026-08-27「これまでと同じ体制で実装に着手せよ」）を裁定として免除した。docker 環境
未稼働のため observation-status の実測は取得していない — 運用開始後に
`document_discuss_opened` / `document_discuss_turn` を含めて観測する。

### Phase A/C/D バックエンド
- migration **073**: `paper_discovery_subscriptions.last_search_found_new BOOLEAN` のみ
  （DEFAULT なし — NULL = 「まだ検索していない」を保つ）。ビット更新は
  `store.touch_last_checked(..., found_new=)` 経由で `POST /search` 実行時のみ
- `core/corpus_view.py`（FastAPI/LLM 非 import・読み時導出・保存物なし）+
  `routes/corpus.py`（learning_router・main.py 直接登録）。**骨格そのものは返さない**
  （既存 `GET /api/atlas?cartridge=` がコース無しで骨格を返すため、配置・縁・外だけを
  返す — 描画資産の二重管理回避）
- `corpus/documents` の母集合 = 「配置がある論文 ∪ gap 信号がある論文」（可視論文全件を
  分野に並べると分野帰属の捏造になるため。`placed:false` が縁の事実）
- fringe は現行凍結版に実在する region のみ・outer は bit=TRUE かつ日付解釈可のときのみ
- Phase D: kind `frontier_interest` を `trace_registry` に宣言（問いの軌跡=非表示 /
  教員=専用 k-匿名集約のみ / わたしの地図=非表示 / わたしの記録=表示）。痕跡の course_id
  はセンチネル `services.CORPUS_TRACE_COURSE_ID = "_corpus"`（help_usage の `"_ui"` と同型）。
  教員向け `GET /api/admin/discovery/frontier-interest`（k=3・レンジのみ・dismissed 非計上）

### Phase B（document 直付け discuss）
- センチネルの正本は `core/discuss/context.py`（`_doc:` の組み立ては AST ガードレールで
  この1ファイルに固定）。migration 0
- `learning_chat` は**本体を `_learning_chat_core` に分離**（route は1行委譲・コース経路の
  挙動はバイト単位で不変が原則、分岐は4点のみ: course_data 注入 / センチネル判定 /
  topic_title / RAG スコープ注入）。document 経路は DA1〜DA6・truncate・tension プレ
  フィルタ・CostGate・U層タグを共通コアからそのまま得る
- API 4本: opening（`build_opening` 再利用 + `document_context` キー・LLM 0回）/ chat /
  history / messages DELETE（truncate）。ゲートは `resolve_document_access.can_view` のみ・
  不可視と不在は同一 404（存在リーク防止）
- **確定した縮退**: document 直付け opening の `fragile_points` は空（`compile_open_
  assumptions` が course_id キーのため。v2 で document_id 引数に切替可）。`action` /
  `atlas_context` / `cycle_mode` はサーバ側で null 化（§5.4 の明示化）。content_grounding
  は当該論文 = course_material 扱い
- 観測2語彙 `document_discuss_opened` / `document_discuss_turn`（サーバ側記録 —
  フロントから二重送信しない）

### 学習UI
- `corpus-sea.js`（ES6・`window.CorpusSea`）: サイドバー常設「🌊 論文の海」→ 全画面
  オーバーレイ（ドメインチップ / 簡易 SVG 地図 = 領域ボックス + 概念ノード + 📄 マーカー /
  端カード / 論文リスト + 詳細 / 議論ビュー）。atlas-overlay は AtlasContext のコース
  結合が強く流用せず、骨格は `/api/atlas?cartridge=` 直接取得（座標規則は
  landscape-layer と同一）。マーカー溢れは「+N」でなく「…」（件数非表示の徹底）
- discuss.js の `renderOpening` を第2引数の多相化で document 文脈対応（コース文脈は
  完全従来どおり・`sendDiscussMetric` は document 文脈で送らない）。議論ビューの離脱は
  閉じるだけ（着地なし）
- 「この先を知りたい」はトグルのみで表示内容を変えない（CR5）。invalidate はログアウト /
  401 の2経路（コース切替では呼ばない — コーパスはコース非依存）

### 3点セット・教員側
- 教員: ディスカバリーモーダルに「学習者の関心」区画（open / 分野選択の2トリガのみ・
  行ゼロと取得失敗は区画ごと非表示 = 「関心なし」と言わない）。アンカー
  `materials.arxiv-discovery-interest`（管理側総数 293 — 正は `test_admin_help_ui_anchors.py`）
- 学習者: アンカー `sidebar.corpus-sea`（学習側 ui_anchors 表・sidebar.* の既存命名に
  合わせた）+ `docs/manual/student/02-student.md` §16（4節）
- teacher マニュアル節・admin_operations 追記

### テスト
`test_corpus_roaming_{core,api,guardrails,ui_static}.py` +
`test_document_discuss_{api,guardrails}.py`（計 81+63+44+α）。既存 discuss テストは
`learning_chat` → `_learning_chat_core` への機械的な参照付替えのみ（アサーション非弱体化）。

### v1 で提供しないもの（§5.4 / §9 の確定）
document 直付けの出典タブ・教材埋め込み記法解決・数式レンダリング（本文素通し）、
着地画面・tension/anchor digest、fragile_points、EmergentRegion 系、G層ルール。
