import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { emailReviewHard, hiddenMailboxHard, levelsFixture } from "../fixtures";
import {
	emailReviewDatasets,
	emailReviewResults,
	emailReviewRunCompleted,
	emailReviewRunGenerating,
	hiddenMailboxDatasets,
	hiddenMailboxResults,
	hiddenMailboxRunCompleted,
	hiddenMailboxRunRunning,
} from "../fixtures/runs";
import {
	mockArenaFetch,
	type RouteValue,
	requestedUrls,
	requests,
} from "../testing/fetch";
import { mockReactFlow } from "../testing/reactflow";
import type { RunDetail } from "../types";
import { RUN_POLL_INTERVAL_MS } from "./useRunPolling";

vi.mock("../../lib/auth-client", () => ({
	authClient: {
		useSession: () => ({ data: { user: { name: "Ada" } }, isPending: false }),
		signOut: vi.fn(),
	},
}));
vi.mock("../../AGUIPlayground", () => ({ default: () => null }));

beforeAll(mockReactFlow);
afterEach(() => {
	cleanup();
	vi.useRealTimers();
	vi.restoreAllMocks();
	window.location.hash = "";
});

const API = "/api/v1/arena";
const baseRoutes: Record<string, RouteValue> = {
	[`${API}/levels`]: levelsFixture,
	[`${API}/levels/hidden-mailbox/hard`]: hiddenMailboxHard,
	[`${API}/levels/email-review/hard`]: emailReviewHard,
	[`${API}/levels/email-review/datasets`]: emailReviewDatasets,
	[`${API}/levels/hidden-mailbox/datasets`]: hiddenMailboxDatasets,
	[`${API}/runs/run-hm-1/results`]: hiddenMailboxResults,
	[`${API}/runs/run-er-1/results`]: emailReviewResults,
};

function renderAt(hash: string, routes: Record<string, RouteValue> = {}) {
	window.location.hash = hash;
	const fetch = mockArenaFetch({ ...baseRoutes, ...routes });
	render(<App />);
	return fetch;
}

/**
 * Answers GET /runs/{id} with one of the snapshots; the test moves on with `next()`, so extra
 * polls (real time also advances the fake timers) never change what is shown.
 */
function snapshots(...runs: RunDetail[]) {
	let i = 0;
	return {
		route: () => runs[i],
		next: () => {
			i = Math.min(i + 1, runs.length - 1);
		},
	};
}

const runGets = (fetch: ReturnType<typeof mockArenaFetch>, id: string) =>
	requestedUrls(fetch).filter((url) => url === `${API}/runs/${id}`).length;

const tick = (ms = RUN_POLL_INTERVAL_MS) =>
	act(() => vi.advanceTimersByTimeAsync(ms));

const stat = (section: string, label: string) => {
	const region = screen.getByRole("region", { name: section });
	const term = within(region).getByText(label);
	return term.nextElementSibling?.textContent;
};

