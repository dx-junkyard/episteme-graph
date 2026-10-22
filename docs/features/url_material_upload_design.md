# URL指定による教材取得（URL Material Upload）

> **状態: 実装済み（正本）**（2026-08-25 起票・同日実装。migration は **070**
> `url_fetch_domains` で採番済み。以後は §8 実装記録のみ追記する。2026-09-30 の
> arXiv URL の書き換えとフォールバックは §10）

**正本**: 本ドキュメント。
**関連**: `docs/features/image_pipeline_knowledge_library_design.md`（`analyze_images`
オプションの意味）/ `docs/features/llm_model_selection_design.md`（M層 §7 の run 単位
`models` オプション）/ `docs/features/account_lifecycle_management_design.md`（AL1 —
`added_by` に FK を張らない理由）。
**関連 migration**: `070_url_fetch_domains.sql`。

---

## 1. 目的

教員が教材を登録するとき、これまでは PDF / TeX アーカイブを**手元にダウンロードしてから**
アップロードゾーンへ渡す必要があった。arXiv のような公開リポジトリから読む論文では、この
往復は純粋な手間である。

本層は、教材管理タブに「URLから取得」の入口を1つ足し、**サーバがそのファイルを取得して
既存のアップロードパイプラインへ流す**。取得後の挙動（チャンク化・解析パイプライン・
一覧表示・状態遷移）はファイル選択と完全に同一で、新しい教材の種類も新しい経路も作らない。

ただし「サーバが外部の URL を取得する」経路は SSRF（Server-Side Request Forgery）の入口に
なる。内部ネットワークのアドレスへ到達させられれば、外からは届かないはずのサービスを
サーバに代理で叩かせられる。したがって本層の設計の主題は取得機能そのものではなく、
**取得先をどう閉じるか**である。

---

## 2. 不変条項（UF1〜UF6）

| ID | 条項 | 意味 |
|---|---|---|
| **UF1** | **許可リストは SYSTEM_ADMIN が管理し、サーバ側で fail-closed に強制する** | 取得先は `url_fetch_domains` に登録されたドメイン（およびそのドット境界サブドメイン）だけ。参照は TEACHER 以上、**変更は SYSTEM_ADMIN のみ**。照合はサーバ側で行い、フロントの表示・無効化は補助に過ぎない（UI を迂回しても取得できない）。 |
| **UF2** | **初期状態は空 = 機能無効。migration でシードしない** | `070_url_fetch_domains.sql` は行を1つも INSERT しない。マイグレーションは**毎起動・番号順に全ファイルが再実行される**ため、初期ドメインを INSERT すると「管理者が削除した行が次の再起動で復活する」（削除が効かない = 許可リストが管理者の意思を表さなくなる）。空の状態は異常ではなく初期状態であり、UI もそう表示する。 |
| **UF3** | **SSRF ガード: 名前解決結果の全アドレスを検査し、リダイレクトは全ホップ再検証する** | `getaddrinfo` が返す**全**アドレスを private / loopback / link-local / reserved で拒否する（1つでも内部なら中止）。リダイレクトは HTTP クライアントに自動追跡させず（`allow_redirects=False`）1ホップずつ手動で辿り、**各ホップでドメイン照合と IP 検査をやり直す**（許可ドメインから内部アドレスへ 302 させる攻撃を塞ぐ唯一の方法）。最大 5 ホップ。 |
| **UF4** | **形式判定は実バイトのマジックのみ** | `%PDF` → `pdf` / gzip マジック（`\x1f\x8b`）→ `tex_archive`。URL の拡張子・`Content-Type` ヘッダは攻撃者や配信側が自由に名乗れるので信用しない。判定できないものは取り込まない。 |
| **UF5** | **取得後は既存アップロードと同一経路。既存挙動を変えない** | 取得したバイト列は `_accept_material_source`（`POST /api/admin/materials/upload` と共有）へそのまま渡す。保存先・チャンク化・解析パイプライン・レスポンス形（202）・フロントの受理後処理（`handleUploadAccepted`）はファイル選択時と同一。URL 経路のための分岐をパイプラインに足さない。 |
| **UF6** | **エラーは日本語の事実文。内部情報を漏らさない** | 拒否・失敗の理由はそのまま教員に見せる（無効化されたボタンだけを見せない）。ただし解決した IP アドレス・接続先の内部名・スタックトレースは `detail` に載せない。フロントはサーバの `detail` を素通しし、独自の推測文で上書きしない。 |

