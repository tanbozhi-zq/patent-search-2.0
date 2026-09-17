"""验证引证应用服务的仓储调用、映射、空标识与异常传播边界。"""

import pytest

from app.core.exceptions import InvalidPatentIdentifierError, PatentNotFoundError
from app.services.citation_service import CitationService


class FakeRepository:
    def __init__(self, hit=None, error=None):
        self.hit = hit
        self.error = error
        self.identifier = None

    def get_patent_by_identifier(self, identifier):
        self.identifier = identifier
        if self.error:
            raise self.error
        return self.hit


def test_citation_service_returns_mapped_citations():
    repository = FakeRepository(
        {
            "_source": {
                "patent_id": "cn-1",
                "ReferencesCitedRaw": "非专利文献",
            }
        }
    )
    service = CitationService(repository=repository)

    result = service.get_citations(" cn-1 ")

    assert repository.identifier == "cn-1"
    assert result["patent_id"] == "cn-1"
    assert result["non_patent_references"] == ["非专利文献"]


def test_citation_service_raises_not_found():
    service = CitationService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_citations("missing")


def test_citation_service_rejects_empty_patent_id():
    service = CitationService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_citations(" ")


def test_citation_service_does_not_hide_programming_error():
    service = CitationService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        service.get_citations("cn-1")
