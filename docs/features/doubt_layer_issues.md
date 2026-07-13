# D層（Doubt Layer）実装 issue 分割設計

> **正本**: `episteme-graph_D層構想準備資料.md`（前提の台帳と地図）
> **位置づけ**: A層（構造化）・B層（学習）・C層（承認）に続く第四の層。
> 本書は構想を **1 issue ≒ 1 PR** の粒度に分割し、実装順序・依存関係・UX 設計を確定させる。

---

## 0. 全体方針（全 issue 共通の不変条項）

C層・B層で確立した積層パターンを踏襲する。

| 原則 | 内容 |
|---|---|
| **A層非改変** | D層は A層成果物（claims.json / equations.json / TheoryOperationGraph / derivations）を**読む側**として実装する。`src/episteme_graph/agents/` のコードは変更しない（C層と同じ立場） |
| **AIに疑わせない** | LLM 出力は常に candidate。前提の確定・疑義・検証提案・操作化の主体はすべて人間。反実仮想の「再構築」は計算しない |
| **帰属必須・匿名疑義なし** | 疑義・検証提案・前提確定・スコープ記帳はすべて帰属と理由を持ち、`theory_review_events` に監査記録する |
| **情報を落とさない（P4）** | 却下は `dismissed` で保持。不明は `untested` / `unknown` で保持。削除しない |
| **煽らない・数値を見せない** | 地図にゾーン煽り文句を付けない。負荷度・検証度の生数値スコアをランキング/バッジ化しない（Field Atlas の「踏破率を数値にしない」と同じ規律） |
| **同期パスに LLM を入れない（P6）** | LLM 補助（スコープ候補抽出・前提候補の正規化）は tension / structure_anchor と同型の非同期バッチ worker で行う |
| **学習者を監視しない（P3）** | 部品6（素朴な問いの計器化）は k-匿名集約のみを D層に送る。個人特定不可、評価利用禁止 |

**実装配置**: バックエンドは `backend/core/doubt/` 配下に tension / structure_anchor と同型の
独立モジュール群（schema / builder / worker / validator …）。API は `backend/api/routes/doubt.py`
を新設。マイグレーションは **029 から**採番する。

**UX の基本姿勢**:
- **教員**: 記帳を義務にしない。空欄は異常ではなく**発見**（パリティの教訓）。パイプラインが決定論的に既定値を自動記帳し、教員の操作は「詳細ペインからワンタップで積み増す」だけにする。新画面を乱立させず、既存の理論グラフ画面（admin.js）への**レイヤ追加**として統合する。
- **学生（コース受講者=大学院生）**: 新しい操作を一切増やさない。既存の質問行動（B層）が自動的に部品6へ流れる。台帳情報は回答・教材の詳細に**静かに併記**され、通知・ポップアップ・誘導カードで割り込まない。

---

## マイルストーン構成

| マイルストーン | 構想の Step | issue |
|---|---|---|
| **M-D1: 記帳を始める** | D-1 | D1-1 〜 D1-5 |
| **M-D2: 地図を描く** | D-2 | D2-1 〜 D2-4 |
| **M-D3: 道具を渡す** | D-3 | D3-1 〜 D3-6 |
| **M-DX: 横断（品質・計測）** | 全体 | DX-1 〜 DX-2 |

依存グラフ（→ は「先行が必要」）:

```
D1-1 → D1-2 → D1-3 → D1-4
         │       └→ D1-5
         └→ D2-1 → D2-3(地図UI)
D1-1 → D2-2(マイニングA) → D2-4(確定フロー) → D3-2(未検証合意リスト)
D1-3 ──────────────────→ D3-1(疑義) → D3-2 → D3-6(学習者導線)
D2-1 → D3-3(反実仮想BE) → D3-4(反実仮想UI)
D2-2 → D3-5(マイニングB)
全体 → DX-1, DX-2（各マイルストーン末に実施）
```

---

# M-D1: 記帳を始める（部品1 + 部品6の入口）

## D1-1: D層スキーマ語彙と認識的地位台帳のデータモデル（migration 029）

**目的**: 「合意の強さ」と「検証の強さ」をデータ構造のレベルで混同不可能にする台帳の器を作る。

