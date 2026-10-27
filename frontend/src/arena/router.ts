import { useSyncExternalStore } from "react";
import { DIFFICULTIES, type Difficulty } from "./types";

/**
 * Hash routes of the arena. The route lives in `location.hash`, so a reload keeps the view and no
 * server-side routing is needed.
 *
 *   #/arena                                → levels
 *   #/arena/levels/:level/:difficulty      → level
 *   #/arena/runs/:runId                    → run
 *   #/arena/runs/:runId/cases/:caseId      → case
 */
export type ArenaRoute =
	| { name: "levels" }
	| { name: "level"; level: string; difficulty: Difficulty }
	| { name: "run"; runId: string }
	| { name: "case"; runId: string; caseId: string }
	| { name: "not_found"; path: string };

const isDifficulty = (value: string): value is Difficulty =>
	(DIFFICULTIES as readonly string[]).includes(value);

const decode = (value: string) => {
	try {
		return decodeURIComponent(value);
	} catch {
		return value;
	}
};

/** Parses a location hash. Returns `null` when the hash is not an arena route. */
export function parseArenaHash(hash: string): ArenaRoute | null {
	const path = hash.replace(/^#/, "").split("?")[0];
	const parts = path.split("/").filter(Boolean);
	if (parts[0] !== "arena") return null;
	const [, section, ...rest] = parts.map(decode);

	if (section === undefined) return { name: "levels" };
	if (section === "levels" && rest.length === 0) return { name: "levels" };
	if (section === "levels" && rest.length === 2 && isDifficulty(rest[1]))
		return { name: "level", level: rest[0], difficulty: rest[1] };
	if (section === "runs" && rest.length === 1)
		return { name: "run", runId: rest[0] };
	if (section === "runs" && rest.length === 3 && rest[1] === "cases")
		return { name: "case", runId: rest[0], caseId: rest[2] };
	return { name: "not_found", path };
}

const enc = encodeURIComponent;

/** Builds the `#/arena/...` href for a route. */
export function arenaHref(route: Exclude<ArenaRoute, { name: "not_found" }>) {
	switch (route.name) {
		case "levels":
			return "#/arena";
		case "level":
			return `#/arena/levels/${enc(route.level)}/${route.difficulty}`;
		case "run":
			return `#/arena/runs/${enc(route.runId)}`;
		case "case":
			return `#/arena/runs/${enc(route.runId)}/cases/${enc(route.caseId)}`;
	}
}

/** Hash of the non-arena app (playground and challenges). */
export const PLAYGROUND_HREF = "#/";

export function navigate(href: string) {
	window.location.hash = href.replace(/^#/, "");
}

const subscribe = (onChange: () => void) => {
	window.addEventListener("hashchange", onChange);
	return () => window.removeEventListener("hashchange", onChange);
};
const getHash = () => window.location.hash;

/** Current `location.hash`, re-rendering on `hashchange`. */
export function useHash() {
	return useSyncExternalStore(subscribe, getHash, () => "");
}

/** Current arena route, or `null` outside the arena. */
export function useArenaRoute(): ArenaRoute | null {
	return parseArenaHash(useHash());
}
