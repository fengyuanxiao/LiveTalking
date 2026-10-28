"""Live control API and authenticated ingress for external platform adapters."""

import hmac
import os

from aiohttp import web

from .live import LiveManager, PLATFORMS, clean_string
from utils.logger import logger


def setup_live_routes(app, session_manager):
    manager = LiveManager(session_manager.get_session)
    app["live_manager"] = manager
    app.on_shutdown.append(manager.close)

    def ok(data=None):
        return web.json_response({"code": 0, "msg": "ok", "data": data})

    def guarded(handler):
        async def wrapper(request):
            try:
                return await handler(request)
            except (ValueError, TypeError, KeyError) as exc:
                return web.json_response({"code": -1, "msg": str(exc)}, status=400)
            except Exception:
                logger.exception("Live API failed")
                return web.json_response({"code": -1, "msg": "直播操作失败，请查看服务日志"}, status=500)
        return wrapper

    async def body(request):
        if request.content_length and request.content_length > 65536:
            raise ValueError("请求内容过大")
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("请求必须是JSON对象")
        return data

    async def products(request):
        if request.method == "GET":
            return ok(list(manager.store.products.values()))
        return ok(manager.store.save(await body(request)))

    async def platforms(request):
        return ok([{"id": key, "name": label, "connection": "bridge_required",
                    "ingest_configured": bool(os.getenv("LIVE_INGEST_TOKEN"))}
                   for key, label in PLATFORMS.items()])

    async def start(request):
        data = await body(request)
        sessionid = clean_string(data.get("sessionid"), "会话ID", 160, True)
        return ok(await manager.start(sessionid, data))

    async def stop(request):
        data = await body(request)
        sessionid = clean_string(data.get("sessionid"), "会话ID", 160, True)
        await manager.stop(sessionid)
        return ok()

    async def status(request):
        sessionid = request.query.get("sessionid", "")
        controller = manager.controllers.get(sessionid)
        return ok(controller.snapshot() if controller else {"running": False, "phase": "stopped"})

    async def comment(request):
        data = await body(request)
        controller = manager.controllers.get(data.get("sessionid"))
        if not controller or not controller.running:
            raise ValueError("请先开始自动讲解")
        return ok({"result": controller.comment(data)})

    async def ingest(request):
        secret = os.getenv("LIVE_INGEST_TOKEN", "")
        supplied = request.headers.get("Authorization", "")
        if not secret:
            return web.json_response({"code": -1, "msg": "尚未配置LIVE_INGEST_TOKEN"}, status=503)
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            return web.json_response({"code": -1, "msg": "弹幕入口认证失败"}, status=401)
        data = await body(request)
        platform = data.get("platform")
        if platform not in PLATFORMS:
            raise ValueError("无效的平台ID")
        room_id = clean_string(data.get("room_id"), "直播间ID", 160, True)
        # Validate even when no matching room is running.
        clean_string(data.get("text"), "弹幕", 500, True)
        clean_string(data.get("username", "观众"), "昵称", 80)
        clean_string(data.get("event_id", ""), "事件ID", 160)
        results = []
        for sid, controller in list(manager.controllers.items()):
            if (controller.running and controller.options["platform"] == platform
                    and controller.options["room_id"] == room_id):
                results.append({"sessionid": sid, "result": controller.comment(data, "bridge")})
        return ok({"deliveries": results})

    for method, path, handler in [
        ("GET", "/api/live/products", products), ("POST", "/api/live/products", products),
        ("GET", "/api/live/platforms", platforms), ("GET", "/api/live/status", status),
        ("POST", "/api/live/start", start), ("POST", "/api/live/stop", stop),
        ("POST", "/api/live/comment", comment), ("POST", "/api/live/ingest", ingest),
    ]:
        app.router.add_route(method, path, guarded(handler))
