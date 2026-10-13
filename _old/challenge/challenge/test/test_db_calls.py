import asyncio
from pprint import pprint

from langgraph.checkpoint.base import CheckpointTuple

from _old.challenge.challenge.chat.datalayer import get_data_layer


async def main():
    submission = await get_data_layer().get_challenge_submission_of_user(user_identifier="benediktbaeumle@lapiscode.com", thread_id="5978634a-f52f-44bb-8bba-229d17dc7a94")

    all_submissions_of_user = await get_data_layer().get_all_challenge_submissions_of_user(user_identifier="benediktbaeumle@lapiscode.com")
    all_submissions_of_user_for_challenge_1 = await get_data_layer().get_all_challenge_submissions_of_user_for_challenge(user_identifier="benediktbaeumle@lapiscode.com", challenge_level="Level 1")

    config = {"configurable": {"thread_id": "d40fd2f1-8cc7-4c4e-83f3-1e37e44c7a75"}}
    checkpoint_tuple: CheckpointTuple = await get_data_layer().get_checkpointer().aget_tuple(config)

    submission_and_execution_sequence = await get_data_layer().get_complete_agent_execution_sequence(user_identifier="benediktbaeumle@lapiscode.com", thread_id="4f411775-7f3c-42f7-8b59-c9fd10712953")

    print("########### SUBMISSION              ###########")
    pprint(submission)
    print("########### SUBMISSIONS OF USER     ###########")
    pprint(all_submissions_of_user)
    print("########### ALL SUBMISSIONS OF USER ###########")
    pprint(all_submissions_of_user_for_challenge_1)
    print("########### CHECKPOINT TUPLE        ###########")
    pprint(checkpoint_tuple[1]['channel_values']["messages"])
    print("########### EXECUTION GRAPH         ###########")
    # pprint(submission_and_execution_sequence["execution_sequence"])

if __name__ == "__main__":
    asyncio.run(main())
