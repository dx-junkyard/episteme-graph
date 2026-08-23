# カテゴリギャップ候補 — 論文カテゴリ判定から地図カテゴリを育てる機能の仕様・UX検討まとめ

- 状態: **実装済み（v1-a / v1-b / v1-c / v1-d + 個人地図の暫定ノード、2026-08-11 — §10 実装記録参照。
  §8 の未決4件はオーナーの実装指示（2026-08-11「添付のように実装を修正せよ」）を裁定とみなし、
  パネル推奨どおり確定 — §10-2）**
- 日付: 2026-08-09（検討まとめ納品）/ 2026-08-11（実装）
- 親文書: `knowledge_landscape_design.md`（本機能は同 §12 ロードマップ **Phase 4「スキーマ進化」の最小実装**に位置づく）
- 関連: `field_atlas_skeleton.md` / `atlas_binding_lifecycle_design.md` / `knowledge_network_vision.md`
- 検討体制: Fable 5 指揮。Opus 5 調査班4系統（vision原則 / atlas不変条項 / candidate前例 / パイプライン配管）→
  Fable 5 検討パネル4観点（学習者体験・認識論 / 教員ガバナンス・運用負荷 / アーキテクチャ・ガードレール整合 /
  ビジョン軌道・前提への反論）。対立点は §4 に裁定つきで記録する。

---

## 0. 要旨

オーナー要望は「解析中の論文が既存の地図カテゴリ（領域=上位 / 概念=下位）に該当するかを判定し、
該当しなければ適切なレイヤーへカテゴリを追加できるようにしたい」。これを、個人が論文を理解・探索する
ための**個人地図**と、共同で参照する安定した**共有地図**を分ける要望として扱う。

**結論: 方向は vision と整合する（Phase 4 の予約席の前倒し）。ただし素案の3段階は次のとおり読み替える。**

| 素案 | 裁定 |
|---|---|
| 段階1: 候補を蓄積し教員レビューで可視化 | **採用**。論文ごとの未配置主題はまず個人地図の暫定ノードとして表示できる。共有地図のレビュー候補への浮上には**反復閾値（2論文以上）**を入れる。信号自体は1論文目から保存する（P4） |
| 段階2: 確定候補を draft へ自動追記 | **却下 → 読み替え**。AI/サーバが骨格 draft を書く経路はこのリポジトリに一つも存在せず、初の前例を作ることは KN-3/AB4 の実質改定になる。代わりに**決定論 JSON Patch の1クリック適用**（提案は読み取り専用 API・適用は教員の既存 `PUT draft`）にする。教員の操作感は confirm 1回で、UX 上の犠牲はほぼ無い |
| 段階3: クラスタリング（EmergentRegion） | **層の読み替え**。EmergentRegion は設計上「基準地図とは**別レイヤー**・candidate-only」（`knowledge_landscape_design.md` §12 Phase 3）。クラスタ成果を骨格へ合流させる読みは採らない。本機能の信号テーブルが Phase 3 のクラスタリング入力になる |

検討全体を貫いた軸は**個人地図・集合候補・共有骨格の層分離**である。「この論文に居場所がない」は
個人地図の事実であり、個人用の暫定ノードで直ちに表現できる。「複数の独立した論文で同じ主題が現れる」は
集合候補の信号であり、「共有地図にカテゴリを足す」は共有骨格の改版判断である。個人地図を単純に平均・
重畳して共有骨格へ反映してはならない。利用者数や閲覧の偏りを分野構造と取り違えるためである。論文由来の
反復信号だけを匿名・集約し、そこから共有骨格へ圧力が流れる流路に**人間の弁**を置くことが本設計の主題である。
既存の不変条項群（KN-3 確定は人間 / AB4 確定は教員 / LS7 地図の安定性 / AB1 一致ゼロは発見）は
すべてこの一点に収束する。

---

## 1. 前提となる現状の事実（調査班の確定事項）

1. **判定はすでに毎回走っている**。`landscape_placement` ステージが論文を凍結骨格へ配置し、置けない
   ドメインを `unplaced_domains` に理由付きで申告している。ただし `{domain_key, reason}` の2フィールド
   のみで「どのレイヤーに何が要るか」の構造を持たない（`agents/landscape_placement/schema.py:363-379`）。
2. **「領域には当たるが概念が無い」は現状表現不能**。`kind`/`region_id` は LLM 入力に既に渡っているが、
   プロンプトに粒度の問いが一切ない（`prompt.py:30-70`）。プロンプト・出力構造の変更が必須。
3. **unplaced は揮発する**。artifact（最新 run）にしか残らず、手動再提案経路は artifact を書かないため
   UI が古い値を読む既知の不整合がある（`routes/landscape.py:223-257, 400-445`）。P4（情報を落とさない）
   に照らし、専用テーブルへの永続化が必要。
