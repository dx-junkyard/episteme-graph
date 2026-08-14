# 分野マップ（Field Atlas）— コースバインディングの「該当なし」UX とドメインライフサイクル 設計書

- 状態: 設計確定（2026-07-20）
- 対象: コース⇄地図バインディング（S2）の該当なしケース、骨格ドメインの共有単位・ライフサイクル
- 親文書: `field_atlas_overlay_spec.md`（3層モデル）、`field_atlas_db_managed_skeleton.md`（migration 027）
  - 注記（2026-08-14）: `field_atlas_overlay_spec.md` の原本は消失している。現存するのは
    2026-08-14 の**再構成版**で、**旧§番号との対応は保証されない**。
- 関連層: G層（`guidance_layer_design.md`）、状態管理・通知基盤（migration 038/045）、L層（retire パターンの先例）

---

## 0. 背景 — 現状の問題

コースビルダーの「承認してコースを登録」直後に `POST /api/admin/courses/{id}/atlas-binding/propose`
が全ドメインの凍結骨格へトピック→概念のカバレッジを決定論的に照合し、教員が確認して保存する
（S2）。このフローには「該当する分野マップが存在しない」ケースの設計が無く、次の問題がある。

1. **0一致でも先頭ドメインが初期選択される。** `admin.js` の
   `initial = recommended || current_cartridge_id || proposals[0].domain_key` により、全ドメイン
   一致 0 でも辞書順先頭の無関係ドメインが選択済みで表示され、そのまま保存できる。
2. **誤バインドは fail-closed ゲートをすり抜ける。** 明示 `cartridge_id` は
   `course_has_skeleton_anchor` の妥当性ゲートを免除する（authoring-time の意思の尊重）ため、
   0一致のまま保存すると学習者には「いまここ」が一つも点かない無関係な地図が出る。
3. **新分野への出口が行き止まり。** 凍結骨格が無い場合はテキストのみ表示され、教員は
   コースビルダーを離れて分野の地図タブで domain_key を発明し、生成→レビュー→凍結を済ませて
   コース管理まで戻る必要がある。導線は張られていない。

共有単位・ライフサイクルの現状:

- `atlas_skeletons`（migration 027）には owner も visibility も無い。共有単位は
  **システム全体 × domain 単位の共同財**。TEACHER 以上なら誰でも任意ドメインの
  draft 生成・編集・凍結ができる。
- ライフサイクルは `シード取込 → generate → draft → freeze → 配信 → 修正報告 → 再凍結` で、
  **終端（retire）が無い**。実験的骨格も凍結した瞬間から全教員のバインド候補に永久に並ぶ。
- **改版時のバインディング再検証が無い。** binding 保存時は当時の凍結版に対して node_id を
  検証するが、後続版で概念が削除・改名されても既存コースの `atlas_node_id` は放置され、
  `match_topic_to_concept` が未知 id を黙ってラベル一致へ縮退させる（silent に壊れる）。
- 凍結・retire の**通知が無い**（監査 `theory_review_events` のみ）。

## 1. 設計原則（不変条項）

- **AB1 一致ゼロは正常な状態であり発見**（D層の空欄スコープと同思想）。エラー表示・警告色・
  無理に埋めさせる誘導をしない。事実文で提示する。
- **AB2 共有単位は「システム全体の共同財」を維持する。** 分野の地図は分野の共通認識の投影で
  あり、教員ごと・グループごとに分裂させない。course/document 型の Visibility は持ち込まない。
- **AB3 削除しない。** ドメインの終端は `retired` 状態遷移のみ（L層と同じ）。凍結版履歴・
  既存バインド済みコースの学習者表示は維持する。
- **AB4 確定は教員。** 新分野の作成・バインド保存・retire はすべて教員の明示操作。
  仮予約（pending）は「意思の記録」であり自動確定しない。
- **AB5 数値・煽りを出さない**（G6 継承）。To-Do・通知・確認文はすべて事実文。
- **AB6 G層の完了フラグ禁止（G1）を継承。** pending の解消・stale の解消はサーバ状態から
  毎回導出する。
- **AB7 明示バインドのゲート免除は維持するが、入口で結果を事実として示す**（0一致確認）。
- **AB8 domain_key と cartridge_id の役割分離を明文化する**（§7）。

## 2. 該当なし UX（コース承認直後・学習マップ編集共通）

### 2.1 既定値の変更

