import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.live import LiveController, LiveManager, ProductStore
from server.live_routes import setup_live_routes


class FakeAvatar:
    def __init__(self):
        self.opt = SimpleNamespace(llm_provider="dashscope")
        self.msgqueues = []
        self.messages = []
        self.live_run_id = None
        self.flushes = 0

    def flush_talk(self):
        self.flushes += 1

    def add_msgqueue(self, q):
        self.msgqueues.append(q)

    def put_msg_txt(self, text, metadata):
        self.messages.append((text, metadata))

    def event(self, index=-1, status="end", **overrides):
        event = {**self.messages[index][1], "status": status, **overrides}
        for q in list(self.msgqueues):
            q.put(json.dumps(event))


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.avatar = FakeAvatar()
        self.product = {"id": "cup", "name": "杯子", "facts": "500毫升", "scripts": ["介绍第一段", "介绍第二段"]}
        self.options = {"platform": "douyin", "room_id": "room1", "mode": "script", "style": "自然亲切",
                        "pause_min": 0, "pause_max": 0, "reply_interval": 0}
        self.controller = LiveController("s1", self.avatar, lambda sid: self.avatar, self.product, self.options)

    async def asyncTearDown(self):
        await self.controller.stop()

    async def test_waits_for_own_playback_end_even_during_synthesis(self):
        self.controller.start()
        await until(lambda: len(self.avatar.messages) == 1)
        self.avatar.event(status="end", live_turn_id="old-turn")
        await asyncio.sleep(0.12)
        self.assertEqual(len(self.avatar.messages), 1)
        self.avatar.event(status="start")
        await until(lambda: self.controller.phase == "speaking")
        self.assertEqual(len(self.avatar.messages), 1)
        self.avatar.event()
        await until(lambda: len(self.avatar.messages) == 2)
        self.assertEqual(self.avatar.messages[1][0], "介绍第二段")

    async def test_replies_then_resumes_and_limits_consecutive_answers(self):
        self.controller.start()
        await until(lambda: len(self.avatar.messages) == 1)
        for text in ["价格？", "容量？", "材质？"]:
            self.assertEqual(self.controller.comment({"text": text}), "accepted")
        self.assertEqual(self.controller.comment({"text": "容量？"}), "duplicate")
        for count in range(1, 4):
            self.avatar.event()
            await until(lambda: len(self.avatar.messages) == count + 1)
        self.assertEqual(self.avatar.messages[3][0], "介绍第二段")
        self.assertEqual(self.controller.index, 1)
        self.assertEqual(len(self.controller.comments), 1)

    async def test_tts_failure_stops_without_advancing(self):
        self.controller.start()
        await until(lambda: len(self.avatar.messages) == 1)
        self.avatar.event(status="tts_error")
        await until(lambda: not self.controller.running)
        self.assertEqual(self.controller.phase, "error")
        self.assertEqual(self.controller.index, 0)
        self.assertIsNone(self.avatar.live_run_id)
        self.assertEqual(self.avatar.msgqueues, [])

    async def test_stopping_during_generation_cannot_enqueue_late_answer(self):
        started, release = threading.Event(), threading.Event()

        def generator(*args):
            started.set()
            release.wait(2)
            return "不应播报的旧回答"

        self.controller.options["mode"] = "llm"
        self.controller.generator = generator
        self.controller.start()
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await self.controller.stop()
        finally:
            release.set()
        await asyncio.sleep(0.1)
        self.assertEqual(self.avatar.messages, [])
        self.assertEqual(self.avatar.msgqueues, [])

    async def test_ai_reply_has_product_context_and_resumes_next_topic(self):
        requests = []

        def generate(opt, product, topic, comment, history, recent, style):
            requests.append({"product": product, "topic": topic, "comment": comment, "history": history})
            return "回答容量500毫升" if comment else "介绍产品" + str(len(requests))

        self.options["mode"] = "llm"
        self.controller.generator = generate
        self.controller.start()
        await until(lambda: len(self.avatar.messages) == 1)
        self.controller.comment({"text": "容量？"})
        self.avatar.event()
        await until(lambda: len(self.avatar.messages) == 2)
        self.assertEqual(self.avatar.messages[1][0], "回答容量500毫升")
        self.assertEqual(requests[1]["product"]["facts"], "500毫升")
        self.assertEqual(requests[1]["comment"]["text"], "容量？")
        self.avatar.event()
        await until(lambda: len(self.avatar.messages) == 3)
        self.assertIsNone(requests[2]["comment"])
        self.assertEqual(requests[1]["topic"], requests[2]["topic"])
        self.assertEqual(requests[2]["history"][-1]["content"], "回答容量500毫升")

    async def test_comment_queue_is_bounded_and_old_comments_expire(self):
        self.controller.start()
        for index in range(30):
            self.assertEqual(self.controller.comment({"text": str(index)}), "accepted")
        self.assertEqual(self.controller.comment({"text": "overflow"}), "queue_full")
        for comment in self.controller.comments:
            comment["received"] -= 100
        self.assertIsNone(self.controller.next_comment())
        self.assertEqual(len(self.controller.comments), 0)

    async def test_disconnect_stops_scheduling(self):
        self.controller.start()
        await until(lambda: len(self.avatar.messages) == 1)
        self.controller.lookup = lambda sid: None
        await until(lambda: not self.controller.running)
        self.assertIn("断开", self.controller.error)


class LiveAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.avatar = FakeAvatar()
        self.app = web.Application()
        setup_live_routes(self.app, SimpleNamespace(get_session=lambda sid: self.avatar if sid == "s1" else None))
        self.manager = self.app["live_manager"]
        self.manager.store = ProductStore(Path(self.temp.name) / "products.json")
        self.product = self.manager.store.save({"name": "产品", "facts": "产品事实", "scripts": ["第一段", "第二段"]})
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def start(self, **options):
        response = await self.client.post("/api/live/start", json={"sessionid": "s1", "product_id": self.product["id"],
                                          "platform": "douyin", "room_id": "room1", "mode": "script", **options})
        self.assertEqual(response.status, 200, await response.text())
        await until(lambda: len(self.avatar.messages) >= 1)

    async def test_products_persist_and_switch_cancels_old_run(self):
        loaded = ProductStore(self.manager.store.path)
        self.assertEqual(loaded.products[self.product["id"]]["name"], "产品")
        await self.start()
        previous = self.manager.controllers["s1"]
        new = self.manager.store.save({"name": "新产品", "facts": "新资料", "scripts": ["新产品第一段"]})
        await self.start(product_id=new["id"])
        await until(lambda: len(self.avatar.messages) == 2)
        self.assertFalse(previous.running)
        self.assertEqual(self.avatar.messages[1][0], "新产品第一段")
        self.assertNotEqual(self.avatar.messages[0][1]["live_run_id"], self.avatar.messages[1][1]["live_run_id"])

    async def test_ingress_auth_room_routing_and_deduplication(self):
        await self.start()
        event = {"platform": "douyin", "room_id": "room1", "event_id": "e1", "text": "容量多大"}
        with patch.dict("os.environ", {"LIVE_INGEST_TOKEN": "test-secret"}):
            response = await self.client.post("/api/live/ingest", json=event)
            self.assertEqual(response.status, 401)
            headers = {"Authorization": "Bearer test-secret"}
            response = await self.client.post("/api/live/ingest", json={**event, "platform": "taobao"}, headers=headers)
            self.assertEqual((await response.json())["data"]["deliveries"], [])
            response = await self.client.post("/api/live/ingest", json={**event, "room_id": "other"}, headers=headers)
            self.assertEqual((await response.json())["data"]["deliveries"], [])
            for expected in ["accepted", "duplicate"]:
                response = await self.client.post("/api/live/ingest", json=event, headers=headers)
                self.assertEqual((await response.json())["data"]["deliveries"][0]["result"], expected)
        self.assertEqual(len(self.manager.controllers["s1"].comments), 1)

    async def test_missing_bridge_secret_and_invalid_start_are_explicit(self):
        with patch.dict("os.environ", {"LIVE_INGEST_TOKEN": ""}):
            response = await self.client.post("/api/live/ingest", json={})
            self.assertEqual(response.status, 503)
        response = await self.client.post("/api/live/start", json={"sessionid": "missing", "product_id": self.product["id"]})
        self.assertEqual(response.status, 400)
        response = await self.client.get("/api/live/platforms")
        platforms = (await response.json())["data"]
        self.assertEqual(len(platforms), 7)
        self.assertTrue(all(p["connection"] == "bridge_required" for p in platforms))

    async def test_invalid_product_does_not_overwrite_catalogue(self):
        response = await self.client.post("/api/live/products", json={"name": "bad", "facts": "data", "scripts": ["x" * 241]})
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.manager.store.products), 1)

    async def test_concurrent_restarts_leave_one_controller_and_event_subscription(self):
        await self.start()
        settings = {"product_id": self.product["id"], "mode": "script"}
        await asyncio.gather(self.manager.start("s1", settings), self.manager.start("s1", settings))
        self.assertTrue(self.manager.controllers["s1"].running)
        self.assertEqual(len(self.avatar.msgqueues), 1)
        self.assertIs(self.avatar.live_controller, self.manager.controllers["s1"])


if __name__ == "__main__":
    unittest.main()
