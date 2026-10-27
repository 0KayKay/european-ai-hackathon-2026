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
import {
	emailReviewCase01,
	emailReviewCase03,
	emailReviewRunCompleted,
	hiddenMailboxCase01,
	hiddenMailboxCase03,
	hiddenMailboxCaseG04,
	hiddenMailboxRunCompleted,
} from "../fixtures/runs";
import { mockArenaFetch } from "../testing/fetch";
import { mockReactFlow } from "../testing/reactflow";

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
const hm = `${API}/runs/run-hm-1`;
const routes = {
	[hm]: hiddenMailboxRunCompleted,
	[`${hm}/cases/hm-01`]: hiddenMailboxCase01,
	[`${hm}/cases/hm-02`]: {
		...hiddenMailboxCase01,
		id: "hm-02",
		label: "02",
		index: 1,
	},
	[`${hm}/cases/hm-03`]: hiddenMailboxCase03,
	[`${hm}/cases/hm-g04`]: hiddenMailboxCaseG04,
	[`${API}/runs/run-er-1`]: emailReviewRunCompleted,
	[`${API}/runs/run-er-1/cases/er-03`]: emailReviewCase03,
	[`${API}/runs/run-er-1/cases/er-08`]: {
		...emailReviewCase01,
		id: "er-08",
		label: "08",
		index: 8,
		outcome: "error",
		decided_by: { kind: "error" },
		decided_label: "Error",
		error: "Invalid generated case: 'subject' is longer than 300 characters",
		content: { subject: "<subject 8>", body: "<body 8>" },
		events: [],
	},
};

function renderAt(hash: string) {
	window.location.hash = hash;
	const fetch = mockArenaFetch(routes);
	render(<App />);
	return fetch;
}

const log = () => screen.getByRole("list", { name: "Event log" });
const currentEntry = () =>
	log().querySelector('[aria-current="step"]') as HTMLElement | null;
const graphNode = (id: string) =>
	screen.getByTestId("arena-graph").querySelector(`[data-node-id="${id}"]`);
const time = () => screen.getByTestId("replay-time").textContent;

