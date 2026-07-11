# ガイダンス層（G層）設計書 — 「次にやること」バッジ + 状態導出型 To-Do + 地図 fail-closed 徹底

## 0. 背景と課題

管理画面は機能の積層（A〜D/R/V層）により強力になった一方、**初見の教員には「次に何を
すればよいか」が読み取れない**。具体的な症状:

1. **教材を登録しても次の一歩が示されない** — 解析パイプラインは走るが、その後
   「コース構築へ進む」という導線がどこにも出ない。
2. **操作アシスタント（Admin Copilot）の存在が知られていない** — ヘッダー右上の小さな
   テキストボタン（`admin.html` の `#admin-copilot-toggle`）のみで、初回ユーザーは気づけない。
3. **プルダウン化されたメニューは場所を知らないと押せない** — 「分野の地図」「原稿スタジオ
   （音声生成）」等はタブグループ（コンテンツ / 学習インサイト / ナレッジ基盤 / 運用）の
   中に畳まれており、存在自体が発見できない。
4. **未設定の分野の地図が学習画面に出る** — コース文脈が無いとき `atlas-data.js` が
   `DEFAULT_CARTRIDGE = "particle_physics"` へフォールバックするため、無関係な素粒子物理の
   地図が学習者に表示され混乱を招く（gap3 ゲートの塞ぎ残し）。
5. **地図のコース割り当てを教員に促す仕組みがない** — atlas-binding（S2）は course-builder
   登録直後の領域と「学習マップ編集」から操作できるが、やらなくても何も起きないため放置される。

本設計はこれらを **「状態から導出する To-Do（Next Steps）」+「ヘッダーバッジ」+
「既存 Copilot 道案内（spotlight）への接続」** で解決する。

## 1. 設計原則（不変条項）

既存層の文化を継承する。

- **G1 完了フラグを持たない**: To-Do はサーバ状態（教材・解析 run・コース・binding・公開状態）
  から**毎回決定論的に導出**する。タスクを実施すれば項目は自動消滅する。完了トラッキング用の
  カウンタ・進捗テーブルは作らない。
- **G2 非LLM・同期**: Next Steps の導出はルールベースの投影のみ。LLM を呼ばない（P6 と同型）。
- **G3 Capability Registry を単一の真実源として再利用**: 各 next-step ルールは必ず
  `core/admin_assistant/capabilities.py` に登録済みの capability を参照する。ロールで
  fail-closed（P1）— そのロールで実行できない操作は To-Do に出さない。
- **G4 押し付けない**: バッジは件数を示すだけで、パネルを自動で開かない。モーダルや
  強制ツアーにしない（atlas cues の「カード提示に留める」文化、P7 と同型）。
- **G5 却下は保持**: 「このコースには地図を割り当てない」等の意図的な非対応は
  `dismissed` として永続化し、行削除しない（P4）。パネル内「非表示にした項目」から復元可能。
- **G6 理由は事実文で**: 各項目は「なぜ今これが出ているか」を根拠付きの事実文で示す
  （例: 「教材『◯◯』はどのコースからも参照されていません」）。煽り文句・推奨度の数値は出さない。
- **G7 既存層のコードを変更しない**: A/B/C/D/R/V 層の core は読むだけ。書き込みは
  dismissal と監査イベントのみ。
- **G8 道案内は誘導まで**: バッジ項目の「案内する」は既存 `AdminAssistant.runLocatePlan()`
  を呼ぶだけ（画面遷移 + spotlight + hint。値入力・送信は本人。P8 継承）。

## 2. 全体アーキテクチャ

```
┌─ ヘッダー右上 ──────────────────────────────────────┐
│  📋 次にやること (3)   🤖 操作アシスタント            │
└──────────┬───────────────────────────────────────┘
           │ クリック（自動では開かない, G4）
           ▼
   ┌─ Next Steps パネル（ドロップダウン）────────────┐
   │ ● 必要                                          │
   │   教材『量子力学入門』からコースが作られていません │
   │   → [案内する] [今はしない]                      │
   │ ○ 推奨                                          │
   │   コース『◯◯』に学習マップが未割り当てです       │
   │   → [案内する] [今はしない]                      │
   │ ─────────────────────────────                  │
   │ 非表示にした項目 (1) ▸                           │
   │ その他の質問は 🤖 操作アシスタントへ              │
   └──────────┬─────────────────────────────────┘
              │ [案内する]
              ▼
   AdminAssistant.runLocatePlan(step.locate_plan)
   = activateTabView（プルダウングループも自動同期）
     → registerUiAnchors で DOM 解決 → scrollIntoView
     → .admin-assistant-spotlight 点灯 + hint 吹き出し
```