UF2 の補足: 「便利な初期値を入れる」ことと「削除が効く」ことは、毎起動再実行方式の
マイグレーションでは両立しない。本層は後者を採った。初回セットアップの手間（管理者が
`arxiv.org` を1件登録する）は、許可リストが管理者の意思の正本であることの対価である。

---

## 3. DB（migration 070）

```sql
CREATE TABLE IF NOT EXISTS url_fetch_domains (
    domain     TEXT PRIMARY KEY,
    added_by   UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `domain` は**正規化済みホスト名**（小文字・scheme / path / port なし）を主キーにする。
  正規化の正本は `core/url_fetch.py::normalize_domain`。主キーなので重複登録は自然に冪等。
- `added_by` に **FK を張らない**。登録した管理者が後に墓標化されうるため（AL1 /
  migration 068 §3.1 と同じ理由）。表示が必要なら `LEFT JOIN users` で解決する。
- **シード行なし**（UF2）。テーブル定義は `IF NOT EXISTS` で冪等。

---

## 4. API

すべて `backend/api/routes/admin.py`（実パス `/api/admin/...`）。ルータ層が担うのは
権限・監査・HTTP 写像だけで、取得の実体は `core/url_fetch.py`。

| メソッド / パス | 権限 | 挙動 |
|---|---|---|
| `GET /url-fetch-domains` | TEACHER 以上 | 許可ドメイン一覧 `{"domains":[{"domain","created_at"}]}`。教員は「どのドメインなら使えるか」を知る必要があるため参照は TEACHER 以上。 |
| `POST /url-fetch-domains` | **SYSTEM_ADMIN** | body `{"domain"}`。`normalize_domain` で正規化して保存し 201 + `{"domain": 正規化後}`。形式不正は 422。冪等。 |
| `DELETE /url-fetch-domains/{domain}` | **SYSTEM_ADMIN** | 解除。存在しなければ 404。 |
| `POST /materials/upload-from-url` | TEACHER 以上 | body `{"url", "analyze_images", "models"}` → 202。**レスポンスは既存 upload と同形**（UF5）。取得はリクエスト内同期（v1）。 |

### エラー写像（`_URL_FETCH_ERROR_STATUS`）

`core/url_fetch.py` の例外型 → HTTP ステータスの写像はルータ側の1つの表に集約する。

| 例外 | ステータス | 代表的な事実文 |
|---|---|---|
| `NoDomainsConfiguredError` | 422 | 「URLからの取得は、管理者が取得先ドメインを許可リストに登録すると利用できます」 |
| `DomainNotAllowedError` | 422 | 「このURLのドメインは許可されていません」 |
| `PrivateAddressError` | 422 | 「URL の接続先が内部アドレスのため取得できません」 |
| `UnsupportedContentError` | 422 | 「取得したファイルはPDFでもTeXアーカイブ（.tar.gz）でもありません」 |
| `TooLargeError` | 413 | 「ファイルサイズが上限を超えています」 |
| `FetchFailedError` | 502 | 「URLからの取得に失敗しました」 |

いずれの `detail` にも解決した IP・内部ホスト名を含めない（UF6）。サーバ側ログには
例外型と利用者 ID を残す（`logger.info`）。

### 監査

`AUDIT_ENTITY_URL_FETCH_DOMAIN = "url_fetch_domain"`（`core/schema.py` の
`AUDIT_ENTITY_*` カタログに登録済み）。許可ドメインの `create` / `delete` を
`services.record_review_event` 経由で `theory_review_events` に記帳する。取得そのものは
通常のアップロードと同じ扱いで、教材側の既存記録に残る（URL 取得専用のイベントは作らない）。

---

## 5. コア（`backend/core/url_fetch.py`）

FastAPI を import しない（開発ルール2）。HTTP ステータスへの写像は API 層の責務で、
ここでは `UrlFetchError` の派生型で理由を表現する。

| 関数 / 定数 | 役割 |
|---|---|
| `normalize_domain(raw)` | ドメイン文字列の正規化（小文字化・scheme / path / port 除去・末尾ドット除去・ラベル形式検査）。**保存形の正本**。 |
| `domain_allowed(host, domains)` | 完全一致または**ドット境界の**サブドメイン一致のみ許可。`arxiv.org` は `export.arxiv.org` に一致し、`evilarxiv.org` / `arxiv.org.evil.com` には一致しない（接尾辞一致の罠を塞ぐ）。 |
| `list_/add_/remove_url_fetch_domain(session, ...)` | 許可リスト CRUD。`commit` / `close` は呼び出し側（API 層）が管理する。 |
| `_is_public_address` / `_assert_public_host` | `getaddrinfo` の**全**アドレスを検査し、private / loopback / link-local / reserved を拒否（UF3）。 |
| `_validated_target(url, allowed_domains)` | scheme 検査（http / https のみ）→ 許可リスト照合 → IP 検査。**各リダイレクトホップでも呼ぶ**。 |
| `detect_source_kind(content)` | 先頭バイトのマジックから `pdf` / `tex_archive` を判定（UF4）。 |
| `derive_filename(url, kind, content_disposition)` | 保存名の決定。`Content-Disposition` を尊重しつつサニタイズする。 |
| `fetch_source_from_url(url, allowed_domains)` | 上記を束ねる公開入口。`allowed_domains` は**必須引数**で、空リストは `NoDomainsConfiguredError`（UF1/UF2 の構造的担保 — 許可リスト照合なしに取得できる公開関数を作らない）。 |
| `MAX_FETCH_BYTES` = 100MB / `FETCH_TIMEOUT_SECONDS` = 60 / `MAX_REDIRECTS` = 5 | 上限はモジュール定数（環境変数を読まない）。サイズ上限はストリーム読み出し中に強制する（`Content-Length` の自己申告を信用しない）。 |

---

## 6. UI

### 6.1 教員（教材管理タブ）

- アップロードゾーン内のリンク `#url-upload-link`（`data-ui-anchor="materials.url-upload"`、
  admin.html の静的マークアップ）。押すとモーダルが開くだけで、取得は始まらない。
