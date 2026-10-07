"""Opt-in, bounded PubMed abstract retrieval; no claim classification here."""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from protocol_optimization_agent.runtime.archive import audited_operation

_LOCK = Lock()
_LAST_REQUEST = 0.0


def _get(endpoint: str, params: dict[str, Any]) -> bytes:
    global _LAST_REQUEST
    # ponytail: per-process throttle; multi-worker deployments need a shared NCBI rate limiter.
    with _LOCK:
        time.sleep(max(0, 0.4 - (time.monotonic() - _LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
        with urlopen(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{endpoint}?{urlencode(params)}", timeout=15) as response:
            data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Literature response exceeds the size limit.")
    return data


def parse_pubmed_articles(data: bytes) -> list[dict[str, Any]]:
    if b"<!ENTITY" in data.upper():
        raise ValueError("External XML entities are not supported.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("Malformed PubMed XML response.") from exc
    rows = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//MedlineCitation/PMID", "")
        title_element = article.find(".//ArticleTitle")
        title = "".join(title_element.itertext()) if title_element is not None else ""
        abstract = "\n".join("".join(part.itertext()) for part in article.findall(".//Abstract/AbstractText"))
        if not pmid.isdigit() or not abstract:
            continue
        rows.append({"pmid": pmid, "title": title, "quote": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "stance": "uncertain",
            "publication_types": ["".join(part.itertext()) for part in article.findall(".//PublicationType")],
            "doi": next((part.text or "" for part in article.findall(".//ArticleId")
                         if part.get("IdType") == "doi"), ""),
            "source_origin": "pubmed_abstract", "retrieved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "applicability": {"status": "requires_review_against_protocol_context"}})
    return rows


@audited_operation
def search_pubmed(query: str, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or not 1 <= limit <= 5:
        raise ValueError("PubMed queries must contain 1-500 characters and request 1-5 records.")
    search = json.loads(_get("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit}))
    ids = search["esearchresult"]["idlist"]
    if not isinstance(ids, list):
        raise ValueError("Malformed PubMed identifier response.")
    if not ids:
        return []
    if not all(isinstance(pmid, str) and pmid.isdigit() for pmid in ids):
        raise ValueError("Malformed PubMed identifier response.")
    return parse_pubmed_articles(_get("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids[:limit]), "retmode": "xml"}))[:limit]
