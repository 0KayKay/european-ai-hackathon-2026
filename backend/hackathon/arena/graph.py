"""Resolved level -> execution graph for the level view and replay (IMPLEMENTATION.md §4).

Stable node ids: `attacker`, `user_task` (indirect), `injection` (indirect), `guardrails:<position>` (one group
per occupied position), `guardrail:<id>` (member of its group), `agent`, `tools` (group), `tool:<name>`,
`data:<toolset id>`, `success`, `output`. Edge kinds: `attacker`, `flow`, `tool_call`, `tool_result`, `success`,
`reply_loop` (direct multi-turn only: output -> attacker). Layout is the frontend's job.
"""

from typing import Any, Literal

from pydantic import BaseModel

from hackathon.arena.config.resolved import (
    PromptFile,
    ResolvedGuardrail,
    ResolvedLevel,
    ResolvedToolset,
)
from hackathon.arena.config.schema import ModelSpec
from hackathon.arena.config.templates import placeholders
from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.registry.guardrails import (
    AGENT_TO_OUTPUT,
    AROUND,
    GUARDRAIL_KINDS,
    TOOL_TO_AGENT,
    USER_TO_AGENT,
    position_title,
)
from hackathon.arena.registry.secrets import SECRET_KINDS
from hackathon.arena.registry.success import SUCCESS_KINDS
from hackathon.arena.registry.tools import TOOL_KINDS

NodeKind = Literal[
    "attacker",
    "user_task",
    "injection",
    "guardrail_group",
    "guardrail",
    "agent",
    "tool_group",
    "tool",
    "data",
    "success",
    "output",
]
EdgeKind = Literal[
    "attacker", "flow", "tool_call", "tool_result", "success", "reply_loop"
]


class Setting(BaseModel):
    label: str
    value: str


class Prompt(BaseModel):
    name: str
    file: str | None
    text: str


class NodeDetails(BaseModel):
    settings: list[Setting] = []
    prompts: list[Prompt] = []
    model: dict[str, Any] | None = None


class Node(BaseModel):
    id: str
    kind: NodeKind
    label: str
    subtitle: str = ""
    group: str | None = None
    details: NodeDetails = NodeDetails()


class Edge(BaseModel):
    id: str
    source: str
    target: str
    kind: EdgeKind
    label: str = ""


class Surface(BaseModel):
    channel: Literal["direct", "indirect"]
    turns: Literal["single", "multi"]
    max_turns: int


class Graph(BaseModel):
    surface: Surface
    nodes: list[Node]
    edges: list[Edge]

    def node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)


def _params(spec: ModelSpec) -> str:
    params = spec.params.compact()
    return (
        ", ".join(f"{key} {value}" for key, value in params.items())
        or "provider defaults"
    )


def _model_settings(spec: ModelSpec, label: str = "Model") -> list[Setting]:
    return [
        Setting(label=label, value=f"{spec.model} via {spec.endpoint}"),
        Setting(label=f"{label} parameters", value=_params(spec)),
    ]


def _prompt(name: str, prompt: PromptFile) -> Prompt:
    return Prompt(name=name, file=prompt.file, text=prompt.text)


def _number(value: int) -> str:
    return f"{value:,}"


def group_id(position: str) -> str:
    return f"guardrails:{position}"


