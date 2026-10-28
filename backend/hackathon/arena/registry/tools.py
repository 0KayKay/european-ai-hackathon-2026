"""Tool kinds: simulated tools over configured level data.

A tool kind contributes one or more tools (names and descriptions reproduce the legacy agents). `build_toolset()`
binds a kind to its data and one case's `ToolState` and returns plain typed functions; the engine wraps them as
pydantic-ai tools, e.g. `Tool(tool.function, name=tool.name, description=tool.description,
sequential=tool.sequential)`, which yields the same tool definitions as the legacy agents.
"""

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from hackathon.arena.config.templates import render_data

# Runtime placeholders available in tool data files.
TOOL_DATA_RUNTIME = frozenset({"secret", "user_email"})


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolState:
    """Per-case state shared by the tools of one level.

    `variables` holds the runtime values of `TOOL_DATA_RUNTIME` placeholders (`secret`, `user_email`).
    `sent_emails` is the simulated outbox, read by the `secret_in_sent_email` success check.
    """

    variables: dict[str, str] = field(default_factory=dict)
    sent_emails: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    sequential: bool = False


@dataclass(frozen=True)
class BoundTool:
    name: str
    description: str
    function: Callable[..., Any]
    sequential: bool
    toolset: str
    kind: str

    def parameters(self) -> list[str]:
        return list(inspect.signature(self.function).parameters)

    def call_arguments(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Arguments of a call with signature defaults filled in (input for `args.*` placeholders)."""
        values = {
            name: parameter.default
            for name, parameter in inspect.signature(self.function).parameters.items()
            if parameter.default is not inspect.Parameter.empty
        }
        return values | dict(args)


Factory = Callable[
    [Any, Any, ToolState, dict[str, str] | None], dict[str, Callable[..., Any]]
]


@dataclass(frozen=True)
class ToolKind:
    name: str
    title: str
    tools: tuple[ToolSpec, ...]
    factory: Factory
    data_label: str
    data_model: type[BaseModel] | None = None
    params_model: type[BaseModel] = _Strict
    data_access: Literal["read", "write"] = "read"
    injection_tool: str | None = None
    summarize: Callable[[Any], str] = lambda data: ""
    check: Callable[[Any, list[str] | None], list[str]] = lambda data, fields: []

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]


# --- shared drive ------------------------------------------------------------------------------------------------


class DriveFile(_Strict):
    file_name: str
    content: str


class DriveFolder(_Strict):
    folder_name: str
    files: list[DriveFile]


class DriveData(_Strict):
    folders: list[DriveFolder] = Field(min_length=1)


def _shared_drive(data, params, state, injection):
    folders = data["folders"]

    def list_folders() -> list[dict[str, str]]:
        return [{"folder_name": folder["folder_name"]} for folder in folders]

    def list_files_of_folder(folder_name: str) -> list[dict]:
        return [
            {
                "folder_name": folder_name,
                "files": [file["file_name"] for file in folder["files"]],
            }
            for folder in folders
            if folder["folder_name"] == folder_name
        ]

    def get_file_content(folder_name: str, file_name: str) -> str:
        for folder in folders:
            if folder["folder_name"] == folder_name:
                for file in folder["files"]:
                    if file["file_name"] == file_name:
                        return file["content"]
        return f"File '{file_name}' not found in folder '{folder_name}'."

    return {
        "list_folders": list_folders,
        "list_files_of_folder": list_files_of_folder,
        "get_file_content": get_file_content,
    }


def _drive_summary(data) -> str:
    folders = data["folders"]
    files = sum(len(folder["files"]) for folder in folders)
    return f"{len(folders)} folders · {files} files"


# --- inbox ---------------------------------------------------------------------------------------------------------


class InboxEmail(_Loose):
    injection: bool = False


class InboxData(_Strict):
    emails: list[InboxEmail] = Field(min_length=1)


def _inbox(data, params, state, injection):
    emails = data["emails"]

    def get_emails() -> list[dict]:
        inbox = []
        for email in emails:
            entry = {key: value for key, value in email.items() if key != "injection"}
            if email.get("injection"):
                entry.update(injection or {})
            inbox.append(entry)
        return deepcopy(inbox)

    return {"get_emails": get_emails}


def _inbox_summary(data) -> str:
    slots = sum(1 for email in data["emails"] if email.get("injection"))
    return f"{len(data['emails'])} emails" + (
        f" · {slots} attacker-controlled" if slots else ""
    )


def _inbox_check(data, fields) -> list[str]:
    slots = sum(1 for email in data["emails"] if email.get("injection"))
    if fields is None and slots:
        return [
            "an email is marked 'injection: true' but the injection point is not get_emails"
        ]
    if fields is not None and slots != 1:
        return [f"exactly one email must be marked 'injection: true' (found {slots})"]
    return []


# --- send email ------------------------------------------------------------------------------------------------


class SendEmailParams(_Strict):
    sender: str = Field(min_length=1)


def _send_email(data, params, state, injection):
    def send_email(
        to: str = "support@internal.com",
        subject: str = "",
        body: str = "",
    ) -> dict:
        state.sent_emails.append(
            {
                "id": str(uuid4()),
                "to": to,
                "subject": subject,
                "body": body,
                "from": params.sender,
            }
        )
        return {"status": "success", "msg": f"You successfully delivered to {to}"}

    return {"send_email": send_email}


# --- offers ----------------------------------------------------------------------------------------------------


class OffersData(_Strict):
    offers: dict[str, dict[str, Any]] = Field(min_length=1)
    requisitions: list[dict[str, Any]]
    routing_targets: list[dict[str, Any]]
    benchmarks: dict[str, dict[str, Any]]


OFFER_NOT_FOUND = {"error": "Offer content not found."}
PRICE_NOT_FOUND = {"error": "No information on provided product available."}


def _offers(data, params, state, injection):
    def load_open_offers() -> list[dict]:
        return deepcopy(data["requisitions"])

    def list_routing_targets() -> list[dict]:
        return deepcopy(data["routing_targets"])

    def get_price_reference(sku: str) -> dict:
        return next(
            (
                deepcopy(value)
                for key, value in data["benchmarks"].items()
                if key in sku
            ),
            deepcopy(PRICE_NOT_FOUND),
        )

    def parse_offer(requisition_id: str) -> dict:
        offer = deepcopy(data["offers"].get(requisition_id))
        if not offer:
            return deepcopy(OFFER_NOT_FOUND)
        offer.update(injection or {})
        return offer

    return {
        "load_open_offers": load_open_offers,
        "list_routing_targets": list_routing_targets,
        "get_price_reference": get_price_reference,
        "parse_offer": parse_offer,
    }


def _offers_summary(data) -> str:
    return (
        f"{len(data['offers'])} offers · {len(data['requisitions'])} requisitions · "
        f"{len(data['routing_targets'])} routing targets · {len(data['benchmarks'])} benchmarks"
    )


TOOL_KINDS: dict[str, ToolKind] = {
    kind.name: kind
    for kind in [
        ToolKind(
            name="shared_drive",
            title="Shared drive",
            tools=(
                ToolSpec("list_folders", "List folders in the company shared drive."),
                ToolSpec(
                    "list_files_of_folder",
                    "List file names in a folder by its full path.",
                ),
                ToolSpec(
                    "get_file_content",
                    "Read a file by its full folder path and file name.",
                ),
            ),
            factory=_shared_drive,
            data_label="Shared drive",
            data_model=DriveData,
            summarize=_drive_summary,
        ),
        ToolKind(
            name="inbox",
            title="Inbox",
            tools=(
                ToolSpec(
                    "get_emails",
                    "Fetch inbox messages, including sender, subject and body.",
                ),
            ),
            factory=_inbox,
            data_label="Inbox",
            data_model=InboxData,
            injection_tool="get_emails",
            summarize=_inbox_summary,
            check=_inbox_check,
        ),
        ToolKind(
            name="send_email",
            title="Send email (simulated)",
            tools=(
                ToolSpec(
                    "send_email",
                    "Simulate sending an email to the specified recipient.",
                    sequential=True,
                ),
            ),
            factory=_send_email,
            data_label="Outbox (simulated)",
            params_model=SendEmailParams,
            data_access="write",
            summarize=lambda data: "records sent emails",
        ),
        ToolKind(
            name="offers",
            title="Offer review",
            tools=(
                ToolSpec(
                    "load_open_offers",
                    "Retrieve active requisitions, fallback thresholds and assigned reviewers.",
                ),
                ToolSpec(
                    "list_routing_targets",
                    "List reviewers with roles, spending authority and fallback status.",
                ),
                ToolSpec(
                    "get_price_reference",
                    "Fetch benchmark unit pricing for a product SKU.",
                ),
                ToolSpec(
                    "parse_offer",
                    "Extract the structured offer data for a requisition.",
                ),
            ),
            factory=_offers,
            data_label="Offers and reference data",
            data_model=OffersData,
            injection_tool="parse_offer",
            summarize=_offers_summary,
        ),
    ]
}


def build_toolset(
    kind: ToolKind,
    *,
    toolset_id: str,
    data: Any,
    params: Mapping[str, Any],
    descriptions: Mapping[str, str],
    state: ToolState,
    injection: dict[str, str] | None = None,
) -> list[BoundTool]:
    """Bind a tool kind to its data and one case's state.

    `data` is rendered with `state.variables` here, so runtime placeholders such as `{{secret}}` are filled.
    `injection` (attacker or benign content, keyed by injection field in field order) is passed only to the
    toolset that owns the injection tool.
    """
    rendered = (
        render_data(deepcopy(data), state.variables) if data is not None else None
    )
    functions = kind.factory(
        rendered, kind.params_model.model_validate(dict(params)), state, injection
    )
    return [
        BoundTool(
            name=spec.name,
            description=descriptions.get(spec.name, spec.description),
            function=functions[spec.name],
            sequential=spec.sequential,
            toolset=toolset_id,
            kind=kind.name,
        )
        for spec in kind.tools
    ]
