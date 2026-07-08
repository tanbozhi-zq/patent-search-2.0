import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.tokenizer import tokenize
from app.query.tokens import TokenType


def token_pairs(q: str):
    return [(token.type, token.value) for token in tokenize(q)]


def test_tokenizes_field_parentheses_and_boolean_words():
    assert token_pairs('ipc:H02M AND tscd:("均衡" OR "平衡")') == [
        (TokenType.FIELD, "ipc"),
        (TokenType.COLON, ":"),
        (TokenType.WORD, "H02M"),
        (TokenType.AND, "AND"),
        (TokenType.FIELD, "tscd"),
        (TokenType.COLON, ":"),
        (TokenType.LPAREN, "("),
        (TokenType.PHRASE, "均衡"),
        (TokenType.OR, "OR"),
        (TokenType.PHRASE, "平衡"),
        (TokenType.RPAREN, ")"),
        (TokenType.EOF, ""),
    ]


def test_tokenizes_range_expression():
    assert token_pairs("ad:[2020-01-01 TO 2020-12-31]") == [
        (TokenType.FIELD, "ad"),
        (TokenType.COLON, ":"),
        (TokenType.LBRACKET, "["),
        (TokenType.WORD, "2020-01-01"),
        (TokenType.TO, "TO"),
        (TokenType.WORD, "2020-12-31"),
        (TokenType.RBRACKET, "]"),
        (TokenType.EOF, ""),
    ]


def test_rejects_unclosed_quote():
    with pytest.raises(QuerySyntaxError, match="引号未闭合"):
        tokenize('tscd:("均衡)')


def test_rejects_empty_query():
    with pytest.raises(QuerySyntaxError, match="查询式不能为空"):
        tokenize("   ")
