#!/usr/bin/env python3
"""Coleta vagas de emprego de Mineiros-GO em várias fontes e grava docs/data/jobs.json.

Cada fonte é isolada: se uma falhar (bloqueio, mudança de layout), as outras continuam
e as vagas antigas da fonte que falhou são preservadas.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

CITY = "Mineiros"
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "jobs.json"
TZ = ZoneInfo("America/Sao_Paulo")
STALE_DAYS = 3  # vaga some do painel se a fonte respondeu OK e ela não apareceu por N dias
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWeb"
    "Kit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})


def get(url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=25, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def job(title, company, location, url, posted=None) -> dict:
    return {
        "title": clean(title),
        "company": clean(company),
        "location": clean(location) or f"{CITY}, GO",
        "url": url,
        "posted": posted,
    }


# --------------------------------------------------------------------------- fontes
def linkedin() -> list[dict]:
    out = []
    loc = quote("Mineiros, Goiás, Brasil")
    for start in range(0, 100, 10):
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords=&location={loc}&f_TPR=r2592000&start={start}"
        )
        soup = BeautifulSoup(get(url).text, "html.parser")
        cards = soup.select("li")
        if not cards:
            break
        for c in cards:
            a = c.select_one("a.base-card__full-link") or c.select_one("a[href*='/jobs/view/']")
            if not a:
                continue
            t = c.select_one(".base-search-card__title")
            co = c.select_one(".base-search-card__subtitle")
            lo = c.select_one(".job-search-card__location")
            tm = c.select_one("time")
            out.append(job(
                t.get_text() if t else a.get_text(), co.get_text() if co else "",
                lo.get_text() if lo else "", a["href"].split("?")[0],
                tm.get("datetime") if tm else None,
            ))
        time.sleep(1.5)
    return [j for j in out if CITY.lower() in j["location"].lower()]


def gupy() -> list[dict]:
    out, offset = [], 0
    while offset < 500:
        r = get(
            "https://portal.api.gupy.io/api/job",
            params={"jobName": "", "city": CITY, "state": "Goiás", "limit": 100, "offset": offset},
        )
        data = r.json().get("data", [])
        for d in data:
            if clean(d.get("city")).lower() != CITY.lower():
                continue
            out.append(job(
                d.get("name"), d.get("careerPageName"), f"{d.get('city')}, GO",
                d.get("jobUrl"), (d.get("publishedDate") or "")[:10] or None,
            ))
        if len(data) < 100:
            break
        offset += 100
    return out


def jooble() -> list[dict]:
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        raise SkipSource("defina o secret JOOBLE_API_KEY (grátis em jooble.org/api/about)")
    out, page = [], 1
    while page <= 5:
        r = session.post(
            f"https://jooble.org/api/{key}",
            json={"keywords": "", "location": "Mineiros, GO", "page": page}, timeout=25,
        )
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        if not jobs:
            break
        for d in jobs:
            out.append(job(d.get("title"), d.get("company"), d.get("location"),
                           d.get("link"), (d.get("updated") or "")[:10] or None))
        page += 1
    return [j for j in out if CITY.lower() in j["location"].lower()]


def anchors(url: str, href_re: str, company_sel: str | None = None):
    """Extrator genérico: pega links cujo href casa com href_re."""
    soup = BeautifulSoup(get(url).text, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=re.compile(href_re)):
        href = urljoin(url, a["href"]).split("#")[0]
        title = clean(a.get("title") or a.get_text())
        if href in seen or len(title) < 4:
            continue
        seen.add(href)
        co = ""
        if company_sel:
            box = a.find_parent(["li", "article", "div"])
            el = box.select_one(company_sel) if box else None
            co = el.get_text() if el else ""
        out.append(job(title, co, f"{CITY}, GO", href))
    return out


def vagas_com_br():
    return anchors("https://www.vagas.com.br/vagas-em-mineiros-go", r"/vagas/v\d+", ".emprVaga")


def infojobs():
    return anchors("https://www.infojobs.com.br/vagas-de-emprego-mineiros-go.aspx", r"/vaga-de-.*__\d+")


def catho():
    return anchors("https://www.catho.com.br/vagas/mineiros-go/", r"/vagas/.+/\d{6,}")


def trabalha_brasil():
    return anchors("https://www.trabalhabrasil.com.br/vagas-empregos-em/mineiros-go", r"/vagas-empregos/.+\d+")


def empregos_com_br():
    return anchors("https://www.empregos.com.br/vagas/mineiros-go", r"/vaga/\d+")


def indeed() -> list[dict]:
    soup = BeautifulSoup(get("https://br.indeed.com/jobs?q=&l=Mineiros%2C+GO&fromage=14").text, "html.parser")
    out = []
    for c in soup.select("div.job_seen_beacon"):
        a = c.select_one("h2 a")
        if not a:
            continue
        co = c.select_one("[data-testid='company-name']")
        lo = c.select_one("[data-testid='text-location']")
        out.append(job(a.get_text(), co.get_text() if co else "", lo.get_text() if lo else "",
                       urljoin("https://br.indeed.com", a["href"])))
    return out


class SkipSource(Exception):
    pass


# nome, função, link de busca manual (mostrado no painel mesmo se a coleta falhar)
SOURCES = [
    ("LinkedIn", linkedin, "https://www.linkedin.com/jobs/search/?location=Mineiros%2C%20Goi%C3%A1s%2C%20Brasil"),
    ("Gupy", gupy, "https://portal.gupy.io/job-search/term=&city=Mineiros&state=Goi%C3%A1s"),
    ("Jooble", jooble, "https://br.jooble.org/emprego-mineiros-go"),
    ("Vagas.com", vagas_com_br, "https://www.vagas.com.br/vagas-em-mineiros-go"),
    ("InfoJobs", infojobs, "https://www.infojobs.com.br/vagas-de-emprego-mineiros-go.aspx"),
    ("Catho", catho, "https://www.catho.com.br/vagas/mineiros-go/"),
    ("Trabalha Brasil", trabalha_brasil, "https://www.trabalhabrasil.com.br/vagas-empregos-em/mineiros-go"),
    ("Empregos.com.br", empregos_com_br, "https://www.empregos.com.br/vagas/mineiros-go"),
    ("Indeed", indeed, "https://br.indeed.com/jobs?q=&l=Mineiros%2C+GO"),
]
# Só links (sem coleta automática: exigem login/JavaScript): SINE e outros
MANUAL_LINKS = [
    ("SINE (gov.br)", "https://empregabrasil.mte.gov.br/76/sine-facil/"),
    ("Google Vagas", "https://www.google.com/search?q=vagas+de+emprego+em+Mineiros+GO&ibp=htl;jobs"),
]


# --------------------------------------------------------------------------- merge
def job_id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{url}".encode()).hexdigest()[:12]


def merge(old: list[dict], results: dict[str, list[dict]], today: str) -> list[dict]:
    """results: fontes que responderam OK -> vagas. Fontes ausentes mantêm vagas antigas."""
    by_id = {j["id"]: j for j in old}
    for source, items in results.items():
        for it in items:
            if not it.get("url") or not it.get("title"):
                continue
            jid = job_id(source, it["url"])
            prev = by_id.get(jid)
            by_id[jid] = {
                **it, "id": jid, "source": source,
                "first_seen": prev["first_seen"] if prev else today,
                "last_seen": today,
            }
    cutoff = (datetime.fromisoformat(today) - timedelta(days=STALE_DAYS)).date().isoformat()
    return [
        j for j in by_id.values()
        if j["source"] not in results or j["last_seen"] >= cutoff
    ]


def main() -> int:
    today = datetime.now(TZ).date().isoformat()
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text()).get("jobs", [])
        except json.JSONDecodeError:
            pass

    results, status = {}, []
    for name, fn, link in SOURCES:
        entry = {"name": name, "url": link, "status": "ok", "count": 0, "error": None}
        try:
            items = fn()
            results[name] = items
            entry["count"] = len(items)
            if not items:
                entry["status"] = "vazio"
        except SkipSource as e:
            entry.update(status="desativada", error=str(e))
        except Exception as e:  # noqa: BLE001 - isolamos qualquer falha de fonte
            entry.update(status="erro", error=f"{type(e).__name__}: {e}"[:200])
        print(f"[{entry['status']:>10}] {name}: {entry['count']} {entry['error'] or ''}", file=sys.stderr)
        status.append(entry)

    jobs = merge(old, results, today)
    jobs.sort(key=lambda j: (j["first_seen"], j.get("posted") or ""), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated_at": datetime.now(TZ).isoformat(timespec="minutes"),
        "city": "Mineiros - GO",
        "sources": status,
        "manual_links": [{"name": n, "url": u} for n, u in MANUAL_LINKS],
        "jobs": jobs,
    }, ensure_ascii=False, indent=1))
    print(f"{len(jobs)} vagas gravadas", file=sys.stderr)
    # Falha o workflow só se TODAS as fontes ativas falharam (dados ficam preservados)
    active = [s for s in status if s["status"] != "desativada"]
    return 1 if active and all(s["status"] == "erro" for s in active) else 0


if __name__ == "__main__":
    sys.exit(main())
