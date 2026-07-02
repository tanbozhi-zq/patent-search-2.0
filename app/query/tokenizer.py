from app.core.exceptions import QuerySyntaxError
from app.query.tokens import Token, TokenType


RESERVED = {
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "NOT": TokenType.NOT,
    "TO": TokenType.TO,
}


def tokenize(q: str) -> list[Token]:
    if not q or not q.strip():
        raise QuerySyntaxError("q 查询语法错误：查询式不能为空")

    tokens: list[Token] = []
    i = 0
    length = len(q)

    while i < length:
        char = q[i]

        if char.isspace():
            i += 1
            continue

        if char == '"':
            start = i
            i += 1
            value_chars: list[str] = []
            while i < length and q[i] != '"':
                value_chars.append(q[i])
                i += 1
            if i >= length:
                raise QuerySyntaxError("q 查询语法错误：引号未闭合")
            tokens.append(Token(TokenType.PHRASE, "".join(value_chars), start))
            i += 1
            continue

        single = {
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            ":": TokenType.COLON,
        }.get(char)
        if single:
            tokens.append(Token(single, char, i))
            i += 1
            continue

        if _is_word_char(char):
            start = i
            chars: list[str] = []
            while i < length and _is_word_char(q[i]):
                chars.append(q[i])
                i += 1
            value = "".join(chars)
            token_type = RESERVED.get(value.upper())
            if token_type is not None:
                tokens.append(Token(token_type, value.upper(), start))
                continue

            j = i
            while j < length and q[j].isspace():
                j += 1
            if j < length and q[j] == ":":
                tokens.append(Token(TokenType.FIELD, value, start))
            else:
                tokens.append(Token(TokenType.WORD, value, start))
            continue

        raise QuerySyntaxError(f"q 查询语法错误：无法识别的符号 {char}")

    tokens.append(Token(TokenType.EOF, "", length))
    return tokens


def _is_word_char(char: str) -> bool:
    return char not in {'"', "(", ")", "[", "]", ":", " "} and not char.isspace()
