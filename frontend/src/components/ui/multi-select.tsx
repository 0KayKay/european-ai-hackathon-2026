"use client";

import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { McpServer } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	FormControl,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@/components/ui/form";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type MultiSelectProps = {
	control: any;
	name: string;
	label: string;
	options: McpServer[];
	setmcpUrls: (urls: string[]) => void;
};

export function MultiSelectMcpUrls({
	control,
	name,
	label,
	options,
	setmcpUrls,
}: MultiSelectProps) {
	const { t } = useTranslation();
	return (
		<FormField
			control={control}
			name={name}
			render={({ field }) => {
				const selected: string[] = Array.isArray(field.value)
					? field.value
					: field.value
						? [field.value]
						: [];

				const toggleValue = (value: string) => {
					let newValues: string[];

					if (selected.includes(value)) {
						newValues = selected.filter((v) => v !== value);
					} else {
						newValues = [...selected, value];
					}

					field.onChange(newValues);
					setmcpUrls(newValues);
				};

				return (
					<FormItem className="flex flex-col gap-2">
						<FormLabel className="font-bold text-md">{label}</FormLabel>

						<Popover>
							<PopoverTrigger asChild>
								<FormControl>
									<Button
										variant="outline"
										className="w-full justify-between min-h-10 h-auto"
									>
										{selected.length > 0 ? (
											<div className="flex flex-wrap gap-1">
												{selected.map((url) => {
													const option = options.find((o) => o.url === url);

													return (
														<Badge key={url} variant="secondary">
															{option?.name || url}
														</Badge>
													);
												})}
											</div>
										) : (
											<span className="text-muted-foreground">
												{t("multiSelect.selectMcpUrls")}
											</span>
										)}
									</Button>
								</FormControl>
							</PopoverTrigger>

							<PopoverContent className="w-[300px] p-0">
								<Command>
									<CommandInput placeholder={t("multiSelect.search")} />

									<CommandList>
										<CommandEmpty>{t("multiSelect.noMcpFound")}</CommandEmpty>

										<CommandGroup>
											{options.map((option) => {
												const isSelected = selected.includes(option.url);

												return (
													<CommandItem
														key={option.url}
														value={option.name}
														onSelect={() => toggleValue(option.url)}
													>
														<Check
															className={cn(
																"mr-2 h-4 w-4",
																isSelected ? "opacity-100" : "opacity-0",
															)}
														/>
														{option.name}
													</CommandItem>
												);
											})}
										</CommandGroup>
									</CommandList>
								</Command>
							</PopoverContent>
						</Popover>

						<FormMessage />
					</FormItem>
				);
			}}
		/>
	);
}
