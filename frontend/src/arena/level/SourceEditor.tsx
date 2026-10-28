import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/** Line height of the source views in px (`leading-[19px]`). */
export const LINE_HEIGHT = 19;
const PADDING_TOP = 12;

/** Offset of the first character of a 1-based line. */
function lineStart(text: string, line: number) {
	let offset = 0;
	for (let n = 1; n < line; n++) {
		const next = text.indexOf("\n", offset);
		if (next < 0) return text.length;
		offset = next + 1;
	}
	return offset;
}

/**
 * A plain-text editor with line numbers: a monospace textarea sized to its content inside a
 * scroll container, so the gutter and the text scroll together. Lines with a validation problem
 * are highlighted; `focus` jumps to a line (its `nonce` repeats a jump to the same line).
 */
export function SourceEditor({
	path,
	value,
	onChange,
	errorLines,
	focus,
}: {
	path: string;
	value: string;
	onChange: (value: string) => void;
	errorLines: number[];
	focus?: { line: number; nonce: number } | null;
}) {
	const scroller = useRef<HTMLDivElement>(null);
	const textarea = useRef<HTMLTextAreaElement>(null);
	const lines = value.split("\n");
	const longest = lines.reduce((max, line) => Math.max(max, line.length), 0);
	const marked = new Set(errorLines);

	useEffect(() => {
		if (!focus) return;
		const area = textarea.current;
		const box = scroller.current;
		if (box)
			box.scrollTop = Math.max(
				0,
				(focus.line - 1) * LINE_HEIGHT - box.clientHeight / 2,
			);
		if (area) {
			const start = lineStart(area.value, focus.line);
			const end = area.value.indexOf("\n", start);
			area.focus({ preventScroll: true });
			area.setSelectionRange(start, end < 0 ? area.value.length : end);
		}
	}, [focus]);

	return (
		<div
			ref={scroller}
			className="min-h-0 flex-1 overflow-auto bg-white"
			style={{ paddingTop: PADDING_TOP, paddingBottom: PADDING_TOP }}
		>
			<div className="flex min-w-full font-mono text-xs leading-[19px]">
				<div
					aria-hidden="true"
					className="sticky left-0 z-10 w-10 shrink-0 bg-white pr-3 text-right text-slate-400 select-none"
				>
					{lines.map((_, index) => {
						const n = index + 1;
						return (
							<div
								key={n}
								data-error-line={marked.has(n) || undefined}
								className={cn(marked.has(n) && "font-semibold text-red-600")}
							>
								{n}
							</div>
						);
					})}
				</div>
				<div className="relative min-w-0 flex-1">
					{[...marked].map((line) => (
						<div
							key={line}
							aria-hidden="true"
							className="absolute inset-x-0 border-l-2 border-red-500 bg-red-50"
							style={{ top: (line - 1) * LINE_HEIGHT, height: LINE_HEIGHT }}
						/>
					))}
					<textarea
						ref={textarea}
						aria-label={`Edit ${path}`}
						aria-invalid={marked.size > 0 || undefined}
						value={value}
						onChange={(event) => onChange(event.target.value)}
						spellCheck={false}
						autoCapitalize="off"
						autoComplete="off"
						autoCorrect="off"
						wrap="off"
						className="relative block resize-none overflow-hidden border-0 bg-transparent p-0 pr-4 pl-1 font-mono text-xs leading-[19px] whitespace-pre text-slate-800 outline-none"
						style={{
							height: lines.length * LINE_HEIGHT,
							width: `calc(${longest + 4}ch + 1.25rem)`,
							minWidth: "100%",
						}}
					/>
				</div>
			</div>
		</div>
	);
}
