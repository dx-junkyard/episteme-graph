# docs/manual のAIアシスタント知識源化 — 最終設計書（3チーム代表討議 統合版）

- 対象: episteme-graph（ura-dev）
- ステータス: 討議確定・実装前
- 関連正本: `docs/features/admin_assistant_design.md` / `guidance_layer_design.md` / `assistant_common_infra_design.md`
- 討議の性格: チームA（最小コスト）・チームB（ガバナンス）・チームC（体験最大化）の3案を相互検証のうえ統合。単なる折衷ではなく、実機検証で生き残った設計だけを採用した。

---

## 0. エグゼクティブサマリー

**結論**: ベクトルRAGは建てない。既に本番稼働している `backend/core/admin_assistant/knowledge.py`（161行・見出し節単位索引 + トークン重なり検索）を `backend/core/help_kb/` に一般化し、docs/manual を **audience 別に物理分離したディレクトリ**から起動時に読み込む非ベクトルKBを3ロール（学生/教員/管理者）に提供する。コーパスは実測 約97KB・70節（manual 36,452文字 + admin_operations 24KB）であり、この規模に埋め込み基盤は過剰投資である。

**討議最大の発見（全チーム合意）**: `backend/Dockerfile`（実機確認済み: COPY 対象は core/db/cartridges/api/src のみ）に **docs の COPY が存在せず、既存 Admin Copilot KB 自体が Docker 本番で `kb_available()==False` に縮退している疑いが濃厚**。docker-compose にもマウントなし。コンテナ内では `knowledge.py:22-34 _candidate_dirs()` の3候補が全滅する。この修正（Phase 0）が本設計を含む全方式の成立前提である。

**最優先のユーザー価値**: 学生の「この画面どう使うの？」が CHIT_CHAT 分類で拒否される既知ギャップを、①UIヘルプボタン（typed action・誤爆ゼロ）+ ②**casual/音声バイパス（learning.py:1910）より手前の非LLM pre-route** の二段で塞ぐ。②の挿入位置は討議で確定した本設計の心臓部 — ここより後ろに置くと音声・casual ユーザー（使い方で最も詰まる場面）に構造的に届かない。

**規模感**: Phase 0+1 で migration 0本・新テーブル 0・埋め込みAPI 0回・既定経路の追加LLMコール 0回（音声整形時のみ既存 quota 内で1コール）。ベクトル層・DB draft/freeze は「着手条件を先に宣言して作らない」（証拠駆動の建て増し）。

---

## 1. 方式の結論 — アシスタント別知識源アーキテクチャ

### 1-1. 共通基盤: `backend/core/help_kb/`（非ベクトルKB・FastAPI 非 import）

- `knowledge.py` の見出し分割（`_HEADING_RE`, knowledge.py:17、H2-H4対応済み）+ 語彙重なりスコア（`_tokenize`/`search`, knowledge.py:61-62, 129-156）を一般化した正本モジュールを新設（約250行）。
- `admin_assistant/knowledge.py` は**外部シグネチャ不変**（`search` / `section_for_howto` / `clear_cache` / `kb_available`）の薄い委譲に置換。呼び出し元は `routes/admin_assistant.py:37,192,206` とテストのみ（grep確認済み）で、既存テスト `test_admin_assistant.py:250-267` は無変更で通ることを移行条件とする。
- 検索の入口は **`search_manual(query, *, audience: str, limit)` — audience をオプショナルにしない必須キーワード引数**。STUDENT 経路の呼び出しはコード上 `audience="student"` 固定リテラル（fail-closed の強制点、ガードレールでソース検査）。
- 索引構築時の決定論変換（本文をそのまま配信しない箇所は2つだけ）:
  - **HTMLコメント除去**: `admin-assistant.js` の `renderMarkdown()` は escHtml が先行するため、コメントが `&lt;!-- ... --&gt;` として生露出する（チームA実証）。
  - **Markdown テーブル平坦化**: `admin-assistant.js` / `app.js`（`mdBlocksToHtml`）ともテーブルパーサを持たないため、パイプ行を「見出し: 値」形式へ決定論変換（索引側1箇所に集約、GitHub 上の docs は表のまま）。
- ただし **TODO マーカーを含むチャンクは索引から除外する**（§3-4。コメントだけ剥がして本文を配信しない — 理由は同節）。