**スコープ**:
- `backend/db/029_epistemic_ledger.sql`:
  - `epistemic_ledger` テーブル: `target_id` / `target_type`（claim | assumption | equation | component）/
    `verification_status`（`directly_verified` | `indirectly_supported` | `untested` | `refuted` | `unknown`）/
    `verification_scopes JSONB`（**配列**。各要素 = `{condition, domain, precision, system, evidence_ids[], recorded_by, reason}`）/
    `consensus_explicit`（C層承認集計への参照値）/ `consensus_behavioral`（コーパス内依存頻度）/
    `load_score`（NULL 可、D2-1 で記入）/ `updated_at`
  - `UNIQUE(target_id, target_type)`
- `backend/core/doubt/schema.py`: 上記の Pydantic モデル + 語彙 Enum。
  **検証は単一ブール値にせずスコープの配列**とする（構想の心臓部）。
- `theory_review_events.entity_type` に `'ledger'` / `'assumption'` / `'challenge'` /
  `'verification_proposal'` / `'counterfactual_session'` を許容拡張。
- 設計原則（§0 の不変条項）を docstring / docs に明文化。

**やらないこと**: 記帳ロジック・API・UI（後続 issue）。（Neo4j は撤去済みのため対象外。動的スキーマ進化側の語彙追加は D3 以降）。

**受け入れ条件**:
- マイグレーションが冪等に適用できる
- `verification_scopes` にスコープ 0 件（=空欄）の行が正常状態として表現できる
- pytest: スキーマの直列化・語彙検証

**依存**: なし（最初に着手）

---

## D1-2: LedgerBuilder — A層成果物からの決定論的バックフィル（非LLM）

**目的**: 教員が 1 文字も入力しなくても台帳が既定値で埋まり、「検証スコープの空欄」が見え始める状態を作る。

**スコープ**:
- `backend/core/doubt/ledger_builder.py`（非LLM・決定論的）:
  - A層成果物を読み、対象ごとに初期台帳行を生成:
    - claim（`theory_claims`）: 既定 `verification_status='unknown'`。evidence_quote を持つ
      `source_backed` atomic claim は `indirectly_supported`（「原文に書いてある」≠「検証されている」
      なので `directly_verified` には**しない**）
    - equation / component: `source_backing_status` から保守的にマップ（`inferred` → `unknown`）
  - `consensus_behavioral`: TheoryOperationGraph / derivation chain 上でそのノードに依存する
    下流ノード数を数えて記帳（後の負荷度の簡易前身）
  - `consensus_explicit`: C層 `component_explanation_endorsement_summary` から参照値を転記
- 既存コーパスへのバックフィルスクリプト（`backend/scripts/` 配置、再実行冪等）
- ドキュメント解析完了時のフック: A層パイプライン完了**後**に呼ばれる後処理として登録
  （document_analysis_runs の完了を見て builder を走らせる。**A層コードは触らない**）

**やらないこと**: LLM によるスコープ推定（D1-4）。verification_status の自動昇格（`directly_verified` は人間の記帳のみ）。

**受け入れ条件**:
- サンプルコーパスでバックフィル後、全 claim/equation に台帳行が存在する
- `directly_verified` の行が自動生成されていない（人間専用語彙のテスト）
- 再実行しても行が重複しない

**依存**: D1-1

---

## D1-3: 台帳の閲覧・記帳 API（教員向け）

**目的**: 検証スコープの記帳を「帰属・根拠つきの正式な学術行為」として API 化する。

**スコープ**:
- `backend/api/routes/doubt.py` 新設（実パス `/api/admin/doubt/...`、TEACHER 以上）:
  - `GET /doubt/ledger/{target_type}/{target_id}` — 台帳行 + スコープ配列 + 出所
  - `POST /doubt/ledger/{target_type}/{target_id}/scopes` — スコープ追加。
    `condition/domain/precision/system` のうち 1 つ以上 + `evidence_ids`（根拠）+ `reason` 必須
  - `PATCH /doubt/ledger/.../scopes/{scope_id}` — 訂正（履歴は review_events に残す）
  - `PUT /doubt/ledger/.../verification-status` — 状態変更（`directly_verified` への昇格は
    スコープが 1 件以上あるときのみ許可 = **全称検証を構造的に禁止**）
  - すべて `theory_review_events` に `entity_type='ledger'` で監査記録
