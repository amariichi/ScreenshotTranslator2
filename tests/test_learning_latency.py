"""Latency changes retain lifecycle, source and notification contracts."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from app.learning import LearningError
from app.learning_api import changes as changes_route
from app.learning_model import FRAME_SCHEMA, LearningModel
import test_learning as fixtures


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LearningTests.asyncSetUp
    asyncTearDown = fixtures.LearningTests.asyncTearDown
    control = fixtures.LearningTests.control
    frame = fixtures.LearningTests.frame
    async def test_frame_completion_wakes_and_coalesces_without_persisting_revision(self):
        self.control(True)
        revision = self.service.snapshot(self.sid)["revision"]
        waiting = asyncio.create_task(self.service.changes(self.sid, revision, 1))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        await self.frame()
        result = await waiting
        self.assertTrue(result["changed"])
        self.assertEqual(result["revision"], result["snapshot"]["revision"])
        self.assertEqual(result["snapshot"]["entry_count"], 1)
        self.assertNotIn("revision", self.service.document(self.sid))
        quiet = await self.service.changes(self.sid, result["revision"], 0)
        self.assertEqual(quiet, {"changed": False, "revision": result["revision"]})

    async def test_cancelling_one_waiter_keeps_other_waiter_and_next_subscription(self):
        revision = self.service.snapshot(self.sid)["revision"]
        first = asyncio.create_task(self.service.changes(self.sid, revision, 1))
        second = asyncio.create_task(self.service.changes(self.sid, revision, 1))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.service.update(self.sid, {"title": "Changed"})
        result = await second
        self.assertEqual(result["snapshot"]["title"], "Changed")
        self.assertLessEqual(len(self.service.change_events), 1)

    async def test_lease_expiry_wakes_without_poll_or_inference(self):
        import time
        self.service.clock = time.time
        self.service.LEASE_SECONDS = 0.02
        self.control(True)
        result = await self.service.changes(self.sid, self.service.revisions[self.sid], 0.5)
        self.assertTrue(result["changed"])
        self.assertFalse(result["snapshot"]["capturing"])
        self.assertEqual(self.model.calls + self.model.notes_calls, 0)

    async def test_deleted_record_wakes_waiter_as_not_found(self):
        waiting = asyncio.create_task(self.service.changes(self.sid, self.service.revisions[self.sid], 1))
        await asyncio.sleep(0)
        self.service.trash(self.sid, self.service.document(self.sid)["title"])
        with self.assertRaises(LearningError) as error:
            await waiting
        self.assertEqual(error.exception.status, 404)

    async def test_shutdown_wakes_waiters_and_closes_model_once(self):
        from unittest.mock import AsyncMock
        self.model.close = AsyncMock()
        waiting = asyncio.create_task(self.service.changes(self.sid, self.service.revisions[self.sid], 1))
        await asyncio.sleep(0)
        await self.service.close()
        with self.assertRaises(LearningError) as error:
            await waiting
        self.assertEqual(error.exception.status, 503)
        self.model.close.assert_awaited_once()
        await self.service.close()

    async def test_disconnect_cancels_endpoint_waiter(self):
        started, disconnect, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def waiting(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        with patch.object(self.service, "changes", waiting):
            task = asyncio.create_task(changes_route(self.sid, SimpleNamespace(receive=receive),
                                                     0, 20, self.service))
            await started.wait()
            disconnect.set()
            self.assertEqual((await task).status_code, 204)
            self.assertTrue(cancelled.is_set())


class PersistentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_client_recovers_connection_and_closes(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("synthetic disconnect")
            return httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps(fixtures.translated())}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = LearningModel()
        settings = SimpleNamespace(api_base="http://127.0.0.1:18099", model_name="fixture")
        with patch("app.learning_model.get_settings", return_value=settings), \
                patch("app.learning_model.httpx.AsyncClient", return_value=client) as factory:
            with self.assertRaises(httpx.ConnectError):
                await model._request("test", FRAME_SCHEMA)
            await model._request("test", FRAME_SCHEMA)
            await model._request("test", FRAME_SCHEMA)
            factory.assert_called_once_with(timeout=60, follow_redirects=False, trust_env=False)
            settings.api_base = "https://example.test"
            with self.assertRaises(ValueError):
                await model._request("test", FRAME_SCHEMA)
            self.assertEqual(len(calls), 3)
            await model.close()
            self.assertTrue(client.is_closed)
            settings.api_base = "http://127.0.0.1:18099"
            with self.assertRaises(RuntimeError):
                await model._request("test", FRAME_SCHEMA)