- **バックエンド**: `backend/core/admin_assistant/next_steps.py`（新規。core は FastAPI 非 import）
  + `backend/api/routes/admin_assistant.py` にエンドポイント追加。
- **フロントエンド**: `frontend/public/js/admin-next-steps.js`（新規, ES5・IIFE・
  `window.AdminNextSteps`）。道案内は既存 `admin-assistant.js` の公開 API を呼ぶだけで、
  spotlight 機構を二重実装しない。
- **更新タイミング**: ログイン時 / タブ切替時 / 教材アップロード完了・コース登録完了などの
  画面イベント後に再取得。**ポーリングしない**（atlas minimap と同じ規律）。

## 3. Next Steps エンジン（`core/admin_assistant/next_steps.py`）

```python
@dataclass
class NextStep:
    step_key: str          # "{rule_id}:{target_id}" — dismissal の主キー
    rule_id: str           # ルールカタログの ID
    severity: str          # "required" | "recommended" | "optional"
    title: str             # 「この教材からコースを作成する」
    reason: str            # 事実文 + 根拠（G6）
    capability_id: str     # 登録済み capability（G3, fail-closed）
    locate_plan: dict      # capability.locate_steps を target で具体化したもの
    target: dict           # {"material_id": ...} / {"course_id": ...}
    dismissible: bool      # required の一部（解析失敗など）は dismiss 不可にしてよい
```

`compute_next_steps(session, user) -> list[NextStep]` は以下を行う:

1. ユーザーのロールで `capabilities_for(role)` を先に取り、参照 capability が権限外の
   ルールは**評価すらしない**（G3 fail-closed）。
2. 各ルールを決定論的に評価（下記カタログ）。対象は**本人が所有する**教材・コースのみ
   （`uploaded_by` / `user_id`。共有 editor は将来拡張）。
3. dismissal テーブルと突合し、dismissed は `hidden` セクション用に分離して返す。
4. severity 順・古い順に整列。**上限 10 件**（多すぎる To-Do は To-Do でない。
   切り捨てが起きたことはレスポンスの `truncated: true` で正直に返す）。

### 3.1 ルールカタログ v1

| rule_id | severity | 条件（すべて既存テーブルの読み取りのみ） | 参照 capability | 案内先 |
|---|---|---|---|---|
| `materials.none` | required | 自分の教材が 0 件 | `materials.upload` | 教材管理・アップロード枠 |
| `material.analysis_failed` | required | `document_analysis_runs` の最新 run が failed | `materials.upload`（v1 は道案内のみ） | 教材管理・該当行 |
| `material.no_course` | required | 解析完了済み教材が、どのコースの `data.sources[].material_id` にも含まれない | `course_builder.open` | コース構築・教材選択 |
| `course.not_published` | recommended | `is_template=true` かつ `is_published=false` | `course.publish` | コース管理・公開ボタン |
| `course.no_atlas_binding` | recommended | コースに `data.cartridge_id` 明示が無く、全 `topics[].atlas_node_id` が空 | `course.atlas_binding`（新規登録） | コース管理・学習マップ編集 |
| `course.audio_missing` | optional | 原稿生成済みチャンクがあり音声キャッシュ未生成トピックが残る | `lecture_studio.generate_audio`（新規登録） | 原稿スタジオ |

v1 はこの 6 ルールに限定する（段階登録の方針は capability registry と同じ）。将来候補:
グループ未共有の editor 向け教材、reconstruction review-queue の滞留、地図修正報告の未処理など。

**チェーン設計**: ルールは「次の一歩だけ」を出すよう依存関係を持つ。教材だけ登録された
段階では `material.no_course` が立ち、コース登録後にはそれが自動消滅して
`course.no_atlas_binding` / `course.not_published` が現れる。ユーザーが常に見るのは
**現在の状態から到達可能な直近のタスクだけ**であり、全工程のチェックリストを最初から
見せて圧倒しない。

### 3.2 追加 capability（`capabilities.py` へ登録）