- コース単位の台帳サマリ: `GET /doubt/courses/{course_id}/ledger-summary`
  （対象ノード数・記帳済み数・空欄数。**内部/教員向けのみ**、学習者には出さない）

**UX 設計**:
- スコープ入力は自由記述 4 フィールド + evidence 選択のみの軽量フォーム。必須は「1 軸 + 根拠 + 理由」だけにし、記帳 1 件 30 秒以内で完了する導線にする
- 「空欄」をエラー表示にしない。サマリでも空欄を欠陥ではなく事実として表示する文言にする

**受け入れ条件**:
- スコープなしで `directly_verified` に変更しようとすると 422
- 全書き込みが review_events に記録される
- STUDENT ロールは書き込み不可（403）

**依存**: D1-2

---

## D1-4: 台帳の表示統合 + スコープ候補の LLM 補助（非同期）

**目的**: 教員が既存の理論グラフ画面から離れずに台帳を見て・記帳できるようにする。記帳の負荷を LLM 候補で下げる（ただし確定は人間）。

**スコープ**:
- **表示（admin.js, ES5）**: 理論操作グラフのノード詳細ペインに「認識的地位」セクションを追加:
  - 検証状態・スコープ一覧（各スコープに根拠 evidence リンクと記帳者）・合意の 2 軸
    （明示的合意=承認の厚みラベル / 行動上の合意=依存数）を**別々の行で**表示
  - スコープが空の場合は「検証スコープの記帳なし」と事実だけを静かに表示（警告色にしない）
  - 「スコープを記帳」ボタン → D1-3 のフォーム
- **LLM 補助（`backend/core/doubt/scope_candidates/`）**: tension / structure_anchor と同型の
  非同期 worker。equation_semantics の仮定復元・claim の evidence_quote から検証スコープ**候補**を
  抽出し、`status='candidate'` で保持。教員 UI では候補が薄色チップで表示され、
  タップ 1 回で確定（確定時に帰属が教員に付く）または却下（`dismissed` 保持）
  - コスト上限: `DOUBT_SCOPE_MAX_CALLS_PER_DAY`（既定 10、tension と独立）
  - validator: 候補は必ず逐語 `evidence_quote`・`reason`・`confidence` を持つ（P5）

**UX 設計**:
- 教員の記帳動線は「候補チップを確認 → タップで確定」が主、手入力は従。ゼロから書かせない
- 候補は詳細ペインを開いたときにだけ見える。一覧画面へのバッジ通知はしない（煽らない）

**受け入れ条件**:
- LLM 候補が確定なしに `verification_scopes` 本体へ入らない
- worker 失敗時も既存画面の表示が壊れない（fail-closed: 候補セクション非表示）
- グラフ画面の既存表示（source_backing_status の線種区別）と干渉しない

**依存**: D1-3

---

## D1-5: 部品6 — 素朴な問いの計器化（B層集計の D層ビュー）

**目的**: 「複数の学習者が独立に同じ前提の手前でつまずく」を、前提側から見える集計にする。B層→D層の水路の第一本。

**スコープ**:
- `backend/core/doubt/naive_signal.py`: `interest_traces` のうち本人が引き受けた行
  （tension: confirmed 系 status / structure_anchor: `learner_selected` または `confirmed`）**のみ**を
  対象に、anchor（claim / equation / stage / concept）単位で集計する読み取り専用ビュー
  - **k-匿名化 k=3、n<3 セル非表示**（既存 anchor-insights / tension 集計と同じ規律）
  - 出力: anchor ごとの「つまずき件数レンジ（3-5 / 6-10 / 11+ の段階表示）」「doubt_type 分布」
- `GET /api/admin/doubt/courses/{course_id}/naive-signals` — 教員・研究者向け
- 台帳表示（D1-4 のペイン）に「学習者の問い」行を追加: 該当 anchor に k≥3 のシグナルが
  あれば「複数の学習者がここで問いを立てています」とだけ表示（**件数の生数値・個人は出さない**）

**UX 設計**:
- 学習者側には何も変わらない・何も見えない（既存の質問行動がそのまま計器になる）
- 教員側も一覧やランキングにしない。ノード詳細を見たときにだけ気づける配置

