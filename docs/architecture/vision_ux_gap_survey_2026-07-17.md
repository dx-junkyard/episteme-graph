# ビジョン × UX ギャップ再調査（2026-07-17）

前回調査 `vision_ux_gap_survey_2026-07.md`（2026-07-16、25項目中22修正）の**フォローアップ調査**。
前回との違いは2点: ①前回修正の定着確認と、前回以降に実装された新機能（#496 図分類・教員指示付き
図再解析・要素インベントリ・トピック音声 047 等）の初回UX監査を含むこと、②調査と同時に
**確定バグはその場で修正**したこと（§4）。

- **調査体制**: Fable 5 が指揮、Sonnet 5 のサブエージェント8体が領域別に並列調査
  （学習者B層 / 個人知識NW+Atlas / W+D層+標準化 / C+V層+共有基盤 / ガイダンス機構 /
  L層+図解析新機能 / レクチャー+R層 / E層+docs整合性）+ L層ライブラリ深掘り1体。
  重要指摘は指揮側でコード直読・関数実行により裏取りし、修正は5体のフィクサーで並列実施した。
- **働き方の前提**: working tree の未コミット変更（外部レビュー対応・図再解析・要素
  インベントリ等）を現在の真実として扱った。
- **テスト**: 調査開始時ベースライン backend 4,133 + src 1,396 passed / 0 failed。
  修正後も全グリーン（§4 末尾）。

---

## 0. エグゼクティブサマリー

1. **前回修正22項目は全て定着しており、退行はゼロ。** 外部レビュー6件対応（コース完了の
   サーバー正本化・digest汚染防止・enroll再試行等）も設計どおり機能している。幹線ジャーニー
   （公開→受講→学習→修了、グループ招待）は UI 上つながっている。
2. **「バックエンド完成・UI到達不能」の第二波が、今度は"確定装置だけあって生成・入口が無い"
   形で現れた。** Phase S（標準化判定）は UI 到達経路ゼロ、同一性リンク（KN-2/3 の実行手段）は
   confirm/reject UI だけあって**候補を作る経路が実質存在しない**、C層は「claim 紐づけの確定は
   教員」という設計の心臓部の**確定ボタンが無い**。前回 G2 と同型だが、今回は「人間確定主義の
   UI が非対称」という前回 §4-3 の警告が的中した形。
3. **独立には正しい2機能の境界で、サイレントに壊れる高重大度バグを4件確定**（いずれも修正済み）:
   口調設定の保存が読み上げ言語を無警告リセット→全原稿の日本語再生成 / コース内容再生成が
   トピック音声を無効化せず古いナレーションが実質永続 / 画像解析オプション既定OFFの再解析が
   未レビューAI図分類を無警告全消去 / 「📘教材から回答」と「参考（根拠なし）」の矛盾同時表示。
   共通パターンは「単体テストは厚いが、機能境界の相互作用が未検証」。
4. **ガードレール自体の追随漏れが観測された。** コース切替競合ガードを3関数に入れた**翌日に
   追加された4つ目の関数**だけガード漏れ（関数名列挙式の静的テストは新関数を検出できない）、
   危険操作確認モーダルは admin.js 内では徹底されたが**モジュール境界（versioning.js・
   スキーマ提案）を越えて伝播していなかった**。仕組みで守る文化は機能しているが、仕組み自体の
   カバレッジ更新が人力のままである。
5. **ガイダンス機構（Copilot / G層）は前回拡張後も新機能に追随していない。** Copilot の
   「代行」は宣言10件中実装2件（設計の原点動機である原稿書き換え代行が未接続）、G層ルールは
   6件のまま図分類・インベントリ・W層への「次の一歩」を出せない。docs も同様に、実装済みの
   図分類 #496 がどの文書にも存在しない等、「実装が先行し記録が追いつかない」構造が続いている。

---

## 1. 前回修正（2026-07-16）の定着状況

前回 §6 の22項目 + 追加バグ2件 + 外部レビュー6件対応を全て再検証した。**退行なし**。
特筆すべき確認結果のみ記す。

| 項目 | 状態 |
|---|---|
| G1-1/G1-2/G1-3（公開UI・initApp・グループ） | 維持。`initApp` は1定義のみ、招待承諾・招待コード参加とも動作経路確認 |
| G1-4/G1-5（完了体験・enroll確認） | 維持+強化。完了判定はサーバー正本（`learning_states.progress_data`）化、enroll は失敗リトライ・二重送信防止付き |
| G2-D/G2-W（D層記帳・identity confirm UI） | 維持。W層レビュー4指摘（4列UNIQUE・exemplar画像ゲート・citation集約・4要素型導線）も解消済みを個別確認 |
| G2-U/G2-B/G2-C（U層タブ・bridge-insights・共有ダッシュボード） | 維持。U層は新機能（図再解析）にも `usage_context` が模範的に追随 |
| G3（空/障害の区別）・G4-T/R/D/L・G5-2/3/4・G6・G7-J | 維持。G4-R はレビューキューのセクション分離等むしろ強化 |
| 公開解除の意味論バグ | 維持（`is_published = (visibility='public')` 現存） |
| PKNレビュー3指摘（target_refs混入・journey権限・retired hub） | 解消済みを実装+回帰テストで確認 |

