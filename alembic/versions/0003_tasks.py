"""tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 02:08:32.435632
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('pools',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.Column('level', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('student_id', sa.Integer(), nullable=True),
    sa.Column('date', sa.Date(), nullable=False),
    sa.CheckConstraint('(group_id IS NOT NULL) <> (student_id IS NOT NULL)', name='ck_assignment_one_target'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_id'], ['tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'date', name='uq_assignment_group_date'),
    sa.UniqueConstraint('student_id', 'date', name='uq_assignment_student_date')
    )
    op.create_table('attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('test_id', sa.Integer(), nullable=True),
    sa.Column('question_ids', sa.JSON(), nullable=False),
    sa.Column('pair_mistakes', sa.JSON(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('correct_count', sa.Integer(), nullable=False),
    sa.Column('total_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_id'], ['tests.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attempts_date'), ['date'], unique=False)
        batch_op.create_index(batch_op.f('ix_attempts_student_id'), ['student_id'], unique=False)

    op.create_table('daily_settings',
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('question_count', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('shuffle_questions', sa.Boolean(), nullable=False),
    sa.Column('shuffle_options', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id')
    )
    op.create_table('group_pools',
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pool_id'], ['pools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id', 'pool_id')
    )
    op.create_table('questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=True),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('prompt', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('image', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['pool_id'], ['pools.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('attempt_answers',
    sa.Column('attempt_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('answer', sa.JSON(), nullable=True),
    sa.Column('is_correct', sa.Boolean(), nullable=False),
    sa.Column('had_typo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ),
    sa.PrimaryKeyConstraint('attempt_id', 'question_id')
    )
    op.create_table('test_questions',
    sa.Column('test_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['test_id'], ['tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('test_id', 'question_id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('current_streak', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('best_streak', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('last_completed_date', sa.Date(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_completed_date')
        batch_op.drop_column('best_streak')
        batch_op.drop_column('current_streak')

    op.drop_table('test_questions')
    op.drop_table('attempt_answers')
    op.drop_table('questions')
    op.drop_table('group_pools')
    op.drop_table('daily_settings')
    with op.batch_alter_table('attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attempts_student_id'))
        batch_op.drop_index(batch_op.f('ix_attempts_date'))

    op.drop_table('attempts')
    op.drop_table('assignments')
    op.drop_table('tests')
    op.drop_table('pools')
