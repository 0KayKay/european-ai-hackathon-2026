import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { authClient } from "./lib/auth-client";

vi.mock("./lib/auth-client", () => ({
	authClient: {
		useSession: () => ({ data: null, isPending: false }),
		signIn: { social: vi.fn(async () => ({ error: null })) },
	},
}));

afterEach(cleanup);

it("keeps the chat hidden until GitHub sign-in", async () => {
	render(<App />);

	expect(
		screen.queryByRole("textbox", { name: "Message input" }),
	).not.toBeInTheDocument();
	fireEvent.click(screen.getByRole("button", { name: "Sign in with GitHub" }));
	await waitFor(() =>
		expect(authClient.signIn.social).toHaveBeenCalledWith({
			provider: "github",
			callbackURL: window.location.origin,
		}),
	);
});