- モーダルは `admin.js` が動的生成する（`#url-upload-modal` / `#url-upload-input` /
  `#url-upload-submit` / 許可ドメインの事実文 `#url-upload-domains-note`）。
- **許可リストが空のときは送信ボタンを無効化し、理由の事実文を表示する**（「URLからの取得は、
  管理者が取得先ドメインを許可すると利用できます。」）。一覧取得に失敗した場合も同様に
  無効化 + 理由（fail-closed）。無効化されたボタンだけを見せない。
- `analyze_images` チェックボックスと M層の解析モデル選択は、ファイル選択時の入力を
  そのまま引き継ぐ（URL 経路に第2の設定 UI を作らない）。
- 202 後は共通ヘルパー `handleUploadAccepted` へ合流し、以降のポーリング・一覧更新は
  既存アップロードと同一（UF5）。

### 6.2 システム管理者（AIモデルタブ）

教材管理タブは SYSTEM_ADMIN では非表示なので、許可ドメインの管理区画は運用グループの
`#tab-llm-models` パネル末尾へ `ensureUrlFetchDomainsSection()` が**冪等に** append する
（既に居れば作り直さず再読込のみ）。一覧（ドメイン / 登録日 / 削除）+ 追加フォーム +
削除 confirm（効果を明記: 「以後このドメインからのURL取得はできなくなります。」）。
空一覧のときは結果まで書いた事実文（「許可ドメインは登録されていません。登録するまで
教員はURLからの取得を利用できません。」）を出す。

