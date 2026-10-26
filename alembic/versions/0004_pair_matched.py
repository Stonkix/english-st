"""attempt pair_matched: собранные пары match_pairs

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default нужен для уже существующих попыток (SQLite не добавит NOT NULL-колонку без значения)
    with op.batch_alter_table('attempts') as batch:
        batch.add_column(sa.Column('pair_matched', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    with op.batch_alter_table('attempts') as batch:
        batch.drop_column('pair_matched')
