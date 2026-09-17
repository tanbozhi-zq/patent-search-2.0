"""tokenizer 采用小型手写扫描器，明确控制引号、括号、字段冒号和保留字；它不
做隐式 AND，也不接受通配符/转义语法，从源头保证 parser 的输入范围。
"""

from app.core.exceptions import QueryComplexityError, QuerySyntaxError
from app.query.budget import DEFAULT_QUERY_BUDGET, QueryBudget
from app.query.tokens import Token, TokenType


RESERVED = {
    # 保留字按大小写不敏感匹配，但 Token.value 统一保存为大写，便于 parser 判断。
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "NOT": TokenType.NOT,
    "TO": TokenType.TO,
}


def tokenize(
    q: str,
    budget: QueryBudget = DEFAULT_QUERY_BUDGET,
) -> list[Token]:
    """将用户查询文本切分为 Parser 可消费的有界 Token 序列。

    该扫描器保留每个 token 的源位置，只负责词法层的引号和结构字符规则；不推断
    隐式布尔关系，也不验证字段是否受支持。语法字符或资源预算违规会在这里失败。
    """
    # 先做空查询和字符预算，再逐字符扫描；EOF 最后追加，仅供 parser 判断输入结束。
    if not q or not q.strip():
        raise QuerySyntaxError("q 查询语法错误：查询式不能为空")
    budget.validate_query_length(q)

    tokens: list[Token] = []

    def append_token(token: Token) -> None:
        # 预算检查放在所有 token 入口的共同 helper 中，避免某个符号分支忘记限额。
        if len(tokens) >= budget.max_tokens:
            raise QueryComplexityError
        tokens.append(token)

    i = 0
    length = len(q)

    while i < length:
        char = q[i]

        if char.isspace():
            i += 1
            continue

        if char == '"':
            # 引号内不再识别括号、冒号或保留字，全部作为短语文本；当前语法不支持
            # 转义引号，因此遇到下一个双引号就结束短语。
            start = i
            i += 1
            value_chars: list[str] = []
            while i < length and q[i] != '"':
                value_chars.append(q[i])
                i += 1
            if i >= length:
                raise QuerySyntaxError("q 查询语法错误：引号未闭合")
            append_token(Token(TokenType.PHRASE, "".join(value_chars), start))
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
            # 这些字符是独立语法 token，不能作为普通词的一部分。
            append_token(Token(single, char, i))
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
                append_token(Token(token_type, value.upper(), start))
                continue

            j = i
            # 允许字段名和冒号之间有空白，例如 title : 阀门；FIELD 只在确实
            # 紧跟冒号时生成，普通词里的 AND/OR/NOT 才会成为布尔 token。
            while j < length and q[j].isspace():
                j += 1
            if j < length and q[j] == ":":
                append_token(Token(TokenType.FIELD, value, start))
            else:
                append_token(Token(TokenType.WORD, value, start))
            continue

        raise QuerySyntaxError(f"q 查询语法错误：无法识别的符号 {char}")

    # EOF 不计入 max_tokens，因为它不是用户输入的语法单元。
    tokens.append(Token(TokenType.EOF, "", length))
    return tokens


def _is_word_char(char: str) -> bool:
    # 除结构字符和空白外的字符都留给上层校验；日期、IPC 斜杠和中文无需在
    # tokenizer 中分别列举。
    return char not in {'"', "(", ")", "[", "]", ":", " "} and not char.isspace()