### 6.3 管理UI 3点セット（CLAUDE.md の規約）

| data-ui-anchor | マニュアル節 |
|---|---|
| `materials.url-upload` | `teacher/11-admin-materials.md#url-upload` |
| `materials.url-upload-modal` | `teacher/11-admin-materials.md#url-upload-modal` |
| `materials.url-upload-submit` | `teacher/11-admin-materials.md#url-upload-submit` |
| `llm-models.url-fetch-domains` | `system_admin/17-admin-url-fetch-domains.md#url-fetch-domains` |
| `llm-models.url-fetch-domain-add` | `system_admin/17-admin-url-fetch-domains.md#url-fetch-domain-add` |
| `llm-models.url-fetch-domain-remove` | `system_admin/17-admin-url-fetch-domains.md#url-fetch-domain-remove` |

`llm-models.*` の値が `system_admin/` 配下、`materials.*` が `teacher/` 配下であることは
ロール fail-closed の規約（`resolve_admin_ui_anchors` が TEACHER に system_admin/ 節を
配信しない）。件数の正は `test_admin_help_ui_anchors.py`。

---

## 7. ガードレールテスト

| ファイル | 固定するもの |
|---|---|
| `backend/tests/test_url_fetch_core.py` | 正規化・ドット境界照合・private IP 拒否・リダイレクト全ホップ再検証・マジック判定・サイズ上限。 |
| `backend/tests/test_url_fetch_api.py` | 権限（参照 = TEACHER 以上 / 変更 = SYSTEM_ADMIN）・エラー写像（422 / 413 / 502）・**内部アドレス拒否時に内部情報を漏らさないこと**（UF6）・202 が既存 upload の経路へ合流すること・`analyze_images` / `models` の受け渡し・監査記帳。 |
| `backend/tests/test_url_fetch_guardrails.py` | `core/url_fetch.py` が FastAPI / HTTPException / 環境変数に触れないこと・`fetch_source_from_url` が `allowed_domains` を必須引数に取り HTTP を行う公開入口が1つだけであること（UF1）・migration にシード INSERT と破壊的 DDL が無いこと（UF2）・リダイレクト自動追跡の無効化とホップ / サイズ / タイムアウト上限の存在（UF3）。 |
| `backend/tests/test_url_fetch_ui_static.py` | 6アンカーの担体実在・モーダル要素 id・**許可リスト空時の無効化と理由の事実文**・`handleUploadAccepted` が `uploadFile` と `submitUrlUpload` の両方から呼ばれること（合流の単一性）・`ensureUrlFetchDomainsSection` が `tab-llm-models` を参照すること。 |
| `backend/tests/test_admin_help_ui_anchors.py` | アンカー表とマニュアル節の整合・件数（**現行値の正本は同テスト**）・ロール fail-closed。 |
| `backend/tests/test_docs_registry_guardrails.py` | migration 070 が data-model.md / layer_registry.md §3 に現れること・本設計書が索引から参照されていること。 |

---

## 8. 実装記録（2026-08-25）

v1 を同日中に実装した。

- **DB**: `backend/db/070_url_fetch_domains.sql`（シードなし・冪等）。
- **コア**: `backend/core/url_fetch.py` 新設。`core/schema.py` に
  `AUDIT_ENTITY_URL_FETCH_DOMAIN` を追加（監査語彙カタログ）。
- **API**: `routes/admin.py` に「URL指定による教材取得」節を追加。取得したバイト列は既存の
  `_accept_material_source` へ渡すのみで、パイプライン側は非改変（UF5）。
- **フロント**: `admin.html` にリンク1本、`admin.js` にモーダルと SYSTEM_ADMIN 向け
  許可ドメイン区画。受理後は既存の `handleUploadAccepted` に合流。
- **ドキュメント**: 本設計書 + `teacher/11-admin-materials.md` に3節 +
  `system_admin/17-admin-url-fetch-domains.md` 新設 + アンカー表に6件追加（総数の正本は
  `test_admin_help_ui_anchors.py`）+ data-model.md / layer_registry.md / docs/README.md /
  CLAUDE.md の更新。