describe("run in progress", () => {
	it("polls the run, updates counts and stops polling when it finishes", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });
		const later: RunDetail = {
			...hiddenMailboxRunRunning,
			elapsed: 165.2,
			progress: {
				red: { done: 7, total: 10 },
				green: { done: 0, total: 10 },
			},
			counts: { ...hiddenMailboxRunRunning.counts, succeeded: 2 },
		};
		const run = snapshots(
			hiddenMailboxRunRunning,
			later,
			hiddenMailboxRunCompleted,
		);
		const fetch = renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: run.route,
		});

		expect(await screen.findByRole("status")).toHaveTextContent(
			"Running · 02:44",
		);
		expect(stat("Red team progress", "Attack succeeded")).toBe("1");
		expect(stat("Red team progress", "Defended")).toBe("4");
		expect(screen.getByTestId("count-error")).toHaveTextContent("1");
		expect(
			screen.getByRole("progressbar", { name: "Red team cases done" }),
		).toHaveAttribute("aria-valuenow", "6");

		const before = runGets(fetch, "run-hm-1");
		run.next();
		await tick();
		expect(runGets(fetch, "run-hm-1")).toBeGreaterThan(before);
		await waitFor(() =>
			expect(stat("Red team progress", "Attack succeeded")).toBe("2"),
		);
		expect(
			screen.getByRole("progressbar", { name: "Red team cases done" }),
		).toHaveAttribute("aria-valuenow", "7");
		expect(screen.getByRole("status")).toHaveTextContent("02:45");

		run.next();
		await tick();
		expect(
			await screen.findByRole("region", { name: "Attack success rate" }),
		).toBeInTheDocument();
		expect(screen.getByRole("status")).toHaveTextContent(
			"Completed · 20 cases · 04:51",
		);
		const polls = runGets(fetch, "run-hm-1");
		await tick(RUN_POLL_INTERVAL_MS * 5);
		expect(runGets(fetch, "run-hm-1")).toBe(polls);
	});

	it("stops polling when the view is left", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });
		const fetch = renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: hiddenMailboxRunRunning,
		});
		await screen.findByRole("region", { name: "Live sample" });
		cleanup();
		const polls = runGets(fetch, "run-hm-1");
		await tick(RUN_POLL_INTERVAL_MS * 5);
		expect(runGets(fetch, "run-hm-1")).toBe(polls);
	});

	it("shows the live sample, generated cases and models used", async () => {
		renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: hiddenMailboxRunRunning,
		});
		const live = await screen.findByRole("region", { name: "Live sample" });
		expect(
			within(live).getByText("Case 07 · linear_jailbreaking · turn 3 of ≤ 15"),
		).toBeInTheDocument();
		// The current step is the last recorded event: a list_files_of_folder call.
		await waitFor(() =>
			expect(
				live.querySelector('[data-node-id="tool:list_files_of_folder"]'),
			).toHaveAttribute("data-highlight", "current"),
		);
		const latest = within(live).getByRole("list", { name: "Latest events" });
		expect(within(latest).getAllByRole("listitem").at(-1)).toHaveAttribute(
			"aria-current",
			"step",
		);

		const cases = screen.getByRole("list", { name: "Cases" });
		const row = (label: string) =>
			cases.querySelector(`[data-case="${label}"]`) as HTMLElement;
		expect(within(row("03")).getByText("Attack succeeded")).toBeInTheDocument();
		expect(within(row("03")).getByText("<attack 3>")).toBeInTheDocument();
		expect(within(row("03")).getByRole("link")).toHaveAttribute(
			"href",
			"#/arena/runs/run-hm-1/cases/hm-03",
		);
		expect(within(row("07")).getByText("Running · turn 3")).toBeInTheDocument();
		expect(within(row("08")).getByText("Queued")).toBeInTheDocument();
		expect(
			within(row("08")).getByText("Generated when the case starts"),
		).toBeInTheDocument();

		fireEvent.click(screen.getByText("Models used"));
		expect(screen.getByText("red_team.attacker")).toBeVisible();
		expect(screen.getAllByText(/temperature 0\.7/).length).toBeGreaterThan(0);
	});

	it("lists generated cases while the run is still generating", async () => {
		renderAt("#/arena/runs/run-er-1", {
			[`${API}/runs/run-er-1`]: emailReviewRunGenerating,
		});
		expect(await screen.findByRole("status")).toHaveTextContent("Generating");
		const cases = screen.getByRole("list", { name: "Cases" });
		expect(within(cases).getAllByRole("listitem")).toHaveLength(4);
		expect(
			within(cases).getByText(
				"<planted email subject 1> · <planted email body 1>",
			),
		).toBeInTheDocument();
		expect(screen.getByText(/Cases are being generated/)).toBeInTheDocument();
	});

	it("stops the run with the stop button", async () => {
		let stopped = false;
		const stoppedCases = hiddenMailboxRunRunning.cases.map((item) =>
			item.status === "running"
				? { ...item, status: "stopped" as const }
				: item,
		);
		const fetch = renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: () =>
				stopped
					? {
							...hiddenMailboxRunRunning,
							status: "stopped",
							live: null,
							cases: stoppedCases,
						}
					: hiddenMailboxRunRunning,
			[`${API}/runs/run-hm-1/results`]: {
				...hiddenMailboxResults,
				cases: stoppedCases,
			},
			[`${API}/runs/run-hm-1/stop`]: ({ method }: { method: string }) => {
				if (method !== "POST") return new Response(null, { status: 405 });
				stopped = true;
				return { ...hiddenMailboxRunRunning, status: "stopped" };
			},
		});
		fireEvent.click(await screen.findByRole("button", { name: "Stop run" }));
		expect(
			await screen.findByText(/This run stopped before all cases were run/),
		).toBeInTheDocument();
		expect(requests(fetch)).toContainEqual([
			"POST",
			`${API}/runs/run-hm-1/stop`,
			undefined,
		]);
		expect(screen.getByRole("status")).toHaveTextContent("Stopped");
		expect(screen.queryByRole("button", { name: "Stop run" })).toBeNull();
		// The interrupted case is marked as stopped, the others as not run.
		const table = screen.getByRole("region", { name: "Cases" });
		const row = (label: string) =>
			table.querySelector(`tr[data-case="${label}"]`) as HTMLElement;
		expect(within(row("07")).getByText("stopped")).toBeInTheDocument();
		expect(within(row("08")).getByText("not run")).toBeInTheDocument();
	});
});

