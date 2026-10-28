import {
	act,
	cleanup,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { hiddenMailboxHard, levelsFixture } from "../fixtures";
import { runListFixture } from "../fixtures/runs";
import { formatDateTime } from "../format";
import { mockArenaFetch, requestedUrls } from "../testing/fetch";
import { mockReactFlow } from "../testing/reactflow";
import type { RunSummary } from "../types";
import { RUN_LIST_POLL_INTERVAL_MS } from "./RunList";

vi.mock("../../lib/auth-client", () => ({
	authClient: {
		useSession: () => ({ data: { user: { name: "Ada" } }, isPending: false }),
		signOut: vi.fn(),
	},
}));

beforeAll(mockReactFlow);
afterEach(() => {
	cleanup();
	vi.useRealTimers();
	vi.restoreAllMocks();
	window.location.hash = "";
});

const API = "/api/v1/arena";

const runsList = () => screen.getByRole("list", { name: "Runs" });
const rowOf = (list: HTMLElement, id: string) =>
	list.querySelector(`[data-run="${id}"]`) as HTMLElement;
const runGets = (fetch: ReturnType<typeof mockArenaFetch>, url: string) =>
	requestedUrls(fetch).filter((u) => u === url).length;
const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

describe("runs overview", () => {
	it("lists active runs first, then finished runs newest first", async () => {
		window.location.hash = "#/arena";
		mockArenaFetch({
			[`${API}/levels`]: levelsFixture,
			[`${API}/runs`]: runListFixture,
		});
		render(<App />);
		await screen.findByRole("heading", { name: "Runs" });
		const list = await screen.findByRole("list", { name: "Runs" });
		const ids = [...list.querySelectorAll("[data-run]")].map((li) =>
			li.getAttribute("data-run"),
		);
		expect(ids).toEqual([
			"run-er-2",
			"run-hm-2",
			"run-er-1",
			"run-hm-1",
			"run-hm-0",
		]);

		const running = rowOf(list, "run-hm-2");
		expect(running).toHaveTextContent("Level 1 · Hidden Mailbox");
		expect(running).toHaveTextContent("Hard");
		expect(within(running).getByText("Running")).toBeInTheDocument();
		expect(within(running).getByTestId("progress-red")).toHaveTextContent(
			"6/10",
		);
		expect(within(running).getByTestId("progress-green")).toHaveTextContent(
			"0/10",
		);
		expect(
			within(rowOf(list, "run-er-2")).getByText("Generating"),
		).toBeVisible();

		const finished = rowOf(list, "run-hm-1");
		expect(within(finished).getByText("Completed")).toBeInTheDocument();
		expect(
			within(finished).getByText(formatDateTime("2026-10-27T18:02:00.000Z")),
		).toHaveAttribute("datetime", "2026-10-27T18:02:00.000Z");
		expect(finished).toHaveTextContent("04:51");
		expect(finished).toHaveTextContent("ASR22.2%2/9");
		expect(finished).toHaveTextContent("FPR10%1/10");
		expect(within(rowOf(list, "run-hm-0")).getByText("Failed")).toBeVisible();
		// No run-level title recorded: the level list names it.
		expect(rowOf(list, "run-er-1")).toHaveTextContent("Level 2 · Email Review");
	});

	it("opens a running run's live view and a finished run's results", async () => {
		window.location.hash = "#/arena";
		mockArenaFetch({
			[`${API}/levels`]: levelsFixture,
			[`${API}/runs`]: runListFixture,
		});
		render(<App />);
		const list = await screen.findByRole("list", { name: "Runs" });
		expect(within(rowOf(list, "run-hm-2")).getByRole("link")).toHaveAttribute(
			"href",
			"#/arena/runs/run-hm-2",
		);
		expect(within(rowOf(list, "run-hm-1")).getByRole("link")).toHaveAttribute(
			"href",
			"#/arena/runs/run-hm-1",
		);
	});

	it("polls while a run is active and stops when nothing runs", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });
		const finished = runListFixture.map(
			(run): RunSummary =>
				run.status === "running" || run.status === "generating"
					? { ...run, status: "completed" }
					: run,
		);
		let answer = runListFixture;
		window.location.hash = "#/arena";
		const fetch = mockArenaFetch({
			[`${API}/levels`]: levelsFixture,
			[`${API}/runs`]: () => answer,
		});
		render(<App />);
		await screen.findByRole("list", { name: "Runs" });
		const before = runGets(fetch, `${API}/runs`);
		await tick(RUN_LIST_POLL_INTERVAL_MS);
		expect(runGets(fetch, `${API}/runs`)).toBeGreaterThan(before);

		answer = finished;
		await tick(RUN_LIST_POLL_INTERVAL_MS);
		await waitFor(() =>
			expect(
				runsList().querySelectorAll('li[data-status="completed"]'),
			).toHaveLength(4),
		);
		const polls = runGets(fetch, `${API}/runs`);
		await tick(RUN_LIST_POLL_INTERVAL_MS * 5);
		expect(runGets(fetch, `${API}/runs`)).toBe(polls);
	});

	it("does not poll when no run is active", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });
		window.location.hash = "#/arena";
		const fetch = mockArenaFetch({
			[`${API}/levels`]: levelsFixture,
			[`${API}/runs`]: [runListFixture[0]],
		});
		render(<App />);
		await screen.findByRole("list", { name: "Runs" });
		const polls = runGets(fetch, `${API}/runs`);
		await tick(RUN_LIST_POLL_INTERVAL_MS * 3);
		expect(runGets(fetch, `${API}/runs`)).toBe(polls);
	});

	it("says when there are no runs yet", async () => {
		window.location.hash = "#/arena";
		mockArenaFetch({
			[`${API}/levels`]: levelsFixture,
			[`${API}/runs`]: [],
		});
		render(<App />);
		expect(
			await screen.findByText(/No runs yet\. Open a level/),
		).toBeInTheDocument();
	});
});