- `recommended` が空（全ドメイン一致 0）のとき、初期選択は
  `current_cartridge_id || ""`（= バインドしない）。**proposals[0] への fallback を廃止する。**
- 一致ゼロ かつ 現行バインド無しのとき、事実文を表示する:
  「このコースのトピックに対応する分野マップは見つかりませんでした（N分野を照合）。」
  （N = `domains_checked`。retired 除外後の照合対象数）

### 2.2 3つの出口を明示する

1. **手動で対応付ける** — 現行の編集テーブル（ドメイン選択→topic ごとの概念選択）をそのまま使う。
2. **今はバインドしない** — 既定。一行添える:
   「今はバインドしなくても、『次にやること』からいつでも再開できます。」
   （G層 `course.no_atlas_binding` が残る事実の提示。督促文にしない）
3. **このコースから新しい分野マップを作る** — 新設導線（§2.3）。

### 2.3 コース起点の新分野作成 + 凍結待ち仮予約（pending binding）

インラインのミニフォーム（domain_key スラッグ / 分野名 = コースタイトルを prefill /
説明 = コース説明を prefill）から:

1. `PUT /api/admin/courses/{course_id}/atlas-binding/pending`（§4.2）で
   `course_data.atlas_binding_pending = domain_key` を保存（先に・軽量・確実）。
2. 既存 `POST /api/admin/cartridges/{domain_key}/atlas/skeleton/generate`
   （`body.domain` = {name, description}。domain_meta 永続化は既存挙動）で draft を生成。
3. 成功時: 「下書きを生成しました。『分野の地図』タブでレビュー・凍結してください。」+
   分野の地図タブへの導線。失敗時: pending は残る（分野の地図タブから再生成できる）。

pending は**意思の記録**であり、コースの地図表示には一切影響しない。propose レスポンスに
`atlas_binding_pending` を含め、バインド編集 UI に「新しい分野『Y』の骨格の凍結待ちです」
+ 取り消しボタン（DELETE）を表示する。

**バインド保存（PUT atlas-binding）が成功したら pending は自動クリアする**（解除保存を含む。
意思決定がなされたため）。

### 2.4 0一致バインドの保存ガード

明示 cartridge_id のゲート免除（AB7）は維持する。その入口で、**topic 対応が 0 件のまま
cartridge_id を保存しようとしたときのみ**、フロントで確認を挟む（禁止しない）:
「この地図には現在このコースの対応（足がかり）がありません。学習者には現在地が表示されない
地図が出ます。反映しますか？」

サーバ側は挙動不変（確認はクライアントの責務。UI 非経由の API 呼び出しは教員の明示意思とみなす）。

### 2.5 現行バインド先が候補に無い場合（retired 等）の維持と明示解除

現行バインド先（`current_cartridge_id`）が retired のとき、propose の候補（proposals）には
現れない（§3.1）。このとき編集 UI が候補から option を構築すると選択が空（バインドしない）へ
落ち、そのまま保存すると**既存バインドと topic 対応が無言で解除される** — これは AB3
（既存バインド済みコースの表示維持）の趣旨に反する。次のように扱う:

- propose レスポンスの `current_retired`（§4.1）を材料に、編集 UI は事実文の注記を出す:
  「現在バインドされている分野『X』は廃止済みのため、候補には表示されません。保存しない限り
  現在のバインドは維持されます（学習者の地図表示は変わりません）。解除するには
  『（バインドしない）』を選んで保存してください。」
- 現行バインドが候補に無い状態で選択が空のまま保存するときは、フロントで確認を挟む
  （解除を明示操作にする）: 「現在のバインド（分野『X』）とトピックの対応をすべて解除します。
  よろしいですか？」
- retired な現行バインドの**維持**は「保存しない」ことで実現する（retired ドメインを
  cartridge_id に再保存することはサーバが 422 で拒否するため、維持＝無変更が唯一の経路。
  学習者表示は §3.1 のとおり不変）。

## 3. ドメインライフサイクル

```
提案（コース起点 §2.3 or 分野の地図タブ）
  → generate → draft レビュー（複数教員・楽観ロック — pull 型・通知なし）
  → freeze（影響プレビュー付き・監査・関係教員へ通知）
  → 配信 → 修正報告 → incorporate → 再凍結（版履歴）
  → retired（バインド候補から退場・履歴と既存表示は保持・監査・通知）⇄ restore
```

### 3.1 retired 状態（migration 057）

