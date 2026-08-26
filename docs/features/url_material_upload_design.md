# URL指定による教材取得（URL Material Upload）

> **状態: 実装済み（正本）**（2026-08-25 起票・同日実装。migration は **070**
> `url_fetch_domains` で採番済み。以後は §8 実装記録のみ追記する）

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
| `backend/tests/test_admin_help_ui_anchors.py` | アンカー表とマニュアル節の整合・件数（283）・ロール fail-closed。 |
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
  `system_admin/17-admin-url-fetch-domains.md` 新設 + アンカー表6件（計 283）+
  data-model.md / layer_registry.md / docs/README.md / CLAUDE.md の更新。

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
