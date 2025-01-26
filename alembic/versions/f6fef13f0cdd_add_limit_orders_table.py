"""add limit orders table

Revision ID: f6fef13f0cdd
Revises: aac53c29e4f0
Create Date: 2025-01-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f6fef13f0cdd'
down_revision = 'aac53c29e4f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'limit_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_address', sa.String(44), nullable=False),
        sa.Column('amount_sol', sa.Float(), nullable=False),
        sa.Column('trigger_price_usd', sa.Float(), nullable=False),
        sa.Column('trigger_price_percent', sa.Float(), nullable=False),
        sa.Column('slippage', sa.Float(), default=1.0),
        sa.Column('status', sa.String(20), default='active', index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('transaction_hash', sa.String(88), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.Index('ix_limit_orders_user_id', 'user_id'),
        sa.Index('ix_limit_orders_token_address', 'token_address'),
        sa.Index('ix_limit_orders_created_at', 'created_at'),
    )


def downgrade() -> None:
    op.drop_table('limit_orders')
