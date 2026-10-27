import { vi } from "vitest";

/** A dynamic route: gets the request and returns a JSON body or a full `Response`. */
export type RouteHandler = (request: {
	url: string;
	method: string;
	body: unknown;
}) => unknown;

/** Route value: a fixed JSON body, or a handler for dynamic answers (polling, POST). */
export type RouteValue = unknown | RouteHandler;

/**
 * Mocks `fetch` with JSON responses by URL. Unknown URLs answer 404 like the backend.
 * Returns the spy so tests can assert the requested URLs.
 */
export function mockArenaFetch(routes: Record<string, RouteValue>) {
	return vi
		.spyOn(globalThis, "fetch")
		.mockImplementation(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = typeof input === "string" ? input : input.toString();
				if (!(url in routes))
					return new Response(
						JSON.stringify({ detail: `No route for ${url}` }),
						{ status: 404 },
					);
				const route = routes[url];
				const value =
					typeof route === "function"
						? (route as RouteHandler)({
								url,
								method: init?.method ?? "GET",
								body:
									typeof init?.body === "string"
										? JSON.parse(init.body)
										: undefined,
							})
						: route;
				if (value instanceof Response) return value;
				return new Response(JSON.stringify(value), { status: 200 });
			},
		);
}

export const requestedUrls = (spy: ReturnType<typeof mockArenaFetch>) =>
	spy.mock.calls.map(([input]) =>
		typeof input === "string" ? input : input.toString(),
	);

/** `[method, url, body]` of every request, e.g. to assert a POST. */
export const requests = (spy: ReturnType<typeof mockArenaFetch>) =>
	spy.mock.calls.map(([input, init]) => [
		init?.method ?? "GET",
		typeof input === "string" ? input : input.toString(),
		typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
	]);