4. **骨格に「1概念だけ足す」経路が存在しない**。freeze は draft を DELETE し（`atlas_store.py:605-610`）、
   draft を作る唯一の API は LLM 全体再生成で id が振り直される（`routes/atlas.py:250-348`）。id の振り
   直しは `landscape_placements.node_id`・`topics[].atlas_node_id`・学習者足跡を破壊する。
   **「現行凍結版を複製して次版 draft を作る」決定論 API の新設が、いかなる案でも前提になる。**
5. **JSON Patch 機構は再利用できる**。`apply_json_patch` + assist の propose→apply→validate→教員 PUT の
   作法（`atlas_generator.py:530-578` / `routes/atlas.py:905-963`）は draft を書かない。gap→patch は
   単一 `add` に落ちるので **LLM 不要の決定論生成**が可能（assist の日次コストゲートも不要）。
6. **validator を hard error にすると配置が全滅する**。`if errors: return None`（`validator.py:178-179`）
   → repair 3回失敗 → `placements=[]`。新フィールドは `_collect_unplaced` と同型の
   **warning-only soft collector** にしなければならない。
7. **最も近い既存パターンは D層 `assumption_nodes`**（migration 030）: コーパス反復（2論文以上）のみ
   候補化・cluster_key の ON CONFLICT 据え置き・確定/却下は状態遷移。次点で `atlas_correction_reports`
   の「採用（accepted）と反映（incorporated/applied_version）の分離」を借りる。
8. **上限の実態**: MAX_REGIONS=12 / MAX_CONCEPTS_PER_REGION=6 は draft 保存時の hard error。宇宙物理
   骨格は 10領域・うち4領域が概念満杯で、追加余地は構造的に希少。
9. **id_migrations は修正報告にしか適用されない**。コース binding・landscape 配置・学習者足跡は改版で
   node が消えると**静かに参照切れ**する（`atlas_view.py:112-121` の known_ids フィルタ等）。
   → **追加（add）は安全・再編（remove/rename/移動）は危険**という非対称が本件の安全性の根拠。

---

## 2. vision との整合 — 守るべき条項の写像

| 条項 | 本機能への適用 |
|---|---|
| KN-3 確定は人間 / AB4 確定は教員 | 候補は常に candidate 始まり。draft への自動書き込み経路を作らない |
| KN-4 3系統分離 | 候補の入力は**コーパス系のみ**（unplaced + 配置分布）。学習者の tension・問い・「まだ地図にない」トレイを入力・重み付け・ソートのいずれにも使わない。学習者信号から浮かぶのは教育的知識でありドメイン知識ではない |
| KN-1 神の視点を作らない | 「カテゴリ空白ダッシュボード」「地図カバー率」を作らない。候補はドメイン視点起点の一覧のみ |
| LS1 地図は正解ではなく投影 | 「穴」「不足」「未整備」の欠陥語彙を使わない。「この地図では言い表せなかった主題」の事実文 |
| LS3 再解析セマンティクス | 教員の却下を AI の再検出で復活させない（却下の永続性を DB レベルで担保） |
| LS4 evidence-based | 候補にも原文逐語 `evidence_quote` + `reason` 必須。verbatim 不一致はその候補のみ drop |
| LS5 / PN-4 数値非表示 | 「該当論文 N 件」バッジを出さない（教員にも）。**一覧は可、集計数値は不可** — 支持論文はタイトル列挙で示す |
| LS7 地図の安定性 | 配置層から骨格を書き換える経路を作らない。骨格の変更は draft→freeze の既存フローのみ |
| LS9 同期パスに LLM なし | 候補生成はパイプライン相乗り（非同期）のみ。レビュー画面を開いた瞬間に LLM を呼ばない。patch 生成も決定論 |
| LS10 配置不能は信号 | 本機能はこの条項の直接の履行。ただし「信号」を「指示」に格上げしない（自動改版・督促をしない） |
| AB1 一致ゼロは発見 | エラー色・警告アイコン・埋めさせる誘導を使わない |
| AB2 共同財 | 1教員の1論文が共同財の骨格を変える導線を作らない（反復閾値 + 骨格レビュー文脈での確定） |
| AB3 / P4 削除しない・落とさない | 信号・判断とも行削除 API なし。却下は状態遷移で保持 |
| G1 / PN-2 導出であって記録ではない | レビューキューは信号+判断から毎回導出。次版に概念が入れば候補は自然消滅し、完了フラグ・掃除バッチが不要 |

---

## 3. 全観点が一致した点（合意事項）

1. **段階2「draft 自動追記」の却下**（4観点全員）。決定論 JSON Patch の1クリック適用へ読み替える。
2. **生成は landscape_placement の同一 LLM コールに相乗り**（追加ステージ・追加コールなし。CostGate は
   既存 `LANDSCAPE_MAX_CALLS_PER_DAY` を共有）。検証は soft collector・verbatim 検査・上限
   `LANDSCAPE_GAP_MAX_PER_DOCUMENT=3`・プロンプトに「placements 最優先・gap は最後・任意」を明示。
