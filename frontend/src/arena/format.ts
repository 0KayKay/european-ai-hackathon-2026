import type {
	CaseOutcome,
	CaseSummary,
	DecidedBy,
	Rate,
	RunStatus,
	Team,
} from "./types";

/** Elapsed time as `mm:ss` (or `h:mm:ss`). */
export function formatClock(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds))
		return "—";
	const total = Math.max(0, Math.floor(seconds));
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const pad = (n: number) => String(n).padStart(2, "0");
	return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** Case duration: `3.4 s` below a minute, `1:31` above. */
export function formatDuration(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds))
		return "—";
	if (seconds < 60) return `${seconds.toFixed(1)} s`;
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Event time since case start, e.g. `0.88s`. */
export const formatEventTime = (t: number) => `${t.toFixed(2)}s`;

/** A rate value (fraction) as a percentage with at most one decimal; `—` without a value. */
export function formatPercent(rate: Rate): string {
	if (rate.value === null || rate.value === undefined) return "—";
	const pct = Math.round(rate.value * 1000) / 10;
	return `${Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(1)}%`;
}

/** Model parameters on one line, e.g. `temperature 0 · reasoning_effort low`. */
export function formatParams(params: Record<string, unknown> | undefined) {
	const entries = Object.entries(params ?? {}).filter(
		([, value]) => value !== undefined && value !== null,
	);
	if (!entries.length) return "provider defaults";
	return entries
		.map(
			([key, value]) =>
				`${key} ${typeof value === "object" ? JSON.stringify(value) : String(value)}`,
		)
		.join(" · ");
}

export const TEAM_LABELS: Record<Team, string> = {
	red: "Red",
	green: "Green",
};

export const OUTCOME_LABELS: Record<CaseOutcome, string> = {
	succeeded: "Attack succeeded",
	defended: "Defended",
	passed: "Passed",
	false_positive: "False positive",
	error: "Error",
};

export const STATUS_LABELS: Record<RunStatus, string> = {
	generating: "Generating",
	running: "Running",
	completed: "Completed",
	stopped: "Stopped",
	failed: "Failed",
	interrupted: "Interrupted",
};

function decidedByText(decided: DecidedBy): string {
	switch (decided.kind) {
		case "guardrail":
			return `Guardrail ${decided.guardrail_id}`;
		case "success_check":
			return "Success check";
		case "agent":
			return "Agent answered";
		case "turn_limit":
			return "Turn limit";
		case "error":
			return "Error";
	}
}

/** Where a case was decided: the backend's label, else a plain name of `decided_by`. */
export function decidedLabel(
	item: Pick<CaseSummary, "decided_label" | "decided_by">,
): string {
	if (item.decided_label) return item.decided_label;
	return item.decided_by ? decidedByText(item.decided_by) : "—";
}
