import {
	BotIcon,
	BoxIcon,
	ChevronDownIcon,
	CircleCheckIcon,
	CrosshairIcon,
	DatabaseIcon,
	LayersIcon,
	type LucideIcon,
	MailWarningIcon,
	MessageSquareQuoteIcon,
	ReplyIcon,
	ShieldCheckIcon,
	WrenchIcon,
} from "lucide-react";
import { useId, useState } from "react";
import { cn } from "@/lib/utils";
import { formatPosition, groupPosition, nodeKind } from "../graph/layout";
import type {
	ArenaGraph,
	GraphNode,
	ModelSpec,
	NodeKind,
	NodePrompt,
	RoleModel,
} from "../types";

const KIND_INFO: Record<
	NodeKind,
	{ label: string; icon: LucideIcon; tone: string }
> = {
	attacker: {
		label: "Red team",
		icon: CrosshairIcon,
		tone: "bg-red-50 text-red-600",
	},
	injection: {
		label: "Attack surface · indirect",
		icon: MailWarningIcon,
		tone: "bg-amber-100 text-amber-700",
	},
	user_task: {
		label: "Fixed user task",
		icon: MessageSquareQuoteIcon,
		tone: "bg-slate-100 text-slate-600",
	},
	guardrails: {
		label: "Guardrail position",
		icon: LayersIcon,
		tone: "bg-emerald-50 text-emerald-700",
	},
	guardrail: {
		label: "Guardrail",
		icon: ShieldCheckIcon,
		tone: "bg-emerald-50 text-emerald-700",
	},
	agent: { label: "Agent", icon: BotIcon, tone: "bg-blue-50 text-blue-600" },
	tools: {
		label: "Tools",
		icon: WrenchIcon,
		tone: "bg-slate-100 text-slate-600",
	},
	tool: {
		label: "Tool",
		icon: WrenchIcon,
		tone: "bg-slate-100 text-slate-600",
	},
	data: {
		label: "Data",
		icon: DatabaseIcon,
		tone: "bg-amber-100 text-amber-700",
	},
	success: {
		label: "Success check",
		icon: CircleCheckIcon,
		tone: "bg-violet-100 text-violet-600",
	},
	output: {
		label: "Output",
		icon: ReplyIcon,
		tone: "bg-slate-100 text-slate-600",
	},
};
const UNKNOWN_INFO = {
	label: "Node",
	icon: BoxIcon,
	tone: "bg-slate-100 text-slate-600",
};

const formatValue = (value: unknown) =>
	typeof value === "string" ? value : JSON.stringify(value);

function SettingsTable({ rows }: { rows: { label: string; value: string }[] }) {
	if (!rows.length) return null;
	return (
		<dl className="divide-y overflow-hidden rounded-lg border bg-white text-[13px]">
			{rows.map((row, i) => (
				<div
					// biome-ignore lint/suspicious/noArrayIndexKey: labels may repeat.
					key={`${row.label}-${i}`}
					className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3 px-3 py-2"
				>
					<dt className="text-slate-500">{row.label}</dt>
					<dd className="text-right font-medium break-words text-slate-800">
						{row.value}
					</dd>
				</div>
			))}
		</dl>
	);
}

const roleLabel = (role: string) =>
	role
		.replace(/^red_team\./, "Red team · ")
		.replace(/^green_team\./, "Green team · ")
		.replace(/^guardrail:/, "Guardrail · ")
		.replace(/^agent$/, "Target agent");

function ModelCard({ role, spec }: { role?: string; spec: ModelSpec }) {
	const params = Object.entries(spec.params ?? {}).filter(
		([, value]) => value !== undefined && value !== null,
	);
	return (
		<div className="overflow-hidden rounded-lg border bg-white text-[13px]">
			{role && (
				<p className="border-b bg-slate-50 px-3 py-1.5 text-[11px] font-medium tracking-wide text-slate-500 uppercase">
					{roleLabel(role)}
				</p>
			)}
			<dl className="divide-y">
				<div className="flex justify-between gap-3 px-3 py-2">
					<dt className="text-slate-500">Model</dt>
					<dd className="font-mono text-xs font-medium text-slate-800">
						{spec.model}
					</dd>
				</div>
				{spec.endpoint && (
					<div className="flex justify-between gap-3 px-3 py-2">
						<dt className="text-slate-500">Endpoint</dt>
						<dd className="font-mono text-xs text-slate-700">
							{spec.endpoint}
						</dd>
					</div>
				)}
				{params.map(([key, value]) => (
					<div key={key} className="flex justify-between gap-3 px-3 py-2">
						<dt className="font-mono text-xs text-slate-500">{key}</dt>
						<dd className="font-mono text-xs text-slate-700">
							{formatValue(value)}
						</dd>
					</div>
				))}
			</dl>
		</div>
	);
}

