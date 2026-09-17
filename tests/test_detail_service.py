"""验证详情应用服务的按需说明书读取、未命中与异常传播语义。"""

import pytest

from app.core.exceptions import InvalidPatentIdentifierError, PatentNotFoundError
from app.mappings.source_fields import DETAIL_SOURCE_FIELDS, detail_source_fields
from app.services.detail_service import DetailService


class FakeRepository:
    def __init__(self, hit=None, error=None):
        self.hit = hit
        self.error = error
        self.identifier = None
        self.source_fields = None

    def get_patent_by_identifier(self, identifier, source_fields=None):
        self.identifier = identifier
        self.source_fields = source_fields
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
    assert repository.source_fields == DETAIL_SOURCE_FIELDS
    assert result["id"] == "cn-1"
    assert result["title"] == "标题"
    assert result["claims"] == "权利要求"


def test_detail_service_reads_instructions_only_when_requested():
    default_repository = FakeRepository({"_source": {"patent_id": "cn-1"}})
    DetailService(repository=default_repository).get_detail("cn-1")

    description_repository = FakeRepository({"_source": {"patent_id": "cn-1", "Instructions": "说明书"}})
    result = DetailService(repository=description_repository).get_detail("cn-1", include_description=True)

    assert "Instructions" not in DETAIL_SOURCE_FIELDS
    assert default_repository.source_fields == DETAIL_SOURCE_FIELDS
    assert description_repository.source_fields == detail_source_fields(True)
    assert description_repository.source_fields[-1] == "Instructions"
    assert result["description"] == "说明书"


def test_detail_service_raises_not_found():
    service = DetailService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_detail("missing")


def test_detail_service_rejects_empty_patent_id():
    service = DetailService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_detail(" ")


def test_detail_service_does_not_hide_programming_error():
    service = DetailService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        service.get_detail("cn-1")
