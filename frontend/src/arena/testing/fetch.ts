import { vi } from "vitest";

/**
 * Mocks `fetch` with fixed JSON responses by URL. Unknown URLs answer 404 like the backend.
 * Returns the spy so tests can assert the requested URLs.
 */
export function mockArenaFetch(routes: Record<string, unknown>) {
	return vi
		.spyOn(globalThis, "fetch")
		.mockImplementation(async (input: RequestInfo | URL) => {
			const url = typeof input === "string" ? input : input.toString();
			if (url in routes)
				return new Response(JSON.stringify(routes[url]), { status: 200 });
			return new Response(JSON.stringify({ detail: `No route for ${url}` }), {
				status: 404,
			});
		});
}

export const requestedUrls = (spy: ReturnType<typeof mockArenaFetch>) =>
	spy.mock.calls.map(([input]) =>
		typeof input === "string" ? input : input.toString(),
	);
