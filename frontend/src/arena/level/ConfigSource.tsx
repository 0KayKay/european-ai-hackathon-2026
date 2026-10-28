import {
	FileCodeIcon,
	FilePlusIcon,
	FileTextIcon,
	PencilIcon,
	RotateCcwIcon,
	SaveIcon,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import type { OverrideError, SourceFile, SourceProblem } from "../types";
import { SourceEditor as SourceTextEditor } from "./SourceEditor";
import type { SourceEditor } from "./useSourceEditor";

export interface LineRange {
	path: string;
	/** 1-based, inclusive. */
	start: number;
	end: number;
}

const isYaml = (path: string) => /\.ya?ml$/i.test(path);

type Token = { text: string; className?: string };

const TOKEN_CLASSES = {
	key: "text-blue-700",
	string: "text-emerald-700",
	comment: "text-slate-400",
	placeholder: "text-fuchsia-700",
} as const;

/** Minimal YAML coloring: keys, comments, quoted strings and `{{placeholders}}`. */
export function tokenizeLine(line: string, yaml: boolean): Token[] {
	const placeholders = (text: string, base?: string): Token[] =>
		text
			.split(/(\{\{[^}]+\}\})/g)
			.filter(Boolean)
			.map((part) =>
				part.startsWith("{{") && part.endsWith("}}")
					? { text: part, className: TOKEN_CLASSES.placeholder }
					: { text: part, className: base },
			);
	if (!yaml) return placeholders(line);

	let code = line;
	let comment = "";
	const hash = line.search(/(^|\s)#/);
	if (hash >= 0) {
		code = line.slice(0, hash);
		comment = line.slice(hash);
	}
	const value = (text: string): Token[] =>
		text
			.split(/("[^"]*")/g)
			.filter(Boolean)
			.flatMap((part) =>
				part.startsWith('"') && part.endsWith('"') && part.length > 1
					? placeholders(part, TOKEN_CLASSES.string)
					: placeholders(part),
			);
	const key = code.match(/^(\s*(?:-\s+)?)([A-Za-z_][\w.-]*)(:)(.*)$/);
	const tokens: Token[] = key
		? [
				{ text: key[1] },
				{ text: key[2], className: TOKEN_CLASSES.key },
				{ text: key[3] },
				...value(key[4]),
			]
		: value(code);
	if (comment) tokens.push({ text: comment, className: TOKEN_CLASSES.comment });
	return tokens.filter((t) => t.text);
}

function renderTokens(tokens: Token[]): ReactNode {
	let offset = 0;
	return tokens.map((token) => {
		const key = offset;
		offset += token.text.length;
		return token.className ? (
			<span key={key} className={token.className}>
				{token.text}
			</span>
		) : (
			<Fragment key={key}>{token.text}</Fragment>
		);
	});
}

function Pill({
	tone,
	children,
}: {
	tone: "amber" | "blue" | "slate" | "red";
	children: ReactNode;
}) {
	const tones = {
		amber: "border-amber-200 bg-amber-50 text-amber-800",
		blue: "border-blue-200 bg-blue-50 text-blue-700",
		slate: "border-slate-200 text-slate-500",
		red: "border-red-200 bg-red-50 text-red-700",
	};
	return (
		<span
			className={cn(
				"shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
				tones[tone],
			)}
		>
			{children}
		</span>
	);
}

function Banner({
	tone,
	label,
	children,
}: {
	tone: "amber" | "red";
	label: string;
	children: ReactNode;
}) {
	const className = cn(
		"flex shrink-0 flex-col gap-2 border-b px-3 py-2.5 text-[12.5px] leading-snug",
		tone === "red" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-900",
	);
	return tone === "red" ? (
		<div role="alert" aria-label={label} className={className}>
			{children}
		</div>
	) : (
		<div role="status" aria-label={label} className={className}>
			{children}
		</div>
	);
}

/** The toolbar of the config pane: edit, save, cancel, new file, reset. */
function EditorToolbar({
	editor,
	modified,
	onAddFile,
}: {
	editor: SourceEditor;
	modified: boolean;
	onAddFile: (path: string) => void;
}) {
	const [adding, setAdding] = useState(false);
	const [name, setName] = useState("");
	const [addError, setAddError] = useState<string | null>(null);
	const busy = editor.busy !== null;

	const add = () => {
		const result = editor.addFile(name);
		if (result.error) return setAddError(result.error);
		setAdding(false);
		setName("");
		setAddError(null);
		if (result.path) onAddFile(result.path);
	};

	return (
		<div className="flex shrink-0 flex-col gap-2 border-b px-3 py-2">
			<div className="flex flex-wrap items-center gap-2">
				{editor.editing ? (
					<span className="text-xs text-slate-600">
						{editor.changed.length
							? `${editor.changed.length} changed file${editor.changed.length === 1 ? "" : "s"}`
							: "No changes yet"}
					</span>
				) : (
					<>
						{modified ? (
							<Pill tone="amber">Modified</Pill>
						) : (
							<span className="text-xs text-slate-500">Repository version</span>
						)}
						{editor.notice && (
							<span role="status" className="text-xs text-emerald-700">
								{editor.notice}
							</span>
						)}
					</>
				)}
				<div className="ml-auto flex flex-wrap items-center gap-1.5">
					{editor.editing ? (
						<>
							<Button
								type="button"
								size="xs"
								variant="ghost"
								disabled={busy}
								onClick={() => setAdding((open) => !open)}
							>
								<FilePlusIcon aria-hidden="true" />
								New file
							</Button>
							<Button
								type="button"
								size="xs"
								variant="outline"
								disabled={busy}
								onClick={() => editor.cancel()}
							>
								Cancel
							</Button>
							<Button
								type="button"
								size="xs"
								disabled={busy || !editor.dirty}
								onClick={() => void editor.save()}
							>
								{editor.busy === "saving" ? (
									<Spinner className="size-3" />
								) : (
									<SaveIcon aria-hidden="true" />
								)}
								Save
							</Button>
						</>
					) : (
						<>
							{modified && (
								<Button
									type="button"
									size="xs"
									variant="ghost"
									disabled={busy}
									onClick={() => void editor.reset()}
								>
									{editor.busy === "resetting" ? (
										<Spinner className="size-3" />
									) : (
										<RotateCcwIcon aria-hidden="true" />
									)}
									Reset to repository version
								</Button>
							)}
							{editor.canEdit && (
								<Button
									type="button"
									size="xs"
									variant="outline"
									disabled={busy}
									onClick={() => editor.start()}
								>
									<PencilIcon aria-hidden="true" />
									Edit
								</Button>
							)}
						</>
					)}
				</div>
			</div>
			{editor.editing && adding && (
				<form
					className="flex flex-col gap-1"
					onSubmit={(event) => {
						event.preventDefault();
						add();
					}}
				>
					<div className="flex items-center gap-1.5">
						<label className="flex min-w-0 flex-1 items-center gap-1 rounded-md border px-2 text-xs focus-within:border-blue-400">
							<span className="text-slate-400">{editor.levelDir}/</span>
							<input
								aria-label="New file path"
								value={name}
								placeholder="prompts/new_judge.md"
								onChange={(event) => setName(event.target.value)}
								className="h-7 min-w-0 flex-1 bg-transparent font-mono outline-none"
							/>
						</label>
						<Button type="submit" size="xs" variant="outline">
							Add
						</Button>
					</div>
					<p className="text-[11.5px] text-slate-500">
						{addError ??
							"Reference the new file from level.yaml in the same save."}
					</p>
				</form>
			)}
		</div>
	);
}

export function ConfigSource({
	files,
	activePath,
	onSelectPath,
	highlight,
	editor,
	modified = false,
	overrideError,
}: {
	files: SourceFile[];
	activePath: string;
	onSelectPath: (path: string) => void;
	highlight?: LineRange | null;
	/** Enables editing of the level's own files. */
	editor?: SourceEditor;
	/** The level is served with saved edits. */
	modified?: boolean;
	overrideError?: OverrideError | null;
}) {
	const editing = editor?.editing ?? false;
	const shown: SourceFile[] = editing
		? [
				...files,
				...(editor?.newFiles ?? []).map((path) => ({
					path,
					content: "",
					editable: true,
				})),
			]
		: files;
	const file = shown.find((f) => f.path === activePath) ?? shown[0];
	const editable =
		editing && editor && file ? file.path in editor.drafts : false;
	const lines = (file?.content ?? "").replace(/\n$/, "").split("\n");
	const marked =
		highlight && file && highlight.path === file.path && !editing
			? highlight
			: null;
	const firstMarked = useRef<HTMLDivElement>(null);
	const problems = editor?.problems ?? [];
	const [focus, setFocus] = useState<{ line: number; nonce: number } | null>(
		null,
	);

	useEffect(() => {
		if (marked) firstMarked.current?.scrollIntoView?.({ block: "center" });
	}, [marked]);

	const jump = (problem: SourceProblem) => {
		onSelectPath(problem.path);
		if (problem.line)
			setFocus((current) => ({
				line: problem.line as number,
				nonce: (current?.nonce ?? 0) + 1,
			}));
	};
	// A rejected save opens the first problem's file at its line.
	// biome-ignore lint/correctness/useExhaustiveDependencies: only when new problems arrive.
	useEffect(() => {
		if (problems[0]) jump(problems[0]);
	}, [problems]);

	const problemLines = (path: string) =>
		problems
			.filter((p) => p.path === path && p.line)
			.map((p) => p.line as number);
	const problemLinesOfFile = file ? problemLines(file.path) : [];

	return (
		<section
			aria-label="Config source"
			className="flex min-h-0 flex-col border-b bg-white lg:border-r lg:border-b-0"
		>
			<div className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
				<div
					role="tablist"
					aria-label="Config files"
					className="flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none]"
				>
					{shown.map((f) => {
						const selected = f.path === file?.path;
						const Icon = isYaml(f.path) ? FileCodeIcon : FileTextIcon;
						const unsaved = editor?.changed.includes(f.path);
						const failed = problems.some((p) => p.path === f.path);
						return (
							<button
								key={f.path}
								type="button"
								role="tab"
								aria-selected={selected}
								title={f.path}
								onClick={() => onSelectPath(f.path)}
								className={cn(
									"flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] whitespace-nowrap transition-colors",
									selected
										? "bg-slate-100 font-medium text-slate-900"
										: "text-slate-500 hover:text-slate-900",
									failed && "text-red-700",
								)}
							>
								<Icon className="size-3.5" aria-hidden="true" />
								{f.path}
								{f.modified && !unsaved && (
									<span className="rounded bg-amber-100 px-1 text-[10.5px] font-medium text-amber-800">
										Modified
									</span>
								)}
								{unsaved && (
									<span
										role="img"
										aria-label="unsaved changes"
										className="size-1.5 rounded-full bg-blue-500"
									/>
								)}
								{failed && (
									<span
										role="img"
										aria-label="has errors"
										className="size-1.5 rounded-full bg-red-500"
									/>
								)}
							</button>
						);
					})}
				</div>
				{editable ? (
					<Pill tone="blue">editing</Pill>
				) : (
					<Pill tone="slate">read-only</Pill>
				)}
			</div>
			{editor && (
				<EditorToolbar
					editor={editor}
					modified={modified}
					onAddFile={onSelectPath}
				/>
			)}
			{editor && overrideError && !editing && (
				<Banner tone="red" label="Saved edits do not load">
					<p>
						<strong className="font-semibold">
							The edits saved for this level no longer load.
						</strong>{" "}
						The repository version is in use.
					</p>
					<p className="font-mono text-[11.5px] break-words">
						{overrideError.errors[0]
							? `${overrideError.errors[0].path}${overrideError.errors[0].line ? `:${overrideError.errors[0].line}` : ""} · ${overrideError.errors[0].message}`
							: overrideError.message}
					</p>
					<div className="flex flex-wrap gap-1.5">
						<Button
							type="button"
							size="xs"
							variant="outline"
							onClick={() => editor.start(true)}
						>
							Fix the saved edits
						</Button>
						<Button
							type="button"
							size="xs"
							variant="ghost"
							onClick={() => void editor.reset()}
						>
							Reset to repository version
						</Button>
					</div>
				</Banner>
			)}
			{editor?.conflict && (
				<Banner tone="amber" label="Save conflict">
					<p>
						<strong className="font-semibold">
							Someone else saved this level in the meantime
						</strong>{" "}
						(version {editor.conflict.version ?? "?"}). Your changes were not
						saved.
					</p>
					<div className="flex flex-wrap gap-1.5">
						<Button
							type="button"
							size="xs"
							variant="outline"
							onClick={() => editor.loadConflict()}
						>
							Load their version
						</Button>
						<Button
							type="button"
							size="xs"
							variant="ghost"
							onClick={() => editor.dismissConflict()}
						>
							Keep my changes
						</Button>
					</div>
				</Banner>
			)}
			{editor?.error && (
				<Banner tone="red" label="Save failed">
					<p>{editor.error}</p>
				</Banner>
			)}
			{problems.length > 0 && (
				<Banner tone="red" label="Validation errors">
					<p className="font-semibold">
						The level config is invalid; nothing was saved.
					</p>
					<ul className="flex flex-col gap-1">
						{problems.map((problem, index) => (
							<li
								// biome-ignore lint/suspicious/noArrayIndexKey: problems have no id.
								key={index}
							>
								<button
									type="button"
									onClick={() => jump(problem)}
									className="text-left hover:underline"
								>
									<span className="font-mono font-medium">
										{problem.path}
										{problem.line ? `:${problem.line}` : ""}
									</span>{" "}
									{problem.message}
								</button>
							</li>
						))}
					</ul>
				</Banner>
			)}
			<div
				role="tabpanel"
				aria-label={file?.path ?? "No file"}
				className={cn(
					"flex min-h-0 flex-1 flex-col",
					!editable && "overflow-auto py-3",
				)}
			>
				{file && editable && editor ? (
					<SourceTextEditor
						key={file.path}
						path={file.path}
						value={editor.drafts[file.path] ?? ""}
						onChange={(content) => editor.change(file.path, content)}
						errorLines={problemLinesOfFile}
						focus={focus}
					/>
				) : file ? (
					<pre className="min-w-max font-mono text-xs leading-[19px] text-slate-800">
						{lines.map((line, index) => {
							const n = index + 1;
							const on =
								(marked && n >= marked.start && n <= marked.end) ||
								problemLinesOfFile.includes(n);
							return (
								<div
									key={n}
									ref={on && n === marked?.start ? firstMarked : undefined}
									data-marked={on || undefined}
									className={cn(
										"flex border-l-2 border-transparent pr-4",
										on &&
											(problemLinesOfFile.includes(n)
												? "border-red-500 bg-red-50"
												: "border-blue-500 bg-blue-50"),
									)}
								>
									<span
										aria-hidden="true"
										className="w-10 shrink-0 pr-3 text-right text-slate-400 select-none"
									>
										{n}
									</span>
									<code className="font-mono">
										{line
											? renderTokens(tokenizeLine(line, isYaml(file.path)))
											: " "}
									</code>
								</div>
							);
						})}
					</pre>
				) : (
					<p className="px-4 text-sm text-slate-500">No config files.</p>
				)}
			</div>
		</section>
	);
}

/**
 * Lines in `level.yaml` that configure a node: a guardrail's list item (found by its id), or the
 * top-level section of other node kinds.
 */
export function configLinesFor(
	nodeId: string,
	kind: string | null,
	files: SourceFile[],
): LineRange | null {
	const file = files.find((f) => /(^|\/)level\.ya?ml$/.test(f.path));
	if (!file) return null;
	const lines = file.content.split("\n");
	const indent = (line: string) => line.length - line.trimStart().length;

	if (nodeId.startsWith("guardrail:")) {
		const id = nodeId.slice("guardrail:".length);
		const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
		const pattern = new RegExp(`\\bid:\\s*["']?${escaped}(?=["'\\s,}]|$)`);
		const start = lines.findIndex(
			(line) => pattern.test(line) && /^\s*-\s/.test(line),
		);
		if (start < 0) return null;
		const itemIndent = indent(lines[start]);
		let end = start;
		while (
			end + 1 < lines.length &&
			lines[end + 1].trim() !== "" &&
			indent(lines[end + 1]) > itemIndent
		)
			end++;
		return { path: file.path, start: start + 1, end: end + 1 };
	}

	const section: Record<string, string> = {
		attacker: "red_team",
		injection: "attack_surface",
		user_task: "attack_surface",
		agent: "agent",
		tools: "agent",
		tool: "agent",
		success: "success",
	};
	const key = kind ? section[kind] : undefined;
	if (!key) return null;
	const start = lines.findIndex((line) => line.startsWith(`${key}:`));
	if (start < 0) return null;
	let end = start;
	while (
		end + 1 < lines.length &&
		(lines[end + 1].trim() === "" || indent(lines[end + 1]) > 0)
	)
		end++;
	while (end > start && lines[end].trim() === "") end--;
	return { path: file.path, start: start + 1, end: end + 1 };
}