- 保存先は `atlas_domain_meta`（migration 028）への列追加:
  `lifecycle TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active','retired'))` +
  `retired_at TIMESTAMPTZ` / `retired_by UUID` / `retire_note TEXT NOT NULL DEFAULT ''`。
  meta 行の無いドメイン（同梱カートリッジ等）は active とみなし、retire 時に upsert する。
- **retired の効果**:
  - binding propose の照合対象・候補から除外（`retired_skipped` で正直に件数を返す）。
  - `PUT atlas-binding` / `PUT atlas-binding/pending` で retired ドメインを指定したら 422。
  - generate / draft 保存 / freeze は 409（読み取り専用。L層 retired の先例に一致）。
    restore で active に戻してから編集する。**draft の破棄（削除）のみ retired 中も許可**
    — `DELETE /api/admin/cartridges/{id}/atlas/skeleton/draft`（§4.3）が後始末の明示経路。
    draft は共有物・履歴ではなく作業コピーのため、AB3「削除しない」（凍結版履歴・ドメイン
    自体が対象）の対象外。凍結版履歴・学習者表示には一切影響しない。
- **lifecycle 判定と書き込みの直列化**: retire / restore と書き込み系（generate / draft 保存 /
  freeze / draft 破棄）は check-then-write のため、domain 単位の
  `pg_advisory_xact_lock`（`atlas_store.lock_domain_for_write`）で直列化する。
  セッションを跨ぐ generate（LLM 生成を挟む）と freeze（報告処理を挟む）は、
  **書き込みトランザクション内で lifecycle を再確認**し、間に retire されていたら
  409 を返して何も書かない（draft は rollback で保持、P4）。
  - **学習者表示は不変**: `load_learner_skeleton` は lifecycle を見ない。バインド済みコースの
    地図・ミニマップは表示され続ける（AB3）。
- 削除 API は作らない。

### 3.2 凍結時の影響プレビュー

`GET /api/admin/cartridges/{id}/atlas/freeze-impact`（§4.4）が draft と現行凍結版を突合し、
「この凍結で消える node_id」×「このドメインにバインド中の全コースの topic 対応」を返す。
フロントは freeze 実行前にこれを表示し、影響があれば事実文で確認する
（「この凍結で N 概念が削除され、M コースの対応が外れます」）。凍結レスポンスにも同じ
impact 要約を含める。

### 3.3 孤児バインドの検出（G層新ルール）

`course.atlas_binding_stale`: バインド済み `atlas_node_id` が現行凍結版に存在しないコースを
検出する（§5）。silent なラベル一致縮退を「情報を落とさない」形で可視化する。

### 3.4 通知（freeze / retire）

- 配送基盤: `core/status/cross_layer_notify.py` の既存パターン（`source='status'`・
  best-effort・migration 不要）。kind を追加する:
  - `NOTIF_ATLAS_SKELETON_FROZEN = "atlas_skeleton_frozen"`
    payload: `{domain_key, version, removed_node_count, affected_course_count}`
  - `NOTIF_ATLAS_DOMAIN_RETIRED = "atlas_domain_retired"` payload: `{domain_key, note}`
  - entity_type = `'atlas_skeleton'`（既存 `AUDIT_ENTITY_ATLAS_SKELETON` と同語彙）、
    entity_id = domain_key。
- **宛先（関係教員）は2集合の和・actor 本人は除外**:
  1. **バインド中コースの所有者** — `learning_courses.data->>'cartridge_id' = domain_key`
     のコース所有者（利害の当事者。改版・退場が自コースの表示を変える）。
  2. **骨格の編集履歴のある教員** — `atlas_skeletons.created_by / updated_by`
     （作り手の当事者。誰でも凍結できる共同財の透明性担保）。
  - 宛先解決 SQL は `core/notification_recipients.py` にプリミティブとして追加し、
    集合の方針（和・actor 除外）は呼び出し側（atlas 層）に置く（横断基盤の分業規約）。
- **学習者には通知しない**（改版は静かに反映。changelog はオーバーレイ内表示で足りる）。
- restore は監査のみ・通知しない。draft レビューは pull 型のまま・通知しない
  （push が要るのは状態が不可逆に確定する freeze / retire の瞬間だけ）。

## 4. API 契約

すべて `_require_teacher`。コース系はさらに所有者 or SYSTEM_ADMIN（`_load_course_for_teacher`）。

### 4.1 propose（変更）

`POST /api/admin/courses/{course_id}/atlas-binding/propose` レスポンスに追加:

```json
{
  "domains_checked": 4,          // retired 除外後に照合した凍結骨格ドメイン数
  "retired_skipped": 1,          // retired のため照合しなかったドメイン数（正直さ）
  "atlas_binding_pending": "",   // course_data.atlas_binding_pending（無ければ空）
  "current_retired": false       // 現行バインド先が retired か（§2.5 の注記・明示解除の材料）
}
```

retired ドメインは `proposals` に含めない。`recommended` ロジックは不変。

### 4.2 pending（新設）

- `PUT /api/admin/courses/{course_id}/atlas-binding/pending` body `{"domain_key": "..."}`
  - domain_key は非空スラッグ（`[a-z0-9_]+`）。retired ドメイン指定は 422。
  - `course_data.atlas_binding_pending` を保存。
  - 監査: `entity_type='atlas_binding'` action=`pending_set`。
- `DELETE /api/admin/courses/{course_id}/atlas-binding/pending`
  - クリア。監査 action=`pending_clear`。
- `PUT /{course_id}/atlas-binding`（既存）は保存成功時に pending をクリアする。
- `PUT /{course_id}/atlas-binding` は retired ドメインの cartridge_id 指定を 422 にする。

### 4.3 retire / restore（新設）

- `POST /api/admin/cartridges/{cartridge_id}/atlas/retire` body `{"note": ""}`（note 任意）
  - domain 不存在は 404（`_ensure_domain_exists`）。既に retired は 409。
  - `atlas_domain_meta` へ upsert（lifecycle='retired', retired_at/by, retire_note）。
  - 監査: `entity_type='atlas_skeleton'` action=`retire`。通知 §3.4。
- `POST /api/admin/cartridges/{cartridge_id}/atlas/restore`
  - retired でなければ 409。lifecycle='active' へ。監査 action=`restore`。通知なし。
- `GET /api/admin/atlas/domains`（既存）の各要素に `lifecycle` を追加（既定 'active'）。
- generate / draft 保存 / freeze は retired ドメインで 409
  （detail に「廃止済みです。復帰してから編集してください」相当の事実文）。
- `DELETE /api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft`（新設・後始末）
  - draft を破棄する。**retired ドメインでも許可**（retire 後に残った draft の唯一の
    後始末経路）。draft が無ければ 404。監査: `entity_type='atlas_skeleton'`
    action=`draft_discard`。通知なし。UI は「下書きを破棄」（事実文 confirm 付き。
    retired 中も有効）。

### 4.4 freeze-impact（新設）

`GET /api/admin/cartridges/{cartridge_id}/atlas/freeze-impact`

- draft が無ければ 404。現行凍結版が無ければ removed は空（初回凍結は常に無影響）。
- レスポンス:

```json
{
  "cartridge_id": "...",
  "frozen_version": "2026.1",          // 比較対象（無ければ ""）
  "removed_node_ids": ["c1", "r2"],    // 凍結版にあり draft に無い concept/region id
  "added_node_ids": ["c9"],            // 参考情報
  "affected_courses": [                 // removed を atlas_node_id に持つバインド中コース
    {"course_id": "...", "title": "...",
     "topics": [{"topic_id": "...", "title": "...", "atlas_node_id": "c1"}]}
  ]
}
```

- 既存 freeze エンドポイントは、凍結成功後に同じ impact を計算してレスポンスに
  `"impact": {...}` として含め、通知（§3.4）を best-effort で配送する（通知失敗は非致命）。

## 5. G層ルール追加（`core/admin_assistant/next_steps.py`）

いずれも severity=`recommended`、capability は既存 `course.atlas_binding` を再利用
（G3: registry 変更不要）。locate_plan は `course.no_atlas_binding` と同じ導線。

- **`course.atlas_binding_ready`** — 仮予約したドメインの骨格が凍結され、割り当てを
  完了できる状態。条件（AND）:
  1. `course_data.atlas_binding_pending` が非空
  2. `_course_needs_atlas_binding(data)` が True（未バインドのまま）
  3. pending ドメインに凍結骨格が存在（`atlas_store.load_learner_skeleton`）
  4. pending ドメインが active
  - reason 例: 「保留中の分野『Y』の骨格が凍結され、コース『X』の学習マップ割り当てを
    完了できます。」
- **`course.atlas_binding_stale`** — バインド済み node_id が現行凍結版に存在しない。条件:
  1. `course_data.cartridge_id` が非空
  2. いずれかの `topics[].atlas_node_id` が現行凍結骨格の concept/region id 集合に無い
  - reason 例: 「コース『X』のトピック対応 N 件が、現在の地図に存在しない概念を
    指しています。」（版数・数値スコアは出さない。件数は事実として可）
  - 骨格の読みは `atlas_store.load_learner_skeleton()`（ドメイン単位でキャッシュして N+1 回避）。
