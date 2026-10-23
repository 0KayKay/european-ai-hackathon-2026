import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type GuardrailCheck, GuardrailDetails } from "./GuardrailDetails";

export type Attempt = {
	threadId: string;
	mode: string;
	difficulty: string;
	createdAt: string;
	status: string;
	solved: boolean;
	solvedAt?: string;
	output?: string;
	error?: string;
	secret?: string;
	submission?: {
		subject?: string;
		body?: string;
		hidden_instructions?: string;
	};
	messages: { id: string; role: "user" | "assistant"; content: string }[];
	checks?: Record<string, boolean>;
	sentEmails?: {
		id: string;
		from: string;
		to: string;
		subject: string;
		body: string;
	}[];
	guardrails?: GuardrailCheck[];
	settings?: { model_id?: string; modelId?: string };
	log?: {
		id: string;
		type: string;
		name?: string;
		content: unknown;
		args?: unknown;
	}[];
	inputTokens?: number;
	outputTokens?: number;
	user?: string;
};

export function AttemptResult({ attempt }: { attempt: Attempt }) {
	return (
		<section
			className="mx-auto my-4 w-full max-w-3xl space-y-4 px-4"
			aria-label="Attempt result"
		>
			<Card>
				<CardHeader>
					<CardTitle>
						{attempt.status === "failed"
							? "Execution failed"
							: attempt.solved
								? "Your attack succeeded!"
								: "Your attack did not succeed"}
					</CardTitle>
				</CardHeader>
				<CardContent className="space-y-3 text-sm">
					{attempt.error && <p role="alert">{attempt.error}</p>}
					{attempt.solved && attempt.secret && (
						<p>
							Discovered secret:{" "}
							<code className="break-all">{attempt.secret}</code>
						</p>
					)}
					{attempt.checks && (
						<ul>
							{Object.entries(attempt.checks).map(([key, passed]) => (
								<li key={key}>
									{key === "price"
										? "Price check"
										: key === "routing"
											? "Routing target"
											: "Plausibility check"}
									: {passed ? "Correct" : "Not achieved"}
								</li>
							))}
						</ul>
					)}
					{attempt.output && (
						<details>
							<summary>Agent assessment</summary>
							<pre className="mt-2 whitespace-pre-wrap break-words">
								{attempt.output}
							</pre>
						</details>
					)}
					{attempt.submission && (
						<details>
							<summary>Submitted attack</summary>
							<pre className="mt-2 whitespace-pre-wrap break-words">
								{JSON.stringify(attempt.submission, null, 2)}
							</pre>
						</details>
					)}
					{attempt.mode === "CHALLENGE_2" && (
						<div>
							<h3 className="font-medium">
								Your inbox ({attempt.sentEmails?.length ?? 0})
							</h3>
							{attempt.sentEmails?.map((mail) => (
								<article key={mail.id} className="mt-2 rounded border p-3">
									<p>From: {mail.from}</p>
									<p>To: {mail.to}</p>
									<p>Subject: {mail.subject}</p>
									<p className="mt-2 whitespace-pre-wrap break-words">
										{mail.body}
									</p>
								</article>
							))}
						</div>
					)}
					<details>
						<summary>Agent log and guardrails</summary>
						<GuardrailDetails checks={attempt.guardrails ?? []} />
						{attempt.log?.map((entry) => (
							<article className="my-2 rounded border p-3" key={entry.id}>
								<p className="font-medium">
									{entry.type} {entry.name}
								</p>
								{entry.args !== undefined && (
									<pre className="whitespace-pre-wrap break-words">
										{JSON.stringify(entry.args, null, 2)}
									</pre>
								)}
								<pre className="whitespace-pre-wrap break-words">
									{typeof entry.content === "string"
										? entry.content
										: JSON.stringify(entry.content, null, 2)}
								</pre>
							</article>
						))}
					</details>
					<p className="text-xs text-muted-foreground">
						Tokens: {attempt.inputTokens ?? 0} input ·{" "}
						{attempt.outputTokens ?? 0} output
					</p>
				</CardContent>
			</Card>
		</section>
	);
}

export function ChallengeDashboard({
	attempts,
	onSelect,
	selectedId,
	loadingId,
}: {
	attempts: Attempt[];
	onSelect: (attempt: Attempt) => void;
	selectedId?: string;
	loadingId?: string;
}) {
	return (
		<div className="space-y-3 p-3 text-sm">
			<p>
				Attempted:{" "}
				{new Set(attempts.map((a) => `${a.mode}:${a.difficulty}`)).size}/9 ·
				Solved:{" "}
				{
					new Set(
						attempts
							.filter((a) => a.solved)
							.map((a) => `${a.mode}:${a.difficulty}`),
					).size
				}
				/9
			</p>
			<p className="text-xs text-muted-foreground">
				{attempts.length} saved attempts
			</p>
			<div className="space-y-1">
				{attempts.map((attempt) => (
					<Button
						className="h-auto w-full justify-start whitespace-normal text-left text-xs"
						variant={attempt.threadId === selectedId ? "secondary" : "ghost"}
						key={attempt.threadId}
						aria-pressed={attempt.threadId === selectedId}
						aria-busy={loadingId === attempt.threadId}
						onClick={() => onSelect(attempt)}
					>
						<span>
							{attempt.mode.replace("CHALLENGE_", "CH")} ·{" "}
							{attempt.difficulty.toLowerCase()} ·{" "}
							{loadingId === attempt.threadId
								? "Loading…"
								: attempt.solved
									? "Solved"
									: attempt.status === "failed"
										? "Run failed"
										: attempt.status === "running"
											? "In progress"
											: "Not solved"}
							<br />
							<span className="text-muted-foreground">
								{new Date(attempt.createdAt).toLocaleString()}
							</span>
						</span>
					</Button>
				))}
			</div>
		</div>
	);
}
