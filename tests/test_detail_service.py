import pytest

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.services.detail_service import DetailService


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


def test_detail_service_returns_mapped_detail():
    repository = FakeRepository(
        {
            "_source": {
                "patent_id": "cn-1",
                "Title": "标题",
                "Requirement": "权利要求",
            }
        }
    )
    service = DetailService(repository=repository)

    result = service.get_detail(" cn-1 ")

    assert repository.identifier == "cn-1"
    assert result["patent_id"] == "cn-1"
    assert result["title"] == "标题"
    assert result["claims"] == "权利要求"


def test_detail_service_raises_not_found():
    service = DetailService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_detail("missing")


def test_detail_service_rejects_empty_patent_id():
    service = DetailService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_detail(" ")


def test_detail_service_wraps_repository_error():
    service = DetailService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(OpenSearchQueryError):
        service.get_detail("cn-1")