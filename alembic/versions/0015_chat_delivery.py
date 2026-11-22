"""Add stable request identifiers for chat sends."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("client_id", sa.String(40), nullable=True))
    op.create_index(
        "uq_chat_messages_sender_client",
        "chat_messages",
        ["room_id", "author_id", "client_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_chat_messages_sender_client", table_name="chat_messages")
    op.drop_column("chat_messages", "client_id")
