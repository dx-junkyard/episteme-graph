"""ペルソナの document_id 引数は UUID のときだけ採用する（第 26 周・sim-sandbox E-0006）。"""

from types import SimpleNamespace

from uxsim.runner.actions_exec import _doc_arg


def _ctx(**args):
    return SimpleNamespace(args=args)


def test_uuid_argument_is_used():
    assert _doc_arg(_ctx(document_id="1771872a-a372-4d3b-b8e9-40390c7344d2")) == "1771872a-a372-4d3b-b8e9-40390c7344d2"


def test_title_fragment_is_ignored():
    # 教員ペルソナが material_hint の語を document_id に入れた事例
    assert _doc_arg(_ctx(document_id="Cepheus B")) == ""


def test_missing_argument_is_empty():
    assert _doc_arg(_ctx()) == ""
