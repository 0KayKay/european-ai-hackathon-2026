import {
	CheckIcon,
	FileTextIcon,
	LoaderCircleIcon,
	ShieldCheckIcon,
	ShieldXIcon,
	SparklesIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type GuardrailCheck = {
	id: string;
	name: string;
	status: string;
	stage?: string;
	rule?: string;
	reason: string;
};
const titles: Record<string, string> = {
	InputGuardrail: "Request safety",
	InputTextSanitizerGuardrail: "Input normalization",
	OutputToolTextSanitizerGuardrail: "Text normalization",
	OutputGuardrail: "Response safety",
	SendEmailGuardrail: "Email authorization",
};

export function GuardrailDetails({
	checks,
	busy = false,
}: {
	checks: GuardrailCheck[];
	busy?: boolean;
}) {
	const blocked = checks.filter((check) => check.status === "blocked").length;
	return (
		<section aria-label="Guardrail details" className="space-y-3">
			<div className="flex items-center justify-between gap-2">
				<h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
					Individual checks
				</h3>
				{checks.length > 0 && (
					<span
						className={cn(
							"rounded-full border px-2 py-0.5 text-[10px] font-medium",
							blocked > 0
								? "border-destructive/30 text-destructive"
								: "text-muted-foreground",
						)}
					>
						{blocked > 0
							? `${blocked} blocked`
							: `${checks.filter((check) => check.status !== "pending").length} checked`}
					</span>
				)}
			</div>
			{checks.length === 0 && (
				<p className="text-xs leading-5 text-muted-foreground">
					{busy
						? "Waiting for the next security check…"
						: "Checks and their explanations appear here when an attempt runs."}
				</p>
			)}
			<ol className="space-y-2">
				{checks.map((check) => {
					const pending = check.status === "pending";
					const rejected = check.status === "blocked";
					const normalized = check.status === "sanitized";
					const Icon = pending
						? LoaderCircleIcon
						: rejected
							? ShieldXIcon
							: normalized
								? SparklesIcon
								: ShieldCheckIcon;
					return (
						<li
							key={check.id}
							className={cn(
								"rounded-lg border bg-background p-3 transition-colors",
								rejected && "border-destructive/30 bg-destructive/5",
								pending && "border-primary/40",
							)}
						>
							<div className="flex items-start gap-2">
								<Icon
									aria-hidden="true"
									className={cn(
										"mt-0.5 size-4 shrink-0",
										pending && "motion-safe:animate-spin text-primary",
										rejected ? "text-destructive" : "text-primary",
									)}
								/>
								<div className="min-w-0 flex-1">
									<p className="text-xs font-semibold">
										{titles[check.name] ?? check.name}
									</p>
									<p className="mt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
										{check.stage ??
											(check.name.startsWith("Input")
												? "input"
												: "output")}{" "}
										·{" "}
										{pending
											? "Checking"
											: rejected
												? "Blocked"
												: normalized
													? "Normalized"
													: "Passed"}
									</p>
								</div>
							</div>
							{check.rule && (
								<p className="mt-2 text-xs font-medium">{check.rule}</p>
							)}
							{check.reason && (
								<p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
									{check.reason}
								</p>
							)}
						</li>
					);
				})}
			</ol>
		</section>
	);
}

export function SubmissionActivity({ label }: { label: string }) {
	return (
		<div
			role="status"
			aria-label="Submission activity"
			className="mx-auto my-4 w-full max-w-2xl shrink-0 overflow-hidden rounded-lg border border-primary/30 bg-primary/5 motion-safe:animate-in motion-safe:fade-in"
		>
			<div className="flex items-center gap-4 p-5">
				<div className="relative flex size-11 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-background">
					<FileTextIcon className="size-5 text-primary" aria-hidden="true" />
					<span className="absolute -bottom-1 -right-1 rounded-full bg-background p-1">
						<LoaderCircleIcon
							aria-hidden="true"
							className="size-3 text-primary motion-safe:animate-spin"
						/>
					</span>
				</div>
				<div className="min-w-0">
					<p className="text-sm font-semibold">Submission in progress</p>
					<p className="mt-1 text-xs text-muted-foreground">{label}</p>
				</div>
			</div>
			<div className="h-0.5 overflow-hidden bg-primary/10">
				<div className="h-full w-1/3 bg-primary motion-safe:animate-[submission-progress_1.8s_ease-in-out_infinite]" />
			</div>
			<p className="flex items-center gap-1.5 px-5 py-3 text-[11px] text-muted-foreground">
				<CheckIcon className="size-3" aria-hidden="true" />
				Attack submitted · Agent processing · Evaluating outcome
			</p>
		</div>
	);
}
