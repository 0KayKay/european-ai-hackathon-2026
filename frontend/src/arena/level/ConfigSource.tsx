import { FileCodeIcon, FileTextIcon } from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { SourceFile } from "../types";

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

export function ConfigSource({
	files,
	activePath,
	onSelectPath,
	highlight,
}: {
	files: SourceFile[];
	activePath: string;
	onSelectPath: (path: string) => void;
	highlight?: LineRange | null;
}) {
	const file = files.find((f) => f.path === activePath) ?? files[0];
	const lines = (file?.content ?? "").replace(/\n$/, "").split("\n");
	const marked =
		highlight && file && highlight.path === file.path ? highlight : null;
	const firstMarked = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (marked) firstMarked.current?.scrollIntoView?.({ block: "center" });
	}, [marked]);

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
					{files.map((f) => {
						const selected = f.path === file?.path;
						const Icon = isYaml(f.path) ? FileCodeIcon : FileTextIcon;
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
								)}
							>
								<Icon className="size-3.5" aria-hidden="true" />
								{f.path}
							</button>
						);
					})}
				</div>
				<span className="shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] text-slate-500">
					read-only
				</span>
			</div>
			<div
				role="tabpanel"
				aria-label={file?.path ?? "No file"}
				className="min-h-0 flex-1 overflow-auto py-3"
			>
				{file ? (
					<pre className="min-w-max font-mono text-xs leading-[19px] text-slate-800">
						{lines.map((line, index) => {
							const n = index + 1;
							const on = marked && n >= marked.start && n <= marked.end;
							return (
								<div
									key={n}
									ref={on && n === marked?.start ? firstMarked : undefined}
									data-marked={on || undefined}
									className={cn(
										"flex border-l-2 border-transparent pr-4",
										on && "border-blue-500 bg-blue-50",
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
