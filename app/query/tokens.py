from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
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
    type: TokenType
    value: str
    position: int
