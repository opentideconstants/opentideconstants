import {
    InvalidArgumentError, OpenTideConstantsError,
    type OpenTideConstants, type Station, type HeightOptions, type CurrentOptions,
    type HeightSeries, type ExtremeList, type CurrentSeries, type CurrentEventList,
} from "opentideconstants";
import document from "../../../api/openapi.json";

export interface RouteContext {
    getClient(): Promise<OpenTideConstants>;
}

type Prediction = HeightSeries | ExtremeList | CurrentSeries | CurrentEventList;

// The encodings below mirror conformance/runners/node/ops.mjs at the pinned SDK.
// HTTP adds metadata headers; it does not add fields to that wire body.
function encStation(station: Station | null): unknown {
    if (station === null) return null;
    return {
        station_id: station.station_id, name: station.name, country: station.country,
        lat: station.lat, lon: station.lon, type: station.type, kind: station.kind,
        timezone: station.timezone, aliases: station.aliases, status: station.status,
        is_reference: station.isReference, is_subordinate: station.isSubordinate,
        is_tide: station.isTide, is_current: station.isCurrent,
        recommended_set_id: station.recommendedSet?.set_id ?? null,
        ...(station.recommended_is_fallback === null ? {} : {
            recommended_is_fallback: station.recommended_is_fallback,
        }),
    };
}

function encPrediction(result: Prediction, units: boolean): unknown {
    const names = [
        "station_id", "timezone", "method", "set_id", "convention_id", "astro_table_id",
        "reference_station_id", "reference_bin", "datum", "datum_name", "datum_kind",
        "datum_uncertainty_m", "bin_number", "omitted_event_types", "tz", "tz_version",
        "release", "algorithm",
    ];
    const fields = Object.fromEntries(Object.entries(result));
    return {
        ...Object.fromEntries(names.map(key => [key, fields[key] === undefined ? null : fields[key]])),
        ...(units ? { units: result.units } : {}),
        items: result.items.map(item => ({
            ...Object.fromEntries(Object.entries(item).filter(([key]) => key !== "units").map(([key, value]) => [
                key, key === "time" ? item.time.toISOString().replace(/\.\d{3}Z$/, "Z") : value,
            ])),
            ...(units ? {
                units: item.units,
                ...("height" in item ? { height: item.height } :
                    "velocity" in item ? { velocity: item.velocity } : {
                        velocity_major: item.velocity_major,
                        velocity_minor: item.velocity_minor,
                        speed: item.speed,
                    }),
            } : {}),
        })),
    };
}

function finite(value: unknown): unknown {
    if (typeof value === "number") {
        if (Number.isNaN(value)) return null;
        if (!Number.isFinite(value)) throw new RangeError(`the SDK returned ${value}, which JSON cannot carry`);
        return value;
    }
    if (typeof value === "bigint" || typeof value === "symbol" || typeof value === "function") {
        throw new TypeError(`the runner cannot encode a ${typeof value} as JSON`);
    }
    if (value === undefined) throw new TypeError("the SDK gave no value (undefined)");
    if (Array.isArray(value)) return Array.from(value, finite);
    if (value !== null && typeof value === "object") {
        if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
            throw new TypeError(`the runner cannot encode a ${value.constructor?.name ?? "object"} as JSON`);
        }
        return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, finite(entry)]));
    }
    return value;
}

function encError(error: unknown): unknown {
    if (!(error instanceof OpenTideConstantsError)) {
        const name = error instanceof Error ? error.constructor.name : typeof error;
        return { ok: false, error: {
            code: `uncaught:${name}`,
            message: String(error instanceof Error ? error.stack ?? error : error),
        } };
    }
    const fields = Object.fromEntries(Object.entries(error).filter(([key, value]) =>
        ["url", "status", "file", "expected", "actual", "path", "removed", "constituent",
            "first_year", "last_year", "missing", "zone", "reference_station_id"].includes(key)
        && value !== null && value !== undefined));
    if ("tombstone" in error && error.tombstone !== null) {
        const tombstone = error.tombstone;
        if (typeof tombstone !== "object") throw new TypeError("the SDK gave no tombstone");
        const values = Object.fromEntries(Object.entries(tombstone));
        fields.tombstone = Object.fromEntries(
            ["station_id", "name", "status", "removed_in", "removed_reason", "merged_into", "detail"]
                .map(key => [key, values[key]]),
        );
    }
    return { ok: false, error: { code: error.code, message: error.message, fields: finite(fields) } };
}