前回の意図的見送り3件（G5-1 タブ再構成 / G2-V document版保護 / G3 の fail-closed 維持箇所)は
仕様として扱った。G2-V については UI 文言が限界を正直に開示していることを確認
（`versioning.js` `_introText`「版固定表示は今後対応予定」）。

---

## 2. 今回発見した問題の構造整理

### P1. 「最後の1マイル」欠落 — 確定装置はあるが生成・入口が無い【重大度: 高】

ビジョンの中核（KN-2/3: 同一性はリンクで表現し人間が確定する、修正③: 標準化の三角測量）の
実装が、**バックエンド完成 + 確定UIあり + 候補生成・入口なし**という奇形で止まっている。

- **Phase S（標準化判定）は到達経路ゼロ**: `shared_part` を「深く検討」モーダルで開く導線が
  フロント全体に1つも無く（`grep shared_part frontend/public/js/*.js` は deliberation.js 自身のみ）、
  唯一 library_entry を閲覧できる `renderLibraryDetail` は `standardization_status` を一切
  表示しない（admin.js に "standardization" 文字列ゼロ）。手動バッチ起動 API のボタンも到達
  不能なセクション内にのみ存在。CLAUDE.md / ビジョン文書の「Phase S 完了」はバックエンドのみの意味。
- **同一性リンクは「無肺」**: 手動作成UI（`POST /identity-links` を呼ぶフロント）ゼロ。
  AI対話経由も、システムプロンプトは identity 候補に `shared_part_id` を要求するのに
  grounding（`dialogue.py` `build_grounding`）が**実在の `library_entries.id` を一切提供しない**
  ため構造的に機能しない — LLM は candidate を出せないか、捏造IDでコミット時エラーになるかの
  二択だった（後者の未処理500は修正済み・§4）。前回G2-Wで付けた confirm/reject UI は、確定すべき
  候補が生まれないため空回りする。
- **C層 claim 紐づけの確定ボタンが無い**: 候補生成 API は `backing_claims` を常に
  `confirmed: false` で返し、確定手段のはずの `PATCH /explanations/{id}` を呼ぶ書き込み導線が
  フロントに存在しない（`lsRenderExplanationCard` は `[✓]/[候補]` を読み取り専用表示のみ）。
  「claim 紐づけの最終確定は必ず教員」（endorsement-sharing.md の設計原則2）が実行不能。

### P2. 機能境界の噛み合わせバグ — サイレントなデータ損失【重大度: 高、全て修正済み】

単体では正しい2機能が境界で噛み合わず、**警告なしに成果物が消える/矛盾する**バグ群。
詳細と修正は §4。共通の教訓: どれも単体テストは存在したが、**2機能をまたぐ回帰テストが
無かった**（例: `test_reextraction_clears_stale_ai_profile...` はリセット挙動自体を仕様として
固定しており、analyze_images on→off の再解析シナリオは誰も書いていなかった）。

1. 原稿スタジオ「設定」保存（口調のみ編集）× `lecture_language` デフォルト補完
   → 言語が en→ja に無警告リセット、次回音声生成で全原稿再生成チェーンが無警告発動。
2. 「コース内容生成」の再実行 × `topic_lecture_audio_cache`
   → 個別編集経路にはある DELETE が一括経路に無く、表示と食い違う古い音声が実質永続。
3. 「解析再開」の options 常時明示渡し × orchestrator の「前回 run 継承」分岐
   → 継承分岐がデッドコード化し、既定OFFの再解析が未レビューAI図分類・装置候補を無警告消去（P4違反）。
4. `content_grounding` の `has_topic_material` 分岐 × `overall_tier` の RAG限定集約
   → 「📘教材から回答」と「参考（教材の裏づけなし）+ 未踏ガード」が同一回答に同時表示。

### P3. ガードレール機構自体の追随漏れ【重大度: 中、修正済み】

- コース切替競合ガード: 2026-07-15 に3関数へ導入した**翌日**追加の `refreshAfterMapExclusionChange`
  だけガード漏れ。原因はガードレールテストが関数名の列挙式で、新関数を自動検出しない構造。
- 危険操作確認モーダル（前回G5-3）: admin.js 内9箇所は統一済みだったが、`openDangerConfirmModal`
  が他モジュールへ未公開のため versioning.js の「削除を予約」は生 `confirm()`、スキーマ提案の
  「システム全体」承認に至っては**確認ゼロで全教材再抽出ジョブが即時開始**だった。
- Atlas C-2「再オープン時に直前状態を復元」: 全5呼び出し元が `level`/`focus` を明示するため
  復元分岐が**一度も発火しないデッドコード**だった（静的テストは復元コードの存在のみ検査）。
  ※ ミニマップ・初回表示の `level:1` は受け入れ条件として静的テストで固定された意図的仕様
  だったため、修正はトップバー（メインの再オープン経路）のみに適用。

