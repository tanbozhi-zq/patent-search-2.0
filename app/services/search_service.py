import struct
from typing import Optional

from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError
from app.mappings.result_mapper import map_search_response
from app.query.dsl_builder import build_search_dsl, build_target_rank_dsl
from app.repositories.opensearch_repo import OpenSearchRepository
from app.schemas.search import SearchRequest, TargetRankRequest


class SearchService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def search(self, request: SearchRequest) -> dict:
        body = build_search_dsl(request)
        try:
            raw = self.repository.search(body)
        except QuerySyntaxError:
            raise
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc
        return map_search_response(raw, page=request.page, page_size=request.page_size)

    def target_rank(self, request: TargetRankRequest) -> dict:
        try:
            # Parse the query before resolving the target so syntax errors are deterministic.
            base_dsl = build_search_dsl(
                SearchRequest(q=request.q, ds=request.ds, sort=request.sort, page=1, page_size=1)
            )
            identifier = request.target_identifier.strip()
            identifier_field, target, match_count = self.repository.find_target(identifier)
            if target is None:
                return {"status": "target_not_found", "in_results": False, "rank": None, "tied_count": 0,
                        "sort_value": None, "target": None}
            if match_count > 1:
                return {"status": "ambiguous_target", "in_results": False, "rank": None, "tied_count": match_count,
                        "sort_value": None, "target": self._target_summary(target)}

            dsl = build_target_rank_dsl(request, identifier_field, target)
            target_in_query = self.repository.find_in_query(
                base_dsl["query"], dsl["identity_clause"]
            )
            in_results = target_in_query is not None
            summary = self._target_summary(target)
            if not in_results:
                return {"status": "not_in_results", "in_results": False, "rank": None, "tied_count": 0,
                        "sort_value": None, "target": summary}

            dsl = build_target_rank_dsl(request, identifier_field, target_in_query)
            if dsl["relevance_sort"]:
                target_score = float(dsl["sort_value"])
                better = self.repository.count_with_min_score(
                    dsl["base_query"], _next_float32(target_score)
                )
                not_lower = self.repository.count_with_min_score(dsl["base_query"], target_score)
                tied = max(not_lower - better - 1, 0)
            else:
                better = self.repository.count(dsl["better_query"])
                tied = self.repository.count(dsl["tied_query"])

            return {"status": "matched", "in_results": True, "rank": better + 1, "tied_count": tied,
                    "sort_value": dsl["sort_value"], "target": summary}
        except QuerySyntaxError:
            raise
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc

    @staticmethod
    def _target_summary(hit: dict) -> dict:
        source = hit.get("_source", {})
        return {
            "patent_id": str(source.get("patent_id") or ""),
            "documentNumber": str(source.get("PublicationNumber") or ""),
            "title": str(source.get("Title") or ""),
        }


def _next_float32(value: float) -> float:
    bits = struct.unpack("!I", struct.pack("!f", value))[0]
    return struct.unpack("!f", struct.pack("!I", bits + 1))[0]
