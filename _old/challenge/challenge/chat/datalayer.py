from functools import lru_cache
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import pytz

import asyncpg
import structlog
from chainlit.data.chainlit_data_layer import ChainlitDataLayer
from langgraph.checkpoint.base import CheckpointTuple
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict, HumanMessage, AIMessage, \
    ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, and_, func, case, bindparam
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.create import create_engine

from challenge.chat.storage import get_storage_client
from _old.challenge.challenge.migrations.models.challenge_solutions import ChallengeSolutions
from challenge.config import get_settings
from challenge.migrations.models import ChatHistory, User
from _old.challenge.challenge.migrations.models.challenge_submissions import ChallengeSubmissions
from _old.challenge.challenge.utils.database import get_asyncpg_connection

logger = structlog.get_logger(__name__)


@lru_cache
def get_data_layer() -> "ChatAiDataLayer":
    """Configure the Chainlit data layer with a database URL and storage client.
    It does not store persist the full chat history, only "visible" messages (steps and elements).
    """
    settings = get_settings()

    return ChatAiDataLayer(
        database_url=settings.database_url(),
        storage_client=get_storage_client(),
        show_logger=False,
        psycopg_pool_min_size=settings.PSYCOPG_MIN_SIZE,
        psycopg_pool_max_size=settings.PSYCOPG_MAX_SIZE,
    )


