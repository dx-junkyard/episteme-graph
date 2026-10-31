# レクチャースライド同期 + 音声言語切替 設計書（migration 040）

> **更新（migration 047・トピック教材ベースへの転換）**: レクチャー受講の**表示ソースは
> トピックの授業用教材（`topics[].student_material`）を最優先**し、非レクチャー教材表示
> （`get_topic_material`）と一致させる。読み上げは各スライドの `spoken_script` を TTS 化し、
> **`topic_lecture_audio_cache`（`(course_id, topic_id, slide_index, voice)`）**にキャッシュする。
> 旧「実チャンク教材を持つトピックはチャンク経路を優先」（`_topic_has_linkable_material`）は
> **撤去**し、表示ソース判定は `_lecture_uses_topic_material(topic)` に一本化した。以下本文中の
> チャンク優先の記述（§1-6・§2-4・受け入れ条件の一部）はこの更新で置き換わっている。
> （さらに正本は `backend/api/routes/lecture.py` の `_lecture_uses_topic_material` から
> `backend/core/lecture.py::lecture_uses_topic_material`（アンダースコアなし）へ移設済み。
> 呼び出し元は `get_lecture_sequence` / `get_topic_audio_status` / studio のトピック音声生成。）
> スライド分割は `_build_topic_slides(topic)`（受講表示・音声生成・readiness で共有・決定論的）
> が `core/lecture.py::auto_paginate_slides` を用いて行う: `===` があれば明示分割を優先、無く
> 長い教材は**段落境界で自動ページ分割**（既定600字目安）し表示と読み上げを同数ページ・同順で
> 対応させる。
> チャンク経路（`lecture_audio_cache`・スライド分割・言語切替）自体は、トピック教材を持たない
> トピックのフォールバックとして現行のまま有効。

コース受講のレクチャー再生を「スクロール追従」から「プレゼンテーション型のスライド送り」に
転換し、**表示と読み上げを構造的に一致させる**。あわせて、音声生成開始時に読み上げ言語を
**日本語 / 英語で切り替え**られるようにする。原稿スタジオ（教員）と受講体験（学習者）の
両方を、単一のメンタルモデル **「スライド + スピーカーノーツ」** で貫く。

- 対象: `backend/api/routes/lecture.py` / `lecture_studio/`（パッケージ）、`backend/core/lecture.py` /
  `tts.py`、`frontend/public/js/app.js`（受講）/ `admin.js`（原稿スタジオ）
- 非対象（変更しない）: A層パイプライン（`src/episteme_graph/agents/`）、チャンク抽出、
  通常閲覧ビュー（`#material-region` の非レクチャー時表示）、学習チャット本体

---

## 0. 背景 — 現状の2つの不一致

### 0-1. 表示と読み上げの進行が一致しない（構造的問題）

現在の受講画面は、**表示と音声が別のコンテンツソース**を使っている。

| | ソース | 単位 | API |
|---|---|---|---|
| 上段の教材表示 | トピックの `student_material` / `content` | **単一ブロック**（`topic:{id}` の1チャンク）を全文スクロール表示 | `GET .../topics/{id}/material` |
| 読み上げ音声 | コース `sources[]` の PDF 由来チャンクの `spoken_text` | **チャンク単位**の複数セグメント | `GET /api/learning/lecture/.../sequence` |

両者は内容も分割粒度も一致しないため、同期は
`overallLectureRatio`（app.js）による**全体進捗比率の線形オートスクロール**という近似しか
できない。コード自身がこの制約を明言している（app.js: 「上段の教材とレクチャーの読み上げは
別内容で厳密な位置対応が無い」）。文ハイライトも音声タイムスタンプではなく
**文字数比の推定**で、非表示ステージング領域（`#lecture-content`）経由でキャプション1文を
出すだけで、表示中の本文はハイライトされない。

→ **タイムスタンプ精度を上げても解決しない。表示のソースと単位を音声に揃えるしかない。**

### 0-2. 表示言語と読み上げ言語が一致しない

- `generate_tts_audio(spoken_text)`（`core/tts.py`）は言語引数を持たない。
  OpenAI 経路は言語未指定（テキストから自動判定）、Google 経路は `language_code="ja-JP"`
  **ハードコード**。
- 原稿（`chunks.spoken_text`）にも言語メタデータがなく、原稿生成プロンプトは
  「ソース言語に合わせる」という指示のみ。英語論文由来のチャンクでは spoken_text が
  英語になり、日本語の教材表示と読み上げがちぐはぐになる（逆も起こる）。

---

## 1. 設計原則（不変条項）

1. **同期は構造で保証する（推定に頼らない）**: スライドを表示と音声の共通最小単位とし、
   「スライド n を表示している間はスライド n の音声だけが流れる」ことを構造的に成立させる。
   タイムスタンプ推定・スクロール比率近似を同期の主手段にしない（スライド内の文ハイライト
   のみ近似を許容する。§6-3）。
2. **教員が見るものと学習者が見るものを一致させる**: 原稿スタジオのスライドプレビューは
   受講画面のスライドと同一のレンダリング・同一の分割結果を使う。プレビューで確認した
   ものがそのまま配信される。
3. **情報を落とさない**: 言語切替で旧言語の音声キャッシュ行は即削除しない（stale マークで
   保持し、原稿編集時の既存無効化ルールに従って削除される）。スライド分割マーカーの不整合は
   エラーで止めず「1スライドに縮退 + スタジオで警告」で必ず配信可能に保つ。
4. **fail-safe / 正直な表示**: スライドに収まらない長文は隠さない（自動縮小 → 等比縮小で
   全文表示）。音声が無いスライドは無音スキップせずタイマー送り + 「音声未生成」表示。
   言語切替時は「既存音声が無効になる」ことを生成前に明示する。
5. **学習者アクセス時に生成しない**: 音声生成のトリガーは従来どおり教員（原稿スタジオ）
   のみ。`generate_tts`（学習者向け）はキャッシュ配信に限定する方針を維持する。
6. **`_topic_has_linkable_material` 経由の判定を維持**: ドラフト専用トピック判定は
   従来どおり同関数を必ず経由する（CLAUDE.md 既存ルール）。スライド化はチャンク教材
   トピック・ドラフトトピックの両方で同じマーカー規約を使う。
   （この関数は migration 047 の転換で撤去済み — 冒頭注記参照）

---

## 2. スライドモデル（心臓部）

### 2-1. 定義

- **スライド = 表示と音声の同期最小単位**。既定では **1セグメント（=1チャンク）= 1スライド**。
- **display_text = 教材本文（画面に表示する学習内容）、spoken_text = そのスライドを口頭で説明する
  別個のナレーション**。両者は役割が異なり、**同一文の使い回しにしない**（字幕/カラオケ型では
  なく「スライド + スピーカーノーツ」型）。同期は「スライド枚数と順序が構造的に一致する」ことで
  保証し、表示文と読み上げ文が逐語一致することは要求しない。原稿生成プロンプト
  （`core/lecture.py::_SPOKEN_TEXT_PROMPT` / `lecture_studio/scripts.py` の各プロンプト）は
  この2役割を書き分けるよう指示する（丸読み禁止）。
- 教員は `display_text` / `spoken_text` の中に**スライド区切りマーカー**を書くことで、
  1チャンクを複数スライドに分割できる。
- マーカー: **単独行の `===`**（eg-markdown の水平線 `---` は既存文書で意味を持ち得るため
  衝突を避ける。行頭行末の空白は許容、`===` 以上の連続 `=` も区切りとして扱う）。

```
display_text:                     spoken_text:
  （スライド1の表示本文）            （スライド1の読み上げ）
  ===                              ===
  （スライド2の表示本文）            （スライド2の読み上げ）
```

### 2-2. 分割の決定論的導出（保存しない）

スライド分割結果は DB に保存せず、読み出し時に決定論的に導出する（原稿とスライドの
二重管理を避ける）。`backend/core/lecture.py` に追加:

```python
def split_slides(display_text, spoken_text, formulas) -> list[LectureSlide]
```