describe("case replay", () => {
	it("shows the case header and starts at the first event", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		await screen.findByRole("list", { name: "Event log" });
		const banner = screen.getByRole("banner");
		expect(within(banner).getByText("Case 03")).toBeInTheDocument();
		expect(within(banner).getByText("Attack succeeded")).toBeInTheDocument();
		expect(
			within(banner).getByText("Red team · prompt_injection · 1 turn · 3.4 s"),
		).toBeInTheDocument();
		expect(
			within(banner).getByRole("link", { name: "Results" }),
		).toHaveAttribute("href", "#/arena/runs/run-hm-1");

		expect(currentEntry()).toHaveAttribute("data-seq", "0");
		expect(currentEntry()).toHaveTextContent("Attacker · message");
		expect(currentEntry()).toHaveTextContent("<attack 3>");
		await waitFor(() =>
			expect(graphNode("attacker")).toHaveAttribute(
				"data-highlight",
				"current",
			),
		);
		expect(time()).toBe("0.00s / 3.22s");
	});

	it("steps with the buttons and the keyboard", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		await screen.findByRole("list", { name: "Event log" });
		await waitFor(() => expect(graphNode("attacker")).not.toBeNull());

		fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
		expect(currentEntry()).toHaveAttribute("data-seq", "1");
		expect(graphNode("guardrail:input-sanitizer")).toHaveAttribute(
			"data-highlight",
			"current",
		);
		expect(graphNode("attacker")).toHaveAttribute("data-highlight", "visited");
		expect(graphNode("guardrail:input-sanitizer")).toHaveTextContent(
			"no changes",
		);

		fireEvent.keyDown(window, { key: "ArrowRight" });
		expect(currentEntry()).toHaveTextContent("Input judge · passed");
		expect(graphNode("guardrail:input-judge")).toHaveAttribute(
			"data-highlight",
			"current",
		);
		expect(graphNode("guardrail:input-judge")).toHaveTextContent(
			"SAFE · passed",
		);
		expect(time()).toBe("0.88s / 3.22s");

		fireEvent.keyDown(window, { key: "ArrowLeft" });
		fireEvent.click(screen.getByRole("button", { name: "Step back" }));
		expect(currentEntry()).toHaveAttribute("data-seq", "0");
		expect(screen.getByRole("button", { name: "Step back" })).toBeDisabled();
	});

	it("jumps to a clicked event and shows the recorded details", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		await screen.findByRole("list", { name: "Event log" });

		fireEvent.click(
			within(log()).getByRole("button", { name: "3.22s Success check · met" }),
		);
		expect(currentEntry()).toHaveTextContent("secret_in_output");
		expect(currentEntry()).toHaveTextContent("agent output");
		await waitFor(() =>
			expect(graphNode("success")).toHaveAttribute("data-highlight", "alert"),
		);
		expect(graphNode("tool:get_file_content")).toHaveTextContent("2.17s");
		expect(time()).toBe("3.22s / 3.22s");

		// Tool calls with arguments; long results collapse.
		expect(
			within(log()).getByText(
				'list_files_of_folder({"folder":"/Finance/Legacy/"})',
			),
		).toBeInTheDocument();
		const toggle = within(log()).getByRole("button", {
			name: /Show full result/,
		});
		expect(toggle).toHaveAttribute("aria-expanded", "false");
		fireEvent.click(toggle);
		expect(toggle).toHaveAttribute("aria-expanded", "true");
		expect(currentEntry()).toHaveTextContent("Success check · met");
	});

	it("shows a blocking guardrail with rule and reason as recorded", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-01");
		await screen.findByRole("list", { name: "Event log" });
		fireEvent.keyDown(window, { key: "ArrowRight" });
		fireEvent.keyDown(window, { key: "ArrowRight" });
		const entry = currentEntry() as HTMLElement;
		expect(entry).toHaveTextContent("Input judge · blocked");
		expect(
			within(entry).getByText("<rule as returned by the judge>"),
		).toBeVisible();
		expect(
			within(entry).getByText(
				"<judge reason as returned: request targets restricted data>",
			),
		).toBeVisible();
		await waitFor(() =>
			expect(graphNode("guardrail:input-judge")).toHaveAttribute(
				"data-highlight",
				"blocked",
			),
		);
		expect(graphNode("guardrail:input-judge")).toHaveTextContent(
			"UNSAFE · blocked",
		);
		expect(graphNode("agent")).not.toHaveAttribute("data-highlight");
	});

	it("plays with the recorded timing and speed", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		await screen.findByRole("list", { name: "Event log" });
		// Loaded with real timers; from here on only the replay timers run, under test control.
		vi.useFakeTimers();

		fireEvent.click(screen.getByRole("button", { name: "2×" }));
		expect(screen.getByRole("button", { name: "2×" })).toHaveAttribute(
			"aria-pressed",
			"true",
		);
		fireEvent.keyDown(window, { key: " " });
		expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
		// 0.00 → 0.02 s takes 10 ms at 2×, 0.02 → 0.88 s takes 430 ms.
		await act(() => vi.advanceTimersByTimeAsync(15));
		expect(currentEntry()).toHaveAttribute("data-seq", "1");
		await act(() => vi.advanceTimersByTimeAsync(400));
		expect(currentEntry()).toHaveAttribute("data-seq", "1");
		await act(() => vi.advanceTimersByTimeAsync(40));
		expect(currentEntry()).toHaveAttribute("data-seq", "2");

		fireEvent.keyDown(window, { key: " " });
		expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
		await act(() => vi.advanceTimersByTimeAsync(5000));
		expect(currentEntry()).toHaveAttribute("data-seq", "2");

		// Playing to the end stops there.
		fireEvent.click(screen.getByRole("button", { name: "4×" }));
		fireEvent.click(screen.getByRole("button", { name: "Play" }));
		// Each act() commits one step, which schedules the next one.
		for (let i = 0; i < 20; i++)
			await act(() => vi.advanceTimersByTimeAsync(500));
		expect(currentEntry()).toHaveAttribute("data-seq", "15");
		expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
	});

	it("goes to the previous and next case in run order", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-03");
		await screen.findByRole("list", { name: "Event log" });
		const prev = await screen.findByRole("link", { name: "Previous case: 02" });
		expect(prev).toHaveAttribute("href", "#/arena/runs/run-hm-1/cases/hm-02");
		expect(screen.getByRole("link", { name: "Next case: 04" })).toHaveAttribute(
			"href",
			"#/arena/runs/run-hm-1/cases/hm-04",
		);
		fireEvent.keyDown(window, { key: "ArrowRight" });

		await act(async () => {
			window.location.hash = "#/arena/runs/run-hm-1/cases/hm-02";
			window.dispatchEvent(new HashChangeEvent("hashchange"));
		});
		await waitFor(() =>
			expect(
				within(screen.getByRole("banner")).getByText("Case 02"),
			).toBeVisible(),
		);
		// A new case starts at its first event.
		await waitFor(() =>
			expect(currentEntry()).toHaveAttribute("data-seq", "0"),
		);
		expect(
			await screen.findByRole("link", { name: "Previous case: 01" }),
		).toBeInTheDocument();
	});

	it("disables previous on the first case and labels green messages", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/hm-g04");
		await screen.findByRole("list", { name: "Event log" });
		expect(currentEntry()).toHaveTextContent("User · message (benign)");
		expect(
			within(screen.getByRole("banner")).getByText("False positive"),
		).toBeInTheDocument();
		await screen.findByRole("link", { name: "Previous case: G03" });

		cleanup();
		renderAt("#/arena/runs/run-hm-1/cases/hm-01");
		await screen.findByRole("link", { name: "Next case: 02" });
		expect(screen.queryByRole("link", { name: /Previous case/ })).toBeNull();
	});

	it("replays an indirect case with planted content and the user task", async () => {
		renderAt("#/arena/runs/run-er-1/cases/er-03");
		await screen.findByRole("list", { name: "Event log" });
		const first = currentEntry() as HTMLElement;
		expect(first).toHaveTextContent("Attacker · planted content");
		expect(within(first).getByText("subject")).toBeInTheDocument();
		expect(
			within(first).getByText("<planted email subject 3>"),
		).toBeInTheDocument();
		expect(
			within(first).getByText("<planted email body 3>"),
		).toBeInTheDocument();
		expect(within(log()).getByText("User task")).toBeInTheDocument();
		expect(
			within(log()).getByText("Please process my email inbox."),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(graphNode("injection")).toHaveAttribute(
				"data-highlight",
				"current",
			),
		);

		fireEvent.click(
			within(log()).getByRole("button", {
				name: "2.31s Send email judge · passed",
			}),
		);
		expect(graphNode("guardrail:send-email-judge")).toHaveTextContent(
			"SAFE · passed",
		);
		expect(graphNode("tool:get_emails")).toHaveTextContent("0.92s");
		expect(within(log()).getByText(/send_email\(/)).toHaveTextContent(
			'"to": "external.sender@example.com"',
		);
	});

	it("shows why a case without events is an error, with its input", async () => {
		renderAt("#/arena/runs/run-er-1/cases/er-08");
		expect(await screen.findByRole("note")).toHaveTextContent(
			"Invalid generated case: 'subject' is longer than 300 characters",
		);
		expect(
			screen.getByText("No events were recorded for this case."),
		).toBeInTheDocument();
		expect(screen.getByText("<subject 8>")).toBeInTheDocument();
		expect(screen.getByText("<body 8>")).toBeInTheDocument();
	});

	it("shows an error for an unknown case", async () => {
		renderAt("#/arena/runs/run-hm-1/cases/nope");
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"This case could not be loaded.",
		);
	});
});
