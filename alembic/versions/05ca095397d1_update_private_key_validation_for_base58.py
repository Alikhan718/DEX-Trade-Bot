"""update_private_key_validation_for_base58

Revision ID: 05ca095397d1
Revises: 4afad4367709
Create Date: 2025-02-13 11:51:50.807857

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05ca095397d1'
down_revision: Union[str, None] = '4afad4367709'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
