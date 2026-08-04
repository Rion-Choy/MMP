from fastapi.testclient import TestClient


def test_root_remains_unassigned(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 404
