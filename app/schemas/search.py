from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=1000)
    ds: str = Field(default="cn", pattern="^([Aa][Ll][Ll]|[A-Za-z]{2})$")
    sort: str = Field(
        default="relation",
        pattern="^(relation|rank|relevance|score|!?applicationDate|!?documentDate)$",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    highlight: int = Field(default=0, ge=0, le=1)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class TargetRankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=1000)
    ds: str = Field(default="cn", pattern="^([Aa][Ll][Ll]|[A-Za-z]{2})$")
    sort: str = Field(
        default="relation",
        pattern="^(relation|rank|relevance|score|!?applicationDate|!?documentDate)$",
    )
    target_identifier: str = Field(min_length=1, max_length=200)