未実施: docker 実機での E2E 検証（arXiv からの実取得）。

---

## 9. 非スコープ（v1）

- **arXiv API によるメタデータ取得**: URL から著者・タイトル・abstract を引いて教材メタデータを
  自動補完すること。取得は「ファイルを取ってくる」までに留め、arXiv 固有の API 依存を作らない。
- **一括取得**: 複数 URL・文献リストからのまとめ取り込み。1回の操作 = 1教材のまま。
- **学習者向けの URL 取得**: 教材の登録は教員の権限であり、学習者に開く予定はない。
- **Admin Copilot の capability 登録**: 道案内・操作代行の対象にしない（capability registry は
  安全・高頻度から段階登録する方針。取得先の設定は SYSTEM_ADMIN の低頻度操作）。
- **非同期取得（バックグラウンドジョブ化）**: v1 はリクエスト内同期。大きなファイルで
  タイムアウトが問題になるなら別途検討する。
- **取得先ごとの取得回数・帯域の上限**: 許可リスト自体が主要な制御で、レート制限は設けない。

## 10. 追補 — arXiv URL の書き換えとフォールバック（2026-09-30・migration / env なし）

> **状態: 実装済み**。正本の詳細は `paper_radar_design.md` §15.9。

- `UploadFromUrlRequest.source_format`（`tex` / `pdf`・空は指定なし・語彙外 422）と
  `language`（`ja` / `en`）を追加。
- URL が arXiv の論文 URL（ホスト `arxiv.org` / `www.arxiv.org` / `export.arxiv.org`・パス
  `abs` / `pdf` / `src` / `e-print`）のときだけ、`api/source_resolution.fetch_url_source` が
  形式を「明示の `source_format` > URL のパス > 既定（TeX）」で決め、同じ論文の `/src/` か
  `/pdf/` を取り直す。TeX が読めなければ同じリクエストの中で `/pdf/` へ倒す。
  **呼び出し予算は 1 件あたり ≤ 2 回**（429 では 2 回目を取らない）。
- arXiv 以外の URL は従来どおり 1 回そのまま取る（UF1〜UF6 は非改変。取得は引き続き
  `fetch_source_from_url` の唯一の公開関数 — 許可リスト照合・各ホップの再検証・マジック判定）。
- `url_fetch` の公開面に `RateLimitedError`（HTTP 429・`FetchFailedError` の部分型）を追加。
  写像は 502（上流の制限を本アプリのコスト上限 429 と同じ形にしない）。
- `documents.source_url` は実際にバイト列を返した URL。レスポンスに `effective_format` /
  `fell_back` を足した（アップロード API と同形のキーは不変・追加のみ）。
- **ホストを保つ（2026-10-01 是正 m10）**: 書き換えるのは**パスだけ**で、ホストは貼られた URL の
  ものを保つ（`export.arxiv.org/abs/<id>` → `export.arxiv.org/src/<id>`、フォールバックも
  `export.arxiv.org/pdf/<id>`）。許可リストが `export.arxiv.org` だけのとき、元の URL なら通るのに
  書き換えで不許可になる食い違いを作らない。
- **429 停止規則（2026-10-01 是正 M6）**: 429 を受けたら同じ操作の中で arXiv を再び呼ばない
  （URL 指定取得は 1 件なので 2 回目を取らないだけ。複数件の `/ingest`・キューの扱いは
  `paper_radar_design.md` §15.9 のレビュー是正）。
- UI: モーダルは arXiv の URL を入力したときだけ「取得する形式」を出す
  （アンカー `materials.url-upload-format`）。生成する言語はアップロード欄の選択を引き継ぐ。

## 11. 追補 — TeX 取り込みの平文化の是正（2026-10-16・migration / env / API なし）

> **状態: 実装済み**。TeX 取り込み（`core/document_pipeline/tex_archive.py`）の先行の設計記録は
> `paper_radar_design.md` §15.4 だが、同文書は並行作業中のため本節に記録する。課題は
> IK-0696 / IK-0697 / IK-0698（論文レーダー自律周回 1 周目で役割判定の入力を読んで見つかった）。