**なぜベクトルRAGを建てないか（3チーム一致）**:
1. コーパス97KB・70節は in-memory 集合演算でミリ秒オーダー。
2. Admin Copilot の guidance 応答は **LLM を呼ばない**（`routes/admin_assistant.py:190-250 _guidance_response` は `kb.search()` の本文素通し。実機確認済み）— 「コーパス増でプロンプト肥大」という懸念は実装上成立しない。
3. **chunks テーブル相乗りは禁止（全チーム一致・不変）**: `services.py:1365 search_chunks_with_metadata` はシステム全域の chunks を material_id フィルタなしで検索するため、マニュアル節を chunks に入れると学習質問に「別の資料」としてマニュアルが混入し `content_grounding` 判定も誤る。
4. 言い換え・音声転写ノイズへの弱さは実在するリスクだが、**無ヒット率を計測する計器（§4-1）を先に置き、数字が要求してからベクトル層を建てる**（§7-3 の条件付き封印）。

### 1-2. アシスタント別の接続

| 対象 | 経路 | 知識源 | 検索・応答方式 |
|---|---|---|---|
| **学生**（学習チャット） | ①typed action ボタン（一次・誤爆ゼロ）②casual バイパス手前の非LLM pre-route（二次・音声含む全モード）③LLM 4ラベル分類（三次・Phase 2 分離リリース） | `docs/manual/student/` の凍結索引**のみ** | テキスト: 節本文+出典の**非LLM素通し・quota 非消費** / 音声・casual: 1 LLM コールで会話調整形（既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY`=300 相乗り） |
| **教員**（Admin Copilot） | 既存 guidance モードに第2知識源として追加（新サイロを作らない） | 既存 capability KB（`admin_operations/`、正本のまま非改変）+ teacher 索引 | capability KB が**手順の正本**・manual 索引は概念/全体像担当。primary 未整備/不在時にフォールバック + citation を出所別に併記 |
| **管理者**（SYSTEM_ADMIN） | 教員と同経路 | 上記 + system_admin 索引 | `capabilities_for(role)` のロール階層（SYSTEM_ADMIN ⊇ TEACHER）に索引アクセスも揃える |

### 1-3. 学生 HELP ルートの実装骨子（挿入点は全て実機確認済み）

1. **一次: typed action**。`_TYPED_ACTION_INTENT`（learning.py:608-616）に `"usage_help": "USAGE_HELP"` を1行追加。`app.js` の入力欄付近にヘルプボタン（`support_action='usage_help'`、schemas.py:280 の既存フィールド利用）。分類LLMを経由しない確定ルート — 既存設計思想（「日本語ラベルが CHIT_CHAT へ誤分類される事故を防ぐ」）そのものに乗る。
2. **二次: 非LLM pre-route を casual バイパスより手前に置く**。`learning.py:1910` の `intent = None if (_is_casual or _atlas_ctx) else (...)` により casual/音声（app.js は音声時 `intent_mode:"casual"` 固定）では意図分類が走らないため、`_is_greeting`（learning.py:589-602）同型の `_is_usage_question()` を **learning.py:1896 手前**に配置。ハンズフリー中の「これどう使うの」に効く唯一の位置。キーワードは保守的に絞る（誤爆は教材質問を壊す方向のコストが大きい）。
3. **ハンドラは CHIT_CHAT/LEARNING_ADVICE と同型の早期 return**（learning.py:1916-1971 の並び）。前提知識チェック（:1975）・誤解検出（:2101）・tension prefilter（:2124）に**構造的に到達しない**。
4. **応答は媒体で分ける（討議決着）**: テキストチャット = 凍結本文素通し + 出典（パラフレーズによる意味ドリフトをゼロにする — 正本性の帰結）、`_consume_learning_chat_quota` 非消費（前例: `_approved_graph_element_answer`, learning.py:918-976）。音声・casual = 1 LLM コール整形（生 Markdown の読み上げは体験として成立しないため）、`usage_context(feature="learning:help_usage")` で U層計測。
5. **無ヒット/未整備時は LLM を呼ばず固定文**「その使い方の説明はまだ整備されていません」（`documented=False` 分岐 routes/admin_assistant.py:224-230 と同じ正直さ。捏造禁止 P4）。
6. **出典は新設 optional フィールド `manual_citations: list[{file, anchor, title}]`**。既存 `sources`/`tier` に相乗りしない — `_TIER_STRENGTH`（core/learning_experience.py:25）は未知 tier を out_of_source=0 に落とすため誤表示リスクがある。`LearningChatResponse`（schemas.py:331-357）は answer 以外全デフォルトで DTO 後方互換。出典マーカーは `[出典N]` 規約に合わせ、TTS の `_SPEECH_STRIP_PATTERNS`（core/tts.py:39-46）で読み上げ時に自動除去される。
7. **音声パネルのフェイルソフト**: マニュアル節は chunk ではないため `/source-chunk/` 表示（app.js:3190 以降）は引けない。`manual_citations` 存在時は教材表示をスキップし回答読み上げのみ。
8. **痕跡は最小構造化トレース**: `interest_traces` に新 kind `"help_usage"`（`services.py:2200 _INTEREST_KINDS` へ追加）。payload は **ヒット節 anchor / documented / 無ヒットフラグのみ — 質問逐語を積まない**（P3）。これが §4-1 の改善ループの唯一のデータ基質。tension/anchor worker・digest・個人知識ネットワーク導出・問いの軌跡ビューに help_usage が一切現れないことをガードレールで固定（§6-6）。
9. CHIT_CHAT 拒否固定文（learning.py:1917-1921）に「画面の使い方についての質問にもお答えできます」の再誘導1文を追加。HELP 応答フッターに「教材の内容についての質問なら、そのまま送り直してください」（誤爆時の自己救済）。

---

## 2. KB構築・更新タイミングの設計

### 2-1. Phase 0（全方式共通の成立前提・最優先）: Docker で docs が死んでいるバグの修正

- `backend/Dockerfile` に `COPY docs/admin_operations/ /app/docs/admin_operations/` と `COPY docs/manual/ /app/docs/manual/` を追加。**docs/ 全体は COPY しない** — 設計書40本（docs/features 等）はイメージに存在させないことで、どんなフィルタバグでも漏れない（**fail-closed のビルド時前倒し**。チームB評: 「我々の思想の最良の実装」）。
- WORKDIR が `/app` のため、COPY だけで既存候補 `cwd / "docs"` が生き返る（チームBの精密化）。`_candidate_dirs()` へのコンテナ向け候補追加は保険として実施。
- **docker 実機で `kb_available()==True` を確認するタスクを完了条件に含める**。

### 2-2. 更新タイミング: 「デプロイ = 凍結版の切替」+ 起動時バリデーション

| 方式 | 採否 | 理由 |
|---|---|---|
| **起動時読み込み（`lru_cache` + 起動時検証）** | **採用（Phase 1）** | knowledge.py と同型・実績あり。非ベクトルなので再構築コストはゼロ（読み直し+正規表現のみ）。正本はリポジトリの docs、改訂は通常 PR → イメージ再ビルド → 再起動 |
| **CI ガードレール = マージ前凍結ゲート** | **採用** | §6 のテスト群が pytest で回ること自体が「マージ = 凍結バリデーション」の実体（起動時検証と二重防御）。チームCも実質同意 |
| content-hash 差分**再埋め込み** | 不採用 | ベクトル不採用のため概念自体が消える。hash は版同定・監査にのみ使う（下記） |
| 管理API手動トリガー | **Phase 2 補助** | `POST /api/admin/help-kb/refresh`（SYSTEM_ADMIN・監査つき）。volume-mount 開発や hotfix の非常口。運用の主経路にしない |
| DB draft/freeze（atlas/library 踏襲） | **Phase 3 条件付き封印** | §7-2 参照。移行基準を先に宣言し、満たされるまで**作らないこと自体がガバナンス** |

**正直な注記（チームA、全チーム合意）**: イメージ焼き込み環境で mtime 失効は飾りであり、本番の更新実態は「デプロイ=再起動=再構築」。これを隠さず明文化する。既存 `clear_cache()`（knowledge.py:113-114）が本番コードから一度も呼ばれていない（呼び出しはテスト test_admin_assistant.py:250 のみ）事実とも整合する。

### 2-3. 版の同定と監査（争点だったが採用）

- `backend/core/schema.py` に `AUDIT_ENTITY_MANUAL = "manual"` を追加（27語彙目）。起動時スナップショット構築で **content-hash が前回記帳値と変化したときのみ** `theory_review_events` に記帳（冪等・再起動ごとに増えない。`metadata={content_hash, files, excluded_sections}`、`changed_by=NULL`）。約30〜50行。
- **二元台帳の正直な宣言**: 「誰が書いたか」= git（PRレビュー・blame）、「いつどの版が配信状態になったか」= DB。コンテナ内に git メタデータは無く、**取れない帰属を偽装記帳しない**。Phase 2 の refresh API のみ `changed_by=操作者`。
- 記帳は `services.record_review_event` 経由・カタログ定数必須（`test_audit_entity_catalog_guardrails.py:72-99` が生リテラルを拒否）。`_AUDIT_CALLER_FILES` 追記と CLAUDE.md 語彙集合の更新を手順に含める。
- チームAは「git+デプロイ記録で足りる」と反対したが、**学生が誤った操作案内を受けた時に「その時点で配信されていた版」を人力突合なしで特定できる**価値と、26語彙の監査カタログを持つ本リポジトリで AIの知識源だけが監査圏外になる非一貫性を重く見て採用（B・C 2:1、コスト30〜50行）。

---

## 3. docs/manual 整理規約

### 3-1. audience はディレクトリで物理分離する（採用: チームB案）

```
docs/manual/
  student/        # 学生に見せてよい情報のみ。全ロールが読める（上位継承）
  teacher/        # TEACHER 以上
  system_admin/   # SYSTEM_ADMIN のみ
  README.md       # 人間用索引（KB非対象）
