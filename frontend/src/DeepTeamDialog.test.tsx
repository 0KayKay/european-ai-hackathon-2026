import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeepTeamDialog } from "./DeepTeamDialog";

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

it("submits the goal and AG-UI target and shows the result", async () => {
	const fetch = vi.fn(async () => ({
		ok: true,
		json: async () => ({ assessment: { score: 1 } }),
	}));
	vi.stubGlobal("fetch", fetch);
	render(<DeepTeamDialog />);

	fireEvent.click(screen.getByRole("button", { name: "Red team" }));
	fireEvent.change(screen.getByRole("textbox", { name: "Goal" }), {
		target: { value: "Protect the assistant" },
	});
	fireEvent.click(screen.getByRole("button", { name: "Start run" }));

	await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
	expect(fetch).toHaveBeenCalledWith("/api/deepteam/runs", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			goal: "Protect the assistant",
			target_url: "http://backend:8000/api/v1/ag-ui",
		}),
	});
	expect(await screen.findByText("Run complete")).toBeInTheDocument();
});
