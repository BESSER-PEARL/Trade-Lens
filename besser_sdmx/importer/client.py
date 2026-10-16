# SPDX-License-Identifier: MIT
"""Minimal SDMX 2.1 REST client: structure (SDMX-ML) and data (SDMX-CSV)."""
from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

USER_AGENT = "besser-sdmx/0.1 (+https://github.com/BESSER-PEARL/statec_hackathon_2026)"
CSV_ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
XML_ACCEPT = "application/vnd.sdmx.structure+xml;version=2.1"


@dataclass(frozen=True)
class Source:
    """An SDMX 2.1 REST endpoint."""
    name: str
    base_url: str
    agency: str
    references: str = "descendants"  # Eurostat rejects "all"; LUSTAT (.Stat Suite) needs it for the DSD


SOURCES = {
    "eurostat-comext": Source(
        "Eurostat Comext", "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1", "ESTAT"
    ),
    "eurostat": Source("Eurostat", "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1", "ESTAT"),
    "lustat": Source("STATEC LUSTAT", "https://lustat.statec.lu/rest", "LU1", references="all"),
}


class TooLargeError(RuntimeError):
    """The provider refused or queued the query because the result is too big.

    Signalled by HTTP 413, or by Eurostat Comext answering 200 with an XML
    "queued" envelope instead of CSV.
    """


class SDMXClient:
    def __init__(self, source: Source | str, timeout: int = 300, retries: int = 3):
        self.source = SOURCES[source] if isinstance(source, str) else source
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _get(self, url: str, params: Optional[dict] = None, accept: Optional[str] = None) -> requests.Response:
        headers = {"Accept": accept} if accept else {}
        last: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:  # network hiccup: retry
                last = e
            else:
                if r.status_code == 413:
                    raise TooLargeError(f"Query too large for {self.source.name}: {r.url}")
                if r.status_code in (429, 500, 502, 503, 504):
                    last = RuntimeError(f"HTTP {r.status_code} from {r.url}")
                else:
                    r.raise_for_status()
                    return r
            time.sleep(2 ** attempt)
        raise RuntimeError(f"Request failed after {self.retries} attempts: {url}") from last

    def structure(self, flow_id: str, references: Optional[str] = None) -> bytes:
        """SDMX-ML structure message for a dataflow, including its DSD, concepts and codelists."""
        url = f"{self.source.base_url}/dataflow/{self.source.agency}/{flow_id}/latest"
        params = {"references": references or self.source.references}
        return self._get(url, params=params, accept=XML_ACCEPT).content

    def hierarchical_codelist(self, agency: str, hcl_id: str, version: str = "latest") -> bytes:
        """SDMX-ML HierarchicalCodelist (e.g. the regions -> countries tree behind a LUSTAT dimension)."""
        url = f"{self.source.base_url}/hierarchicalcodelist/{agency}/{hcl_id}/{version}"
        return self._get(url, accept=XML_ACCEPT).content

    def content_constraint(self, flow_id: str) -> Optional[bytes]:
        """The dataflow's content constraint (codes actually used), or None when the provider has none."""
        url = f"{self.source.base_url}/contentconstraint/{self.source.agency}/{flow_id}"
        try:
            return self._get(url, accept=XML_ACCEPT).content
        except Exception:  # optional: not every provider exposes it
            return None

    def data(
        self,
        flow_id: str,
        key: str = "all",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Observations as a DataFrame (SDMX-CSV). `key` is a dot-separated series key, e.g. ``A.LU..TOTAL.1+2.``."""
        url = f"{self.source.base_url}/data/{flow_id}/{key or 'all'}"
        params = {}
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        try:
            r = self._get(url, params=params, accept=CSV_ACCEPT)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:  # SDMX "no results found"
                return pd.DataFrame()
            raise
        body = r.content.lstrip()
        if not body:
            return pd.DataFrame()
        if body.startswith(b"<"):
            if b"queued" in body[:2000].lower() or b"SUBMITTED" in body[:2000]:
                raise TooLargeError(f"Query queued (too large) by {self.source.name}: {r.url}")
            if b"NoResults" in body or b"No Results" in body or b"100</" in body[:2000]:
                return pd.DataFrame()
            raise RuntimeError(f"Expected SDMX-CSV from {r.url}, got XML: {body[:300]!r}")
        return pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False)