**受け入れ条件**:
- n<3 の anchor がレスポンスに一切含まれない
- candidate 状態（本人未確定）の trace が集計に入らない（P1）
- 個人を特定できるフィールドがレスポンスに存在しない

**依存**: D1-2（台帳表示に載せるのは D1-4 以降でも可。API 単体は独立に出せる）

---

# M-D2: 地図を描く（部品3 + 部品2 経路A）

## D2-1: 負荷度（load_score）のバッチ計算

**目的**: 「その前提が偽なら下流の何が崩れるか」を理論操作グラフ上の到達可能性で決定論的に計算し、台帳に記帳する。

**スコープ**:
- `backend/core/doubt/load_calculator.py`（非LLM・決定論的）:
  - TheoryOperationGraph（main + equation_detail 層）+ derivation chain + claim リンクから
    依存グラフを構成し、各ノードの下流到達集合サイズを計算
  - `load_score` は生数値で保存するが、**API/UI では段階ラベル**（低 / 中 / 高 / 最高位=上位パーセンタイル）
    に変換して返す（数値スコアを見せない原則）
  - `graph_layer='debug'` / `inferred` ノードは負荷計算の**根拠にしない**（弱い backing を負荷に混ぜない）
- 再計算バッチ（解析完了時 + 手動トリガ）。（旧 `backend/core/batch.py` パターンは撤去済みのため対象外。`core/llm_worker/` 等の現行正本に接続する）
- `epistemic_ledger.load_score` への書き込み + `load_computed_at`

**受け入れ条件**:
- 循環があってもハングしない（到達可能性計算の閉路対応テスト)
- debug 層ノードの有無で main 層の負荷値が変わらない
- API レスポンスに生スコアが漏れない

**依存**: D1-2

---

## D2-2: 暗黙前提マイニング 経路A — 導出の隙間の反転（migration 030）

**目的**: AIが行間を埋めるために補った補完（現在は品質上の弱点扱い）を、複数論文・複数導出にまたがる反復として検出し、暗黙前提の**候補**に昇格させる。

**スコープ**:
- `backend/db/030_assumption_nodes.sql`: `assumption_nodes` テーブル
  （`statement` / `origin`（`mined_gap` | `mined_corpus` | `naive_aggregate` | `manual`）/
  `status`（`candidate` | `confirmed` | `operationalized` | `dismissed`）/
  `operationalized_by` / `created_from JSONB`（出所参照: derivation_ids, review_reasons 等））
- `backend/core/doubt/assumption_mining/`（tension と同型のモジュール構成）:
  - **検出（非LLM）**: `inferred` / `review_required` + 既存 review 理由を持つ補完ステップを
    コーパス横断で収集し、операция・対象式の構造キーでクラスタリング。
    **2 論文以上 or 2 導出以上**で反復する同一補完のみ候補化
  - **正規化（LLM・非同期バッチ）**: クラスタを「原子化された前提文」1 文に正規化
    （claim_qualification の原子化と同じ型: 逐語 evidence・reason・confidence 必須、
    repair 2 回失敗は `unclassified` 相当で保持）
  - 出力は常に `status='candidate'`。**確定 API はこの issue に含めない**（D2-4）
- review 理由語彙に `implicit_assumption_candidate` を追加（D層側の語彙として。A層 validator は触らない）

**受け入れ条件**:
- 単一導出にしか現れない補完が候補化されない
- LLM 正規化失敗時も検出クラスタが行として保持される（P4）
- 候補が `confirmed` になる経路がこの時点で存在しない（API 未実装の確認）

**依存**: D1-1（台帳語彙）。D2-1 と並行可。

---

## D2-3: 前提の地図 UI（Assumption Atlas — 教員・研究者向け）

**目的**: 負荷度×検証度の二次元分布を「評価抜きの事実の投影」として描く。1956 年の弱い相互作用パリティが右下に沈んで見える地図。

**スコープ**:
- `GET /api/admin/doubt/courses/{course_id}/assumption-atlas` — 散布データ
  （対象: confirmed assumption + 高負荷 claim/equation。x=検証度（スコープ被覆の段階）、
  y=負荷度（段階）、点の属性: 合意の厚み・学習者シグナル有無）
