import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, RangeQuery, WordNode
from app.query.parser import parse_query


def test_parses_simple_field_query():
    ast = parse_query("title:(阀门)")

    assert ast == FieldQuery(field="title", value=WordNode(value="阀门"))


def test_parses_phrase_or_inside_field_query():
    ast = parse_query('tscd:("均衡" OR "平衡")')

    assert ast == FieldQuery(
        field="tscd",
        value=OrNode(left=PhraseNode("均衡"), right=PhraseNode("平衡")),
    )


def test_and_has_higher_precedence_than_or():
    ast = parse_query("title:(A) OR title:(B) AND ipc:H02M")

    assert isinstance(ast, OrNode)
    assert isinstance(ast.right, AndNode)


def test_parses_not_before_field_query():
    ast = parse_query("ipc:H02M AND NOT tscd:(均衡)")

    assert isinstance(ast, AndNode)
    assert isinstance(ast.right, NotNode)
    assert ast.right.child == FieldQuery(field="tscd", value=WordNode(value="均衡"))


def test_parses_date_range():
    assert parse_query("ad:[2020-01-01 TO 2020-12-31]") == RangeQuery(
        field="ad",
        start="2020-01-01",
        end="2020-12-31",
    )


@pytest.mark.parametrize(
    "q,error",
    [
        ("ipc:H02M AND AND tscd:(均衡)", "AND 后缺少查询条件"),
        ("AND tscd:(均衡)", "AND 位置错误"),
        ("tscd:(均衡) OR", "OR 后缺少查询条件"),
        ("tscd:()", "字段 tscd 的值不能为空"),
        ("ipc:", "字段 ipc 的值不能为空"),
        ("ad:[2020-01-01 2020-12-31]", "范围表达式缺少 TO"),
        ("NOT", "NOT 后缺少查询条件"),
        ("tscd:(均衡) NOT", "NOT 位置错误"),
    ],
)
def test_rejects_invalid_syntax(q, error):
    with pytest.raises(QuerySyntaxError, match=error):
        parse_query(q)