```

- **学生索引ビルダーは `docs/manual/student/` のパスしか受け取らない**。「学生索引は student/ 由来のみ」がコード構造として保証され、クエリ時フィルタの書き忘れという事故クラスが構造的に消える。
- **01-specification.md の分割は必須**。「全ロール共通」ページに `ADMIN_PASSWORD` 環境変数・`_require_teacher()` 内部関数名が実在する（01-specification.md:134、3チーム全員が実測確認）— 「共通=安全」の前提は既に一度失敗している。学生安全部分（§1/§3.2/§5/§7、k-匿名説明 :189 含む）→ student/、運用機微（§2/§4）→ system_admin/。
- チームCの「注釈タグ+default-deny で足りる」案は不採用: タグ付き節への後日**追記**による漏洩を diff レビューで視認できない（境界が数十行上のコメントにある）。学生に1行漏れたら事故という前提では、境界はファイルパスで物理化する。34本の相対リンク張り替え（03→admin_operations、全件健全を実測済み）は Phase 1 の docs 作業に計上。
- **student/ 配下の禁止語彙 denylist** を機械検査（§6-2）。初期セット: `ADMIN_PASSWORD` / `JWT_SECRET` / `OPENAI_API_KEY` / `admin.html` / `localhost:8001|9001` / `/api/admin` / `_require_` / `MAX_CALLS_PER_DAY` 系 env 名。

### 3-2. front-matter 規約（「装飾」から「検証される宣言」へ）

```yaml
---
audience: student   # student | teacher | system_admin。ディレクトリと一致必須（validator 検査）
screen: learning    # 任意。コンテキストヘルプ（§4-3）の突合キー
capabilities: [materials.upload]  # 任意。registry と相互参照（実在性をテスト）
---
```

- 重要な実態（チームC発見）: 現行 `knowledge.py:53-58 _strip_front_matter` は front-matter を**読み飛ばすだけでパースしない**（admin_operations の `screen:/role:` は現状装飾。実権限制御は capability registry 側）。help_kb で初めて解釈層を新設する。
- admin_operations 側も `role:` ⟷ capability `required_role` の一致テストを新設し、注釈を契約に変える。
- CLAUDE.md の「front-matter `capability`/`role`/`screen`」という記述は実ファイルと不一致（`capability` キーは実在しない）— 併せて修正する。
- **三点一致の原則**: 境界はディレクトリ（レビューでパスとして見える）+ front-matter（宣言）+ コード（検証）。front-matter 単独にも許可リスト単独にも依存しない。

### 3-3. 見出し・チャンク規約

- 全 `##`〜`####` 見出しに**明示 `{#anchor}` を必須化**（`_slugify`, knowledge.py:44-50 は対応済み）。根拠: `_slugify` と GitHub 自動スラグは実際に不一致（`02-student.md:177` で `_slugify`=`8-分野の地図-わたしの地図` vs 手書きTOC=`#8-分野の地図--わたしの地図`、チームC実証）。anchor はファイル内一意（§6-8）。
- チャンク粒度は**可変**: `###` がある節は `###` 単位（02-student.md §4 は4トピック混在、§8 は2機能同居 — `##` 単位では質問意図が混線）、無い節は `##` 単位。
- 相互リンクは `../admin_operations/xxx.md#anchor` 形式のみ許可し、実在性をテストで検証（現状34リンク全件健全 — 「たまたま」を「保証」に変える）。

