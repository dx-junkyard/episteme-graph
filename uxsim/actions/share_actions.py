"""第 25 周「共有・転用の通し」（face=share）の行為 — 教員の確定 → 学生の見え方。

対象の製品の層: 概念レジストリ（ナレッジライブラリタブの同一性の候補・別名・関係・地図との対応）/
W層の同一性リンク（深く検討の「共通部品と結びつける」）/ C層（原稿スタジオの「説明・承認の共有」）/
教材のグループ共有（教材管理の行「共有」）/ 学生の部品チップ（「共通部品として」タブ・説明バージョン）。

学生側の既存の行為（``learning.component.context`` / ``learning.symbol.lookup`` /
``learning.personal_network.journey``）はレジストリ本体のものを再利用する（ここでは足さない）。
実行と投影は ``uxsim/runner/share_exec.py``。registry.py の末尾で ``REGISTRY`` に合流させる。
"""
from __future__ import annotations

from uxsim.actions.registry import Action, _a

_TE = "teacher/"
_ST = "student/02-student.md#"
_KL = _TE + "19-admin-knowledge-library.md#"
_DL = _TE + "24-admin-deliberation.md#"
_LS = _TE + "14-admin-lecture-studio.md#"
_LIB = "/api/admin/library"

ACTIONS: list[Action] = [
    # --- ナレッジライブラリタブ（概念レジストリ）---
    _a("admin.library.identity_candidates", "ナレッジライブラリの「同一性の候補」を見る", "admin",
       "knowledge-library.identity-candidates", ("GET", _LIB + "/identity-candidates"),
       args={"domain_key": "str?", "include_dismissed": "bool?"}, manual=_KL + "identity-candidates",
       note="画面（admin.js renderLibraryIdentityCandidates）は事実文・概念の名前・支持する論文の題名・"
            "論文ごとのリンク 1 本ずつ [確認][見送り]・概念の [確定][見送り] を出す。件数は出さない"),
    _a("admin.library.identity_link_decide", "同一性の候補のリンクを [確認] / [見送り] する", "admin",
       "knowledge-library.identity-candidates",
       ("POST", "/api/admin/deliberation/identity-links/{link_id}/confirm"),
       extra_api=(("POST", "/api/admin/deliberation/identity-links/{link_id}/reject"),),
       args={"decision": "str", "candidate_hint": "str?", "link_hint": "str?", "reason": "str?"},
       precondition="同一性の候補が表示されている", manual=_KL + "identity-candidates",
       note="decision は confirm（確認）| reject（見送り）。candidate_hint は概念の名前の部分一致か番号、"
            "link_hint は論文の題名の部分一致か番号（1 始まり）。リンクの見送りの画面には理由の欄が無い — "
            "reason を書いても送られない（runner_note に残す）"),
    _a("admin.library.entries", "ナレッジライブラリのエントリ一覧を見る", "admin", "knowledge-library.entries-list",
       ("GET", _LIB + "/entries"), args={"domain_key": "str?", "q": "str?", "entry_type": "str?"},
       manual=_KL + "entries-list", note="画面は候補（include_candidates=true）も一覧に出し、行のチップで区別する"),
    _a("admin.library.entry_open", "エントリを開いて詳細（別名・関係・地図との対応・扱い）を見る", "admin",
       "knowledge-library.entries-list", ("GET", _LIB + "/entries/{entry_id}"),
       args={"entry_hint": "str?"},
       extra_api=(("GET", _LIB + "/entries/{entry_id}/labels"), ("GET", _LIB + "/relations"),
                  ("GET", _LIB + "/atlas-links")),
       precondition="エントリ一覧か同一性の候補が表示されている", manual=_KL + "entries-list",
       note="entry_hint は名前の部分一致か一覧の番号。画面は詳細の下に 4 区画を順に読み込む"),
    _a("admin.library.entry_create", "「新規エントリ作成」で概念を白紙から登録する", "admin",
       "knowledge-library.new-entry-btn", ("POST", _LIB + "/entries"),
       args={"name": "str", "domain_key": "str?", "entry_type": "str?", "aliases": "list[str]?", "summary": "str?"},
       manual=_KL + "new-entry-btn",
       note="entry_type は theory_component（既定）| concept | apparatus など。登録直後は draft（凍結されていない）。凍結（版発行）は埋め込みの計算を伴い取り消せないので、この周の行為にしない"),
    _a("admin.library.entry_review", "概念の扱いを [確定] / [見送り] にする", "admin", "knowledge-library.entry-review",
       ("POST", _LIB + "/entries/{entry_id}/review"),
       args={"status": "str", "review_note": "str?", "entry_hint": "str?", "candidate_hint": "str?"},
       precondition="同一性の候補かエントリの詳細が表示されている", manual=_KL + "entry-review",
       note="status は confirmed（確定）| dismissed（見送り・理由必須。画面は理由が空なら送らない）"),
    _a("admin.library.label_add", "別名・隠しラベルを追加する", "admin", "knowledge-library.labels",
       ("POST", _LIB + "/entries/{entry_id}/labels"), args={"label": "str", "kind": "str?", "entry_hint": "str?"},
       precondition="エントリの詳細を開いている", manual=_KL + "labels",
       note="kind は alternate（別名・既定）| hidden（隠しラベル）"),
    _a("admin.library.label_dismiss", "別名・隠しラベルの［見送り］を押す（理由を書く）", "admin",
       "knowledge-library.labels", ("POST", _LIB + "/entries/{entry_id}/labels/{label_id}/dismiss"),
       args={"label": "str", "review_note": "str?", "entry_hint": "str?"},
       precondition="エントリの詳細の「別名・隠しラベル」に見送っていないラベルがある", manual=_KL + "labels",
       note="第 25 周 第 4 周の前（IK-0897 の第 3 の言い分けを見るため）: label は見送るラベルの文字列（部分一致か番号）。"
            "画面は理由の入力ダイアログで空のまま OK すると送らない。行は消えず「見送り」の状態で残る"),
    _a("admin.library.relation_add", "関係（上位・関連・同じ・近い）を追加する", "admin", "knowledge-library.relations",
       ("POST", _LIB + "/relations"),
       args={"kind": "str", "object_entry_id": "str?", "object_hint": "str?", "reason": "str?", "entry_hint": "str?"},
       precondition="エントリの詳細を開いている", manual=_KL + "relations",
       note="kind は broader | related | exact_match | close_match。画面の相手の欄は「相手のエントリ ID」の"
            "手入力だけ — object_hint（名前）を渡すと runner が一覧から ID を引き、その事実を runner_note に残す"),
    _a("admin.library.relation_decide", "関係の候補を [確定] / [見送り] にする", "admin", "knowledge-library.relations",
       ("POST", _LIB + "/relations/{relation_id}/decide"),
       args={"status": "str", "review_note": "str?", "relation_hint": "str?"},
       precondition="エントリの詳細に関係が表示されている", manual=_KL + "relations",
       note="status は confirmed | dismissed（見送りは理由必須）"),
    _a("admin.library.atlas_links_derive", "「地図との対応を導出」を押す", "admin", "knowledge-library.identity-derive",
       ("POST", _LIB + "/atlas-links/derive"), args={"domain_key": "str?"}, manual=_KL + "identity-derive",
       note="画面は分野を選んでいないと「先に左の一覧から分野を選んでください」で止まり、押すと確認ダイアログが出る"),
    _a("admin.library.atlas_link_decide", "地図との対応の候補を [確定] / [見送り] にする", "admin",
       "knowledge-library.atlas-links", ("POST", _LIB + "/atlas-links/{link_id}/decide"),
       args={"status": "str", "review_note": "str?", "link_hint": "str?", "entry_hint": "str?"},
       extra_api=(("GET", _LIB + "/atlas-links"),),
       precondition="エントリの詳細に地図との対応が表示されている", manual=_KL + "atlas-links",
       note="status は confirmed | dismissed（見送りは理由必須）。link_hint はノード名の部分一致か番号"),
    _a("admin.library.atlas_link_manual", "エントリの「分野の地図との対応」で地図の概念を名前で探して手で対応を付ける",
       "admin", "knowledge-library.atlas-link-manual", ("POST", _LIB + "/entries/{entry_id}/atlas-links"),
       args={"q": "str?", "node_hint": "str?", "entry_hint": "str?"},
       extra_api=(("GET", _LIB + "/atlas-links/nodes"),),
       precondition="エントリの詳細を開いている", manual=_KL + "atlas-links",
       note="第 3 波（IK-0927）: q で地図の概念を名前検索し（選択肢は「名前（領域名）」）、node_hint（名前の部分一致か番号）を"
            "渡すとその概念へ対応を付ける。作られるのは候補（manual_curation）で、確定は一覧の［確定］"
            "（admin.library.atlas_link_decide）。node_hint を省くと検索だけ"),
    # --- 深く検討（W層）の「同一性リンク」---
    _a("admin.deliberation.identity_open", "深く検討の「同一性リンク」区画を見る", "admin",
       "deliberation.identity-link-create",
       ("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/identity-links"),
       args={"material_hint": "str?", "format_hint": "str?", "component_hint": "str?", "element_id": "str?",
             "document_id": "str?"},
       extra_api=(("GET", "/api/admin/materials"), ("GET", "/api/admin/documents/{document_id}/component-graph"),
                  ("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/overview")),
       precondition="グラフレビューか原稿スタジオで部品を選んでいる", manual=_DL + "identity-links",
       note="material_hint（教材一覧の題名・ファイル名の部分一致）と format_hint（tex | pdf — 同じ論文の TeX 版と PDF 版を"
            "見分ける）を渡すと、その教材のグラフレビューを開いてノードを選ぶ（教材行 → グラフ → ノード → 深く検討）。"
            "component_hint はノードか理論コンポーネント一覧のラベルの部分一致。画面は深く検討を開いて内訳を読み、"
            "同一性リンクの一覧を読み込む"),
    _a("admin.deliberation.shared_part_search", "「共通部品と結びつける」で共通部品を検索する", "admin",
       "deliberation.identity-link-create",
       ("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/shared-part-candidates"),
       args={"q": "str?"}, precondition="深く検討の同一性リンク区画を開いている", manual=_DL + "identity-link-create",
       llm_cost="product",
       note="q を空にすると画面はこの要素の内容から自動で検索する。製品は検索文の埋め込みを 1 回計算する"
            "（チャットの検索と同じ経路。LLM の文章生成はしない）"),
    _a("admin.deliberation.shared_part_domain", "共通部品の検索結果の「探す分野」で分野を選ぶ", "admin",
       "deliberation.identity-link-create",
       ("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/shared-part-candidates"),
       args={"domain_hint": "str"}, precondition="共通部品の検索結果に「探す分野:」の選択欄が出ている",
       manual=_DL + "identity-link-create", llm_cost="product",
       note="domain_hint は選択肢の分野名の一部。選ぶと同じ検索文で domain_key 付きで探し直す（教員の選択がサーバの"
            "推定より優先。製品は検索文の埋め込みを 1 回計算する）"),
    _a("admin.deliberation.identity_link_create", "検索結果の「この部品と結びつける（候補を作成）」を押す", "admin",
       "deliberation.identity-link-create", ("POST", "/api/admin/deliberation/identity-links"),
       args={"part_hint": "str?", "reason": "str?"}, precondition="共通部品の検索結果が表示されている",
       manual=_DL + "identity-link-create", note="part_hint は検索結果の名前の部分一致か番号。作られるのは候補だけ"),
    _a("admin.deliberation.identity_link_decide", "同一性リンクを [確定] / [却下] する", "admin",
       "deliberation.identity-link-decide",
       ("POST", "/api/admin/deliberation/identity-links/{link_id}/confirm"),
       extra_api=(("POST", "/api/admin/deliberation/identity-links/{link_id}/reject"),),
       args={"decision": "str", "link_hint": "str?"}, precondition="同一性リンクの候補が表示されている",
       manual=_DL + "identity-link-decide", note="decision は confirm | reject。却下に理由の欄は無い"),
    # --- 原稿スタジオ「説明・承認の共有」（C層）---
    _a("admin.explanations.open", "原稿スタジオで部品の「説明・承認の共有」を開く", "admin",
       "lecture-studio.component-endorse",
       ("GET", "/api/admin/theory-components/{component_id}/explanations"),
       args={"component_hint": "str?", "course_id": "str?"},
       extra_api=(("GET", "/api/admin/courses/{course_id}/theory-components"),),
       precondition="原稿スタジオでコースを選んでいる", manual=_LS + "component-endorse",
       note="画面はコースの理論コンポーネント一覧からカードの「説明・承認の共有」を押して説明バージョンを読み込む"),
    _a("admin.explanations.add", "独自解釈（説明）を追加する", "admin", "lecture-studio.component-endorse",
       ("POST", "/api/admin/theory-components/{component_id}/explanations"),
       args={"body": "str", "title": "str?"}, precondition="説明・承認の共有を開いている",
       manual=_LS + "endorsement-add-explanation", note="画面は共有 OFF で追加する（共有は別のボタン）"),
    _a("admin.explanations.update", "説明カードの「共有ON/OFF」「学生に公開してよいと査読する」を押す", "admin",
       "lecture-studio.component-endorse", ("PATCH", "/api/admin/explanations/{explanation_id}"),
       args={"what": "str", "explanation_hint": "str?"}, precondition="説明バージョンのカードが表示されている",
       manual=_LS + "endorsement-card",
       note="what は share_on | share_off | approve（学生に公開してよいと査読する）。explanation_hint は題名・作者の部分一致"
            "か番号。第 3 波（IK-0925）: 2 つのボタンは説明の著者（と管理者）のカードにだけ出る — 出ていないカードでは押せない"),
    _a("admin.explanations.endorse", "説明カードの「支持する」を押す", "admin", "lecture-studio.component-endorse",
       ("POST", "/api/admin/explanations/{explanation_id}/endorse"),
       args={"level": "str?", "expertise_tag": "str?", "explanation_hint": "str?"},
       precondition="説明バージョンのカードが表示されている", manual=_LS + "endorsement-card",
       note="level は provisional（暫定の支持）| endorsed（支持・既定）| strong（強い支持）。著者本人の支持は人数に数えない"),
    _a("admin.explanations.revoke", "説明カードの「支持を取り消す」を押す", "admin", "lecture-studio.component-endorse",
       ("DELETE", "/api/admin/explanations/{explanation_id}/endorse"), args={"explanation_hint": "str?"},
       precondition="説明バージョンのカードが表示されている", manual=_LS + "endorsement-card",
       note="第 3 波（IK-0924）: 自分の支持を取り消す（記録は残る）"),
    _a("admin.explanations.endorsers", "説明カードの「支持した教員の一覧」を開く", "admin",
       "lecture-studio.component-endorse", ("GET", "/api/admin/explanations/{explanation_id}/endorsements"),
       args={"explanation_hint": "str?"}, precondition="説明バージョンのカードが表示されている",
       manual=_LS + "endorsement-card",
       note="第 25 周 H28: 折りたたみを開くと一度だけ読み込み、名前・専門タグ・支持の段階（著者自身の支持には印）を出す。"
            "数は出さない"),
    _a("admin.explanations.cite", "説明カードの「別コースで引用」で引用する", "admin", "lecture-studio.cite-intent",
       ("POST", "/api/admin/explanations/{explanation_id}/cite"),
       args={"course_hint": "str?", "citation_intent": "str?", "explanation_hint": "str?"},
       extra_api=(("GET", "/api/admin/courses"),), precondition="説明バージョンのカードが表示されている",
       manual=_LS + "endorsement-cite",
       note="画面は管理コース一覧のうち owner / editor の別コースだけを引用先に出す。citation_intent は任意（空 = 記録しない）"),
    # --- 教材管理の行「共有」---
    _a("admin.document_share.open", "教材の行の「共有」を開く", "admin", "materials.row-share",
       ("GET", "/api/admin/documents/{document_id}/groups"), args={"material_hint": "str?", "format_hint": "str?", "document_id": "str?"},
       extra_api=(("GET", "/api/groups"), ("GET", "/api/admin/materials")),
       manual=_TE + "11-admin-materials.md#row-share",
       note="material_hint は教材一覧の題名の部分一致。画面は現在の共有設定と自分のグループ一覧を読み込む"),
    _a("admin.document_share.add", "解析成果のグループ共有に「追加」する", "admin", "materials.row-share",
       ("POST", "/api/admin/documents/{document_id}/groups"), args={"group_hint": "str?", "permission": "str?"},
       precondition="教材の共有を開いている", manual=_TE + "11-admin-materials.md#row-share",
       note="permission は viewer（既定）| editor。共有設定の変更は教材の所有者だけ"),
    # --- 学生: 部品チップ ---
    _a("learning.component.shared_part", "部品の文脈の「共通部品として」タブを見る", "learning",
       _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/components/{component_id}/context"),
       args={"component_id": "str?", "element_ref": "str?"}, precondition="教材の ⚓（部品）が見えている",
       manual=_ST + "material-element-anchor",
       note="タブは同一性リンクが確定した部品にだけ出る（無ければ「この論文では」だけ）"),
    _a("learning.component.explanations", "部品の「説明」ボタンで標準・各教員の説明を見る", "learning",
       _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/components/{component_id}/explanations"),
       args={"component_id": "str?", "element_ref": "str?"}, precondition="教材の部品が見えている",
       manual=_ST + "material-element-anchor",
       note="画面（app.js showComponentExplanations）は承認済みの説明だけを並べ、承認の厚みを段階ラベルで添える"),
]

# share_actions が registry より先に読まれた循環（registry 末尾の合流が ImportError で飛ばされた場合）の合流
from uxsim.actions import registry as _registry  # noqa: E402

for _act in ACTIONS:
    _registry.REGISTRY.setdefault(_act.id, _act)