function metadata(client: OpenTideConstants | undefined, stations?: readonly (Station | null)[]): HeadersInit {
    if (client === undefined) return {};
    return {
        Attribution: encodeURIComponent(client.attribution(stations)),
        DATESTAMP: client.release.datestamp,
    };
}

async function reply(context: RouteContext, run: (client: OpenTideConstants, selected: (Station | null)[]) => Promise<unknown> | unknown): Promise<Response> {
    let client: OpenTideConstants | undefined;
    const selected: (Station | null)[] = [];
    try {
        client = await context.getClient();
        return Response.json({ ok: true, result: finite(await run(client, selected)) }, { headers: metadata(client, selected) });
    } catch (error) {
        return Response.json(encError(error), { status: 400, headers: metadata(client, selected) });
    }
}

function params(request: Request): URLSearchParams {
    return new URL(request.url).searchParams;
}

function required(query: URLSearchParams, name: string): string {
    const value = query.get(name);
    if (value === null || value.trim() === "") throw new InvalidArgumentError(`${name} is required`);
    return value;
}

function number(query: URLSearchParams, name: string): number {
    return Number(required(query, name));
}

function timestamp(query: URLSearchParams, name: string): Date {
    const value = required(query, name);
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
        throw new InvalidArgumentError(`${name} must be an ISO 8601 timestamp with an explicit offset`);
    }
    return new Date(value);
}

export async function openapi(_request: Request, context: RouteContext): Promise<Response> {
    let client: OpenTideConstants | undefined;
    try {
        client = await context.getClient();
        return Response.json(document, { headers: metadata(client, []) });
    } catch (error) {
        return Response.json(encError(error), { status: 400, headers: metadata(client, []) });
    }
}

export async function stations(request: Request, context: RouteContext): Promise<Response> {
    return reply(context, (client, selected) => {
        const query = params(request);
        const search = query.has("q"), bbox = query.has("bbox");
        const near = query.has("lat") || query.has("lon") || query.has("radius_km");
        if (Number(search) + Number(bbox) + Number(near) > 1) {
            throw new InvalidArgumentError("choose search, near or bbox");
        }
        const limit = query.has("limit") ? number(query, "limit") : undefined;
        if (near) {
            const hits = client.near({
                lat: number(query, "lat"), lon: number(query, "lon"),
                radiusKm: number(query, "radius_km"), limit,
            });
            selected.push(...hits.map(hit => hit.station));
            return { station_ids: hits.map(hit => hit.station.station_id), distance_km: hits.map(hit => hit.distance_km) };
        }
        if (bbox) {
            const values = required(query, "bbox").split(",");
            if (values.length !== 4 || values.some(value => value.trim() === "")) {
                throw new InvalidArgumentError("bbox requires west,south,east,north");
            }
            const [west, south, east, north] = values.map(Number);
            const found = client.bbox({ west, south, east, north, limit });
            selected.push(...found);
            return { station_ids: found.map(station => station.station_id) };
        }
        if (!search && limit !== undefined && (!Number.isInteger(limit) || limit < 1)) {
            throw new InvalidArgumentError(`limit must be an integer of 1 or more, or null; got ${JSON.stringify(limit)}`);
        }
        const found = search ? client.search({ name: required(query, "q"), limit }) : client.stations().slice(0, limit);
        selected.push(...found);
        return { station_ids: found.map(station => station.station_id) };
    });
}

