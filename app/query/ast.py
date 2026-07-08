from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class WordNode:
    value: str


@dataclass(frozen=True)
class PhraseNode:
    value: str


@dataclass(frozen=True)
class FieldQuery:
    field: str
    value: "QueryNode"


@dataclass(frozen=True)
class RangeQuery:
    field: str
    start: str
    end: str


@dataclass(frozen=True)
class AndNode:
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class OrNode:
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class NotNode:
    child: "QueryNode"


QueryNode = Union[WordNode, PhraseNode, FieldQuery, RangeQuery, AndNode, OrNode, NotNode]