- **admin.js**: 理論グラフ画面に「前提の地図」タブ（またはビュー切替）を追加:
  - 散布図（Vanilla JS / SVG。既存グラフ描画の流儀に合わせる）
  - **ゾーン名・煽り文句・推奨マークを描かない**。軸ラベルは「検証スコープの被覆」「依存の広がり」のみ
  - 点タップ → 既存のノード詳細ペイン（D1-4 の台帳セクション）へ。地図専用の別詳細を作らない
  - 空欄（検証スコープ 0 件）の点は塗りなし・点線輪郭で描く（構想のモチーフ「点線の輪郭を持つノード」）
- Field Atlas（分野の地図）とは別機能である旨を UI 文言・コードコメントで明示（命名衝突回避:
  フロントは `doubt-atlas.js` プレフィックス）

**UX 設計**:
- 地図はプル型（タブを開いた人だけが見る）。ログイン時表示・通知・バッジにしない
- 描画は台帳のスナップショットで静的に。リアルタイム LLM 生成をしない（Field Atlas と同じ規律）

**受け入れ条件**:
- レスポンス・DOM のどこにも「疑うべき」等の評価語・生スコア数値がない
- 台帳未記帳（全欄空）のコースでも空の地図が壊れず表示される
- 既存の theory graph 表示・Field Atlas と DOM/CSS が干渉しない

**依存**: D2-1、D1-3

---

## D2-4: 前提の確定フロー — candidate → confirmed / operationalized（教員 UI）

**目的**: マイニング候補を「専門家が確かにこれは暗黙の前提だと引き受ける」行為で確定する。確定は人間、が制度として機能する最初の画面。

**スコープ**:
- API（`routes/doubt.py`）:
  - `GET /doubt/courses/{course_id}/assumptions?status=candidate` — 候補一覧（出所つき）
  - `POST /doubt/assumptions/{id}/confirm` — body: `statement?`（教員が文面を訂正可）+ `reason` 必須。
    確定者の帰属が付き、`theory_review_events`（`entity_type='assumption'`）に記録
  - `POST /doubt/assumptions/{id}/dismiss` — `dismissed` で保持（P4）
  - 確定時に `epistemic_ledger` へ assumption の台帳行を自動生成（既定 `untested`・スコープ空欄）
- **admin.js**: 候補レビュー画面。候補文 + 出所（どの論文のどの導出の隙間から来たか、
  逐語 evidence）+ 学習者シグナル（D1-5、k-匿名）を 1 カードで見せ、確定 / 文面訂正 / 却下
- 手動登録: `POST /doubt/assumptions`（`origin='manual'`。マイニングを待たずに教員が直接明示化できる）

**UX 設計**:
- レビューは「たまったら見る」プル型。件数バッジは出すが赤色・警告表現にしない
- 確定は 2 タップ（カード → 確定）。理由は既定文候補から選択 + 追記可、で入力負荷を下げる

**受け入れ条件**:
- 確定・却下・訂正がすべて監査記録される
- dismissed 候補が一覧フィルタで確認できる（消えない）
- confirmed になった前提が地図（D2-3）と台帳に現れる

**依存**: D2-2、D1-3

---

# M-D3: 道具を渡す（部品4・5 + 経路B + 学習者導線）

## D3-1: 疑義（Challenge）の一級市民化 — C層対称拡張（migration 031）

**目的**: 承認（endorsement）と対になる行為として、疑義を帰属・理由・型・履歴つきの正式な研究行為にする。

**スコープ**:
- `backend/db/031_challenges.sql`: `challenges` テーブル
  （`target_id` / `target_type`（assumption | claim）/ `challenger_id` /
  `challenge_type`（`scope_extrapolation` | `untested_in_domain` | `definitional` | `hidden_lemma`）/
  `reason TEXT NOT NULL`（本人の言葉）/ `status`（`open` | `answered` | `withdrawn` | `led_to_verification`））
- API:
  - `POST /doubt/{target_type}/{target_id}/challenges` — 作成（型 + 理由必須。**匿名不可**）
  - `GET .../challenges` — 一覧（解釈の並存と同様の併記表示用）
  - `POST /doubt/challenges/{id}/withdraw` — 取り下げ（行削除ではなく status 遷移、履歴保持）
  - 監査: `entity_type='challenge'`
- 表示: ノード詳細ペイン・地図の詳細に「疑義」セクションを併記
  （「A先生はこの前提の適用範囲に疑義を残している」形式。承認の厚みと同じく**数値スコア化しない**）