- `display_text` をマーカーで分割 → n 枚。`spoken_text` をマーカーで分割 → m 個。
- **n == m** のとき: i 番目の表示と i 番目の読み上げをペアにする。
- **n != m** のとき: **1スライドに縮退**（マーカーを除去した全文どうしをペアにする）。
  情報は落とさず、スタジオで不整合警告を出す（§4-3）。受講側は警告を出さない。
- `formulas` は各スライドの display_text が参照する `[[FORMULA_N]]` プレースホルダー
  だけをそのスライドに割り当てる（未参照の数式は最後のスライドに残す。落とさない）。
- 空スライド（マーカー連続・先頭末尾マーカー）は除去する。

### 2-3. 音声はスライド単位で生成・キャッシュ

- `_batch_audio_worker` は各チャンクを `split_slides` にかけ、**スライドごとに**
  `generate_tts_audio(slide.spoken_text, language)` を呼び、
  `lecture_audio_cache (chunk_id, slide_index, voice)` に保存する。
- これにより「スライド n の表示 ⇔ 音声 n の再生」が**構築時点で一致**する。
  再生時に位置合わせの計算は一切不要になる。
- 原稿編集・AI 書き換え時の音声キャッシュ無効化（当該チャンク行 DELETE）は従来どおり。
  スライド数が変わっても、チャンク単位で全スライドの音声が消えるので不整合は起きない。

### 2-4. ドラフト専用トピック（実チャンク教材なし）

`_topic_has_linkable_material` が False のトピックは従来どおり
`_build_topic_draft_segment` を使うが、`student_material.source_text` / `spoken_script`
に同じ `===` マーカー規約を適用して複数スライドに分割する（`has_audio=False` のまま。
再生は §6-4 のタイマー送り）。原稿スタジオのコーストピックドラフト編集でも同じ
マーカーが使える。

---

## 3. 言語切替モデル

### 3-1. 「コースごとに単一のアクティブ読み上げ言語」

- 対応言語は v1 では **`ja` / `en`** の2値。
- レクチャースタジオ設定（`GET/PUT /api/admin/courses/{id}/lecture-studio/settings`）に
  **`lecture_language`**（既定 `"ja"`）を追加。音声生成ダイアログの初期値になる。
- **1コースにつき同時にアクティブな読み上げ言語は1つ**。学習者側の言語選択
  （ja/en 音声の並存・切替）は v1 非スコープ（§10）。

### 3-2. 言語が効く2箇所

1. **読み上げ原稿の生成言語**: `generate_spoken_text_and_formulas(..., language)` の
   プロンプトに「spoken_text は必ず {language} で書く。display_text は原文の言語のまま
   変更しない」と指示する。**display_text（表示）は翻訳しない** — 表示言語を変えるのは
   本仕様の対象外で、この切替は「表示されている教材に対し、どの言語で説明するか」の選択。
2. **TTS の言語/ボイス**: `generate_tts_audio(spoken_text, language)` に言語引数を追加。
   - OpenAI 経路: voice は多言語対応の `alloy` のまま（言語はテキストに従う）。
   - Google 経路: `language_code` を `ja-JP` / `en-US` に切替（ハードコード撤廃）。
   - 既定値・上書きは env `LECTURE_TTS_VOICE`（任意、既定 `alloy`）。

### 3-3. 言語切替のフロー（正直な再生成）

音声生成ダイアログ（§4-4）で現在の `lecture_language` と異なる言語を選ぶと:

1. 確認文を明示: 「読み上げ原稿を **英語** で再生成し、音声を作り直します。
   既存の日本語音声は使われなくなります。」
2. 実行時: `lecture_language` を更新 → 原稿一括生成（`language` 付き、
   `override=true` で spoken_text を選択言語で再生成。display_text は保持）→
   完了後に音声一括生成（`language` 付き）を自動チェーン。
3. 旧言語の `lecture_audio_cache` 行は原稿更新時の既存無効化ルールで削除される。
   `chunks.spoken_language` に生成言語を記録し、配信・状態表示は
   「spoken_language == lecture_language の行だけを有効」として扱う（不一致は
   audio-status で `stale` 扱い。§5-3）。

同一言語での再実行は従来どおり「未生成分のみ生成」（キャッシュ有はスキップ）。

---

## 4. 原稿スタジオの UX（教員）

メンタルモデル: **原稿スタジオ = スライドとスピーカーノーツの編集画面**。
display_text = スライド本文、spoken_text = そのスライドのナレーション。

### 4-1. スライドプレビュー（displayView に `slides` を追加）

右ペインの表示切替（現行 `preview | script | formulas`）に **`スライド`** を追加。

- 選択中チャンクを `split_slides` と**受講画面と同一のレンダラ**でスライドカードの
  縦列として描画。各カードは:
  - スライド本文（KaTeX・数式プレースホルダー解決済み、受講画面と同じ見た目）
  - その下にスピーカーノーツ風の読み上げ原稿（グレー地・小さめ）
  - ステータスバッジ: `音声あり (ja)` / `音声未生成` / `原稿と音声の言語が不一致`
  - **試聴ボタン ▶**（§4-2）
  - **長さ警告**: display_text がスライド1枚の目安（初期値 600 文字、数式は 1 個 =
    60 文字換算）を超えるカードに「受講画面で縮小表示されます。`===` で分割を検討して
    ください」を表示
- ペイン上部に整合インジケータ: `表示 3 枚 / 読み上げ 3 区切り ✓`。
  不一致時は `表示 3 / 読み上げ 2 — 1枚に統合して配信されます ⚠`（§2-2 の縮退を正直に表示）。

### 4-2. 試聴（新規・重要）

現状、教員は生成した音声を管理画面で聴けない（受講画面でしか確認できない）。
スライド単位の同期を教員が**公開前に検証できる**よう、試聴を追加する。

- 新規エンドポイント: `GET /api/admin/chunks/{chunk_id}/lecture-audio`
  （query: `slide_index`、`_require_teacher`。**キャッシュ配信のみ**・生成しない）
- スライドカードの ▶ で該当スライドの音声を再生し、再生中はカードをハイライト。
  これが「受講画面でどう聞こえるか」の最短の確認手段になる。

### 4-3. エディタ支援

- `#ls-display-text` / `#ls-spoken-text` の間に **「スライド区切りを挿入」ボタン**:
  両 textarea のカーソル位置（spoken 側は対応する区切り番目の末尾）に `===` 行を挿入。
- 表示テキストの編集は読み上げ文へ無条件に追随し、マーカー行も同期対象に含める
  （旧 `syncSpoken` チェックボックス `#ls-sync-spoken` は audio ビューの廃止で到達不能に
  なっていたため 2026-09-05 に撤去。既定 ON だった挙動を無条件化しただけで挙動は不変）。
- コーストピックドラフト（`#ls-course-material-text` / `#ls-course-spoken-script`）にも
  同じ挿入ボタンと整合インジケータを付ける。

### 4-4. 音声生成ダイアログ（言語選択）

`#ls-audio-all-btn`（音声生成）押下で即実行せず、小さなモーダルを開く:

```
音声を生成します
  読み上げ言語:  (●) 日本語   ( ) English
  現在の状態:   日本語音声 24/30 スライド生成済み
  [言語を切り替えた場合]
  ⚠ 読み上げ原稿を English で再生成し、音声を作り直します。
     既存の日本語音声は使われなくなります。
                          [キャンセル] [生成を開始]
```

- 初期選択 = 設定の `lecture_language`。
- 同一言語 → 従来どおり未生成分のみ生成。
- 別言語 → §3-3 のフロー（原稿再生成 → 音声生成の自動チェーン）。進捗は既存の
  タスクポーリング UI を流用し、フェーズ（原稿 / 音声）を表示する。
- コースビルダー登録直後の自動チェーン（`auto_audio`）は設定の `lecture_language` を使う。

---

## 5. API 変更

### 5-1. 学習者向け（`routes/lecture.py`）

- `GET .../sequence` — `LectureSegment` に追加（既存フィールドは互換のため残す）:
  ```
  slides: [ { slide_index, display_text, spoken_text, formulas,
              has_audio, duration_ms } ]
  language: "ja" | "en"        # このセグメントの spoken_language（無指定は "ja"）
  ```
  `LectureSequenceResponse` に `total_slides` を追加。
