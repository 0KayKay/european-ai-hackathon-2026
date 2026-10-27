import type {
	CaseDetail,
	CreateRunRequest,
	DatasetDetail,
	DatasetSummary,
	Difficulty,
	LevelDetail,
	LevelSummary,
	RunDetail,
	RunExport,
	RunResults,
	RunSummary,
} from "./types";

export const ARENA_API_BASE = "/api/v1/arena";

/** Error thrown for every failed arena request; `status` is 0 for network failures. */
export class ArenaApiError extends Error {
	readonly status: number;
	readonly detail: unknown;

	constructor(message: string, status: number, detail?: unknown) {
		super(message);
		this.name = "ArenaApiError";
		this.status = status;
		this.detail = detail;
	}
}

type RequestOptions = { signal?: AbortSignal };

const segment = (value: string) => encodeURIComponent(value);

function messageFor(status: number, detail: unknown): string {
	if (typeof detail === "string" && detail) return detail;
	if (Array.isArray(detail) && detail.length) {
		// FastAPI validation errors: [{ loc, msg, type }]
		const first = detail[0] as { msg?: unknown };
		if (typeof first?.msg === "string") return first.msg;
	}
	if (status === 401) return "Your session has expired. Please sign in again.";
	if (status === 403) return "You are not allowed to access the arena.";
	if (status === 404) return "Not found.";
	return `Request failed (${status}).`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${ARENA_API_BASE}${path}`, {
			credentials: "same-origin",
			...init,
			headers: {
				Accept: "application/json",
				...(init.body ? { "Content-Type": "application/json" } : {}),
				...init.headers,
			},
		});
	} catch (error) {
		if (error instanceof DOMException && error.name === "AbortError")
			throw error;
		throw new ArenaApiError(
			"Could not reach the arena service. Please check your connection.",
			0,
		);
	}

	let body: unknown = null;
	const text = await response.text();
	if (text) {
		try {
			body = JSON.parse(text);
		} catch {
			body = text;
		}
	}

	if (!response.ok) {
		const detail =
			body && typeof body === "object" && "detail" in body
				? (body as { detail: unknown }).detail
				: body;
		throw new ArenaApiError(
			messageFor(response.status, detail),
			response.status,
			detail,
		);
	}
	return body as T;
}

const json = (body: unknown): RequestInit => ({
	method: "POST",
	body: JSON.stringify(body),
});

// Levels ---------------------------------------------------------------------

export const listLevels = (opts?: RequestOptions) =>
	request<LevelSummary[]>("/levels", opts);

export const getLevel = (
	level: string,
	difficulty: Difficulty,
	opts?: RequestOptions,
) =>
	request<LevelDetail>(
		`/levels/${segment(level)}/${segment(difficulty)}`,
		opts,
	);

// Datasets -------------------------------------------------------------------

export const listDatasets = (level: string, opts?: RequestOptions) =>
	request<DatasetSummary[]>(`/levels/${segment(level)}/datasets`, opts);

export const getDataset = (id: string, opts?: RequestOptions) =>
	request<DatasetDetail>(`/datasets/${segment(id)}`, opts);

// Runs -----------------------------------------------------------------------

export const createRun = (body: CreateRunRequest, opts?: RequestOptions) =>
	request<RunSummary>("/runs", { ...json(body), ...opts });

export const listRuns = (level?: string, opts?: RequestOptions) =>
	request<RunSummary[]>(
		level ? `/runs?level=${encodeURIComponent(level)}` : "/runs",
		opts,
	);

export const getRun = (id: string, opts?: RequestOptions) =>
	request<RunDetail>(`/runs/${segment(id)}`, opts);

export const stopRun = (id: string, opts?: RequestOptions) =>
	request<RunSummary>(`/runs/${segment(id)}/stop`, { method: "POST", ...opts });

export const getRunResults = (id: string, opts?: RequestOptions) =>
	request<RunResults>(`/runs/${segment(id)}/results`, opts);

export const getRunCase = (
	runId: string,
	caseId: string,
	opts?: RequestOptions,
) =>
	request<CaseDetail>(`/runs/${segment(runId)}/cases/${segment(caseId)}`, opts);

export const getRunExport = (id: string, opts?: RequestOptions) =>
	request<RunExport>(`/runs/${segment(id)}/export`, opts);

/** URL for a download link of the run export. */
export const runExportUrl = (id: string) =>
	`${ARENA_API_BASE}/runs/${segment(id)}/export`;