class ChatAiDataLayer(ChainlitDataLayer):
    """
    Custom Data Layer to extend Chainlit's functionality to correctly persist chat history.
    Also, add another connection pool for LangGraph to use, since it needs Psycopg3 instead of asyncpg.
    """

    def __init__(self, *args, psycopg_pool_min_size: int = 1, psycopg_pool_max_size: int = 5, **kwargs):
        super().__init__(*args, **kwargs)

        # LangGraph resources (one global pool + one shared checkpointer)
        self._lg_pool: Optional[AsyncConnectionPool] = None
        self._checkpointer: Optional[AsyncPostgresSaver] = None
        self._psycopg_pool_min_size: int = psycopg_pool_min_size
        self._psycopg_pool_max_size: int = psycopg_pool_max_size

    async def connect(self) -> None:
        """Override the connect method to use Cloud SQL Python Connector if needed."""
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                self.database_url, connect=get_asyncpg_connection()
            )

        if not self._lg_pool:
            # Important: DSN WITHOUT "+asyncpg"
            db_url = get_settings().database_url(include_driver=False)
            self._lg_pool = AsyncConnectionPool(
                conninfo=db_url,
                min_size=self._psycopg_pool_min_size,
                max_size=self._psycopg_pool_max_size,
                # autocommit + dict_row recommended for the checkpointer
                kwargs={"autocommit": True, "row_factory": dict_row},
                open=False,
            )
            # Open the connection pool separately for easier debug
            await self._lg_pool.open()

        # 3) One shared checkpointer instance bound to the Psycopg3 pool
        if not self._checkpointer:
            self._checkpointer = AsyncPostgresSaver(conn=self._lg_pool)

    async def set_chat_history(
            self, thread_id: str, chat_history: list[BaseMessage]
    ) -> None:
        """Persist the full chat history including user and AI messages."""
        # Convert LangChain messages to dict format for storage
        messages_dict = messages_to_dict(chat_history)
        messages_json = json.dumps(messages_dict, default=str)
        values_to_insert = {
            "thread_id": thread_id,
            "messages": messages_json,
        }

        insert_stmt = insert(ChatHistory).values(values_to_insert)
        upsert_stmt = (
            insert(ChatHistory)
            .values(values_to_insert)
            .on_conflict_do_update(
                index_elements=["thread_id"],
                set_={"messages": insert_stmt.excluded.messages},
            )
        )
        compiled_query = str(upsert_stmt.compile(self._get_engine()))
        await self.execute_query(compiled_query, values_to_insert)

    async def get_chat_history(self, thread_id: str) -> list[BaseMessage]:
        """Retrieve the full chat history including user and AI messages."""
        get_stmt = select(ChatHistory.messages).where(
            ChatHistory.thread_id == thread_id
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            chat_history = await self.execute_query(
                compiled_query, {"thread_id": thread_id}
            )
            # Convert stored dict format back to LangChain messages
            messages_dict = json.loads(chat_history[0]["messages"])
            return messages_from_dict(messages_dict)
        except Exception:
            return []

    async def set_challenge_solution(
            self, user_identifier: str, challenge_level: str, solution_data: dict
    ) -> ChallengeSolutions | None:
        """Persist the full chat history including user and AI messages."""
        values_to_insert = {
            "user_identifier": user_identifier,
            "challenge_level": challenge_level,
            "solution_data": json.dumps(solution_data, default=str)
        }

        insert_stmt = insert(ChallengeSolutions).values(values_to_insert)
        upsert_stmt = (
            insert_stmt
            .values(values_to_insert)
            .on_conflict_do_update(
                index_elements=["user_identifier", "challenge_level"],
                # dummy update so that it returns the object
                set_={"user_identifier": insert_stmt.excluded.user_identifier}
            ).returning(ChallengeSolutions)
        )
        compiled_query = str(upsert_stmt.compile(self._get_engine()))
        result = await self.execute_query(compiled_query, values_to_insert)
        return result[0] if result else None

    async def get_challenge_solution(self, user_identifier: str, challenge_level: str) -> ChallengeSolutions | None:
        """Retrieve the full chat history including user and AI messages."""
        get_stmt = select(ChallengeSolutions).where(
            ChallengeSolutions.user_identifier == user_identifier,
            ChallengeSolutions.challenge_level == challenge_level
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            challenge_solution = await self.execute_query(
                compiled_query, {"user_identifier": user_identifier,
                                 "challenge_level": challenge_level}
            )
            # needed because we do not use proper SQLAlchemy sessions
            if challenge_solution:
                challenge_solution[0]["solution_data"] = json.loads(challenge_solution[0]["solution_data"])
                return challenge_solution[0]
            return None
        except Exception:
            return None

    def get_checkpointer(self) -> AsyncPostgresSaver:
        """Return the shared LangGraph async checkpointer."""
        if not self._checkpointer:
            raise RuntimeError("Data layer not connected yet")
        return self._checkpointer

    async def set_challenge_submission_for_user(
            self, user_identifier: str, challenge_level: str, submission_executed_successfully: bool = False,
            submission_data: dict | None = None, solved: bool = False, thread_id: str | None = None,
            input_token_usage: int = 0, output_token_usage: int = 0) -> ChallengeSubmissions | None:
        """Submit challenge attempt or solution to the database."""
        # Extract level without difficulty (e.g., "Level 1 - EASY" -> "Level 1")
        # level_only = _extract_level_without_difficulty(challenge_level)

        values_to_insert = {
            "thread_id": thread_id,
            "user_identifier": user_identifier,
            "challenge_level": challenge_level,
            "submission_data": json.dumps(submission_data, default=str) if submission_data else None,
            "submission_executed_successfully": submission_executed_successfully,
            "solved": solved,
            "solved_at": datetime.now() if solved else None,
            "input_tokens_used": input_token_usage,
            "output_tokens_used": output_token_usage
        }

        insert_stmt = insert(ChallengeSubmissions).values(values_to_insert)

        update_fields = {
            "input_tokens_used": ChallengeSubmissions.input_tokens_used + insert_stmt.excluded.input_tokens_used,
            "output_tokens_used": ChallengeSubmissions.output_tokens_used + insert_stmt.excluded.output_tokens_used,
        }
        if submission_executed_successfully:
            update_fields["submission_executed_successfully"] = insert_stmt.excluded.submission_executed_successfully
        # Only update solved and solved_at when solved is True
        if solved:
            update_fields["solved"] = insert_stmt.excluded.solved
            update_fields["solved_at"] = insert_stmt.excluded.solved_at
        # should always be empty on update, but maybe there is case where it needs to be updated
        if submission_data:
            update_fields["submission_data"] = insert_stmt.excluded.submission_data

        upsert_stmt = (
            insert(ChallengeSubmissions)
            .values(values_to_insert)
            .on_conflict_do_update(
                index_elements=["user_identifier", "thread_id"],
                set_=update_fields
            ).returning(ChallengeSubmissions)
        )

        compiled_query = upsert_stmt.compile(self._get_engine())
        result = await self.execute_query(str(compiled_query), values_to_insert)
        return result[0] if result else None

    async def get_challenge_submission_of_user(
            self, user_identifier: str, thread_id: str,
    ) -> ChallengeSubmissions | None:
        """Get challenge attempt or solution from the database."""
        get_stmt = select(ChallengeSubmissions).where(
            ChallengeSubmissions.user_identifier == user_identifier,
            ChallengeSubmissions.thread_id == thread_id,
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            challenge_submission = await self.execute_query(
                compiled_query, {"user_identifier": user_identifier, "thread_id": thread_id}
            )
            return challenge_submission[0] if challenge_submission else None
        except Exception as e:
            logger.error(f"Failed to get challenge submissions of user {user_identifier} and thread {thread_id}: {e}")
            return None

    async def get_all_challenge_submissions_of_user(
            self, user_identifier: str,
    ) -> list[ChallengeSubmissions]:
        """Get challenge attempts/solutions from the database."""
        get_stmt = select(ChallengeSubmissions).where(
            ChallengeSubmissions.user_identifier == user_identifier,
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            challenge_submissions = await self.execute_query(
                compiled_query, {"user_identifier": user_identifier}
            )
            return challenge_submissions
        except Exception as e:
            logger.error(f"Failed to get all challenge submissions of user {user_identifier}: {e}")
            return []

    async def get_all_challenge_submissions_of_user_for_challenge(
            self, user_identifier: str, challenge_level: str
    ) -> list[ChallengeSubmissions]:
        """Get challenge attempts/solutions from the database."""
        challenge_level = challenge_level.upper().replace(" ", "_")
        get_stmt = select(ChallengeSubmissions).where(
            ChallengeSubmissions.user_identifier == user_identifier,
            ChallengeSubmissions.challenge_level == challenge_level,
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            challenge_submissions_of_user_for_challenge = await self.execute_query(
                compiled_query, {"user_id": user_identifier, "challenge_level": challenge_level}
            )
            return challenge_submissions_of_user_for_challenge
        except Exception as e:
            logger.error(
                f"Failed to get all challenge submissions of user {user_identifier} for challenge {challenge_level}: {e}")
            return []

    async def check_challenge_solved(self, user_identifier: str, challenge_level: str) -> bool:
        """Check if the challenge is solved."""
        challenge_level = challenge_level.upper().replace(" ", "_")
        get_stmt = select(ChallengeSubmissions.solved).where(
            ChallengeSubmissions.user_identifier == user_identifier,
            ChallengeSubmissions.challenge_level == challenge_level,
            ChallengeSubmissions.solved == True,
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            challenge_submissions_of_user_for_challenge = await self.execute_query(
                compiled_query, {"user_id": user_identifier, "challenge_level": challenge_level}
            )
            return len(challenge_submissions_of_user_for_challenge) > 0
        except Exception as e:
            logger.error(
                f"Failed to get all challenge submissions of user {user_identifier} for challenge {challenge_level}: {e}")
            return []

    async def get_challenge_submission_and_solution_of_user(
            self, user_identifier: str, thread_id: str,
    ) -> dict | None:
        """Get challenge attempt or solution from the database."""
        get_stmt = (
            select(ChallengeSubmissions, ChallengeSolutions)
            .join(
                ChallengeSolutions,
                and_(ChallengeSubmissions.user_identifier == ChallengeSolutions.user_identifier,
                     ChallengeSubmissions.challenge_level == ChallengeSolutions.challenge_level)
            )
            .where(
                ChallengeSubmissions.user_identifier == user_identifier,
                ChallengeSubmissions.thread_id == thread_id,
            )
        )
        compiled_query = str(get_stmt.compile(self._get_engine()))
        try:
            submission_and_solution = await self.execute_query(
                compiled_query,
                {"user_identifier": user_identifier, "thread_id": thread_id}
            )
            # needed because we do not use proper SQLAlchemy sessions
            if submission_and_solution and submission_and_solution[0]["solution_data"]:
                submission_and_solution[0]["solution_data"] = json.loads(submission_and_solution[0]["solution_data"])
                return submission_and_solution[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get challenge submissions of user {user_identifier} and thread {thread_id}: {e}")
            return None

    def _construct_sequenetial_execution_flow_with_guardrails(self, checkpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all execution steps in sequential order.
        Returns a single list showing the exact flow of execution.
        """
        flow = []

        # Get all messages
        messages = checkpoint['channel_values'].get('messages', [])

        # track tool calls
        recorded_tool_calls = {}
        for message in messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                for tool_call in message.tool_calls:
                    recorded_tool_calls[tool_call['id']] = {
                        'args': tool_call['args'],
                        'name': tool_call['name'],
                    }

        for message in messages:
            if isinstance(message, HumanMessage):
                flow_block = {
                    'type': 'human',
                    'content': message.content,
                    'guardrails': []
                }
                if message.additional_kwargs and 'guardrails' in message.additional_kwargs:
                    for guardrail in message.additional_kwargs['guardrails']:
                        flow_block['guardrails'].append({
                            'type': 'guardrail',
                            'name': guardrail['name'],
                            'status': guardrail['status'],
                            'reason': guardrail.get('reason', ''),
                        })
                flow.append(flow_block)
            elif isinstance(message, AIMessage) and message.content != '':
                flow_block = {
                    'type': 'ai',
                    'content': message.content,
                    'guardrails': []
                }
                if message.additional_kwargs and 'guardrails' in message.additional_kwargs:
                    for guardrail in message.additional_kwargs['guardrails']:
                        flow_block['guardrails'].append({
                            'type': 'guardrail',
                            'name': guardrail['name'],
                            'status': guardrail['status'],
                            'reason': guardrail.get('reason', ''),
                        })
                flow.append(flow_block)
            elif isinstance(message, ToolMessage):
                flow_block = {
                    'type': 'tool',
                    'content': message.content,
                    'name': recorded_tool_calls[message.tool_call_id]['name'],
                    # add pull request #84 changes of Marina here nicely formatting tool args
                    'args': json.dumps(recorded_tool_calls[message.tool_call_id]['args'], default=str, indent=2),
                    'guardrails': []
                }
                if message.additional_kwargs and 'guardrails' in message.additional_kwargs:
                    for guardrail in message.additional_kwargs['guardrails']:
                        flow_block['guardrails'].append({
                            'type': 'guardrail',
                            'name': guardrail['name'],
                            'status': guardrail['status'],
                            'reason': guardrail.get('reason', ''),
                        })
                flow.append(flow_block)
        return flow

    def _construct_sequential_execution_flow(self, checkpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all execution steps in sequential order.
        Returns a single list showing the exact flow of execution.
        """
        flow = []

        # Get all messages
        messages = checkpoint['channel_values'].get('messages', [])

        # Get all middleware executions from versions_seen
        versions_seen = checkpoint.get('versions_seen', {})

        # Create a map of version strings to step numbers for ordering
        version_to_step = {}

        # Parse middleware versions
        for key, versions in versions_seen.items():
            if 'Middleware' in key or key in ['model', '__start__']:
                for branch, version_str in versions.items():
                    # Parse version format: "00000000000000000000000000000016.0.9032791862870599"
                    parts = version_str.split('.')
                    step_hex = parts[0]
                    step_num = int(step_hex, 16) if step_hex else 0

                    version_to_step[version_str] = {
                        'step': step_num,
                        'name': key,
                        'branch': branch,
                        'version': version_str
                    }

        # Add messages - they typically happen at specific version steps
        # We'll interleave them based on the conversation flow
        for idx, msg in enumerate(messages):
            msg_type = type(msg).__name__

            flow_item = {
                'step': idx * 3,  # Rough ordering - we'll sort properly later
                'type': msg_type,
                'content': msg.content,
                'id': getattr(msg, 'id', None),
            }

            if isinstance(msg, HumanMessage):
                flow_item['name'] = getattr(msg, 'name', 'User')
                flow_item['display'] = f"👤 {flow_item['name']}: {msg.content}"

            elif isinstance(msg, AIMessage):
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_names = [tc['name'] for tc in msg.tool_calls]
                    flow_item['tool_calls'] = msg.tool_calls
                    flow_item['display'] = f"🤖 AI: Calling tools: {', '.join(tool_names)}"
                else:
                    flow_item['display'] = f"🤖 AI: {msg.content}"

                if hasattr(msg, 'usage_metadata') and msg.usage_metadata:
                    flow_item['tokens'] = {
                        'input': msg.usage_metadata.get('input_tokens'),
                        'output': msg.usage_metadata.get('output_tokens')
                    }

            elif isinstance(msg, ToolMessage):
                flow_item['tool_name'] = getattr(msg, 'name', 'Unknown')
                flow_item['tool_call_id'] = getattr(msg, 'tool_call_id', None)
                content_preview = msg.content[:500] + ('...' if len(msg.content) > 500 else '')
                flow_item['display'] = f"🔧 Tool ({flow_item['tool_name']}): {content_preview}"

            flow.append(flow_item)

        return flow

    async def get_submission_and_solution_and_agent_execution_sequence(self, user_identifier, thread_id: str):
        try:
            get_stmt = (select(ChallengeSubmissions, ChallengeSolutions)
            .join(
                ChallengeSolutions,
                and_(ChallengeSubmissions.user_identifier == ChallengeSolutions.user_identifier,
                     ChallengeSubmissions.challenge_level == ChallengeSolutions.challenge_level)
            )
            .where(
                ChallengeSubmissions.user_identifier == user_identifier,
                ChallengeSubmissions.thread_id == thread_id,
            ))
            compiled_query = str(get_stmt.compile(self._get_engine()))
            # Verify the submission exists
            submission_and_solution = await self.execute_query(
                compiled_query, {"user_id": user_identifier, "thread_id": thread_id}
            )

            if not submission_and_solution:
                logger.info(f"No submission found for user {user_identifier} and thread {thread_id}")
                return None
            if submission_and_solution and submission_and_solution[0]["submission_data"]:
                submission_and_solution[0]["submission_data"] = json.loads(
                    submission_and_solution[0]["submission_data"])
            if submission_and_solution and submission_and_solution[0]["solution_data"]:
                submission_and_solution[0]["solution_data"] = json.loads(submission_and_solution[0]["solution_data"])
            # Use LangGraph's checkpointer to get the latest state
            config = {"configurable": {"thread_id": thread_id}}
            # aget_tuple gets the latest checkpoint of an agent graph if config does not contain a checkpoint id
            checkpoint_tuple: CheckpointTuple = await self._checkpointer.aget_tuple(config)

            sequential_execution_flow = self._construct_sequenetial_execution_flow_with_guardrails(
                checkpoint_tuple.checkpoint)

            return {
                "submission": submission_and_solution[0],
                "execution_sequence": sequential_execution_flow,
            }

        except Exception as e:
            logger.exception(f"Failed to get agent execution graph of user {user_identifier} and thread {thread_id}")
            raise

    async def get_complete_agent_execution_sequence(self, user_identifier, thread_id: str):
        try:
            get_stmt = select(ChallengeSubmissions).where(
                ChallengeSubmissions.user_identifier == user_identifier,
                ChallengeSubmissions.thread_id == thread_id,
            )
            compiled_query = str(get_stmt.compile(self._get_engine()))
            # Verify the submission exists
            submission = await self.execute_query(
                compiled_query, {"user_id": user_identifier, "thread_id": thread_id}
            )

            if not submission:
                logger.info(f"No submission found for user {user_identifier} and thread {thread_id}")
                return None

            # Use LangGraph's checkpointer to get the latest state
            config = {"configurable": {"thread_id": thread_id}}
            # execution_history = []
            # async for checkpoint_tuple in self._checkpointer.alist(config):
            #     execution_history.append({
            #         "config": checkpoint_tuple.config,
            #         "checkpoint": checkpoint_tuple.checkpoint,
            #         "metadata": checkpoint_tuple.metadata,
            #         "parent_config": checkpoint_tuple.parent_config,
            #     })
            # aget_tuple gets the latest checkpoint of an agent graph if config does not contain a checkpoint id
            checkpoint_tuple: CheckpointTuple = await self._checkpointer.aget_tuple(config)

            sequential_execution_flow = self._construct_sequenetial_execution_flow_with_guardrails(
                checkpoint_tuple.checkpoint)

            return {
                "submission": submission[0],
                "execution_sequence": sequential_execution_flow,
            }

        except Exception as e:
            logger.exception(f"Failed to get agent execution graph of user {user_identifier} and thread {thread_id}")
            raise

    async def get_all_submissions(self, since: datetime = None, until: datetime = None):
        try:
            conditions = [ChallengeSubmissions.input_tokens_used > 0]
            if since is not None:
                since_utc_naive = since.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at >= since_utc_naive)
            if until is not None:
                until_utc_naive = until.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at <= until_utc_naive)
            get_stmt = (select(ChallengeSubmissions)
                .where(and_(*conditions))
                .order_by(ChallengeSubmissions.challenge_level.desc(), ChallengeSubmissions.user_identifier.asc())
            )
            # compiled_query = str(get_stmt.compile(self._get_engine()))
            compiled_query = get_stmt.compile(self._get_engine())

            submissions = await self.execute_query(str(compiled_query), compiled_query.params)

            if not submissions:
                logger.info(f"No submissions found")
                return []

            return submissions

        except Exception as e:
            logger.exception(f"Failed to get all successful agent execution sequences")
            raise


    async def get_all_successful_submissions(self):
        try:
            get_stmt = (select(ChallengeSubmissions)
                .where(ChallengeSubmissions.solved == True)
                .order_by(ChallengeSubmissions.challenge_level.desc(), ChallengeSubmissions.user_identifier.asc())
            )
            compiled_query = str(get_stmt.compile(self._get_engine()))

            submissions = await self.execute_query(compiled_query)

            if not submissions:
                logger.info(f"No submissions found")
                return []

            return submissions

        except Exception as e:
            logger.exception(f"Failed to get all successful agent execution sequences")
            raise

    async def get_all_submission_and_solved_count(self, since: datetime = None, until: datetime = None):
        try:
            conditions = [ChallengeSubmissions.input_tokens_used > 0]
            if since is not None:
                since_utc_naive = since.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at >= since_utc_naive)
            if until is not None:
                until_utc_naive = until.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at <= until_utc_naive)
            get_stmt = select(
                # Total Submissions (where tokens > 0)
                func.count(ChallengeSubmissions.id).label("n_total_attempts"),

                # Total Solved (where solved is True AND tokens > 0)
                func.count(case((ChallengeSubmissions.solved == True, 1))).label("n_total_solved")
            ).where(
                *conditions
            )
            compiled_query = str(get_stmt.compile(self._get_engine(), compile_kwargs={"literal_binds": True}))

            submission_stats = await self.execute_query(compiled_query)

            if not submission_stats:
                logger.info(f"No submissions found")
                return {}

            return submission_stats[0] if len(submission_stats) > 0 else []
        except Exception as e:
            logger.exception(f"Failed to get all submission and solved count")
            raise

    async def get_all_submission_and_solved_count_grouped_by_challenge_level(self, since: datetime = None, until: datetime = None):
        try:
            conditions = [ChallengeSubmissions.input_tokens_used > 0]
            if since is not None:
                since_utc_naive = since.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at >= since_utc_naive)
            if until is not None:
                until_utc_naive = until.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at <= until_utc_naive)
            get_stmt =(select(
                ChallengeSubmissions.challenge_level,

                # Count all attempts for this level
                func.count(ChallengeSubmissions.id).label("n_attempts"),

                # Count only solved for this level
                func.count(case((ChallengeSubmissions.solved == True, 1))).label("n_solved")
            )
            .where(*conditions)
            .group_by(ChallengeSubmissions.challenge_level)
            .order_by(ChallengeSubmissions.challenge_level.asc()))
            compiled_query = str(get_stmt.compile(self._get_engine(), compile_kwargs={"literal_binds": True}))

            submission_stats = await self.execute_query(compiled_query)

            if not submission_stats:
                logger.info(f"No submissions found")
                return {}

            return submission_stats
        except Exception as e:
            logger.exception(f"Failed to get all submission and solved count")
            raise

    async def get_all_submission_and_solved_count_grouped_by_challenge_level_for_user(self, user_identifier: str):
        try:
            get_stmt =(select(
                ChallengeSubmissions.challenge_level,

                # Count all attempts for this level
                func.count(ChallengeSubmissions.id).label("n_attempts"),

                # Count only solved for this level
                func.count(case((ChallengeSubmissions.solved == True, 1))).label("n_solved")
            )
            .where(and_(ChallengeSubmissions.input_tokens_used > 0,
                        ChallengeSubmissions.user_identifier == bindparam('user_identifier')))
            .group_by(ChallengeSubmissions.challenge_level)
            .order_by(ChallengeSubmissions.challenge_level.asc()))
            compiled_query = get_stmt.compile(self._get_engine())

            submission_stats_of_user = await self.execute_query(str(compiled_query), {**compiled_query.params, 'user_identifier': user_identifier})

            if not submission_stats_of_user:
                logger.info(f"No submissions found")
                return {}

            return submission_stats_of_user
        except Exception as e:
            logger.exception(f"Failed to get all submission and solved count")
            raise

    async def get_all_users(self):
        try:
            get_stmt = select(User).distinct()
            compiled_query = str(get_stmt.compile(self._get_engine()))

            users = await self.execute_query(compiled_query)

            if not users:
                logger.info(f"No users found")
                return []

            return users
        except Exception as e:
            logger.exception(f"Failed to get all users")
            raise

    async def get_winners_sorted_by_points(self, n = None, since: datetime = None, until: datetime = None):
        try:
            conditions = [ChallengeSubmissions.solved == True, ChallengeSubmissions.input_tokens_used > 0]
            if since is not None:
                since_utc_naive = since.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at >= since_utc_naive)
            if until is not None:
                until_utc_naive = until.astimezone(timezone.utc).replace(tzinfo=None)
                conditions.append(ChallengeSubmissions.created_at <= until_utc_naive)
            # Subquery to get distinct user-challenge combinations
            distinct_solves = (
                select(
                    ChallengeSubmissions.user_identifier,
                    ChallengeSubmissions.challenge_level
                )
                .where(and_(*conditions))
                .distinct()
                .subquery()
            )

            # Points per level
            points_case = case(
                (distinct_solves.c.challenge_level.in_(['LEVEL_1', 'LEVEL_2', 'LEVEL_3']), 1),
                (distinct_solves.c.challenge_level.in_(['LEVEL_4', 'LEVEL_5', 'LEVEL_6']), 5),
                (distinct_solves.c.challenge_level.in_(['LEVEL_7', 'LEVEL_8', 'LEVEL_9']), 10),
                else_=0
            )


            get_stmt = (
                select(
                    distinct_solves.c.user_identifier,
                    func.sum(points_case).label("points")
                )
                .group_by(distinct_solves.c.user_identifier)
                .order_by(func.sum(points_case).desc())
                .limit(n)
            )
            compiled_query = str(get_stmt.compile(self._get_engine(), compile_kwargs={"literal_binds": True}))
            # Verify the submission exists
            winner_stats = await self.execute_query(compiled_query)

            if not winner_stats:
                logger.info(f"No winner stats found")
                return []

            return winner_stats
        except Exception as e:
            logger.exception(f"Failed to get current winners")
            raise

    async def cleanup(self):
        """Override the cleanup method to close the connection pool of LangGraph."""
        await super().cleanup()

        if self._lg_pool:
            logger.debug("Cleaning up psycopg3 connection pool")
            await self._lg_pool.close()
            self._lg_pool = None

        self._checkpointer = None

    @staticmethod
    @lru_cache
    def _get_engine() -> Engine:
        return create_engine(
            get_settings().database_url(include_driver=True), echo=False
        )

    # async def get_or_create_secret_email(self, user_identifier: str, thread_id: str) -> str:
    #     """
    #     Get the secret email for a specific thread, or generate and store a new one if it doesn't exist.
    #
    #     Args:
    #         user_identifier: The user identifier
    #         thread_id: The thread ID
    #
    #     Returns:
    #         The secret email address (randomized with UUID)
    #     """
    #     # Try to get existing submission with secret_email
    #     get_stmt = select(ChallengeSubmissions.secret_email).where(
    #         ChallengeSubmissions.user_identifier == user_identifier,
    #         ChallengeSubmissions.thread_id == thread_id,
    #     )
    #     compiled_query = str(get_stmt.compile(self._get_engine()))
    #
    #     try:
    #         result = await self.execute_query(
    #             compiled_query, {"user_identifier": user_identifier, "thread_id": thread_id}
    #         )
    #
    #         # If exists and has a secret_email, return it
    #         if result and result[0].get("secret_email"):
    #             return result[0]["secret_email"]
    #
    #     except Exception as e:
    #         logger.debug(f"No existing secret email found for user {user_identifier} and thread {thread_id}: {e}")
    #
    #     # Generate a new secret email with UUID
    #     secret_uuid = str(uuid.uuid4())[:8]  # Use first 8 chars of UUID for readability
    #     secret_email = f"finance-fast-{secret_uuid}@internal.com"
    #
    #     # Store it in the database
    #     values_to_insert = {
    #         "thread_id": thread_id,
    #         "user_identifier": user_identifier,
    #         "challenge_level": "LEVEL_1",
    #         "submission_data": json.dumps({}),
    #         "submission_executed_successfully": False,
    #         "solved": False,
    #         "secret_email": secret_email,
    #     }
    #
    #     insert_stmt = insert(ChallengeSubmissions).values(values_to_insert)
    #     upsert_stmt = (
    #         insert(ChallengeSubmissions)
    #         .values(values_to_insert)
    #         .on_conflict_do_update(
    #             index_elements=["user_identifier", "thread_id"],
    #             set_={"secret_email": insert_stmt.excluded.secret_email}
    #         )
    #     )
    #
    #     compiled_query = upsert_stmt.compile(self._get_engine())
    #     await self.execute_query(str(compiled_query), values_to_insert)
    #
    #     logger.info(f"Generated new secret email for user {user_identifier}, thread {thread_id}: {secret_email}")
    #     return secret_email
