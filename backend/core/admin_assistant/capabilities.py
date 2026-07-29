"""Capability Registry — Admin Copilot の権限スコープ設計の心臓部（設計 §4）。

画面横断で **単一の真実源** として操作を宣言する。Copilot はここに登録され、かつ
現在ユーザーのロールで許可された操作のみを説明・道案内・代行する（P1 fail-closed）。

段階登録の方針: 全 admin API を一度に載せない。安全・高頻度から順に足す。
"""

from __future__ import annotations

from typing import Optional

from core.admin_assistant.schema import (
    KIND_ACTION,
    KIND_GUIDANCE_ONLY,
    ROLE_SYSTEM_ADMIN,
    ROLE_TEACHER,
    Capability,
    LocateStep,
    role_satisfies,
)

# admin.js の data-tab に一致する既知スクリーン。locate_steps の妥当性検証にも使う。
# G6 是正: knowledge-library / llm-usage は実装済みタブだが未登録で「構造的に案内不能」
# だった（vision_ux_gap_survey_2026-07.md G6）。新規タブを追加したら必ずここに足すこと。
KNOWN_SCREENS = (
    "materials",
    "course-builder",
    "course-management",
    "lecture-studio",
    "stumbles",
    "interest-dashboard",
    "schema-proposals",
    "atlas",
    "doubt-atlas",
    "groups",
    "system-stats",
    "error-analysis",
    "knowledge-library",
    "llm-usage",
    # 2026-07-29 是正: 実タブ20個のうち以下6個が capability registry にも
    # docs/admin_operations/ にも未登録で「構造的に案内不能」だった。
    "students",
    "teachers",
    "schema",
    "manual-editor",
    "discuss-observation",
    "llm-models",
)


def _step(screen: str, anchor_id: str, hint: str, precondition: str = "") -> LocateStep:
    return LocateStep(screen=screen, anchor_id=anchor_id, hint=hint, precondition=precondition)


# ---------------------------------------------------------------------------
# 登録カタログ
# ---------------------------------------------------------------------------

