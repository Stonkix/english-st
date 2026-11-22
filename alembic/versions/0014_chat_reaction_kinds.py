"""Allow different reactions while preserving existing hearts."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_reactions") as batch:
        batch.add_column(sa.Column("kind", sa.String(), nullable=False, server_default="heart"))
        batch.create_check_constraint(
            "ck_chat_reaction_kind", "kind IN ('heart', 'like', 'laugh', 'wow', 'sad', 'fire')",
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_reactions") as batch:
        batch.drop_constraint("ck_chat_reaction_kind", type_="check")
        batch.drop_column("kind")