- `POST .../tts` — `LectureTTSRequest` に `slide_index: int = 0` を追加。
  キャッシュキーは `(chunk_id, slide_index, voice)`。生成しない方針は不変
  （キャッシュ無しは 404）。
- `GET .../audio-status` — 返却に追加:
  ```
  ready_slides, total_slides, language,
  stale_language: bool   # spoken_language と lecture_language の不一致がある
  ```
  `has_audio` の意味は従来どおり（ready > 0）。ただし stale_language の音声は
  ready に数えない。

### 5-2. 教員向け（`routes/lecture_studio/` パッケージ）

- `POST /courses/{id}/lecture-scripts/generate` — body に `language`（省略時は設定値）。
- `POST /courses/{id}/lecture-audio/generate` — body に `language`（省略時は設定値）。
  設定と異なる言語が来た場合は原稿再生成フェーズを内包する（§3-3）。
- `GET/PUT .../lecture-studio/settings` — `lecture_language` を追加。
- `GET /courses/{id}/lecture-scripts` — `LectureScriptChunkOut` に
  `spoken_language`、`slide_count`、`slide_mismatch: bool`、
  `audio_ready_slides: int` を追加（スタジオの整合インジケータ・バッジ用）。
- 新規 `GET /chunks/{chunk_id}/lecture-audio`（試聴。§4-2）。

### 5-3. データモデル（migration `040_lecture_slides.sql`）

```sql
-- 音声キャッシュをスライド単位に拡張
ALTER TABLE lecture_audio_cache
  ADD COLUMN IF NOT EXISTS slide_index INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'ja';
-- UNIQUE(chunk_id, voice) → UNIQUE(chunk_id, slide_index, voice) に張り替え
-- （既存行は slide_index=0 のままそのまま有効 = 後方互換）

-- 原稿の生成言語を記録
ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS spoken_language TEXT;   -- NULL = 旧データ（ja とみなす）
```

- `lecture_language` は `learning_courses.data`（JSONB）内の lecture-studio 設定に保持
  （新テーブル・新列を増やさない）。
- `main.py` 起動時 DDL・`init.sql` にも同一の冪等 DDL を追加する（既存慣例）。
- 既知の不整合の解消を同時に行う: `core/models.py` の `LectureAudioCache.audio_data`
  型注記（Text → 実体は BYTEA）はコメント修正のみ（ORM は音声 IO に未使用のため挙動不変）。
  未使用の `_save_audio_cache`（`lecture.py`）は削除する。

---

## 6. 受講体験の UX（学習者）

メンタルモデル: **受講のレクチャーモード = ナレーション付きスライドショー**。

### 6-1. レクチャーモード起動時の画面

- `#material-region`（スクロール教材）を隠し、**スライドステージ**
  （`#lecture-slide-stage`、新設）に切り替える。1枚のスライドだけを表示し、
  **縦スクロールは発生させない**。
- 表示ソースは **現在スライドの `display_text`**（= 読み上げている spoken_text と
  ペアで編集されたテキスト）。従来の `student_material` 全文表示はレクチャーモード中は
  使わない（通常閲覧ビューでは従来どおり）。これが §0-1 の構造的問題の解消。
- 非表示ステージング領域 `#lecture-content` と、全体進捗比率によるオートスクロール
  （`autoScrollMaterialToProgress` / `overallLectureRatio`）は**廃止**する。

### 6-2. オーバーフロー処理（スクロールさせない）

1. スライド本文はステージ高さに `fit`: まず基準フォントサイズから最小サイズ
   （例 70%）まで段階的に縮小。
2. それでも収まらない場合は **等比縮小（CSS transform scale）で全文を1画面に収める**
   （プレゼンテーションソフトと同じ挙動。隠すよりも縮む方を選ぶ。§1-4）。
3. 恒常的な対策はスタジオ側の長さ警告と `===` 分割に誘導する（§4-1）。

### 6-3. 再生と同期

- 再生開始: `POST .../tts` に `{chunk_id, slide_index, voice}`。スライドの音声を
  そのまま再生。**音声とスライドは 1:1 なので位置合わせ処理は存在しない。**
- 音声 `ended` → 次スライドへ自動送り（次チャンクの先頭スライドへも連続）。
- **文ハイライト**: 現行の文字数比近似（`annotateSentences` + ratio 推定）を、
  **表示中のスライド本文そのもの**に適用する。表示と読み上げが同一ペアになったため、
  近似でも実用精度になる。キャプション領域（`#lecture-caption`）は廃止し、
  スライド内の現在文を `.lecture-sentence.reading` で直接ハイライトする。
  （将来: `word_timestamps` を書き込むようになれば精密化。v1 非スコープ）
- コントロールの意味を変更: ◀/▶ = **スライド移動**（チャンク境界をまたいで連続）、
  進捗表示 = 「スライド N / M」+ プログレスバー。頭出し・一時停止は従来どおり。
- ~~`segment_mode` の扱い: `skip` はスライドも出さない、`summary` は要約 spoken_text
  1件 = 1スライド（display は従来どおりセグメント本文の要約表示）。~~
  **2026-09-10（是正 F3 / 六つのレンズ 提案1）に沈黙適応を撤去したため、`skip` /
  `summary` は発生しない**（`segment_mode` の語彙と `_build_slides_for_segment` の
  `summary` 分岐は互換のため残置。実挙動は常に `full`）。学習者の状態で内容を変えるのは
  やめ、注記フラグ `previously_touched` と再生バーの「短く聴く」トグル（本人の操作・
  既定 OFF・畳んだ位置に「前に触れた箇所（開く）」を残す）に置き換えた。詳細は
  [learning.md](learning.md) §4 と
  `docs/architecture/six_lenses_2026-09-10/01_learner.md` §2 提案1。

### 6-4. 音声が無いスライド

- `has_audio=false` のスライド（ドラフトトピック、生成漏れ）はスライド右下に
  小さく「音声未生成」を表示し、`simulatePlayback` 相当のタイマー
  （日本語 300 字/分・英語 150 wpm・最低 3 秒）で自動送りする。無音でも表示は
  スライド単位で進むため、進行の同期は保たれる。

### 6-5. 中断チャット・トグル

- 中断チャット（質問）は従来どおり。送信 payload の `current_chunk_id` に加えて
  `current_slide_index` を添付する（interest_traces の `structure_anchor` が
  将来スライド粒度を使えるようにするための記録のみ。挙動変更なし）。
- レクチャートグルの有効化判定（`audio-status`）は従来どおり。ただし
  `stale_language: true` のときは「音声の言語が更新待ちです」をツールチップで表示し、
  トグル自体は既存の ready 判定に従う。

### 6-6. スタジオと受講の対応表（違和感のない UX の要）

| 原稿スタジオ | 受講画面 |
|---|---|
| スライドカード（display_text をスライドレンダラで描画） | まったく同じレンダリングの1枚表示 |
| カード下のスピーカーノーツ（spoken_text） | そのスライドで流れるナレーション音声 |
| `===` マーカー | スライドの切れ目（自動送りの単位） |
| カードの長さ警告 | 縮小表示の発生予告 |
| 試聴 ▶（スライド単位） | 再生（スライド単位） |
| 音声生成ダイアログの言語選択 | ナレーションの言語 |

教員は「スタジオで見たスライド列・聞いた音声」がそのまま学習者に届く。
プレビューと配信のレンダラ・分割ロジックを共有すること（§1-2）が実装上の必須要件。

---

## 7. 実装フェーズ

1. **Phase 1 — スライド基盤（バックエンド）**: `split_slides` +
   migration 040 + `_batch_audio_worker` のスライド単位生成 + sequence/tts/audio-status
   拡張。マーカーなし原稿は全経路で従来と同一挙動（1チャンク=1スライド、slide_index=0）
   であることをテストで保証。
2. **Phase 2 — 受講スライドビュー（app.js）**: スライドステージ・自動送り・
   スライド内文ハイライト・オーバーフロー fit。旧オートスクロール/キャプション廃止。
3. **Phase 3 — スタジオのスライド編集（admin.js）**: スライドプレビュー・整合
   インジケータ・区切り挿入・長さ警告・試聴。
