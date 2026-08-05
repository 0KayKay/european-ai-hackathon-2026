import { useAgUiSetState, useAgUiState } from "@assistant-ui/react-ag-ui";
import { SettingsIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Input } from "@/components/ui/input";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";

export type Difficulty = "EASY" | "MEDIUM" | "HARD";
export type ChatMode = "PLAYGROUND" | "CHALLENGE_1";
export type ChatState = {
	settings: { model_id: string; mode: ChatMode; difficulty: Difficulty };
};
export const defaultModelId = "gemini-3.8-flash";

export function AgUiSettings() {
	const { t } = useTranslation();
	const state = useAgUiState<ChatState>();
	const setState = useAgUiSetState<ChatState>();

	return (
		<Popover>
			<PopoverTrigger asChild>
				<TooltipIconButton
					type="button"
					tooltip={t("assistantUi.modelSettings")}
					aria-label={t("assistantUi.modelSettings")}
					className="size-8 rounded-full text-muted-foreground hover:text-foreground"
				>
					<SettingsIcon className="size-5" />
				</TooltipIconButton>
			</PopoverTrigger>
			<PopoverContent align="start" side="top" className="w-72">
				<label htmlFor="agui-model" className="mb-2 block text-sm font-medium">
					{t("assistantUi.model")}
				</label>
				<Input
					id="agui-model"
					value={state?.settings?.model_id ?? defaultModelId}
					onChange={(event) =>
						setState({
							settings: {
								model_id: event.target.value,
								mode: state?.settings?.mode ?? "CHALLENGE_1",
								difficulty: state?.settings?.difficulty ?? "EASY",
							},
						})
					}
				/>
			</PopoverContent>
		</Popover>
	);
}
