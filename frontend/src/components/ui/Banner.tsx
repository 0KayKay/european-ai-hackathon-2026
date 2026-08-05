import { X } from "lucide-react";
import { marked } from "marked";
import { type FC, useEffect, useState } from "react";

interface BannerProps {
	content: string;
	onClose?: () => void;
}

export const Banner: FC<BannerProps> = ({ content, onClose }) => {
	const [htmlContent, setHtmlContent] = useState("");

	useEffect(() => {
		// Configure marked to open links in a new tab
		const renderer = new marked.Renderer();
		const originalLink = renderer.link.bind(renderer);
		renderer.link = (token) => {
			const html = originalLink(token);
			return html.replace(
				/^<a /,
				'<a target="_blank" rel="noopener noreferrer" ',
			);
		};

		marked.setOptions({ renderer });

		// Parse markdown content
		const parsed = marked.parse(content, { async: false }) as string;
		setHtmlContent(parsed);
	}, [content]);

	if (!content) return null;

	return (
		<div
			className="rounded-lg border px-4 py-2 text-sm gap-x-3 items-center border-primary bg-primary text-primary-foreground justify-between flex shadow-sm transition-all duration-300 ease-in-out"
			role="alert"
		>
			<div
				className="font-medium tracking-tight break-words flex-1"
				// biome-ignore lint/security/noDangerouslySetInnerHtml: Markdown content needs to be rendered as HTML
				dangerouslySetInnerHTML={{ __html: htmlContent }}
			/>
			{onClose && (
				<button
					type="button"
					onClick={onClose}
					className="ml-3 shrink-0 hover:opacity-80 transition-opacity cursor-pointer"
					aria-label="Close banner"
				>
					<X size={18} />
				</button>
			)}
		</div>
	);
};
