import { OpenTideConstants } from "opentideconstants";

let client: OpenTideConstants | undefined;
let opening: Promise<OpenTideConstants> | undefined;

function describeError(error: unknown): unknown {
    if (!(error instanceof Error)) return String(error);
    return {
        name: error.name,
        message: error.message,
        stack: error.stack,
        code: "code" in error ? error.code : undefined,
        cause: error.cause === undefined ? undefined : describeError(error.cause),
    };
}

export async function openClient(baseUrl: string): Promise<OpenTideConstants> {
    if (client) return client;
    return opening ??= OpenTideConstants.open({
        release: "20261011",
        baseUrl,
        mode: "stream",
        autoUpdate: false,
    }).then(opened => {
        client = opened;
        return opened;
    }).catch(error => {
        opening = undefined;
        throw error;
    });
}

export async function probeSdk(baseUrl: string, served: Record<string, number>): Promise<Response> {
    const started = performance.now();
    const cold = client === undefined;
    let operation = "OpenTideConstants.open";
    try {
        client = await openClient(baseUrl);
        operation = "requireStation";
        const station = client.requireStation("OTC-ee4593cd");
        if (client.stationByAlias("noaa", "9414290")?.station_id !== station.station_id) {
            throw new Error("Pinned NOAA alias does not resolve to pinned station");
        }
        const options = {
            setId: "OTC-ee4593cd/noaa/9414290",
            datum: "mean_level",
            units: "meters" as const,
        };
        operation = "heightsAt";
        const heights = await client.heightsAt(station, [
            new Date(1791673200000), new Date(1791694800000),
        ], options);
        const openAndFirstPredictionMs = performance.now() - started;
        operation = "extremes";
        const extremes = await client.extremes(station, {
            ...options,
            start: new Date(1791673200000),
            stop: new Date(1791759600000),
        });
        return Response.json({
            ok: true,
            cold,
            open_and_first_prediction_ms: openAndFirstPredictionMs,
            total_ms: performance.now() - started,
            station_id: station.station_id,
            set_id: options.setId,
            heights: heights.items.map(item => ({
                time_ms: item.time.getTime(), height_m: item.height_m,
            })),
            extremes: extremes.items.map(item => ({
                time_ms: item.time.getTime(), height_m: item.height_m, type: item.type,
            })),
            served,
        });
    } catch (error) {
        return Response.json({
            ok: false,
            operation,
            cold,
            elapsed_ms: performance.now() - started,
            error: describeError(error),
            served,
        }, { status: 500 });
    }
}