- **地位勾配への配慮（構想 §8-3）**: 疑義カードの主語は常に型
  （例:「この検証スコープに空白があるという疑義」）で描き、人格対立の文面にしない。
  作成フォームの型選択を先・自由記述を後にする

**受け入れ条件**:
- reason 空で 422。challenger_id なしの行が作れない
- withdraw 後も行と履歴が残る
- 承認済み説明の表示（C層）と同一ペインで併記されても視覚衝突しない

**依存**: D1-3（対象の台帳が存在すること）。D2-4 完了後が望ましいが claim 対象なら先行可。

---

## D3-2: 検証提案 + 未検証合意リスト（Open Assumptions List）

**目的**: 疑義から「この実験・この計算で検証可能」への昇格経路（操作化型の完成形）と、分野の生きたオープンプロブレム集を作る。

**スコープ**:
- migration 032: `verification_proposals`
  （`challenge_id` / `proposal TEXT`（どの実験・計算で検証可能か）/ `proposer_id` / `status`）
- API:
  - `POST /doubt/challenges/{id}/proposals` — 昇格（元 challenge を `led_to_verification` に遷移）
  - `GET /doubt/courses/{course_id}/open-assumptions` — **自動編纂**:
    台帳から「高負荷（段階: 高以上）× 低検証（untested / unknown またはスコープ空欄）」を抽出し、
    紐づく疑義・検証提案・学習者シグナルを合成したリスト
- **admin.js**: 未検証合意リスト画面（教員・研究者向け）。並び順は負荷段階→依存数。
  各行: 前提文 / 検証状態と空欄表示 / 疑義数（段階ラベル）/ 検証提案の有無
- リストは編集不可（台帳の投影であることを保つ）。行タップで詳細ペインへ

**UX 設計**:
- 「オープンプロブレム集」という価値づけ文言は使ってよいが、順位・スコア・
  「ノーベル賞候補」的演出は禁止（§8-5）

**受け入れ条件**:
- リストが台帳の状態変化（スコープ記帳）で自動的に増減する（編纂ロジックのテスト）
- 提案昇格で challenge status が遷移し、両方向の参照が引ける
- 監査記録（`entity_type='verification_proposal'`）

**依存**: D3-1、D2-1

---

## D3-3: 反実仮想モード — バックエンド（伝播計算 + セッション永続化、migration 033）

**目的**: 前提を仮に「偽」に倒し、崩れる領域と生き残る領域をグラフ上で描き分ける計算基盤。再構築は計算しない（そこが人間の創造の場所）。

**スコープ**:
- `backend/core/doubt/counterfactual.py`（非LLM・決定論的）:
  - 入力: `toggled_assumption_ids[]`（複数同時トグル可）
  - D2-1 の依存グラフ上で下流伝播 → `collapsed`（崩壊）/ `surviving`（生存）/
    `boundary`（両者に依存し判定不能 → 崩壊側に倒さず `indeterminate` で保持）の 3 区分を返す
  - **「外した後に何が再構築できるか」は計算しない**（docstring に限界を明記）
- migration 033: `counterfactual_sessions`
  （`toggled_assumption_ids[]` / `collapsed_subgraph JSONB`（スナップショット）/
  `surviving_subgraph JSONB` / `owner_id` / `shared_scope`（private | group | public、
  既存 Visibility 語彙を流用）/ `notes TEXT`（本人の言葉））
- API: `POST /doubt/counterfactual/compute`（保存なし試算）、
  `POST /doubt/counterfactual/sessions`（保存）、`GET /doubt/counterfactual/sessions`（自分 + 共有分）、
  `PATCH .../sessions/{id}`（notes・共有範囲）

**受け入れ条件**:
- 計算がスナップショット（保存時点のグラフ）に対して決定論的（同入力同出力）
- 共有範囲が既存 RBAC / Group 可視性と整合（Group 非メンバーに 404/403）
- 大きいグラフでもタイムアウトしない（到達可能性は D2-1 の実装を再利用）

**依存**: D2-1、D2-4

---

## D3-4: 反実仮想モード — フロントエンド

**目的**: 地図・グラフで気になった前提を、その場で「仮に外してみる」道具として渡す。

