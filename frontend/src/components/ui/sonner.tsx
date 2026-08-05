"use client";

import type { ToasterProps } from "sonner";
import { Toaster as Sonner } from "sonner";
import { useTheme } from "@/layout/theme-provider";

const Toaster = ({ ...props }: ToasterProps) => {
	const { variant } = useTheme();

	return (
		<Sonner
			theme={variant as ToasterProps["theme"]}
			className="toaster group"
			style={
				{
					"--normal-bg": "var(--popover)",
					"--normal-text": "var(--popover-foreground)",
					"--normal-border": "var(--border)",
				} as React.CSSProperties
			}
			{...props}
		/>
	);
};

export { Toaster };
