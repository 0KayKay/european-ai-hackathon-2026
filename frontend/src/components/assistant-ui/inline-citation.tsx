"use client";

import { PreviewCard } from "@base-ui/react/preview-card";
import { type ComponentProps, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

// Adapted from https://www.assistant-ui.com/elements/inline-citation.
export function MarkdownLink({
	href,
	title,
	children,
	className,
	...props
}: ComponentProps<"a">) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const numbered = /^\d+$/.test(String(children));
	const url = href && URL.canParse(href) ? new URL(href) : undefined;
	if (numbered && href === "#id") {
		return (
			<span
				className="mx-0.5 rounded bg-muted px-1 text-xs text-muted-foreground"
				title={t("assistantUi.citationUnavailable")}
			>
				{children}
				<span className="sr-only">{t("assistantUi.citationUnavailable")}</span>
			</span>
		);
	}
	if (!numbered || !url || !["https:", "http:"].includes(url.protocol)) {
		return (
			<a
				href={href}
				title={title}
				className={cn(
					"aui-md-a text-primary hover:text-primary/80 underline underline-offset-2",
					className,
				)}
				{...props}
			>
				{children}
			</a>
		);
	}
	return (
		<PreviewCard.Root open={open} onOpenChange={setOpen}>
			<PreviewCard.Trigger
				delay={0}
				render={<a href={url.href} target="_blank" rel="noopener noreferrer" />}
				aria-label={t("assistantUi.citation", {
					number: String(children),
					title: title || url.hostname,
				})}
				className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded px-1 align-middle font-mono text-[10px] font-medium tabular-nums bg-muted text-muted-foreground hover:text-foreground focus-visible:outline-ring"
			>
				{children}
			</PreviewCard.Trigger>
			<PreviewCard.Portal>
				<PreviewCard.Positioner side="top" sideOffset={8}>
					<PreviewCard.Popup className="z-50 w-64 rounded-xl border bg-popover p-3.5 text-popover-foreground shadow-md outline-none">
						<span className="text-xs text-muted-foreground">
							{url.hostname}
						</span>
						<p className="mt-2 text-sm font-medium">{title || url.hostname}</p>
						<p className="mt-1 break-all text-xs text-muted-foreground">
							{url.href}
						</p>
					</PreviewCard.Popup>
				</PreviewCard.Positioner>
			</PreviewCard.Portal>
		</PreviewCard.Root>
	);
}
