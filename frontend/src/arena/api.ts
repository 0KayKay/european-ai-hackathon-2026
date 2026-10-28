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
	SaveSourceRequest,
} from "./types";

export const ARENA_API_BASE = "/api/v1/arena";

/** Error thrown for every failed arena request; `status` is 0 for network failures. */
export class ArenaApiError extends Error {
	readonly status: number;
	readonly detail: unknown;
	/** The whole response body (e.g. `errors` of a 422, `current` of a 409). */
	readonly body: unknown;

	constructor(
		message: string,
		status: number,
		detail?: unknown,
		body?: unknown,
	) {
		super(message);
		this.name = "ArenaApiError";
		this.status = status;
		this.detail = detail;
		this.body = body ?? detail;
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
			body,
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

/**
 * Saves edited level files (validated with every difficulty). Rejects with an `ArenaApiError`:
 * 422 `body.errors: SourceProblem[]`, 409 `body.current: LevelDetail` (someone else saved).
 */
export const saveLevelSource = (
	level: string,
	body: SaveSourceRequest,
	opts?: RequestOptions,
) =>
	request<LevelDetail>(`/levels/${segment(level)}/source`, {
		method: "PUT",
		body: JSON.stringify(body),
		...opts,
	});

/** Drops the level's saved edits: it is served from the repository files again. */
export const resetLevelSource = (
	level: string,
	params: { difficulty?: Difficulty; base_version?: number } = {},
	opts?: RequestOptions,
) => {
	const query = new URLSearchParams();
	if (params.difficulty) query.set("difficulty", params.difficulty);
	if (params.base_version !== undefined)
		query.set("base_version", String(params.base_version));
	const search = query.toString();
	return request<LevelDetail>(
		`/levels/${segment(level)}/source${search ? `?${search}` : ""}`,
		{ method: "DELETE", ...opts },
	);
};

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
