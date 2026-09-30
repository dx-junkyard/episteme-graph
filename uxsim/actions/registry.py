"""行為レジストリ（PE9 — ペルソナの行為空間は UI の affordance から導く）。

各行為は画面の要素（``data-ui-anchor``）か利用者マニュアルの節（``{#anchor}``）に対応づける。
``api`` は製品の実ルート（``(METHOD, path template)``）。path の ``{name}`` は
``PersonaSession`` の既知 ID か行為の引数から解決する。複合行為（2 呼び出し）は
``api`` に主となる呼び出しを書き、``extra_api`` に残りを書く。

args の型表記: ``str`` / ``int`` / ``bool`` / ``list[str]`` / ``dict``。末尾 ``?`` は省略可。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Screen = Literal["learning", "admin"]
LlmCost = Literal["product", "none"]

LEARNING_CHAT = ("POST", "/api/learning/courses/{course_id}/topics/{topic_id}/chat")


@dataclass(frozen=True)
class Action:
    """ペルソナができる 1 つの行為。"""

    id: str
    label: str
    screen: Screen
    affordance: str
    api: Optional[tuple[str, str]]
    args: dict[str, str] = field(default_factory=dict)
    precondition: str = ""
    llm_cost: LlmCost = "none"
    manual: str = ""  # docs/manual 相対の "<file>#<anchor>"（審判 D の引き先）
    browser: Optional[tuple[str, ...]] = None
    extra_api: tuple[tuple[str, str], ...] = ()
    unsupported: bool = False
    note: str = ""

    @property
    def all_api(self) -> tuple[tuple[str, str], ...]:
        return ((self.api,) if self.api else ()) + self.extra_api


def _a(id: str, label: str, screen: Screen, affordance: str, api, **kw) -> Action:
    return Action(id=id, label=label, screen=screen, affordance=affordance, api=api, **kw)


_ST = "student/02-student.md#"
_CHAT_ARGS = {"message": "str", "selection_text": "str?", "screen_mode": "str?"}

_LEARNING: list[Action] = [
    _a("auth.login", "ログインする", "learning", _ST + "login", ("POST", "/api/auth/login"),
       manual=_ST + "login", note="runner がセッション開始時に呼ぶ。screen は利用者に応じて読み替える"),
    _a("learning.course.list", "コース一覧を見る", "learning", "sidebar.course-tree",
       ("GET", "/api/learning/courses"), manual=_ST + "enroll-course"),
    _a("learning.course.enroll", "コースの受講を始める", "learning", _ST + "enroll-course",
       ("POST", "/api/learning/courses/{course_id}/enroll"), args={"course_id": "str?"},
       precondition="コース一覧で受講可能なコースを見ている", manual=_ST + "enroll-course"),
    _a("learning.course.open", "コースを開く", "learning", "sidebar.course-tree",
       ("GET", "/api/learning/courses/{course_id}"), args={"course_id": "str?"},
       precondition="受講中のコースがある", manual=_ST + "screen-overview"),
    _a("learning.topic.open", "トピックを開いて教材を読む", "learning", "sidebar.course-tree",
       ("GET", "/api/learning/courses/{course_id}/topics/{topic_id}/material"), args={"topic_id": "str?"},
       precondition="コースを開いている", manual=_ST + "material-split"),
    _a("learning.chat.ask", "チャットで質問する", "learning", "composer.send", LEARNING_CHAT,
       args=_CHAT_ARGS, precondition="トピックを開いている", llm_cost="product", manual=_ST + "ai-chat",
       browser=("fill", "[data-ui-anchor='composer.input']", "click", "[data-ui-anchor='composer.send']")),
    _a("learning.chat.rewrite", "自分のメッセージを書き直して送る", "learning", _ST + "edit-delete-message",
       LEARNING_CHAT, args={"message": "str", "replace_message_id": "str?"},
       precondition="以前に質問を送っている", llm_cost="product", manual=_ST + "edit-delete-message"),
    _a("learning.chat.history", "これまでの会話を見る", "learning", "rightpanel.clear-history",
       ("GET", "/api/learning/courses/{course_id}/topics/{topic_id}/chat"), manual=_ST + "ai-chat"),
    _a("learning.check.take", "確認問題に答える", "learning", _ST + "comprehension-check",
       ("POST", "/api/learning/courses/{course_id}/topics/{topic_id}/check"),
       args={"answer": "str", "question": "str?"}, precondition="トピックの教材を読んだ",
       llm_cost="product", manual=_ST + "comprehension-check"),
    _a("learning.check.self_check", "確認問題の並置を見て自己確認する", "learning", _ST + "check-options",
       ("POST", "/api/learning/courses/{course_id}/topics/{topic_id}/check/self-check"),
       args={"self_check": "str"}, precondition="確認問題に答えた", manual=_ST + "check-options",
       note="self_check は agreed | disagreed | verdict_wrong"),
    _a("learning.discuss.opening", "「この論文と議論する」を開く", "learning", "sidebar.mode-discuss",
       ("GET", "/api/learning/courses/{course_id}/discuss/opening"), manual=_ST + "discuss-opening"),
    _a("learning.discuss.ask", "論文と議論する（発言を送る）", "learning", "composer.send", LEARNING_CHAT,
       args={"message": "str", "discuss_scope": "str?"}, precondition="議論モードを開いている",
       llm_cost="product", manual=_ST + "discuss-opening", note="topic_id は _discussion 固定"),
    _a("learning.discuss.reflection", "今日の理解を自分の言葉で残す", "learning", "discuss.end",
       ("POST", "/api/learning/courses/{course_id}/discuss/reflection"), args={"text": "str"},
       precondition="議論を終えようとしている", manual=_ST + "discuss-end"),
    _a("learning.cycle.intention", "読む動機・持ち越しの問いを書く", "learning", _ST + "understanding-cycle",
       ("POST", "/api/learning/courses/{course_id}/cycle/intention"),
       args={"role": "str", "text": "str", "source_trace_id": "str?"}, manual=_ST + "understanding-cycle",
       note="role は opening_motive | carryover_question | revisit_answer | leave_note"),
    _a("learning.cycle.return_door", "帰還の扉を見る", "learning", "material.return-door",
       ("GET", "/api/learning/courses/{course_id}/cycle/return-door"), manual=_ST + "return-door"),
    _a("learning.cycle.todays_words", "今日の自分の言葉を見る", "learning", _ST + "return-door-leave-note",
       ("GET", "/api/learning/courses/{course_id}/cycle/todays-words"), manual=_ST + "return-door-leave-note"),
    _a("learning.cycle.anchor", "教材の箇所に軽い印を付ける", "learning", "material.quick-anchor",
       ("POST", "/api/learning/courses/{course_id}/cycle/anchor"),
       args={"quick_label": "str", "selection_text": "str?"}, precondition="トピックの教材を読んでいる",
       note="quick_label は 気になる | まだ分からない | あとで戻る | 何かとつながりそう のどれか",
       manual=_ST + "quick-anchor"),
    _a("learning.tension.digest", "引っかかりの候補を見る", "learning", _ST + "tension-digest",
       ("GET", "/api/learning/courses/{course_id}/tension/digest"), manual=_ST + "tension-digest"),
    _a("learning.tension.confirm", "引っかかりの候補を引き受ける", "learning", _ST + "tension-digest",
       ("POST", "/api/learning/tension/{trace_id}/confirm"), args={"trace_id": "str?", "learner_text": "str?"},
       precondition="引っかかりの候補が表示されている", manual=_ST + "tension-digest"),
    _a("learning.tension.dismiss", "引っかかりの候補を外す", "learning", _ST + "tension-digest",
       ("POST", "/api/learning/tension/{trace_id}/dismiss"), args={"trace_id": "str?"},
       precondition="引っかかりの候補が表示されている", manual=_ST + "tension-digest"),
    _a("learning.anchors.digest", "問いの帰属の候補を見る", "learning", _ST + "selection-ask",
       ("GET", "/api/learning/courses/{course_id}/anchors/digest"), manual=_ST + "selection-ask"),
    _a("learning.anchors.confirm", "問いの帰属を確定する", "learning", _ST + "selection-ask",
       ("POST", "/api/learning/anchors/{trace_id}/confirm"), args={"trace_id": "str?", "doubt_type": "str?"},
       precondition="帰属の候補が表示されている", manual=_ST + "selection-ask"),
    _a("learning.anchors.dismiss", "問いの帰属の候補を外す", "learning", _ST + "selection-ask",
       ("POST", "/api/learning/anchors/{trace_id}/dismiss"), args={"trace_id": "str?"},
       precondition="帰属の候補が表示されている", manual=_ST + "selection-ask"),
    _a("learning.reconstruction.next", "「再構成に挑戦」を開く", "learning", _ST + "reconstruction-challenge",
       ("GET", "/api/learning/courses/{course_id}/topics/{topic_id}/reconstruction/next"),
       manual=_ST + "reconstruction-challenge"),
    _a("learning.reconstruction.submit", "再構成の答えを出す", "learning", _ST + "reconstruction-challenge",
       ("POST", "/api/learning/reconstruction/{item_id}/submit"),
       args={"item_id": "str?", "response": "dict", "revision_of": "str?"},
       precondition="再構成の問いが表示されている", manual=_ST + "reconstruction-challenge"),
    _a("learning.reconstruction.self_check", "再構成の結果を自己確認する", "learning", _ST + "reconstruction-challenge",
       ("POST", "/api/learning/reconstruction/{recon_id}/self-check"), args={"recon_id": "str?", "result": "str"},
       precondition="再構成の答えを出した", manual=_ST + "reconstruction-challenge",
       note="result は agreed | disagreed | verdict_wrong"),
    _a("learning.reconstruction.descend", "記号まで降りて確かめる", "learning", _ST + "reconstruction-challenge",
       ("POST", "/api/learning/reconstruction/{recon_id}/descend"), args={"recon_id": "str?"},
       precondition="再構成の答えを出した", manual=_ST + "reconstruction-challenge"),
    _a("learning.symbol.lookup", "式の記号をタップして定義を見る", "learning", "material.symbol-lookup",
       ("GET", "/api/learning/courses/{course_id}/symbols/lookup"),
       args={"symbol": "str", "equation_id": "str?", "chunk_id": "str?"},
       precondition="式を含む教材を読んでいる", manual=_ST + "symbol-lookup"),
    _a("learning.descent.ladder", "足場ダイヤル（降下路）を開く", "learning", "material.descent-ladder",
       ("GET", "/api/learning/courses/{course_id}/descent/ladder"),
       args={"element_type": "str?", "element_id": "str?"}, precondition="教材の要素を選んでいる",
       manual=_ST + "descent-ladder"),
    _a("learning.chat.backstage", "楽屋で質問する（記録は自分だけ）", "learning", _ST + "descent-backstage",
       LEARNING_CHAT, args={"message": "str"}, llm_cost="product", manual=_ST + "descent-backstage"),
    _a("learning.element.context", "⚓（引用）を開いて要素の文脈を見る", "learning", _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/elements/{element_type}/{element_id}/context"),
       args={"element_ref": "str?", "element_type": "str?", "element_id": "str?"}, precondition="教材の ⚓ が見えている",
       note="element_ref は教材の投影にある ⚓ の番号（「⚓3」/ 3）か要素 id",
       manual=_ST + "material-element-anchor"),
    _a("learning.source_chunk.open", "出典の本文を開く", "learning", "rightpanel.tab-sources",
       ("GET", "/api/learning/courses/{course_id}/source-chunk/{chunk_id}"), args={"source_no": "int?", "chunk_id": "str?"},
       precondition="回答に出典が付いている", manual=_ST + "answer-origin-badge",
       note="source_no は直前の回答の「出典N」の番号"),
    _a("learning.records.mine", "わたしの記録を開く", "learning", "topbar.my-records",
       ("GET", "/api/me/records"), manual=_ST + "my-records"),
    _a("learning.personal_network.mine", "わたしの地図を開く", "learning", "topbar.my-map",
       ("GET", "/api/me/personal-network"), manual=_ST + "personal-map"),
    _a("learning.personal_network.journey", "わたしの地図で問いからの旅を見る", "learning", "topbar.my-map",
       ("GET", "/api/me/personal-network/journey"), args={"node_id": "str?"},
       precondition="わたしの地図にノードがある", manual=_ST + "personal-map"),
    _a("learning.personal_network.nearby", "わたしの地図で「いまここの周り」を見る", "learning", "topbar.my-map",
       ("GET", "/api/me/personal-network/nearby"), args={"node_id": "str?", "mode": "str?", "center_component_id": "str?"},
       precondition="わたしの地図にノードがある", manual=_ST + "personal-map",
       note="mode は near | root。topic 縮退の記録は範囲表示（応答の mode=range）になり、center_component_id で点に移る"),
    _a("learning.atlas.view", "分野の地図を開く", "learning", "topbar.atlas",
       ("GET", "/api/atlas"), args={"level": "int?"}, manual=_ST + "field-atlas"),
    _a("learning.landscape.view", "論文の位置づけを見る", "learning", "sources.paper-placement",
       ("GET", "/api/learning/courses/{course_id}/landscape"), manual=_ST + "paper-placement"),
    _a("learning.corpus.domains", "論文の海を開く", "learning", "sidebar.corpus-sea",
       ("GET", "/api/learning/corpus/domains"), manual=_ST + "corpus-sea"),
    _a("learning.corpus.documents", "論文の海で論文の一覧を見る", "learning", "sidebar.corpus-sea",
       ("GET", "/api/learning/corpus/documents"), args={"domain_key": "str?"}, manual=_ST + "corpus-sea-map"),
    _a("learning.corpus.discuss_ask", "論文の海の論文と直接議論する", "learning", _ST + "corpus-sea-discuss",
       ("POST", "/api/learning/documents/{document_id}/discuss/chat"),
       args={"message": "str", "document_id": "str?"}, precondition="論文の海で論文を選んでいる",
       llm_cost="product", manual=_ST + "corpus-sea-discuss"),
    _a("learning.lecture.sequence", "レクチャーを再生する", "learning", "material.lecture-toggle",
       ("GET", "/api/learning/lecture/courses/{course_id}/topics/{topic_id}/sequence"),
       precondition="トピックを開いている", manual=_ST + "interactive-lecture"),
    _a("learning.lecture.audio_status", "レクチャーの音声の準備を確かめる", "learning", "material.lecture-toggle",
       ("GET", "/api/learning/lecture/courses/{course_id}/topics/{topic_id}/audio-status"),
       precondition="トピックを開いている", manual=_ST + "interactive-lecture"),
    _a("learning.help.inspect", "「？」で使い方を調べる", "learning", "topbar.inspect",
       ("GET", "/api/learning/help/ui-anchors"), args={"anchor_id": "str?"},
       extra_api=(("POST", "/api/learning/help/ui-anchor-events"),), manual=_ST + "inspect-mode",
       note="anchor_id に節が無ければ UI と同じく no_hit を記録する"),
    _a("learning.chat.usage_help", "チャットで使い方を聞く", "learning", "topbar.inspect", LEARNING_CHAT,
       args={"message": "str", "ui_anchor": "str?"}, manual=_ST + "inspect-mode"),
    _a("learning.progress", "進捗タブを見る", "learning", "rightpanel.tab-progress",
       ("GET", "/api/learning/courses/{course_id}/progress"), manual=_ST + "progress-tab"),
    _a("learning.voice.speak", "回答を読み上げてもらう", "learning", "composer.voice",
       ("POST", "/api/learning/voice/speak"), args={"text": "str?"}, llm_cost="product",
       manual=_ST + "voice-mode"),
    _a("learning.chat.casual", "気軽に話しかける", "learning", "composer.send", LEARNING_CHAT,
       args={"message": "str"}, llm_cost="product", manual=_ST + "ai-chat"),
    # --- §18.2 論理構造を辿る行為（2026-09-30） ---
    _a("learning.component.context", "⚓ の部品チップを開いて部品の文脈を見る", "learning",
       _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/components/{component_id}/context"),
       args={"component_id": "str?", "element_ref": "str?"}, precondition="教材の ⚓（部品）が見えている",
       manual=_ST + "material-element-anchor"),
    _a("learning.component.context_hop", "部品の文脈の図で隣の部品へ移る（旅）", "learning",
       _ST + "material-element-anchor",
       ("GET", "/api/learning/courses/{course_id}/components/{component_id}/context"),
       args={"component_id": "str?"}, precondition="部品の文脈を開いている",
       manual=_ST + "material-element-anchor",
       note="component_id を省くと直前の文脈の graph.upper → lower の最初の移動可能な部品を中心にする"),
    _a("learning.chunk.claim_refs", "出典本文に紐づく主張を見る", "learning", "rightpanel.tab-sources",
       ("GET", "/api/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs"), args={"chunk_id": "str?"},
       precondition="出典の本文を開いている", manual=_ST + "answer-origin-badge"),
    _a("learning.chat.ask_selection", "選んだ箇所について質問する", "learning", _ST + "selection-ask",
       LEARNING_CHAT, args={"message": "str", "selection_ref": "str?", "selection_text": "str?", "selection_segment_id": "int?"},
       precondition="教材の本文を選んでいる", llm_cost="product", manual=_ST + "selection-ask",
       note="selection_ref=「区画番号:文番号」か、教材本文に実在する selection_text（無い文は送らない）"),
    _a("learning.chat.cycle", "予想を立てる問い・予想との違いを AI に求める", "learning",
       _ST + "understanding-cycle", LEARNING_CHAT, args={"message": "str", "cycle_mode": "str"},
       precondition="議論モードで予想の枠を開いている", llm_cost="product", manual=_ST + "understanding-cycle",
       note="cycle_mode は elicit | diff（discuss.js と同じく議論中の会話へ相乗りする）"),
    _a("learning.atlas.threads", "分野の地図で推定の糸を見る", "learning", "atlas.relation-threads",
       ("GET", "/api/atlas"), args={"level": "int?"}, manual=_ST + "relation-threads"),
    _a("learning.atlas.neighbors", "わたしの地図で近くにある概念（霧）を見る", "learning", "topbar.my-map",
       ("GET", "/api/me/personal-network/atlas-neighbors"), args={"node_id": "str?"},
       precondition="わたしの地図にノードがある", manual=_ST + "personal-map"),
]

_TE = "teacher/"
_ADMIN: list[Action] = [
    _a("admin.next_steps.list", "「次にやること」を見る", "admin", "header.next-steps",
       ("GET", "/api/admin/assistant/next-steps"), manual=_TE + "10-admin-common.md#next-steps"),
    _a("admin.materials.list", "教材一覧を見る", "admin", "materials.refresh",
       ("GET", "/api/admin/materials"), manual=_TE + "11-admin-materials.md"),
    _a("admin.materials.get", "教材 1 件の詳細を見る", "admin", "materials.refresh",
       ("GET", "/api/admin/materials/{material_id}"), args={"material_id": "str?"},
       manual=_TE + "11-admin-materials.md"),
    _a("admin.materials.upload_url", "URL から教材を取り込む", "admin", "materials.url-upload-submit",
       ("POST", "/api/admin/materials/upload-from-url"),
       args={"url": "str", "analyze_images": "bool?", "cartridge_id": "str?"}, llm_cost="product",
       manual=_TE + "11-admin-materials.md#url-upload-submit"),
    _a("admin.materials.task_status", "解析の進み具合を見る", "admin", "materials.refresh",
       ("GET", "/api/admin/tasks/{task_id}"), args={"task_id": "str?"},
       manual=_TE + "11-admin-materials.md"),
    _a("admin.materials.visibility", "教材の開示範囲を変える", "admin", "materials.row-share",
       ("PUT", "/api/admin/materials/{material_id}/visibility"),
       args={"material_id": "str?", "visibility": "str", "group_id": "str?"},
       manual=_TE + "11-admin-materials.md#row-share"),
    _a("admin.graph_review.open", "グラフレビューを開く", "admin", "materials.row-graph-review",
       ("GET", "/api/admin/documents/{document_id}/component-graph"), args={"document_id": "str?"},
       manual=_TE + "26-admin-graph-review.md"),
    _a("admin.graph_review.approve_component", "グラフのノードを承認する", "admin", "graph-review.approve",
       ("POST", "/api/admin/theory-components/{component_id}/approve"), args={"component_id": "str?"},
       precondition="グラフレビューを開いている", manual=_TE + "26-admin-graph-review.md"),
    _a("admin.graph_review.reject_component", "グラフのノードを却下する", "admin", "graph-review.reject",
       ("POST", "/api/admin/theory-components/{component_id}/reject"), args={"component_id": "str?"},
       precondition="グラフレビューを開いている", manual=_TE + "26-admin-graph-review.md"),
    _a("admin.graph_review.chat", "グラフ全体について AI と話す", "admin", "graph-review.graph-chat",
       ("POST", "/api/admin/deliberation/documents/{document_id}/graph-sessions/{session_id}/messages"),
       args={"content": "str", "document_id": "str?"}, llm_cost="product",
       extra_api=(("POST", "/api/admin/deliberation/documents/{document_id}/graph-sessions"),),
       precondition="グラフレビューを開いている", manual=_TE + "26-admin-graph-review.md#graph-chat",
       note="セッションが無ければ先に graph-sessions を開く"),
    _a("admin.course_builder.session_create", "コースビルダーで新しいセッションを作る", "admin",
       "course-builder.new-session-btn", ("POST", "/api/admin/course-builder/sessions"),
       args={"title": "str?"}, manual=_TE + "12-admin-course-builder.md"),
    _a("admin.course_builder.chat", "コースビルダーで AI と対話する", "admin", "course-builder.send-btn",
       ("POST", "/api/admin/course-builder/chat"),
       args={"message": "str", "selected_material_ids": "list[str]?"}, llm_cost="product",
       manual=_TE + "12-admin-course-builder.md"),
    _a("admin.course_builder.register", "承認してコースを登録する", "admin", "course-builder.approve-btn",
       ("POST", "/api/learning/courses"), args={"title": "str?"},
       extra_api=(("PUT", "/api/admin/course-builder/sessions/{session_id}"),),
       precondition="コースの下書きができている", manual=_TE + "12-admin-course-builder.md"),
    _a("admin.course.visibility", "コースを公開する（開示範囲を変える）", "admin",
       "course-management.visibility-apply", ("PUT", "/api/admin/courses/{course_id}/visibility"),
       args={"visibility": "str", "group_id": "str?", "course_id": "str?"},
       manual=_TE + "13-admin-course-management.md#visibility-apply"),
    _a("admin.release_review.placements", "リリース前の確認で論文の位置づけを見る", "admin",
       "release-review.modal", ("GET", "/api/admin/landscape/courses/{course_id}/placements"),
       args={"course_id": "str?"}, manual=_TE + "13-admin-course-management.md#release-review-modal"),
    _a("admin.release_review.accept", "リリース前の確認で「この配置で次へ」を押す", "admin", "release-review.next",
       ("POST", "/api/admin/landscape/courses/{course_id}/placements/accept"), args={"course_id": "str?"},
       precondition="リリース前の確認で位置づけを見ている",
       manual=_TE + "13-admin-course-management.md#release-review-next"),
    _a("admin.atlas_binding.propose", "学習マップの割り当て候補を出す", "admin",
       "course-management.atlas-binding-open", ("POST", "/api/admin/courses/{course_id}/atlas-binding/propose"),
       args={"course_id": "str?"}, manual=_TE + "13-admin-course-management.md#atlas-binding-open"),
    _a("admin.atlas_binding.save", "学習マップの割り当てを保存する", "admin",
       "course-management.atlas-binding-open", ("PUT", "/api/admin/courses/{course_id}/atlas-binding"),
       args={"cartridge_id": "str?", "course_id": "str?"}, precondition="割り当て候補を見ている",
       manual=_TE + "13-admin-course-management.md#atlas-binding-open",
       note="cartridge_id を省くと直前の propose の第 1 候補を使う"),
    _a("admin.lecture_studio.scripts", "原稿スタジオで原稿を確かめる", "admin", "lecture-studio.course-select",
       ("GET", "/api/admin/courses/{course_id}/lecture-scripts"), args={"course_id": "str?"},
       manual=_TE + "14-admin-lecture-studio.md#overview"),
    _a("admin.users.create_student", "学生アカウントを作る", "admin", "students.user-list",
       ("POST", "/api/admin/users/student"), args={"username": "str", "email": "str?", "password": "str?"},
       manual=_TE + "16-admin-students.md#student-form"),
    _a("admin.groups.create", "グループを作る", "admin", "groups.create-form",
       ("POST", "/api/groups"), args={"name": "str", "description": "str?"},
       manual=_TE + "15-admin-groups.md"),
    _a("admin.groups.add_member", "グループにメンバーを招待する", "admin", "groups.invite-btn",
       ("POST", "/api/groups/{group_id}/members"), args={"group_id": "str?", "username": "str"},
       manual=_TE + "15-admin-groups.md"),
    _a("admin.copilot.chat", "操作アシスタントに聞く", "admin", "header.copilot",
       ("POST", "/api/admin/assistant/chat"), args={"message": "str", "tab": "str?"}, llm_cost="product",
       manual=_TE + "10-admin-common.md#copilot"),
    _a("admin.help.inspect", "「❓ 使い方」で調べる", "admin", "header.inspect",
       ("GET", "/api/admin/assistant/help/ui-anchors"), args={"anchor_id": "str?"},
       extra_api=(("POST", "/api/admin/assistant/help/ui-anchor-events"),),
       manual=_TE + "10-admin-common.md"),
    _a("admin.discuss_opening_review.list", "議論のきっかけのレビュー一覧を見る", "admin",
       "deliberation.discussion-seed-group",
       ("GET", "/api/admin/documents/{document_id}/element-explanations"), args={"document_id": "str?"},
       manual=_TE + "24-admin-deliberation.md#discussion-seed-group"),
    # --- §18.2 論理構造を辿る行為（2026-09-30） ---
    _a("admin.paper_layer.view", "グラフレビューで「論文の順」に切り替える", "admin", "graph-review.paper-view",
       ("GET", "/api/admin/documents/{document_id}/paper-layer"), args={"document_id": "str?"},
       precondition="グラフレビューを開いている", manual=_TE + "26-admin-graph-review.md#paper-view"),
    _a("admin.theory_modules.view", "グラフレビューで理論モジュールを見る", "admin", "graph-review.module-view",
       ("GET", "/api/admin/documents/{document_id}/theory-modules"), args={"document_id": "str?"},
       precondition="グラフレビューを開いている", manual=_TE + "26-admin-graph-review.md#theory-modules"),
    _a("admin.theory_modules.related", "同じ構造のモジュールを持つ論文を見る", "admin", "graph-review.module-member",
       ("GET", "/api/admin/documents/{document_id}/theory-modules/related"), args={"document_id": "str?"},
       precondition="理論モジュールの詳細を開いている", manual=_TE + "26-admin-graph-review.md#theory-modules",
       note="画面は外枠モジュールの詳細ペインで取得する。専用の data-ui-anchor は無いので詳細ペインの部品に寄せた"),
    _a("admin.graph_review.node_chat", "選んだノードについて AI と話す", "admin", "graph-review.open-deliberation",
       ("POST", "/api/admin/deliberation/sessions/{session_id}/messages"),
       args={"message": "str", "component_id": "str?", "document_id": "str?", "node_id": "str?"},
       extra_api=(("POST", "/api/admin/deliberation/sessions"),), llm_cost="product",
       precondition="グラフレビューでノードを選んでいる", manual=_TE + "26-admin-graph-review.md#open-deliberation",
       note="セッションが無ければ先に W層セッションを作る（element_type=theory_component）"),
    _a("admin.deliberation.overview", "要素を深く検討する（内訳を見る）", "admin", "deliberation.decomposition",
       ("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/overview"),
       args={"element_type": "str?", "element_id": "str?", "document_id": "str?"},
       precondition="要素を選んでいる", manual=_TE + "24-admin-deliberation.md#decomposition",
       note="§18.2 の deliberation.open は data-ui-anchor に無いので、overview が描く内訳区画に寄せた"),
    _a("admin.graph_review.approve_claim", "根拠の主張を承認する", "admin", "graph-review.claim-approve",
       ("POST", "/api/admin/claims/{claim_id}/review"), args={"claim_id": "str?", "review_status": "str?"},
       precondition="グラフレビューでノードの根拠を見ている", manual=_TE + "26-admin-graph-review.md#claim-approve",
       note="review_status は teacher_approved（既定）| rejected | needs_revision | teacher_review_required"),
    _a("admin.seminar_brief.view", "ゼミ前ブリーフを開く", "admin", "materials.row-seminar-brief",
       ("GET", "/api/admin/documents/{document_ref}/seminar-brief"), args={"document_ref": "str?"},
       manual=_TE + "11-admin-materials.md#seminar-brief-open"),
]

REGISTRY: dict[str, Action] = {a.id: a for a in _LEARNING + _ADMIN}


def get(action_id: str) -> Optional[Action]:
    """行為 id から ``Action`` を返す（未登録は None）。"""
    return REGISTRY.get(action_id)


def actions_for(screen: Screen) -> list[Action]:
    """画面ごとの行為（``auth.login`` は runner 専用なので除く）。"""
    return [a for a in REGISTRY.values() if a.screen == screen and a.id != "auth.login"]
