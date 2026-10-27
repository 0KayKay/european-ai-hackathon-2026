import { describe, expect, it } from "vitest";
import { arenaHref, parseArenaHash } from "./router";

describe("parseArenaHash", () => {
	it("ignores hashes outside the arena", () => {
		expect(parseArenaHash("")).toBeNull();
		expect(parseArenaHash("#/")).toBeNull();
		expect(parseArenaHash("#/playground")).toBeNull();
		expect(parseArenaHash("#/arenas")).toBeNull();
	});

	it("parses every arena route", () => {
		expect(parseArenaHash("#/arena")).toEqual({ name: "levels" });
		expect(parseArenaHash("#/arena/")).toEqual({ name: "levels" });
		expect(parseArenaHash("#/arena/levels/hidden-mailbox/hard")).toEqual({
			name: "level",
			level: "hidden-mailbox",
			difficulty: "hard",
		});
		expect(parseArenaHash("#/arena/runs/r1")).toEqual({
			name: "run",
			runId: "r1",
		});
		expect(parseArenaHash("#/arena/runs/r1/cases/c%2007")).toEqual({
			name: "case",
			runId: "r1",
			caseId: "c 07",
		});
	});

	it("reads the start-run flag of a level route", () => {
		expect(parseArenaHash("#/arena/levels/email-review/hard?start=1")).toEqual({
			name: "level",
			level: "email-review",
			difficulty: "hard",
			start: true,
		});
		expect(
			parseArenaHash("#/arena/levels/email-review/hard?start=0"),
		).not.toHaveProperty("start");
		expect(
			arenaHref({
				name: "level",
				level: "email-review",
				difficulty: "hard",
				start: true,
			}),
		).toBe("#/arena/levels/email-review/hard?start=1");
	});

	it("rejects unknown paths and difficulties", () => {
		expect(parseArenaHash("#/arena/levels/x/extreme")).toEqual({
			name: "not_found",
			path: "/arena/levels/x/extreme",
		});
		expect(parseArenaHash("#/arena/nope")?.name).toBe("not_found");
	});

	it("round-trips hrefs", () => {
		const routes = [
			{ name: "levels" },
			{ name: "level", level: "email-review", difficulty: "medium" },
			{ name: "run", runId: "a/b" },
			{ name: "case", runId: "r1", caseId: "G04" },
		] as const;
		for (const route of routes)
			expect(parseArenaHash(arenaHref(route))).toEqual(route);
	});
});
