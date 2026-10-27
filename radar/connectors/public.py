"""Public sources: openFDA (510(k), recalls), ClinicalTrials.gov, PubMed and RSS news feeds.

Each connector turns the source's own record format into RawItems and nothing more;
interpretation happens in the ingest step so every source is judged the same way.
When a live API cannot be reached, the openFDA connectors fall back to the snapshot
committed under data/public/ and say so in the pull status.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import re
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

from radar.config import REPO
from radar.connectors.base import Connector, PullResult, SourceUnavailable, http_get, http_json, in_window
from radar.schemas import WatchBrief


def _fda_date(s: str | None) -> str:
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _compact(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ------------------------------------------------------------------ openFDA
class _OpenFDA(Connector):
    endpoint = ""
    date_field = ""

    def _codes(self, brief: WatchBrief) -> list[str]:
        return brief.regulatory_ids.get("fda_product_codes", [])

    def _live(self, code: str, since: str | None, until: str) -> list[dict]:
        a = (since or "1976-01-01").replace("-", "")
        b = until.replace("-", "")
        params = {"search": f"product_code:{code}+AND+{self.date_field}:[{a}+TO+{b}]", "limit": 100}
        if os.getenv("OPENFDA_API_KEY"):
            params["api_key"] = os.environ["OPENFDA_API_KEY"]
        try:
            return http_json(f"https://api.fda.gov/device/{self.endpoint}.json", params).get("results", [])
        except urllib.error.HTTPError as e:
            if e.code == 404:                       # openFDA's answer for "no matches"
                return []
            raise SourceUnavailable(str(e)) from e

    def _snapshot(self) -> list[dict]:
        p = self.cfg.get("snapshot")
        if not p or not (REPO / p).exists():
            return []
        return json.load(open(REPO / p, encoding="utf-8"))["results"]

    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        rows, status, note = [], "ok", ""
        for code in self._codes(brief):
            try:
                if offline:
                    raise SourceUnavailable("offline mode")
                rows += self._live(code, since, until)
            except SourceUnavailable as e:
                rows += [r for r in self._snapshot() if r.get("product_code") == code]
                status, note = "snapshot", f"live API unavailable ({e}); used committed snapshot"
        items = [self.to_item(r) for r in rows]
        return PullResult([i for i in items if in_window(i.published, since, until)], status, note)


class OpenFDA510k(_OpenFDA):
    endpoint, date_field = "510k", "decision_date"

    def to_item(self, r: dict):
        d = _fda_date(r.get("decision_date"))
        k = r.get("k_number", "")
        text = (f"FDA 510(k) decision: {(r.get('decision_description') or 'cleared').lower()}. {r.get('applicant')}, "
                f"{_compact(r.get('device_name'))}, product code {r.get('product_code')}, decision date {d}.")
        return self._item(id=f"fda510k-{k}", kind="fda_510k", published=d,
                          title=f"510(k) {k}: {r.get('applicant')} {_compact(r.get('device_name'))}", text=text,
                          url=f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={k}",
                          fields={"k_number": k, "applicant": r.get("applicant"), "device_name": _compact(r.get("device_name")),
                                  "product_code": r.get("product_code"), "decision_date": d,
                                  "decision_description": r.get("decision_description")})


class OpenFDARecall(_OpenFDA):
    endpoint, date_field = "recall", "event_date_initiated"

    def to_item(self, r: dict):
        d = _fda_date(r.get("event_date_initiated"))
        num = r.get("product_res_number", "")
        desc = _compact(r.get("product_description"))[:240]
        reason = _compact(r.get("reason_for_recall"))[:400]
        return self._item(id=f"fdarecall-{num}", kind="fda_recall", published=d,
                          title=f"Recall {num}: {r.get('recalling_firm')}, {desc[:90]}",
                          text=f"{reason} Product: {desc}.",
                          url=f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRES/res.cfm?id={r.get('cfres_id', '')}",
                          fields={"product_res_number": num, "recalling_firm": r.get("recalling_firm"),
                                  "product_code": r.get("product_code"), "product_description": desc,
                                  "recall_status": r.get("recall_status"), "k_numbers": r.get("k_numbers") or []})


# ------------------------------------------------------------------ ClinicalTrials.gov
class ClinicalTrialsGov(Connector):
    URL = "https://clinicaltrials.gov/api/v2/studies"

    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        if offline:
            return PullResult(status="unreachable", note="offline mode")
        term = f"{brief.diagnostic_solution} point-of-care"
        rng = f"AREA[LastUpdatePostDate]RANGE[{since or 'MIN'},{until}]"
        params = {"query.term": term, "filter.advanced": rng, "pageSize": 50, "format": "json"}
        try:
            data = http_json(self.URL, params)
        except (SourceUnavailable, urllib.error.HTTPError) as e:
            return PullResult(status="unreachable", note=str(e))
        return PullResult([self.to_item(s) for s in data.get("studies", [])])

    def to_item(self, s: dict):
        p = s.get("protocolSection", {})
        ident, status = p.get("identificationModule", {}), p.get("statusModule", {})
        design = p.get("designModule", {})
        nct = ident.get("nctId", "")
        updated = (status.get("lastUpdatePostDateStruct") or {}).get("date", "")[:10]
        locs = (p.get("contactsLocationsModule") or {}).get("locations") or []
        fields = {"nct_id": nct, "sponsor": ((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name"),
                  "status": status.get("overallStatus"), "phase": ",".join(design.get("phases") or []) or "NA",
                  "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
                  "start_date": (status.get("startDateStruct") or {}).get("date"),
                  "conditions": (p.get("conditionsModule") or {}).get("conditions") or [],
                  "study_type": design.get("studyType"),
                  "countries": sorted({x.get("country") for x in locs if x.get("country")})}
        return self._item(id=f"ctgov-{nct}-{updated}", kind="trial", published=updated or fields["start_date"] or "",
                          title=ident.get("briefTitle", ""), text=_compact((p.get("descriptionModule") or {}).get("briefSummary"))[:800],
                          url=f"https://clinicaltrials.gov/study/{nct}", fields=fields)


# ------------------------------------------------------------------ PubMed
class PubMed(Connector):
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        if offline:
            return PullResult(status="unreachable", note="offline mode")
        key = {"api_key": os.environ["NCBI_API_KEY"]} if os.getenv("NCBI_API_KEY") else {}
        term = f'({brief.diagnostic_solution}) AND ("point-of-care" OR "point of care")'
        try:
            ids = http_json(f"{self.BASE}/esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retmax": 50,
                                                           "datetype": "pdat", "mindate": (since or "2000-01-01").replace("-", "/"),
                                                           "maxdate": until.replace("-", "/"), **key})["esearchresult"]["idlist"]
            if not ids:
                return PullResult()
            xml = http_get(f"{self.BASE}/efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **key})
        except (SourceUnavailable, urllib.error.HTTPError, KeyError) as e:
            return PullResult(status="unreachable", note=str(e))
        return PullResult(self.parse(xml))

    def parse(self, xml: bytes):
        items = []
        for art in ET.fromstring(xml).iter("PubmedArticle"):
            pmid = art.findtext(".//PMID", "")
            title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
            abstract = " ".join("".join(a.itertext()) for a in art.iter("AbstractText"))
            journal = art.findtext(".//Journal/Title", "")
            y = art.findtext(".//PubDate/Year") or art.findtext(".//ArticleDate/Year") or "1900"
            m = art.findtext(".//ArticleDate/Month") or "01"
            dd = art.findtext(".//ArticleDate/Day") or "01"
            items.append(self._item(id=f"pubmed-{pmid}", kind="publication", published=f"{y}-{int(m):02d}-{int(dd):02d}",
                                    title=_compact(title), text=_compact(abstract)[:1500],
                                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", fields={"pmid": pmid, "journal": journal}))
        return items


# ------------------------------------------------------------------ RSS / Atom news
class RSS(Connector):
    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        if offline:
            return PullResult(status="unreachable", note="offline mode")
        items, failures = [], []
        for url in self.cfg.get("feeds", []):
            try:
                items += self.parse(http_get(url, timeout=20), url)
            except (SourceUnavailable, urllib.error.HTTPError, ET.ParseError) as e:
                failures.append(f"{url}: {e}")
        terms = [t.lower() for t in brief.terms]
        items = [i for i in items if in_window(i.published, since, until)
                 and any(t in (i.title + " " + i.text).lower() for t in terms)]
        status = "unreachable" if failures and not items and len(failures) == len(self.cfg.get("feeds", [])) else "ok"
        return PullResult(items, status, "; ".join(failures))

    def parse(self, xml: bytes, feed_url: str = ""):
        root, out = ET.fromstring(xml), []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//a:entry", ns)
        for e in entries:
            title = _compact(e.findtext("title") or e.findtext("a:title", namespaces=ns))
            link = e.findtext("link") or ""
            if not link:
                le = e.find("a:link", ns)
                link = le.get("href", "") if le is not None else ""
            desc = re.sub(r"<[^>]+>", " ", e.findtext("description") or e.findtext("a:summary", namespaces=ns) or "")
            raw_date = e.findtext("pubDate") or e.findtext("a:updated", namespaces=ns) or ""
            try:
                d = email.utils.parsedate_to_datetime(raw_date).date().isoformat()
            except (TypeError, ValueError):
                d = raw_date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", raw_date) else datetime.utcnow().date().isoformat()
            source = e.findtext("source") or (feed_url.split("/")[2] if feed_url else "")
            rid = "rss-" + hashlib.sha1((link or title).encode()).hexdigest()[:12]
            out.append(self._item(id=rid, kind="news", published=d, title=title, text=_compact(desc)[:1200], url=link,
                                  fields={"outlet": source}))
        return out
