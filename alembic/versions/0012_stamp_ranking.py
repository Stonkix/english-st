"""Set ranking point values on existing standard stamp types."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stamp_types", sa.Column("ranking_points", sa.Integer(), server_default=sa.text("0"), nullable=False))
    stamp_types = sa.table("stamp_types", sa.column("name"), sa.column("ranking_points"))
    weights = {"Легендарный": 25, "Legendary": 25, "Редкий": 10, "Rare": 10, "Обычный": 1, "Common": 1}
    op.execute(
        stamp_types.update()
        .where(stamp_types.c.name.in_(weights))
        .values(ranking_points=sa.case(weights, value=stamp_types.c.name))
    )


def downgrade() -> None:
    op.drop_column("stamp_types", "ranking_points")