3. **専用テーブルへの永続化**（artifact 相乗りは既知の不整合を継承するため不可）。保存は builder の
   `_persist` と同一トランザクション。
4. **却下の永続性**を決定論 cluster_key で DB レベルに持つ（assumption_nodes 型）。
5. **レビュー UI は「分野の地図」タブの修正報告セクションに同居**させる（第4のレビューキュー・専用
   タブを新設しない）。地図を直す材料（学習者の修正報告・論文由来の候補）と直す手段（次版 draft・
   公開前チェック）を同一画面に揃える。
6. **「現行凍結版→次版 draft 複製」決定論 API の新設**が前提（§1-4）。
7. **採用（accepted）と反映（applied_version）の分離**（migration 046 の前例踏襲）。公開前チェックに
   「採用済みで次版未反映の候補」ゲートを修正報告と同列で追加。
8. **gap 駆動の改版は additive-only**（op=add のみ）。改名・統合・移動は年次改版に隔離する
   （id_migrations が足跡・配置に適用されない現実装では、再編は静かな参照切れを起こすため）。
9. **満杯領域への候補は取り込みボタンを非活性**にし、理由と解消方法の事実文を添える（無効化されうる
   ボタンのマニュアル節規約に従う）。分割・統合の自動提案は v1 で作らない。
10. **共有地図の候補・教員判断・集約結果は学習者に見せない**。ただし本人が扱う論文の未配置主題は、個人地図の暫定ノードとして本人にだけ表示できる。個人地図のノードは共有骨格を変更せず、個人の閲覧・行動量で共有候補を重み付けしない。共有地図の新ノードは凍結後、霧（未訪問）として静かに現れる。NEW バッジ・更新告知・貢献表示・changelog の学習者向け表示は作らない。
11. **数値を出さない**（教員にも）。支持論文は件数でなくタイトル列挙。
12. **学習者信号を入力に混ぜない**（KN-4）。v1 の入力はコーパス系のみ。

---

## 4. 対立点と裁定

パネルで実際に対立した論点。裁定は指揮（Fable 5）による。

### 4.1 単発（1論文）の候補化を認めるか

- **教員ガバナンス / ビジョン軌道**: 認めない。1論文即候補は「自分の論文を置くために共同財を膨らませる」
  圧力を各教員に与え、骨格が個人コーパスの和集合に劣化する（AB2 の実質崩壊）。1本の論文の主題は分野の
  カテゴリではない。
- **オーナー要望の字義**（「そのとき取り扱っている論文の…追加したい」）は単発起点を含意する。
- **裁定**: **信号は1論文目から保存するが、レビューキューへの浮上は同一 cluster に distinct document ≥ 2
  を必須とする**（assumption_mining の `MIN_DOCUMENTS_FOR_CANDIDATE=2` を移植）。単発の信号は既存の
  教材管理 landscape モーダルの unplaced 事実行に「地図への候補として記録されています」の一行を足す
  だけに留める。**単発でも今すぐ追加したい教員の出口は候補機構ではなく、既存の骨格編集**（分野の地図
  タブで次版 draft を編集する明示操作。このとき候補カードの evidence を参照材料にできる）。
  → オーナー確認事項（§8-1）。

### 4.2 cluster_key に skeleton_version を含めるか

- **パイプライン / アーキテクチャ**: 含める（新版凍結で概念が入った候補が自動無効化される）。
- **教員ガバナンス**: 含めない（凍結のたびに却下済み候補が蘇る「ゾンビ候補」を生み、同じ判断を版ごとに
  要求する — G4 違反の運用負荷）。
- **裁定**: **教員ガバナンス案を採る**。キーは `(domain_key, parent_region_id or '',
  normalize_label(proposed_label))` の版非依存とし、skeleton_version は**刻印列**として持つ
  （旧版由来は `version_mismatch` チップで表示 — `atlas_reports.summarize_queue` と同型）。
  「新版で解消された候補の自動消滅」は、読み時導出で現行凍結版の概念集合と突合すれば版キー無しで
  実現できるため、版キーの利点は却下ゾンビのコストに見合わない。

### 4.3 候補を「行」として蓄積するか「読み時導出」にするか

- **アーキテクチャ**: candidate 行方式（ハウススタイル: landscape/assumption 型）。
- **ビジョン軌道 / 教員ガバナンス**: 2層分離 — 論文単位の**信号**は行として蓄積（supersede 型）、
  cluster 単位の**候補**は読み時導出、**教員の判断**（採用/却下/反映）のみ第2テーブルの行として持つ
  （PN-2/G1: 導出であって記録ではない）。
