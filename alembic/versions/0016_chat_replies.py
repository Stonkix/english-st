"""Link chat replies to the original message."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        # A batch copy would drop the old table and cascade to existing reactions.
        op.execute("ALTER TABLE chat_messages ADD COLUMN reply_to_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL")
    else:
        op.add_column("chat_messages", sa.Column("reply_to_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_chat_message_reply", "chat_messages", "chat_messages", ["reply_to_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index("ix_chat_messages_reply_to_id", "chat_messages", ["reply_to_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_reply_to_id", table_name="chat_messages")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_chat_message_reply", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "reply_to_id")
