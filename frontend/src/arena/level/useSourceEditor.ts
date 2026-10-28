import { useCallback, useEffect, useMemo, useState } from "react";
import { ArenaApiError, resetLevelSource, saveLevelSource } from "../api";
import type {
	Difficulty,
	LevelDetail,
	SourceFile,
	SourceProblem,
} from "../types";

/** Whether a source file can be edited with this level (its own directory, never `shared/`). */
export const isEditable = (level: string, file: SourceFile) =>
	file.editable ?? file.path.startsWith(`${level}/`);

export const DISCARD_CONFIRM = "Discard your unsaved changes?";
export const RESET_CONFIRM =
	"Reset this level to the repository version? The edits saved in the UI are removed for everyone.";

export interface SourceEditor {
	editing: boolean;
	/** Edited content by path (editable files and new files). */
	drafts: Record<string, string>;
	/** Files added in this edit (not yet saved). */
	newFiles: string[];
	/** Paths whose draft differs from the served content. */
	changed: string[];
	dirty: boolean;
	problems: SourceProblem[];
	/** The level as someone else saved it (409), until loaded or dismissed. */
	conflict: LevelDetail | null;
	busy: "saving" | "resetting" | null;
	error: string | null;
	notice: string | null;
	canEdit: boolean;
	/** The level directory, relative to `backend/levels/` (new files go there). */
	levelDir: string;
	start: (fromSavedEdits?: boolean) => void;
	change: (path: string, content: string) => void;
	/** Adds a file relative to the level directory; returns its path, or an error message. */
	addFile: (relative: string) => { path?: string; error?: string };
	save: () => Promise<void>;
	cancel: () => boolean;
	reset: () => Promise<void>;
	loadConflict: () => void;
	dismissConflict: () => void;
	/** Leaves edit mode without asking (navigation already confirmed). */
	discard: () => void;
}

const problemsOf = (error: ArenaApiError): SourceProblem[] | null => {
	const body = error.body as { errors?: unknown } | null;
	return Array.isArray(body?.errors) ? (body.errors as SourceProblem[]) : null;
};

const conflictOf = (error: ArenaApiError): LevelDetail | null => {
	const body = error.body as { current?: LevelDetail } | null;
	return error.status === 409 && body?.current ? body.current : null;
};

/**
 * Plain-text editing of a level's own source files. Edits are sent to `PUT /levels/{level}/source`,
 * which validates the whole level; `onDetail` receives the level view the backend answers with.
 */
export function useSourceEditor({
	level,
	difficulty,
	detail,
	onDetail,
}: {
	level: string;
	difficulty: Difficulty;
	detail: LevelDetail | undefined;
	onDetail: (detail: LevelDetail) => void;
}): SourceEditor {
	const [editing, setEditing] = useState(false);
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [newFiles, setNewFiles] = useState<string[]>([]);
	const [problems, setProblems] = useState<SourceProblem[]>([]);
	const [conflict, setConflict] = useState<LevelDetail | null>(null);
	const [busy, setBusy] = useState<SourceEditor["busy"]>(null);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<string | null>(null);

	const files = detail?.source.files ?? [];
	const served = useMemo(
		() => new Map(files.map((f) => [f.path, f.content])),
		[files],
	);
	const canEdit = files.some((f) => isEditable(level, f));

	const clear = useCallback(() => {
		setEditing(false);
		setDrafts({});
		setNewFiles([]);
		setProblems([]);
		setConflict(null);
		setError(null);
	}, []);

	// Another difficulty is another view: edits do not carry over (navigation asked first).
	// biome-ignore lint/correctness/useExhaustiveDependencies: reset when the route changes.
	useEffect(() => {
		clear();
		setNotice(null);
	}, [level, difficulty, clear]);

	const begin = useCallback(
		(from: LevelDetail, fromSavedEdits: boolean) => {
			const next: Record<string, string> = {};
			for (const file of from.source.files)
				if (isEditable(level, file)) next[file.path] = file.content;
			const added: string[] = [];
			if (fromSavedEdits)
				for (const file of from.override_error?.files ?? []) {
					if (!(file.path in next)) added.push(file.path);
					next[file.path] = file.content;
				}
			setDrafts(next);
			setNewFiles(added);
			setProblems(fromSavedEdits ? (from.override_error?.errors ?? []) : []);
			setConflict(null);
			setError(null);
			setNotice(null);
			setEditing(true);
		},
		[level],
	);

	const changed = useMemo(
		() =>
			editing
				? Object.keys(drafts).filter(
						(path) => drafts[path] !== served.get(path),
					)
				: [],
		[editing, drafts, served],
	);
	const dirty = changed.length > 0;

	const fail = useCallback((reason: unknown) => {
		if (reason instanceof ArenaApiError) {
			const current = conflictOf(reason);
			if (current) {
				setConflict(current);
				return;
			}
			const items = problemsOf(reason);
			if (items) {
				setProblems(items);
				setError(null);
				return;
			}
		}
		setError(reason instanceof Error ? reason.message : String(reason));
	}, []);

	const save = useCallback(async () => {
		if (!detail || busy) return;
		setBusy("saving");
		setError(null);
		try {
			const saved = await saveLevelSource(level, {
				files: changed.map((path) => ({ path, content: drafts[path] })),
				base_version: detail.version ?? 0,
				difficulty,
			});
			onDetail(saved);
			clear();
			setNotice(
				changed.length
					? `Saved · version ${saved.version ?? "?"}`
					: "Nothing to save",
			);
		} catch (reason) {
			fail(reason);
		} finally {
			setBusy(null);
		}
	}, [detail, busy, level, changed, drafts, difficulty, onDetail, clear, fail]);

	const reset = useCallback(async () => {
		if (!detail || busy || !window.confirm(RESET_CONFIRM)) return;
		setBusy("resetting");
		setError(null);
		try {
			const current = await resetLevelSource(level, {
				difficulty,
				base_version: detail.version ?? 0,
			});
			onDetail(current);
			clear();
			setNotice("Reset to the repository version");
		} catch (reason) {
			fail(reason);
		} finally {
			setBusy(null);
		}
	}, [detail, busy, level, difficulty, onDetail, clear, fail]);

	return {
		editing,
		drafts,
		newFiles,
		changed,
		dirty,
		problems,
		conflict,
		busy,
		error,
		notice,
		canEdit,
		levelDir: level,
		start: (fromSavedEdits = false) => {
			if (detail) begin(detail, fromSavedEdits);
		},
		change: (path, content) => {
			setDrafts((current) => ({ ...current, [path]: content }));
			setNotice(null);
		},
		addFile: (relative) => {
			const name = relative.trim().replace(/^\.?\/+/, "");
			const parts = name.split("/");
			if (!name)
				return { error: "Enter a file path, e.g. prompts/new_judge.md" };
			if (parts.some((part) => part === "" || part === "." || part === ".."))
				return { error: "Use a plain path inside the level directory." };
			const path = `${level}/${name}`;
			if (path in drafts || served.has(path))
				return { error: "This file already exists." };
			setNewFiles((current) => [...current, path]);
			setDrafts((current) => ({ ...current, [path]: "" }));
			return { path };
		},
		save,
		cancel: () => {
			if (dirty && !window.confirm(DISCARD_CONFIRM)) return false;
			clear();
			return true;
		},
		reset,
		loadConflict: () => {
			if (!conflict) return;
			onDetail(conflict);
			begin(conflict, false);
		},
		dismissConflict: () => setConflict(null),
		discard: clear,
	};
}
