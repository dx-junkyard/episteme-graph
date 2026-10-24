"""第 25 周 面 C「分野の地図の運用」の行為（教員のギャップ・辺・別名・凍結 → 学生の地図）。

行為の形は ``uxsim/actions/registry.py`` の ``Action``。affordance は管理画面の ``data-ui-anchor``
（``core/help_kb/admin_ui_anchors.py``）か学習画面の anchor、manual は ``docs/manual/`` の節。
実行と投影は ``uxsim/runner/atlas_exec.py``（画面 = ``frontend/public/js/admin.js`` の分野の地図タブ /
``atlas-threads-layer.js`` / ``landscape-layer.js`` / ``corpus-sea.js``）。

使わない操作: 骨格の generate（LLM）/ 下書きの破棄 / retire・restore（後戻りしにくい）/ 修正報告の処理。
embedding を本物の OpenAI へ素通しする従量の行為は ``llm_cost="product"`` にする: 索引の再構築
（``admin.atlas.vectors.refresh``・第 2 周の 1 回だけ）/ 別名の登録（製品がその 1 項目の索引を裏で作り直す）/
凍結（製品が新しい版の索引を裏で作る・第 2 周の 1 名だけ）。
"""
from __future__ import annotations

from uxsim.actions import registry as _registry
from uxsim.actions.registry import Action, _a

_TA = "teacher/17-admin-atlas.md#"
_ST = "student/02-student.md#"
_CART = "/api/admin/cartridges/{cartridge_id}/atlas"
_SKELETON = ("GET", _CART + "/skeleton")
_GAPS = ("GET", _CART + "/gap-candidates")
_GAP_DECIDE = ("POST", _CART + "/gap-candidates/decide")
_EDGES = ("GET", _CART + "/edge-candidates")
_EDGE_DECIDE = ("POST", _CART + "/edge-candidates/decide")
_ALIASES = ("GET", _CART + "/aliases")
_VSTATUS = ("GET", _CART + "/vectors/status")
_DRAFT_PUT = ("PUT", _CART + "/skeleton/draft")

