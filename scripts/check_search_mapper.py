from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter
from app.mappings.result_mapper import map_search_response


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}\nexpected={expected!r}\nactual={actual!r}")


def mapped_record(source: dict) -> dict:
    raw = {
        "took": 1,
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_score": 1.5,
                    "_source": {
                        "patent_id": "cn-test",
                        "ApplicationNumber": "CN202411108082.1",
                        "PublicationNumber": "CN119188170B",
                        "Title": "测试标题",
                        "Abstract": "测试摘要",
                        "Applicant": "测试申请人",
                        "IPC": "H02M",
                        **source,
                    },
                }
            ],
        },
    }
    return map_search_response(raw, page=1, page_size=10)["records"][0]


def main() -> int:
    record = mapped_record(
        {
            "MainClaim": "主权利要求",
            "IndependentClaims": "独立权利要求",
            "IndependentClaimsCN": "中文独立权利要求",
            "Requirement": "完整权利要求书",
        }
    )
    assert_equal(record["mainClaim"], "主权利要求", "mainClaim must come from MainClaim")
    assert_equal(record["main_claim"], "主权利要求", "main_claim must alias MainClaim")
    assert_equal(
        record["independentClaims"],
        "独立权利要求",
        "independentClaims must come from IndependentClaims",
    )
    assert_equal(
        record["independent_claims"],
        "独立权利要求",
        "independent_claims must alias IndependentClaims",
    )

    no_global_independent = mapped_record(
        {
            "MainClaim": "主权利要求",
            "IndependentClaimsCN": "中文独立权利要求",
            "Requirement": "完整权利要求书",
        }
    )
    assert_equal(
        no_global_independent["independentClaims"],
        "",
        "missing IndependentClaims must not fall back to IndependentClaimsCN/MainClaim/Requirement",
    )
    assert_equal(
        no_global_independent["independent_claims"],
        "",
        "missing IndependentClaims snake_case alias must stay empty",
    )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(use_self_hosted=True),
        client=None,
    )
    tool_record = adapter._map_search_record(record)
    assert_equal(tool_record["main_claim"], "主权利要求", "tool record must pass through main_claim")
    assert_equal(
        tool_record["independent_claims"],
        "独立权利要求",
        "tool record must pass through independent_claims",
    )

    print("search mapper checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
