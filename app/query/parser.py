"""Parser 是递归下降实现，函数层级直接对应布尔优先级：OR → AND → NOT → primary。
它只构建 AST 和统计复杂度，不知道 OpenSearch 字段名或具体 DSL 形状。
"""

from app.core.exceptions import QueryComplexityError, QuerySyntaxError
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.budget import DEFAULT_QUERY_BUDGET, QueryBudget
from app.query.tokenizer import tokenize
from app.query.tokens import Token, TokenType


class Parser:
    """把 tokenizer 输出解析为受预算约束的 QueryNode。

    Parser 只拥有语法优先级和 AST 资源计数；字段白名单、OpenSearch 字段名和具体
    查询语义仍由 DSL builder 负责，因此它可以被独立测试为纯解析器。
    """

    def __init__(self, tokens: list[Token], budget: QueryBudget):
        self.tokens = tokens
        self.budget = budget
        self.index = 0
        self.ast_nodes = 0
        self.boolean_clauses = 0
        self.nesting_depth = 0

    def parse(self) -> QueryNode:
        """解析一整段 token 流，并拒绝任何没有被语法消费的尾部输入。"""
        # 顶层必须消费到 EOF；多余 token 通常意味着缺失运算符、括号或字段值。
        node = self._parse_or()
        current = self._current()
        if current.type is not TokenType.EOF:
            if current.type is TokenType.NOT:
                raise QuerySyntaxError("q 查询语法错误：NOT 位置错误")
            raise QuerySyntaxError(f"q 查询语法错误：无法解析 {current.value}")
        return node

    def _parse_or(self) -> QueryNode:
        # 最低优先级：a AND b OR c 会先形成 (a AND b) OR c。
        node = self._parse_and()
        while self._match(TokenType.OR):
            if self._current().type in {TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：OR 后缺少查询条件")
            node = self._node(
                OrNode(left=node, right=self._parse_and()),
                boolean=True,
            )
        return node

    def _parse_and(self) -> QueryNode:
        # AND 高于 OR；显式要求操作数，拒绝连续 AND/OR 和缺少右侧条件。
        if self._current().type is TokenType.AND:
            raise QuerySyntaxError("q 查询语法错误：AND 位置错误")
        node = self._parse_not()
        while self._match(TokenType.AND):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：AND 后缺少查询条件")
            node = self._node(
                AndNode(left=node, right=self._parse_not()),
                boolean=True,
            )
        return node

    def _parse_not(self) -> QueryNode:
        # NOT 可连续嵌套，但每层都进入 nesting budget，防止深递归消耗栈/CPU。
        if self._match(TokenType.NOT):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：NOT 后缺少查询条件")
            self._enter_nesting()
            try:
                child = self._parse_not()
            finally:
                self._leave_nesting()
            return self._node(NotNode(child=child), boolean=True)
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        """解析最高优先级的原子表达式或带括号的子表达式。

        括号和字段值分组都会暂时占用 nesting budget；调用方只会在当前 token 可以
        开始一个查询节点时进入这里，其他 token 统一转换为位置错误。
        """
        # primary 是括号、字段、短语和裸词的落点；其他 token 在这里报位置错误。
        current = self._current()
        if current.type is TokenType.LPAREN:
            self._advance()
            self._enter_nesting()
            try:
                node = self._parse_or()
                self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            finally:
                self._leave_nesting()
            return node
        if current.type is TokenType.FIELD:
            return self._parse_field_or_range()
        if current.type is TokenType.PHRASE:
            self._advance()
            if current.value == "":
                raise QuerySyntaxError("q 查询语法错误：查询式不能为空")
            return self._node(PhraseNode(current.value))
        if current.type is TokenType.WORD:
            self._advance()
            return self._node(WordNode(current.value))
        raise QuerySyntaxError(f"q 查询语法错误：{current.value or current.type.value} 位置错误")

    def _parse_field_or_range(self) -> QueryNode:
        """解析 ``field:value``、``field:(...)`` 或 ``field:[start TO end]``。

        方括号只表达闭区间，圆括号只表达字段内布尔分组，避免两种语义在 AST 中被
        混用；字段名称本身暂不校验，留给 DSL builder 的白名单边界处理。
        """
        # 字段值允许单值、短语或括号表达式；方括号专用于闭区间，避免与布尔分组混淆。
        field = self._advance().value
        self._consume(TokenType.COLON, f"q 查询语法错误：字段 {field} 的值不能为空")

        if self._match(TokenType.LBRACKET):
            start = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少起始值").value
            self._consume(TokenType.TO, "q 查询语法错误：范围表达式缺少 TO")
            end = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少结束值").value
            self._consume(TokenType.RBRACKET, "q 查询语法错误：范围表达式缺少右方括号")
            return self._node(RangeQuery(field=field, start=start, end=end))

        if self._match(TokenType.LPAREN):
            if self._current().type is TokenType.RPAREN:
                raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")
            self._enter_nesting()
            try:
                value = self._parse_or()
                self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            finally:
                self._leave_nesting()
            return self._node(FieldQuery(field=field, value=value))

        current = self._current()
        if current.type is TokenType.WORD:
            self._advance()
            value = self._node(WordNode(current.value))
            return self._node(FieldQuery(field=field, value=value))
        if current.type is TokenType.PHRASE:
            self._advance()
            value = self._node(PhraseNode(current.value))
            return self._node(FieldQuery(field=field, value=value))
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    def _match(self, token_type: TokenType) -> bool:
        # match 是可选消费，适合处理连续 OR/AND/NOT；必需语法使用 _consume。
        if self._current().type is token_type:
            self._advance()
            return True
        return False

    def _consume(self, token_type: TokenType, message: str) -> Token:
        # _consume 统一使用调用方给出的稳定中文错误，避免把内部 token 结构暴露给 API。
        current = self._current()
        if current.type is not token_type:
            raise QuerySyntaxError(message)
        return self._advance()

    def _advance(self) -> Token:
        # 所有索引前进都集中在这里，方便保证 current/consume 的行为一致。
        current = self._current()
        self.index += 1
        return current

    def _current(self) -> Token:
        return self.tokens[self.index]

    def _node(self, node: QueryNode, *, boolean: bool = False) -> QueryNode:
        # 每创建一个 AST 节点都计数；boolean 额外统计 AND/OR/NOT，两个预算分别
        # 控制内存结构大小和布尔展开可能带来的 DSL/查询成本。
        self.ast_nodes += 1
        if self.ast_nodes > self.budget.max_ast_nodes:
            raise QueryComplexityError
        if boolean:
            self.boolean_clauses += 1
            if self.boolean_clauses > self.budget.max_boolean_clauses:
                raise QueryComplexityError
        return node

    def _enter_nesting(self) -> None:
        # 先判断再递增，确保失败时不会留下错误的深度状态。
        if self.nesting_depth >= self.budget.max_nesting_depth:
            raise QueryComplexityError
        self.nesting_depth += 1

    def _leave_nesting(self) -> None:
        # 与 _enter_nesting 成对，finally 负责在语法异常时也恢复计数。
        self.nesting_depth -= 1


def parse_query(
    q: str,
    budget: QueryBudget = DEFAULT_QUERY_BUDGET,
) -> QueryNode:
    """以同一份预算完成词法分析和语法分析，返回不可变查询 AST。"""
    # 对外只暴露一个 tokenizer+parser 入口，调用方不会忘记使用同一份预算。
    return Parser(tokenize(q, budget=budget), budget=budget).parse()