### P4. 権限とボタン表示の非対称 — 過剰隠蔽と過小隠蔽の同居【重大度: 中】

バージョン API・教材 API のレスポンスに**所有者判定フラグが無い**ため、フロントが場当たり対応に
分化していた: コース版管理は非所有者にボタンごと非表示（**過剰隠蔽** — viewer が「自分がいまどの
版を見ているか」を確認できず、V層の核心の約束を体験主体が確認不能）、文書側は逆に発行・削除予約
ボタンを全員に見せて押すと生の "not found"（**過小隠蔽**）。例示画像チェックも同型（設計書は
「所有者以外には選択肢自体を出さない」と明記、実装は figureId の有無のみで表示）。
→ フロント側の fail-closed 化と `viewer_is_owner` の API 追加は §4 で実施。コース版モーダルの
読み取り専用開放は残課題（§5-3）。

### P5. ガイダンス機構の停滞【重大度: 中】

- Copilot `KIND_ACTION` capability 10件のうち実行ハンドラ実装は2件（course.set_visibility /
  course.publish）。残り8件は「自動実行は現在未対応です」に縮退。とりわけ
  `lecture_studio.rewrite_chunk_script` は設計書§0の**原点動機そのもの**で未接続。パネルの挨拶文は
  「代行できます」を例示するが、どれが実際に動くか事前に区別する手段が無い。
- G層 `RULE_CATALOG` は6件のまま。図分類レビュー・要素インベントリ・W層・V層発行への
  「次の一歩」は現れない（設計書自身が Phase 3 課題と明記＝想定内だが、追随を強制する仕組みがない）。
- `stumbles` / `schema-proposals` の2画面は capability 登録ゼロで Copilot から構造的に不可視。
  後者は上記の無確認・全体承認バグを抱えていた画面でもあり、カバレッジ不足と危険操作が重なっていた。
- 前回G6のアンカー整合ガードレールは「anchor 文字列が admin.js のどこかに存在するか」の部分
  文字列一致のみで、screen 対応・DOM 実在までは検証しない（今回は手動突合で実害なしを確認）。

### P6. ドキュメント正本の分散腐敗【重大度: 中、誤誘導分は修正済み】

- **誤誘導レベル**（修正済み）: api.md / admin.md に撤去済み `PUT /courses/{id}/publish` が残存
  （前回、同内容の admin_operations/course.md だけ修正され姉妹文書が取り残された）、api.md の
  enroll「クローン」説明（migration 011 で廃止済み）、overview.md の「23ステージ」（正: 26）、
  structure-anchored-questions.md の「コード未着手」ヘッダ（完全実装済み）、KB
  interest_dashboard.md のタブ名不一致。
- **索引の機能不全**: README は features/ 36本中4本しかリンクせず、「レイヤー索引の正本」を
  自称する layer_registry.md に W層と個人知識NWの行が無く、migration 一覧は 045 で停止
  （実際は 053）。**#496 図分類はコミット済みなのに docs にも CLAUDE.md にも一切登場しない**。
- **構造要因**: 同じ事実（publish 撤去等）が3文書に重複記載され、修正時に全箇所を横断する
  チェックリストが無い。「新レイヤー実装時に CLAUDE.md / layer_registry / README の3点を同時
  更新する」規律が L層以降で崩れている。

### P7. その他の個別バグ・ギャップ（領域別）

- 学習者B層: 認証モーダルの「新規登録⇄ログイン」切替が strict mode の `arguments.callee`
  TypeError で初回クリック後に恒久故障（2026-03から潜伏、実行系JSテスト不在のため未検出）。
  tension/anchor worker がコスト上限到達時に claim 済み痕跡を無言で失う（P4違反エッジケース）。
  いずれも修正済み（§4）。
- PKN/旅: 再構成成功ノードは `topic_id=None` で導出されるため、旅が[4]地図骨格・コーススコープ
  [5]近傍に構造的に到達できない。「わたしの地図」ラベルが最上位パネルとオーバーレイ内トグルの
  別機能2箇所で重複。訂正操作（地図に反映しない/戻す）が最上位パネルから利用不能。
- レクチャー: 音声生成 readiness がチャンク経路のみを見ており、トピック教材経路の前提
  （draft 充足）を反映しない — 未充足でもタスクは completed 表示で音声は静かに0件。
- D層学習者導線: 検証状態の一行併記が component ポップアップ1経路のみで、設計（doubt D3-6）が
  指定する**出典タブ**の claim/equation には出ない。
- L層: `domain_key` がフリーテキスト入力で typo エントリが検索に孤立（カートリッジ選択の
  既存プルダウン部品を再利用していない）。装置候補の `connections` は昇格モーダルが `parts`
  しか転記せずスキーマにも受け皿が無いため構造的に落ちる。retired エントリの編集・凍結が
  API/UIともブロックされない（仕様未確定）。

---

## 3. 課題一覧（重大度順・対応状況付き）

凡例: ✅=今回修正済み（§4） / 🔧=修正方向確定・未実施 / 💬=設計判断が必要

