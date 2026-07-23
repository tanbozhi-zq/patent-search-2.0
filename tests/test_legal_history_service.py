import pytest

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.services.legal_history_service import LegalHistoryService


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


def test_legal_history_service_returns_base_response():
    repository = FakeRepository({"_source": {"patent_id": "cn-1"}})
    service = LegalHistoryService(repository=repository)

    result = service.get_legal_history(" cn-1 ")

    assert repository.identifier == "cn-1"
    assert result == {
        "patent_id": "cn-1",
        "transaction_count": 0,
        "transactions": [],
    }


def test_legal_history_service_raises_not_found():
    service = LegalHistoryService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_legal_history("missing")


def test_legal_history_service_rejects_empty_patent_id():
    service = LegalHistoryService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_legal_history(" ")


def test_legal_history_service_wraps_repository_error():
    service = LegalHistoryService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(OpenSearchQueryError):
        service.get_legal_history("cn-1")
