import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import scrape


def test_merge_keeps_first_seen_and_prunes_stale():
    old = [
        {"id": scrape.job_id("A", "u1"), "source": "A", "title": "x", "url": "u1",
         "first_seen": "2026-11-01", "last_seen": "2026-11-10"},
        {"id": scrape.job_id("A", "u2"), "source": "A", "title": "y", "url": "u2",
         "first_seen": "2026-11-01", "last_seen": "2026-11-01"},   # sumiu há 10 dias
        {"id": scrape.job_id("B", "u3"), "source": "B", "title": "z", "url": "u3",
         "first_seen": "2026-10-01", "last_seen": "2026-10-01"},   # fonte B falhou: preserva
    ]
    res = {"A": [scrape.job("x", "Co", "", "u1"), scrape.job("n", "Co", "", "u4")]}
    out = {j["url"]: j for j in scrape.merge(old, res, "2026-11-11")}
    assert out["u1"]["first_seen"] == "2026-11-01" and out["u1"]["last_seen"] == "2026-11-11"
    assert out["u4"]["first_seen"] == "2026-11-11"
    assert "u2" not in out and "u3" in out


def test_anchor_and_linkedin_parsing(monkeypatch):
    html = '<ul><li><div class="base-card"><a class="base-card__full-link" href="https://x/jobs/view/1?a=b"></a>' \
           '<h3 class="base-search-card__title"> Vendedor </h3><h4 class="base-search-card__subtitle">Loja</h4>' \
           '<span class="job-search-card__location">Mineiros, Goiás, Brasil</span><time datetime="2026-11-10"></time>' \
           '</div></li></ul>'

    class R:
        def __init__(s, t): s.text = t
    calls = iter([R(html), R("")])
    monkeypatch.setattr(scrape, "get", lambda *a, **k: next(calls))
    monkeypatch.setattr(scrape.time, "sleep", lambda *_: None)
    jobs = scrape.linkedin()
    assert jobs[0]["title"] == "Vendedor" and jobs[0]["url"] == "https://x/jobs/view/1"

    monkeypatch.setattr(scrape, "get", lambda *a, **k: R('<a href="/vaga-de-caixa__123.aspx">Caixa</a>'))
    assert scrape.infojobs()[0]["url"].endswith("/vaga-de-caixa__123.aspx")