| 重大度 | ID | 課題（一行） | 状態 |
|---|---|---|---|
| 高 | N1 | Phase S 標準化判定の UI 到達経路ゼロ（shared_part 入口なし・status 非表示） | 🔧 §5-1 |
| 高 | N2 | 同一性リンクの候補生成経路が実質不在（grounding に ID 不供給 + 手動UIなし） | 🔧 §5-1 |
| 高 | N3 | C層 claim 紐づけ確定 UI 不在（backing_claims が永久 candidate） | 🔧 §5-2 |
| 高 | N4 | 設定保存による lecture_language 無警告リセット→全原稿再生成 | ✅ |
| 高 | N5 | コース内容再生成がトピック音声を無効化しない（古い音声が実質永続） | ✅ |
| 高 | N6 | 既定OFF再解析が未レビューAI図分類を無警告全消去（P4違反） | ✅(継承+復元) / 💬(明示OFF時の警告 §5-6) |
| 高 | N7 | grounding×tier の矛盾同時表示（📘教材から回答 + 根拠なし参考） | ✅ |
| 高 | N8 | スキーマ提案「システム全体」承認が確認ゼロで全教材再抽出を即時開始 | ✅ |
| 中 | N9 | 認証モーダルの登録⇄ログイン切替が初回クリックで恒久故障 | ✅ |
| 中 | N10 | 版管理の権限表現非対称（コース過剰隠蔽 / 文書過小隠蔽） | ✅(文書側fail-closed) / 🔧(コース側開放 §5-3) |
| 中 | N11 | `_commit_identity` の実在未検証→未処理500 | ✅ |
| 中 | N12 | Copilot 代行が宣言10件中実装2件（原点動機の rewrite 未接続） | 🔧 §5-5 |
| 中 | N13 | G層ルール6件が新機能に追随せず・追随を強制する仕組みなし | 🔧 §5-5 |
| 中 | N14 | 横断インボックスに C/D/R/L 層イベントが流れない（受け皿は統合済み） | 🔧 §5-4 |
| 中 | N15 | D層検証状態が出典タブの claim/equation に出ない（設計とズレ） | 🔧 §5-7 |
| 中 | N16 | 再構成ノード起点の旅が地図骨格へ構造的に到達不能（topic_id=None） | 🔧 §5-7 |
| 中 | N17 | 「わたしの地図」ラベル2重使用 + 最上位パネルに訂正操作なし | 🔧 §5-7 |
| 中 | N18 | 音声 readiness がトピック教材経路の draft 充足を見ない（静かな0件生成） | 🔧 §5-7 |
| 中 | N19 | personal-map 競合ガードの4関数目欠落（+列挙式テストの構造問題） | ✅ |
| 中 | N20 | 危険操作確認のモジュール境界断絶（V層削除予約が生confirm） | ✅ |
| 中 | N21 | 例示画像チェックの fail-open（設計は選択肢自体を出さない） | ✅ |
| 中 | N22 | 統合（merge）時に例示画像指定がサイレント破棄 | ✅ |
| 中 | N23 | 凍結時 embedding 失敗が成功表示に化け、retrieval から静かに脱落 | ✅(警告表示) / 🔧(fallback検索 §5-6) |
| 中 | N24 | worker コスト上限時の claim 済み痕跡消失（P4） | ✅ |
| 中 | N25 | Atlas C-2 復元のデッドコード化 | ✅(トップバー経路) |
| 中 | N26 | docs 誤誘導群（publish残存・23ステージ・クローン説明・未着手ヘッダ等） | ✅ |
| 低中 | N27 | domain_key フリーテキストで typo エントリが検索から孤立 | 🔧 §5-6 |
| 低中 | N28 | 装置候補 connections が昇格で構造的に落ちる（受け皿スキーマなし） | 💬 §5-6 |
| 低中 | N29 | retired エントリの編集・凍結が素通し | 💬 §5-6 |
| 低 | N30 | 承認者個別一覧 API 未接続（誰が・どの専門タグで承認したか不可視） | 🔧 §5-2 |
| 低 | N31 | stumbles / schema-proposals 画面の capability ゼロ | 🔧 §5-5 |
| 低 | N32 | ロックトピックの視覚（鍵）と実挙動（遷移可）の齟齬 | 🔧 §5-7 |
| 低 | N33 | ハンズフリー音声がサーバーエラー時に無言で聞き取りへ戻る | 🔧 §5-7 |
| 低 | N34 | journey 兄弟検出のコーススコープ/横断の非対称 | 💬 §5-7 |
| 低 | N35 | 引用操作が `window.prompt` で生のコースID入力を要求 | 🔧 §5-2 |
| 低 | N36 | 教材テーブルに開示範囲のインライン表示なし（コース側と非対称） | 🔧 §5-3 |
| 低 | N37 | reanalyze モーダル選択非復元・identityコミット後の一覧未更新・種別変更で類似検索非再実行・adopt失敗時既読化・凍結競合の素500・監査old_statusハードコード・apparatus_semanticsステージメニュー欠落・lsHasScriptsフォールバック・デッドコード2関数・0件トレイ/復帰フィードバック | ✅(一括) |
| 低 | N38 | connect の edge_id 閲覧可否未検証（現状無害・将来の防御） | 🔧 §5-7 |
| 低 | N39 | 通知 dismiss が source='status' 限定（V層通知に将来使えない非対称） | 記録 |
| 低 | N40 | counterfactual セッション一覧のノード数が素の数値表示（原則との整合未確定） | 💬 §6 |
| 記録 | N41 | E層未実装の継続 + 着手前提の4変化（migration 054以降 / llm_worker アダプタ方式 / UI差し込み先が admin-lecture-studio.js へ移動 / 学習者が Atlas・わたしの地図で既にグラフ視覚語彙に接触済み） | §5-8 |
| 記録 | N42 | docs 構造問題（README索引4/36・layer_registry自己陳腐化・#496未文書化・CLAUDE.md W層節なし・正本分散） | 🔧 §5-9 |

