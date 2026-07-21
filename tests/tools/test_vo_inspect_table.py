import pytest
from astropy.table import Table

import manna.tools.inspect as inspect_mod


class _Tap:
    def __init__(self, *, cols, sample=None, sample_exc=None):
        self._cols = cols
        self._sample = sample
        self._sample_exc = sample_exc

    def query(self, *, endpoint, adql, maxrec=10_000):
        if adql.startswith("SELECT column_name"):
            return self._cols
        if self._sample_exc:
            raise self._sample_exc
        return self._sample


@pytest.fixture
def known_datalab(monkeypatch):
    # by_short_name -> archive with tap_url; lookup_schema -> curated schema dict
    from manna.archives._model import Archive

    arch = Archive(
        short_name="datalab",
        display_name="Data Lab",
        host_substrings=("d.x",),
        tap_url="http://d/tap",
        waveband="optical",
    )
    monkeypatch.setattr(inspect_mod, "by_short_name", lambda n: arch if n == "datalab" else None)
    return arch


def test_inspect_returns_columns_enums_and_sample(known_datalab, monkeypatch):
    cols = Table({"column_name": ["ra", "dec"], "datatype": ["double", "double"]})
    sample = Table({"ra": [187.7], "dec": [12.4]})
    monkeypatch.setattr(inspect_mod, "_get_tap", lambda: _Tap(cols=cols, sample=sample))
    monkeypatch.setattr(
        inspect_mod, "lookup_schema", lambda *, archive, table: None
    )  # no curated notes is fine

    out = inspect_mod.vo_inspect_table(table="nsc_dr2.object", archive="datalab", sample_rows=1)

    assert {"name": "ra", "datatype": "double"} in out["columns"]
    assert out["sample_status"] == "ok"
    assert out["sample_rows"] and out["sample_rows"][0]["ra"] == pytest.approx(187.7)


def test_inspect_sample_soft_fails_on_error(known_datalab, monkeypatch):
    from manna.errors import ArchiveError

    cols = Table({"column_name": ["s_ra"], "datatype": ["double"]})
    monkeypatch.setattr(
        inspect_mod,
        "_get_tap",
        lambda: _Tap(cols=cols, sample_exc=ArchiveError(message="unfiltered read fails")),
    )
    monkeypatch.setattr(inspect_mod, "lookup_schema", lambda *, archive, table: None)

    out = inspect_mod.vo_inspect_table(table="tap_schema.obscore", archive="datalab", sample_rows=5)

    assert out["columns"] == [{"name": "s_ra", "datatype": "double"}]
    assert out["sample_status"] == "error"
    assert out["sample_rows"] == []


def test_inspect_sample_disabled_when_zero(known_datalab, monkeypatch):
    cols = Table({"column_name": ["ra"], "datatype": ["double"]})
    monkeypatch.setattr(inspect_mod, "_get_tap", lambda: _Tap(cols=cols))
    monkeypatch.setattr(inspect_mod, "lookup_schema", lambda *, archive, table: None)
    out = inspect_mod.vo_inspect_table(table="nsc_dr2.object", archive="datalab", sample_rows=0)
    assert out["sample_status"] == "disabled"


def test_inspect_unknown_archive_soft_fails(monkeypatch):
    monkeypatch.setattr(inspect_mod, "by_short_name", lambda n: None)
    monkeypatch.setattr(inspect_mod, "lookup_schema", lambda *, archive, table: None)
    out = inspect_mod.vo_inspect_table(table="foo.bar", archive="nope")
    assert out["known"] is False
    assert "hint" in out