- **裁定**: **2層分離を採る**。導出方式は (a) 次版で概念が入った候補の自然消滅、(b) supersede 連鎖の
  回避、(c) 完了フラグ・掃除バッチの不要化を同時に満たす。アーキテクチャ観点の要求（却下永続の
  DB 担保・ON CONFLICT・語彙の migration 一致検査）は判断テーブル側で全て満たせる。
  なお「信号行を反復閾値で INSERT 制限する」案は却下（閾値未満の信号が再解析ごとに揮発・再出現する。
  行は初回から作り、閾値は表示と G層にのみ使う — アーキテクチャ観点の指摘を採用）。

### 4.4 個人地図の暫定ノード（骨格の外側の居場所）を作るか

- **提案**: 作る。共有骨格に置けない論文の主題を、本人だけが見る個人地図の暫定ノードとして示す。論文には即時の居場所を作れる一方、暫定ノードは共有骨格にも共有候補にも直接書き込まれない。
- **懸念**: 個人地図をそのまま重畳すると、閲覧量・利用者の偏り・個人の関心を分野構造と誤認する。共有候補へ流せるのは、出所論文と根拠を持つ論文由来 signal のみとする。
- **裁定**: **個人地図の暫定ノードは v1-b に採用する**。共有地図の改版経路は「匿名・重複排除した論文信号 → 反復閾値 → 教員レビュー → 次版 draft → 凍結」の一本道に保つ。複数論文をまとめた共有の浮遊アンカーや EmergentRegion は Phase 2 / Phase 3 の検討対象として残す。

### 4.5 学習者に「配置ゼロ」の事実文を見せるか

- **学習者体験**: 見せる。出典タブ「分野の中の位置づけ」で、配置ゼロの論文に
  「この論文は、現在の分野の地図（版 {version}）のどの領域にも配置されていません。」の一行を通常
  テキスト・アイコンなし・行動喚起なしで出す。現行実装は placements ゼロを節ごと silent 非表示にして
  おり、「取得失敗の fail-closed」と「置けなかったという発見」を同じ非表示に潰している（AB1 の学習者側
  への適用）。
- **実装保守側**: fail-closed の現行挙動維持。
- **裁定**: **学習者体験案を採る**。ただし節内に配置済み論文が1件も無い場合は現行どおり節ごと非表示
  （データ有無の判別がつかないため）。この変更は候補機構と独立に価値があるため、切り出して先行実装
  してもよい。

### 4.6 G層 To-Do を追加するか

- **アーキテクチャ**: `atlas.category_gap_pending`（recommended・全却下抑止つき）を3点セットで追加。
- **教員ガバナンス / ビジョン軌道**: v1 では追加しない。通知面は既に多く、地図の改版は年次〜低頻度の
  重い意思決定で To-Do 駆動に馴染まない。分野の地図タブのローカルナビ表示で足りる。
- **裁定**: **v1 では追加しない**。運用してレビュー導線が実際に見落とされる事実が出たら、全却下抑止
  つきで追加する（そのときの仕様はアーキテクチャ観点の案をそのまま使う）。

---

## 5. 推奨仕様（v1）

### 5.1 生成（パイプライン相乗り）

- `landscape_placement` の同一 LLM コールに出力セクション `category_gaps` を追加（新ステージ・新コール
  なし。`PIPELINE_STAGES` / `LLM_STAGE_NAMES` 無改変）。
- 1件のフィールド: `layer`(`region`|`concept`) / `domain_key` / `parent_region_id`（layer=concept で必須・
  実在検査は warning）/ `proposed_label` / `reason` / `evidence_quote` / `confidence`（DB のみ・表示は
  段階ラベル）。
- プロンプト変更: 骨格をフラット列挙から `region → concepts[]` のネスト提示に変え「この領域の概念は
  この N 件だけ」という**閉世界**を伝える。各 region に `concept_slots_remaining` を決定論で添付。
  指示は「配置した region の配下概念がこの論文の対象を覆っていなければ concept 候補を申告 / どの
  region にも当たらない場合のみ region 候補 / **既存概念の言い換えを新概念にしない**（捏造ガード）/
  placements 最優先・gap は最後・任意・上限3件」。
- 検証: `_collect_category_gaps` を `_collect_unplaced` 同型の **soft collector** として実装
  （errors に一度も積まない）。evidence_quote は `quote_haystack` で verbatim 検査し、不一致は
  その候補のみ drop。repair 失敗時の gap は保存しない（根拠不明の gap を作らない）。
- 出力形変更に伴い `examples/landscape_placement_example.json` を同時更新（validator 通過契約）。

### 5.2 データモデル（migration 066・冪等）

**(a) `landscape_gap_signals` — 論文単位の構造化信号（AI 由来・供給側）**

