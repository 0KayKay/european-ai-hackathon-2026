import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { formatEventTime } from "../format";
import type { ArenaGraph, CaseEvent, Team } from "../types";
import { type EventTone, eventHeading, guardrailData } from "./events";

const TONE_TEXT: Record<EventTone, string> = {
	attacker: "text-red-600",
	user: "text-sky-700",
	"guardrail-ok": "text-emerald-700",
	"guardrail-block": "text-amber-700",
	tool: "text-blue-600",
	agent: "text-blue-700",
	success: "text-emerald-700",
	fail: "text-red-600",
	muted: "text-slate-500",
};

const LONG_TEXT = 320;
const LONG_LINES = 6;

/** Recorded value as text: strings as they are, everything else as indented JSON. */
export const asText = (value: unknown) =>
	typeof value === "string" ? value : JSON.stringify(value, null, 2);

function TextBox({
	children,
	mono,
	tone = "plain",
}: {
	children: ReactNode;
	mono?: boolean;
	tone?: "plain" | "attacker" | "agent";
}) {
	return (
		<div
			className={cn(
				"rounded-lg border px-3 py-2 text-[13px] leading-relaxed break-words whitespace-pre-wrap",
				mono && "font-mono text-xs",
				tone === "attacker" && "border-red-100 bg-red-50/50",
				tone === "agent" && "border-blue-100 bg-blue-50/50",
				tone === "plain" && "border-slate-200 bg-slate-50",
			)}
		>
			{children}
		</div>
	);
}

/** Long text (tool results) shows its first lines with a toggle for the rest. */
export function CollapsibleText({ text }: { text: string }) {
	const [open, setOpen] = useState(false);
	const lines = text.split("\n");
	const long = text.length > LONG_TEXT || lines.length > LONG_LINES;
	const shown =
		!long || open
			? text
			: `${lines.slice(0, LONG_LINES).join("\n").slice(0, LONG_TEXT)}…`;
	return (
		<div className="flex flex-col items-start gap-1">
			<TextBox mono>{shown}</TextBox>
			{long && (
				<button
					type="button"
					aria-expanded={open}
					onClick={(event) => {
						event.stopPropagation();
						setOpen((o) => !o);
					}}
					className="text-xs font-medium text-blue-600 hover:underline"
				>
					{open ? "Show less" : `Show full result (${text.length} chars)`}
				</button>
			)}
		</div>
	);
}

function Fields({ rows }: { rows: [string, ReactNode][] }) {
	if (!rows.length) return null;
	return (
		<dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[13px]">
			{rows.map(([label, value]) => (
				<div key={label} className="contents">
					<dt className="text-slate-500">{label}</dt>
					<dd className="break-words whitespace-pre-wrap text-slate-800">
						{value}
					</dd>
				</div>
			))}
		</dl>
	);
}

/** Data fields not rendered specially, shown as recorded. */
function rest(data: Record<string, unknown>, known: string[]) {
	return Object.entries(data)
		.filter(
			([key, value]) =>
				!known.includes(key) && value !== null && value !== undefined,
		)
		.map(([key, value]): [string, ReactNode] => [key, asText(value)]);
}

function EventBody({ event }: { event: CaseEvent }) {
	const data = event.data;
	switch (event.kind) {
		case "attacker_message":
			return (
				<>
					{data.text !== undefined && (
						<TextBox tone="attacker">{asText(data.text)}</TextBox>
					)}
					<Fields rows={rest(data, ["text", "team"])} />
				</>
			);
		case "injection":
			return (
				<div className="flex flex-col gap-2">
					{Object.entries(data).map(([field, value]) => (
						<div key={field} className="flex flex-col gap-1">
							<span className="text-xs text-slate-500">{field}</span>
							<TextBox tone="attacker">{asText(value)}</TextBox>
						</div>
					))}
				</div>
			);
		case "user_task":
			return (
				<>
					{data.text !== undefined && <TextBox>{asText(data.text)}</TextBox>}
					<Fields rows={rest(data, ["text"])} />
				</>
			);
		case "guardrail": {
			const g = guardrailData(event);
			const rows: [string, ReactNode][] = [];
			if (g.rule !== null && g.rule !== undefined) rows.push(["rule", g.rule]);
			if (g.reason !== null && g.reason !== undefined)
				rows.push(["reason", g.reason]);
			if (g.changed !== null && g.changed !== undefined)
				rows.push(["changed", g.changed ? "yes" : "no character changes"]);
			if (g.position) rows.push(["position", g.position]);
			if (g.model) rows.push(["model", g.model]);
			rows.push(
				...rest(data, [
					"guardrail_id",
					"guardrail_kind",
					"position",
					"status",
					"rule",
					"reason",
					"changed",
					"model",
				]),
			);
			return <Fields rows={rows} />;
		}
		case "model_call":
			return <Fields rows={rest(data, ["model"])} />;
		case "tool_call": {
			const args = data.args ?? {};
			const inline = JSON.stringify(args);
			return (
				<>
					<TextBox mono>
						{inline.length <= 80
							? `${asText(data.tool)}(${inline === "{}" ? "" : inline})`
							: `${asText(data.tool)}(${JSON.stringify(args, null, 2)})`}
					</TextBox>
					<Fields rows={rest(data, ["tool", "args"])} />
				</>
			);
		}
		case "tool_result":
			return (
				<div className="flex flex-col gap-1">
					<span className="font-mono text-xs text-slate-500">
						{asText(data.tool)}
					</span>
					{data.result !== undefined && (
						<CollapsibleText text={asText(data.result)} />
					)}
					<Fields rows={rest(data, ["tool", "result"])} />
				</div>
			);
		case "agent_reply":
			return (
				<>
					{data.text !== undefined && (
						<TextBox tone="agent">{asText(data.text)}</TextBox>
					)}
					<Fields rows={rest(data, ["text"])} />
				</>
			);
		case "success_check": {
			const detail =
				data.detail && typeof data.detail === "object"
					? (data.detail as Record<string, unknown>)
					: {};
			return (
				<Fields
					rows={[
						...(data.kind !== undefined
							? [["check", asText(data.kind)] as [string, ReactNode]]
							: []),
						...rest(detail, []),
						...rest(data, ["met", "kind", "detail"]),
					]}
				/>
			);
		}
		default:
			return <Fields rows={rest(data, [])} />;
	}
}