4. **Phase 4 — 言語切替**: `lecture_language` 設定・生成ダイアログ・
   原稿→音声の言語付き再生成チェーン・`tts.py` の言語引数（Google `ja-JP`
   ハードコード撤廃）。

Phase 1+2 だけで「進行の一致」は解消する（既存原稿はマーカーなし = チャンク単位
スライドとして即動く）。Phase 4 は独立に着手可能。

---

## 8. テスト・ガードレール観点（`backend/tests/test_lecture_slides.py` 想定）

- `split_slides` の決定論性: 同一入力 → 同一分割。マーカーなし = 1スライド。
  分割数不一致 = 1スライド縮退 + mismatch フラグ。formulas の割当が全スライドの
  和で元の formulas と一致（情報を落とさない）。
- 後方互換: 既存の `lecture_audio_cache` 行（slide_index=0）がマーカーなしチャンクで
  そのまま配信されること。旧フロント互換のため `LectureSegment` の既存フィールドが
  残っていること。
- キャッシュ無効化: 原稿保存 / AI 書き換えで当該チャンクの**全スライド・全言語**の
  音声行が削除されること。
- 言語: `spoken_language != lecture_language` の音声が audio-status の ready に
  数えられないこと。Google 経路で `language` が locale に反映されること。
- 学習者経路から音声生成が起きないこと（`generate_tts` はキャッシュ配信のみ、404 方針）。
- ドラフト判定が `_topic_has_linkable_material` を経由していること（既存ルール）。
- 試聴エンドポイントが `_require_teacher` で保護され、生成を行わないこと。

---

## 9. 移行・後方互換

- **既存コースは無変更で動く**: マーカーなし原稿は 1チャンク=1スライド、既存音声
  キャッシュは slide_index=0 として有効。受講画面は「チャンク単位のスライドショー」に
  なり、これだけでも表示と音声の対応は現状より厳密になる。
- `chunks.spoken_language IS NULL` は `ja` とみなす（既存データは日本語前提で生成
  されているため）。実態が英語の旧原稿は、次回の音声生成ダイアログで言語を選び直せば
  正しいメタデータに揃う。
- 旧 UI 要素の削除（キャプション領域・`#lecture-content`・線形オートスクロール）は
  Phase 2 で行い、CSS の `.lecture-caption` 系は同時に整理する。

## 10. 非スコープ（v1 でやらないこと）

- 学習者側の音声言語選択（ja/en 音声の並存配信）— キャッシュキーに `language` を
  含める拡張で将来対応可能だが、v1 は「コースごとに単一言語」。
- `display_text`（表示教材）の翻訳。
- 通常閲覧ビュー（非レクチャー時の教材表示）のスライド化。
- `word_timestamps` による単語レベル精密ハイライト（生成経路が timestamps を
  書いていない既知の未実装。スライド同期で必要性が下がるため見送り）。
- ja / en 以外の言語。
- カジュアル音声会話（`/voice/speak`）の言語切替 — 別機構のため対象外。

## 2026-10-23 追補（第 25 周 面 D 第 1 周の是正）

- **区切りが自動分割を止めた事実（IK-0878）**: `POST /api/admin/lecture-studio/preview-split` は
  `auto_paginate_suppressed`（`===` が 1 つでもあり自動ページ分割をしなかった）と `notes`（理由と揃え方の事実文。
  正本は `core/lecture.py::slide_split_notes`・JS にミラーしない）を返す。統合で配信される状態のまま保存するときは
  同じ内容につき 1 回だけ確認する（`admin-lecture-studio.js::lsSaveCourseTopicDraft`）。
- **保存の副作用（IK-0877）**: トピック保存は DELETE の rowcount を `audio_invalidated` で返し、消したときだけ
  `notice`（`core/lecture_studio_state.TOPIC_AUDIO_INVALIDATED_NOTICE`）を添える。音声のあるトピックでは保存の
  近くに `TOPIC_AUDIO_SAVE_NOTICE` を常に出す。コースの状態はトピック教材の経路ではトピック単位
  （course-structure の `topic_audio_status`）で言い、チャンクの数を出さない。監査の
  `topic_audio_cache_invalidated` は実際に消したときだけ True。
- **学生の講義の入口（IK-0879）**: `audio-status` に `lecture_note`（`core/lecture.py::lecture_availability_note`）。
  音声ゼロでもスライドがあれば講義に入れ、全スライドをタイマー送りにする（入口と再生の条件を揃えた。学習者経路から
  音声を作らない規律は不変）。マニュアル `student/02-student.md#interactive-lecture` を実装に合わせた。
- **チャンク経路の読み上げ（IK-0881）**: `persistence._spoken_text_from_formulas` は `[[id]]` と裸の `id`（語境界）を
  同じキーで置換し、読み上げ文の無い式は講義の言語の語（ja「数式」/「式 (N)」・en「the equation」/「equation (N)」）。
- ガードレール: `test_lecture_studio_round25.py`。

## 2026-10-23 追補（第 25 周 面 D 第 2 周の是正）

- **保存の可否と届き方を 1 つの値で（IK-0908 / IK-0915）**: course-structure は `can_save`（保存 API と同じ所有者 /
  SYSTEM_ADMIN の判定）・`save_notice`（editor 共有のとき、所有者へ依頼する事実文）・`delivery`
  （`{status: immediate|release, message}`。版が無ければ「すぐ受講者に届きます」、版があれば「版 N を見ています。
  届けるには新しい版を発行」。受講者のいない非公開コースではキーなし）を返す。正本は
  `core/lecture_studio_state.py::topic_save_facts`（条件は V層の版解決 `services._apply_course_version_view` と同じ）。
  保存の 403 は `TOPIC_SAVE_FORBIDDEN_DETAIL`（日本語）で、画面は detail を描く。保存の応答にも `delivery`。
  editor では保存ボタンを無効にして理由を出し、書き換えの応答に `save_notice` を添える（書き換えは editor に許したまま）。
- **保存・採用の後にサーバの派生を取り直す（IK-0910）**: `lsRefreshTopicServerFacts(topic, {keepDraft})` が
  course-structure から当該トピックの `evidence_items` / `evidence_state` / `appendix_state` 等だけを写す
  （採用直後は編集中の本文に触れない）。
- **区切りの事実文（IK-0914）**: `slide_split_notes(..., display_text=, spoken_text=)` は欄の先頭・末尾・連続の区切りが
  スライドにならず枚数に数えないことを `SLIDE_MARKER_IGNORED_NOTE` で言う（`has_ignored_slide_markers`。分割の挙動は不変）。
- **分割インジケータの係留（IK-0913）**: `#ls-course-slides-indicator` に `data-ui-anchor="lecture-studio.slides-indicator"`、
  マニュアル `teacher/14-admin-lecture-studio.md#slides-indicator`、`ADMIN_UI_ANCHORS` の 3 点。負荷ラベルの節の見出しから
  内部の呼び名を外した。
- **付録の付け直しは明示の操作だけ（IK-0872 / IK-0880 の残り・オーナー判断の推奨どおり）**: 保存済みの付録は読み時に
  付け直さない（配信済みのスライド境界と音声キャッシュの番号を黙って動かさない）。
  `course_content_builder.reattach_generated_appendix` / `topic_appendix_is_stale`（生成時と同じ `_ensure_required_*`）で、
  course-structure のトピックに `appendix_state`（違うときだけ）、`POST .../course-topics/{topic_id}/appendix/reattach`
  （DB 非変更・LLM なし・付け直した本文を返すだけ。確定は既存の保存）、[付録を付け直す]（`lecture-studio.reattach-appendix`・
  押す前に `appendix_reattach_confirm` で音声とスライドの区切りが動くことを言う）。G層 `course.topic_appendix_stale`
  （optional・道案内のみ・既存 capability `lecture_studio.generate_audio` の最初の段＝コース選択だけを使う
  `_make_step(locate_steps_limit=1)`）。教員が付録を消したトピックは対象外。
- **書き換えの根拠の予算（IK-0909）**: `compact_rewrite_evidence` が直列化の前にキーごとに予算を配り（学ぶ単位を先頭に必ず
  残す・リストは項目単位・JSON を壊さない）、`is_uninformative_equation_summary` に当たる状態の文を材料から外す。省いた
  材料は応答の `grounding_trimmed` / `grounding_notice`。