```
id UUID PK / document_id UUID FK→documents(id) ON DELETE CASCADE / run_id
domain_key TEXT NOT NULL / skeleton_version TEXT NOT NULL（刻印）
layer TEXT CHECK IN ('region','concept') / parent_region_id TEXT DEFAULT ''
proposed_label TEXT NOT NULL / normalized_label TEXT NOT NULL
reason TEXT / evidence_quote TEXT / confidence（DB のみ）
status TEXT CHECK IN ('active','superseded')   -- 再解析は active のみ supersede（LS3 同型）
created_at / updated_at
```

**(b) `atlas_gap_decisions` — cluster 単位の教員判断（人間由来・弁側）**

```
cluster_key TEXT UNIQUE  -- gap|{domain_key}|{parent_region_id or ''}|{normalize_label(proposed_label)}
status TEXT CHECK IN ('accepted','dismissed','merged')
review_note TEXT（dismiss は必須・空 422）/ merged_into TEXT DEFAULT ''
draft_node_id TEXT DEFAULT '' / applied_version TEXT DEFAULT ''  -- 採用と反映の分離
decided_by UUID / decided_at / created_at / updated_at
```

- **レビューキュー（候補）は毎回導出**: active な signal を cluster_key でグルーピング →
  distinct document ≥ 2 のみ表示 → 現行凍結版の概念集合と突合して解消済みを除外 →
  dismissed 行が生きている cluster を抑止。完了フラグを持たない（G1）。
- documents への FK は signals のみ（論文削除で信号は消える）。decisions はコーパス横断の共同財行
  なので document FK を張らない。
- 語彙の正本は `backend/core/atlas_gaps/schema.py`（新 core モジュール・FastAPI/LLM SDK 非 import）。
  migration CHECK と 1:1（SQL パース検査）。store に `DELETE FROM` を書かない。
- 鮮度: 必要になれば `compute_source_fingerprint`（discuss_opening と同契約）を流用。

### 5.3 実装の置き場所

- `backend/core/atlas_gaps/`（schema / store / patching=決定論 JSON Patch 生成）。
  `core/landscape/builder.py` から store を呼ぶ（保存は `_persist` と同一トランザクション）。
- **gap 系コード・ルートから `atlas_skeletons` への INSERT/UPDATE が存在しない**ことをガードレール
  テストで固定する（配置層から骨格を書き換える経路の不在証明 — LS7 の構造的保証）。

### 5.4 レビュー UI（分野の地図タブ）

- `atlas-reports-section`（修正報告のレビュー）内に第2グループ **「論文の解析から見つかった候補」**
  を追加（専用タブ・専用キューを新設しない）。導入文: 「複数の論文が、この地図にまだ無い項目に
  触れています。」
- 候補カード: 提案ラベル（インライン編集可・id 自動採番プレビュー）/ 層バッジ（「領域」「概念
  （親: {region label}）」）/ **支持論文のタイトル列挙**（件数バッジなし）/ 各論文行クリックで
  reason + evidence 逐語を展開 / 出所ラベル「AIによる検出（未確認）」/ 旧版由来は
  「この候補は版 {version} の地図に対するものです」チップ。エラー色・警告アイコン禁止。
- ボタン3つ:
  - **[採用]** — accepted へ。「カテゴリとして妥当」の判断のみで draft は変わらない旨を説明文に明記。
  - **[却下…]** — 理由必須（空は送信不可）。注記「この分野で同じ名前の候補は今後表示されません。
    『見送り済み』フィルタから戻せます。」復活は明示 restore（状態遷移・行削除しない）。
  - **[次版の下書きに取り込む…]** — accepted かつ draft 存在時のみ活性。draft 不在時は同じ場所に
    「現在の版から次版の下書きを作る」（from-frozen 複製 API → 事実文 confirm「現行版 {version} を
    複製して下書きを作ります。下書きは学習者には表示されません」）。押下で patch プレビュー
    （追加 node の id・ラベル・位置 + validate 結果）→ 教員の既存 `PUT draft`（revision 楽観ロック）。
- 満杯領域（概念 6/6）: 取り込みボタン非活性 + 「この領域の概念は上限（6件）に達しています。追加
  するには次版で既存概念の整理が必要です。」ゲージ・空きスロット表示・督促はしない。
- 公開前チェック（freeze）: 「採用済みでまだ次版に反映されていない候補が残っています」+ 該当ラベル
  列挙で中止（修正報告ゲートと同列・同温度・件数なし）。凍結成功時に `apply_freeze_to_gaps` が
  draft_node_id の実在する accepted 行へ applied_version を刻印。
- 教材管理 landscape モーダルの unplaced 事実行には「この分野の地図への候補として記録されています →
  分野の地図タブで確認」のリンク一行のみ（モーダル内でレビューさせない。レビュー箇所を2つに増やすと
  監査 action と UI アンカーが分裂する）。**1論文の画面に「地図を直す」ボタンを置かない**。
