"""add facilities lat lng index

Revision ID: 0a1ef822b958
Revises: d086830beeec
Create Date: 2026-09-14 23:21:23.517290

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a1ef822b958'
down_revision: Union[str, Sequence[str], None] = 'd086830beeec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ix_users_nickname_trgm 관련 줄은 autogenerate가 잘못 잡아낸 것이라 제거함
    # (users.nickname 트라이그램 검색용 GIN 인덱스 - SQLAlchemy 모델엔 없는 raw
    # 인덱스라 autogenerate가 "모델에 없으니 지워야 할 것"으로 오판함, user 도메인
    # 소관이라 이 마이그레이션에서 손대지 않음)
    op.create_index(op.f('ix_facilities_latitude'), 'facilities', ['latitude'], unique=False)
    op.create_index(op.f('ix_facilities_longitude'), 'facilities', ['longitude'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_facilities_longitude'), table_name='facilities')
    op.drop_index(op.f('ix_facilities_latitude'), table_name='facilities')
