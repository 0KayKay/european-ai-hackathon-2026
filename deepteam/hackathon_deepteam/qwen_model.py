"""DeepEval model adapter for the local Ollama attacker."""

import asyncio
import json
import os
from threading import Event

from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel

from hackathon_deepteam.scan_config import AttackerConfig

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from litellm import acompletion, completion  # noqa: E402


class LocalQwenModel(DeepEvalBaseLLM):
    def __init__(
        self, config: AttackerConfig, base_url: str, stop_event: Event | None = None
    ):
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.stop_event = stop_event or Event()
        self._active_tasks: set[asyncio.Task] = set()
        super().__init__(model=config.model)

    def abort(self) -> None:
        self.stop_event.set()
        try:
            current_loop = asyncio.get_running_loop()
            current_task = asyncio.current_task()
        except RuntimeError:
            current_loop = None
            current_task = None
        for task in tuple(self._active_tasks):
            if task is current_task or task.done():
                continue
            loop = task.get_loop()
            if loop.is_closed():
                continue
            if loop is current_loop:
                task.cancel()
            else:
                loop.call_soon_threadsafe(task.cancel)

    def _ensure_active(self) -> None:
        if self.stop_event.is_set():
            raise asyncio.CancelledError("DeepTeam run stopped")

    def load_model(self):
        return self.config.model

    def get_model_name(self) -> str:
        return self.config.model

    def _request(self, prompt: str, schema: type[BaseModel] | None) -> dict:
        if schema is not None:
            prompt += (
                "\n\nReturn only valid JSON matching this schema; no Markdown or explanation:\n"
                + json.dumps(schema.model_json_schema())
            )
        request = {
            "model": self.config.model,
            "api_base": self.base_url,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.config.reasoning_effort is not None:
            request["reasoning_effort"] = self.config.reasoning_effort
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            }
        return request

    @staticmethod
    def _decode(response, schema: type[BaseModel] | None):
        content = response.choices[0].message.content or ""
        if schema is None:
            return content
        cleaned = (
            content.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        try:
            return schema.model_validate_json(cleaned)
        except ValueError:
            decoder = json.JSONDecoder()
            for start, char in enumerate(cleaned):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(cleaned[start:])
                    return schema.model_validate(value)
                except ValueError:
                    continue
            raise ValueError(f"{schema.__name__}: Qwen did not return valid JSON") from None

    def generate(self, prompt: str, schema: type[BaseModel] | None = None, **kwargs):
        self._ensure_active()
        try:
            result = self._decode(completion(**self._request(prompt, schema)), schema)
            self._ensure_active()
            return result
        except Exception:
            self.abort()
            raise

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None, **kwargs):
        self._ensure_active()
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        request_task: asyncio.Task | None = None
        try:
            request_task = asyncio.create_task(acompletion(**self._request(prompt, schema)))
            while not request_task.done():
                await asyncio.wait({request_task}, timeout=0.1)
                self._ensure_active()
            result = self._decode(await request_task, schema)
            self._ensure_active()
            return result
        except Exception:
            self.abort()
            raise
        finally:
            if task is not None:
                self._active_tasks.discard(task)
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
