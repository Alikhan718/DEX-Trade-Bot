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
    settings_table = sa.Table(
        "settings",
        sa.MetaData(),
        sa.Column("name", sa.String),
        sa.Column("slug", sa.String),
        sa.Column("default_value", sa.JSON),
    )

    op.bulk_insert(
        settings_table,
        [
            {
                "name": "AutoBuy",
                "slug": "auto_buy",
                "default_value": {
                    "enabled": True,
                    "type": "buy",
                    "amount_sol": 0.01,
                    "slippage": 5,
                    "max_mc": None,
                    "min_liquidity": None
                },
            },
            {
                "name": "Buy",
                "slug": "buy",
                "default_value": {
                    "gas_fee": 50000,
                    "slippage": 15
                },
            },
            {
                "name": "Sell",
                "slug": "sell",
                "default_value": {
                    "gas_fee": 50000,
                    "slippage": 15
                },
            },
            {
                "name": "Anti-MEV",
                "slug": "anti_mev",
                "default_value": False,
            },
        ],
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM settings 
        WHERE slug in (
            'auto_buy', 
            'buy', 
            'sell', 
            'anti_mev'
        );
    """)
