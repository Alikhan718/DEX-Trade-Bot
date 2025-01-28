"""empty message

Revision ID: 6318251af2c9
Revises: 06606b403fd6, 94184ca71e4a
Create Date: 2025-01-26 19:25:54.417656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6318251af2c9'
down_revision: Union[str, None] = ('06606b403fd6', '94184ca71e4a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