- ガードレール: `test_lecture_studio_round25_r2.py`。

### pre-mortem（第 2 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 保存済みの付録を作り直す | `stale-derivative-served` | 読み時に付け直さず、教員の明示操作 + G層の道案内（境界・音声が黙って動かない） |
| 採用・保存の後の根拠一覧 | `stale-derivative-served` | 成功の直後に course-structure を取り直す（入口 2 つ） |
| 書き換えの根拠を切る | `information-dropped-as-unrepresentable` | 直列化の前にキーごとの予算・切ったことを記録 |
| 保存の権限と画面 | `condition-not-propagated` | 保存 API と同じ判定を course-structure で返し、画面はその値だけを見る |

## 2026-10-23 追補（第 25 周 面 D 第 3 周の是正）

- **読む表示と講義は `build_topic_slides` の戻り値を同じ契約で受け取る（IK-0944 / IK-0946）**: 受講画面の教材区画
  （`topic_material_delivery_segments`）は 1 枚にしか割れないときもスライドの本文（区切り `===` を剥がした
  display_text）を配り、全文（区切りが残る）へ落ちるのはスライドが作れないとき（教材なし・例外）だけ。式の解決表は
  **id の鍵（`[[FORMULA_n]]`・正規化形）を優先**し、並び順の鍵（`FORMULA_i`）は id の鍵がまだ空いているときの
  救済だけ（`app.js renderMaterialChunk`・原稿スタジオの 3 箇所のプレビューが同じ規則）。スライドに付く式は
  そのスライドが引く式だけの部分集合なので、並び順の鍵が上書きすると 2 枚目以降の式が 1 つずつずれていた。
  ハーネスの写し（`uxsim` の resolve_formula_placeholders）は投影同期係が同じ規則に揃える。
- **空のトピックは空だと言う（IK-0947）**: 教材が空なら `preparation_notice` に `label_vocab.TOPIC_MATERIAL_EMPTY_NOTICE`
  （生成中なら `TOPIC_MATERIAL_EMPTY_PREPARING_NOTICE`）。講義ボタンの事実文 `lecture_availability_note(...,
  has_reading_material=)` は読む表示に本文があるときだけ「📖 読む で読めます」を言う。開示範囲の変更・クイック公開・
  リリース前の確認の公開の応答に、本文の無いトピックが残るなら `COURSE_PUBLISHED_WITH_EMPTY_TOPICS_NOTICE`（件数なし・
  公開は止めない = RR7）。
- **下書きの欄の規則表は 1 つ（IK-0948）**: `course_content_builder.TOPIC_DRAFT_FIELD_RULES`（注意点 = 学習者がつまずく点・
  適用条件・前提。番地・内部 ID・作業メモを書かない）をコース内容生成と AI 書き換えの両方の生成指示が読み、
  `TOPIC_DRAFT_HANDOFF_RULE` で教員への申し送りを `handoff_notes` に分ける。モデルが注意点に混ぜた作業メモは
  `split_cautions_and_handoff` が決定論で申し送りへ移す。画面は申し送りを書き換えの知らせ「申し送り:」に出し欄に入れない。
- **自作の説明図は根拠に数えない（IK-0949）**: `extra.teaching` の図は `topic_evidence_state` の判定から外し、根拠の項目に
  `teaching: true` / `origin_label = TEACHING_FIGURE_ORIGIN_LABEL`（「教員が作った説明図」）。題名の「図:」の前置をやめ、
  種類の印は `lsEvidenceItemKindText` の 1 箇所で付ける。
- **保存できない教員（IK-0950）**: 書き換えの知らせは `can_save=false` のとき `REWRITE_PROPOSAL_UNSAVABLE_NOTICE` に**置き換え**
  （「保存すると確定」に権限の文を継ぎ足さない）。course-structure に `share_mark`（`COURSE_ROLE_LABELS["editor"]`）、
  モーダルに［案をコピー］（`lecture-studio.rewrite-copy`・クリップボードのみ・DB 非変更）。
- **題名の TeX（IK-0951）**: 題名に数式の記法を書かない約束を生成指示（コースビルダー）に入れ、`course_data.title_has_raw_tex` /
  `course_titles_with_raw_tex` で検出してコース登録の応答と course-structure に `title_notice`（`COURSE_TITLE_RAW_TEX_NOTICE`）+
  `raw_tex_titles`。表示側でのインライン描画はしない（題名の使い道が多い）。
- **見出しの内部語と章番号（IK-0952）**: 旧い状態語の「チャンクなし」を見出しに出さず、`no_topics` でもサーバの事実文を出す。
  章題が「第N章」で始まるなら前置しない（`lsChapterHeading`）。
- **残り（IK-0909 / IK-0914）**: 書き換えの根拠の予算はリスト・ブロックを丸ごと落とさず項目単位で公平に配る。区切りの
  事実文は読み上げ欄が空の分岐でも出す。
- ガードレール: `test_lecture_studio_round25_r3.py` / `test_lecture_reading_lecture_contract.py` / `test_course_entry_gate.py`。

### pre-mortem（第 3 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 部分集合の式の解決表 | `id-namespace-conflated` | id の鍵を優先し並び順は救済だけ（3 箇所の写しを同時に） |
| 1 枚の分岐で区切りを剥がす | `rule-enforced-per-surface` | 割った後の本文を 1 枚でも配る（掃除を分割の分岐に閉じない） |
| 入場判定を 1 つに | `rule-enforced-per-surface` | `resolve_course_entry` に委譲・方針は auth-visibility.md §3 |
| 自由記述の欄を AI に埋めさせる | `duplicate-canonical-sources` | 欄の宛先を規則表で両方の生成指示に渡し、申し送りは別キー |
| 空のトピックを配る | `disclosure-not-declared` | 空・準備中・公開時の準備状態を事実文で運ぶ |
| 自作図を根拠の置き場に入れる | `projection-mistaken-for-source` | 出所の印を判定と一覧で区別 |
| editor の書き換え | `permission-and-affordance-asymmetric` | 既定文を置き換え・見出しの印・持ち出しはコピーだけ |


## 2026-10-25 追補（第 25 周 面 D 第 4 周の是正 = 第 4 波）

正本: `uxsim/runs/round25/judge_studio_r4.md` §3（IK-0975〜0979）。§6 のオーナー判断候補 1（共有教員に所有者の名前を見せる）は
推奨どおり「見せる（教員間だけ・学生の DTO には出さない）」で実装した。

- **スライドの境目を決める入口は 1 つ（IK-0975）**: 分割にかける前の入力（`$…$` → `[[FORMULA_N]]`・`![[equation:…]]` /
  `![[figure:…]]` の解決）を `core/lecture.py::prepare_topic_slide_input` に切り出し、`build_topic_slides` と原稿スタジオの
  `POST /api/admin/lecture-studio/preview-split`（`auto_paginate=true`）の両方がこれを通る。プレビューは optional
  `course_id` / `topic_id`（/ `chapter_index` / `topic_index`）で保存済みトピックの文脈（evidence_links / content_blocks）を借り、
  編集欄の未保存の本文を student_material / spoken_script に重ねた疑似トピックを作る（`_preview_topic_context`。閲覧できない・
  見つからないときは黙って文脈なし = 応答の形は変えない）。生の本文を `auto_paginate_slides` に直接渡す経路は無くなった。
- **読みの単位で詰める（IK-0976）**: `_group_units_by_size` は段落ではなく `_reading_blocks`（見出しだけの段落は次と・式だけの
  段落は直前と結ぶ）を単位に貪欲に詰め、結んだ単位が目安の 2 倍を超えるときだけ単位の中を段落で切る。境目は段落の境界のまま
  なので、表示と読み上げの同じ index 境界の対応は崩れない。境目が動いたトピックの既存音声は、保存・再生成で作り直す既存の規則
  （IK-0877）に乗る（構造の判断ではない — judge §6）。
