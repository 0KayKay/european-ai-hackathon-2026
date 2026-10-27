import {
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
	hiddenMailboxDatasets,
	newRunSummary,
} from "../fixtures/runs";
import { mockArenaFetch, type RouteValue, requests } from "../testing/fetch";
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
};

function renderAt(hash: string, routes: Record<string, RouteValue> = {}) {
	window.location.hash = hash;
	const fetch = mockArenaFetch({ ...baseRoutes, ...routes });
	render(<App />);
	return fetch;
}

describe("start run", () => {
	it("starts a run on an indirect level with a stored red dataset", async () => {
		const fetch = renderAt("#/arena/levels/email-review/hard", {
			[`${API}/runs`]: newRunSummary,
		});
		await screen.findByText("Indirect · single-turn");
		fireEvent.click(screen.getByRole("button", { name: /Run evaluation/ }));

		const dialog = await screen.findByRole("dialog", {
			name: "Run evaluation",
		});
		expect(within(dialog).getByText(/prompt_injection × 5/)).toBeVisible();
		expect(within(dialog).getByText(/roleplay × 5/)).toBeVisible();
		const red = within(dialog).getByRole("region", {
			name: "Red team · attacks",
		});
		const green = within(dialog).getByRole("region", {
			name: "Green team · benign cases",
		});
		expect(within(red).getByText("10 cases")).toBeInTheDocument();
		expect(within(green).getByText("10 cases")).toBeInTheDocument();

		const stored = await within(red).findByRole("radio", {
			name: /Use stored dataset/,
		});
		await waitFor(() => expect(stored).toBeEnabled());
		fireEvent.click(stored);
		expect(within(red).getByRole("combobox")).toHaveValue("ds-er-red-1");
		expect(within(red).getByText("10 stored cases")).toBeInTheDocument();
		// The green team keeps generating fresh cases.
		expect(
			within(green).getByRole("radio", { name: /Generate new/ }),
		).toBeChecked();

		fireEvent.click(within(dialog).getByRole("button", { name: "Start run" }));
		await waitFor(() =>
			expect(window.location.hash).toBe("#/arena/runs/run-new"),
		);
		expect(requests(fetch)).toContainEqual([
			"POST",
			`${API}/runs`,
			{
				level: "email-review",
				difficulty: "hard",
				red: { source: "dataset", dataset_id: "ds-er-red-1" },
				green: { source: "generate" },
			},
		]);
	});

	it("generates red cases of a direct level during each run", async () => {
		const fetch = renderAt("#/arena/levels/hidden-mailbox/hard", {
			[`${API}/runs`]: { ...newRunSummary, id: "run-hm-2" },
		});
		await screen.findByText("Direct · multi-turn");
		fireEvent.click(screen.getByRole("button", { name: /Run evaluation/ }));
		const dialog = await screen.findByRole("dialog");
		const red = within(dialog).getByRole("region", {
			name: "Red team · attacks",
		});
		expect(
			within(red).getByText(/Red cases are generated during each run/),
		).toBeInTheDocument();
		expect(within(red).queryByRole("radio")).toBeNull();
		expect(within(red).getByText("15 cases · ≤ 15 turns")).toBeInTheDocument();
		expect(
			within(red).getByText(/linear_jailbreaking × 5/),
		).toBeInTheDocument();

		// Green cases can still be reused.
		const green = within(dialog).getByRole("region", {
			name: "Green team · benign cases",
		});
		const stored = within(green).getByRole("radio", {
			name: /Use stored dataset/,
		});
		await waitFor(() => expect(stored).toBeEnabled());
		fireEvent.click(stored);
		fireEvent.click(within(dialog).getByRole("button", { name: "Start run" }));
		await waitFor(() =>
			expect(window.location.hash).toBe("#/arena/runs/run-hm-2"),
		);
		expect(requests(fetch)).toContainEqual([
			"POST",
			`${API}/runs`,
			{
				level: "hidden-mailbox",
				difficulty: "hard",
				red: { source: "generate" },
				green: { source: "dataset", dataset_id: "ds-hm-green-1" },
			},
		]);
	});

	it("shows why a run could not be started", async () => {
		renderAt("#/arena/levels/email-review/hard", {
			[`${API}/runs`]: () =>
				new Response(JSON.stringify({ detail: "A run is already active." }), {
					status: 409,
				}),
		});
		await screen.findByText("Indirect · single-turn");
		fireEvent.click(screen.getByRole("button", { name: /Run evaluation/ }));
		const dialog = await screen.findByRole("dialog");
		fireEvent.click(within(dialog).getByRole("button", { name: "Start run" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent(
			"A run is already active.",
		);
		expect(window.location.hash).toBe("#/arena/levels/email-review/hard");
	});

	it("opens the dialog from “Run again” and drops the flag from the hash", async () => {
		renderAt("#/arena/levels/email-review/hard?start=1");
		expect(
			await screen.findByRole("dialog", { name: "Run evaluation" }),
		).toBeInTheDocument();
		expect(window.location.hash).toBe("#/arena/levels/email-review/hard");
	});
});