_REGISTRY: list[Capability] = [
    # --- 教材 (materials) ---
    Capability(
        id="materials.upload",
        screen="materials",
        title="教材（PDF / TeX）をアップロードする",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#upload",
        description="教材ファイルをアップロードして解析パイプラインに乗せる。",
        api={"method": "POST", "path": "/api/admin/materials/upload"},
        locate_steps=(
            _step("materials", "upload_dropzone", "ここに PDF / TeX をドロップ、またはクリックして選択します"),
        ),
    ),
    Capability(
        id="materials.set_visibility",
        screen="materials",
        title="教材の開示範囲を変更する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        scope="own_material",
        reversible=True,
        target_type="material",
        howto_doc="admin_operations/materials.md#visibility",
        description="教材を public / group / private に切り替える。",
        api={"method": "PUT", "path": "/api/admin/materials/{material_id}/visibility"},
        revert={"strategy": "restore_visibility"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材を選びます"),
            _step("materials", "material_visibility_control", "開示範囲を選びます", precondition="material_selected"),
        ),
    ),
    Capability(
        id="materials.delete",
        screen="materials",
        title="教材を削除する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        scope="own_material",
        reversible=False,
        confirm=True,
        target_type="material",
        howto_doc="admin_operations/materials.md#delete",
        description="教材を物理削除する（取り消し不可）。",
        api={"method": "DELETE", "path": "/api/admin/materials/{material_id}"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "削除したい教材を選びます"),
        ),
    ),
    # --- コース構築 (course-builder) ---
    # 多ターンチャットのため apply/revert を持たせず、道案内・説明に留める（P1 の段階登録）。
    Capability(
        id="course_builder.open",
        screen="course-builder",
        title="AI とコースを設計する（コース構築チャット）",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/course.md#builder",
        description="コース構築タブで AI と対話しながらコース案を作る。",
        locate_steps=(
            _step("course-builder", "cb_material_select", "コースの素材となる教材を選びます"),
            _step("course-builder", "cb_chat_input", "ここに設計したいコースの狙いを書いて送信します"),
        ),
    ),
    # --- 原稿スタジオ (lecture-studio) ---
    Capability(
        id="lecture_studio.rewrite_chunk_script",
        screen="lecture-studio",
        title="チャンクの原稿を AI で書き換える",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        # N12 是正: 既存 rewrite エンドポイント（routes/lecture_studio/scripts.py::
        # rewrite_lecture_script）は `_require_teacher` のみでコース所有チェックを持たない。
        # 代行も同一の権限意味論に合わせる（P7: 既存 API の契約を変えない）。
        scope="any",
        # N12 是正: 実 API は chunks.display_text / spoken_text / formulas を即時 UPDATE し
        # 音声キャッシュも無効化する（「保存は別操作」ではない）。したがって L1（クライアント
        # Undo）ではなく L2 永続可逆 — before スナップショットからサーバ側 revert で復元する。
        reversible=True,
        target_type="chunk",
        howto_doc="admin_operations/lecture_studio.md#rewrite-chunk",
        description="選択中チャンクの原稿を指示に沿って AI が書き換えて保存する（あとで戻せる）。",
        api={"method": "POST", "path": "/api/admin/chunks/{chunk_id}/lecture-script/rewrite"},
        revert={"strategy": "restore_chunk_script"},
        locate_steps=(
            _step("lecture-studio", "chunk_list", "対象のチャンクを選びます", precondition="chunk_selected"),
            _step("lecture-studio", "assistant_open_button", "AI アシスタントを開きます"),
            _step("lecture-studio", "ls-rewrite-prompt", "ここに書き換えの指示を入力します"),
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
    # --- コース管理 (course-management) ---
    Capability(
        id="course.set_visibility",
        screen="course-management",
        title="コースの開示範囲を変更する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        scope="own_course",
        reversible=True,
        target_type="course",
        howto_doc="admin_operations/course.md#visibility",
        description="コースを public / group / private に切り替える。",
        api={"method": "PUT", "path": "/api/admin/courses/{course_id}/visibility"},
        revert={"strategy": "restore_visibility"},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "対象のコースを選びます"),
            _step("course-management", "course_visibility_control", "開示範囲を選びます", precondition="course_selected"),
        ),
    ),
    Capability(
        id="course.publish",
        screen="course-management",
        title="コースを学生に公開する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        scope="own_course",
        reversible=False,  # 公開は学生の受講対象になるため確認ゲート（設計 §6.2）
        confirm=True,
        target_type="course",
        howto_doc="admin_operations/course.md#publish",
        description="コースをテンプレートとして公開し、学生が受講できるようにする。",
        # G1-6 是正: `PUT .../publish` は撤去済み（test_publish_endpoint_removed）。
        # 後継は visibility=public への更新（is_published は visibility から常時導出される,
        # admin.py::update_course_visibility の G1-1 是正と同一意味論）。
        api={"method": "PUT", "path": "/api/admin/courses/{course_id}/visibility",
             "body": {"visibility": "public"}},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "公開したいコースを選びます"),
            _step("course-management", "publish_button", "『公開する』を押します", precondition="course_selected"),
        ),
    ),
    Capability(
        id="course.delete",
        screen="course-management",
        title="コースを削除する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        scope="own_course",
        reversible=False,
        confirm=True,
        target_type="course",
        howto_doc="admin_operations/course.md#delete",
        description="コースを削除する（取り消し不可）。",
        api={"method": "DELETE", "path": "/api/admin/courses/{course_id}"},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "削除したいコースを選びます"),
        ),
    ),
    # --- ガイダンス層（G層）Next Steps が参照する道案内専用 capability（設計 §3.2）---
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
    # --- 分野の地図 (atlas) ---
    Capability(
        id="atlas.generate_skeleton",
        screen="atlas",
        title="分野の地図の骨格を生成する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        reversible=True,  # draft の再生成（凍結前）
        target_type="cartridge",
        howto_doc="admin_operations/atlas.md#generate",
        description="カートリッジの骨格 draft を LLM で生成する（凍結前は再生成可）。",
        api={"method": "POST", "path": "/api/admin/{cartridge_id}/atlas/skeleton/generate"},
        locate_steps=(
            _step("atlas", "atlas_generate_button", "骨格を生成するボタンを押します"),
        ),
    ),
    Capability(
        id="atlas.freeze_skeleton",
        screen="atlas",
        title="分野の地図の骨格を凍結する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        reversible=False,
        confirm=True,
        target_type="cartridge",
        howto_doc="admin_operations/atlas.md#freeze",
        description="骨格 draft を凍結して版として確定する（取り消し不可）。",
        api={"method": "POST", "path": "/api/admin/{cartridge_id}/atlas/skeleton/freeze"},
    ),
    # --- ユーザー管理 (students / teachers) ---
    # 2026-07-29 是正: 学生・教員アカウント作成フォームは #tab-groups ではなく
    # 独立タブ #tab-students / #tab-teachers にある（admin.js setupRoleBasedUI）。
    # 従来 screen="groups" だったため道案内が実在しないフォームを指していた。
    Capability(
        id="users.create_student",
        screen="students",
        title="学生アカウントを作成する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        reversible=False,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/students.md#create-student",
        description="学生アカウントを新規作成する（取り消し不可）。",
        api={"method": "POST", "path": "/api/admin/users/student"},
        locate_steps=(
            _step("students", "create_student_form", "学生アカウント作成フォームに入力します"),
        ),
    ),
    Capability(
        id="users.create_teacher",
        screen="teachers",
        title="教員アカウントを作成する",
        required_role=ROLE_SYSTEM_ADMIN,  # 教員作成は SYSTEM_ADMIN のみ
        kind=KIND_ACTION,
        reversible=False,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/teachers.md#create-teacher",
        description="教員アカウントを新規作成する（SYSTEM_ADMIN のみ・取り消し不可）。",
        api={"method": "POST", "path": "/api/admin/users/teacher"},
        locate_steps=(
            _step("teachers", "create_teacher_form", "教員アカウント作成フォームに入力します"),
        ),
    ),
    # --- グループ管理 (groups) ---
    Capability(
        id="groups.manage",
        screen="groups",
        title="グループを作成・招待する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/users.md#manage-groups",
        description="教材・コースをグループ内限定で共有するためのグループを作成し、"
                    "招待コード・直接招待でメンバーを管理する。",
        locate_steps=(
            _step("groups", "groups_create_form", "グループ名を入力して「グループを作成」を押します"),
        ),
    ),
    # --- システム (SYSTEM_ADMIN 専用・説明のみ) ---
    Capability(
        id="system.view_stats",
        screen="system-stats",
        title="システム統計を見る",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/system.md#stats",
        description="教材・処理状況などの統計を確認する。",
        api={"method": "GET", "path": "/api/admin/system/materials-stats"},
    ),
    Capability(
        id="system.view_error_logs",
        screen="error-analysis",
        title="エラーログを見る",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/system.md#error-logs",
        description="システムのエラーログを確認する。",
        api={"method": "GET", "path": "/api/admin/error-logs"},
    ),
    # -------------------------------------------------------------------
    # G6 是正（vision_ux_gap_survey_2026-07.md）: C/D/R/W/L/V/U 層・Phase B の
    # 段階登録。すべて guidance_only（DB 非変更）。捏造しない（P4）ため、UI から
    # 実際に到達できる導線だけを locate_steps にする（到達経路が無い操作は
    # locate_steps を持たせず、guidance の説明文に留める）。
    # -------------------------------------------------------------------
    # --- D層（Doubt Layer, doubt-atlas） ---
    Capability(
        id="doubt.record_verification_status",
        screen="doubt-atlas",
        title="検証状態を記帳する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/doubt.md#record-verification-status",
        description="認識的地位台帳の検証状態（未検証・間接的支持・直接検証・反証等）を記帳する。"
                    "「確定は人間」の実行手段そのもの。",
        api={"method": "PUT", "path": "/api/admin/doubt/ledger/{target_type}/{target_id}/verification-status"},
        locate_steps=(
            _step("doubt-atlas", "doubt_verification_form_button",
                  "台帳ノードの詳細ペインで「検証状態を記帳する」ボタンを押します"),
        ),
    ),
    Capability(
        id="doubt.manage_challenge",
        screen="doubt-atlas",
        title="疑義を取り下げる・検証提案にする",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/doubt.md#manage-challenge",
        description="自分が投稿した疑義を取り下げる、または検証提案へ昇格させる。",
        locate_steps=(
            _step("doubt-atlas", "doubt_challenge_withdraw_button",
                  "取り下げたい自分の疑義の「取り下げ」ボタンを押します"),
            _step("doubt-atlas", "doubt_challenge_proposal_button",
                  "検証提案にしたい疑義の「検証提案にする」ボタンを押します"),
        ),
    ),
    # --- R層（再構成ループ, lecture-studio） ---
    Capability(
        id="reconstruction.review_queue",
        screen="lecture-studio",
        title="再構成レビューキューを確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/lecture_studio.md#review-queue",
        description="再構成ループ（R層）の疑わしさランク順レビューキューを確認する。",
        api={"method": "GET", "path": "/api/admin/reconstruction/items/review-queue"},
        locate_steps=(
            _step("lecture-studio", "ls_course_select", "対象のコースを選びます"),
            _step("lecture-studio", "recon_review_button", "「再構成の確認」ボタンを押します",
                  precondition="course_selected"),
        ),
    ),
    # --- W層（要素検討ワークスペース, lecture-studio の「深く検討」モーダル） ---
    Capability(
        id="deliberation.identity_links_standardization",
        screen="lecture-studio",
        title="同一性リンクを確定する・標準化度を評価する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/lecture_studio.md#identity-links-standardization",
        description="要素検討（深く検討）モーダルで同一性リンクの候補を確定・却下する。"
                    "共通部品（shared_part）を開いた場合のみ標準化度評価も行える。",
        locate_steps=(
            _step("lecture-studio", "deliberation_open_button",
                  "対象要素の「深く検討」ボタンを押してモーダルを開きます"),
            _step("lecture-studio", "identity_link_confirm_button",
                  "「同一性リンク」セクションの確定/却下ボタンを押します",
                  precondition="deliberation_modal_open"),
        ),
    ),
    # --- L層（分野別ナレッジライブラリ, knowledge-library） ---
    Capability(
        id="library.view_and_freeze",
        screen="knowledge-library",
        title="ナレッジライブラリのエントリを参照・凍結する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/library.md#view-and-freeze",
        description="分野別ナレッジライブラリのエントリを参照し、凍結版として発行する。",
        api={"method": "POST", "path": "/api/admin/library/entries/{entry_id}/freeze"},
        locate_steps=(
            _step("knowledge-library", "library_domain_list", "分野を選び、参照したいエントリを開きます"),
            _step("knowledge-library", "library_entry_freeze_button", "「凍結（版発行）」を押します",
                  precondition="entry_selected"),
        ),
    ),
    # --- V層（共有物のバージョン管理） ---
    Capability(
        id="materials.manage_shared_version",
        screen="materials",
        title="教材の共有版を発行・削除予約する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#manage-shared-version",
        description="教材の解析成果を共有版として発行し、削除猶予を予約・取消する。",
        api={"method": "POST", "path": "/api/admin/shared/document/{object_id}/releases"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材を選びます"),
            _step("materials", "shared_version_button", "「版の管理」を押します",
                  precondition="material_selected"),
        ),
    ),
    Capability(
        id="course.manage_shared_version",
        screen="course-management",
        title="コースの共有版を発行・削除予約する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/course.md#manage-shared-version",
        description="コースを共有版として発行し、削除猶予を予約・取消する。",
        api={"method": "POST", "path": "/api/admin/shared/course/{object_id}/releases"},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "対象のコースを選びます"),
            _step("course-management", "shared_version_button", "「版の管理」を押します",
                  precondition="course_selected"),
        ),
    ),
    # --- U層（LLM トークン使用量推計） ---
    Capability(
        id="llm_usage.view_metrics",
        screen="llm-usage",
        title="LLM使用量メトリクスを確認する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/llm_usage.md#view-metrics",
        description="LLM トークン使用量（実測/推計の分離集計・dropped_events・費用）を確認する。",
        api={"method": "GET", "path": "/api/admin/llm-usage/metrics"},
        locate_steps=(
            _step("llm-usage", "llm_usage_metrics", "使用量メトリクスを確認します"),
        ),
    ),
    Capability(
        id="materials.estimate_cost",
        screen="materials",
        title="教材の解析コストを見積る",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#estimate-cost",
        description="教材の解析パイプラインが使うトークン量の目安をレンジで確認する（金額は出さない）。",
        api={"method": "GET", "path": "/api/admin/llm-usage/estimate/documents/{document_id}"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材を選びます"),
            _step("materials", "material_estimate_button", "「解析コスト見積り」を押します",
                  precondition="material_selected"),
        ),
    ),
    # --- 個人知識ネットワーク Phase B / C層（承認・共有レイヤー） ---
    Capability(
        id="interest_dashboard.bridge_insights",
        screen="interest-dashboard",
        title="橋の候補集約（Phase B）を確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/interest_dashboard.md#bridge-insights",
        description="個人知識ネットワーク Phase B の「橋」の候補集約（k-匿名）を確認する。",
        api={"method": "GET", "path": "/api/admin/courses/{course_id}/bridge-insights"},
        locate_steps=(
            _step("interest-dashboard", "interest_dashboard_course_select", "対象のコースを選びます"),
            _step("interest-dashboard", "bridge_insights_section", "橋の候補集約セクションを確認します",
                  precondition="course_selected"),
        ),
    ),
    Capability(
        id="course.sharing_dashboard",
        screen="course-management",
        title="共有ダッシュボードを確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/course.md#sharing-dashboard",
        description="コースの理論コンポーネントに紐づく説明バージョンの共有・承認・引用状況を確認する。",
        api={"method": "GET", "path": "/api/admin/courses/{course_id}/sharing-dashboard"},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "対象のコースを選びます"),
            _step("course-management", "sharing_dashboard_button", "「共有ダッシュボード」を押します",
                  precondition="course_selected"),
        ),
    ),
    # -------------------------------------------------------------------
    # N13/N31 是正（vision_ux_gap_survey_2026-07-17.md §5-5）: 図分類レビュー導線と、
    # capability 登録ゼロで Copilot から構造的に不可視だった stumbles / schema-proposals
    # の2画面を段階登録する。いずれも guidance_only（DB 非変更）。
    # -------------------------------------------------------------------
    # --- 図・画像の分類レビュー (#496, materials) ---
    Capability(
        id="materials.review_figures",
        screen="materials",
        title="図・画像の分類を確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#review-figures",
        description="教材から抽出された図・画像の AI 分類（機能構成図/データグラフ/解説画像）を"
                    "確認・確定する。AI の分類は教員が確認するまで候補のまま。",
        api={"method": "GET", "path": "/api/admin/documents/{document_id}/figures"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材を選びます"),
            _step("materials", "material_figures_button", "「図・画像」を押してモーダルを開きます",
                  precondition="material_selected"),
        ),
    ),
    # --- つまづきデータ (stumbles) ---
    Capability(
        id="stumbles.view",
        screen="stumbles",
        title="学生のつまづきデータを確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/stumbles.md#view",
        description="RAG 検索で教材から回答できなかった質問の一覧を確認し、教材補強に活用する。"
                    "claim 単位のつまづきサマリーは k-匿名集約（評価利用禁止）。",
        api={"method": "GET", "path": "/api/admin/courses/{course_id}/unanswered-queries"},
        locate_steps=(
            _step("stumbles", "stumbles_course_select", "対象のコースを選びます"),
            _step("stumbles", "stumbles_table", "つまづきデータの一覧を確認します",
                  precondition="course_selected"),
        ),
    ),
    # --- スキーマ提案 (schema-proposals) ---
    Capability(
        id="schema_proposals.review",
        screen="schema-proposals",
        title="スキーマ提案を確認・承認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/schema_proposals.md#review",
        description="AI が提案したスキーマ拡張をシミュレーションで検証し、承認・却下する。"
                    "システム全体への適用は全教材の再抽出を伴うため確認ゲートがある。",
        api={"method": "GET", "path": "/api/admin/schema-proposals"},
        locate_steps=(
            _step("schema-proposals", "sp_proposals_list", "提案の一覧を確認します"),
        ),
    ),
    # --- DSL進化分析 (schema) ---
    # 2026-07-29 是正: 「スキーマ提案」タブ（schema-proposals、シミュレーション付き）とは
    # 別に、「DSL進化分析」タブ（schema）が直接の承認・却下フロー（確認ダイアログ無し）を
    # 持つ。未登録で案内不能だったため追加する。
    Capability(
        id="schema.evolve",
        screen="schema",
        title="DSL進化分析を実行し、提案を承認・却下する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/schema.md#analyze",
        description="学生のつまづきデータから AI がスキーマ拡張（概念タイプ・述語）を提案する。"
                    "このタブの承認・却下には確認ダイアログが無い（慎重に検証したい場合は"
                    "「スキーマ提案」タブのシミュレーション経由の承認を使う）。",
        api={"method": "POST", "path": "/api/admin/schema-proposals/analyze"},
        locate_steps=(
            _step("schema", "schema_analyze_button", "「AIメタ分析を実行」ボタンを押します"),
        ),
    ),
    # -------------------------------------------------------------------
    # 利用者マニュアル KB（help_kb, manual_help_kb_design.md §4-1）: 需要側/供給側の
    # 両面計器 G層ルールが参照する capability。3件とも guidance_only（DB 非変更）。
    # 専用の管理画面はまだ無い（Phase 2 時点）ため locate_steps は持たせない
    # （§8 の流儀: 誘導先が無ければ省略可）。
    # -------------------------------------------------------------------
    Capability(
        id="manual_help.view_gaps",
        screen="interest-dashboard",
        title="受講者マニュアルの説明ギャップを確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        description="学生 HELP ルートの無ヒット・未整備節ヒットを k-匿名集計した需要側計器を確認する。",
    ),
    Capability(
        id="assistant_kb.view_undocumented",
        screen="system-stats",
        title="操作ナレッジベースの未整備箇所を確認する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        description="Admin Copilot の capability registry のうち、操作KBの説明が"
                    "整備されていないものを確認する（供給側計器）。",
    ),
    Capability(
        id="manual_kb.view_todos",
        screen="system-stats",
        title="マニュアルの未解消 TODO を確認する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        description="TODO 注記のため索引から除外されているマニュアル節を確認する。",
    ),
    # -------------------------------------------------------------------
    # 2026-07-29 是正: manual-editor / discuss-observation / llm-models は
    # 実装済み SYSTEM_ADMIN 専用タブだが capability registry 未登録だった。
    # いずれも guidance_only（DB 非変更の確認・操作は各タブの既存 UI に委ねる）。
    # -------------------------------------------------------------------
    Capability(
        id="manual_kb.edit_freeze",
        screen="manual-editor",
        title="利用者マニュアルの draft を編集し凍結配信する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/manual_editor.md#edit-and-freeze",
        description="利用者マニュアル（help_kb）の draft を編集し、検証に通ったうえで"
                    "凍結版として配信ソースを切り替える。",
        api={"method": "POST", "path": "/api/admin/help-kb/freeze"},
        locate_steps=(
            _step("manual-editor", "manual_editor_panel", "編集したいファイルを一覧から選びます"),
        ),
    ),
    Capability(
        id="discuss_observation.view",
        screen="discuss-observation",
        title="discuss 観測データを確認・ダンプする",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/discuss_observation.md#view-and-dump",
        description="「論文と話す」（discuss）モードの Phase 3 着手判断のための観測データ"
                    "（LLM利用量・grounding分布・UIイベント・参考目安）を確認し、ダンプする。",
        api={"method": "GET", "path": "/api/admin/discuss/observation-status"},
        locate_steps=(
            _step("discuss-observation", "discuss_observation_panel", "「取得」ボタンを押します"),
        ),
    ),
    Capability(
        id="llm_models.manage",
        screen="llm-models",
        title="場面別のAIモデル既定を変更する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/llm_models.md#change-default",
        description="各場面（コース構築チャット・原稿の書き換え等）の LLM 呼び出しに使う"
                    "モデルのシステム既定を確認・変更・解除する。教員個人の既定はここでは"
                    "変更できない。",
        api={"method": "PUT", "path": "/api/admin/llm-models/policies/{scene_key}"},
        locate_steps=(
            _step("llm-models", "llm_models_ops_table", "変更したい場面の「変更」ボタンを押します"),
        ),
    ),
]