- **復元の印は式の写しへ運ぶ（IK-0977 / IK-0895 / IK-0872 ②）**: 判定の正本は `learner_context_common.
  equation_latex_is_reconstructed`（平坦形も原文の LaTeX・原文と一致すれば印なし）。写しの照合は `reconstructed_latex_keys` /
  `latex_copy_is_reconstructed`（正規化後の完全一致だけ）。`prepare_topic_slide_input` が本文に書き写された式に印を引き直し
  （`_mark_reconstructed_formula_copies`）、`annotate_reconstructed_formulas` も正本を通す。講義の DTO（`LectureFormulaItem`）が
  印を落としていたのを直し（`reconstructed` / `reconstructed_mark` / `reconstructed_note` / `latex_withheld`）、`app.js` は
  `[[FORMULA_N]]` の写しでも式カードと同じ描き方で印を出す。根拠の式（`build_topic_evidence_items`）・記号の吹き出し
  （`symbol_lookup` — 式一覧と、原文の LaTeX の無い定義式）・教材図スタジオの資料（grounding の行と
  `RECONSTRUCTED_EQUATION_CONSTRAINT`）も同じ印を持つ。付録の付け直しは英語の要約を説明に採らず、本文が触れていない
  復元式を足さない。
- **持ち出しは「何を・誰に」を言う（IK-0978）**: course-structure の保存不可の教員向けに `owner_display_name` / `owner_notice`
  （`COURSE_OWNER_NOTICE`）/ `proposal_copy_notice`（`PROPOSAL_COPY_NOTICE` の `{sections}` を画面が埋める）。［案をコピー］は
  教材欄・読み上げ原稿・注意点・確認問題・申し送り（書き換えの応答の `handoff_notes` を画面の状態に保持）を写す。
  `disclosure_axes`（course_materials の name_disclosure）に 1 行足した。
- **図の採用の知らせ（IK-0979）**: 固定文 `ADOPT_NOTICE` をやめ、course-structure の `figure_adopt_notice_parts`
  （`lead` / `audio` / `delivery` — delivery は保存の届き方と同じ文、届く経路の無いコースは `TOPIC_DELIVERY_NONE_NOTICE`）と
  トピックの `has_topic_audio` から図スタジオが組む。
- **束ねなかったもの**: 保存済みの旧い付録が学生に届く（IK-0872 ①）は「読み時に付け直さない」判断のまま・区切りを 1 つ入れると
  3 枚 → 1 枚（IK-0878）は区切りの挿入の UI の問題で境目の入口とは別の根。
- ガードレール: `test_lecture_studio_round25_r4.py`。

### pre-mortem（第 4 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| プレビューと配信の入力 | `rule-enforced-per-surface` | 正規化を 1 関数に切り出し、両経路がそれを通る |
| 長さの目安で区切る | `extent-decided-by-proxy-signal` | 読みの単位を先に結び、境目は段落境界のまま |
| 印を写しへ運ぶ | `condition-not-propagated` | 判定は 1 関数・写しは完全一致で引き直す・DTO が落とさない |
| 持ち出しの中身と宛先 | `last-mile-missing` | 区画名と所有者の表示名（教員間だけ）をサーバの文で |
| 固定の採用の知らせ | `wording-mismatch` | 保存側と同じ値・同じ文の部品から組む |

## 2026-10-27 追補（第 25 周 面 D 第 5 周の是正 = 第 5 波）

正本: `uxsim/runs/round25/judge_studio_r5.md` §3（IK-1008 / 1010 / 1011）と §1 の残り（IK-0895 STILL・
IK-0872 ②・IK-0878 の構造）。IK-1007（表示名）と IK-1009（チャットの式の事実行）は別枠の是正。

- **印と表示の可否は式の単位で 1 関数から（IK-0895 / IK-1008）**: `learner_context_common.
  equation_display_fields(record, *, latex=None, reconstructed=None)` が（`reconstructed` /
  `reconstructed_mark` / `reconstructed_note`、壊れた復元なら `latex: ""` + `latex_withheld: True`）の組を返す。
  壊れ方の判定 `is_broken_reconstructed_latex` も同モジュールへ移し（`core.lecture` は再エクスポート）、
  箇条書きの点（U+2022 — 結果の値の列挙が式の候補に入った形）を壊れ方に加えた。`annotate_reconstructed_formulas`
  （講義・読む表示・チャットの formulas）・記号の吹き出し（`symbol_lookup._reconstructed_mark_fields` — 式一覧と
  定義式。伏せた式も一覧から落とさず事実文だけ残す）・根拠の式（`_evidence_reconstructed_fields`）がこれを通る。
  `![[equation:id]]` の埋め込みと `[[FORMULA_N]]` の解決は、写しの LaTeX の一致ではなく**同じ id の記録**
  （根拠リンク ∪ content_blocks）のどれかが復元なら復元とする（`lecture._equation_unit_reconstructed`）—
  描く LaTeX が根拠リンク側で content_blocks の記録と文字が違っても印が落ちない（作用の式 S の食い違いの根）。
  `app.js` は `latex_withheld` の式を KaTeX で描かず、印と事実文だけを出す（`renderMaterialEquationBody` の先頭）。
- **人が加えたものと機械が拾ったものを区画で分ける（IK-1010）**: `topic_evidence_state` は教員が加えた資料
  （`role == "teaching_figure"` / `teaching`）があれば `teacher_added_heading`（「教員が加えた資料（教材欄に
  埋め込んだ順）」）を返す。根拠の状態（`status`）の判定は変えない。原稿スタジオは中央のチップと右ペインの
  一覧の両方で、教員の資料を先頭の区画に教材欄の `![[figure:id]]` の位置順で並べ、`unmapped_heading` は
  解析の候補の区画にだけ掛ける（`lsSplitTeacherAddedEvidence`）。
- **申し送りの根拠は教員の画面で読める材料だけ（IK-1011）**: 書き換えの規則 `TOPIC_DRAFT_HANDOFF_RULE` に
  「外す・取り下げる根拠は現在の下書きか根拠候補の見出し・要約・式からの逐語の引用で添え、論理ブロックの要約など
  画面に出ない材料は挙げない」を足し、応答は `lecture_studio_state.qualify_handoff_basis` が決定論で検査する
  （照合先は `teacher_visible_texts` = 前の版の下書き・題名・概要・`build_topic_evidence_items` の見出し・要約・
  式・図題）。照合できない引用・引用の無い取り下げは項目を落とさず末尾に `HANDOFF_UNVERIFIED_BASIS_NOTE`。
- **付録の名前と本文との整合（IK-0872 ②）**: 名前の段に「定める記号」（`${記号}$ を定める式`。左辺が短く演算・
  積分・和・分数を含まないときだけ記号とみなす — `_equation_defined_symbol`）を足し、どの段でも名前が付かない式
  （旧「この節の式」）は付録に足さない。本文が同じ文の中で扱わない語（追わ / 扱わ / 立ち入ら / 触れない / 省略 /
  割愛）と共にその式の印字番号・記号・添字の族（`\mathcal{L}_i` は `\mathcal{L}_4` の族）に触れていれば足さず
  （`_text_declines_equation`）、本文に既に埋め込んだ式と同じ記号を定める式（同じ式が 2 つの id で供給された断片）
  も足さない。生成時と［付録を付け直す］が同じ関数を通る。
- **区切りを両方の欄へ（IK-0878 の構造）**: 分割の規約（教員の区切りが優先・数が合わなければ 1 枚）は不変。
  原稿スタジオに［+ 両方の欄に区切りを挿入］（教材欄のカーソル位置 + 読み上げ欄を揃える）と、統合の告知が出ている
  ときだけ出る［読み上げ欄の区切りを教材欄に揃える］を足した（`lsAlignSpokenSlideMarkers` — 読み上げ欄の区切りを
  外し、教材欄の内側の区切りの相対位置に近い段落の境目（足りなければ文の境目）へ入れ直す。教材欄は変えない。
  入れきれなければ事実文で知らせる）。アンカー `lecture-studio.insert-slide-marker-both` /
  `lecture-studio.align-slide-markers`。
