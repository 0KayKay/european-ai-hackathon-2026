import {
	PauseIcon,
	PlayIcon,
	SkipBackIcon,
	SkipForwardIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatEventTime } from "../format";
import type { CaseEvent } from "../types";
import { guardrailData } from "./events";

export const SPEEDS = [1, 2, 4] as const;
export type Speed = (typeof SPEEDS)[number];

export interface Playback {
	index: number;
	playing: boolean;
	speed: Speed;
	seek: (index: number) => void;
	step: (delta: number) => void;
	toggle: () => void;
	setSpeed: (speed: Speed) => void;
}

/**
 * Steps through recorded events. Playing advances with the recorded timing (`t` deltas) divided by
 * the speed and stops at the last event; stepping pauses.
 */
export function usePlayback(events: CaseEvent[]): Playback {
	const [index, setIndex] = useState(0);
	const [playing, setPlaying] = useState(false);
	const [speed, setSpeed] = useState<Speed>(1);
	const last = events.length - 1;
	const clamp = useCallback(
		(i: number) => Math.max(0, Math.min(i, Math.max(last, 0))),
		[last],
	);

	useEffect(() => {
		if (!playing) return;
		if (index >= last) {
			setPlaying(false);
			return;
		}
		const gap = Math.max(0, events[index + 1].t - events[index].t);
		const timer = setTimeout(
			() => setIndex((i) => Math.min(i + 1, last)),
			(gap * 1000) / speed,
		);
		return () => clearTimeout(timer);
	}, [playing, index, speed, events, last]);

	const seek = useCallback((i: number) => setIndex(clamp(i)), [clamp]);
	const step = useCallback(
		(delta: number) => {
			setPlaying(false);
			setIndex((i) => clamp(i + delta));
		},
		[clamp],
	);
	const toggle = useCallback(() => {
		if (last < 1) return;
		if (!playing && index >= last) setIndex(0);
		setPlaying(!playing);
	}, [last, playing, index]);

	return { index: clamp(index), playing, speed, seek, step, toggle, setSpeed };
}

function markerTone(event: CaseEvent) {
	if (event.kind === "guardrail")
		return guardrailData(event).status === "blocked"
			? "bg-amber-500"
			: "bg-emerald-500";
	if (event.kind === "success_check")
		return event.data.met ? "bg-red-500" : "bg-violet-500";
	if (event.kind === "attacker_message" || event.kind === "injection")
		return "bg-red-400";
	if (event.kind === "tool_call" || event.kind === "tool_result")
		return "bg-blue-500";
	if (event.kind === "error" || event.kind === "limit") return "bg-red-600";
	return "bg-slate-400";
}

export function PlaybackBar({
	events,
	playback,
}: {
	events: CaseEvent[];
	playback: Playback;
}) {
	const { index, playing, speed } = playback;
	const total = events.length ? events[events.length - 1].t : 0;
	const now = events[index]?.t ?? 0;
	const empty = events.length === 0;
	const position = (t: number) => (total > 0 ? (t / total) * 100 : 0);

	const scrub = (value: number) => {
		let found = 0;
		events.forEach((event, i) => {
			if (event.t <= value + 1e-9) found = i;
		});
		playback.seek(found);
	};

	return (
		<section
			aria-label="Playback"
			className="flex shrink-0 flex-wrap items-center gap-3 border-t bg-white px-4 py-3"
		>
			<div className="flex items-center gap-1.5">
				<Button
					type="button"
					variant="outline"
					size="icon-sm"
					aria-label="Step back"
					disabled={empty || index === 0}
					onClick={() => playback.step(-1)}
				>
					<SkipBackIcon />
				</Button>
				<Button
					type="button"
					size="icon"
					className="rounded-full"
					aria-label={playing ? "Pause" : "Play"}
					disabled={events.length < 2}
					onClick={playback.toggle}
				>
					{playing ? <PauseIcon /> : <PlayIcon />}
				</Button>
				<Button
					type="button"
					variant="outline"
					size="icon-sm"
					aria-label="Step forward"
					disabled={empty || index >= events.length - 1}
					onClick={() => playback.step(1)}
				>
					<SkipForwardIcon />
				</Button>
			</div>

			<div className="relative flex h-6 min-w-40 flex-1 items-center">
				<input
					type="range"
					aria-label="Replay position"
					aria-valuetext={`${formatEventTime(now)} of ${formatEventTime(total)}, event ${index + 1} of ${events.length}`}
					min={0}
					max={total || 1}
					step={0.01}
					value={now}
					disabled={empty}
					onChange={(e) => scrub(Number(e.target.value))}
					className="w-full accent-blue-600"
				/>
				<div className="pointer-events-none absolute inset-x-[7px] top-0 h-1.5">
					{events.map((event, i) => (
						<button
							key={event.seq}
							type="button"
							tabIndex={-1}
							aria-hidden="true"
							data-marker={event.seq}
							onClick={() => playback.seek(i)}
							style={{ left: `${position(event.t)}%` }}
							className={cn(
								"pointer-events-auto absolute top-0 size-1.5 -translate-x-1/2 rounded-full",
								markerTone(event),
								i === index && "ring-2 ring-blue-500 ring-offset-1",
							)}
						/>
					))}
				</div>
			</div>

			<span
				className="font-mono text-xs text-slate-600 tabular-nums"
				data-testid="replay-time"
			>
				{formatEventTime(now)} / {formatEventTime(total)}
			</span>

			<fieldset
				aria-label="Speed"
				className="m-0 flex min-w-0 items-center gap-0.5 rounded-md border bg-slate-50 p-0.5"
			>
				{SPEEDS.map((s) => (
					<button
						key={s}
						type="button"
						aria-pressed={speed === s}
						onClick={() => playback.setSpeed(s)}
						className={cn(
							"rounded px-1.5 py-0.5 font-mono text-[11px] text-slate-500",
							speed === s && "bg-white text-slate-900 shadow-sm",
						)}
					>
						{s}×
					</button>
				))}
			</fieldset>
		</section>
	);
}