### 3-4. TODO の扱い（実測: 17注釈 + 地の文1）

三層構成（討議で最も割れた論点。§7-6 に不採用側の理由）:

1. **Phase 0 でマニュアルとコードの矛盾2件を即時修正**: #11「説明バージョンは履歴として保持」→ 実際は title/body 素上書きで履歴なし（theory_components.py:3290-3293）、#16「ライブラリ廃止」→ restore API が実在し可逆（library.py:412-421）。**AIが誤りを根拠付きで復唱する事故**はコメント除去では防げない（コメントの外の本文が間違っている）。
2. **将来機構: TODO マーカーを含むチャンクは索引から除外（凍結拒否）+ 除外事実を G層 To-Do に出す**（採用: チームB）。根拠: `03-teacher.md:46` の実データは `| 可（後から戻せます） | <!-- TODO: 要確認 -->` — マーカーは直前セルの主張が未検証であることの宣言であり、コメントだけ剥がすと**未検証の主張が検証済みの顔で配信される**（「捏造しない・確定は人間」への構造的抵触）。チームAの「操作表を含む節が丸ごと消える」懸念は、除外粒度をチャンク（`###` 単位）に絞ることと、除外が G層 To-Do で即座に可視化されることで緩和する。
3. **残り15件は Phase 0〜1 のドキュメントタスクとして解消**（チームBの調査で全17件がコード事実として回答済み — 例: #5 コース非公開化→既受講者は次回アクセスから閲覧不可・再公開で復元 services.py:631 / #8 studio直接実行のAIリライトは取り消し不可 admin-lecture-studio.js:7090-7160 / #14 検証状態の記帳は何度でも上書き可 doubt.py:517-524）。解消すれば**現行コーパスでは除外集合は空**で出荷できる。
4. #5 のような「挙動は判明したが、それでよいかは製品判断」の項目は、マニュアルには現行挙動を事実として書き、製品判断は別 issue に切り出す（§8-3）。