- 管理 UI 3点セット（マニュアル節 + `ADMIN_UI_ANCHORS` + `data-ui-anchor`）を実装と同時に揃える
  （無効化されうるボタンは「無効になっている場合: 理由+解消方法」節を必ず持つ）。

### 5.5 骨格への反映（additive-only）

- 新設 `POST /api/admin/cartridges/{id}/atlas/skeleton/draft/from-frozen`（決定論・LLM 不要）:
  現行凍結版を draft へ複製。既存 draft あり / retired は 409。`lock_domain_for_write` +
  トランザクション内 lifecycle 再確認。
- patch は決定論生成（`{op:add, path:/regions/-}` または `/regions/{i}/concepts/-`）。id は
  `_slugify`/`_unique_id`（region/concept 同一名前空間・衝突回避）、concept 座標は所属領域内相対の
  空きスロットへ決定論配置（骨格ノード用の自動配置は現存しないため新設。重なりは warning 止まり
  なので完璧を要求しない）。
- **gap 経路の patch は op=add のみ**。remove / replace(label) / 概念の領域間移動を含めない。
  再編は年次改版（既存運用ルールの棚）に隔離する。
- `compute_freeze_impact` に「removed node を参照する学習者の足跡・現在地はその版から表示され
  なくなります（記録自体は残ります）」の事実文（件数なし）を追加 — gap 経路に限らず freeze 全般の
  改善として。
- 凍結完了画面に「既存論文の配置は再解析するまで変わりません」の事実文を出す（再配置バッチは
  Phase 4 の別スコープ。「カテゴリを足したのに論文が載らない」という空振りの期待を作らない）。

### 5.6 学習者側（v1 = 個人地図のみ追加）

- 共有地図の候補・accepted・draft・教員判断・集約数は学習者 API/DTO に出さない（gap 語彙の再帰キー走査ガードレールを追加）。
- 本人が扱う論文について、共有骨格に置けない主題は**個人地図の暫定ノード**として本人にだけ表示できる。暫定ノードは共有骨格の外側に置き、出所論文と根拠を持つが、共有地図へ書き込まない。
- 共有候補は、個人地図そのものの重畳ではなく、論文由来の信号を domain ごとに匿名・重複排除して集約する。閲覧回数、学習者の行動、個人の重みは入力・ソート・閾値に使わない（KN-4）。
- 版が上がっても告知しない。バナー・トースト・NEW バッジ・追加数表示なし。共有骨格の新ノードは未訪問の霧として静かに現れ、版文字列は既存のコーパス事実行（LS8）内でのみ更新される。
- 「あなたの読んだ論文が地図の空白を指しました」等の貢献・帰属演出は作らない（空白を指したのは論文であって学習者ではない。P7/KN-4）。
- 独立採用可: 出典タブの配置ゼロ事実文（§4.5 裁定）。
- v2 候補: 学習者が自分でオーバーレイを開いたときに限り、前回以降「まだ地図にない」トレイから地図へ
  移った項目に既存 `atlas-pulse` を1回だけ適用（学習者起点の開扉への応答なので「宣言」にならない）。

### 5.7 監査・ガードレール

- `AUDIT_ENTITY_CATEGORY_GAP = "category_gap"` を `AUDIT_ENTITY_TYPES` に追加。
  `services.record_review_event` に委譲し、metadata.action で `detect`（AI・old→candidate）/
  `accept` / `dismiss` / `restore` / `merge` / `incorporate` を区別。
- `backend/tests/test_atlas_gaps_guardrails.py`（`guardrail_helpers` 使用）:
  ① `core/atlas_gaps` の FastAPI/LLM SDK 非 import ② store に DELETE FROM 不在・公開面に
  delete/purge 名不在 ③ migration CHECK ⇄ schema 語彙の完全一致 + 冪等スタイル
  ④ DTO 再帰キー走査で confidence/weight/gap 語彙の非漏洩（教員向け DTO は confidence のみ検査）
  ⑤ validator の soft collector に `errors.append` 不在 ⑥ **gap 系コードに `atlas_skeletons` への
  INSERT/UPDATE 文が無い**（骨格書き込み経路の不在証明）⑦ 監査語彙の登録・重複なし
  ⑧ dismiss の review_note 必須 ⑨ プロンプトの捏造ガード文言 grep ⑩ 禁止語彙（欠陥語彙・督促語彙）grep。

### 5.8 コスト・モデル（M層/U層）

- 追加 LLM コールなし。既存 `LANDSCAPE_MAX_CALLS_PER_DAY` / scene `pipeline:landscape_placement` を
  共有。patch 生成・レビュー読み取り・複製 API はすべて非 LLM。

---

## 6. 段階計画（素案の読み替え）

