import { useCallback, useState } from 'react';

import { apiFetch } from '../../api';
import { useAdminPagedList } from '../../hooks/useAdminPagedList';
import KakaoMap from '../map/KakaoMap';
import AdminPagination from './AdminPagination';

// 위도/경도 입력창이 빈 문자열이면 Number('')이 NaN이 아니라 0이라 그냥 넘기면 안 됨
// ㅡ 값이 있는지부터 확인한 뒤에 숫자로 변환
const parseCoord = (value) => (value.trim() === '' ? NaN : Number(value));

// 위도/경도 입력창 값(문자열)이 둘 다 유효한 숫자일 때만 지도에 찍을 마커 하나로 변환
const toMarker = (form) => {
  const lat = parseCoord(form.latitude);
  const lng = parseCoord(form.longitude);
  return Number.isNaN(lat) || Number.isNaN(lng) ? [] : [{ lat, lng }];
};

// FastAPI 422 응답의 detail은 문자열이 아니라 필드별 검증 에러 객체 배열로 옴
// ㅡ 그 배열을 그대로 state에 넣고 jsx에 렌더하면 화면 전체 하얀 크래시 버그 생김.
// CustomCourseForm.jsx의 _extractErrorMessage와 같은 방식으로
// 항상 안전한 문자열로 변환.
const _extractErrorMessage = (detail, fallback) => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item?.msg) return null;
        const field = item.loc?.at(-1);
        const msg = item.msg.replace(/^Value error,\s*/, '');
        return typeof field === 'string' ? `${field}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join(' / ');
  }
  return fallback;
};

const PAGE_SIZE = 20;
const FACILITY_TYPE_LABEL = { RESTROOM: '화장실', PARKING: '주차장', LOCKER: '보관함', OTHERS: '기타' };
const EMPTY_FORM = {
  facility_type: 'RESTROOM',
  facility_name: '',
  facility_address: '',
  latitude: '',
  longitude: '',
  kakao_place_id: '',
};

const toEditForm = (facility) => ({
  facility_type: facility.facility_type,
  facility_name: facility.facility_name,
  facility_address: facility.facility_address ?? '',
  latitude: String(facility.latitude),
  longitude: String(facility.longitude),
  kakao_place_id: facility.kakao_place_id ?? '',
});

// 관리자 - 편의시설 등록/수정/활성화-비활성화. 모든 편의시설 관리 가능.
const FacilityManagement = () => {
  const [facilityType, setFacilityType] = useState('');
  const [activeFilter, setActiveFilter] = useState(''); // '':전체/'true':활성/'false':비활성
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [createForm, setCreateForm] = useState(EMPTY_FORM);
  const [createError, setCreateError] = useState('');
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [editError, setEditError] = useState('');
  const [saving, setSaving] = useState(false);
  const [togglingId, setTogglingId] = useState(null);
  const [toggleError, setToggleError] = useState('');

  const buildQuery = useCallback(
    (targetPage, size = PAGE_SIZE) => {
      const params = new URLSearchParams({ page: targetPage, size });
      if (facilityType) params.set('facility_type', facilityType);
      if (activeFilter) params.set('is_active', activeFilter);
      if (keyword) params.set('keyword', keyword);
      return params.toString();
    },
    [facilityType, activeFilter, keyword]
  );

  const {
    items: facilities,
    total,
    page,
    loading,
    error,
    goToPage,
    reload,
  } = useAdminPagedList('/v1/facilities/admin', buildQuery, [facilityType, activeFilter, keyword]);

  const handleSearchSubmit = (event) => {
    event.preventDefault();
    setKeyword(keywordInput.trim());
  };

  const handleCreateSubmit = async (event) => {
    event.preventDefault();
    setCreateError('');
    const latitude = parseCoord(createForm.latitude);
    const longitude = parseCoord(createForm.longitude);
    if (!createForm.facility_name.trim() || Number.isNaN(latitude) || Number.isNaN(longitude)) {
      setCreateError('시설명과 좌표(위도/경도)는 필수예요.');
      return;
    }
    setCreating(true);
    try {
      const res = await apiFetch('/v1/facilities', {
        method: 'POST',
        body: JSON.stringify({
          facility_type: createForm.facility_type,
          facility_name: createForm.facility_name.trim(),
          facility_address: createForm.facility_address.trim() || null,
          latitude,
          longitude,
          kakao_place_id: createForm.kakao_place_id.trim() || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setCreateError(_extractErrorMessage(data?.detail, '편의시설 등록에 실패했어요.'));
        return;
      }
      setCreateForm(EMPTY_FORM);
      // 목록은 facility_id desc 정렬이라 새로 등록한 시설은 항상 1페이지
      const reloaded = await goToPage(1);
      if (!reloaded) setCreateError('등록은 됐지만 목록을 새로고침하지 못했어요. 새로고침 해주세요.');
    } catch (err) {
      console.error('편의시설 등록 실패:', err);
      setCreateError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (facility) => {
    setEditingId(facility.facility_id);
    setEditForm(toEditForm(facility));
    setEditError('');
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditError('');
  };

  const handleEditSubmit = async (event, facilityId) => {
    event.preventDefault();
    setEditError('');
    const latitude = parseCoord(editForm.latitude);
    const longitude = parseCoord(editForm.longitude);
    if (!editForm.facility_name.trim() || Number.isNaN(latitude) || Number.isNaN(longitude)) {
      setEditError('시설명과 좌표(위도/경도)는 필수예요.');
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch(`/v1/facilities/${facilityId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          facility_type: editForm.facility_type,
          facility_name: editForm.facility_name.trim(),
          facility_address: editForm.facility_address.trim() || null,
          latitude,
          longitude,
          kakao_place_id: editForm.kakao_place_id.trim() || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setEditError(_extractErrorMessage(data?.detail, '편의시설 수정에 실패했어요.'));
        return;
      }
      setEditingId(null);
      const reloaded = await reload();
      if (!reloaded) setEditError('수정은 됐지만 목록을 새로고침하지 못했어요. 새로고침 해주세요.');
    } catch (err) {
      console.error('편의시설 수정 실패:', err);
      setEditError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActive = async (facility) => {
    const nextActive = !facility.is_active;
    if (
      !window.confirm(nextActive ? '이 편의시설을 다시 활성화할까요?' : '이 편의시설을 비활성화할까요?')
    ) {
      return;
    }
    setToggleError('');
    setTogglingId(facility.facility_id);
    try {
      const res = await apiFetch(`/v1/facilities/${facility.facility_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: nextActive }),
      });
      if (!res.ok) {
        setToggleError('편의시설 상태 변경에 실패했어요.');
        return;
      }
      const reloaded = await reload();
      if (!reloaded) {
        setToggleError('변경은 됐지만 목록을 새로고침하지 못했어요. 새로고침 해주세요.');
      }
    } catch (err) {
      console.error('편의시설 상태 변경 실패:', err);
      setToggleError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <section className="admin-section">
      <h2>편의시설 관리 ({total}건)</h2>
      <p className="admin-section-hint">
        화장실/주차장/편의점(편의점은 기타에 해당)은 카카오 검색으로 자동 등록되고 여기서 수정·비활성화할 수 있어요.
        보관함은 자동 등록 소스가 없어 아래 폼으로 직접 등록해주세요.
      </p>

      <form className="admin-facility-form" onSubmit={handleCreateSubmit}>
        <select
          value={createForm.facility_type}
          onChange={(event) => setCreateForm((prev) => ({ ...prev, facility_type: event.target.value }))}
        >
          {Object.entries(FACILITY_TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="시설명"
          value={createForm.facility_name}
          onChange={(event) => setCreateForm((prev) => ({ ...prev, facility_name: event.target.value }))}
        />
        <input
          type="text"
          placeholder="주소 (선택)"
          value={createForm.facility_address}
          onChange={(event) => setCreateForm((prev) => ({ ...prev, facility_address: event.target.value }))}
        />
        <input
          type="text"
          inputMode="decimal"
          placeholder="위도"
          value={createForm.latitude}
          onChange={(event) => setCreateForm((prev) => ({ ...prev, latitude: event.target.value }))}
        />
        <input
          type="text"
          inputMode="decimal"
          placeholder="경도"
          value={createForm.longitude}
          onChange={(event) => setCreateForm((prev) => ({ ...prev, longitude: event.target.value }))}
        />
        <button type="submit" className="primary-button" disabled={creating}>
          {creating ? '등록 중...' : '등록'}
        </button>
        <KakaoMap
          editable
          markers={toMarker(createForm)}
          onMapClick={({ lat, lng }) =>
            setCreateForm((prev) => ({ ...prev, latitude: String(lat), longitude: String(lng) }))
          }
          height="260px"
          emptyHint="지도를 클릭해서 위치를 찍으면 위도/경도가 자동으로 채워져요"
        />
      </form>
      {createError && <p className="course-list-status error">{createError}</p>}

      {loading && <p className="course-list-status">불러오는 중...</p>}
      {error && <p className="course-list-status error">{error}</p>}
      {toggleError && <p className="course-list-status error">{toggleError}</p>}

      <form className="admin-search-form" onSubmit={handleSearchSubmit}>
        <select value={facilityType} onChange={(event) => setFacilityType(event.target.value)}>
          <option value="">전체 시설</option>
          {Object.entries(FACILITY_TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <select value={activeFilter} onChange={(event) => setActiveFilter(event.target.value)}>
          <option value="">상태 전체</option>
          <option value="true">활성만</option>
          <option value="false">비활성만</option>
        </select>
        <input
          type="text"
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          placeholder="시설명 검색"
        />
        <button type="submit" className="primary-button">검색</button>
      </form>

      {!loading && !error && facilities.length === 0 && (
        <p className="course-list-status">등록된 편의시설이 없어요.</p>
      )}

      {!loading && !error && facilities.length > 0 && (
        <ul className="admin-banned-list">
          {facilities.map((facility) =>
            editingId === facility.facility_id ? (
              <li key={facility.facility_id} className="admin-banned-item admin-facility-editing">
                <form
                  className="admin-facility-form"
                  onSubmit={(event) => handleEditSubmit(event, facility.facility_id)}
                >
                  <select
                    value={editForm.facility_type}
                    onChange={(event) => setEditForm((prev) => ({ ...prev, facility_type: event.target.value }))}
                  >
                    {Object.entries(FACILITY_TYPE_LABEL).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={editForm.facility_name}
                    onChange={(event) => setEditForm((prev) => ({ ...prev, facility_name: event.target.value }))}
                  />
                  <input
                    type="text"
                    value={editForm.facility_address}
                    onChange={(event) => setEditForm((prev) => ({ ...prev, facility_address: event.target.value }))}
                  />
                  <input
                    type="text"
                    inputMode="decimal"
                    value={editForm.latitude}
                    onChange={(event) => setEditForm((prev) => ({ ...prev, latitude: event.target.value }))}
                  />
                  <input
                    type="text"
                    inputMode="decimal"
                    value={editForm.longitude}
                    onChange={(event) => setEditForm((prev) => ({ ...prev, longitude: event.target.value }))}
                  />
                  <button type="submit" className="primary-button" disabled={saving}>
                    {saving ? '저장 중...' : '저장'}
                  </button>
                  <button type="button" onClick={cancelEdit} disabled={saving}>취소</button>
                  <KakaoMap
                    editable
                    markers={toMarker(editForm)}
                    onMapClick={({ lat, lng }) =>
                      setEditForm((prev) => ({ ...prev, latitude: String(lat), longitude: String(lng) }))
                    }
                    height="260px"
                    emptyHint="지도를 클릭해서 위치를 찍으면 위도/경도가 자동으로 채워져요"
                  />
                </form>
                {editError && <p className="course-list-status error">{editError}</p>}
              </li>
            ) : (
              <li key={facility.facility_id} className="admin-banned-item">
                <div>
                  <strong>{facility.facility_name}</strong>
                  <span>{FACILITY_TYPE_LABEL[facility.facility_type] ?? facility.facility_type}</span>
                  {facility.facility_address && <span>{facility.facility_address}</span>}
                  <span className={facility.is_active ? 'admin-badge-active' : 'admin-badge-inactive'}>
                    {facility.is_active ? '활성' : '비활성'}
                  </span>
                  {facility.is_admin_edited && (
                    <span title="관리자가 직접 비활성화한 시설 - 다시 활성화하기 전까지 자동 재시드가 되살리지 않아요">
                      🔒 관리자 지정
                    </span>
                  )}
                </div>
                <div className="admin-facility-actions">
                  <button type="button" className="admin-unban-button" onClick={() => startEdit(facility)}>
                    수정
                  </button>
                  <button
                    type="button"
                    className="admin-unban-button"
                    onClick={() => handleToggleActive(facility)}
                    disabled={togglingId === facility.facility_id}
                  >
                    {togglingId === facility.facility_id
                      ? '처리 중...'
                      : facility.is_active
                        ? '비활성화'
                        : '활성화'}
                  </button>
                </div>
              </li>
            )
          )}
        </ul>
      )}

      {!loading && !error && (
        <AdminPagination page={page} total={total} size={PAGE_SIZE} onPageChange={goToPage} disabled={loading} />
      )}
    </section>
  );
};

export default FacilityManagement;
