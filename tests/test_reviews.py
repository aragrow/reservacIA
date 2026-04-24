from __future__ import annotations


def _payload(**overrides):
    base = {
        "reviewer_name": "Jane Doe",
        "reviewer_city": "Madrid",
        "rating": 5,
        "body": "Wonderful evening — the paella was outstanding.",
    }
    base.update(overrides)
    return base


def test_create_read_update_happy_path(client, auth_headers):
    created = client.post("/reviews", json=_payload(), headers=auth_headers)
    assert created.status_code == 201, created.text
    body = created.json()
    rid = body["id"]
    assert body["rating"] == 5
    assert body["comments"] == []

    got = client.get(f"/reviews/{rid}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["id"] == rid

    updated = client.patch(
        f"/reviews/{rid}",
        json={"rating": 4, "body": "Good but not great on the second visit."},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    out = updated.json()
    assert out["rating"] == 4
    assert out["body"].startswith("Good")
    assert out["reviewer_name"] == "Jane Doe"  # unchanged


def test_comments_embedded_in_chronological_order(client, auth_headers):
    created = client.post("/reviews", json=_payload(), headers=auth_headers).json()
    rid = created["id"]

    first = client.post(
        f"/reviews/{rid}/comments",
        json={
            "author_role": "customer",
            "author_name": "Jane Doe",
            "body": "Forgot to mention — the tiramisu was perfect too.",
        },
        headers=auth_headers,
    )
    assert first.status_code == 201

    second = client.post(
        f"/reviews/{rid}/comments",
        json={
            "author_role": "restaurant",
            "author_name": "Chef Carla",
            "body": "Thanks Jane! We'd love to see you back soon.",
        },
        headers=auth_headers,
    )
    assert second.status_code == 201

    resp = client.get(f"/reviews/{rid}", headers=auth_headers)
    assert resp.status_code == 200
    comments = resp.json()["comments"]
    assert len(comments) == 2
    assert comments[0]["author_role"] == "customer"
    assert comments[1]["author_role"] == "restaurant"
    assert comments[0]["created_at"] <= comments[1]["created_at"]


def test_list_filters_by_min_rating(client, auth_headers):
    client.post("/reviews", json=_payload(rating=5, body="a"), headers=auth_headers)
    client.post("/reviews", json=_payload(rating=4, body="b"), headers=auth_headers)
    client.post("/reviews", json=_payload(rating=3, body="c"), headers=auth_headers)
    client.post("/reviews", json=_payload(rating=2, body="d"), headers=auth_headers)

    resp = client.get("/reviews", params={"min_rating": 4}, headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert all(r["rating"] >= 4 for r in rows)


def test_delete_review_returns_405(client, auth_headers):
    created = client.post("/reviews", json=_payload(), headers=auth_headers).json()
    resp = client.delete(f"/reviews/{created['id']}", headers=auth_headers)
    assert resp.status_code == 405


def test_delete_comment_returns_405(client, auth_headers):
    created = client.post("/reviews", json=_payload(), headers=auth_headers).json()
    c = client.post(
        f"/reviews/{created['id']}/comments",
        json={"author_role": "customer", "author_name": "x", "body": "y"},
        headers=auth_headers,
    ).json()
    resp = client.delete(
        f"/reviews/{created['id']}/comments/{c['id']}", headers=auth_headers
    )
    assert resp.status_code == 405


def test_unauthenticated_request_returns_401(client):
    resp = client.get("/reviews")
    assert resp.status_code == 401


def test_patch_unknown_review_returns_404(client, auth_headers):
    resp = client.patch("/reviews/999", json={"rating": 3}, headers=auth_headers)
    assert resp.status_code == 404


def test_post_comment_on_unknown_review_returns_404(client, auth_headers):
    resp = client.post(
        "/reviews/999/comments",
        json={"author_role": "restaurant", "author_name": "Chef", "body": "…"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_rating_zero_returns_422(client, auth_headers):
    resp = client.post("/reviews", json=_payload(rating=0), headers=auth_headers)
    assert resp.status_code == 422


def test_rating_six_returns_422(client, auth_headers):
    resp = client.post("/reviews", json=_payload(rating=6), headers=auth_headers)
    assert resp.status_code == 422