- **参照を空文字にしない（IK-0697）**: 地の文の `\ref` / `\eqref` / `\cref` / `\autoref` は読める代用に置く。
  ラベル → 印字番号の対応は取り込み時に `_build_label_map` が本文から決定論で数える（式: 番号付き環境の
  行単位・`\nonumber` / `\notag` / `\tag` / subequations の 4a・4b、節: section 系・`\appendix` 以降は A・B、
  図・表: `\caption` の出現順）。`\numberwithin` / `\theequation` の再定義 / カウンタの直接設定 / 付録の式は
  **数えずラベルを残す**（違う番号を出すより正直）。表示は「式 (3)」「§2」「図 1」「表 2」、直前の語が
  「Eq.」「Section」「Fig.」「Table」なら番号だけ（「Eq. (3)」）、`Eq.~(\ref{…})` は括弧を二重にしない。
  `\cite` 系は固定の目印を 1 つ（著者・年は作らない。目印は §11.1 で `[ref]` に変更）。数えられた式番号は式ブロックの
  `raw.printed_numbers` にも載る（IK-0681 の表示名への配線は未着手）。
- **環境の残骸を本文ブロックにしない（IK-0696）**: 数式・図表以外の環境は開始・終了の印と幅・列指定だけを
  落として中身を残す。`tabular` 系は「セル | セル」の行の並びの表本体（`raw.in_table` — GROBID 経路と同じ形で、
  役割判定・主張採否の母集合から外れる。`table` 環境の中の表本体も同様に残す）、`itemize` / `enumerate` /
  `description` は 1 項目 1 行の 1 ブロック（`raw.container_type="list"`）。前付け（`\title` / `\author` /
  `\affiliation` / `\email` / ORCID 等のコマンド、最初の節より前の ORCID・連絡先を含む段落・center 環境）は
  `block_type="unknown"` + `raw.front_matter` で残す。`\\` / `\\[10pt]` / `\vspace*` 等のレイアウト残骸は語の
  区切りにし、記号・句読点だけの断片はブロックにしない。件数は `metadata.structure_recovery`
  （`table_bodies` / `lists` / `front_matter_blocks` / `punctuation_only_fragments` / `footnotes`）に残す。
- **脚注を本文の語へ密着させない（IK-0698）**: `\footnote{…}` は「（脚注: …）」で区切る（中の数式も保つ）。
  「centre → node」の化けは `tex_archive.py` の置換規則からは起きないことを確かめた（素の語は書き換えない・
  論文のマクロは制御綴だけを展開する）。原因の特定には当該論文の TeX ソースが要り、外部取得をしない方針の
  ため未確定のまま課題に残す。
- テスト: `test_latex_display.py::TestTexIngestKeepsReferencesAndFootnotes` / `TestTexLabelMap` /
  `test_document_pipeline.py::test_tex_archive_keeps_environment_content_without_residue_blocks`。

### 11.1 実論文での再演と追加の是正（2026-10-16・2610.08908）

論文レーダー自律周回で TeX 経路の 2610.08908（赤方偏移空間の銀河トライスペクトル）を読んだ頭脳が、要旨の欠落・
`$\begin{eqnarray}$` に包まれて `\partial` 等が剥がれた式・段落見出しの混入・`subequations` の残骸・空の参照を報告した。
取り込み済みの tarball を MinIO から読み出して `build_structure_from_tex_archive` を直接通し（LLM・DB なし）、切り分けた。

- **切り分け**: この論文は是正後のコードで取り込まれていた（`\cite` は当時の目印 `[引用]`）。「Eq. to any」「In we show」
  「derived in and」は PN 論文（古い `document_structure` artifact を resume で再利用）由来で、現行コードに PN 論文の
  tarball を通すと残らない。トライスペクトル論文の崩れは別の原因だった。
