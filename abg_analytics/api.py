import asyncio
import logging

from aiohttp import web

log = logging.getLogger("ABGModelAnalytics")
PREFIX = "/abg-model-analytics"


def register_routes(routes, analytics):
    async def respond(request, action, mutation=False):
        try:
            if mutation:
                # Require JSON so a cross-origin HTML form cannot move models.
                if request.content_type != "application/json":
                    raise ValueError("Use an application/json request.")
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
                data = await asyncio.to_thread(action, body)
            else:
                data = await asyncio.to_thread(action)
            return web.json_response(data)
        except (ValueError, OSError) as error:
            return web.json_response({"error": str(error)}, status=400)
        except Exception:
            log.exception("Analytics request failed")
            return web.json_response(
                {"error": "Analytics operation failed. See the ComfyUI console for details."}, status=500
            )

    @routes.get(PREFIX + "/inventory")
    async def inventory(request):
        return await respond(request, analytics.inventory)

    @routes.get(PREFIX + "/settings")
    async def settings(request):
        return await respond(request, analytics.store.settings)

    @routes.post(PREFIX + "/settings")
    async def save_settings(request):
        return await respond(request, analytics.configure, True)

    @routes.post(PREFIX + "/protection")
    async def protection(request):
        return await respond(request, lambda body: analytics.protect(body.get("model_id"), body.get("protected")), True)

    @routes.post(PREFIX + "/duplicates/scan")
    async def duplicates(request):
        return await respond(request, lambda body: analytics.find_duplicates(), True)

    @routes.get(PREFIX + "/trash/summary")
    async def trash_summary(request):
        return await respond(request, analytics.trash_summary)

    @routes.post(PREFIX + "/trash/preview")
    async def preview(request):
        return await respond(request, lambda body: analytics.preview(body.get("model_ids")), True)

    @routes.post(PREFIX + "/trash")
    async def trash(request):
        return await respond(request, lambda body: analytics.trash(body.get("plan_id")), True)

    @routes.get(PREFIX + "/moves")
    async def moves(request):
        return await respond(request, lambda: {"moves": analytics.store.moves()})

    @routes.post(PREFIX + "/restore")
    async def restore(request):
        return await respond(request, lambda body: analytics.restore(body.get("move_id")), True)
