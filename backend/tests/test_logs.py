import asyncio
import json
import threading
from functools import partial

import pytest
from fastapi.testclient import TestClient

from portal.services import streaming
from portal.services.logs import LogWriter, read_logs
from portal.services.streaming import environment_events, read_snapshot
from tests.conftest import Portal

PROVISION_OUTPUT = [
    "[portal] planning static-site@v1",
    "Initializing the backend...",
    "Terraform has been successfully initialized!",
    "  # fake.network will be created",
    "  # fake.compute will be created",
    "  # fake.database will be created",
    "Plan: 3 to add, 0 to change, 0 to destroy.",
    "[portal] policy check passed",
    "fake.network: Creation complete",
    "fake.compute: Creation complete",
    "fake.database: Creation complete",
    "Apply complete! Resources: 3 added, 0 changed, 0 destroyed.",
    "[portal] environment is ready",
]


def parse_events(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append(
            {
                "id": int(fields["id"]) if "id" in fields else None,
                "event": fields["event"],
                "data": json.loads(fields["data"]),
            }
        )
    return events


def collect(portal: Portal, environment_id: str, **kwargs) -> list[dict]:
    async def run() -> str:
        read = partial(read_snapshot, portal.container.sessions, environment_id)
        kwargs.setdefault("poll_interval", 0)
        return "".join([chunk async for chunk in environment_events(read, **kwargs)])

    return parse_events(asyncio.run(run()))


def log_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event["event"] == "log"]


def test_lines_are_stored_in_the_order_terraform_printed_them(portal: Portal) -> None:
    environment_id = portal.ready()

    lines = portal.logs(environment_id)

    assert [line.message for line in lines] == PROVISION_OUTPUT
    assert [line.seq for line in lines] == list(range(1, len(PROVISION_OUTPUT) + 1))


def test_sequence_continues_without_gaps_across_operations(portal: Portal) -> None:
    environment_id = portal.ready()
    portal.destroy(environment_id)
    portal.run_pending()

    lines = portal.logs(environment_id)
    provision, destroy = portal.operations(environment_id)

    assert [line.seq for line in lines] == list(range(1, len(lines) + 1))
    assert {line.operation_id for line in lines[: len(PROVISION_OUTPUT)]} == {provision.id}
    assert {line.operation_id for line in lines[len(PROVISION_OUTPUT) :]} == {destroy.id}
    assert lines[-1].message == "[portal] environment destroyed"


def test_logs_of_different_environments_do_not_mix(portal: Portal) -> None:
    first = portal.ready(name="one")
    second = portal.ready(name="two")

    for environment_id in (first, second):
        lines = portal.logs(environment_id)
        assert [line.seq for line in lines] == list(range(1, len(PROVISION_OUTPUT) + 1))


def test_failed_run_keeps_the_error_output(portal: Portal) -> None:
    portal.runner.apply_fails_after = 1
    environment_id = portal.ready()

    messages = [line.message for line in portal.logs(environment_id)]

    assert "Error: creating fake.compute: quota exceeded" in messages
    assert messages[-1].startswith("[portal] provision failed during apply")


def test_writer_recovers_when_another_writer_took_its_sequence_number(portal: Portal) -> None:
    environment_id = portal.request()
    (operation_id,) = portal.queue.pending
    sessions, clock = portal.container.sessions, portal.clock
    first = LogWriter(sessions, clock, environment_id, operation_id)
    second = LogWriter(sessions, clock, environment_id, operation_id)

    first.write("a")
    second.write("b")
    first.write("c")

    lines = portal.logs(environment_id)
    assert [(line.seq, line.message) for line in lines] == [(1, "a"), (2, "b"), (3, "c")]


def test_read_logs_pages_with_after_and_limit(portal: Portal) -> None:
    environment_id = portal.ready()

    with portal.container.sessions() as session:
        page = read_logs(session, environment_id, after=3, limit=4)

    assert [line.seq for line in page] == [4, 5, 6, 7]