- **束ねなかったもの**: 保存済みの旧い付録（IK-0872 ①）は「読み時に付け直さない」判断のまま。平坦形の
  chunks.formulas に原文の LaTeX が無い式の判定そのもの（再解析で揃う）は変えていない — 本波は「同じ式の記録の
  どれかが復元なら写しも復元」を式の id で揃えた。
- ガードレール: `test_lecture_studio_round25_r5.py`。

### pre-mortem（第 5 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 印だけ借りて伏せる規則を借りない | `rule-enforced-per-surface` | 印と可否の組を 1 関数（`equation_display_fields`）にし、吹き出し・講義・根拠の式・写しが通る |
| 同じ式の記録が 2 つで印が食い違う | `duplicate-canonical-sources` | 埋め込みは式の id の全記録で判定（写しの文字一致に頼らない） |
| 人の項目に機械の見出しが掛かる | `wording-mismatch` | 見出しを出所ごとの区画に分ける（判定は不変） |
| AI が人の記述を外す根拠を画面外に置く | `ai-decides-instead-of-human` | 引用の照合を決定論で行い、照合できない項目に事実文（項目は落とさない） |
| 名前の無い付録・本文と食い違う付録 | `fallback-fabricates-missing-link` | 名前は記号から・名前が付かない式と本文が扱わない式は足さない |
| 区切りの片側だけ入る | `wording-mismatch` | 規約は不変・両欄へ入れる手段と揃える導線を足す |

## 2026-10-27 追補（第 25 周 面 D 第 6 周の是正 = 第 6 波）

正本: `uxsim/runs/round25/judge_studio_r6.md` §2・§3・§6（IK-0895 STILL / IK-1032〜1034 / IK-0976・IK-1010 の残り）。
オーナー判断の候補 2 件は推奨どおりに実装した（下の「オーナー判断」）。

- **印は生きた式の記録から読み時に引く（IK-0895）**: 根は「凍結した写し（content_blocks の式・根拠リンク・講義の
  式）に復元の状態が無い」こと（第 5 波の「同じ id の記録のどれかが復元なら」は、どの記録にも状態が無いので効かなかった
  — 固定コースの講義の式 253 件で復元 0 件）。`learner_context_common.EquationReconstructionIndex` が document ごとの
  生きた記録（採用 run の `equation_semantics` artifact の式 ∪ `chunks.formulas`）の判定（印の正本
  `equation_latex_is_reconstructed`）を、式の id → 印字番号 → 正規化した LaTeX の鍵で引ける形にする（同じ鍵の記録は
  OR・論文が分からず答えが割れる鍵は使わない）。DB から集めるのは `core/equation_reconstruction.py`
  （`resolve_artifact_runs` = adopted ポリシ・120 秒キャッシュ・失敗は空の索引で写し自身の判定に戻る）。
  判定の入口は 1 関数 `equation_is_reconstructed(record, *, index, document_id)`（索引で引ければその答え、引けなければ
  記録自身の判定）で、`equation_display_fields` / `reconstructed_equation_note` / `annotate_reconstructed_formulas` /
  `formula_fact_line` がこれを通る。5 か所の配線: 本文・付録（`get_topic_material`）と講義（`get_lecture_sequence`）は
  `services.overlay_topic_equation_reconstruction` がトピックの写しに状態（`reconstructed` + `reconstruction_checked`）を
  載せた複製を読み、チャットは表示中の教材（`_topic_formula_fact_line`）と出典のチャンク（`services._formula_fact_line`）、
  部品の文脈は `component_context._build_equations` が索引を受け取る。`reconstruction_checked` の付いた写しは決まった
  答えとして扱い、写しの LaTeX と原文の照合で引き直さない（画面には出さない内部の印）。新しい freeze は
  `_frozen_reconstruction_state` で写しに状態を書き（記録が状態を持つときだけ）、`chunks.formulas` 由来の補充枠の
  `reconstructed` も運ぶ。`must_not_treat_as_source_extracted` は印の判定に単独では使わない（PDF 由来の一律の前提でも
  立つため印を付け過ぎる — 第 2 波の正本のまま）。保存データは書き換えない。
- **付け直しの結果を言い分ける（IK-1032）**: `reattach_generated_appendix` が `outcome`（`replaced` / `removed` /
  `added` / `unchanged`）を返し、知らせは `lecture_studio_state.appendix_reattach_notice` の 1 箇所で分岐ごとに
  （取り除いたときは「いまの根拠からは付録に載せる式・図が無いため、付録を外しました。保存すると確定します。」）。
- **トピック概要は 1 項目（IK-1034）**: `build_topic_evidence_items` は `source:topic_summary` 1 項目に `alt_ids:
  ["summary"]` を持たせ、別名は解決の索引だけにする（`_evidence_item_keys`・app.js `lookupEvidenceItem`・原稿スタジオ
  `lsEvidenceItemByRef` が `alt_ids` でも引く）。
- **記号の定義の文に添える式（IK-1033）**: `symbol_lookup.equation_defines_symbol` — 記号（引数 `(r)` を外した本体・
  `\frac{dm}{dr}` の `d` は外して見る）が最初の `=` より前（左辺）に無い式は、定義の文に添えない（文は残す・式の名前も
  外す）。文も無い式は候補にしない。`=` の無い式は判定しない。
- **読みの単位を 2 つ足す（IK-0976 の残り）**: `_reading_blocks` は、見出しの直後の導入 1 文（1 文・160 字以内の散文）と
  `：` で終わる導入の段落を、その次の本体（箇条書き・表・本文 — 次の見出しは除く）と結び、式だけの段落の直後の解説 1 文を
  式と結ぶ。結んだ単位が目安の 2 倍を超えれば従来どおり段落で切る。境目は段落の境界のまま（表示・音声・readiness の一致は不変）。
- **解析の候補の区画の見出し（IK-1010 の残り）**: `topic_evidence_state` は教員が加えた資料があり、`unmapped_heading` が
  出ない（根拠の状態が missing 以外の）とき `analysis_heading`「論文の解析から拾った根拠（式・主張・原文抜粋・概要）」を返し、
  中央のチップと右ペインは解析の区画（`data-evidence-section="analysis"`）にそれを掛ける。根拠の状態の判定は不変。
- **オーナー判断 1（保存済みの教材は書き直さない）**: 付録・注意点・凍結した写しを是正のたびにシステムが書き直すことはしない
  （IK-0895 の写しも読み時に引くだけ）。所有者への道案内は原稿スタジオ側に留め、新しい G層 To-Do は作らない（既存の
  `course.topic_appendix_stale` はそのまま）。コース見出しに事実文 `APPENDIX_STALE_COURSE_NOTICE` + 該当トピックの題名 +
  ［付録をまとめて付け直す］（`POST /api/admin/courses/{id}/lecture-studio/appendix/reattach-all`・所有者 / SYSTEM_ADMIN だけ・
  提示した一覧 ∩ いま付録が古いトピックだけを付け直して保存・付け直したトピックの音声キャッシュを消す・監査
  `AUDIT_ENTITY_COURSE_TOPIC` / `action=topic_appendix_reattached_all` に `decision_context`（basis
  `lecture_studio.appendix_reattach_all`・提示 / 適用 / 対象外 = `not_stale_or_unchanged`）。押す前に音声・区切りが動くことを
  `APPENDIX_REATTACH_ALL_CONFIRM` で確かめる。editor にはボタンを無効にして `APPENDIX_REATTACH_ALL_OWNER_ONLY_NOTICE`。
  アンカー `lecture-studio.reattach-appendix-all`。
- **オーナー判断 2（音声生成は所有者だけ）**: `POST /api/admin/courses/{id}/lecture-audio/generate` はトピックの保存と同じ
  `ensure_course_owner` を通し、editor には 403 + `TOPIC_AUDIO_GENERATE_FORBIDDEN_DETAIL`。`topic_save_facts` が editor に
  `audio_generate_notice` を返し、原稿スタジオの音声準備の状態に `owner_only`（理由はサーバの文・次の操作「所有者に依頼する」）。
  学習者経路からの音声生成禁止は不変。
- ガードレール: `test_lecture_studio_round25_r6.py`。キャッシュの版 `admin-lecture-studio.js?v=…-studio6` / `app.js?v=…-studio6`。

