"""tokenizer 只负责把字符流切成有限种 token；它不判断字段是否支持，也不构建
布尔结构，这些语义分别由 parser 和 dsl_builder 处理。
"""

from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    """检索词法器可输出的有限 token 种类，包含仅供 parser 使用的 EOF 哨兵。"""

    # FIELD 表示“后面紧跟冒号”的字段名；WORD/PHRASE 是值。EOF 是内部哨兵，
    # 不计入用户可配置的 token 预算。
    WORD = "WORD"
    PHRASE = "PHRASE"
    FIELD = "FIELD"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    TO = "TO"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LBRACKET = "LBRACKET"
    RBRACKET = "RBRACKET"
    COLON = "COLON"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    """带原始文本和字符位置的不可变词法单元，供 parser 构造有定位能力的错误。"""

    # position 只用于将来定位错误和调试，value 保留原始文本（保留字段大小写）。
    type: TokenType
    value: str
    position: int