---

## 4. 通知・G層連携を含む能力向上策

### 4-1. 需要側 + 供給側の両面計器（改善ループの閉環）

- **需要側（採用: チームC）**: `interest_traces(kind='help_usage')` のうち「無ヒット or 未整備節ヒット」を anchor 単位で集計し、**k≥3 で G層ルール `manual.help_gaps_pending` を点灯**（severity=recommended、事実文「受講者マニュアルの『◯◯』節への質問が複数ありますが、説明が未整備です」、件数はレンジ表示のみ）。k-匿名は `core/privacy.py` 正本（`K_ANONYMITY=3` / `bucket_count_range`）を使い**リテラル再定義しない**。教員に質問逐語は見せない（P3）。
- **供給側（採用: チームB）**: `assistant_kb.undocumented`（severity=optional, SYSTEM_ADMIN）— `documented=False`（knowledge.py:151 で判定されるが現状どこにも永続化されず G層から参照されていない実測）の capability を To-Do 化。
- **TODO 可視化**: `manual.todo_unresolved`（SYSTEM_ADMIN）— 索引除外チャンクが1件以上で点灯。TODO を解消すれば次回起動で自動消滅（G1: 完了フラグを持たない）。
- ルール追加の型は `next_steps.py:69-106 RULE_CATALOG` + `:545-555 _RULE_EVALUATORS` に準拠。`next_steps.py:32` が既に `capabilities` を import しており、同一 core パッケージ内 `help_kb` import は前例整合（G7 非抵触も確認済み — knowledge.py 系は FastAPI 非 import）。バッジ件数のみ・自動表示なし・ポーリングなし（G4）。

### 4-2. 通知

- **deploy 連動のマニュアル更新通知は作らない**（§7-5）。actionable な情報は全て G層 To-Do（pull 型・件数のみ）で表現できる。
- **学生には push しない**: 学生宛 `user_notifications` INSERT 経路はコード上ゼロ（routes/notifications.py 全点 `_require_teacher`、唯一の学生向け前例は pull 型 version-notice, learning.py:436-455）。この境界を越えない。学生の体験は「質問したらその場で正しい答えが返る」ことで完結させる。

### 4-3. UI内コンテキストヘルプ（Phase 2〜3）

- 学生UI: 「？」ボタン押下で現在の `screen_mode: "lecture"|"chat"|"voice"` / topic を添えて HELP 質問をプリフィル（送信は本人。proactive カード・滞留検知・自動ポップアップは作らない — G4）。クライアントは `lectureState.active` / `voiceState.active` を既に持つが未送信（app.js:4107, 3080台）。`LearningChatRequest`（schemas.py:276-300）に `screen_mode` を追加し、front-matter `screen:` 一致節を検索の第一候補にする。
- 教員UI: Copilot は `getScreenContext`（admin-lecture-studio.js 公開API）を既に持つため、manual 索引検索に screen ヒントを渡すだけ。

### 4-4. 意図分類の4ラベル化（Phase 2 分離リリース）

`_classify_intent`（learning.py:628-672）のプロンプト（:650-654）とラベル判定（:658-666）に `USAGE_HELP` を追加。**迷えば DOMAIN_RAG に倒す**保守設計（誤爆しても教材RAGに落ちるだけで安全側）。Phase 1 に入れない理由: 分類LLM変更は既存3ルートの分類分布を動かす回帰リスクであり、ボタン+pre-route で音声含む全モードが既にカバーされる以上、**切り戻し単位として分離する**方が健全（チームAの主張を採用。help_usage 計器のデータを見てから出す）。既存テスト3本（`test_intent_routing.py:89-134` / `test_learning_chat_infra.py:176` / `test_voice_casual_chat.py:40`）の更新はこのリリースに同梱。

