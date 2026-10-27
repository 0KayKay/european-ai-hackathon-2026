import { useState } from "react";
import AGUIPlayground from "./AGUIPlayground";
import { ArenaApp } from "./arena/ArenaApp";
import { useArenaRoute } from "./arena/router";
import { Button } from "./components/ui/button";
import { authClient } from "./lib/auth-client";

export default function App() {
	const { data: session, isPending } = authClient.useSession();
	const [error, setError] = useState("");
	const arenaRoute = useArenaRoute();

	if (isPending) {
		return (
			<main className="grid h-dvh place-items-center">Checking session…</main>
		);
	}
	if (!session) {
		return (
			<main className="grid h-dvh place-items-center bg-background text-foreground">
				<div className="flex flex-col items-center gap-4">
					<h1 className="text-xl font-medium">Guardrail lab</h1>
					<Button
						onClick={async () => {
							try {
								const result = await authClient.signIn.social({
									provider: "github",
									callbackURL: window.location.origin,
								});
								if (result.error)
									setError("GitHub sign-in failed. Please try again.");
							} catch {
								setError("GitHub sign-in failed. Please try again.");
							}
						}}
					>
						Sign in with GitHub
					</Button>
					{error && (
						<p role="alert" className="text-sm text-destructive">
							{error}
						</p>
					)}
				</div>
			</main>
		);
	}

	// The playground stays mounted while the arena is open, so a running chat keeps its state.
	return (
		<>
			{arenaRoute && <ArenaApp route={arenaRoute} />}
			<div hidden={Boolean(arenaRoute)}>
				<AGUIPlayground
					userName={session.user.name}
					onSignOut={() => void authClient.signOut()}
				/>
			</div>
		</>
	);
}