| 段階 | 内容 | 備考 |
|---|---|---|
| **v1-a（独立先行可）** | 出典タブの配置ゼロ事実文（§4.5） | 候補機構と無関係に価値がある小変更 |
| **v1-b** | 信号の構造化・永続化（migration 066a: signals）+ 個人地図の暫定ノード + 単発 unplaced 行への案内 | 共有地図は変わらない。個人の論文には居場所を作り、共有候補のための論文由来データが溜まり始める |
| **v1-c** | 判断テーブル + 導出キュー + レビュー UI（採用/却下）| まだ draft は触らない |
| **v1-d** | from-frozen 複製 API + 決定論 patch 1クリック適用 + freeze ゲート/刻印 | ここで初めて地図が育つ |
| **Phase 2（別文書）** | 浮遊アンカー（§4.4）/ G層ルール / 学習者 atlas-pulse / 過去論文の再配置バッチ | 実測を見てから |
| **Phase 3（既定義）** | EmergentRegion（別レイヤー・candidate-only）。signals をクラスタリング入力に流用 | 骨格への合流はしない |

---

## 7. 非スコープ（v1）

- 領域・概念の削除/改名/統合の候補化（additive-only。再編は年次改版の人間判断）
- 分割・統合の自動提案（id_migrations の適用範囲が狭い現実装では参照切れが静かに起きる）
- 学習者信号（tension / structure_anchor / トレイ）の入力混合（KN-4）
- 候補件数バッジ・カバー率・横断ダッシュボード（KN-1 / LS5 / PN-4）
- 新版凍結後の過去論文の自動再配置（Phase 4）
- 浮遊アンカー / EmergentRegion（Phase 2 / 3）
- G層 To-Do ルール（運用実測後に判断）

---

## 8. 未決事項（オーナー裁定待ち）

1. **反復閾値（2論文以上）の確定** — パネルは全会一致で推奨するが、オーナー要望の字義（1論文起点）
   とは緊張する（§4.1）。単発で追加したい場合の出口は「既存の骨格編集を教員が明示的に行う」で足りるか。
2. **個人地図の暫定ノードの v1 優先度と寿命** — 「置けない論文に即時の居場所を与える」ため、個人地図を v1-b に含める。共有地図に候補が採用・反映された後も個人ノードを残すか、対応する共有ノードへ解決表示するかを決める。
3. **v1-a（配置ゼロ事実文）の先行実装** — 候補機構と切り離して先に入れてよいか。
4. **G層 To-Do の要否**（§4.6） — v1 は見送りが裁定だが、レビュー導線の発見性に不安があれば追加する。

---

## 9. 検討記録（観点別要旨）

- **学習者体験・認識論**: 学習者にとって地図の価値は「正しさ」ではなく「安定した座標系の上に自分の
  足跡と発見が積もること」。候補機構の全体が凍結の瞬間まで不可視である限り健全。最大のリスクは改版の
  頻度ではなく**質**（add は無害・再編は足跡を無言で消す）。「育ち」は告知ではなく、学習者自身が次に
  地図を開いたときの静かな差分として現れるべき。
- **教員ガバナンス・運用負荷**: 敵は「もう一つの放置されるレビューキュー」と「1論文ごとの椅子取り
  ゲーム」。反復閾値・既存画面への同居・3クリック以内の確定（id/座標/親領域の決定論事前計算）・
  却下の版跨ぎ永続、の4点が骨子。スチュワード制は不要（既存の「誰でも凍結+監査+通知+公開前チェック」
  で足りる）。
- **アーキテクチャ・ガードレール整合**: 層境界の構造的保証が本丸 — gap 系コードから骨格テーブルへの
  書き込み文が存在しないことをテストで証明できる形にする。atlas_correction_reports への相乗りは
  reporter_id NOT NULL（匿名不可 = 人間の帰属）の設計意図と衝突するため新テーブルが正。
- **ビジョン軌道（前提への反論）**: 「論文が置けない→骨格に足す」は四層モデルの層混同であり、骨格の
  価値は安定と薄さ（最小限の道路網）にある。オーナーの真の要求「いま扱っている論文に居場所がない」は
  骨格改版なしに満たせる（浮遊アンカー）。骨格昇格は反復+教員明示編集に限る稀な判断とせよ。
  段階3を「クラスタで骨格に足す」と読んではならない。

（各観点の全文・調査班4系統のダイジェストはセッション成果物として別途保存。実装着手時は本文書を
正式な設計書に昇格させ、migration コメント・ガードレールテストから本ファイル名を参照すること。）

---

## 10. 実装記録（2026-08-11, Fable 5 指揮 / Opus 5 実働 6 タスク・3ウェーブ）

### 10-1. 実装範囲と置き場所

§6 の v1-a〜v1-d と §4.4 の個人地図の暫定ノードを一括実装した（migration は **066**）。

