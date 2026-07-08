from app.core.exceptions import QuerySyntaxError
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.tokenizer import tokenize
from app.query.tokens import Token, TokenType


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    def parse(self) -> QueryNode:
        node = self._parse_or()
        current = self._current()
        if current.type is not TokenType.EOF:
            if current.type is TokenType.NOT:
                raise QuerySyntaxError("q 查询语法错误：NOT 位置错误")
            raise QuerySyntaxError(f"q 查询语法错误：无法解析 {current.value}")
        return node

    def _parse_or(self) -> QueryNode:
        node = self._parse_and()
        while self._match(TokenType.OR):
            if self._current().type in {TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：OR 后缺少查询条件")
            node = OrNode(left=node, right=self._parse_and())
        return node

    def _parse_and(self) -> QueryNode:
        if self._current().type is TokenType.AND:
            raise QuerySyntaxError("q 查询语法错误：AND 位置错误")
        node = self._parse_not()
        while self._match(TokenType.AND):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：AND 后缺少查询条件")
            node = AndNode(left=node, right=self._parse_not())
        return node

    def _parse_not(self) -> QueryNode:
        if self._match(TokenType.NOT):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：NOT 后缺少查询条件")
            return NotNode(child=self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        current = self._current()
        if current.type is TokenType.LPAREN:
            self._advance()
            node = self._parse_or()
            self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            return node
        if current.type is TokenType.FIELD:
            return self._parse_field_or_range()
        if current.type is TokenType.PHRASE:
            self._advance()
            if current.value == "":
                raise QuerySyntaxError("q 查询语法错误：查询式不能为空")
            return PhraseNode(current.value)
        if current.type is TokenType.WORD:
            self._advance()
            return WordNode(current.value)
        raise QuerySyntaxError(f"q 查询语法错误：{current.value or current.type.value} 位置错误")

    def _parse_field_or_range(self) -> QueryNode:
        field = self._advance().value
        self._consume(TokenType.COLON, f"q 查询语法错误：字段 {field} 的值不能为空")

        if self._match(TokenType.LBRACKET):
            start = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少起始值").value
            self._consume(TokenType.TO, "q 查询语法错误：范围表达式缺少 TO")
            end = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少结束值").value
            self._consume(TokenType.RBRACKET, "q 查询语法错误：范围表达式缺少右方括号")
            return RangeQuery(field=field, start=start, end=end)

        if self._match(TokenType.LPAREN):
            if self._current().type is TokenType.RPAREN:
                raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")
            value = self._parse_or()
            self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            return FieldQuery(field=field, value=value)

        current = self._current()
        if current.type is TokenType.WORD:
            self._advance()
            return FieldQuery(field=field, value=WordNode(current.value))
        if current.type is TokenType.PHRASE:
            self._advance()
            return FieldQuery(field=field, value=PhraseNode(current.value))
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    def _match(self, token_type: TokenType) -> bool:
        if self._current().type is token_type:
            self._advance()
            return True
        return False

    def _consume(self, token_type: TokenType, message: str) -> Token:
        current = self._current()
        if current.type is not token_type:
            raise QuerySyntaxError(message)
        return self._advance()

    def _advance(self) -> Token:
        current = self._current()
        self.index += 1
        return current

    def _current(self) -> Token:
        return self.tokens[self.index]


def parse_query(q: str) -> QueryNode:
    return Parser(tokenize(q)).parse()
