"""avatar moderation: history, lock

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('avatar_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('avatar_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_avatar_history_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar_locked_until', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('avatar_locked_forever', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('avatar_locked_forever')
        batch_op.drop_column('avatar_locked_until')

    with op.batch_alter_table('avatar_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_avatar_history_user_id'))

    op.drop_table('avatar_history')