| 層 | 実体 |
|---|---|
| 生成（§5.1） | `src/episteme_graph/agents/landscape_placement/` — `CategoryGapRecord` / region→concepts ネスト提示 + `concept_slots_remaining` / `_collect_category_gaps`（warning-only soft collector・errors 非接触を構造テストで固定）/ `max_gaps_per_document`（既定3） |
| データ（§5.2） | `backend/db/066_category_gap_signals.sql`（`landscape_gap_signals` + `atlas_gap_decisions`）+ `backend/core/atlas_gaps/`（schema / store / patching）。保存は `core/landscape/builder.py` の `_persist` と同一トランザクション・document 単位 supersede・空入力 SQL 非発行。detect 監査は builder トランザクション同乗の直接 INSERT（persistence.py と同じ例外扱い） |
| 管理 API | `backend/api/routes/atlas_gaps.py`（gap-candidates 読み時導出 / decide / incorporate-preview（DB 非変更）/ mark-incorporated）+ `routes/atlas.py`（`draft/from-frozen` 決定論複製・freeze の公開前チェック 409 `{message, pending_labels}`・凍結と同一トランザクションでの `applied_version` 刻印）+ `core/atlas_lifecycle.py`（freeze impact の件数なし事実文 `facts[]`）+ admin placements の `gap_signals_recorded` フラグ |
| 管理 UI（§5.4） | `admin.js` — atlas-reports-section 内 第2グループ「論文の解析から見つかった候補」。取り込みは preview → 既存 `applyAssistProposal` + `saveDraft()`（PUT draft）→ mark-incorporated の3手で、gap 系 UI から骨格への直接書き込みなし。アンカー7件（`atlas.gap-*`）+ マニュアル節（teacher/17-admin-atlas.md 7節 / 11-admin-materials.md 1節）で3点セット完備（アンカー総数 248→255） |
| 学習者 v1-a（§4.5） | 学習者 landscape API に `unplaced_documents` + `skeleton_version`、出典タブに配置ゼロ事実文（版が引けないときは出さない fail-closed。節内に配置済み論文ゼロなら従来どおり節ごと非表示）。student マニュアル追記 |
| 学習者 v1-b（§4.4） | `backend/core/personal_graph/provisional.py` + コースビュー `personal-network` の `provisional_nodes`（読み時導出・上限12・キーは id/label/documents[].title/evidence_quote/source_label の5つ厳密）。personal-map.js の骨格外ストリップ「地図の外の主題（あなたの教材から）」+ 事実カード |

### 10-2. §8 未決事項の確定

1. **反復閾値**: 採用（`MIN_DOCUMENTS_FOR_CANDIDATE = 2`）。単発の出口は既存の骨格編集。
2. **個人地図の暫定ノードの寿命**: 読み時導出により「①現行凍結骨格に同名（正規化一致）の領域・概念が現れた時点 ②当該 document の再解析で signal が superseded になった時点」の早い方で自然消滅。共有ノードへの解決表示は作らない（学習者に改版・貢献を告知しない §5.6 を維持）。
3. **v1-a の先行実装**: 実施（独立タスクとして実装・全体と同時納品）。
4. **G層 To-Do**: 見送り（§4.6 裁定どおり）。

### 10-3. 設計書からの意図的逸脱

- `atlas_gap_decisions.status` に **`'candidate'` を追加**（restore を行削除なしで実現するための最小逸脱。P4/AB3 と restore 監査アクションの両立）。
- decide の `merge` は v1 では **422**（語彙・列は予約済みだが導線を作らない — §7 非スコープ）。
- ラベル正規化は**二重実装を意図的に併存**: agent 側 `normalize_gap_label`（言い換え申告の保守的 drop 用・強い同一視）と backend `core/atlas_gaps/schema.py::normalize_label`（cluster_key の正本・NFKC+casefold+空白畳み込み）。
- `record_signals` は不正 1 件を drop（raise しない）— gap ノイズで placements の保存を巻き戻さない。
- cluster_key とパスの分野の食い違い操作は 422（fail-closed の追加）。incorporate-preview は `proposed_label` 上書き受け口を持つ（§5.4 のインライン編集）。
- `SkeletonCapacityError` は概念満杯に加え領域満杯（MAX_REGIONS）でも送出。
- from-frozen 複製は `changelog` を引き継ぎ **`id_migrations` は引き継がない**（次版での二重付け替え防止）。

### 10-4. 検証状態

- backend **8,708 passed / 25 skipped**・src **1,803 passed**（2026-08-11、backend/.venv ローカル実行）。
- ガードレール `test_atlas_gaps_guardrails.py`（12 クラス）が §5.7 の①〜⑩を網羅（⑤ soft collector の構造検査は `src/tests/agents/landscape_placement/test_category_gaps.py` 側にも保持）。
- 未実施: docker 実機 E2E（066 の実 DB 適用・実 LLM での gap 申告品質）・コミット分割。
