import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { levelsFixture } from "./arena/fixtures";
import { mockArenaFetch } from "./arena/testing/fetch";
import { authClient } from "./lib/auth-client";

const session = vi.hoisted(() => ({
	current: null as null | { user: { name: string } },
}));

vi.mock("./lib/auth-client", () => ({
	authClient: {
		useSession: () => ({ data: session.current, isPending: false }),
		signIn: { social: vi.fn(async () => ({ error: null })) },
		signOut: vi.fn(async () => ({ error: null })),
	},
}));

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	vi.clearAllMocks();
	session.current = null;
	window.location.hash = "";
});

it("shows only the GitHub sign-in while signed out", async () => {
	const fetch = mockArenaFetch({});
	render(<App />);

	expect(
		screen.getByRole("heading", { name: "Guardrail Arena" }),
	).toBeInTheDocument();
	expect(screen.queryByRole("heading", { name: "Levels" })).toBeNull();
	expect(fetch).not.toHaveBeenCalled();
	fireEvent.click(screen.getByRole("button", { name: "Sign in with GitHub" }));
	await waitFor(() =>
		expect(authClient.signIn.social).toHaveBeenCalledWith({
			provider: "github",
			callbackURL: window.location.origin,
		}),
	);
});

it("opens the level overview at #/arena after sign-in", async () => {
	session.current = { user: { name: "Ada Lovelace" } };
	mockArenaFetch({ "/api/v1/arena/levels": levelsFixture });
	render(<App />);

	expect(
		await screen.findByRole("heading", { name: "Levels" }),
	).toBeInTheDocument();
	expect(window.location.hash).toBe("#/arena");
	expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
	expect(screen.getByRole("link", { name: "Guardrail Arena" })).toHaveAttribute(
		"href",
		"#/arena",
	);
	expect(screen.queryByRole("link", { name: /playground/i })).toBeNull();
});

it("signs out through the auth client", async () => {
	session.current = { user: { name: "Ada Lovelace" } };
	mockArenaFetch({ "/api/v1/arena/levels": levelsFixture });
	render(<App />);

	fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
	expect(authClient.signOut).toHaveBeenCalledOnce();
});
