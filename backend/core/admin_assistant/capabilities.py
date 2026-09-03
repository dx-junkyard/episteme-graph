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
            # 2026-09-03 是正: 行操作は「⋯」メニューに畳まれており、開くまでメニュー項目は
            # DOM に存在しない（admin.js の materialPipelineMenuHtml / material-more-panel）。
            # materials.paper_radar と同じく、まずメニューのトリガーを点灯する（P8: 誘導まで）。
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_share_button", "「共有設定…」を押します",
                  precondition="material_menu_open"),
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
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_delete_button", "メニュー末尾の「削除…」を押します",
                  precondition="material_menu_open"),
        ),
    ),
    # URL 指定による教材取得（url_material_upload_design.md UF1〜UF6）: 取得先は
    # システム管理者が許可リストに登録したドメインのみ（初期状態は空 = 機能無効）。
    # 取得後は既存アップロードとまったく同じ経路に合流するため、代行は登録せず道案内に留める。
    Capability(
        id="materials.url_upload",
        screen="materials",
        title="URL を指定して教材を取得する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#url-upload",
        description="論文の URL（PDF または TeX の .tar.gz）を指定してサーバに取得させ、"
                    "ファイルをアップロードした場合と同じ解析パイプラインに乗せる。"
                    "取得できるのは管理者が許可リストに登録したドメインのみ。",
        api={"method": "POST", "path": "/api/admin/materials/upload-from-url"},
        locate_steps=(
            _step("materials", "url_upload_button",
                  "アップロード領域の中にある「URLから取得（arXiv など）」を押します"),
        ),
    ),
    # 教材行の「⋯」メニュー自体の説明（admin_ux_issues_2026-08-01.md §2.3 の2層化）。
    # 行に常時出ているのは「パイプラインを実行 ▼」と「⋯」だけなので、
    # 「行にボタンが見当たらない」という問い合わせの受け皿として登録する。
    Capability(
        id="materials.row_actions_menu",
        screen="materials",
        title="教材行の「⋯」メニューから操作を探す",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#row-menu",
        description="教材一覧の行に常に出ているのは「パイプラインを実行 ▼」と「⋯」の 2 つだけ。"
                    "図・画像 / 検出要素 / グラフレビュー / 共有設定 / 版の管理 / 位置づけ / "
                    "近い論文を探す / 解析コスト見積り / 削除 などは「⋯」メニューの中にある。",
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材の行を選びます"),
            _step("materials", "material_row_menu", "行の「⋯」を押すと操作の一覧が開きます",
                  precondition="material_selected"),
        ),
    ),
    # グラフ対話レビュー（graph_dialogue_review_design.md GR1）: 承認・却下は常に人間で、
    # AI との対話から承認 API を呼ぶ経路は存在しない。代行は登録せず道案内に留める。
    Capability(
        id="materials.graph_review",
        screen="materials",
        title="グラフを見ながら論理要素・主張を確認・承認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#graph-review",
        description="理論操作グラフを見取り図に、未レビューのノードをたどりながら AI と対話し、"
                    "論理要素（component）と根拠の主張（claim）を承認・却下する。"
                    "閲覧権限だけの場合は表示と対話まで（承認・却下には編集権限が必要）。",
        api={"method": "POST", "path": "/api/admin/deliberation/documents/{document_id}/graph-sessions"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材の行を選びます"),
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_graph_review_button", "「🕸 グラフレビュー…」を押します",
                  precondition="material_menu_open"),
        ),
    ),
    # 知識ランドスケープ（knowledge_landscape_design.md LS2）: AI 配置は inferred 止まりで、
    # 確定は教員の操作だけ。代行は登録せず道案内に留める。
    Capability(
        id="materials.review_landscape",
        screen="materials",
        title="論文の位置づけ（分野マップ）を確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#landscape",
        description="この論文が分野マップのどのアンカーに位置づくかの AI 候補を分野ごとに確認し、"
                    "[確認][却下][再検討] で状態を変える。AI が出したものは「AIによる推定（未確認）」の"
                    "ままで、確定は教員の操作だけ。どこにも置けなかった分野は理由付きで表示される。",
        api={"method": "GET", "path": "/api/admin/landscape/documents/{document_ref}/placements"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材の行を選びます"),
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_landscape_button", "「位置づけ（分野マップ）…」を押します",
                  precondition="material_menu_open"),
        ),
    ),
    # 論文ディスカバリー（paper_discovery_design.md §4.4）: 「arXivから探す」の道案内。
    # 取り込み実行（POST /api/admin/discovery/ingest）の代行 capability は登録しない —
    # LLM コストを伴う操作を Copilot の代行に載せない判断（PD1: 承認は教員の明示操作）。
    Capability(
        id="materials.arxiv_discovery",
        screen="materials",
        title="arXiv から論文を探して取り込む",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#arxiv-discovery",
        description="分野の条件（arXiv カテゴリ・キーフレーズ）で arXiv を検索し、"
                    "候補の一覧から選んだ論文だけを教材として取り込む。"
                    "候補を並べるところまでが自動で、取り込みは教員の明示操作のみ。",
        api={"method": "POST", "path": "/api/admin/discovery/search"},
        locate_steps=(
            _step("materials", "paper_discovery_button",
                  "アップロード領域の中にある「arXivから探す」を押します"),
        ),
    ),
    # 論文レーダー（paper_radar_design.md §4.2）: 教材1件を起点にした類似論文探索の道案内。
    # 比較分析（POST /api/admin/discovery/radar/compare）・取り込みの代行 capability は
    # 登録しない — arxiv_discovery と同じ判断（LLM コストを伴う操作を Copilot 代行に
    # 載せない / 取り込みの弁は教員の明示操作のまま。PR3/PR5）。
    Capability(
        id="materials.paper_radar",
        screen="materials",
        title="この論文に近い論文を探す（論文レーダー）",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/materials.md#paper-radar",
        description="教材（論文）を起点に、距離（近い / 中間 / 同じ分野の別テーマ）を選んで "
                    "arXiv から類似論文の候補を並べる。必要なら候補との違いを AI に分析させ、"
                    "取り込みは既存の取り込み操作（教員の明示操作）で行う。",
        api={"method": "POST", "path": "/api/admin/discovery/radar/search"},
        locate_steps=(
            _step("materials", "paper_radar_row_menu",
                  "起点にしたい教材の行の「⋯」メニューから「近い論文を探す…」を開けます"),
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
        # 2026-09-03 是正: コース管理タブにコース削除の UI は無い。削除ボタンは
        # 「コース構築」タブの「既存コースを読込」モーダル内の行にしかないため、
        # screen / locate_steps とも到達可能な経路（course-builder）に合わせる。
        screen="course-builder",
        title="コースを削除する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        # 2026-09-03 是正: 実 API（routes/admin.py::delete_course）のゲートは
        # `user_can_edit_course` = 所有者 **または** editor 権限グループのメンバー。
        # scope="own_course" は実際より狭い宣言だった。
        # 注: routes/admin_assistant.py が実際に enforce するのは "own_course" のみで、
        # この capability には action handler が無い（代行は 501）。宣言が緩む分を
        # 埋めるため、ガードレール（test_admin_assistant.py）で
        # 「未 enforce の scope を持つ action capability に handler が無いこと」を固定する。
        scope="editable_course",
        reversible=False,
        confirm=True,
        target_type="course",
        howto_doc="admin_operations/course.md#delete",
        description="コースを削除する（取り消し不可）。削除ボタンはコース管理タブではなく"
                    "「コース構築」タブの「既存コースを読込」モーダルの中にある。",
        api={"method": "DELETE", "path": "/api/admin/courses/{course_id}"},
        locate_steps=(
            _step("course-builder", "import_course_button",
                  "チャット欄の上にある「既存コースを読込」を押します"),
            _step("course-builder", "course_delete_button",
                  "一覧から対象のコースの行の「削除」を押します（確認でコース名の入力が必要です）",
                  precondition="import_course_modal_open"),
        ),
    ),
    # リリース前の確認（release_review_flow_design.md RR1〜RR7）: 学習マップの割り当て →
    # 論文の位置づけ → 公開 の 3 ステップウィザード。各ステップに「あとで」があり、
    # 飛ばしても学習者側の表示は変わらない（リリースを止めない, RR7）。
    Capability(
        id="course.release_review",
        screen="course-management",
        title="リリース前に確認して公開する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/course.md#release-review",
        description="コースの「確認して公開」から、学習マップの割り当て・論文の位置づけ・公開を"
                    "3 ステップで確認する。「次へ」を押すと、表示されている内容を確認したものとして"
                    "記録される。各ステップは「あとで」で飛ばせる。",
        api={"method": "POST", "path": "/api/admin/landscape/courses/{course_id}/placements/accept"},
        locate_steps=(
            _step("course-management", "course_row:{course_id}", "対象のコースを選びます"),
            _step("course-management", "release_review_button", "「確認して公開」を押します",
                  precondition="course_selected"),
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
        # 2026-09-03 是正: 実パスは `/api/admin/cartridges/{cartridge_id}/...`
        # （routes/atlas.py の APIRouter(prefix="/cartridges") + main.py の "/api/admin"）。
        api={"method": "POST", "path": "/api/admin/cartridges/{cartridge_id}/atlas/skeleton/generate"},
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
        # 2026-09-03 是正: generate と同じく `cartridges/` プレフィックスが必要。
        api={"method": "POST", "path": "/api/admin/cartridges/{cartridge_id}/atlas/skeleton/freeze"},
    ),
    # 分野の地図のレビューキュー（修正報告 / 論文の解析から見つかった候補 / 関係（辺）の候補）。
    # いずれも教員の採用操作までは骨格に反映されない（KN-3/AB4: AI・サーバは draft を書かない）。
    Capability(
        id="atlas.review_reports",
        screen="atlas",
        title="修正報告と候補をレビューする",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/atlas.md#review-queue",
        description="分野の地図の「修正報告のレビュー」区画で、修正報告・論文の解析から見つかった候補・"
                    "関係（辺）の候補を採用/見送りする。採用したものは「下書きへ反映」で次版に取り込む。"
                    "行を消す操作は無く、見送りも記録として残る。",
        api={"method": "GET", "path": "/api/admin/cartridges/{cartridge_id}/atlas/reports"},
        locate_steps=(
            _step("atlas", "atlas_reports_section", "「修正報告のレビュー」区画を確認します"),
        ),
    ),
    # VA層（atlas_vector_anchoring_design.md）: ベクトル索引と別名レジストリ。
    # 別名は status 遷移のみで削除 API を持たない（VA6）。
    Capability(
        id="atlas.vector_index_aliases",
        screen="atlas",
        title="ベクトル索引を再構築し、別名を登録する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/atlas.md#vector-aliases",
        description="公開中の骨格の各項目に論文と同じ空間での索引を作り（「索引を再構築」）、"
                    "同じものを指す別の表記を別名として登録する。別名は索引と論文検索の"
                    "キーフレーズに使われる。「見送り」は行を消す操作ではない。",
        api={"method": "POST", "path": "/api/admin/cartridges/{cartridge_id}/atlas/vectors/refresh"},
        locate_steps=(
            _step("atlas", "atlas_vectors_refresh_button", "「索引を再構築」を押します"),
            _step("atlas", "atlas_aliases_section", "「登録済みの別名」区画で別名を登録・確認します"),
        ),
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
    # --- アカウントライフサイクル管理 ---
    # 正本: docs/features/account_lifecycle_management_design.md §9.4。
    # `required_role` は 1 値なので **対象ロール別に capability を分割する**。
    # `users.suspend` 1 本に required_role=TEACHER を付けると Copilot が TEACHER に
    # 教員停止の手順まで案内してしまい P1 違反になる。
    # v1 は action tool（actions/ の capture_before/apply/revert）を作らず、
    # guidance / locate 用の宣言に留める（users.create_teacher と同じ構え）。
    Capability(
        id="users.list",
        screen="students",
        title="アカウントを一覧する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/users.md#list-users",
        description="学生アカウントの一覧・状態・最終ログインを確認する（教員は学生のみ）。",
        api={"method": "GET", "path": "/api/admin/users"},
        locate_steps=(
            _step("students", "user_list", "アカウント一覧です。状態と最終ログインを確認できます"),
        ),
    ),
    Capability(
        id="users.suspend_student",
        screen="students",
        title="学生アカウントを停止する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        reversible=True,   # 「再開」で元に戻せる（L2 永続可逆）
        confirm=True,      # 本人がログインできなくなるため理由入力 + 確認を要求する
        target_type="user",
        howto_doc="admin_operations/users.md#suspend-student",
        description="学生アカウントのログインを停止する（理由必須・再開可能）。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/suspend"},
        revert={"strategy": "restore_user"},
        locate_steps=(
            _step("students", "user_list", "対象の学生を一覧から選びます"),
            _step("students", "user_suspend_button", "「停止…」を押して理由を入力します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.restore_student",
        screen="students",
        title="学生アカウントの停止を解除する",
        required_role=ROLE_TEACHER,
        kind=KIND_ACTION,
        reversible=True,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#restore-student",
        description="停止中の学生アカウントを再開する。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/restore"},
        revert={"strategy": "suspend_user"},
        locate_steps=(
            _step("students", "user_list", "停止中の学生を一覧から選びます"),
            _step("students", "user_restore_button", "「再開」を押します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.suspend_teacher",
        screen="teachers",
        title="教員アカウントを停止する",
        required_role=ROLE_SYSTEM_ADMIN,  # 教員・管理者への操作は SYSTEM_ADMIN のみ
        kind=KIND_ACTION,
        reversible=True,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#suspend-teacher",
        description="教員アカウントのログインを停止する（所有教材・コースの共有と受講は継続する）。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/suspend"},
        revert={"strategy": "restore_user"},
        locate_steps=(
            _step("teachers", "user_list", "対象の教員を一覧から選びます"),
            _step("teachers", "user_suspend_button", "「停止…」を押して理由を入力します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.restore_teacher",
        screen="teachers",
        title="教員アカウントの停止を解除する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_ACTION,
        reversible=True,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#restore-teacher",
        description="停止中の教員アカウントを再開する。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/restore"},
        revert={"strategy": "suspend_user"},
        locate_steps=(
            _step("teachers", "user_list", "停止中の教員を一覧から選びます"),
            _step("teachers", "user_restore_button", "「再開」を押します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.password_reset",
        screen="teachers",
        title="パスワードを再設定する",
        required_role=ROLE_SYSTEM_ADMIN,  # 対象が学生でも SYSTEM_ADMIN のみ（§14-1 裁定）
        kind=KIND_ACTION,
        reversible=False,  # 旧パスワードは復元できない（発行済みトークンも即時失効する）
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#password-reset",
        description="アカウントのパスワードを管理者が再設定する（取り消し不可・既存ログインは失効）。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/password-reset"},
        locate_steps=(
            _step("teachers", "user_list", "対象のアカウントを一覧から選びます"),
            _step("teachers", "user_reset_button", "「パスワード再設定…」を押します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.schedule_deletion",
        screen="teachers",
        title="アカウントの削除を予約する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_ACTION,
        # 予約自体は取消可能だが、猶予経過後の outcome が破壊的なため reversible=False 扱い
        reversible=False,
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#schedule-deletion",
        description="停止中のアカウントに削除猶予を設定する（猶予中は取消可能）。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/deletion"},
        locate_steps=(
            _step("teachers", "user_list", "停止中のアカウントを一覧から選びます"),
            _step("teachers", "user_delete_button", "「削除予約…」を押します",
                  precondition="user_selected"),
        ),
    ),
    Capability(
        id="users.transfer_ownership",
        screen="teachers",
        title="所有物を別の教員へ移管する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_ACTION,
        reversible=False,  # 逆方向の移管は可能だが、同一操作の revert としては扱わない
        confirm=True,
        target_type="user",
        howto_doc="admin_operations/users.md#transfer-ownership",
        description="教材・コース・グループの所有者を後任の教員へ付け替える（取り消し不可）。",
        api={"method": "POST", "path": "/api/admin/users/{user_id}/transfer-ownership"},
        locate_steps=(
            _step("teachers", "user_list", "移管元の教員を一覧から選びます"),
            _step("teachers", "user_transfer_button", "「移管…」を押して移管先を選びます",
                  precondition="user_selected"),
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
    # SL層（stakes_ledger_design.md SL2/SL3）: 「何が起これば覆るのか」の記帳。
    # 到達可能性は人間専用語彙で worker が書かない。AI 候補は候補のままで、
    # 教員が「確認して記帳」を押すまで確定しない。
    Capability(
        id="doubt.record_falsification_conditions",
        screen="doubt-atlas",
        title="覆る条件を記帳する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/doubt.md#falsification-conditions",
        description="台帳ノードの「覆る条件」区画で、この主張が何が起これば覆るのかを記帳する。"
                    "手で記帳するほか、AI に候補を出させて確認・記帳もできる。"
                    "到達可能性は人が判断して記帳する項目で、AI は書き込まない。"
                    "まだ何も記帳されていない状態はエラーではなく、そのままの事実。",
        api={"method": "POST",
             "path": "/api/admin/doubt/ledger/{target_type}/{target_id}/falsification-conditions"},
        locate_steps=(
            _step("doubt-atlas", "doubt_falsification_form_button",
                  "台帳ノードの詳細ペインで「覆る条件を記帳する」を押します"),
            _step("doubt-atlas", "doubt_falsification_refresh_button",
                  "AI の候補から選びたいときは「AIに候補を出してもらう」を押します"),
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
        # 実ルートは `/api/admin/shared/{object_type}/{object_id}/releases`。
        # ここは object_type を "document" に固定した具体パスとして宣言する。
        api={"method": "POST", "path": "/api/admin/shared/document/{object_id}/releases"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材を選びます"),
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "shared_version_button", "「版の管理…」を押します",
                  precondition="material_menu_open"),
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
        # 実ルートは `/api/admin/shared/{object_type}/{object_id}/releases`（object_type="course"）。
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
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_estimate_button", "「解析コスト見積り…」を押します",
                  precondition="material_menu_open"),
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
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_figures_button", "「図・画像」を押してモーダルを開きます",
                  precondition="material_menu_open"),
        ),
    ),
    # discuss_opening_authoring_design.md §6.2: 開幕素材（議論のきっかけ）のレビュー。
    # キューは教材管理タブの「検出要素」→「説明レビュー」にあるため screen は materials。
    # v1 は道案内のみ（承認・編集は既存 element-explanations API を UI から行う）。
    Capability(
        id="course.discuss_opening_review",
        screen="materials",
        title="論文の議論のきっかけ（AI候補）を確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        # 2026-09-03: 節を新設したので結線する（従来は howto_doc 未設定で
        # assistant_kb.undocumented に載っていた）。
        howto_doc="admin_operations/materials.md#discuss-opening-review",
        description="discuss の開幕画面で使う「議論のきっかけ」の AI 候補を確認・編集・承認する。"
                    "承認するまで学習者には表示されない（候補のまま）。",
        api={"method": "GET", "path": "/api/admin/documents/{document_id}/element-explanations"},
        locate_steps=(
            _step("materials", "material_row:{material_id}", "対象の教材の行を選びます"),
            _step("materials", "material_row_menu", "行の「⋯」メニューを開きます",
                  precondition="material_selected"),
            _step("materials", "material_inventory_button",
                  "「検出要素」を押して要素の一覧を開きます",
                  precondition="material_menu_open"),
            _step("materials", "explanation_review_button",
                  "「説明レビュー」を押して候補を確認します",
                  precondition="inventory_modal_open"),
        ),
    ),
    # --- つまづきデータ (stumbles) ---
    Capability(
        id="stumbles.view",
        screen="stumbles",
        title="学生のつまづきデータを確認する",
        required_role=ROLE_TEACHER,
        kind=KIND_GUIDANCE_ONLY,
        # 2026-09-03 是正: 実 API（routes/admin.py::list_unanswered_queries）は
        # `_require_editable_course_or_404` を通すため、ロールだけでは足りず
        # course owner / editor が必要（それ以外は 404）。宣言を実 API に合わせる。
        scope="editable_course",
        howto_doc="admin_operations/stumbles.md#view",
        description="RAG 検索で教材から回答できなかった質問の一覧を確認し、教材補強に活用する。"
                    "学生名と質問本文を返すため、対象コースの所有者または editor 権限が必要"
                    "（権限が無いコースは 404 で、件数も返らない）。"
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
    # URL指定による教材取得の許可リスト（url_material_upload_design.md UF1）。
    # 参照は TEACHER 以上だが **追加・削除は SYSTEM_ADMIN のみ**（routes/admin.py::
    # add_url_fetch_domain / remove_url_fetch_domain）。この capability は管理操作を
    # 説明・案内するものなので required_role は SYSTEM_ADMIN に fail-closed で寄せる。
    Capability(
        id="llm_models.manage_url_fetch_domains",
        screen="llm-models",
        title="URL取得の許可ドメインを管理する",
        required_role=ROLE_SYSTEM_ADMIN,
        kind=KIND_GUIDANCE_ONLY,
        howto_doc="admin_operations/llm_models.md#url-fetch-domains",
        description="教員が URL を指定して教材を取得できるドメインの許可リストを追加・削除する。"
                    "登録したドメインからのみ取得でき、初期状態は空（URL 指定の取得と arXiv からの"
                    "取り込みは無効）。照合はサーバ側で行われる。",
        api={"method": "POST", "path": "/api/admin/url-fetch-domains"},
        locate_steps=(
            _step("llm-models", "url_fetch_domains_section",
                  "タブ末尾の「URL取得の許可ドメイン」区画でドメインを入力して「追加」を押します"),
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
