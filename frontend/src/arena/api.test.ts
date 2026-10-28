import { afterEach, describe, expect, it, vi } from "vitest";
import {
	ArenaApiError,
	createRun,
	getLevel,
	getRun,
	getRunCase,
	listDatasets,
	listLevels,
	listRuns,
	runExportUrl,
	stopRun,
} from "./api";
import { levelsFixture } from "./fixtures";

const respond = (body: unknown, status = 200) =>
	vi.spyOn(globalThis, "fetch").mockImplementation(
		async () =>
			new Response(typeof body === "string" ? body : JSON.stringify(body), {
				status,
			}),
	);

afterEach(() => vi.restoreAllMocks());

describe("arena api", () => {
	it("fetches levels from the same-origin arena prefix", async () => {
		const fetch = respond(levelsFixture);
		await expect(listLevels()).resolves.toEqual(levelsFixture);
		expect(fetch).toHaveBeenCalledWith(
			"/api/v1/arena/levels",
			expect.objectContaining({ credentials: "same-origin" }),
		);
	});

	it("encodes path segments", async () => {
		const fetch = respond({});
		await getLevel("hidden mailbox", "hard");
		await getRunCase("run/1", "case 7");
		await listDatasets("email-review");
		await listRuns("email-review");
		await getRun("r1");
		const urls = fetch.mock.calls.map(([url]) => url);
		expect(urls).toEqual([
			"/api/v1/arena/levels/hidden%20mailbox/hard",
			"/api/v1/arena/runs/run%2F1/cases/case%207",
			"/api/v1/arena/levels/email-review/datasets",
			"/api/v1/arena/runs?level=email-review",
			"/api/v1/arena/runs/r1",
		]);
		expect(runExportUrl("r1")).toBe("/api/v1/arena/runs/r1/export");
	});

	it("posts JSON for runs", async () => {
		const fetch = respond({ id: "r1" }, 201);
		const body = {
			level: "email-review",
			difficulty: "hard" as const,
			red: { source: "dataset" as const, dataset_id: "d1" },
			green: { source: "generate" as const },
		};
		await createRun(body);
		await stopRun("r1");
		const [, init] = fetch.mock.calls[0];
		expect(init?.method).toBe("POST");
		expect(JSON.parse(String(init?.body))).toEqual(body);
		expect(new Headers(init?.headers).get("Content-Type")).toBe(
			"application/json",
		);
		expect(fetch.mock.calls[1][0]).toBe("/api/v1/arena/runs/r1/stop");
		expect(fetch.mock.calls[1][1]?.method).toBe("POST");
	});

	it("turns FastAPI error details into an ArenaApiError", async () => {
		respond({ detail: "Unknown level 'nope'" }, 404);
		const error = await getLevel("nope", "easy").catch((e: unknown) => e);
		expect(error).toBeInstanceOf(ArenaApiError);
		expect(error).toMatchObject({
			status: 404,
			message: "Unknown level 'nope'",
		});
	});

	it("uses the first validation message of a 422", async () => {
		respond(
			{ detail: [{ loc: ["body", "level"], msg: "Field required" }] },
			422,
		);
		await expect(listLevels()).rejects.toMatchObject({
			status: 422,
			message: "Field required",
		});
	});

	it("explains an expired session", async () => {
		respond("", 401);
		await expect(listLevels()).rejects.toMatchObject({
			status: 401,
			message: "Your session has expired. Please sign in again.",
		});
	});

	it("reports network failures with status 0", async () => {
		vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
		await expect(listLevels()).rejects.toMatchObject({ status: 0 });
	});
});