- **`course.no_atlas_binding`（既存）の変更**: `atlas_binding_pending` が非空のコースには
  出さない（二重督促の回避。ready ルールが凍結後に出る）。pending 中で骨格が未凍結の間は
  To-Do ゼロになるが、バインド編集 UI に凍結待ち表示が出るため受容する（既知の限界として記録）。

## 6. DB（migration 057 `057_atlas_domain_lifecycle.sql`）

```sql
ALTER TABLE atlas_domain_meta
    ADD COLUMN IF NOT EXISTS lifecycle TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle IN ('active', 'retired'));
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retired_by UUID;
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retire_note TEXT NOT NULL DEFAULT '';
```

- 冪等（毎起動再実行に耐える）。`atlas_binding_pending` は `learning_courses.data` JSONB 内
  のためスキーマ変更不要（アクセサは `core/course_data.py` に追加。素の dict 読み禁止 —
  Tier 3-18）。

## 7. domain_key と cartridge_id の役割分離（明文化）

- 現状、バインド保存は `learning_courses.data.cartridge_id` を書き、これは
  **①地図の分野（骨格の参照）** と **②解析語彙・W層レンズ等の文脈** の二重の意味を持つ。
- 本設計では名前空間は当面同一のまま維持するが、**binding 保存が意図するのは①のみ**である
  ことをここに明記する。地図しか持たない新分野（カートリッジファイル無し）を binding で
  指定しても、解析パイプラインは document 単位の設定で動くため実害は無い。
- 将来、②と独立に地図分野を差し替える需要が出た場合は `course_data.atlas_domain_key` を
  別キーとして導入する（本設計の非スコープ）。

## 8. フロントエンド（`admin.js`。ES5）

1. `atlasBindingRenderEditor`:
   - 初期選択 fallback の廃止（§2.1）・事実文・出口3つ（§2.2）・pending 表示 + 取消・
     新分野ミニフォーム（§2.3）・0一致保存確認（§2.4）。
2. 分野の地図タブ:
   - ドメイン選択肢に retired 表示（「（廃止済み）」）。retire / restore ボタン
     （retire は事実文の confirm 付き）。retired 中は生成・保存・凍結ボタンを無効化し
     理由を近傍表示。
   - 凍結前に freeze-impact を取得し、影響があれば事実文 confirm（§3.2）。
3. 通知: 既存 status インボックスのフロント描画に
   `atlas_skeleton_frozen` / `atlas_domain_retired` の表示ラベルを追加。

## 9. テスト・ガードレール

- `backend/tests/test_atlas_domain_lifecycle.py` — retire/restore（状態遷移・409/422/404・
  meta upsert・監査）、retired の読み取り専用（generate/draft/freeze 409）、
  propose の retired 除外 + `retired_skipped`、学習者表示の不変。
- `backend/tests/test_atlas_binding_pending.py` — アクセサ、pending API（設定/取消/監査/
  retired 422/スラッグ検証）、バインド保存での自動クリア、propose レスポンス。
- `backend/tests/test_atlas_freeze_impact.py` — removed/added の決定論計算、affected_courses
  の突合、freeze レスポンスの impact、通知 kind・宛先2集合・actor 除外・best-effort 非致命。
- G層: 新ルール2件 + no_atlas_binding の pending 抑制（既存 `test_next_steps_guardrails.py`
  の catalog 整合・禁止語彙・fail-closed を新ルールも通す）。
- 静的 UI（既存 `test_atlas_operations_ux.py` 型）: proposals[0] fallback の不在、
  0一致確認文の存在、新分野導線の存在、freeze-impact 呼び出しの存在。

## 10. 非スコープ（v1）

- グループ限定・非掲載（unlisted）ドメイン（必要になれば `object_group_permissions` の
  ポリモーフィック拡張で対応可能な設計余地のみ確保）。
- スチュワード制（凍結権限の所有モデル）。「誰でも凍結 + 監査 + 通知」で足りると判断。
- `atlas_domain_key` の cartridge_id からの分離実装（§7 は明文化のみ）。
- 修正報告の incorporate 時の報告者への通知。
- 学習者向けの改版通知。
- stale バインドの自動修復（検出と提示まで。付け替えは教員操作）。
