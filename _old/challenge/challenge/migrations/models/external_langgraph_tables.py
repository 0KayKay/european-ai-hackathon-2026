from sqlalchemy import Table, MetaData, Column, String, LargeBinary

"""
Not really needed at the moment. But, may be useful.
"""

# Define the metadata
reflected_metadata = MetaData()

# Define LangGraph tables we use in queries
# There are more tables in the database. All LangGraph tables:
# checkpoint_blobs
# checkpoint_migrations
# checkpoint_writes
# checkpoints
# Have one declared demonstratively
checkpoint_blobs = Table(
    'checkpoint_blobs',
    reflected_metadata,
    Column('thread_id', String, primary_key=True),
    Column('checkpoint_ns', String, primary_key=True),
    Column('channel', String, primary_key=True),
    Column('version', String, primary_key=True),
    Column('type', String),
    Column('blob', LargeBinary),
)
