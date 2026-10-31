from fastapi.testclient import TestClient

from shiritaibot.core import canonical, now
from shiritaibot.web import create_app


async def seed(runtime):
    # Equal timestamps exercise deterministic tie ordering beyond the old 500-record cap.
    async with runtime.store.lock:
        await runtime.store.db.executemany(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            [
                ("event", f"event-{i:04}", "", canonical({"id": f"event-{i:04}"}), 1, 1)
                for i in range(505)
            ],
        )
        await runtime.store.db.executemany(
            "INSERT INTO mem_fact(subject,content,as_of,authority,confidence,status,created,updated) "
            "VALUES(NULL,?,1,'owner',1,?,1,1)",
            [(f"分页事实 {i}", "archived" if i % 2 else "active") for i in range(205)],
        )
        await runtime.store.db.execute(
            "INSERT INTO mem_conflict(kind,items,note,status,created) VALUES('fact','[]','待确认的问题','open',?)",
            (now().timestamp(),),
        )
        await runtime.store.db.commit()
    for i in range(25):
        await runtime.store.put(
            "approval", f"pending-{i}", {"status": "pending", "reviewer": "human"}
        )
        await runtime.store.put("approval", f"model-{i}", {"status": "denied", "reviewer": "model"})
    await runtime.store.put("approval", "human-finished", {"status": "approved"})
    await runtime.store.put("approval", "model-pending", {"status": "pending", "reviewer": "model"})


def test_records_and_memory_paginate_all_history_with_filters(tmp_path):
    api = create_app(tmp_path, token="admin")
    with TestClient(api) as client:
        assert client.get("/api/records/event?page=1").status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        client.portal.call(seed, api.state.runtime)

        events = [
            client.get(f"/api/records/event?page={page}&page_size=100").json()
            for page in range(1, 7)
        ]
        assert events[0]["total"] == 505
        ids = [row["id"] for page in events for row in page["items"]]
        assert len(ids) == len(set(ids)) == 505
        assert ids == sorted(ids, reverse=True)
        last = client.get("/api/records/event?page=100&page_size=100").json()
        assert last["page"] == 6 and len(last["items"]) == 5
        # Keep existing unpaginated consumers compatible.
        assert isinstance(client.get("/api/records/event").json(), list)

        pending = client.get("/api/records/approval?page=2&approval_filter=pending").json()
        assert pending["total"] == 25 and len(pending["items"]) == 5
        assert all(row["id"].startswith("pending-") for row in pending["items"])
        history = client.get("/api/records/approval?page=1&approval_filter=history").json()
        assert history["total"] == 27
        assert {"human-finished", "model-pending"} <= {row["id"] for row in history["items"]}

        pages = [client.get(f"/api/memory?page={page}&page_size=100").json() for page in (1, 2, 3)]
        refs = [row["id"] for page in pages for row in page["items"]]
        assert pages[0]["total"] == 206  # excludes the fixed owner entity
        assert len(refs) == len(set(refs)) == 206
        assert pages[0]["items"][0]["type"] == "conflict"
        facts = client.get("/api/memory?kind=fact&q=分页事实&page=3&page_size=100").json()
        assert facts["total"] == 205 and len(facts["items"]) == 5
        assert all(row["type"] == "fact" for row in facts["items"])
        assert any(row["status"] == "archived" for row in facts["items"])
        conflicts = client.get("/api/memory?kind=conflict&q=待确认&page=1").json()
        assert conflicts["total"] == 1
        empty = client.get("/api/memory?q=无匹配内容&page=12").json()
        assert empty["total"] == 0 and empty["items"] == [] and empty["page"] == 1
        for path in [
            "/api/records/event?page=0",
            "/api/records/event?page=1&page_size=101",
            "/api/records/event?page=1&approval_filter=bad",
            "/api/memory?page=-1",
            "/api/memory?page=1&page_size=0",
            "/api/memory?kind=bad&page=1",
        ]:
            assert client.get(path).status_code == 422