class _Builder:
    def __init__(self, level: ResolvedLevel) -> None:
        self.level = level
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    def node(self, **values: Any) -> None:
        self.nodes.append(Node(**values))

    def edge(self, source: str, target: str, kind: EdgeKind, label: str = "") -> None:
        edge_id = f"{source}->{target}"
        if any(edge.id == edge_id for edge in self.edges):
            return  # e.g. guardrails:tool->agent -> agent, shared by every tool
        self.edges.append(
            Edge(id=edge_id, source=source, target=target, kind=kind, label=label)
        )

    def through(
        self, source: str, position: str, target: str, kind: EdgeKind, label: str = ""
    ) -> None:
        """Connect source -> target, passing the guardrail group at `position` when it is occupied."""
        if self.level.guardrails_at(position):
            self.edge(source, group_id(position), kind, label)
            self.edge(group_id(position), target, kind, label)
        else:
            self.edge(source, target, kind, label)

    # --- nodes ----------------------------------------------------------------------------------------

    def attacker(self) -> None:
        red = self.level.red_team
        surface = self.level.surface
        total = sum(attack.attempts for attack in red.attacks)
        attacks = ", ".join(
            f"{ATTACK_TYPES[attack.type].title} × {attack.attempts} ({ATTACK_TYPES[attack.type].turns}-turn)"
            for attack in red.attacks
        )
        settings = [
            Setting(label="Channel", value=f"{surface.channel}, {surface.turns}-turn"),
            Setting(label="Attack types", value=attacks),
            Setting(label="Red cases", value=str(total)),
        ]
        if surface.turns == "multi":
            settings.append(Setting(label="Max turns", value=str(surface.max_turns)))
        settings += _model_settings(red.attacker, "Attacker model")
        settings += _model_settings(red.simulator, "Simulator model")
        settings += _model_settings(red.evaluator, "Evaluator model")
        objective = red.objective
        self.node(
            id="attacker",
            kind="attacker",
            label="Attacker agent",
            subtitle=f"DeepTeam · {len(red.attacks)} attack types · {total} cases",
            details=NodeDetails(
                settings=settings,
                prompts=[
                    Prompt(name="Purpose", file=None, text=objective.purpose),
                    Prompt(name="Success criteria", file=None, text=objective.criteria),
                    Prompt(
                        name="Attacker knowledge",
                        file=None,
                        text=objective.attacker_knowledge,
                    ),
                ],
                model=red.attacker.summary(),
            ),
        )

    def user_task(self) -> None:
        text = self.level.surface.user_task or ""
        self.node(
            id="user_task",
            kind="user_task",
            label="User task",
            subtitle=text if len(text) <= 60 else text[:57] + "…",
            details=NodeDetails(
                settings=[
                    Setting(
                        label="Sent as", value="user message, one fresh run per case"
                    )
                ],
                prompts=[Prompt(name="User task", file=None, text=text)],
            ),
        )

    def injection(self) -> None:
        injection = self.level.surface.injection
        fields = ", ".join(
            f"{field.name} ≤ {_number(field.max_length)}" for field in injection.fields
        )
        self.node(
            id="injection",
            kind="injection",
            label="Injection point",
            subtitle=f"{injection.tool} · {fields}",
            details=NodeDetails(
                settings=[
                    Setting(label="Point", value=injection.point),
                    Setting(label="Delivered by", value=str(injection.tool)),
                    *[
                        Setting(
                            label=f"Field {field.name}",
                            value=f"≤ {_number(field.max_length)} characters"
                            + (f" · {field.description}" if field.description else ""),
                        )
                        for field in injection.fields
                    ],
                    Setting(
                        label="Reply to attacker", value="none (single-turn, indirect)"
                    ),
                ],
                prompts=[
                    Prompt(name="Description", file=None, text=injection.description)
                ]
                if injection.description
                else [],
            ),
        )

    def guardrail_groups(self) -> None:
        for position in self.level.positions():
            members = self.level.guardrails_at(position)
            self.node(
                id=group_id(position),
                kind="guardrail_group",
                label=position_title(position),
                subtitle=f"{len(members)} guardrail"
                + ("s" if len(members) != 1 else ""),
                details=NodeDetails(
                    settings=[
                        Setting(label="Position", value=position),
                        Setting(
                            label="Order",
                            value=" → ".join(member.id for member in members),
                        ),
                    ]
                ),
            )
            for member in members:
                self.guardrail(member, position)

    def guardrail(self, guardrail: ResolvedGuardrail, position: str) -> None:
        kind = GUARDRAIL_KINDS[guardrail.kind]
        settings = [
            Setting(label="Kind", value=kind.title),
            Setting(label="Position", value=guardrail.at),
            Setting(label="Order", value=str(guardrail.order + 1)),
        ]
        if guardrail.name != guardrail.id:
            settings.append(Setting(label="Name", value=guardrail.name))
        if guardrail.description:
            settings.append(Setting(label="Description", value=guardrail.description))
        prompts = []
        if kind.uses_model:
            settings += _model_settings(guardrail.model)
            settings += [
                Setting(label="On block", value=str(guardrail.on_block)),
                Setting(label="On timeout or failure", value=guardrail.on_error),
                Setting(label="Timeout", value=f"{guardrail.timeout_seconds:g} s"),
            ]
            prompts = [
                _prompt("System prompt", guardrail.system_prompt),
                _prompt("Policy", guardrail.policy),
            ]
        else:
            settings.append(
                Setting(label="On block", value="never blocks (normalizes text)")
            )
        if guardrail.blocked_message:
            settings.append(
                Setting(label="Blocked message", value=guardrail.blocked_message)
            )
        if guardrail.on_block in {"refuse", "replace_output"}:
            prompts.append(_prompt("Refusal text", self.level.refusal))
        self.node(
            id=f"guardrail:{guardrail.id}",
            kind="guardrail",
            label=guardrail.name if guardrail.name != guardrail.id else guardrail.id,
            subtitle=kind.title
            + (f" · {guardrail.model.model}" if guardrail.model else ""),
            group=group_id(position),
            details=NodeDetails(
                settings=settings,
                prompts=prompts,
                model=guardrail.model.summary() if guardrail.model else None,
            ),
        )

    def agent(self) -> None:
        agent = self.level.agent
        limits = self.level.limits
        prompts = [
            Prompt(
                name="System prompt",
                file=agent.system_prompt_file.file,
                text=agent.system_prompt,
            )
        ]
        if agent.security_prompt:
            prompts.append(
                _prompt(
                    f"Security rules ({self.level.difficulty})", agent.security_prompt
                )
            )
        self.node(
            id="agent",
            kind="agent",
            label=agent.name,
            subtitle=agent.model.model,
            details=NodeDetails(
                settings=[
                    *_model_settings(agent.model),
                    Setting(
                        label="Tools",
                        value=", ".join(tool.name for tool in self.level.tools),
                    ),
                    Setting(
                        label="Limits per run",
                        value=f"{limits.run.model_requests} model requests, {limits.run.tool_calls} tool calls",
                    ),
                    Setting(
                        label="Limits per conversation",
                        value=f"{limits.conversation.model_requests} model requests, "
                        f"{limits.conversation.tool_calls} tool calls",
                    ),
                    Setting(label="Timeout", value=f"{limits.timeout_seconds:g} s"),
                ],
                prompts=prompts,
                model=agent.model.summary(),
            ),
        )

    def tools(self) -> None:
        tools = self.level.tools
        if not tools:
            return
        self.node(
            id="tools", kind="tool_group", label="Tools", subtitle=f"{len(tools)} tools"
        )
        for tool in tools:
            around = self.level.guardrails_at(f"{AROUND}{tool.name}")
            settings = [
                Setting(label="Kind", value=TOOL_KINDS[tool.kind].title),
                Setting(label="Parameters", value=", ".join(tool.parameters) or "none"),
            ]
            if tool.sequential:
                settings.append(Setting(label="Execution", value="sequential"))
            if around:
                settings.append(
                    Setting(label="Guarded by", value=", ".join(g.id for g in around))
                )
            if tool.name == self.level.surface.injection.tool:
                settings.append(Setting(label="Carries injected content", value="yes"))
            self.node(
                id=f"tool:{tool.name}",
                kind="tool",
                label=tool.name,
                subtitle=TOOL_KINDS[tool.kind].title,
                group="tools",
                details=NodeDetails(
                    settings=settings,
                    prompts=[
                        Prompt(name="Description", file=None, text=tool.description)
                    ],
                ),
            )
        for toolset in self.level.agent.toolsets:
            self.data(toolset)

    def data(self, toolset: ResolvedToolset) -> None:
        kind = TOOL_KINDS[toolset.kind]
        settings = [Setting(label="Tool kind", value=kind.title)]
        settings += [
            Setting(label=key, value=str(value))
            for key, value in toolset.params.items()
        ]
        secret = self.level.secret
        prompts = []
        if toolset.data_file:
            prompts.append(
                Prompt(
                    name="Data",
                    file=toolset.data_file,
                    text=self.level.sources[toolset.data_file],
                )
            )
        if (
            secret.kind != "none"
            and toolset.data_file
            and _mentions_secret(toolset.data)
        ):
            settings.append(
                Setting(
                    label="Secret",
                    value=f"{SECRET_KINDS[secret.kind].title} · {secret.embedded_in}",
                )
            )
        self.node(
            id=f"data:{toolset.id}",
            kind="data",
            label=kind.data_label,
            subtitle=kind.summarize(toolset.data),
            details=NodeDetails(settings=settings, prompts=prompts),
        )

    def success(self) -> None:
        success = self.level.success
        kind = SUCCESS_KINDS[success.kind]
        settings = [
            Setting(label="Kind", value=kind.title),
            Setting(label="Observes", value=kind.observes),
        ]
        settings += [
            Setting(label=key, value=str(value))
            for key, value in success.params.items()
        ]
        secret = self.level.secret
        if secret.kind != "none":
            settings.append(
                Setting(label="Secret", value=f"{SECRET_KINDS[secret.kind].title}")
            )
        self.node(
            id="success",
            kind="success",
            label="Success check",
            subtitle=kind.title,
            details=NodeDetails(
                settings=settings,
                prompts=[
                    Prompt(name="Description", file=None, text=success.description)
                ]
                if success.description
                else [],
            ),
        )

    def output(self) -> None:
        surface = self.level.surface
        direct = surface.channel == "direct"
        self.node(
            id="output",
            kind="output",
            label="Agent output",
            subtitle="reply to the attacker"
            if direct
            else "final answer · not returned to the attacker",
            details=NodeDetails(
                settings=[
                    Setting(
                        label="Returned to attacker",
                        value=f"yes, next turn ≤ {surface.max_turns}"
                        if direct and surface.turns == "multi"
                        else "yes"
                        if direct
                        else "no",
                    )
                ],
                prompts=[_prompt("Refusal text", self.level.refusal)],
            ),
        )

    # --- edges ----------------------------------------------------------------------------------------

    def wire(self) -> None:
        level = self.level
        surface = level.surface
        if surface.channel == "direct":
            self.through(
                "attacker", USER_TO_AGENT, "agent", "attacker", "attacker message"
            )
        else:
            self.edge("attacker", "injection", "attacker", "planted content")
            self.edge(
                "injection",
                f"tool:{surface.injection.tool}",
                "attacker",
                "in tool output",
            )
            self.through("user_task", USER_TO_AGENT, "agent", "flow", "user task")
        for tool in level.tools:
            self.through(
                "agent",
                f"{AROUND}{tool.name}",
                f"tool:{tool.name}",
                "tool_call",
                "call",
            )
            self.through(
                f"tool:{tool.name}", TOOL_TO_AGENT, "agent", "tool_result", "result"
            )
        for toolset in level.agent.toolsets:
            kind = TOOL_KINDS[toolset.kind]
            for tool in toolset.tools:
                if kind.data_access == "read":
                    self.edge(
                        f"data:{toolset.id}", f"tool:{tool.name}", "flow", "reads"
                    )
                else:
                    self.edge(
                        f"tool:{tool.name}", f"data:{toolset.id}", "flow", "writes"
                    )
        self.through("agent", AGENT_TO_OUTPUT, "output", "flow", "reply")
        if SUCCESS_KINDS[level.success.kind].observes == "sent_emails":
            outbox = next(
                toolset
                for toolset in level.agent.toolsets
                if toolset.kind == "send_email"
            )
            self.edge(f"data:{outbox.id}", "success", "success", "sent emails")
        else:
            self.edge("output", "success", "success", "agent output")
        if surface.channel == "direct" and surface.turns == "multi":
            self.edge(
                "output", "attacker", "reply_loop", f"next turn ≤ {surface.max_turns}"
            )

    def build(self) -> Graph:
        surface = self.level.surface
        self.attacker()
        if surface.channel == "indirect":
            self.user_task()
            self.injection()
        self.guardrail_groups()
        self.agent()
        self.tools()
        self.success()
        self.output()
        self.wire()
        return Graph(
            surface=Surface(
                channel=surface.channel,
                turns=surface.turns,
                max_turns=surface.max_turns,
            ),
            nodes=self.nodes,
            edges=self.edges,
        )


def _mentions_secret(value: Any) -> bool:
    if isinstance(value, str):
        return "secret" in placeholders(value)
    if isinstance(value, list):
        return any(_mentions_secret(item) for item in value)
    if isinstance(value, dict):
        return any(_mentions_secret(item) for item in value.values())
    return False


def build_graph(level: ResolvedLevel) -> Graph:
    return _Builder(level).build()
