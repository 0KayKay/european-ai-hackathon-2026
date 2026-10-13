import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function DeepTeamDialog() {
	const [goal, setGoal] = useState("");
	const [targetUrl, setTargetUrl] = useState(
		"http://backend:8000/api/v1/ag-ui",
	);
	const [pending, setPending] = useState(false);
	const [result, setResult] = useState<unknown>(null);
	const [error, setError] = useState("");

	async function startRun(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		setPending(true);
		setResult(null);
		setError("");
		try {
			const response = await fetch("/api/deepteam/runs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ goal, target_url: targetUrl }),
			});
			const body = await response.json();
			if (!response.ok) {
				throw new Error(
					typeof body.detail === "string"
						? body.detail
						: `Run failed (${response.status})`,
				);
			}
			setResult(body);
		} catch (cause) {
			setError(cause instanceof Error ? cause.message : "Run failed");
		} finally {
			setPending(false);
		}
	}

	return (
		<Dialog>
			<DialogTrigger asChild>
				<Button type="button" variant="outline">
					Red team
				</Button>
			</DialogTrigger>
			<DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>Run DeepTeam</DialogTitle>
					<DialogDescription>
						Challenge an AG-UI API. The scan may take several minutes.
					</DialogDescription>
				</DialogHeader>
				<form onSubmit={startRun} className="space-y-4">
					<label
						htmlFor="deepteam-goal"
						className="block space-y-1 text-sm font-medium"
					>
						<span>Goal</span>
						<Textarea
							id="deepteam-goal"
							required
							maxLength={500}
							value={goal}
							onChange={(event) => setGoal(event.target.value)}
							placeholder="Protect the finance assistant from leaking secrets"
						/>
					</label>
					<label
						htmlFor="deepteam-target"
						className="block space-y-1 text-sm font-medium"
					>
						<span>AG-UI target URL</span>
						<Input
							id="deepteam-target"
							type="url"
							required
							value={targetUrl}
							onChange={(event) => setTargetUrl(event.target.value)}
						/>
					</label>
					<Button type="submit" disabled={pending}>
						{pending ? "Running…" : "Start run"}
					</Button>
				</form>
				{error && (
					<p role="alert" className="text-sm text-destructive">
						{error}
					</p>
				)}
				{result !== null && (
					<div role="status" className="space-y-2">
						<p className="text-sm font-medium">Run complete</p>
						<pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
							{JSON.stringify(result, null, 2)}
						</pre>
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}
