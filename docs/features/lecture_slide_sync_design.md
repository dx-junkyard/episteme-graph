# レクチャースライド同期 + 音声言語切替 設計書（migration 040）

> **更新（migration 047・トピック教材ベースへの転換）**: レクチャー受講の**表示ソースは
> トピックの授業用教材（`topics[].student_material`）を最優先**し、非レクチャー教材表示
> （`get_topic_material`）と一致させる。読み上げは各スライドの `spoken_script` を TTS 化し、
> **`topic_lecture_audio_cache`（`(course_id, topic_id, slide_index, voice)`）**にキャッシュする。
> 旧「実チャンク教材を持つトピックはチャンク経路を優先」（`_topic_has_linkable_material`）は
> **撤去**し、表示ソース判定は `_lecture_uses_topic_material(topic)` に一本化した。以下本文中の
> チャンク優先の記述（§1-6・§2-4・受け入れ条件の一部）はこの更新で置き換わっている。
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
- `syncSpoken`（表示⇄読み上げ同期チェック）はマーカー行を同期対象に含める。
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
- `segment_mode` の扱い: `skip` はスライドも出さない、`summary` は要約 spoken_text
  1件 = 1スライド（display は従来どおりセグメント本文の要約表示）。

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