- **環境の別名マクロ**: `\newcommand{\be}{\begin{equation}}` / `\beqa` / `\bseq` を地の文の数式マクロとして展開して
  いたため、式の中身が平文化されていた。置き換え先が環境の印か節見出し 1 つだけの引数なしマクロを、切り出しの前に
  本文で戻す（`_expand_structural_aliases`。数式の数え上げ `tex_equation_inventory` も同じ版を見る）。`flalign` /
  `alignat` も式ブロックにする。
- **同梱 .sty**: `\usepackage{style}` の `style.sty` が tar にあれば前付けの位置に読み込む（マクロ・番号付け・
  `\acknowledgments` の定義がここにある）。本文の候補にはしない。
- **要旨**: JHEP 系の `\abstract{…}`（前付けのコマンド形）や前付けの abstract 環境を、`\maketitle` の直後（最初の
  節より前）の本文ブロックに置き `raw.abstract` を付ける（骨格の「要旨」欄が導入の段落にならない）。
- **節番号**: `Section.section_number`（「2.1」・付録は「A」）/ `parent_section_id` / `is_appendix` を `\appendix`
  から決定論で付ける（星付きの節は番号なし）。節の一覧に subsection が無いのは paper_skeleton が level-1 だけを
  候補にする設計で、TeX 経路の欠落ではない。頁は TeX からは分からないので従来どおり 1（推測しない）。
- **段落見出し**: `\paragraph{…}` は本文の頭に混ぜず `subsection_heading` + `raw.paragraph_heading` のブロックにする
  （節は作らない）。
- **後付け**: 謝辞（`\section*{Acknowledgments}` / `\paragraph{Acknowledgments}` / `acknowledgments` 環境 /
  `\acknowledgments`）は次の節・段落見出し・`\appendix` までを `block_type="unknown"` + `raw.back_matter`、
  `thebibliography` は `\bibitem` ごとの `reference_entry` にする（役割判定の母集合に入れない）。
- **式番号**: `\numberwithin{equation}{section}`（と `\@addtoreset{equation}{section}`・
  `\theequation` を `\thesection.\arabic{equation}` にする再定義）は「節.通し番号」（付録「A.1」）で数える。前付け
  （同梱 .sty を含む）の宣言も見る。それ以外の再定義は従来どおり数えない。「Sec.\,\ref」の細い空白も語の区切りとし、
  直前の語が別の種類を言う参照（「Appendix \ref{…}」が組版上は式を指す）は番号だけを置く。
- **式のつなぎ語**: 数式ブロック直前の 3 語以下・文末記号なしの断片（「with」「as follows」）は `unknown` +
  `raw.equation_connective`（落とさない）。
- 件数は `structure_recovery` の `back_matter_blocks` / `paragraph_headings` / `equation_connectives`。
- **引用の目印（F0 seq 1218）**: 英語の本文に日本語の `[引用]` を混ぜない。目印は言語に依らない `[ref]`、
  `\citet` 系と文頭の引用は主語として読める既定の語「the authors of [ref]」（文頭は大文字）、前置詞の後と
  `\citeyear` は `[ref]` のみ。著者名・年は作らない。主語の位置は段落全体で判断する（数式で区切った断片では
  判断しない）。