function PromptBlock({
	prompt,
	onOpenFile,
}: {
	prompt: NodePrompt;
	onOpenFile?: (path: string) => void;
}) {
	const [open, setOpen] = useState(false);
	const id = useId();
	const long = prompt.text.split("\n").length > 4 || prompt.text.length > 240;
	return (
		<div className="flex flex-col gap-1.5">
			<div className="flex items-baseline justify-between gap-2">
				<p className="text-[13px] font-semibold text-slate-800">
					{prompt.name}
				</p>
				{prompt.file &&
					(onOpenFile ? (
						<button
							type="button"
							onClick={() => onOpenFile(prompt.file!)}
							className="truncate font-mono text-[11px] text-slate-500 hover:text-blue-600 hover:underline"
							title={`Open ${prompt.file} in the config source`}
						>
							{prompt.file}
						</button>
					) : (
						<span className="truncate font-mono text-[11px] text-slate-500">
							{prompt.file}
						</span>
					))}
			</div>
			<pre
				id={id}
				className={cn(
					"relative overflow-hidden rounded-lg border bg-slate-50 px-3 py-2 font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap text-slate-700",
					!open && long && "max-h-[5.6rem]",
				)}
			>
				{prompt.text}
				{!open && long && (
					<span className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-slate-50" />
				)}
			</pre>
			{long && (
				<button
					type="button"
					aria-expanded={open}
					aria-controls={id}
					onClick={() => setOpen((v) => !v)}
					className="flex items-center gap-1 self-start text-[12.5px] font-medium text-blue-600 hover:underline"
				>
					{open ? "Hide full prompt" : "Show full prompt"}
					<ChevronDownIcon
						className={cn(
							"size-3.5 transition-transform",
							open && "rotate-180",
						)}
						aria-hidden="true"
					/>
				</button>
			)}
		</div>
	);
}

function Section({
	title,
	children,
}: {
	title: string;
	children: React.ReactNode;
}) {
	return (
		<section className="flex flex-col gap-2">
			<h3 className="text-[13px] font-semibold text-slate-800">{title}</h3>
			{children}
		</section>
	);
}

function NodeLinks({
	nodes,
	onSelect,
}: {
	nodes: GraphNode[];
	onSelect?: (id: string) => void;
}) {
	return (
		<ul className="flex flex-col gap-1">
			{nodes.map((node) => (
				<li key={node.id}>
					<button
						type="button"
						onClick={() => onSelect?.(node.id)}
						className="flex w-full items-start gap-2 rounded-md px-1 py-1 text-left text-[13px] hover:bg-slate-100"
					>
						<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-emerald-500" />
						<span className="text-slate-700">
							<span className="font-medium text-slate-900">{node.label}</span>
							{node.subtitle && <span> — {node.subtitle}</span>}
						</span>
					</button>
				</li>
			))}
		</ul>
	);
}

export function NodeInspector({
	node,
	graph,
	models,
	onSelect,
	onOpenFile,
}: {
	node: GraphNode | null;
	graph: ArenaGraph;
	models: RoleModel[];
	onSelect?: (id: string) => void;
	onOpenFile?: (path: string) => void;
}) {
	if (!node)
		return (
			<p className="p-4 text-sm text-slate-500">
				Select a node in the graph to see its settings and prompts.
			</p>
		);

	const kind = nodeKind(node);
	const info = kind ? KIND_INFO[kind] : UNKNOWN_INFO;
	const Icon = info.icon;
	const kindText =
		kind === "guardrails"
			? `Guardrail position · ${formatPosition(groupPosition(node.id) ?? "")}`
			: [info.label, node.subtitle].filter(Boolean).join(" · ");

	const roleModels = models.filter((m) => m.node === node.id);
	const ownModel = node.details?.model;
	const modelCards: { role?: string; spec: ModelSpec }[] = roleModels.length
		? roleModels.map((m) => ({ role: m.role, spec: m }))
		: ownModel
			? [{ spec: ownModel }]
			: [];

	const group = node.group
		? graph.nodes.find((n) => n.id === node.group)
		: undefined;
	const siblings = node.group
		? graph.nodes.filter((n) => n.group === node.group && n.id !== node.id)
		: [];
	const members = graph.nodes.filter((n) => n.group === node.id);
	const isGuardrailGroup = node.group?.startsWith("guardrails:");

	return (
		<div className="flex flex-col gap-5 p-4">
			<div className="flex items-start gap-3">
				<span
					className={cn(
						"grid size-9 shrink-0 place-items-center rounded-lg",
						info.tone,
					)}
				>
					<Icon className="size-4.5" aria-hidden="true" />
				</span>
				<div className="min-w-0">
					<h2 className="text-base leading-tight font-semibold text-slate-900">
						{kind === "user_task" ? `“${node.label}”` : node.label}
					</h2>
					<p className="mt-0.5 text-[13px] text-slate-500">{kindText}</p>
				</div>
			</div>

			<SettingsTable rows={node.details?.settings ?? []} />

			{modelCards.length > 0 && (
				<Section title={modelCards.length > 1 ? "Models" : "Model"}>
					{modelCards.map((card) => (
						<ModelCard
							key={card.role ?? "model"}
							role={card.role}
							spec={card.spec}
						/>
					))}
				</Section>
			)}

			{(node.details?.prompts ?? []).map((prompt) => (
				<PromptBlock
					key={`${prompt.name}-${prompt.file ?? ""}`}
					prompt={prompt}
					onOpenFile={onOpenFile}
				/>
			))}

			{members.length > 0 && (
				<Section
					title={kind === "tools" ? "Tools" : "Guardrails at this position"}
				>
					<NodeLinks nodes={members} onSelect={onSelect} />
				</Section>
			)}

			{group && (
				<Section
					title={
						isGuardrailGroup
							? `Same position · ${formatPosition(groupPosition(group.id) ?? "")}`
							: "Same group"
					}
				>
					{siblings.length ? (
						<NodeLinks nodes={siblings} onSelect={onSelect} />
					) : (
						<p className="text-[13px] text-slate-500">
							No other {isGuardrailGroup ? "guardrails" : "members"} here.
						</p>
					)}
				</Section>
			)}
		</div>
	);
}
