"""Rename trades.price to trades.price_usd

Revision ID: 0367ffc70a57
Revises: 04ebb38f107a
Create Date: 2025-01-30 04:30:46.746159

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0367ffc70a57'
down_revision: Union[str, None] = '04ebb38f107a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('trades', sa.Column('price_usd', sa.Float(), nullable=False))
    op.drop_column('trades', 'price')
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('trades', sa.Column('price', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False))
    op.drop_column('trades', 'price_usd')
    # ### end Alembic commands ###