- **参照の前置きの言語（G2 seq 1340）**: 直前に `Eq.` / `Eqn.` / `Sec.` / `Fig.` / `Tab.` / `Appendix` 等の語があれば番号だけ、
  無ければ段落に日本語の文字（かな・漢字。脚注の区切りの目印は除く）が無いとき英語の前置き（`Eq. (5.5)` /
  `Sec. 2` / `Fig. 1` / `Tab. 1`）、日本語の段落のときだけ「式 (5.5)」「§2」「図 1」「表 2」。TeX の引用符
  ````…''`` / ```…'`` は `"…"` / `'…'` にする。
- **色の引数（F2 seq 1172）**: `\textcolor{blue}{blue}` は色の引数を落として本文だけ残す（`blueblue` にしない）。
  `\colorbox` / `\color{…}` も同様。
- **図表ラベルの二重定義**: 同じ図表の中で caption の前後に同じラベルを書いた場合は、LaTeX と同じく後の定義
  （caption の後 = 表の番号）を採る（「Tab. tab:priors」→「Tab. 1」）。
- **脚注の中の表示式（G1 seq 1297・2610.20589）**: 脚注に `\begin{equation}` / `\[…\]` / `$$…$$` があると切り出しが
  脚注を割り、脚注の括弧が崩れて後ろの表示式が前の段落に連結していた（「reducing toAnother advantage」・
  「operator.To get」）。番号付けは脚注の中の式も数えたうえで、切り出しでは脚注の中の表示式をインライン数式に
  する（`_inline_footnote_display_math`）。残る「projection.Such」「parameters.The」は TeX 原文どおり（著者の空白欠落）。
  語と参照の密着（`Eq.\eqref{…}`）は空白を 1 つ挟む（「Eq. (3.1)」）。
- **段落見出しのラベル（G1 seq 1243）**: `\paragraph{…}\label{app:x}` は囲んでいる節の番号を採る（「Appendix A」。
  LaTeX の組版上は直前の式の番号になるが、著者の意図を採る）。番号の付かない式の行（`\nonumber` / 星付き）の
  ラベルは参照先を進めない。
- **アクセント記法（I2 seq 1658）**: `Schr\"odinger` / `\'e` / `\`a` / `\^o` / `\~n` / `\c{c}` / `\H{o}` / `\ss` /
  `\o` 等を Unicode（NFC）にする（`_tex_accents`。本文・節見出し・題名・著者・参考文献の全経路。数式の中は対象外）。
- **ラベルのキーの空白・マクロ展開の語境界（J0）**: `\label{eq: tri-def}` / `\ref{eq: tri-def}` のキーは空白を除いて
  正規化する（`eq:tri-def` → 式 ID `eq_tri-def`。以前は検証で落ちて `eq_tex_bNN`）。マクロ展開で制御語と英字が
  詰まらないよう空白を 1 つ挟む（`\to\gsc` → `\to G_\text{scalar}`）。
- **式の意味づけの文脈（J0 / J1・`equation_semantics/input_builder.py`）**: 前後文脈は同じ節の中だけ（付録の式の前に結論を
  入れない）。抜粋は `context_excerpt` が `$…$` と語の途中で切らずに作り、前文脈は式に近い末尾側を残す（LLM 回数は不変）。
- **「node of inertia」（J0）**: 2610.20589 の TeX 原文の語（l.443 ほか「(i.e., node of inertia)」）で、取り込みの化けではない。
- **再演で再現しなかった所見**: 「Thus a genera」の途中切れと itemize / enumerate の環境名の残留は、現行コードに
  2610.20589 の tarball を通すと起きない（段落は式の直前の語で終わり、環境名は残らない）。古い artifact 由来。
- **参照の範囲・列挙（O0・2610.19001）**: `Appendices~\ref{B}--\ref{F}` / `Figs.~\ref{a}, \ref{b} and \ref{c}` の 2 つ目以降は、先に並んだ参照と継ぎ目（`--` / `-` / `,` / `and` / `or` / `to`）を剥がして連なりの先頭の語で判断し、先頭の語がある限り番号だけを置く（`_strip_ref_chain`。「B--Sec. F」にしない。先頭の語が無い並びは従来どおり各参照に前置き）。
- **役割判定の入力の本文上限（O0 seq 2220）**: 語の途中で切っていたのは取り込みではなく `rhetorical_role/input_builder.py` の 1,800 字の先頭切り。文境界（. ! ?・略語と数式の中は除く）が上限の半分より後ろにあればそこで、無ければ語境界（`context_excerpt`）で切って「…」を付け、切った block の ID を coverage `details.truncated_text_block_ids` に残す（LLM 回数・母集合は不変）。
- テスト: `test_document_pipeline.py` の `test_tex_*`（論文の形を写した小さな TeX 断片・tarball はリポジトリに入れない）/
  `test_latex_display.py::TestTexLabelMap::test_section_wise_equation_numbering_is_counted`。