```python
Capability(
    id="course.atlas_binding",
    screen="course-management",
    title="コースに学習マップ（分野の地図）を割り当てる",
    required_role=ROLE_TEACHER,
    kind=KIND_GUIDANCE_ONLY,   # v1 は道案内のみ。代行は将来（propose API は既存）
    howto_doc="admin_operations/course.md#atlas-binding",
    locate_steps=(
        _step("course-management", "course_row:{course_id}", "対象のコースを選びます"),
        _step("course-management", "atlas_binding_button", "『学習マップ編集』を開きます",
              precondition="course_selected"),
    ),
),
Capability(
    id="lecture_studio.generate_audio",
    screen="lecture-studio",
    title="コース原稿の音声を生成する",
    required_role=ROLE_TEACHER,
    kind=KIND_GUIDANCE_ONLY,
    howto_doc="admin_operations/lecture_studio.md#audio",
    locate_steps=(
        _step("lecture-studio", "ls_course_select", "対象のコースを選びます"),
        _step("lecture-studio", "ls_audio_generate", "音声生成を実行します",
              precondition="course_selected"),
    ),
),
```

併せて `admin.js` の `registerAssistantHooks()` に不足アンカーを追加する
（`atlas_binding_button` / `ls_course_select` / `ls_audio_generate`、および
`material_row:{id}` / `course_row:{id}` の**行単位解決**——現状は tbody 全体を返しており
spotlight が粗い。`data-material-id` / `data-course-id` 属性で行を特定する）。

## 4. API（`routes/admin_assistant.py` に追加）

- `GET /api/admin/assistant/next-steps`
  → `{steps: [...], hidden: [...], truncated: bool}`。TEACHER 以上。
  各 step は §3 の NextStep を JSON 化したもの（locate_plan は target 具体化済み）。
- `POST /api/admin/assistant/next-steps/{step_key}/dismiss`
  → dismissal を upsert（G5）。`theory_review_events` に `entity_type='next_step'` で監査。
- `POST /api/admin/assistant/next-steps/{step_key}/restore`
  → `revoked` 扱いで復元（行削除しない）。同じく監査。

判定はすべてサーバ側。フロントは表示するだけ（P1 と同型）。

## 5. DB（migration 038）

```sql
CREATE TABLE IF NOT EXISTS assistant_step_dismissals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step_key    TEXT NOT NULL,          -- "{rule_id}:{target_id}"
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,  -- 復元は行削除でなく revoked（G5/P4）
    UNIQUE (user_id, step_key)
);
```

これ以外のテーブルは作らない（G1: To-Do 本体は状態からの投影で、保存しない）。

## 6. フロントエンド（`admin-next-steps.js`）

- **バッジ**: `admin.html` ヘッダーの `#admin-copilot-toggle` の左に
  `📋 次にやること` ボタンを置く。`required` があれば `(n)` を強調色で表示、
  `recommended` のみなら通常色、0 件なら数字なし（ボタン自体は残す—空状態は
  「今やるべきことはありません」）。**自動でパネルを開かない**（G4）。
- **パネル**: severity 別グループ。各項目 = タイトル + 理由（事実文）+
  `[案内する]`（→ `AdminAssistant.runLocatePlan(step.locate_plan)`）+
  `[今はしない]`（→ dismiss API。dismissible=false の項目には出さない）。
  下部に折りたたみ「非表示にした項目」と、`🤖 操作アシスタントに質問する` リンク
  （→ `AdminAssistant.open()`）。
- **再取得**: `initApp()` 時 / `activateTabView` 後 / 教材アップロード完了・
  コース登録完了・公開・binding 保存の各成功ハンドラ後。ポーリング禁止。
- 識別子/CSS は `admin-next-steps-` プレフィックス（既存の atlas / doubt / assistant と非衝突）。
- ES5 で記述（admin.js の規約に合わせる）。

## 7. イベント直後のインラインカード（バッジの補完）

バッジは「あとから気づく」ための受動導線。**操作完了の瞬間**にはその場で次の一歩を示す:

- **教材アップロード成功時**: 進捗表示の下に一行カード
  「解析が完了したら『コース構築』でこの教材からコースを作成できます」+ `[案内する]`。
- **コース登録完了時**: 既存の `cb-atlas-binding-area`（登録直後の binding 提案 UI）を
  「次のステップ: 学習マップの割り当て」という見出しで提示し、スキップ可能なまま
  存在に気づける見せ方にする（機能追加ではなく見出し・文言の調整）。