export async function station(_request: Request, context: RouteContext, id: string): Promise<Response> {
    return reply(context, (client, selected) => {
        const found = client.station(id);
        if (found !== null) selected.push(found);
        return { station: encStation(found) };
    });
}

type Operation = "extremes" | "timeline" | "currents" | "current-events";

async function predict(request: Request, context: RouteContext, operation: Operation, id?: string): Promise<Response> {
    return reply(context, async (client, used) => {
        const query = params(request);
        const selected = id === undefined ? client.nearest({
            lat: number(query, "lat"), lon: number(query, "lon"),
            ...(query.has("radius_km") ? { maxKm: number(query, "radius_km") } : {}),
        })?.station ?? null : client.station(id);
        if (selected === null) return { prediction: null };
        used.push(selected);
        const window = { start: timestamp(query, "from"), stop: timestamp(query, "to") };
        const units = query.get("units") ?? undefined;
        const stepS = query.has("step") ? number(query, "step") : undefined;
        if (operation === "timeline" && stepS === undefined) throw new InvalidArgumentError("timeline requires step in seconds");
        if ((operation === "extremes" || operation === "current-events") && stepS !== undefined) {
            throw new InvalidArgumentError("step applies to timeline and currents");
        }
        const current = operation === "currents" || operation === "current-events";
        if (current && query.has("datum")) throw new InvalidArgumentError("datum applies to height predictions");
        if (!current && query.has("bin")) throw new InvalidArgumentError("bin applies to current predictions");
        const heightOptions = {
            ...window, datum: query.get("datum") ?? undefined, units: units as HeightOptions["units"],
            setId: query.get("set_id") ?? undefined, offsetId: query.get("offset_id") ?? undefined,
        };
        const currentOptions = {
            ...window, units: units as CurrentOptions["units"], setId: query.get("set_id") ?? undefined,
            ...(query.has("bin") ? { binNumber: number(query, "bin") } : {}),
        };
        const result = operation === "timeline" ? await client.heights(selected, { ...heightOptions, stepS }) :
            operation === "extremes" ? await client.extremes(selected, heightOptions) :
                operation === "currents" ? await client.currents(selected, { ...currentOptions, stepS }) :
                    await client.currentEvents(selected, currentOptions);
        return { prediction: encPrediction(result, query.has("units")) };
    });
}

export async function stationExtremes(request: Request, context: RouteContext, id: string): Promise<Response> {
    return predict(request, context, "extremes", id);
}
export async function stationTimeline(request: Request, context: RouteContext, id: string): Promise<Response> {
    return predict(request, context, "timeline", id);
}
export async function positionExtremes(request: Request, context: RouteContext): Promise<Response> {
    return predict(request, context, "extremes");
}
export async function positionTimeline(request: Request, context: RouteContext): Promise<Response> {
    return predict(request, context, "timeline");
}
export async function stationCurrents(request: Request, context: RouteContext, id: string): Promise<Response> {
    return predict(request, context, "currents", id);
}
export async function stationCurrentEvents(request: Request, context: RouteContext, id: string): Promise<Response> {
    return predict(request, context, "current-events", id);
}

export async function handleRoute(request: Request, context: RouteContext): Promise<Response | null> {
    if (request.method !== "GET") return null;
    const path = new URL(request.url).pathname;
    if (path === "/openapi.json") return openapi(request, context);
    if (path === "/stations") return stations(request, context);
    if (path === "/extremes") return positionExtremes(request, context);
    if (path === "/timeline") return positionTimeline(request, context);
    const match = /^\/stations\/([^/]+)(?:\/(extremes|timeline|currents|current-events))?$/.exec(path);
    if (!match) return null;
    const id = decodeURIComponent(match[1]);
    switch (match[2]) {
        case "extremes": return stationExtremes(request, context, id);
        case "timeline": return stationTimeline(request, context, id);
        case "currents": return stationCurrents(request, context, id);
        case "current-events": return stationCurrentEvents(request, context, id);
        default: return station(request, context, id);
    }
}
