// Local API-W qualification only. Transactional storage follows:
// https://developers.cloudflare.com/durable-objects/api/legacy-kv-storage-api/
interface Counters {
    minute: number;
    day: number;
    minute_count: number;
    day_count: number;
}

interface CounterStorage {
    get<T>(key: string): Promise<T | undefined>;
    put<T>(key: string, value: T): Promise<void>;
}

interface CounterState {
    storage: CounterStorage & {
        transaction<T>(callback: (storage: CounterStorage) => Promise<T>): Promise<T>;
    };
}

export interface LimitEnvironment {
    ENABLE_LIMIT_SEED?: string;
    LIMITS: {
        idFromName(name: string): { toString(): string };
        get(id: { toString(): string }): { fetch(request: Request): Promise<Response> };
    };
}

export class RateLimit {
    constructor(private readonly state: CounterState, private readonly env: LimitEnvironment) {}

    async fetch(request: Request): Promise<Response> {
        const now = Date.now();
        const minute = Math.floor(now / 60000);
        const day = Math.floor(now / 86400000);
        if (new URL(request.url).pathname === "/seed") {
            // Enabled only by the local proof's wrangler --var, never in configuration.
            if (request.method !== "POST" || this.env.ENABLE_LIMIT_SEED !== "true") {
                return new Response("Local seed disabled", { status: 403 });
            }
            const input: unknown = await request.json();
            if (typeof input !== "object" || input === null || !("minute_count" in input) ||
                !("day_count" in input) || input.minute_count !== 0 ||
                (input.day_count !== 0 && input.day_count !== 9999)) {
                return new Response("Invalid qualification seed", { status: 400 });
            }
            const counters: Counters = { minute, day, minute_count: 0, day_count: input.day_count };
            await this.state.storage.put("counters", counters);
            return Response.json(counters);
        }
        return this.state.storage.transaction(async (storage) => {
            const saved = await storage.get<Counters>("counters");
            const counters: Counters = {
                minute, day,
                minute_count: saved?.minute === minute ? saved.minute_count : 0,
                day_count: saved?.day === day ? saved.day_count : 0,
            };
            const limit = counters.day_count >= 10000 ? "day" : counters.minute_count >= 100 ? "minute" : null;
            if (limit) return Response.json({ limit }, { status: 429 });
            counters.minute_count++;
            counters.day_count++;
            await storage.put("counters", counters);
            return new Response(null, { status: 204 });
        });
    }
}