describe("results", () => {
	it("shows the rates exactly as returned", async () => {
		renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: hiddenMailboxRunCompleted,
		});
		const asr = await screen.findByTestId("rate-attack-success");
		expect(asr).toHaveTextContent("22.2%");
		expect(asr).toHaveTextContent("2 / 9");
		const fpr = screen.getByTestId("rate-false-positive");
		expect(fpr).toHaveTextContent("10%");
		expect(fpr).toHaveTextContent("1 / 10");
		expect(screen.getByTestId("defended")).toHaveTextContent("7/ 9");
		expect(screen.getByTestId("errors")).toHaveTextContent(
			"red 1 · green 0 · excluded from both rates",
		);

		const matrix = screen.getByRole("region", { name: "Expected vs. outcome" });
		expect(
			within(matrix).getByText("false negative").previousSibling,
		).toHaveTextContent("2");
		expect(
			within(matrix).getByText("false positive").previousSibling,
		).toHaveTextContent("1");

		const byType = screen.getByRole("region", { name: "By attack type" });
		expect(
			within(byType)
				.getAllByRole("listitem")
				.map((li) => li.textContent),
		).toEqual(["prompt_injection1 / 5 · 1 error", "linear_jailbreaking1 / 5"]);

		const decided = screen.getByRole("region", {
			name: "Where it was decided",
		});
		expect(
			within(decided).getByText("Input judge · UNSAFE"),
		).toBeInTheDocument();
		expect(
			within(decided).getByText("4 attacks · 1 benign"),
		).toBeInTheDocument();
	});

	it("shows a rate without a value as a dash", async () => {
		renderAt("#/arena/runs/run-er-1", {
			[`${API}/runs/run-er-1`]: emailReviewRunCompleted,
			[`${API}/runs/run-er-1/results`]: {
				...emailReviewResults,
				rates: {
					...emailReviewResults.rates,
					false_positive: { value: null, numerator: 0, denominator: 0 },
				},
			},
		});
		const fpr = await screen.findByTestId("rate-false-positive");
		expect(fpr).toHaveTextContent("—0 / 0");
		expect(screen.getByTestId("rate-attack-success")).toHaveTextContent(
			"11.1%",
		);
	});

	it("filters the case list and opens the replay", async () => {
		renderAt("#/arena/runs/run-hm-1", {
			[`${API}/runs/run-hm-1`]: hiddenMailboxRunCompleted,
		});
		const table = await screen.findByRole("region", { name: "Cases" });
		const labels = () =>
			[...table.querySelectorAll("tbody tr[data-case]")].map((r) =>
				r.getAttribute("data-case"),
			);
		expect(labels()).toHaveLength(20);

		fireEvent.click(
			within(table).getByRole("button", { name: "False negatives 2" }),
		);
		expect(labels()).toEqual(["03", "07"]);
		fireEvent.click(
			within(table).getByRole("button", { name: "False positives 1" }),
		);
		expect(labels()).toEqual(["G04"]);
		fireEvent.click(within(table).getByRole("button", { name: "Errors 1" }));
		expect(labels()).toEqual(["05"]);
		const row = table.querySelector('tr[data-case="05"]') as HTMLElement;
		expect(
			within(row).getByText("The run exceeded the time limit (300 s)."),
		).toBeInTheDocument();
		expect(within(row).getByText("30.2 s")).toBeInTheDocument();
		fireEvent.click(within(table).getByRole("button", { name: "All 20" }));
		expect(labels()).toHaveLength(20);

		fireEvent.click(
			within(
				table.querySelector('tr[data-case="06"]') as HTMLElement,
			).getByText("Turn limit reached · 15 turns"),
		);
		expect(window.location.hash).toBe("#/arena/runs/run-hm-1/cases/hm-06");
	});

	it("links export and run again", async () => {
		renderAt("#/arena/runs/run-er-1", {
			[`${API}/runs/run-er-1`]: emailReviewRunCompleted,
		});
		const exportLink = await screen.findByRole("link", { name: /Export JSON/ });
		expect(exportLink).toHaveAttribute("href", `${API}/runs/run-er-1/export`);
		expect(exportLink).toHaveAttribute("download", "run-er-1.json");
		expect(screen.getByRole("link", { name: /Run again/ })).toHaveAttribute(
			"href",
			"#/arena/levels/email-review/hard?start=1",
		);
		expect(
			screen.getByRole("link", { name: "Level 2 · Email Review · Hard" }),
		).toHaveAttribute("href", "#/arena/levels/email-review/hard");
	});

	it("shows failed case generation and the reason of an error case", async () => {
		renderAt("#/arena/runs/run-er-1", {
			[`${API}/runs/run-er-1`]: {
				...emailReviewRunCompleted,
				generation: {
					red: {
						status: "failed",
						error:
							"DeepTeam service unreachable at http://deepteam:8080 (ConnectError).",
					},
					green: { status: "done", error: null },
				},
			},
		});
		const alert = await screen.findByRole("alert", { name: "Run problems" });
		expect(alert).toHaveTextContent(
			"Red-team generation failed: DeepTeam service unreachable at http://deepteam:8080 (ConnectError).",
		);
		expect(alert).not.toHaveTextContent("Green-team");
	});

	it("shows an error with retry for an unknown run", async () => {
		renderAt("#/arena/runs/missing");
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("This run could not be loaded.");
		expect(
			within(alert).getByRole("button", { name: "Try again" }),
		).toBeVisible();
	});
});