---

## 5. 実装フェーズ分割

### Phase 0（半日・即出荷価値 — 全工程の成立前提）

| 成果物 | 規模 |
|---|---|
| Dockerfile COPY 2行（manual + admin_operations のみ、docs/features 非同梱）+ `_candidate_dirs()` 保険候補 | 〜10行 |
| **docker 実機で `kb_available()==True` 確認**（既存 Admin Copilot KB の本番復旧 — docs/manual 化と独立に価値がある） | 検証タスク |
| マニュアル矛盾2件（#11 説明バージョン履歴 / #16 ライブラリ restore）の本文修正 | docs 編集 |

### Phase 1（3〜4日・migration 0本）

| 成果物 | 規模 |
|---|---|
| `backend/core/help_kb/`（loader/validator/index、audience 必須引数・コメント除去・テーブル平坦化・TODO チャンク除外）+ `admin_assistant/knowledge.py` 委譲化 | 〜300行 |
| docs 手術: ディレクトリ物理分離・01分割・denylist スクラブ・front-matter・全70節 `{#anchor}`・34リンク張り替え・TODO 15件解消 | docs 編集（〜1,100行改訂） |
| 学生 HELP: typed action 1行 + `_is_usage_question` pre-route（casual 手前）+ ハンドラ + `manual_citations` + `_INTEREST_KINDS` に `help_usage` + CHIT_CHAT 文言 | learning.py 〜100〜150行 |
| Copilot guidance への teacher/system_admin 索引フォールバック | admin_assistant.py 〜30行 |
| `AUDIT_ENTITY_MANUAL` + 起動時 content-hash 記帳 | 30〜50行 |
| app.js: ヘルプボタン + `manual_citations` 出典表示 + 音声フェイルソフト | 〜80行 |
| ガードレールテスト（§6） | 400〜600行 |

### Phase 2（運用フィードバック・証拠が揃い次第）

- G層ルール3本（`manual.help_gaps_pending` / `assistant_kb.undocumented` / `manual.todo_unresolved`）: 〜150行
- 4ラベル化の分離リリース（既存テスト3本更新同梱）
- `POST /api/admin/help-kb/refresh`（SYSTEM_ADMIN・監査つき）: 〜60行
- 学生「？」ボタン + `screen_mode` コンテキスト絞り込み

### Phase 3（条件付き封印 — 着手条件を先に宣言）

- **ベクトル補助層（migration `manual_sections`）**: 着手条件 =「`help_usage` 痕跡の無ヒット率が運用で実測され、非ベクトル検索の限界がレンジ表示で示されたとき」のみ。着手時の必須条件: ①専用テーブル（chunks 非汚染）②**シード = 全置換スナップショット（現行ファイル集合に無い `(file, anchor)` 行を同一トランザクションで DELETE — 孤児行が存在しない機能の古い説明を返すドリフトの遮断）**③凍結検証を通過した節のみ埋め込む ④`library_entry_versions`（042_knowledge_library.sql:36-50）前例踏襲・70節なら HNSW 不要。参考実測: 全70節で約22Kトークン・`embedder.py:61 _BATCH=100` で1コール。
- **DB draft/freeze**: 着手条件 = (a) git 非利用の編集者（事務職員等）の運用開始 (b) デプロイ独立の版操作要求の実測 (c) audience 別部分公開の必要、のいずれか。移行時は `revision_store.update_with_revision_lock` / `idempotent_seed_import`（revision_store.py:56-69, 114-122）へ委譲。

---

## 6. 新設ガードレールテスト一覧（`backend/tests/test_help_kb_guardrails.py` ほか）

`guardrail_helpers.py`（`assert_module_tree_does_not_import` / `assert_source_forbids` / `extract_function_source`）と既存パターン（`test_every_capability_has_a_kb_section`, test_admin_assistant.py:253-259 / `test_no_polling`, test_next_steps_guardrails.py:771）を流用:

