"""Add last_buy_amount to users table

Revision ID: 94184ca71e4a
Revises: aac53c29e4f0
Create Date: 2025-01-23 23:11:35.438805

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94184ca71e4a'
down_revision: Union[str, None] = 'aac53c29e4f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