def test_stream_of_a_finished_operation_replays_everything_then_ends(portal: Portal) -> None:
    environment_id = portal.ready()

    events = collect(portal, environment_id)

    logs = log_events(events)
    assert [event["data"]["message"] for event in logs] == PROVISION_OUTPUT
    assert [event["id"] for event in logs] == list(range(1, len(PROVISION_OUTPUT) + 1))
    assert events[-2] == {
        "id": None,
        "event": "status",
        "data": {"status": "ready", "active": False},
    }
    assert events[-1] == {
        "id": None,
        "event": "end",
        "data": {"last_seq": len(PROVISION_OUTPUT)},
    }


def test_stream_resumes_after_the_last_line_the_client_saw(portal: Portal) -> None:
    environment_id = portal.ready()

    events = collect(portal, environment_id, after=10)

    assert [event["id"] for event in log_events(events)] == [11, 12, 13]


def test_stream_is_complete_when_output_exceeds_one_batch(
    portal: Portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(streaming, "BATCH_SIZE", 4)
    environment_id = portal.ready()

    events = collect(portal, environment_id)

    assert [event["data"]["message"] for event in log_events(events)] == PROVISION_OUTPUT
    assert events[-1]["event"] == "end"


def test_stream_follows_a_running_operation_to_the_end(portal: Portal) -> None:
    environment_id = portal.request()
    apply_started = threading.Event()
    let_apply_finish = threading.Event()

    def pause_apply(_workspace) -> None:
        apply_started.set()
        assert let_apply_finish.wait(timeout=10)

    portal.runner.hooks["apply"] = pause_apply
    worker = threading.Thread(target=portal.run_pending)
    worker.start()
    assert apply_started.wait(timeout=10)

    polls: list[int] = []
    received_before_release: list[int] = []

    async def run() -> list[dict]:
        chunks: list[str] = []

        async def sleep(_seconds: float) -> None:
            # The first sleep means the stream has caught up with a still-running
            # operation. Only then is the apply allowed to finish.
            polls.append(len(chunks))
            if len(polls) == 1:
                received_before_release.append(len(chunks))
                let_apply_finish.set()
                worker.join(timeout=10)

        read = partial(read_snapshot, portal.container.sessions, environment_id)
        async for chunk in environment_events(read, poll_interval=0, sleep=sleep):
            chunks.append(chunk)
        return parse_events("".join(chunks))

    events = asyncio.run(run())
    worker.join(timeout=10)

    logs = log_events(events)
    assert [event["data"]["message"] for event in logs] == PROVISION_OUTPUT
    assert [event["id"] for event in logs] == list(range(1, len(PROVISION_OUTPUT) + 1))
    # Lines up to the policy check arrived while the apply was still blocked.
    assert received_before_release[0] >= 8
    statuses = [event["data"]["status"] for event in events if event["event"] == "status"]
    assert statuses == ["applying", "ready"]
    assert events[-1]["event"] == "end"
    assert [line.message for line in portal.logs(environment_id)] == PROVISION_OUTPUT


def test_stream_endpoint_speaks_server_sent_events(client: TestClient, portal: Portal) -> None:
    environment_id = portal.ready()

    response = client.get(f"/api/environments/{environment_id}/logs/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_events(response.text)
    assert [event["data"]["message"] for event in log_events(events)] == PROVISION_OUTPUT
    assert events[-1]["event"] == "end"


def test_stream_endpoint_honours_last_event_id(client: TestClient, portal: Portal) -> None:
    environment_id = portal.ready()

    response = client.get(
        f"/api/environments/{environment_id}/logs/stream", headers={"Last-Event-ID": "11"}
    )

    assert [event["id"] for event in log_events(parse_events(response.text))] == [12, 13]


def test_stream_endpoint_returns_404_for_unknown_environment(client: TestClient) -> None:
    assert client.get("/api/environments/nope/logs/stream").status_code == 404


def test_logs_endpoint_returns_lines_after_a_cursor(client: TestClient, portal: Portal) -> None:
    environment_id = portal.ready()

    response = client.get(f"/api/environments/{environment_id}/logs", params={"after": 11})

    assert response.status_code == 200
    assert [line["seq"] for line in response.json()] == [12, 13]
    assert response.json()[-1]["message"] == "[portal] environment is ready"