---

## 4. その場で修正した項目（2026-07-17 実施）

フィクサー5体 + 指揮側で計約30箇所を修正。**フロント JS は全て esprima で構文検証、admin 系は
ES5 維持。関連テストは全て追加・更新のうえパス**（実施末尾に全体テスト結果）。

### 高重大度

| 対象 | 修正内容 |
|---|---|
| N4 言語リセット | `LectureStudioSettings.lecture_language` を Optional 化し**省略=変更しない**セマンティクスへ（コメントで事故の構造を明記）。加えて `lsSaveSettings()` が現在言語を毎回明示送信する二重防御。回帰テスト追加（省略で en 維持・`scripts_need_regeneration` 不発火） |
| N5 トピック音声無効化漏れ | `build_course_content` がトピック draft 再生成後に当該コースの `topic_lecture_audio_cache` を DELETE。個別編集経路と対称に。テスト追加 |
| N6 図分類消去 | `reanalyze_document` を「`analyze_images` 未指定なら `options=None`」に変更し orchestrator の**前回 run 継承分岐を初めて機能させた**。`GET /admin/materials` に `analysis_options`（最新 run の options）を追加し、解析再開モーダルのチェックボックス初期値を前回選択から復元 |
| N7 grounding×tier 矛盾 | `tier_floor()` を新設し、トピック教材注入時は `overall_tier` を `source` 下限にフロア（**approved への昇格はしない** — 不可侵の一線維持）。静的+機能テスト追加、rag-chat.md の判定表も実装に一致させた |
| N8 スキーマ提案全体承認 | `openDangerConfirmModal` 経由に変更（「全教材の再抽出ジョブを開始します。取り消せません」） |

### 中重大度

| 対象 | 修正内容 |
|---|---|
| N9 認証切替故障 | `arguments.callee` を廃し、安定な親要素 `#auth-toggle` へのイベント委任に変更 |
| N10 文書版の過小隠蔽 | 図一覧 API に `viewer_is_owner` を追加し、例示画像チェック（N21）と合わせフロントは**フラグ未取得時 false の fail-closed** |
| N11 identity 未検証500 | `_commit_identity` 冒頭で `refs.resolve` による実在検証を追加し、失敗を `CommitRoutingError`（4xx）へ変換。実体を通るテストを追加（従来テストはスタブ差し替えで実体未踏だった） |
| N19 競合ガード | `refreshAfterMapExclusionChange` に同型の courseId ガードを追加し、ガードレールテストを4関数対応に強化 |
| N20 確認モーダル境界 | `window.AdminDangerConfirm` として公開し、versioning.js の削除予約を統一モーダル化（フォールバック付き） |
| N21/N22 例示画像 | 所有者フラグによる fail-closed 化 + 統合選択時はチェックを無効化し「統合では例示画像は引き継がれません」を明示（サイレント破棄の廃止） |
| N23 凍結embedding失敗 | `embedding_status="failed"` を警告表示に変更（成功トーストに化けない） |
| N24 worker痕跡消失 | tension / structure_anchor 両 worker で、コスト上限時に claim（`analyzed_at` / `payload.anchor_analyzed_at`）を解放し上限リセット後に再解析可能へ（P4）。テスト追加 |
| N25 Atlas 復元 | トップバー「地図」ボタンを `level` 非指定に変更し C-2 復元を有効化（ミニマップ・初回表示の L1 固定は受け入れ条件のため維持） |

### 低重大度（N37 一括）

reanalyze モーダルの前回選択復元 / identity コミット成功時の同一性リンク一覧再読込 /
ライブラリ種別変更での類似検索再実行 / 🔔「取り込む」失敗時に既読化しない /
ライブラリ凍結の同時実行を 409 化 / retire・restore 監査の old_status を実遷移前値に /
パイプライン手動メニューに `apparatus_semantics`（図の装置・パーツ解析）追加 /
`lsHasScripts` をサーバ status ベースへ（display_text フォールバックの罠除去） /
到達不能デッドコード `lsRunCourseStep` 系の削除 / わたしの地図の 0件トレイ説明文と
「地図に戻しました」フィードバック追加。

### docs（誤誘導レベル）