### pre-mortem（第 6 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 凍結した写しの印が生きた記録と食い違う | `duplicate-canonical-sources` | 判定の正本は生きた記録の 1 関数（`equation_is_reconstructed`）。写しは決まった答え（`reconstruction_checked`）だけを持つ |
| 印の規則を面ごとに足す | `rule-enforced-per-surface` | 5 か所が同じ索引・同じ関数を通ることをテストで固定（route の配線もソースで検査） |
| 再解析の採用で古い答えが残る | `stale-derivative-served` | 索引は 120 秒のキャッシュだけで保存しない（写しは書き換えない）。読み時に引き直す |
| 一覧と解決の索引の混同 | `duplicate-canonical-sources` | 別名は `alt_ids`（解決の索引）・一覧は 1 項目 |
| 結果の分岐を知らせが受け取らない | `wording-mismatch` | 分岐の語を応答に持たせ、文はサーバの 1 箇所 |
| 文と例を別の候補から選ぶ | `fallback-fabricates-missing-link` | 左辺に記号が無い式は添えない（推測で結ばない） |
| 長さの代理で読みの組を割る | `extent-decided-by-proxy-signal` | 導入と本体・式と解説を先に結ぶ（2 倍を超えたら従来どおり段落で） |
| システムが人の保存物を書き直す | `ai-decides-instead-of-human` | 書き直さない。所有者の明示操作（一括確定・`decision_context`）だけ |
| editor の権限の非対称 | `condition-not-propagated` | 音声生成もサーバで所有者判定（UI の無効化は補助） |

## 2026-10-28 追補（第 25 周 面 D 第 7 周の是正 = 第 7 波）

正本: `uxsim/runs/round25/judge_studio_r7.md` §3・§5・§6（IK-1053 / IK-1054 NEW・IK-0976 の残り・IK-0571 の面 D 側）。
オーナー判断の候補 1 件（版ありのコースの発行の手続）は推奨どおりに実装した（下の「オーナー判断」）。

- **未発行の変更の事実と版の管理への入口（IK-1053）**: 根は「届く条件（版）は運んだが、届けるための手続（発行）への
  道と、その手続が済んでいるかを保存の画面へ運んでいない」こと（last-mile-missing）。`_topic_save_facts_for` が
  受講者の見ている版（`active_release_id`）の写しと、いまの working copy（`releases.build_course_snapshot` — V層の
  写しと同じ形 `{title, description, data}`）を `lecture_studio_state.course_has_unpublished_changes` で比べる
  （中身の比較・時刻の比較はしない・どちらかが読めなければ None = 何も言わない）。`topic_save_facts(...,
  unpublished_changes=)` は版ありのとき delivery に `unpublished_changes` と事実文 `unpublished_changes_note`
  （`TOPIC_UNPUBLISHED_CHANGES_NOTES` — 「版 N の発行の後に保存した変更があります。受講者はまだ版 N を見ています。」/
  「いまの内容は版 N と同じです（未発行の変更はありません）。」）を載せ、所有者にだけ `versions_entry`
  （札「版の管理を開く」）を添える（course-structure のトップレベルにも同じ 2 キー）。原稿スタジオは届き方の文の下に
  事実文と［版の管理を開く］（`lecture-studio.open-versions`）を描き、押すと V層の `Versioning.openModal("course", …)`
  を開く（`openModal` に任意の 4 引数目 `options.onClose` を足した — 閉じたら course-structure を取り直して事実文を
  更新する。モーダルの発行の判断・操作は不変）。保存の結果の知らせにも未発行の変更の文が続く。
- **付録が古いトピックは一覧で（IK-1054）**: 知らせ `APPENDIX_STALE_COURSE_NOTICE` は「付録が古いトピックがあります
  （下の一覧）。…」で始め、題名は文の括弧に連結しない。原稿スタジオは折りたたみ（`<details>`・見出しはサーバの
  `appendix_stale_list_summary`・アンカー `lecture-studio.appendix-stale-list`）に 1 行 1 題名、左のトピック一覧と同じ
  「章.トピック」の番号で並べ、各項目は左の一覧の該当トピックを開く（同じ選択の経路を通す）。
- **2 文の導入・解説と箇条書きの直後の解説（IK-0976 の残り）**: `_is_single_sentence_unit`（1 文・160 字）を
  `_is_short_lead_unit`（`_LEAD_IN_MAX_SENTENCES = 2`・`_LEAD_IN_MAX_CHARS = 240`）に置き換え、見出しの直後の導入と
  式の直後の解説を 2 文まで本体と結ぶ。箇条書き（先頭行が `- ` / `* ` / `1. `）の直後の短い解説（2 文まで）は
  箇条書きと結ぶ。結んだ単位が目安の 2 倍を超えれば従来どおり段落で切る。境目は段落の境界のまま（表示・音声・
  readiness の 3 者は同じ関数を通るので一致は保たれ、保存済みの音声は保存・再生成まで旧い境目）。
- **⚓・吹き出しの原文の印（IK-0571 の面 D 側）**: 翻訳はしない（LLM 0 回）。判定は `text_excerpt.is_untranslated_text`
  （数式区間を除いた本文に和文の文字が無く英字の語がある）、印の正本は `label_vocab.ORIGINAL_TEXT_MARK`「（原文）」。
  ①根拠の項目（`build_topic_evidence_items`）は component / claim の表示する名前（`display_label` 優先 — 第 5 波の
  親 unit の日本語の見出し）が原文のままなら `title_original_mark`。⚓ のチップと見出しは `display_label` を優先し、
  文脈パネルは部品名（英語の内部名）で見出しを上書きしない。②部品の文脈は、位置づけ（`_narrative_role`）の優先順を
  保ったまま日本語の候補があれば先に使い、それでも原文なら `narrative_role_original_mark`、部品名には
  `label_original_mark`、主張には `original_mark`。③記号の吹き出しの使われ方の例文（`mentions[]`）にも
  `original_mark`。JS は印をサーバの値のまま描く（日本語をミラーしない）。
- **オーナー判断（版ありのコースの発行）**: 発行は所有者の明示操作のまま（V層の確定仕様）。保存の画面に置くのは
  未発行の変更の事実と［版の管理］への入口だけで、自動発行・督促・G層 To-Do（`course.unpublished_changes`）は
  作らない（To-Do は恒常点灯になりうるので運用の実測の後に判断）。
- ガードレール: `test_lecture_studio_round25_r7.py`。アンカー +2（件数の正本は `test_admin_help_ui_anchors.py`）。
  キャッシュの版 `admin-lecture-studio.js?v=…-studio7` / `versioning.js?v=…-studio7` / `app.js?v=…-studio7`。

### pre-mortem（第 7 周の是正）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 「別の画面で発行してください」と言うが道と未完了の状態が無い | `last-mile-missing` | 入口（版の管理）と未発行の事実を保存の画面に置く。発行の操作は V層のモーダルのまま |
| 未発行の判定を時刻で代用する | `completion-defined-by-proxy` | 時刻ではなく版の写しと HEAD の中身を比べる。読めなければ言わない |
| 自動発行・督促で人の判断を置き換える | `ai-decides-instead-of-human` | しない（オーナー判断）。事実と入口だけ |
| 対象の集合を挿入句に連結する | `wording-mismatch` | 項目として並べ、各項目から開ける |
| 長さの代理で読みの組を割る（閾値が 1 文） | `extent-decided-by-proxy-signal` | 2 文まで・箇条書きの直後も結ぶ。2 倍超は従来どおり段落で |
| 表示・音声・readiness の分割の食い違い | `duplicate-canonical-sources` | 規則は `_reading_blocks` の 1 箇所のまま（3 者が同じ関数） |
| 原文の言語のまま出る文を黙って出す | `information-dropped-as-unrepresentable` | 落とさず・訳さず、原文の印を付ける。日本語の既存候補を先に選ぶ |
| 印の文言を面ごとに持つ | `duplicate-canonical-sources` | 文言は `label_vocab.ORIGINAL_TEXT_MARK`、判定は `is_untranslated_text` の 1 箇所。JS は素通し |

当たらない族: 再実行と削除（保存済みデータを書き換えない — 読み時の判定と事実文だけ）。
