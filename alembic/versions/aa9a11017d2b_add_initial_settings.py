"""add_initial_settings

Revision ID: aa9a11017d2b
Revises: 8b3a859d2b76
Create Date: 2025-01-17 03:46:25.877704

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa9a11017d2b'
down_revision: Union[str, None] = '8b3a859d2b76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO settings (name, slug, default_value)
        VALUES (
            'AutoBuy',
            'auto_buy',
            '{"enabled": true, "amount_sol": 0.1, "slippage": 1.0, "max_mc": null, "min_liquidity": null}'::jsonb
        )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM settings WHERE slug = 'auto_buy';
    """)
