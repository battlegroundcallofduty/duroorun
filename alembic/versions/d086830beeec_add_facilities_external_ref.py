"""add facilities.external_ref + admin override locks (courses.is_admin_managed,
facilities.is_admin_edited, course_facility.is_excluded)

Revision ID: d086830beeec
Revises: 9cc45abd03d1
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd086830beeec'
down_revision: Union[str, Sequence[str], None] = '9cc45abd03d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('facilities', sa.Column('external_ref', sa.String(), nullable=True))
    op.create_unique_constraint('uq_facility_external_ref', 'facilities', ['external_ref'])
    op.add_column(
        'courses',
        sa.Column('is_admin_managed', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'facilities',
        sa.Column('is_admin_edited', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'course_facility',
        sa.Column('is_excluded', sa.Boolean(), server_default='false', nullable=False),
    )
    # 이미 비활성화된 시설은 (자동 비활성화 경로가 없으므로) 전부 관리자가 수동으로
    # 비활성화한 것 — 배포 시점부터 바로 재시드 덮어쓰기 대상에서 제외되도록 백필.
    # courses는 시스템이 스스로 비활성화하는 경로(_deactivate_missing_courses)가 있어
    # 똑같이 백필하면 안 됨 — is_admin_managed는 기본값 false로 둔다.
    op.execute("UPDATE facilities SET is_admin_edited = true WHERE is_active = false")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('course_facility', 'is_excluded')
    op.drop_column('facilities', 'is_admin_edited')
    op.drop_column('courses', 'is_admin_managed')
    op.drop_constraint('uq_facility_external_ref', 'facilities', type_='unique')
    op.drop_column('facilities', 'external_ref')