**スコープ**:
- **admin.js**: 理論グラフ画面に「反実仮想」モードトグル:
  - 前提ノード（confirmed assumption / 台帳を持つ claim）をタップ → 「この前提を仮に偽にする」
  - compute 結果で崩壊領域を減衰表示（薄色 + 点線）、生存領域を通常表示、
    indeterminate は中間トーン。凡例は事実記述のみ（「この前提に依存」等）
  - 複数トグルのスタック表示と個別解除
  - 「セッションを保存」→ notes（本人の言葉）入力 → 共有範囲選択（既定 private）
- 保存済みセッション一覧 + 読み込み（共有されたセッションは作成者帰属を表示）
- モード退出で通常表示に完全復帰（状態が漏れない）

**UX 設計**:
- トグルは可逆・非破壊であることを UI で明示（「元のグラフは変更されません」）。
  確認ダイアログは出さない（試行のコストを最小に — Shadow Testing と同じ思想）
- 「崩壊」という語は使わず「この前提に依存する範囲」と事実で書く

**受け入れ条件**:
- モード中の表示が graph_layer トグル・source_backing 線種と両立する
- 保存 → 再読み込みで同じ描き分けが再現される（スナップショット整合）
- private セッションが他ユーザーの一覧に出ない

**依存**: D3-3

---

## D3-5: 暗黙前提マイニング 経路B — コーパス横断の監査

**目的**: 「ほぼすべての導出が依存しているのに、どの論文もそれ自体を主張・引用・防御していないノード」= 引用グラフの空白を検出する。

**スコープ**:
- `backend/core/doubt/assumption_mining/corpus_audit.py`:
  - DSL 埋め込み + 構造的同型性評価（`core/isom.py`）を使い、コーパス全論文を横断して
    「依存されているが被主張・被引用がない」概念/前提ノードを検出（非LLM の突合が主、
    同型判定の閾値調整のみ）
  - 検出結果は `assumption_nodes` に `origin='mined_corpus'` / `status='candidate'` で登録
    （確定フローは D2-4 を再利用 — 新 UI 不要）
  - 対象コーパス規模の下限（例: 3 論文未満のコーパスでは実行しない）を設け、
    小規模コーパスでの偽陽性を防ぐ
- バッチ実行（手動トリガ + 解析完了時オプション）

**受け入れ条件**:
- 単一論文コーパスで候補が出ない
- 既に confirmed / dismissed 済みの同一前提が再候補化されない（同型性による重複判定）
- 候補の `created_from` から検出根拠（依存ノード群・空白の証明）が追える

**依存**: D2-2（テーブル・確定フロー共用）

---

## D3-6: 学習者向け導線 — 台帳の正直表示と問いからの間接経路

**目的**: コース受講者（大学院生）に D層を**読み取り専用 + 間接参加**で開く。「初学者の『なぜ?』は専門家が舗装して忘れた前提の真上で発せられる」の水路を UI にする。

**スコープ**:
- **台帳の正直表示（app.js）**: 学習チャットの出典タブ・教材詳細ペインに、対象 claim/equation の
  検証状態を**一行の事実**として併記
  （例:「この関係は◯◯の条件で検証されています / この前提の検証スコープはまだ記帳されていません」）。
  API: `GET /api/learning/courses/{course_id}/ledger/{target_type}/{target_id}`（読み取り専用・
  スコープと段階ラベルのみ。生スコア・疑義の生一覧は返さない）
- **未検証合意リストの閲覧**: 学習 UI から `GET /api/learning/courses/{course_id}/open-assumptions`
  （D3-2 の読み取り専用版。疑義者の氏名は Public 共有分のみ表示）
- **問いからの間接経路**: 学習者の structure_anchor 確定時（既存 B層フロー）、その anchor が
  confirmed assumption に対応していれば、確認画面に
  「この問いは、分野で明示化されている前提『…』に関わっています」と**事後に静かに**表示
  （通知しない・押し付けない。B層の「接続を宣言しない」と同じ規律 — 表示は帰属事実のみ）
- 学習者からの直接の疑義投稿は**このマイルストーンではやらない**
  （地位勾配 §8-3 への配慮。まず k-匿名の間接経路と読み取りで文化を観察し、
  直接投稿は D層運用後の別 issue として判断する — この判断自体を issue 本文に明記）