describe("recent runs on the level view", () => {
	it("lists the latest runs of the level", async () => {
		const many: RunSummary[] = Array.from({ length: 7 }, (_, i) => ({
			...runListFixture[0],
			id: `run-old-${i}`,
			started_at: `2026-10-2${i}T10:00:00.000Z`,
		}));
		window.location.hash = "#/arena/levels/hidden-mailbox/hard";
		const fetch = mockArenaFetch({
			[`${API}/levels/hidden-mailbox/hard`]: hiddenMailboxHard,
			[`${API}/runs?level=hidden-mailbox`]: [...many, runListFixture[1]],
		});
		render(<App />);
		const list = await screen.findByRole("list", { name: "Recent runs" });
		const ids = [...list.querySelectorAll("[data-run]")].map((li) =>
			li.getAttribute("data-run"),
		);
		expect(ids).toEqual([
			"run-hm-2",
			"run-old-6",
			"run-old-5",
			"run-old-4",
			"run-old-3",
		]);
		expect(requestedUrls(fetch)).toContain(`${API}/runs?level=hidden-mailbox`);
		expect(within(rowOf(list, "run-old-6")).getByRole("link")).toHaveAttribute(
			"href",
			"#/arena/runs/run-old-6",
		);
		expect(rowOf(list, "run-old-6")).toHaveTextContent("ASR22.2%2/9");
		expect(screen.getByRole("link", { name: "All runs" })).toHaveAttribute(
			"href",
			"#/arena",
		);
	});
});

describe("formatDateTime", () => {
	it("shows local date and time", () => {
		const local = new Date(2026, 9, 28, 21, 14, 5).toISOString();
		expect(formatDateTime(local)).toBe("28 Oct, 21:14");
		expect(formatDateTime(local, { seconds: true })).toBe("28 Oct, 21:14:05");
		expect(formatDateTime(null)).toBe("—");
	});
});