api.md（publish→visibility・enroll クローン説明是正）/ admin.md（同）/ overview.md
（23→26 ステージ・migration 範囲）/ README（→053）/ rag-chat.md（grounding 表 + tier フロア）/
data-model.md（046〜053 追記）/ layer_registry.md（W層・個人知識NW 行）/
interest_dashboard.md（タブ名）/ field_atlas_skeleton.md（fog_dots 固定3個）/
element_deliberation_workspace_design.md（バナー + 046→049）/ CLAUDE.md（llm_worker 利用系統数）/
reconstruction_loop_design.md（承認語彙の確定注記）/ structure-anchored-questions.md
（「コード未着手」→実装済み）。

### テスト結果

- ベースライン: backend 4,133 + src 1,396 passed / 0 failed（調査開始時）
- 修正後: **backend 4,168 passed / 14 skipped / 0 failed（+35 テスト追加）、src 1,396 passed**。
  フロント JS 全21ファイルを esprima で構文検証済み（admin 系は ES5 維持）

---

## 5. 残課題の修正方向性

### 5-1. Phase S・同一性リンクの「最後の1マイル」を開通させる（N1/N2、最優先）

KN-2/3 とビジョン修正③の実行手段を初めて使える状態にする。最短経路は3点セット:

1. **ライブラリタブを shared_part の正面玄関にする**: `renderLibraryDetail` に
   `standardization_status` バッジ（5語彙）+「深く検討」ボタンを追加し、
   `Deliberation.openElement({elementType:'shared_part', ...})` を配線する。既存モーダルは
   shared_part 対応済み（`_standardizationSectionHtml` が眠っているだけ）なので、入口1つで
   標準化評価ボタン・手動バッチ起動まで一気に生き返る。
2. **対話 grounding に同一性候補の材料を供給する**: `build_grounding()` に「同分野の凍結済み
   library_entries 上位k件（id・名称・aliases）」を非LLMで注入する（apparatus retrieval と同じ
   `search_frozen_entries` の再利用で実装可能）。これで対話由来の identity 候補が初めて
   実在IDを持てる。供給ゼロ件時はプロンプト側で identity 候補の生成を促さない（捏造ガード）。
3. **手動リンク作成UI**: 深く検討モーダルの同一性リンク一覧に「共通部品と結びつける」ボタン →
   類似エントリ検索（既存 `find_similar_entries` 再利用）→ candidate 作成。確定は既存 UI が担う。

### 5-2. C層の確定操作を実装する（N3/N30/N35）

`lsRenderExplanationCard` の backing_claims 表示に確定/却下ボタンを付け
`PATCH /explanations/{id}` を呼ぶ（読み取り専用→書き込み化）。同カードに
`GET /explanations/{id}/endorsements` の個別承認者リスト（名前+専門タグ、数値スコアなし）を
併設。引用操作の `window.prompt` はコース選択プルダウンに置換。

### 5-3. 権限フラグの API 標準化（N10残・N36）

`version-state` レスポンスに `is_owner` / `can_publish` を追加し、コース版管理を非所有者にも
**読み取り専用で開放**（発行・削除予約セクションのみ非表示）。「見せて404」でも「隠して不可視」
でもなく「見せて、できない操作は理由付きで無効化」へ統一。教材テーブルに開示範囲列を追加し
コース側（G5-2）と対称に。

### 5-4. 横断インボックスの拡張（N14）

受け皿（`user_notifications`、kind は open-vocab）と単一🔔UIは既に統合済みで、**各層の変化点に
fan-out 呼び出しを足すだけ**。第一弾は「本人宛て・低頻度・行動可能」の4種に絞る:
C層承認受領 / D層疑義の被起票（対象の記帳者宛て）/ R層 item の flagged 遷移（オーサー宛て）/
L層エントリ retire（引用者宛て）。G4「押し付けない」原則のため、集計系（レビューキュー滞留数
など）はバッジ側（G層ルール追加）に寄せ、インボックスには個別イベントのみ流す。

### 5-5. ガイダンス機構の追随を仕組み化する（N12/N13/N31）

- Copilot: 挨拶文の「代行」例示を実装済み2件に限定 or ハンドラ未実装 capability は
  「道案内のみ」と明示。次の実装候補は原点動機の `lecture_studio.rewrite_chunk_script`
  （既存 API 呼び出しのみ・L2 可逆で P2 ゲート設計が単純）。
- G層: ルール追加（`figure.unreviewed_modes`＝未レビュー図分類あり→図モーダルへ、
  `material.inventory_unvisited` は押し付けになるため見送り推奨）。
- 仕組み化: 「新機能 PR チェックリスト」（capability/KB/G層ルール/docs 3点セットの更新確認）を
  CONTRIBUTING 相当に置く。アンカー整合ガードレールを「screen ごとに anchor が
  `registerUiAnchors` の対応表に存在する」検査へ強化。
- stumbles / schema-proposals に guidance capability + KB を追加（前者は k-匿名の説明含み）。

### 5-6. L層の運用堅牢化（N6残・N23残・N27/N28/N29）

- 明示 OFF 再解析時に未レビュー分類が存在する場合の確認ダイアログ（「AI 分類 n 件が失われます」）。
- retrieval の embedding fallback（最新版が failed なら直近の有効 embedding 版を対象に含める）
  または凍結レスポンスでの再試行導線強化。
