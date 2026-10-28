import io

import pytest

from app.models import Vacancy, VacancyStatus
from app.services import search_service

CRIT = {"target_roles": ["Frontend Developer", "Backend Engineer"], "technology_stacks": ["React", "Python"],
        "locations": [], "experience_levels": [], "sources": ["company_sites", "search_api"]}


def _company(client, h, name, site):
    r = client.post("/api/v1/companies", json={"name": name, "website": site, "careers_url": f"https://{site}/careers",
                                               "location": "Kochi", "tech_stack": ["React"]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture()
def seeded(client, advisor):
    ids = {k: _company(client, advisor, k, f"{k}.com") for k in ("acme", "nojobs", "failhost")}
    return ids


def _run(client, h, ids, **extra):
    r = client.post("/api/v1/searches", json={**CRIT, "company_ids": ids, **extra}, headers=h)
    assert r.status_code == 202, r.text
    # TestClient runs background tasks before returning, so the run has finished
    return client.get(f"/api/v1/searches/{r.json()['id']}", headers=h).json()


def test_search_end_to_end_distinguishes_outcomes(client, advisor, seeded):
    run = _run(client, advisor, list(seeded.values()))
    assert run["processed_companies"] == 3 and run["progress_percent"] == 100
    assert run["status"] == "completed_with_errors" and run["incomplete_companies"] == 1
    res = client.get(f"/api/v1/searches/{run['id']}/results?page_size=100", headers=advisor).json()["items"]
    by_company = {}
    for r in res:
        by_company.setdefault(r["company_name"], []).append(r)
    assert any(r["outcome"] == "active_verified" for r in by_company["acme"])
    assert [r["outcome"] for r in by_company["nojobs"]] == ["no_matching_vacancy_found"]   # looked, found nothing
    assert [r["outcome"] for r in by_company["failhost"]] == ["search_incomplete"]          # could not look
    assert by_company["failhost"][0]["error"]
    assert run["vacancies_found"] == len([r for r in res if r["vacancy_id"]])


def test_rerun_dedupes_and_stores_evidence(client, advisor, seeded):
    ids = [seeded["acme"]]
    _run(client, advisor, ids)
    n1 = client.get("/api/v1/vacancies?page_size=100", headers=advisor).json()["total"]
    assert n1 >= 2
    _run(client, advisor, ids)
    assert client.get("/api/v1/vacancies?page_size=100", headers=advisor).json()["total"] == n1
    v = client.get("/api/v1/vacancies?page_size=100", headers=advisor).json()["items"][0]
    d = client.get(f"/api/v1/vacancies/{v['id']}", headers=advisor).json()
    assert d["sources"] and d["evidence"] and d["match_rationale"] and d["status_label"]
    assert all(s["url"].startswith("https://acme.com") for s in d["sources"])


def test_filters_sorting_pagination(client, advisor, seeded):
    _run(client, advisor, list(seeded.values()))
    base = "/api/v1/vacancies"
    allv = client.get(f"{base}?page_size=100", headers=advisor).json()
    assert allv["pages"] == 1 and allv["total"] > 0
    scores = [v["match_score"] for v in allv["items"]]
    assert scores == sorted(scores, reverse=True)
    assert client.get(f"{base}?page_size=1", headers=advisor).json()["pages"] == allv["total"]
    py = client.get(f"{base}?stack=python&page_size=100", headers=advisor).json()
    assert 0 < py["total"] <= allv["total"] and all("Python" in v["skills"] for v in py["items"])
    act = client.get(f"{base}?status=active_verified&min_score=50&page_size=100", headers=advisor).json()
    assert all(v["status"] == "active_verified" and v["match_score"] >= 50 for v in act["items"])
    assert client.get(f"{base}?status=bogus", headers=advisor).status_code == 400
    assert client.get(f"{base}?sort=password", headers=advisor).status_code == 400
    assert client.get(f"{base}?q=%25", headers=advisor).json()["total"] == 0  # LIKE wildcards are escaped


def test_closed_posting_is_recorded_as_closed(client, advisor):
    # find a mock host whose hash gives a closed devops posting
    from app.research.mock import _closed_slug
    host = next(f"co{i}.com" for i in range(200) if _closed_slug(f"co{i}.com"))
    cid = _company(client, advisor, "Closer", host)
    _run(client, advisor, [cid], target_roles=["DevOps Engineer"], technology_stacks=["Docker"])
    items = client.get("/api/v1/vacancies?page_size=100", headers=advisor).json()["items"]
    assert any(v["status"] == "closed_verified" for v in items)


def test_manual_vacancy_cannot_be_active_and_review_flow(client, advisor, viewer, seeded):
    cid = seeded["acme"]
    r = client.post("/api/v1/vacancies", headers=advisor, json={"company_id": cid, "title": "Tester", "status": "active_verified",
                                                                "application_url": "https://acme.com/x"})
    assert r.status_code == 201 and r.json()["status"] == "found_unverified"
    assert client.post("/api/v1/vacancies", headers=advisor, json={"company_id": cid, "title": "Tester"}).status_code == 409
    vid = r.json()["id"]
    p = client.patch(f"/api/v1/vacancies/{vid}", headers=advisor, json={"reviewed": True, "advisor_notes": "Called HR"})
    assert p.json()["reviewed"] and p.json()["advisor_notes"] == "Called HR"
    assert client.patch(f"/api/v1/vacancies/{vid}", headers=viewer, json={"reviewed": False}).status_code == 403
    assert client.patch(f"/api/v1/vacancies/{vid}", headers=advisor, json={"status": "active_verified"}).json()["status"] == "found_unverified"


def test_recheck_updates_status_and_failed_fetch_keeps_it(client, advisor, session_factory, seeded):
    _run(client, advisor, [seeded["acme"]])
    vid = client.get("/api/v1/vacancies?status=active_verified", headers=advisor).json()["items"][0]["id"]
    s = session_factory()
    v = s.get(Vacancy, vid)
    v.status = VacancyStatus.FOUND_UNVERIFIED
    s.commit(); s.close()
    assert client.post(f"/api/v1/vacancies/{vid}/recheck", headers=advisor).status_code == 202
    assert client.get(f"/api/v1/vacancies/{vid}", headers=advisor).json()["status"] == "active_verified"
    # point the vacancy at a host whose fetch times out: status must not change
    s = session_factory(); v = s.get(Vacancy, vid)
    v.sources[0].url = "https://failhost.com/careers/jobs/x"; s.commit(); s.close()
    client.post(f"/api/v1/vacancies/{vid}/recheck", headers=advisor)
    d = client.get(f"/api/v1/vacancies/{vid}", headers=advisor).json()
    assert d["status"] == "active_verified" and "could not complete" in d["verification_notes"]


def test_retry_only_incomplete_companies(client, advisor, seeded):
    run = _run(client, advisor, list(seeded.values()))
    r = client.post(f"/api/v1/searches/{run['id']}/retry", headers=advisor)
    assert r.status_code == 202
    new = client.get(f"/api/v1/searches/{r.json()['id']}", headers=advisor).json()
    assert new["total_companies"] == 1 and new["retry_of_id"] == run["id"]
    ok = _run(client, advisor, [seeded["acme"]])
    assert client.post(f"/api/v1/searches/{ok['id']}/retry", headers=advisor).status_code == 409


def test_search_validation_and_rbac(client, advisor, viewer, seeded):
    ids = list(seeded.values())
    assert client.post("/api/v1/searches", json={**CRIT, "company_ids": ids}, headers=viewer).status_code == 403
    assert client.post("/api/v1/searches", json={**CRIT, "company_ids": [9999]}, headers=advisor).status_code == 400
    assert client.post("/api/v1/searches", json={"company_ids": ids}, headers=advisor).status_code == 422
    client.post(f"/api/v1/companies/{ids[0]}/archive", headers=advisor)
    assert client.post("/api/v1/searches", json={**CRIT, "company_ids": ids}, headers=advisor).status_code == 400


def test_reports_dashboard_and_exports(client, advisor, seeded):
    _run(client, advisor, list(seeded.values()))
    d = client.get("/api/v1/reports/dashboard", headers=advisor).json()
    assert d["total_companies"] == 3 and d["vacancies_discovered"] > 0 and len(d["discovery_trend"]) == 14
    assert sum(x["count"] for x in d["discovery_trend"]) == d["vacancies_discovered"]
    assert d["recent_runs"] and d["verified_active"] >= 1
    rep = client.get("/api/v1/reports/companies", headers=advisor).json()["items"]
    assert {r["company"]: r["finding"] for r in rep}["nojobs"] == "No matching vacancy found"
    assert client.get("/api/v1/reports/summary?status=active_verified", headers=advisor).json()["total_vacancies"] >= 1
    csv = client.get("/api/v1/exports/vacancies?format=csv", headers=advisor)
    assert csv.status_code == 200 and "Job title" in csv.text and "attachment" in csv.headers["content-disposition"]
    x = client.get("/api/v1/exports/vacancies?format=xlsx", headers=advisor)
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb.active.max_row > 1 and wb.active["A1"].value == "Job title"
    assert client.get("/api/v1/exports/company-report?format=csv", headers=advisor).status_code == 200


def test_export_neutralises_formulas(client, advisor, seeded):
    client.post("/api/v1/vacancies", headers=advisor, json={"company_id": seeded["acme"], "title": "=HYPERLINK(\"http://evil\")"})
    csv = client.get("/api/v1/exports/vacancies", headers=advisor).text
    assert "'=HYPERLINK" in csv


def test_settings_and_admin_only_research_config(client, advisor, admin, viewer):
    assert client.put("/api/v1/settings/preferences", json={"default_page_size": 50, "theme": "dark"}, headers=viewer).status_code == 200
    assert client.get("/api/v1/settings/preferences", headers=viewer).json()["default_page_size"] == 50
    body = {"name": "Default", "target_roles": ["Data Analyst"], "technology_stacks": ["Python"], "sources": ["company_sites"]}
    assert client.put("/api/v1/settings/research", json=body, headers=advisor).status_code == 403
    assert client.put("/api/v1/settings/research", json=body, headers=admin).status_code == 200
    assert client.get("/api/v1/settings/research", headers=viewer).json()["target_roles"] == ["Data Analyst"]
    r = client.post("/api/v1/settings/search-configurations", json=body, headers=advisor)
    assert r.status_code == 201
    assert client.delete(f"/api/v1/settings/search-configurations/{r.json()['id']}", headers=admin).status_code == 404  # not theirs
    assert client.delete(f"/api/v1/settings/search-configurations/{r.json()['id']}", headers=advisor).status_code == 200


def test_internal_errors_are_not_leaked(client, advisor, monkeypatch):
    from app.services import company_service
    monkeypatch.setattr(company_service, "list_companies", lambda *a, **k: 1 / 0)
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/v1/companies", headers=advisor)
    assert r.status_code == 500 and "division" not in r.text and r.json()["error"]["code"] == "internal_error"
