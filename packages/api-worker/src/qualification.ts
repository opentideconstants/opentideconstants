import { handleRoute } from "./routes.js";
import type { LimitEnvironment } from "./rate-limit.js";
export { RateLimit } from "./rate-limit.js";

interface Environment extends LimitEnvironment {
    ENABLE_LOCAL_QUALIFICATION?: string;
    RELEASES: {
        get(key: string): Promise<{
            body: ReadableStream<Uint8Array>;
            size: number;
        } | null>;
    };
}

const files = new Set([
    "OTC_20261011.jsonl",
    "OTC_20261011.meta.json",
    "OTC_20261011.sha256",
]);
const served: Record<string, number> = {};

export default {
    async fetch(request: Request, env: Environment): Promise<Response> {
        const url = new URL(request.url);
        if (url.hostname !== "127.0.0.1") {
            return new Response("Local qualification only", { status: 403 });
        }
        const ip = request.headers.get("CF-Connecting-IP") ?? "127.0.0.1";
        const limiter = env.LIMITS.get(env.LIMITS.idFromName(ip));
        if (url.pathname === "/__qualification/limits/seed") {
            if (env.ENABLE_LOCAL_QUALIFICATION !== "true" || env.ENABLE_LIMIT_SEED !== "true") {
                return new Response("Local seed disabled", { status: 403 });
            }
            return limiter.fetch(new Request("https://limiter/seed", request));
        }
        const admission = await limiter.fetch(new Request("https://limiter/admit"));
        if (admission.status !== 204) return admission;
        if (env.ENABLE_LOCAL_QUALIFICATION === "true" && request.method === "GET" && url.pathname === "/__qualification/sdk") {
            return probeSdk(url.origin + "/", served);
        }
        const route = await handleRoute(request, { getClient: () => openClient(url.origin + "/") });
        if (route) return route;
        if (env.ENABLE_LOCAL_QUALIFICATION !== "true") return new Response("Not found", { status: 404 });
        const key = url.pathname.slice(1);
        if (request.method !== "GET" || !files.has(key)) {
            return new Response("Not found", { status: 404 });
        }
        const object = await env.RELEASES.get(key);
        if (!object) {
            return new Response("Release object missing", { status: 404 });
        }
        const measured = object.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
            transform(chunk, controller) {
                served[key] = (served[key] ?? 0) + chunk.byteLength;
                controller.enqueue(chunk);
            },
        }));
        return new Response(measured, {
            headers: {
                "content-type": "application/octet-stream",
                "content-length": String(object.size),
            },
        });
    },
};
import { openClient, probeSdk } from "./sdk-probe.js";
