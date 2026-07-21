"""Real-archive regression guard for vo_count_observations.

Unlike test_vo_count_observations.py (fully faked backends), this exercises the
REAL resolver + TapClient end-to-end against recorded HTTP, locking in that each
archive's curated `count_target` produces a COUNT query the live service
actually accepts. It covers the three geometry dialects that the fake tests can
only assert as strings:

  * datalab — q3c_radial_query on nsc_dr2.object (sync)
  * alma    — INTERSECTS(s_region) + COUNT(DISTINCT member_ous_uid) (sync)

The CONTAINS/POINT dialect (gaia / eso / nrao) is NOT recorded here: every
archive that uses it is currently async or unavailable for a *deterministic*
sync cassette — gaia's ESAC endpoint was in scheduled downtime at record time,
and eso/nrao promote a spatial obscore COUNT to async (a full-table scan that
outruns the sync budget). That dialect stays covered offline by the pure-
predicate, recipe, and faked-backend tests; its live/async path is smoke-checked
out-of-band. NRAO's async count is likewise excluded for non-determinism.

Re-record with:  uv run pytest --record-mode=once -k count_observations_live
"""

import pytest
from fastmcp import Client

# (target, waveband, archive-override, expected chosen archive, ADQL substring).
CASES = {
    "datalab_q3c": ("M87", "optical", None, "datalab", "q3c_radial_query"),
    "alma_intersects": ("NGC 253", "millimeter", None, "alma", "INTERSECTS(CIRCLE("),
}


@pytest.mark.vcr(
    # datalab/alma calls include a Sesame resolve + one TAP POST; body matching
    # keeps multi-request cassettes from cross-matching on a shared path.
    match_on=["method", "scheme", "host", "port", "path", "query", "body"],
)
@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
async def test_count_against_live_archive(mcp_server, case):
    target, waveband, archive, expected_archive, adql_needle = CASES[case]
    args = {"target": target, "radius_deg": 0.05}
    if waveband is not None:
        args["waveband"] = waveband
    if archive is not None:
        args["archive"] = archive
    async with Client(mcp_server) as client:
        result = await client.call_tool("vo_count_observations", args)
    out = result.structured_content

    # A completed count: explicit ok status + a real integer.
    assert out["status"] == "ok"
    assert isinstance(out["count"], int)
    assert out["count"] >= 0

    # The curated archive was selected and its dialect-correct ADQL was run.
    plan = out["plan"]
    assert plan["chosen_archive"] == expected_archive
    assert adql_needle in plan["adql"]
    assert plan["endpoint"].startswith("http")

    # ALMA counts distinct datasets, not spectral-window rows.
    if case == "alma_intersects":
        assert plan["count_expr"] == "COUNT(DISTINCT member_ous_uid)"

    # Coordinates were resolved/coerced.
    assert out["resolved"]["frame"] == "icrs"
    assert isinstance(out["resolved"]["ra"], float)