ACTIONS: list[Action] = [
    # --- 教員: 分野の地図タブ ---------------------------------------------------------------
    _a("admin.atlas.open", "分野の地図タブで分野を選ぶ", "admin", "atlas.cartridge-select",
       ("GET", "/api/admin/atlas/domains"), args={"domain_hint": "str?"},
       extra_api=(_SKELETON, ("GET", _CART + "/reports"), _GAPS, _EDGES, _VSTATUS, _ALIASES),
       manual=_TA + "cartridge-select",
       note="domain_hint は分野の管理 ID か表示名の一部（省くと最初の分野）。画面と同じく骨格の状態・修正報告・"
            "論文の解析から見つかった候補・関係（辺）の候補・ベクトル索引・登録済みの別名を読み直す"),
    _a("admin.atlas.gaps.list", "「論文の解析から見つかった候補」を読む", "admin", "atlas.gap-candidates",
       _GAPS, precondition="分野を選んでいる", manual=_TA + "gap-candidates",
       note="候補ごとに名前・層（領域 / 概念と親領域）・触れている論文の題名と理由・逐語・近い既存項目の注記を見る"),
    _a("admin.atlas.gaps.dismissed_filter", "候補の「見送り済みも表示」を切り替える", "admin",
       "atlas.gap-dismissed-filter", _GAPS, args={"show": "bool?"}, precondition="分野を選んでいる",
       manual=_TA + "gap-dismissed-filter", note="show を省くと切り替える（既定は表示しない）"),
    _a("admin.atlas.gaps.accept", "候補を[採用]する", "admin", "atlas.gap-accept", _GAP_DECIDE,
       args={"gap_hint": "str?", "memo": "str?"}, extra_api=(_GAPS,), precondition="候補を読んでいる", manual=_TA + "gap-accept",
       note="gap_hint はカードの番号（画面を読み直しても変わらない）か名前の一部。採用は「カテゴリとして妥当」の判断だけで下書きは変わらない"),
    _a("admin.atlas.gaps.dismiss", "候補を[却下…]する（理由を書く）", "admin", "atlas.gap-dismiss", _GAP_DECIDE,
       args={"gap_hint": "str?", "reason": "str"}, extra_api=(_GAPS,), precondition="候補を読んでいる",
       manual=_TA + "gap-dismiss",
       note="理由は必須。画面は理由が空のとき送らずに「却下には理由が必要です」と出す（runner も送らない）"),
    _a("admin.atlas.gaps.restore", "見送った候補を[見送りから戻す]", "admin", "atlas.gap-restore", _GAP_DECIDE,
       args={"gap_hint": "str?"}, extra_api=(_GAPS,), precondition="「見送り済みも表示」で見送り済みの候補が見えている",
       manual=_TA + "gap-restore"),
    _a("admin.atlas.gaps.alias_register", "候補を既存項目の[別名として登録]する", "admin", "atlas.gap-alias-register",
       ("POST", _CART + "/aliases"), args={"gap_hint": "str?"},
       extra_api=(_GAP_DECIDE, _GAPS, _ALIASES, _VSTATUS), precondition="近い既存項目の注記が付いた候補がある",
       manual=_TA + "gap-alias-register", llm_cost="product",
       note="登録の後、製品はその 1 項目の索引を裏で作り直す（embedding の従量・best-effort）。"
            "画面は別名の登録が通ったときだけ、理由（既存概念『…』の別名として登録）を自動で書いて候補を却下する（2 段）"),
    _a("admin.atlas.gaps.draft_from_frozen", "[現在の版から次版の下書きを作る]", "admin", "atlas.gap-draft-from-frozen",
       ("POST", _CART + "/skeleton/draft/from-frozen"), extra_api=(_SKELETON, _GAPS, _EDGES),
       precondition="採用済みの候補があり、次版の下書きが無い", manual=_TA + "gap-draft-from-frozen",
       note="現行の凍結版を複製するだけ（LLM なし）。下書きは学習者に表示されない。下書きが既にあれば 409 の事実文"),
    _a("admin.atlas.gaps.incorporate_preview", "候補を[次版の下書きに取り込む…]（確認まで）", "admin",
       "atlas.gap-incorporate", ("POST", _CART + "/gap-candidates/incorporate-preview"),
       args={"gap_hint": "str?", "label": "str?"}, precondition="採用済みの候補があり、次版の下書きがある",
       manual=_TA + "gap-incorporate",
       note="確認の画面（追加する項目の名前・親領域・検証）まで。下書きはまだ変わらない。label で名前を編集できる"),
    _a("admin.atlas.gaps.incorporate_apply", "確認の画面で[下書きに追加する]", "admin", "atlas.gap-incorporate",
       _DRAFT_PUT, extra_api=(("POST", _CART + "/gap-candidates/mark-incorporated"), _GAPS),
       precondition="候補の取り込みの確認を見ている", manual=_TA + "gap-incorporate",
       note="画面と同じく既存の下書き保存（revision 付き）→ 取り込み先の記録の順。骨格を書くのはこの保存だけ"),
    _a("admin.atlas.edges.list", "「関係（辺）の候補」を読む", "admin", "atlas.edge-candidates", _EDGES,
       precondition="分野を選んでいる", manual=_TA + "edge-candidates",
       note="候補ごとにつなぐ 2 項目・出所（プロトタイプ近傍 / 共起）・両方に配置がある論文の題名を見る"),
    _a("admin.atlas.edges.dismissed_filter", "関係の候補の「見送り済みも表示」を切り替える", "admin",
       "atlas.edge-dismissed-filter", _EDGES, args={"show": "bool?"}, precondition="分野を選んでいる",
       manual=_TA + "edge-dismissed-filter"),
    _a("admin.atlas.edges.accept", "関係の種類を選んで[採用]する", "admin", "atlas.edge-candidates", _EDGE_DECIDE,
       args={"edge_hint": "str?", "kind": "str", "memo": "str?"}, extra_api=(_EDGES,), precondition="関係の候補を読んでいる",
       manual=_TA + "edge-candidates",
       note="edge_hint はカードの番号（画面を読み直しても変わらない）か「A — B」の見出し・名前の一部。"
            "kind は 隣接 | 依存 | 関連（adjacent | depends | related）。画面は種類が未選択だと送らない"),
    _a("admin.atlas.edges.dismiss", "関係の候補を[見送り…]にする（理由を書く）", "admin", "atlas.edge-candidates",
       _EDGE_DECIDE, args={"edge_hint": "str?", "reason": "str"}, extra_api=(_EDGES,),
       precondition="関係の候補を読んでいる", manual=_TA + "edge-candidates",
       note="理由は必須（空は送らない）。見送った関係は学習者の「推定の糸」からも消える（RE8）"),
    _a("admin.atlas.edges.restore", "見送った関係を[見送りから戻す]", "admin", "atlas.edge-dismissed-filter",
       _EDGE_DECIDE, args={"edge_hint": "str?"}, extra_api=(_EDGES,),
       precondition="「見送り済みも表示」で見送り済みの関係が見えている", manual=_TA + "edge-dismissed-filter"),
    _a("admin.atlas.edges.incorporate_preview", "関係を[次版の下書きへ反映…]（確認まで）", "admin",
       "atlas.edge-incorporate", ("POST", _CART + "/edge-candidates/incorporate-preview"),
       args={"edge_hint": "str?"}, precondition="採用済みの関係があり、次版の下書きがある",
       manual=_TA + "edge-incorporate", note="確認の画面（つなぐ項目・関係の種類・検証）まで。下書きはまだ変わらない"),
    _a("admin.atlas.edges.incorporate_apply", "関係の確認の画面で[下書きに追加する]", "admin", "atlas.edge-incorporate",
       _DRAFT_PUT, extra_api=(("POST", _CART + "/edge-candidates/mark-incorporated"), _EDGES),
       precondition="関係の反映の確認を見ている", manual=_TA + "edge-incorporate"),
    _a("admin.atlas.aliases.list", "「登録済みの別名」を見る", "admin", "atlas.aliases", _ALIASES,
       args={"include_dismissed": "bool?"}, precondition="分野を選んでいる", manual=_TA + "aliases"),
    _a("admin.atlas.vectors.status", "「ベクトル索引」の状態を読む", "admin", _TA + "vector-index", _VSTATUS,
       precondition="分野を選んでいる", manual=_TA + "vector-index"),
    _a("admin.atlas.vectors.refresh", "[索引を再構築]する", "admin", "atlas.vector-refresh",
       ("POST", _CART + "/vectors/refresh"), extra_api=(_VSTATUS,), llm_cost="product",
       precondition="分野を選んでいる", manual=_TA + "vector-refresh",
       note="embedding を呼ぶ（bridge で肩代わりできない本物の従量）。周に 1 回・第 2 周の 1 名だけ"),
    _a("admin.atlas.freeze_impact", "凍結の前に「凍結の影響」を読む", "admin", "atlas.freeze",
       ("GET", _CART + "/freeze-impact"), args={"version": "str"}, extra_api=(_SKELETON, ("GET", _CART + "/reports")),
       precondition="次版の下書きがある", manual=_TA + "freeze",
       note="version は版数（例 2026.2）— 画面は版数が空だと影響を読む前に止まる。[公開（凍結）]を押して出る"
            "影響の確認（削除される項目・外れるコース・事実文・前の版のノードとの対応）か、影響が無ければ公開前チェック"
            "まで。キャンセルして凍結しない"),
    _a("admin.atlas.freeze", "対応を確かめて凍結する（学習者向けに公開する）", "admin", "atlas.freeze-correspondence",
       ("POST", _CART + "/skeleton/freeze"), args={"version": "str", "note": "str?", "correspondence": "str?"},
       extra_api=(("GET", _CART + "/freeze-impact"), _SKELETON, ("GET", _CART + "/reports"), _GAPS, _EDGES),
       precondition="次版の下書きがあり、採用済みの候補を下書きに反映した", manual=_TA + "freeze", llm_cost="product",
       note="version は版数（例 2026.2）。correspondence は none（既定・チェックしない）| all（候補を全部チェック）| "
            "旧ノードの名前の一部をカンマ区切り。凍結は取り消せず、学習者の地図の版が上がる。凍結の後、製品は新しい版の"
            "索引を裏で作る（embedding の従量）。周に 1 回・第 2 周の 1 名だけ"),
    # --- 第 25 周 第 2 波（IK-0884〜0887）---------------------------------------------------
    _a("admin.atlas.edges.show_all", "関係の候補の[すべて表示]を押す（畳んだ近さだけの候補も出す）", "admin",
       "atlas.edge-show-all", _EDGES, precondition="関係の候補の下に「…畳んでいます」の事実文と[すべて表示]が出ている",
       manual=_TA + "edge-show-all", note="GET edge-candidates?show_all=true。分野を切り替えると畳みに戻る"),
    _a("admin.atlas.gaps.orphaned_restore", "「候補の条件から外れた採用済みの判断」を[候補へ戻す]", "admin",
       "atlas.gap-orphaned", _GAP_DECIDE, args={"gap_hint": "str?", "reason": "str?"}, extra_api=(_GAPS,),
       precondition="候補の区画の下に「候補の条件から外れた採用済みの判断」が出ている", manual=_TA + "gap-orphaned",
       note="gap_hint はその別枠のカードの番号か名前の一部。判断を未判断へ戻す（行は消さない）"),
    _a("admin.atlas.gaps.orphaned_incorporate", "外れた採用済みの判断を[次版の下書きに取り込む…]（確認まで）", "admin",
       "atlas.gap-orphaned", ("POST", _CART + "/gap-candidates/incorporate-preview"), args={"gap_hint": "str?"},
       precondition="「候補の条件から外れた採用済みの判断」があり、次版の下書きがある", manual=_TA + "gap-orphaned",
       note="確認の画面まで。続けて admin.atlas.gaps.incorporate_apply で下書きに追加する"),
    _a("admin.atlas.edges.orphaned_dismiss", "「候補の条件から外れた採用済みの関係」を[見送り…]にする（理由を書く）",
       "admin", "atlas.edge-orphaned", _EDGE_DECIDE, args={"edge_hint": "str?", "reason": "str"}, extra_api=(_EDGES,),
       precondition="関係の区画の下に「候補の条件から外れた採用済みの関係」が出ている", manual=_TA + "edge-orphaned"),
    _a("admin.atlas.edges.orphaned_incorporate", "外れた採用済みの関係を[次版の下書きへ反映…]（確認まで）", "admin",
       "atlas.edge-orphaned", ("POST", _CART + "/edge-candidates/incorporate-preview"), args={"edge_hint": "str?"},
       precondition="「候補の条件から外れた採用済みの関係」があり、次版の下書きがある", manual=_TA + "edge-orphaned",
       note="確認の画面まで。続けて admin.atlas.edges.incorporate_apply で下書きに追加する"),
    _a("admin.atlas.freeze_blocked_goto", "凍結の影響で[止めている候補を見る]を押す", "admin", "atlas.freeze-blocked-goto",
       _GAPS, extra_api=(_EDGES,), precondition="凍結の影響に「公開できません」が出ている（readiness.ready が偽）",
       manual=_TA + "freeze-blocked-goto", note="モーダルを閉じて候補の区画（候補 → 関係の候補）へ移る"),
    # --- 学生 --------------------------------------------------------------------------------
    _a("learning.atlas.select_node", "分野の地図で領域・概念を選んで右のパネル（状態・検証の行・承認の行）を読む",
       "learning", "topbar.atlas", ("GET", "/api/atlas"), args={"node_hint": "str?", "level": "int?"},
       precondition="分野の地図を開いている", manual=_ST + "field-atlas",
       note="node_hint は領域・概念の名前（または番号）。パネルは検証の行（台帳／論文が置かれている事実）・承認の行・"
            "［ここから学ぶ］［根拠を見る］（ある要素だけ）［気になる］［修正を報告］"),
    _a("learning.atlas.layers", "分野の地図で「推定の糸」と「論文の位置」を重ねて見る", "learning",
       "atlas.relation-threads", ("GET", "/api/atlas"), args={"level": "int?"},
       extra_api=(("GET", "/api/learning/courses/{course_id}/landscape"),),
       precondition="コースを開いている", manual=_ST + "relation-threads",
       note="地図のコースレベルで 2 つのトグルを入れた見え方（糸 = 点線・AIによる推定（未確認）・骨格の版 / "
            "論文の位置 = ノードごとの論文と出所ラベル・現行版に対応の無い位置づけの事実文）"),
    _a("learning.atlas.sea_map", "論文の海で分野の地図（厚み・縁・外）を見る", "learning", "sidebar.corpus-sea",
       ("GET", "/api/learning/corpus/landscape"), args={"domain_key": "str?"},
       extra_api=(("GET", "/api/atlas"), ("GET", "/api/learning/corpus/documents")),
       manual=_ST + "corpus-sea-map",
       note="分野を選ぶと地図（領域の塗りの段階・アンカーの円の段階）・地図の端（縁・外）・論文の一覧が出る"),
]

# registry.py の末尾の 1 行（``# r25:atlas``）が無い順序で読まれても行為が引けるように、ここでも合流する（冪等）。
_registry.REGISTRY.update({a.id: a for a in ACTIONS if a.id not in _registry.REGISTRY})