- `domain_key` を既存カートリッジ一覧のプルダウン + 自由入力の複合に（typo 孤立の防止）。
- `connections` の受け皿: `APPARATUS_BODY_KEYS` に `connections` を追加し昇格モーダルが転記
  （情報を落とさない P4 と整合）。要スキーマ判断のため 💬。
- retired エントリの編集・凍結ブロックは仕様確定が先（§6）。

### 5-7. 学習者体験の小粒改善（N15〜N18・N32/N33/N34/N38）

- 出典タブの根拠カードに claim/equation の台帳一行を併記（backend は対応済み、フロントの
  呼び出し追加のみ。台帳未記帳コースで出ない fail-closed は維持）。
- 再構成ノード導出時に `claim_id` → トピック解決を試みる（不能なら設計書 §14 に既知の限界として
  明記）。
- オーバーレイ内トグルを「自分の記録を重ねる」に改名し、最上位パネルのノード行に
  「地図には反映しない」を追加。
- 音声 readiness にトピック draft 充足を合算、または生成タスク完了サマリに
  「対象トピック n / 生成 0 件」を表示（正直な報告）。
- ロックトピックはクリック時に一行の事実文（「前のトピックの確認が未完了です」）を表示して遷移は
  許可のまま（押し付けない原則と両立）。ハンズフリーはエラー時に短い TTS フィードバック。
  edge_id にも component と同じ閲覧可否チェックを予防的に追加。

### 5-8. E層着手時の前提更新（N41）

設計書 §10 の issue 分割は有効なまま、着手前に4点を反映する: ①空き migration は **054以降**
②worker は独立モジュール新設ではなく `core/llm_worker` への**アダプタ接続**（現行の家風）
③UI 差し込み先 DOM は `admin-lecture-studio.js` に移動済み ④「学習者はグラフ表示に無垢」という
前提は Atlas・わたしの地図の実装で崩れており、E層ビューはこの2つと**視覚語彙を意図的に差別化**
する設計検討が必要（同じ node/edge 表現だと「また別の地図？」という混乱を生む）。

### 5-9. docs 運用の立て直し（N42）

- 大型更新（課題として実施）: api.md に13ルーター分のエンドポイント表 / admin.md のタブ表最新化 /
  learning.md に6機能（enroll確認・完了カード・書き直し削除・Atlas・わたしの地図・再構成）/
  frontend/overview.md の19モジュール一覧と `/api/atlas` プロキシ / agents.md に
  ApparatusSemanticsAgent 節 / **#496 図分類の文書化**（L層設計書追補 + CLAUDE.md）/
  CLAUDE.md に W層節。
- 運用: 「実装 PR に docs 3点セット（CLAUDE.md・layer_registry・README）の更新有無を明記する」
  規律の導入。記述重複の解消（api.md はエンドポイント正本、admin_operations は手順正本、と役割を
  分けて相互リンク）。

---

## 6. 次に議論すべき論点

1. **Phase S / 同一性リンクの v1 スコープ**: §5-1 の3点セットをどこまで一度に開くか。
   特に grounding への候補供給は「LLM に同一視を促しすぎない」バランス（KN-3）を要設計 —
   供給は事実（同分野の既存エントリ一覧）のみとし、プロンプトでは「該当がある場合のみ」を強調する案を推奨。
2. **コース版管理の読み取り専用開放**（§5-3）と、前回から継続する G2-V（document 版の凍結
   ブラウズ）の優先順位。権限フラグ API はどちらにも必要な下地なので先行実施を推奨。
3. **横断インボックスの通知設計**: §5-4 の4種で始めるか、教員側の滞留可視化（バッジ）を
   G層ルール拡張に寄せるか。「押し付けない」原則との線引きを明文化してから実装すべき。
4. **仕様グレーの確定**: retired エントリの編集可否（N29）/ counterfactual のノード数表示は
   数値非表示原則の対象か（N40）/ journey 兄弟検出の非対称は仕様か（N34）。いずれも実装は
   小さく、決めれば即日直せる。
5. **E層の着手時期**: 「入門者が手に取れる」というビジョンの約束は依然未達（前回 G7 から継続）。
   §5-8 の前提更新を織り込んだ上で、W層 Phase 3（グラフレンズ等）との先後を決める。
6. **機能境界テストの標準化**: §2-P2 の教訓として、「設定保存×生成チェーン」「再実行×キャッシュ
   無効化」「オプション×前回 run 継承」のような**2機能境界の回帰テストを新機能の Definition of
   Done に含める**か。今回の高重大度4件は全てこの型だった。

---

*調査・修正: 2026-07-17。前回調査 `vision_ux_gap_survey_2026-07.md`（2026-07-16）の後継。
本文書の課題 ID（N1〜N42）は前回の G 系列とは独立。*

---

## 追補: 残課題の解消記録（2026-07-18）

