"""add order_type to limit orders

Revision ID: 06606b403fd6
Revises: f6fef13f0cdd
Create Date: 2025-01-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '06606b403fd6'
down_revision: Union[str, None] = 'f6fef13f0cdd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add order_type column
    op.add_column('limit_orders', sa.Column('order_type', sa.String(4), nullable=True))
    
    # Add amount_tokens column
    op.add_column('limit_orders', sa.Column('amount_tokens', sa.Float(), nullable=True))
    
    # Make amount_sol nullable
    op.alter_column('limit_orders', 'amount_sol',
                    existing_type=sa.Float(),
                    nullable=True)
    
    # Update existing orders to be 'buy' type
    op.execute("UPDATE limit_orders SET order_type = 'buy' WHERE order_type IS NULL")
    
    # Make order_type not nullable after setting default value
    op.alter_column('limit_orders', 'order_type',
                    existing_type=sa.String(4),
                    nullable=False)


def downgrade() -> None:
    # Make amount_sol not nullable again
    op.alter_column('limit_orders', 'amount_sol',
                    existing_type=sa.Float(),
                    nullable=False)
    
    # Drop the new columns
    op.drop_column('limit_orders', 'amount_tokens')
    op.drop_column('limit_orders', 'order_type')