1. **audience 越境禁止（最重要）**: 学生索引ビルダーのソースが teacher/system_admin ディレクトリを参照しない（`assert_source_forbids`）+ 構築済み学生索引の全節 provenance が student/ 配下 + `search_manual(audience="student")` の全 citation が student/ 由来のみ。
2. **denylist**: student/ 配下全ファイルに §3-1 の禁止語彙が無い（全違反まとめて報告）。
3. **audience 引数必須の fail-closed**: `search_manual` が audience 未指定で呼べない（シグネチャ検査）+ learning.py の呼び出しが `audience="student"` リテラルであることのソース検査。
4. **TODO 凍結拒否**: TODOマーカー入りフィクスチャが索引から除外され除外リストに記録される + 全索引 body に `TODO` / `<!--` / `|---` 生テーブル行が残らない。
5. **HELP ルートの痕跡非汚染（構造検査）**: HELP 分岐ソースに `judge_tension_hint` / `detect_and_record_misconception` / 前提知識チェックの呼び出しが無い + テキスト経路で `_consume_learning_chat_quota` 非消費 + pre-route が casual バイパス（learning.py:1910）より手前にあることの検査。
6. **help_usage の消費者排除**: 個人知識ネットワーク導出（derive）・tension/anchor worker（`_fetch_pending_*`）・各 digest・問いの軌跡ビューに `kind='help_usage'` が現れない（kind 許可リスト方式の監査）+ payload に質問逐語を保存する経路がない + 教員向け集計は anchor + レンジのみ。
7. **Docker 同梱の回帰防止**: Dockerfile に manual/ と admin_operations/ の COPY が存在し、かつ docs/features 等の開発文書を COPY していない（静的検証）。
8. **front-matter 契約 + anchor 整合**: audience 必須・ディレクトリ一致 / `capabilities:` 記載 id が registry に実在 / admin_operations の `role:` ⟷ `required_role` 一致 / 全見出しに明示 `{#anchor}`・ファイル内一意 / manual→admin_operations 全リンク解決。
9. **数値非表示**: HELP レスポンス / `manual_citations` に confidence/score キーが漏れない（test_next_steps_guardrails.py:264 の禁止語彙パターン踏襲）。
10. **k=3 再定義禁止**: 集約コードが `core/privacy.py` を import しリテラル再定義しない。
11. **構造規約**: `core/help_kb/` の FastAPI 非 import / 索引・監査の削除 API 不在 / 監査はカタログ定数経由（`_AUDIT_CALLER_FILES` 追記込み）/ フロントに `setInterval` 不在（ポーリング禁止）。
12. **委譲互換**: `admin_assistant/knowledge.py` 委譲化後も既存テスト（test_admin_assistant.py:250-267）が無変更で通る。
13. **chunks 非汚染**: help_kb が `chunks` テーブル・`search_chunks_with_metadata` に触れない（ソース検査）。
14. **G層整合（Phase 2）**: 新ルール3本の capability 実在・fail-closed・dismiss が行削除しない（test_next_steps_guardrails.py へ追加）。

---

## 7. 討議で却下した案とその理由

| # | 却下案（提案元） | 理由 |
|---|---|---|
| 1 | **ベクトルRAG即時実装 / migration `manual_sections` の Phase 2 無条件実装**（C初案） | 計測前の最適化。C自身の2段構え設計（無ヒット時のみベクトル）が「無ヒット率実測まで価値は仮説」であることを含意。孤児行の削除経路未設計というドリフト欠陥（B指摘）も未解決だった。C自身が条件付き封印へ降格を受諾 |
| 2 | **DB draft/freeze の Phase 1 採用**（B初案の看板） | 実測: atlas_store 603行 + revision_store 171行 + migration + UI 相当の投資に対し、得られる追加価値は「UI からの draft 編集」のみ。現在の書き手は git を使う開発者だけ。移行基準の事前宣言つきで Phase 3 封印 |
| 3 | **chunks テーブルへのマニュアル相乗り** | 全チーム一致で禁止。`search_chunks_with_metadata`（services.py:1365）は全域検索であり、教材回答へのマニュアル混入・content_grounding 誤判定が構造的に起きる |
| 4 | **TODO コメントの正規表現除去のみで本文配信**（A初案） | P4 逆立ち: マーカーは直前主張の未検証宣言であり、剥がすと「未確認の事実から未確認マークだけ剥がして配信」になる（03-teacher.md:46 実データで実証）。レンダリング事故対策としてのコメント除去自体は非TODO コメントに対して採用 |
| 5 | **deploy 連動のマニュアル更新通知 `manual_updated`**（B/C初案） | docs を触る全デプロイが通知になるノイズ設計。C自身が撤回、actionable な部分は G層 To-Do が覆う。`all_teacher_ids()` ヘルパー新設も不要化 |
| 6 | **TODO節の索引除外に対する A の拒否（コメント除去+配信継続）** | §3-4 のとおり B 案を採用。A の「操作表が丸ごと消える」懸念はチャンク粒度除外 + G層可視化で緩和し、Phase 0 の全件解消で現行除外集合は空 |
| 7 | **audience 境界のコード内許可リスト単独**（A初案）/ **節単位タグ単独**（C初案） | A案は 01 丸ごと許可で `ADMIN_PASSWORD` 等を学生に配信する実バグ（A自身が撤回）。C案は追記漏洩を diff で視認できない。物理分離+宣言+検証の三点一致に統合 |
| 8 | **無ヒット計測の logger 記録**（A初案） | 教員はサーバログを読まない・G層は「サーバ状態からの決定論導出」（G1）でありログは状態でない。しかも質問逐語のフリーテキストログは構造化最小痕跡より監視性が高く P3 的に劣化。A自身が撤回 |
| 9 | **HELP キーワードゲートを casual バイパスの内側に置く**（A初案・B初案） | learning.py:1910 実測により音声・casual ユーザーに構造的に届かない。全チームが C の「バイパス手前 pre-route」に合流 |
| 10 | **4ラベル化の Phase 1 同梱**（B/C初案） | 分類LLM変更は既存3ルートの分類分布を動かす回帰リスク。ボタン+pre-route で全モードカバー済みのため、切り戻し単位として分離（Phase 2）。§4-4 |
| 11 | **学生向け push 通知 / proactive ヘルプカード（滞留検知）**（C自身が思想上限として封印） | 学生宛 INSERT 経路ゼロの境界・G4（押し付けない）文化と正面衝突 |
| 12 | **refresh API の Phase 1 同梱**（B初案） | 現運用（再起動）で足りる。Phase 2 の非常口に降格 |