§3 の 🔧 / 💬 全件を解消した（Fable 5 指揮 + Sonnet 5 サブエージェント12体の並列実施、未コミット）。
テスト: backend **4,382 passed / 0 failed**（+249）、src 1,396 passed、フロント JS 全21ファイル esprima 構文 OK。

- **N1/N2（最後の1マイル）**: ライブラリ詳細に standardization バッジ（5語彙・数値なし）+「深く検討」ボタン
  → Phase S セクション開通。対話 grounding に同分野凍結エントリ top-k（id/名称/aliases のみ、
  `DELIBERATION_IDENTITY_CANDIDATES_TOP_K` 既定5）を供給、0件時は identity 候補生成を明示禁止
  （捏造ガード）。手動「共通部品と結びつける」UI（新設 `GET /elements/{type}/{id}/shared-part-candidates`
  → 既存 `POST /identity-links`）も追加。
- **N3/N30/N35（C層確定）**: backing_claims の確定/却下ボタン（PATCH 全置換・却下は
  `status="rejected"` 保持で可逆）+ 承認者一覧（段階ラベルのみ）+ 引用のコースセレクタ
  （owner/editor 絞り込み）。既知の限界: standard 説明（author_id=NULL）は非 admin 教員が PATCH 不可。
- **N10残/N36**: version-state に `is_owner`/`can_publish`/`role` 等を追加し、コース版モーダルを
  非所有者に読み取り専用開放（発行・削除予約は理由付き無効化）。教材テーブルに開示範囲バッジ。
- **N12/N13/N31**: Copilot capability に `executable` フラグ（挨拶文が実装済みのみ例示）、
  rewrite_chunk_script 実行ハンドラ（L2 可逆・実装済み action は3件に）、G層
  `figure.unreviewed_modes` ルール、stumbles / schema-proposals の capability+KB、
  アンカー整合ガードレールを screen 単位検査に強化。
- **N14**: `core/status/cross_layer_notify.py` 新設。C層承認受領 / D層疑義被起票 / R層 flagged
  （宛先=document 所有者。`created_by` は全経路 NULL のため）/ L層 retire（宛先=confirmed
  同一性リンクの instance document 所有者）を `source='status'` で統合インボックスへ fan-out
  （dismiss 可・migration 不要）。
- **N15残**: 学習者向け `GET /courses/{cid}/chunks/{chunk_id}/claim-refs` を新設（コース sources
  所属検証つき fail-closed）し、出典タブの台帳併記が equation + claim の両対応に。
- **N16/N17/N38**: 再構成ノードは `topics[].linked_claim_ids` 逆引きでトピック/atlas に帰属
  （解決不能は誤帰属よりゼロ帰属、設計書 §14 に明記）。トグル改名「自分の記録を重ねる」+
  最上位パネルに map-exclude。connect の edge_id 閲覧可否を fail-closed 検証。
- **N18**: readiness 正本を `core/lecture.py::compute_course_audio_readiness` に一本化
  （トピック教材経路の原稿充足を合算）、音声バッチの完了サマリを「対象トピック n / 生成 m /
  原稿未生成 k」の正直な報告に。
- **N23残/N27/N28/N29/N6残**: embedding fallback（有効 embedding を持つ最新凍結版が代表）、
  domain_key の datalist 化、connections の昇格転記（`APPARATUS_BODY_KEYS` 拡張）、
  **retired は読み取り専用**（編集・凍結 409、restore が先 — 仕様確定）、明示 OFF 再解析時の
  「未レビュー AI 分類 n 件が失われます」確認ダイアログ。
- **N32/N33**: ロックトピックは事実文トースト表示のみで遷移許可。ハンズフリーはエラー時に
  TTS/パネルでフィードバック（15秒クールダウン）。
- **N34/N40（仕様確定）**: journey 兄弟検出の非対称は意図的仕様として設計書に明記。
  counterfactual のノード数は「評価スコアでなく理論構造の事実」として数値表示維持を doubt 文書に明記。
- **N41/N42**: E層設計書に着手前提4点を追記。api.md を26ルーター・272エンドポイントの正本表に
  全面改訂、admin.md 18タブ表、frontend/overview.md 22モジュール+DI 契約、learning.md 6機能、
  agents.md ApparatusSemanticsAgent 節、**#496 図分類を L層設計書 §15 + CLAUDE.md に文書化**、
  CLAUDE.md に W層節、README 索引 29 リンク化、layer_registry に migration 帰属一覧（〜053）、
  `docs/development_checklist.md` 新設（2機能境界回帰テストの DoD 化を含む）。

**調査中に発見・未対応のコード課題（別途判断）**: `unanswered-queries`（学生名付きログが任意
TEACHER に可視）/ `reanalyze` / `bridge-insights` のコース・document ゲート欠落、
`source-chunk` のチャンク可視性ゲートなし（course_id 未使用）、`PUT /materials/{id}/pdf` が
閲覧権のみで差し替え可、原稿スタジオのチャンク単位 API に所有チェックなし、学習者向け
open-assumptions の `dependent_count` 生整数露出、theory_components.py の旧インライン LLM
抽出機構の死蔵コード群、`_classify_intent` 等の物理学ハードコード文言。