- カードは提示に留め、自動でタブ遷移・モーダル表示をしない（G4）。

## 8. 操作アシスタントの発見性向上

- **初回ログイン一度きりの cue**: 🤖 ボタンに 3 秒の pulse + 吹き出し
  「操作に迷ったらここで質問できます」。一度きりフラグは atlas の
  `atlas_cue_events (first_login)` と同型に `theory_review_events` ではなく
  **`assistant_cue_events` を作らず**、`assistant_step_dismissals` に
  `step_key='cue:first_login'` の行で代用する（テーブルを増やさない）。
  フラグ確認不能時は表示しない（fail-closed、atlas F と同じ）。
- バッジパネル → アシスタント、アシスタント → locate と、**入口は複数・基盤は単一**
  （capability registry + runLocatePlan）を保つ。

## 9. 分野の地図の fail-closed 徹底（Phase 0・バグ修正扱い）

`atlas-data.js` の既定カートリッジフォールバックを廃止する:

```js
// 現状 (atlas-data.js:78-79): コース文脈が無いと素粒子物理へフォールバックする
url = "/api/atlas?cartridge=" +
  encodeURIComponent(cartridgeId || c.cartridgeId || DEFAULT_CARTRIDGE);

// 修正後: 明示指定が無ければ取得せず null（= 地図領域ごと非表示）
const cid = cartridgeId || c.cartridgeId;
if (!cid) return null;
url = "/api/atlas?cartridge=" + encodeURIComponent(cid);
```

- gap3 のサーバ側ゲート（導出カートリッジで topic アンカー無し → 404）は既に正しい。
  残っていたのは**クライアント側のこの既定値**で、コース文脈が配線されない画面
  （コース未選択・binding 前）で素粒子物理の地図が出る唯一の経路。
- 学習者 UI（minimap / overlay / cues）はすべて `AtlasData.load()` 経由なので、
  この 1 箇所の修正で「未設定コースでは地図領域ごと非表示」が完成する。
- 教員向けプレビュー等で明示カートリッジを見たい場合は従来どおり
  `window.AtlasContext.cartridgeId` の明示で表示できる（挙動不変）。
- 割り当てを促す能動導線は §3.1 の `course.no_atlas_binding` ルールが担う。
  つまり「学習者には出さない（fail-closed）+ 教員には割り当てを促す（badge）」の対で解決する。

## 10. ガードレール（`backend/tests/test_next_steps_guardrails.py`）

- 全ルールの `capability_id` が registry に存在し、`required_role` がルールの想定ロール以下。
- 権限外ロールで `compute_next_steps` を呼んでも該当項目が出ない（fail-closed）。
- dismiss / restore が行削除しない（G5/P4）。
- `core/admin_assistant/next_steps.py` が FastAPI / LLM クライアントを import しない（G2/G7）。
- reason 文に禁止語彙（煽り・命令口調の督促）を含まない。
- 返却件数上限と `truncated` の整合。

## 11. 段階導入

| Phase | 内容 | 依存 |
|---|---|---|
| 0 | atlas-data.js の既定カートリッジフォールバック除去（§9） | なし・即時 |
| 1 | `next_steps.py` + GET API + バッジ UI（6 ルール）+ 追加 capability + 不足アンカー | なし |
| 2 | dismissal 永続化（migration 038）+ 監査 + 初回ログイン cue + インラインカード（§7・§8） | Phase 1 |
| 3 | ルール拡充（共有・review-queue 滞留・地図修正報告未処理など） | Phase 2 |

Phase 1 の時点では dismiss を localStorage 暫定にしてもよい（G5 の完全実装は Phase 2）。

## 12. 非スコープ / 決定事項

- **学習者向けバッジは作らない** — 本設計は教員の運用導線のみ。学習者への「やるべきこと」
  提示は学習設計の問題であり別議論（P7: 演技化・督促化させない）。
- **To-Do の自動実行はしない** — 「案内する」は道案内（P8）まで。代行が欲しい操作は
  従来どおり capability の `kind=action` 登録（P2 確認ゲート付き）を個別に進める。
- **通知（メール・プッシュ）はしない** — バッジはログイン中の受動表示のみ。
- **進捗率・達成率を出さない** — 件数のみ。チェックリストの消化ゲームにしない。
