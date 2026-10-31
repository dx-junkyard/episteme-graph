"""D層・SL層（前提の地図・台帳・疑義・検証提案・反実仮想）と学生の検証状態の一行の行為（第 25 周・面 B）。

画面の正本は ``frontend/public/js/doubt-atlas.js``（教員の「前提の地図」タブと、原稿スタジオのグラフの
ノード詳細に差し込まれる台帳の区画・反実仮想モード）と ``app.js``（出典タブの検証状態の一行・部品の
文脈の一行・「この分野の未検証の前提を見る」）。契約の正本は ``backend/api/routes/doubt.py``。

行為 id の接頭辞は ``admin.doubt.`` / ``learning.ledger.``。実行と投影は ``uxsim/runner/doubt_exec.py``。
画面がクライアント側で止める入力（理由が空・スコープ 0 件の直接検証・コーパス外の確認が空）は HTTP を
出さずに画面の文言を返す（doubt-atlas.js と同じ止め方）。
"""
from __future__ import annotations

from uxsim.actions.registry import Action, _a

_TD = "teacher/18-admin-doubt-atlas.md#"
_TL = "teacher/14-admin-lecture-studio.md#"
_ST = "student/02-student.md#"
_DOUBT = "/api/admin/doubt"
_LEDGER = ("GET", _DOUBT + "/ledger/{target_type}/{target_id}")
_ATLAS = ("GET", _DOUBT + "/courses/{course_id}/assumption-atlas")
_ASSUMPTIONS = ("GET", _DOUBT + "/courses/{course_id}/assumptions")
_OPEN = ("GET", _DOUBT + "/courses/{course_id}/open-assumptions")
_CF_SESSIONS = ("GET", _DOUBT + "/counterfactual/sessions")
_RELOAD = (_ATLAS, _ASSUMPTIONS, _OPEN, _CF_SESSIONS)
_GRAPH = ("GET", "/api/admin/documents/{document_id}/component-graph")

