def test_create_and_get_run(client):
    response = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [4, 3]})
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    loaded = client.get(f"/api/runs/{run_id}")
    assert loaded.status_code == 200
    assert loaded.json()["run_id"] == run_id
    assert loaded.json()["status"] == "created"


def test_missing_run_returns_404(client):
    response = client.get("/api/runs/not-found")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"