---

## 8. 未決事項（ユーザー判断が必要）

1. **docs/manual の未コミット状態**: 対象マニュアル5ファイル（2026-07-22 新設）と本設計の前提となる大量の未コミット変更が ura-dev に滞留している。**Phase 0 着手前にコミット分割の方針**（マニュアルを先にコミットするか、本設計の docs 手術後にまとめるか）の判断が必要。
2. **ディレクトリ物理分離の範囲**: 本設計は teacher/system_admin を含む全分離を採用したが、チームCは「分割が本当に必要なのは汚染実証済みの 01 のみ」と最後まで主張した。34リンク張り替え + 履歴の churn を許容するか、01 分割のみの縮小版にするかは最終的に運用者の好みが残る（設計としては全分離を推奨 — 以後の節単位タグ規律が不要になり最小メンテ）。
3. **TODO #5 系の製品判断**: 「コース非公開化→既受講者は次回アクセスから閲覧不可」等、現行挙動をマニュアルに固定化してよいかは製品判断。矛盾2件と異なり技術的には解消済みのため、別 issue 化の要否と優先度の判断を仰ぐ。
4. **content-hash 監査記帳（§2-3）の Phase 1 同梱**: B・C 採用 / A 反対で 2:1 採用としたが、30〜50行とはいえ「起動時 DB 書込」が増える。Phase 2 送りにしたい場合は fail-closed・捏造禁止は一切弱くならない（Aの指摘どおり）ため降格可能。
5. **音声・casual での LLM 整形の既定**: 本設計は「音声のみ整形・テキストは素通し」としたが、テキストでも短い会話調を好むユーザーがいる場合、整形をオプトインにする UI（トグル）を作るかは体験判断。
6. **既存 Admin Copilot KB の本番縮退の確認**: Phase 0 の docker 実機検証で `kb_available()==False` が確定した場合、「Admin Copilot の guidance が本番で常に『未整備』を返していた」期間の告知・hotfix の扱い（通常リリースに載せるか緊急修正か）。

---

### 不変条項との整合確認（全項目）

| 不変条項 | 本設計での担保箇所 |
|---|---|
| fail-closed | audience 必須引数・物理分離・ビルド時非同梱（§1-1, §2-1, §3-1）、取得失敗時は非表示/固定文 |
| 捏造禁止（確定は人間） | 無ヒット固定文（§1-3-5）、TODO チャンク凍結拒否（§3-4）、矛盾2件の Phase 0 修正 |
| 数値非表示 | confidence/score 非漏洩テスト（§6-9）、G層集計はレンジのみ（§4-1） |
| ポーリング禁止 | `setInterval` 不在テスト（§6-11）、G4 準拠の pull 型 To-Do のみ |
| core 非 FastAPI | `help_kb` の import 禁止テスト（§6-11） |
| 冪等 migration | Phase 0〜2 は migration 0本。Phase 3 着手時も backend/db/*.sql 正本・冪等規約に従う |
| P3（監視にしない） | help_usage payload 逐語なし・k≥3・`core/privacy.py` 正本（§1-3-8, §6-10） |
| P4（情報を落とさない） | TODO は除外+To-Do 可視化で保持、dismiss は行削除しない（§6-14） |
| A層ほか既存レイヤー非改変 | admin_operations 正本のまま・knowledge.py は委譲のみ・読む側として積層 |
| コスト上限文化 | 新規 env 0（既存 quota 相乗り + `usage_context` U層計測） |