_BY_ID = {cap.id: cap for cap in _REGISTRY}


# ---------------------------------------------------------------------------
# 構造的不変条項の検証（P2 / 設計 §12 のガードレール）
# ---------------------------------------------------------------------------


def validate_registry() -> None:
    """registry の不変条項を構造的に検証する（import 時 + テストで呼ぶ）。"""
    seen = set()
    for cap in _REGISTRY:
        assert cap.id and cap.id not in seen, f"duplicate/empty capability id: {cap.id}"
        seen.add(cap.id)
        assert cap.screen in KNOWN_SCREENS, f"unknown screen {cap.screen!r} in {cap.id}"
        assert cap.kind in (KIND_ACTION, KIND_GUIDANCE_ONLY), f"bad kind in {cap.id}"
        assert cap.required_role in (ROLE_TEACHER, ROLE_SYSTEM_ADMIN), f"bad required_role in {cap.id}"
        # P2: 不可逆（reversible=False）は必ず確認ゲートを持つ。
        if not cap.reversible:
            assert cap.confirm, f"reversible=False capability must set confirm=True: {cap.id}"
        # guidance_only は変更を伴わないので reversible/confirm を強制しない。
        # locate_steps の構造検証。
        for st in cap.locate_steps:
            assert st.screen in KNOWN_SCREENS, f"locate step screen {st.screen!r} unknown in {cap.id}"
            assert st.anchor_id, f"empty anchor_id in {cap.id}"
            assert st.hint, f"empty hint in {cap.id}"


validate_registry()


# ---------------------------------------------------------------------------
# 参照 API
# ---------------------------------------------------------------------------


def all_capabilities() -> list[Capability]:
    return list(_REGISTRY)


def get_capability(capability_id: str) -> Optional[Capability]:
    return _BY_ID.get(capability_id)


def capabilities_for(role: str) -> list[Capability]:
    """そのロールで到達可能な capability 集合（P1 の権限フィルタ）。"""
    return [cap for cap in _REGISTRY if role_satisfies(cap.required_role, role)]


def capabilities_for_screen(role: str, screen: str) -> list[Capability]:
    return [cap for cap in capabilities_for(role) if cap.screen == screen]


def can_access(capability_id: str, role: str) -> bool:
    cap = get_capability(capability_id)
    if cap is None:
        return False
    return role_satisfies(cap.required_role, role)
