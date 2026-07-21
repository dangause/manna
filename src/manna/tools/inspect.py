"""Table-inspection facade: vo_inspect_table.

"What's actually in table T?" in one call — the real column list + curated
value-enums/notes + a best-effort sample of rows. Columns and curated facts
come from reliable metadata (tap_schema.columns + the schema KB); the sample is
a soft-fail extra (an archive that rejects an unfiltered read just yields
sample_status='error', never a hard failure). A convenience layer over
vo_schema_describe; drop to it + vo_tap_query for full control.
"""

from typing import Annotated

from pydantic import Field

from manna.archives._endpoints import by_short_name
from manna.archives._knowledge import lookup_schema, schema_to_dict
from manna.backends.tap import TapClient
from manna.config import get_settings
from manna.errors import ToolExecutionError, ValidationError, wrap_tool_errors
from manna.tools._constants import _ERROR_DOCSTRING
from manna.tools.schema import _column_recipe, _fetch_columns

_tap: TapClient | None = None


def _get_tap() -> TapClient:
    global _tap
    if _tap is None:
        _tap = TapClient(sync_timeout_seconds=get_settings().tap_sync_timeout_seconds)
    return _tap


def _fetch_sample(endpoint: str, table: str, n: int) -> tuple[list[dict], str]:
    """Best-effort TOP-n sample. Returns (rows, status)."""
    adql = f"SELECT TOP {n} * FROM {table}"
    try:
        t = _get_tap().query(endpoint=endpoint, adql=adql, maxrec=n)
    except ToolExecutionError:
        return [], "error"
    names = t.colnames
    return [{c: _jsonify(row[c]) for c in names} for row in t], "ok"


def _jsonify(v):
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return v.item()
    except Exception:  # noqa: BLE001
        pass
    return v


@wrap_tool_errors
def vo_inspect_table(
    table: Annotated[
        str,
        Field(
            description=(
                "Fully qualified table name (e.g. 'nsc_dr2.object', "
                "'tap_schema.obscore', 'ivoa.obscore')."
            ),
            examples=["nsc_dr2.object", "tap_schema.obscore"],
        ),
    ],
    archive: Annotated[
        str | None,
        Field(
            description="Archive short_name. If omitted, inferred from curated schema knowledge.",
            examples=["datalab", "nrao"],
        ),
    ] = None,
    sample_rows: Annotated[
        int,
        Field(
            ge=0,
            le=20,
            description="How many example rows to sample (SELECT TOP n *). 0 disables.",
        ),
    ] = 5,
) -> dict:
    """Columns + curated enums/notes + a sample of rows for one table, in one call.

    Returns the real column list (name + datatype), curated `value_enums`,
    `notes`, and `sample_rows` (best-effort — `sample_status` ∈ {ok, error,
    disabled}). Reliable metadata is always returned even if the sample fails
    (some archives reject unfiltered reads). Soft-fails (`known: false` + hint)
    when the table/archive can't be identified.
    """
    table_clean = table.strip()
    if not table_clean:
        raise ValidationError(message="'table' must be non-empty (fully qualified).")

    # Resolve owning archive: explicit, else via curated schema lookup across actives.
    arch_name = archive.strip() if archive else None
    known = by_short_name(arch_name) if arch_name else None
    schema = lookup_schema(archive=arch_name, table=table_clean) if arch_name else None

    if known is None or known.tap_url is None:
        payload = {
            "known": False,
            "archive": arch_name,
            "table": table_clean,
            "hint": (
                "Could not identify a TAP endpoint for this table. Pass an "
                "explicit `archive` (see vo_archive_list) or use "
                "vo_registry_search to locate the service."
            ),
        }
        return payload

    payload: dict = {
        "known": schema is not None,
        "archive": known.short_name,
        "table": table_clean,
    }
    if schema is not None:
        payload.update(schema_to_dict(schema))

    columns = _fetch_columns(known.tap_url, table_clean, tap=_get_tap())
    if columns is None:
        payload["column_list_recipe"] = _column_recipe(table_clean)
    else:
        payload["columns"] = columns

    if sample_rows == 0:
        payload["sample_rows"], payload["sample_status"] = [], "disabled"
    else:
        payload["sample_rows"], payload["sample_status"] = _fetch_sample(
            known.tap_url, table_clean, sample_rows
        )

    return payload


vo_inspect_table.__doc__ = (vo_inspect_table.__doc__ or "") + _ERROR_DOCSTRING
