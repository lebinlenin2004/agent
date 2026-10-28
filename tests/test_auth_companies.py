import io

CO = {"name": "Acme Soft", "location": "Kochi", "tech_stack": ["React", "Node.js"], "website": "acme.com"}


def test_register_login_me(client):
    r = client.post("/api/v1/auth/register", json={"email": "New@Example.org", "full_name": "New", "password": "long-enough-pw"})
    assert r.status_code == 201 and r.json()["role"] == "advisor" and r.json()["email"] == "new@example.org"
    assert "hashed_password" not in r.json()
    tok = client.post("/api/v1/auth/login", data={"username": "new@example.org", "password": "long-enough-pw"}).json()
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "new@example.org"


def test_bad_login_and_weak_password(client):
    assert client.post("/api/v1/auth/register", json={"email": "a@b.org", "full_name": "A", "password": "short"}).status_code == 422
    r = client.post("/api/v1/auth/login", data={"username": "nobody@x.org", "password": "whatever-123"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_requires_auth(client):
    r = client.get("/api/v1/companies")
    assert r.status_code == 401


def test_company_crud_and_rbac(client, advisor, viewer, admin):
    r = client.post("/api/v1/companies", json=CO, headers=advisor)
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.post("/api/v1/companies", json={**CO, "name": "Other"}, headers=advisor).status_code == 409  # same website
    assert client.post("/api/v1/companies", json=CO, headers=viewer).status_code == 403
    assert client.get("/api/v1/companies", headers=viewer).json()["total"] == 1
    assert client.get("/api/v1/companies?stack=react", headers=viewer).json()["total"] == 1
    assert client.get("/api/v1/companies?stack=python", headers=viewer).json()["total"] == 0
    r = client.patch(f"/api/v1/companies/{cid}", json={"location": "Chennai"}, headers=advisor)
    assert r.json()["location"] == "Chennai"
    client.post(f"/api/v1/companies/{cid}/archive", headers=advisor)
    assert client.get("/api/v1/companies", headers=viewer).json()["total"] == 0
    assert client.get("/api/v1/companies?archived=yes", headers=viewer).json()["total"] == 1
    assert client.delete(f"/api/v1/companies/{cid}", headers=advisor).status_code == 403
    assert client.delete(f"/api/v1/companies/{cid}", headers=admin).status_code == 200
    assert client.get(f"/api/v1/companies/{cid}", headers=admin).status_code == 404


def test_validation_error_envelope(client, advisor):
    r = client.post("/api/v1/companies", json={"name": "", "website": "not a url"}, headers=advisor)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["details"]


def test_import_validation_and_duplicates(client, advisor):
    client.post("/api/v1/companies", json=CO, headers=advisor)
    csv = ("Company,City,Stack,Website\n"
           "Acme Again,Kochi,React,https://www.acme.com/\n"      # duplicate of existing (by domain)
           "Bolt IO,Chennai,\"Go; Python\",bolt.io\n"
           "Bolt Twin,Chennai,Go,bolt.io\n"                       # duplicate within file
           ",Remote,Java,nova.dev\n"                              # missing name
           "Delta,Pune,Java,not a url\n")                         # bad website
    files = {"file": ("c.csv", io.BytesIO(csv.encode()), "text/csv")}
    r = client.post("/api/v1/companies/import?dry_run=true", files=files, headers=advisor).json()
    assert r["created"] == 1 and len(r["duplicates"]) == 2 and len(r["errors"]) == 2 and r["dry_run"]
    assert client.get("/api/v1/companies", headers=advisor).json()["total"] == 1  # dry run wrote nothing
    files = {"file": ("c.csv", io.BytesIO(csv.encode()), "text/csv")}
    client.post("/api/v1/companies/import", files=files, headers=advisor)
    assert client.get("/api/v1/companies", headers=advisor).json()["total"] == 2


def test_import_rejects_bad_files(client, advisor):
    r = client.post("/api/v1/companies/import", files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")}, headers=advisor)
    assert r.status_code == 400
    r = client.post("/api/v1/companies/import", files={"file": ("x.csv", io.BytesIO(b"foo,bar\n1,2\n"), "text/csv")}, headers=advisor)
    assert r.status_code == 400 and "name" in r.json()["error"]["message"]
