# episteme-graph 利用者マニュアル

本マニュアルは、episteme-graph（大学院生の学習プロセスを支援する知識グラフ管理システム）を
**利用する立場の方**に向けたドキュメントです。開発者・運用者向けの設計解説は
[docs/ のトップページ](../README.md) から各ドキュメントを参照してください。

このページ自体は人間が読むための索引です（AI アシスタントのナレッジベースには含まれません）。

---

## 構成

マニュアルはロール（audience）ごとにディレクトリで分かれています。権限は
「受講者 ⊂ 教員 ⊂ システム管理者」と**累積**するため、教員は受講者向けページも、
システム管理者は教員向けページもあわせて読むことをおすすめします。

| ディレクトリ | 対象 | 想定読者レベル | 内容 |
|---|---|---|---|
| [student/](student/) | 受講者（STUDENT）。全ロールが読める共通の土台 | 非技術（画面操作のみ） | [仕様編](student/01-specification.md)（全体像・データフロー・開示範囲・用語集・共通設計原則）、[受講者向けマニュアル](student/02-student.md)（ログイン、コース受講、AI チャット、音声会話、レクチャー、地図、再構成、論文の海、修了 など） |
| [teacher/](teacher/) | 教員（TEACHER） | 運用担当 | [教員編](teacher/03-teacher.md)（教材管理、コース構築・公開、原稿スタジオ、承認・共有、学習者ダッシュボード、ユーザー管理 など）＋ 管理画面のタブ別リファレンス（`10-admin-common.md` 以降） |
| [system_admin/](system_admin/) | システム管理者（SYSTEM_ADMIN） | 技術者 | [仕様編（運用・アーキテクチャ）](system_admin/01-operations-spec.md)（全体アーキテクチャ、ロールと権限の運用詳細）、[システム管理者編](system_admin/04-system-admin.md)（初期構築、Docker 構成、環境変数、監視、スキーマ進化の運用、セキュリティ）＋ 管理者専用タブのリファレンス（`10-admin-teachers.md` 以降） |

### 管理画面のタブ別リファレンス {#admin-tab-reference}

管理画面（`admin.html`）の「❓ 使い方」インスペクト・モードが参照するのは、以下のタブ別
リファレンスです。**操作要素 1 つにつき 1 節**という粒度で書かれています。

| ディレクトリ | ファイル | 内容 |
|---|---|---|
| teacher/ | `10-admin-common.md` | 管理画面の共通操作（ヘッダー・タブ・確認モーダル） |
| teacher/ | `11-admin-materials.md` 〜 `26-admin-graph-review.md` | 教材管理・コースビルダー・コース管理・原稿スタジオ・グループ・学生管理・分野の地図・前提の地図・ナレッジライブラリ・つまづき・関心ダッシュボード・スキーマ提案・スキーマ・要素検討・共有版・グラフレビュー |
| system_admin/ | `10-admin-teachers.md` | 教員管理（アカウント作成とアカウントライフサイクル運用） |
| system_admin/ | `11-admin-system-stats.md` 〜 `17-admin-url-fetch-domains.md` | システム統計・エラー解析・LLM使用量・マニュアル編集・discuss観測・AIモデル・URL取得の許可ドメイン |

（2026-09-03 時点のファイル構成: student/ 2 件、teacher/ 17 件、system_admin/ 10 件。
リファレンスの節と管理画面の UI 部品の対応は `backend/core/help_kb/admin_ui_anchors.py` が
正本で、対応の網羅は `backend/tests/test_admin_help_ui_anchors.py` が検証します。）

## 読み方

1. まず [student/01-specification.md](student/01-specification.md) を読み、システムの全体像・データフロー・用語を把握してください（立場を問わず共通の土台です）。
2. 次に、ご自身の立場に対応するページを読んでください。
   - 受講者の方: [student/02-student.md](student/02-student.md)
   - 教員の方: [teacher/03-teacher.md](teacher/03-teacher.md)
   - システム管理者の方: [system_admin/01-operations-spec.md](system_admin/01-operations-spec.md) → [system_admin/04-system-admin.md](system_admin/04-system-admin.md)

---

## 関連ドキュメント

- 管理画面の画面単位の詳しい操作手順: [../admin_operations/](../admin_operations/)（教員編・管理者編から必要箇所へリンクしています）
- 機能ごとの設計・動作解説（開発者向け）: [../features/](../features/)
- アーキテクチャ・デプロイ: [../architecture/overview.md](../architecture/overview.md) / [../architecture/deployment.md](../architecture/deployment.md)