/** One line of recorded content for compact lists (live sample). */
export function eventSummary(event: CaseEvent): string {
	const data = event.data;
	switch (event.kind) {
		case "attacker_message":
			return asText(data.text ?? "");
		case "injection":
			return Object.values(data).map(asText).join(" · ");
		case "user_task":
		case "agent_reply":
			return asText(data.text ?? "");
		case "guardrail": {
			const g = guardrailData(event);
			if (g.reason) return g.rule ? `${g.rule} · ${g.reason}` : g.reason;
			if (g.changed === false) return "no character changes";
			return g.changed ? "text changed" : "";
		}
		case "tool_call":
			return `${asText(data.tool)}(${JSON.stringify(data.args ?? {})})`;
		case "tool_result":
			return asText(data.result ?? "");
		case "model_call":
			return data.n !== undefined ? `request ${asText(data.n)}` : "";
		case "success_check": {
			const detail =
				data.detail && typeof data.detail === "object"
					? Object.entries(data.detail).map(([k, v]) => `${k} ${asText(v)}`)
					: [];
			return [asText(data.kind ?? ""), ...detail].filter(Boolean).join(" · ");
		}
		default:
			return asText(data.message ?? data);
	}
}

export function EventEntry({
	event,
	team,
	graph,
	current,
	onSelect,
	compact,
}: {
	event: CaseEvent;
	team: Team;
	graph?: ArenaGraph;
	current?: boolean;
	onSelect?: () => void;
	/** One summary line instead of the full recorded content. */
	compact?: boolean;
}) {
	const heading = eventHeading(event, team, graph);
	const header = (
		<>
			<span className="font-mono text-xs text-slate-500 tabular-nums">
				{formatEventTime(event.t)}
			</span>
			<span
				className={cn("text-[13px] font-semibold", TONE_TEXT[heading.tone])}
			>
				{heading.title}
			</span>
		</>
	);
	return (
		// biome-ignore lint/a11y/useKeyWithClickEvents: the header button is the keyboard target.
		<li
			data-seq={event.seq}
			data-kind={event.kind}
			aria-current={current ? "step" : undefined}
			onClick={onSelect}
			className={cn(
				"grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3 border-b border-slate-100 px-4 transition-colors",
				compact ? "gap-y-0.5 py-2" : "gap-y-1.5 py-3",
				onSelect && "cursor-pointer hover:bg-slate-50",
				current &&
					"bg-blue-50/70 shadow-[inset_3px_0_0_#3b82f6] hover:bg-blue-50/70",
				heading.tone === "fail" && !current && "bg-red-50/40",
			)}
		>
			{onSelect ? (
				<button
					type="button"
					onClick={(e) => {
						e.stopPropagation();
						onSelect();
					}}
					aria-label={`${formatEventTime(event.t)} ${heading.title}`}
					className="col-span-2 grid grid-cols-subgrid items-baseline text-left focus-visible:outline-2 focus-visible:outline-blue-500"
				>
					{header}
				</button>
			) : (
				<div className="col-span-2 grid grid-cols-subgrid items-baseline">
					{header}
				</div>
			)}
			{compact ? (
				<p
					className="col-start-2 truncate text-xs text-slate-600"
					title={eventSummary(event)}
				>
					{eventSummary(event)}
				</p>
			) : (
				<div className="col-start-2 flex min-w-0 flex-col gap-1.5">
					<EventBody event={event} />
				</div>
			)}
		</li>
	);
}

/** Recorded case events with timestamps; the current one is highlighted and kept in view. */
export function EventLog({
	events,
	team,
	graph,
	current,
	onSelect,
	label = "Event log",
	compact,
}: {
	events: CaseEvent[];
	team: Team;
	graph?: ArenaGraph;
	current?: number;
	onSelect?: (index: number) => void;
	label?: string;
	compact?: boolean;
}) {
	const list = useRef<HTMLOListElement>(null);
	const multiTurn = events.some((e) => e.turn > 1);

	useEffect(() => {
		if (current === undefined) return;
		const item = list.current?.querySelector<HTMLElement>(
			'[aria-current="step"]',
		);
		item?.scrollIntoView?.({ block: "nearest" });
	}, [current]);

	return (
		<ol ref={list} aria-label={label} className="flex flex-col">
			{events.map((event, index) => (
				<EventGroup
					key={event.seq}
					turn={
						multiTurn && event.turn !== events[index - 1]?.turn
							? event.turn
							: null
					}
				>
					<EventEntry
						event={event}
						team={team}
						graph={graph}
						current={index === current}
						onSelect={onSelect ? () => onSelect(index) : undefined}
						compact={compact}
					/>
				</EventGroup>
			))}
		</ol>
	);
}

function EventGroup({
	turn,
	children,
}: {
	turn: number | null;
	children: ReactNode;
}) {
	return (
		<>
			{turn !== null && (
				<li
					data-turn={turn}
					className="border-b border-slate-100 bg-slate-50 px-4 py-1 text-[11px] font-semibold tracking-[0.08em] text-slate-500 uppercase"
				>
					Turn {turn}
				</li>
			)}
			{children}
		</>
	);
}