ACTIONS: list[Action] = [
    # --- 前提の地図タブ（doubt-atlas.js initTab / reloadAll）---
    _a("admin.doubt.tab_open", "「前提の地図」タブを開いてコースを選ぶ", "admin", "doubt-atlas.course-select", _ATLAS,
       args={"course_hint": "str?"}, extra_api=(("GET", "/api/admin/courses"),) + _RELOAD[1:],
       manual=_TD + "course-select",
       note="course_hint はコースの題名の部分一致か一覧の番号（1 始まり）。省くと画面と同じく一覧の先頭を選ぶ。"
            "選ぶと散布図・前提候補（候補）・未検証合意リスト・反実仮想セッションを読み込む"),
    _a("admin.doubt.refresh", "前提の地図を「再読込」する", "admin", "doubt-atlas.refresh", _ATLAS,
       extra_api=_RELOAD[1:], precondition="前提の地図タブでコースを選んでいる", manual=_TD + "refresh"),
    _a("admin.doubt.audit_run", "「コーパス監査実行」を押す", "admin", "doubt-atlas.audit-run",
       ("POST", _DOUBT + "/courses/{course_id}/corpus-audit/run"), extra_api=_RELOAD,
       precondition="前提の地図タブでコースを選んでいる", manual=_TD + "audit-run",
       note="非LLM。依存されているのに主張・引用の無いノードを前提候補にする（3 論文未満のコースでは走らない）"),
    _a("admin.doubt.mining_run", "「前提マイニング実行」を押す", "admin", "doubt-atlas.mining-run",
       ("POST", _DOUBT + "/courses/{course_id}/assumption-mining/run"), extra_api=_RELOAD,
       precondition="前提の地図タブでコースを選んでいる", llm_cost="product", manual=_TD + "mining-run",
       note="非同期（押した直後は一覧が変わらないことがある。しばらくしてから再読込する）"),
    _a("admin.doubt.load_recompute", "「負荷再計算」を押す", "admin", "doubt-atlas.load-recompute",
       ("POST", _DOUBT + "/courses/{course_id}/load/recompute"), extra_api=_RELOAD,
       precondition="前提の地図タブでコースを選んでいる", manual=_TD + "load-recompute"),
    _a("admin.doubt.assumption_filter", "前提候補の表示を切り替える", "admin", "doubt-atlas.assumption-filter", _ASSUMPTIONS,
       args={"status": "str"}, precondition="前提の地図タブでコースを選んでいる", manual=_TD + "assumption-filter",
       note="status は 候補 | 確定済み | 却下済み | すべて（candidate | confirmed | dismissed | all）"),
    _a("admin.doubt.assumption_confirm", "前提候補を確定する（文面を訂正して確定も）", "admin", "doubt-atlas.assumption-confirm",
       ("POST", _DOUBT + "/assumptions/{assumption_id}/confirm"), args={"pick": "str?", "reason": "str", "statement": "str?"},
       extra_api=_RELOAD, precondition="前提候補の一覧に候補がある", manual=_TD + "assumption-confirm",
       note="pick は候補の番号（1 始まり）か前提文の一部。reason は確定の理由（本人の言葉・必須。空なら画面は何もしない）。"
            "statement を渡すと「文面を訂正して確定」"),
    _a("admin.doubt.assumption_dismiss", "前提候補を却下する", "admin", "doubt-atlas.assumption-dismiss",
       ("POST", _DOUBT + "/assumptions/{assumption_id}/dismiss"), args={"pick": "str?"}, extra_api=_RELOAD,
       precondition="前提候補の一覧に候補がある", manual=_TD + "assumption-dismiss",
       note="却下は削除ではなく状態の遷移（表示を「却下済み（保持）」にすると見える）"),
    _a("admin.doubt.assumption_open_ledger", "確定済みの前提のカードで［台帳を開く］を押す", "admin",
       "doubt-atlas.assumption-open-ledger", _LEDGER, args={"pick": "str?"},
       precondition="前提候補のレビューに［台帳を開く］の出ている確定済みの前提がある（表示を確定済み / すべてに切り替える）",
       manual=_TD + "assumption-open-ledger",
       note="pick はカードの番号（1 始まり）か前提文の一部。右の詳細に前提の台帳が開き、記帳フォームがそのまま使える。"
            "論文に結ばれていない前提にはボタンが無く、代わりに事実文が 1 行出る"),
    _a("admin.doubt.ledger_open", "散布図の点・未検証合意リストの行を開いて台帳の詳細を読む", "admin", "doubt-atlas.plot-detail",
       _LEDGER, args={"target_hint": "str?", "from": "str?"}, precondition="前提の地図タブでコースを選んでいる",
       manual=_TD + "plot-detail",
       note="target_hint は点・行の番号（1 始まり）かラベルの一部。from は atlas（散布図）| open（未検証合意リスト）|"
            " assumptions（前提候補の一覧の確定済みの前提）| unplotted（散布図の下の「依存の広がりを計算できない前提」）。"
            "省くと散布図 → 未検証合意リスト → 確定済みの前提の順に探す。target_hint「前提N」は計算できない前提の区画の N 番目"
            "（第 9 波・H52）"),
    _a("admin.doubt.unplotted_open", "散布図の下の「依存の広がりを計算できない前提」のボタンを押して台帳を開く", "admin",
       "doubt-atlas.plot-detail", _LEDGER, args={"pick": "str?"}, precondition="前提の地図に「依存の広がりを計算できない前提」の区画がある",
       manual=_TD + "plot-detail",
       note="第 9 波（doubt H52）: pick は区画の中の番号（観察の「前提N」の N — 散布図の点の番号とは別）か前提文の一部。"
            "右の詳細に前提の台帳が開き、記帳フォームがそのまま使える"),
    _a("admin.doubt.open_reachable_filter", "未検証合意リストを「到達可能な反証条件がある項目だけ」に絞る", "admin",
       "doubt-atlas.open-assumptions-reachable-filter", _OPEN, args={"on": "bool?"},
       precondition="未検証合意リストを読んでいる", manual=_TD + "open-assumptions-reachable-filter",
       note="再取得はしない（読み込み済みの一覧をその場で絞るだけ。HTTP なし）"),
    # --- 原稿スタジオのグラフ（ノード詳細の台帳区画・反実仮想モード）---
    _a("admin.doubt.studio_node_ledger", "原稿スタジオのグラフでノードを選び、台帳の区画を読む", "admin", _TL + "counterfactual-mode",
       _LEDGER, args={"document_id": "str?", "material_hint": "str?", "node_hint": "str?", "expect_row": "str?"},
       extra_api=(_GRAPH, ("GET", "/api/admin/materials")),
       precondition="原稿スタジオで教材のグラフを開いている", manual=_TD + "ledger-detail",
       note="material_hint は教材の題名・ファイル名の部分一致。node_hint はノードのラベルの一部か番号（1 始まり・主グラフが先）。"
            "画面はノードの component_id（無ければノード id）で台帳を引く。expect_row=true は場面の前提「このノードの台帳に"
            "行がある」を runner が確かめる印（無ければ blocked_precondition に記す・第 25 周 H31）"),
    # --- 台帳の詳細（renderLedgerInto）---
    _a("admin.doubt.scope_record", "「スコープを記帳」する", "admin", "doubt-atlas.record-scope",
       ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/scopes"),
       args={"condition": "str?", "domain": "str?", "precision": "str?", "system": "str?", "evidence_quote": "str?",
             "reason": "str?"}, extra_api=(_LEDGER,), precondition="台帳の詳細を開いている", manual=_TD + "record-scope",
       note="成立条件・領域・精度・系のどれか 1 つ以上 + 根拠（原文の引用）+ 理由。空欄は画面ではなくサーバが 422 で止める"),
    _a("admin.doubt.scope_record_raw", "閲覧だけの台帳で、スコープの記帳の要求を画面を経ずに 1 回送ってサーバの応答を見る",
       "admin", _TD + "record-scope", ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/scopes"),
       args={"condition": "str?", "reason": "str?"}, extra_api=(_LEDGER,), precondition="台帳の詳細を開いている",
       manual=_TD + "record-scope",
       note="第 25 周 H30: 画面の操作ではない（ハーネスの確認）。画面は閲覧だけの台帳で記帳のボタンを無効にして送らない — "
            "サーバ側の拒否（403 / 404 と detail）を確かめるための 1 手。権限の確認の場面だけが使う"),
    _a("admin.doubt.scope_record_raw_incomplete",
       "閲覧だけの台帳で、根拠（原文の引用）を空にしたスコープの記帳の要求を画面を経ずに 1 回送ってサーバの応答を見る",
       "admin", _TD + "record-scope", ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/scopes"),
       args={"condition": "str?", "reason": "str?"}, extra_api=(_LEDGER,), precondition="台帳の詳細を開いている",
       manual=_TD + "record-scope",
       note="第 25 周 第 7 周 H43: 画面の操作ではない（ハーネスの確認）。scope_record_raw は根拠・理由を既定値で埋めた有効な body を"
            "送るので、権限 → 入力の順（IK-0999）は当たらない。こちらは根拠（evidence_quote）を空のまま送り、入力の不足より権限が"
            "先に効くか（403 / 404）・入力の不足が先か（422）を見る"),
    _a("admin.doubt.scope_candidate_decide", "AI のスコープ候補を確定・却下する", "admin", "doubt-atlas.record-scope",
       ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/confirm"),
       args={"decision": "str", "pick": "str?"}, extra_api=(
           ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/dismiss"), _LEDGER),
       precondition="台帳の詳細にスコープの候補がある", manual=_TD + "record-scope",
       note="decision は 確定 | 却下（confirm | dismiss）。pick は候補の番号（1 始まり）"),
    _a("admin.doubt.verification_status", "「検証状態を記帳する」", "admin", "doubt-atlas.record-verification-status",
       ("PUT", _DOUBT + "/ledger/{target_type}/{target_id}/verification-status"),
       args={"status": "str", "reason": "str?"}, extra_api=(_LEDGER,), precondition="台帳の詳細を開いている",
       manual=_TD + "record-verification-status",
       note="status は 直接検証の記帳あり | 間接的な支持あり | 未検証 | 反証の記帳あり | 検証情報なし（キーでも可）。"
            "理由が空・スコープ 0 件での直接検証は画面が止める（HTTP なし）"),
    _a("admin.doubt.challenge_record", "「疑義を残す」", "admin", "doubt-atlas.record-challenge",
       ("POST", _DOUBT + "/targets/{target_type}/{target_id}/challenges"),
       args={"challenge_type": "str", "challenge_mode": "str?", "reason": "str?"}, extra_api=(_LEDGER,),
       precondition="前提か主張の台帳の詳細を開いている", manual=_TD + "record-challenge",
       note="challenge_type は 外挿 | 未検証 | 定義 | 補題（または画面の型の文言・キー）。challenge_mode は "
            "主張そのものへ | 主張と根拠のつながりへ（既定は前者）"),
    _a("admin.doubt.supporting_claim_ledger", "疑義を残せない対象の台帳で［この主張の台帳を開く］を押す", "admin",
       "doubt-atlas.record-challenge", _LEDGER, args={"claim_hint": "str?"},
       precondition="式・部品などの台帳に「疑義は前提と主張に残せます。この対象を支える主張は次のとおりです。」が出ている",
       manual=_TD + "record-challenge",
       note="claim_hint は支える主張の名前の一部か番号（1 始まり）。同じ論文で主張の台帳を開く（第 25 周 第 2 波・IK-0904）"),
    _a("admin.doubt.challenge_withdraw", "疑義を「取り下げ」る", "admin", "doubt-atlas.manage-challenge",
       ("POST", _DOUBT + "/challenges/{challenge_id}/withdraw"), args={"pick": "str?"}, extra_api=(_LEDGER,),
       precondition="台帳の詳細に未対応の疑義がある", manual=_TD + "manage-challenge",
       note="pick は疑義の番号（1 始まり）か理由の一部。取り下げは投稿した本人だけ"),
    _a("admin.doubt.withdrawn_challenges_open", "疑義の区画の末尾「取り下げ済みの疑義（読み取りのみ）」を開く", "admin",
       "doubt-atlas.withdrawn-challenges", _LEDGER, precondition="台帳の詳細に「取り下げ済みの疑義」の折りたたみが出ている",
       manual=_TD + "withdrawn-challenges",
       note="既定で閉じている折りたたみ。取り下げた疑義を型・向き・投稿者 — 理由・状態・検証提案のカードの順に読む"
            "（読み取りのみ・ボタンなし・件数なし）。疑義の番号（疑義N）は未対応の一覧だけで数え、ここは含まない"),
    _a("admin.doubt.challenge_propose", "疑義を「検証提案にする」", "admin", "doubt-atlas.manage-challenge",
       ("POST", _DOUBT + "/challenges/{challenge_id}/proposals"),
       args={"pick": "str?", "proposal": "str?", "external_check": "str?", "reachability": "str?"},
       precondition="台帳の詳細に未対応の疑義がある", manual=_TD + "manage-challenge",
       note="external_check（コーパス外の確認）は必須で、空なら画面が止める。reachability は 現在の観測で検証可能 |"
            " 次世代の装置・観測なら可能 | 現状では困難 | 未評価"),
    _a("admin.doubt.proposal_status", "検証提案の進み具合を記帳する（着手・完了・取り下げ）", "admin",
       "doubt-atlas.manage-challenge", ("PATCH", _DOUBT + "/proposals/{proposal_id}"),
       args={"status": "str", "pick": "str?", "completion_note": "str?"}, extra_api=(_LEDGER,),
       precondition="台帳の詳細の疑義の下に検証提案のカードがある", manual=_TD + "manage-challenge",
       note="status は 検証に着手 | 完了を記録 | 取り下げ。第 4 波（IK-0968）: 提案は台帳の疑義の下に常設され、"
            "開き直しても操作できる。pick は提案の番号か文の一部（無ければ status を押せる最初の提案）。"
            "第 10 波（IK-1121）: completion_note は着手済みのカードの「確かめたこと（任意）」の欄（完了を記録するときだけ"
            "送る・空なら「確かめたことの記帳なし」と残る）"),
    _a("admin.doubt.falsification_record", "「覆る条件を記帳する」", "admin", "doubt-atlas.falsification-record",
       ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/falsification-conditions"),
       args={"statement": "str?", "kind": "str?", "evidence_quote": "str?", "reason": "str?", "reachability": "str?"},
       extra_api=(_LEDGER,), precondition="台帳の詳細を開いている", manual=_TD + "falsification-record",
       note="kind は 観測値そのもの | 較正・装置などの補助仮説 | 反証条件を定式化できない"),
    _a("admin.doubt.falsification_candidate_decide", "覆る条件の候補を確認して記帳する・見送る", "admin",
       "doubt-atlas.falsification-candidate-decide",
       ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/falsification-candidates/{candidate_id}/confirm"),
       args={"decision": "str", "pick": "str?", "reachability": "str?"}, extra_api=(
           ("POST", _DOUBT + "/ledger/{target_type}/{target_id}/falsification-candidates/{candidate_id}/dismiss"), _LEDGER),
       precondition="台帳の詳細に覆る条件の候補がある", manual=_TD + "falsification-candidate-decide",
       note="decision は 確認して記帳 | 見送る"),
    _a("admin.doubt.falsification_refresh", "覆る条件の候補を「AIに候補を出してもらう」", "admin", "doubt-atlas.falsification-refresh",
       ("POST", _DOUBT + "/courses/{course_id}/falsification-candidates/refresh"),
       precondition="前提の地図タブでコースを選び、台帳の詳細を開いている", llm_cost="product",
       manual=_TD + "falsification-refresh", note="非同期（候補はしばらくしてから台帳を開き直すと現れる）"),
    _a("admin.doubt.counterfactual_toggle", "反実仮想モードでグラフのノードを仮に偽にする", "admin", _TL + "counterfactual-mode",
       ("POST", _DOUBT + "/counterfactual/compute"),
       args={"node_hint": "str?", "document_id": "str?", "material_hint": "str?"},
       extra_api=(_GRAPH, ("GET", "/api/admin/materials")), precondition="原稿スタジオで教材のグラフを開いている", manual=_TL + "counterfactual-mode",
       note="node_hint はノードのラベルの一部か番号。もう一度同じノードを指すと元に戻す（画面のトグルと同じ）"),
    _a("admin.doubt.counterfactual_observation", "「観測を仮に倒す」", "admin", "doubt-atlas.counterfactual-observation-toggle",
       ("POST", _DOUBT + "/counterfactual/compute"), args={"claim_hint": "str?", "aspect": "str?"},
       extra_api=(("GET", _DOUBT + "/courses/{course_id}/observation-targets"),),
       precondition="原稿スタジオで教材のグラフを開いている", manual=_TD + "counterfactual-observation-toggle",
       note="claim_hint は観測系の主張のラベルの一部か番号。aspect は 観測値そのもの | 較正・装置などの補助仮説"),
    _a("admin.doubt.counterfactual_save", "反実仮想の「セッションを保存」する", "admin", _TL + "counterfactual-mode",
       ("POST", _DOUBT + "/counterfactual/sessions"), args={"notes": "str?"}, extra_api=(_CF_SESSIONS,),
       precondition="反実仮想モードで前提か観測を仮に倒している", manual=_TD + "counterfactual-sessions"),
    # --- 学習画面（app.js の D3-6）---
    _a("learning.ledger.sources_tab", "出典タブで根拠カードの検証状態の一行を読む", "learning", "rightpanel.tab-sources",
       ("GET", "/api/learning/courses/{course_id}/ledger/{target_type}/{target_id}"),
       extra_api=(("GET", "/api/learning/courses/{course_id}/source-chunk/{chunk_id}"),
                  ("GET", "/api/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs")),
       precondition="回答に出典が付いている（直前に質問した）", manual=_ST + "answer-origin-badge",
       note="画面は出典タブの「参照した根拠」カードごとに、そのチャンクの式・主張の台帳を最大 3 行まで添える"),
    _a("learning.ledger.component_line", "教材の「◯◯の説明」で部品の説明を開き、その下の検証状態の一行を読む", "learning",
       _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/ledger/{target_type}/{target_id}"),
       args={"component_hint": "str?", "component_id": "str?"},
       extra_api=(("GET", "/api/learning/courses/{course_id}/components/{component_id}/explanations"),),
       precondition="トピックの教材を開いている（教材に「◯◯の説明」のボタンがある）", manual=_ST + "material-element-anchor",
       note="画面（app.js showComponentExplanations）は承認済みの説明が 1 件以上あるときだけ、その下に一行を添える。"
            "⚓ の部品の文脈パネルには一行は無い"),
    _a("learning.ledger.open_assumptions", "出典タブの「この分野の未検証の前提を見る」を開く", "learning", "rightpanel.tab-sources",
       ("GET", "/api/learning/courses/{course_id}/open-assumptions"), precondition="コースを開いている",
       manual=_ST + "answer-origin-badge"),
]

# doubt_actions が registry より先に読まれた循環（registry 末尾の合流が ImportError で飛ばされた場合）の合流（冪等）
from uxsim.actions import registry as _registry  # noqa: E402

for _act in ACTIONS:
    _registry.REGISTRY.setdefault(_act.id, _act)