**UX 設計**:
- すべてプル型・併記型。学習の主フロー（チャット・レクチャー）に割り込む要素ゼロ
- 「まだ検証されていない」表示は不安を煽らない中立文言にし、
  相対主義への誤誘導を防ぐため検証**済み**スコープも同じ精度で必ず併記する（§8-1・8-2）

**受け入れ条件**:
- STUDENT ロールで書き込み系 doubt API がすべて 403
- 台帳未記帳コースで学習 UI の表示が一切変わらない（セクション自体が出ない fail-closed）
- 出典タブの既存表示（content_grounding / tier）と視覚的に両立する

**依存**: D1-3、D2-4、D3-2

---

# M-DX: 横断

## DX-1: D層ガードレールの自動テスト

**目的**: 構想 §8「守るべき一線」を、レビュー頼みでなく pytest で構造的に守る。

**スコープ**（`backend/tests/test_doubt_guardrails.py` ほか）:
- **AI断定禁止**: 全 LLM 出力経路（scope_candidates / assumption_mining 正規化）の出力が
  candidate 系 status 以外に直接書けないことのテスト
- **匿名疑義なし**: challenger_id / reason の NOT NULL 制約 + API 422 テスト
- **数値非表示**: 学習者向け・教員向け API レスポンスに `load_score` 生値・confidence 生値が
  含まれないことの契約テスト
- **監視にしない**: naive-signals / open-assumptions レスポンスの k-匿名検査（n<3 非含有）
- **P4**: dismiss / withdraw 系がすべて行保持であることのテスト
- **文言リント**: フロント資産に禁止語（「疑え」「ノーベル賞」等の煽り語彙リスト）が
  含まれないことの簡易チェック（CI で grep）

**依存**: 各 issue と並走（各 PR に該当テストを含めた上で、本 issue で横断分を補完）

---

## DX-2: KPI 内部計測（ユーザー非表示）

**目的**: 「システムが指摘した数」ではなく「人間が行為した数」を測る（構想 §9）。数値をユーザーに見せる API・UI は作らない（Field Atlas cues と同じ規律）。

**スコープ**:
- 既存イベント（`theory_review_events`）からの集計バッチ:
  台帳被覆率（高負荷ノード中スコープ記帳済み率）/ 空欄の新規可視化数 /
  candidate → confirmed 率 / 理由つき疑義数と検証提案昇格率 /
  反実仮想セッション共有数 / 素朴な問い → confirmed 前提の転換数
- `GET /api/admin/doubt/metrics`（SYSTEM_ADMIN のみ。運用判断用）
- ダッシュボード UI は作らない（JSON で十分。可視化が必要になったら別 issue）

**受け入れ条件**:
- 集計が review_events の再集計だけで再現できる（専用カウンタテーブルを持たない）
- TEACHER / STUDENT からアクセス不可

**依存**: M-D1〜M-D3 の各書き込み系 issue

---

## 補足: 分割の設計判断

1. **D-1 を最小で先行させる理由**: 台帳は属性追加中心・低リスクで、「空欄が見える」だけで
   価値が立つ（パリティの例）。D2 以降の地図・道具はすべて台帳の投影なので、
   台帳の記帳体験（D1-3/D1-4）の質が全体の UX を決める。
2. **マイニングを 2 issue（経路A/B）に分けた理由**: 経路A は既存 review 理由の反転で
   独立に完結するが、経路B は同型性評価の閾値調整というリスクの異なる作業を含む。
   確定フロー（D2-4）を共用させることで B 側の UI コストをゼロにしている。
3. **反実仮想を BE/FE に分けた理由**: 伝播計算はスナップショット整合・性能・可逆性という
   検証項目が独立して重く、UI と混ぜると PR が肥大する。
4. **学習者の直接疑義投稿を意図的に外した理由**: 地位勾配（§8-3）への配慮は UI 設計だけで
   解決できず、運用観察が要る。間接経路（部品6）と読み取り導線を先に育てる。
5. **Field Atlas との命名衝突**: 構想の「前提の地図（Assumption Atlas）」と実装済みの
   「分野の地図（Field Atlas）」は別物。コード・API・UI 文言のすべてで `doubt-` / `assumption-`
   プレフィックスを使い分ける（D2-3 に明記）。
