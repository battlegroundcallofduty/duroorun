import { useEffect, useRef, useState } from 'react';

import { loadKakaoMaps } from '../../lib/kakaoMaps';

// 강원 해안 대략 중앙(강릉 인근) — path/markers가 아직 없을 때만 쓰는 기본 좌표
const _FALLBACK_CENTER = { lat: 37.75, lng: 128.9 };
const _DEFAULT_LEVEL = 6;

// marker.color가 있으면 기본 카카오 핀 대신 이 색 원형 마커 사용.
// (같은 색+크기는 재사용 - 이미지 매번 새로 안 만듦)
const _markerImageCache = new Map();
const _buildMarkerImage = (kakao, color, size) => {
  const cacheKey = `${color}:${size}`;
  if (!_markerImageCache.has(cacheKey)) {
    const half = size / 2;
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">` +
      `<circle cx="${half}" cy="${half}" r="${half - 2}" fill="${color}" stroke="#fff" stroke-width="2"/></svg>`;
    _markerImageCache.set(
      cacheKey,
      new kakao.maps.MarkerImage(`data:image/svg+xml;base64,${btoa(svg)}`, new kakao.maps.Size(size, size), {
        offset: new kakao.maps.Point(half, half),
      })
    );
  }
  return _markerImageCache.get(cacheKey);
};

/**
 * 재사용 가능한 카카오맵 컴포넌트.
 *
 * 두 가지 방식으로 씀:
 * - 읽기 전용(코스 상세): `path`로 경로선을, `markers`로 시작/종료·편의시설 마커를 표시
 * - 편집 가능(커스텀 코스 생성/수정): `editable`+`onMapClick`으로 지도 클릭 시 경유지 추가.
 *   `path`가 곧 지금까지 찍은 경유지 목록 — 순서 관리(삭제/재정렬)는 이 컴포넌트 밖(부모)에서.
 *
 * 주의: DRNB 코스는 시작/종료 좌표만 있고 전체 경로 좌표가 없음
 * - `path`에 좌표 2개만 넘기면 실제 트레일과 무관한 직선이 그려지므로,
 * - DRNB는 `path` 없이 `markers`만 쓸 것.
 *
 * markers 각 항목은 `{ lat, lng, label?, color?, size?, url?, name? }`
 * color 없으면 기본 카카오 핀, 주면 그 색 원형 마커(프론트 보고 수정).
 * url을 주면 마커 클릭 시 그 주소를 새 탭으로 엶(편의시설의 카카오맵 상세)
 * name을 주면 마우스를 올렸을 때만 그 이름을 작은 말풍선으로
 * (한 번에 하나만 보이게, 다른 마커에 올리면 이전것 닫음.)
 */
const KakaoMap = ({
  path = [],          // 선(순서 있는 점들)
  markers = [],       // 핀(점)
  editable = false,   // true: 지도 클릭으로 경유지 추가 모드
  onMapClick,         // 클릭한 좌표 부모에게 알려주는 콜백
  height = '360px',
  emptyHint,          // 아무것도 안 찍었을때 안내문구
  initialCenter,      // 지도 처음 열때 중심 어디 보여줄지
  center,             // 마운트 이후에도 지도 중심을 옮기고 싶을 때(예: GPS 응답 도착) — path/markers가 비어있을 때만 적용
}) => {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const kakaoRef = useRef(null); // loadKakaoMaps()가 resolve한 kakao 객체
  const overlaysRef = useRef([]); // 매 렌더마다 지우고 다시 그리는 폴리라인/마커/오버레이 전부
  const hoverOverlayRef = useRef(null); // 지금 떠있는 호버 미리보기 오버레이 - 한번에 하나만
  const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
  const [retryToken, setRetryToken] = useState(0);
  // [현재값, 값바꾸는함수]: 밑에서 버튼 누르면 n+1 시켜서 1로 바뀌고 effect 재실행

  // 지도 인스턴스는 마운트 시(+재시도 시) 한 번만 생성.
  // initialCenter는 이 시점 값만 씀(이후 바뀌어도 재초기화 X)
  // — path/markers가 아직 없는 생성 폼에서 유저 GPS 위치로 초기 화면만 잡아주는 용도
  useEffect(() => {  // useEffect: 화면이 그려진 다음 실행할 부수작업 등록하는것
    let cancelled = false;
    setStatus('loading');

    loadKakaoMaps()  // 지도 심기
      .then((kakao) => {
        if (cancelled || !containerRef.current) return;
        kakaoRef.current = kakao;
        const center = initialCenter ?? _FALLBACK_CENTER;
        mapRef.current = new kakao.maps.Map(containerRef.current, {
          center: new kakao.maps.LatLng(center.lat, center.lng),
          level: _DEFAULT_LEVEL,
        });
        setStatus('ready');
      })
      .catch((err) => {
        console.error('카카오맵 SDK 로드 실패:', err);
        if (!cancelled) setStatus('error');
      });

    return () => {
      cancelled = true;
      mapRef.current = null;
    };
    // 밑 해석: effect 안에서 쓰는 값인데 의도적으로 배열에 안 넣었으니 무시해라
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryToken]);

  // path/markers가 바뀔 때마다 기존 오버레이 지우고 다시 그림 + 화면에 다 보이도록 범위 조정
  useEffect(() => {
    if (status !== 'ready') return;
    const kakao = kakaoRef.current;
    const map = mapRef.current;

    overlaysRef.current.forEach((overlay) => overlay.setMap(null));
    overlaysRef.current = [];
    // markers가 바뀌면 그 아래 깔려있던 마커도 다시 그려지므로, 이전 렌더에서 열려있던
    // 호버 미리보기가 이제는 존재하지 않는 마커를 가리키게 됨 (고아 오버레이 방지)
    if (hoverOverlayRef.current) {
      hoverOverlayRef.current.setMap(null);
      hoverOverlayRef.current = null;
    }

    const allPoints = [...path, ...markers];
    if (allPoints.length === 0) return;

    if (path.length >= 2) {
      const polyline = new kakao.maps.Polyline({
        map,
        path: path.map((p) => new kakao.maps.LatLng(p.lat, p.lng)),
        strokeWeight: 4,
        strokeColor: '#005baa',
        strokeOpacity: 0.85,
      });
      overlaysRef.current.push(polyline);
    }

    if (editable) {
      // 편집 모드에서는 순서가 중요하니 번호가 보이는 커스텀 오버레이로 경유지 표시
      path.forEach((p, index) => {
        const el = document.createElement('div');
        el.className = 'kakao-map-waypoint-badge';
        el.textContent = String(index + 1);
        const overlay = new kakao.maps.CustomOverlay({
          map,
          position: new kakao.maps.LatLng(p.lat, p.lng),
          content: el,
        });
        overlaysRef.current.push(overlay);
      });
    }
    // 읽기 전용일 때는 path 좌표마다 마커를 찍지 않음
    // — 시작/종료 등 표시하고 싶은 지점은 호출부가 markers prop으로 명시적으로 넘길것

    markers.forEach((m) => {
      const marker = new kakao.maps.Marker({
        map,
        position: new kakao.maps.LatLng(m.lat, m.lng),
        image: m.color ? _buildMarkerImage(kakao, m.color, m.size ?? 18) : undefined,
        // clickable: true를 줘야 이 마커 위에서 커서가 포인터로 바뀜(호버 힌트) +
        // 클릭 이벤트를 마커가 먼저 받고 거기서 끝내야 경유지 추가 등으로 안 샘.
        clickable: Boolean(m.url),
      });
      overlaysRef.current.push(marker);

      // url이 있으면(예: 편의시설 카카오맵 상세) 클릭 시 새 탭으로 엶.
      // 마커 자체가 지도와 함께 정리되므로 리스너를 따로 해제할 필요 X
      if (m.url) {
        kakao.maps.event.addListener(marker, 'click', () => {
          window.open(m.url, '_blank', 'noopener,noreferrer');
        });
      }

      if (m.label) {
        // innerHTML 문자열이 아니라 DOM 요소를 직접 넘겨서 label에 악성 마크업이 섞여
        // 들어와도(예: 나중에 편의시설 이름을 여기 쓰게 될 경우) 실행되지 않고 텍스트로만 보이게 함
        const el = document.createElement('div');
        el.className = 'kakao-map-label';
        el.textContent = m.label;
        const overlay = new kakao.maps.CustomOverlay({
          map,
          position: new kakao.maps.LatLng(m.lat, m.lng),
          content: el,
          yAnchor: 2.2,
        });
        overlaysRef.current.push(overlay);
      }

      // name이 있으면(편의시설 이름 등) 마우스 올렸을 때만 작은 말풍선으로.
      // label과 달리 항상 떠있는 게 아니라 그때그때
      // hoverOverlayRef 하나로 관리(한 번에 하나만 보이게)
      if (m.name) {
        const hoverEl = document.createElement('div');
        hoverEl.className = 'kakao-map-hover-label';
        hoverEl.textContent = m.name;
        const hoverOverlay = new kakao.maps.CustomOverlay({
          position: new kakao.maps.LatLng(m.lat, m.lng),
          content: hoverEl,
          yAnchor: 1.6,
        });
        kakao.maps.event.addListener(marker, 'mouseover', () => {
          // 이전 마커에서 떠 있던 미리보기 먼저 닫기 - 여러 개가 동시에 남지 않도록
          if (hoverOverlayRef.current) hoverOverlayRef.current.setMap(null);
          hoverOverlay.setMap(map);
          hoverOverlayRef.current = hoverOverlay;
        });
        kakao.maps.event.addListener(marker, 'mouseout', () => {
          hoverOverlay.setMap(null);
          if (hoverOverlayRef.current === hoverOverlay) hoverOverlayRef.current = null;
        });
      }
    });

    if (allPoints.length === 1) {
      // 중심만 옮기고 줌은 안 건드림 - 프론트 테스트하며 UX 개선
      // 지도 처음 열릴 때의 기본 줌(_DEFAULT_LEVEL)은 map 생성 시점에만 적용
      map.setCenter(new kakao.maps.LatLng(allPoints[0].lat, allPoints[0].lng));
    } else if (!editable) {
      // 읽기 전용(코스 상세)에서는 완성 경로 전체가 한 화면에 들어오게 자동 맞춤
      const bounds = new kakao.maps.LatLngBounds();
      allPoints.forEach((p) => bounds.extend(new kakao.maps.LatLng(p.lat, p.lng)));
      map.setBounds(bounds);
    }
    // editable + 점 2개 이상(경유지 추가 중)인 경우는 화면 아예 안 건드림
    // ㅡ 프론트 테스트해보니 매번 줌아웃되는게 경유지 찍는 과정에서 불편.
    // path/markers는 매 렌더마다 새 배열/객체로 넘어올 수 있음.
    // — 좌표 내용을 문자열화해 실제 값이 바뀔 때만 다시 그리도록
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, editable, JSON.stringify(path), JSON.stringify(markers)]);

  // 컴포넌트가 언마운트될 때(페이지 이동 등) 지도에 남아있는 오버레이를 정리
  // ㅡ 안하면 죽은 kakao.maps 인스턴스 참조가 계속 쌓일 수 있음
  useEffect(() => {
    return () => {
      overlaysRef.current.forEach((overlay) => overlay.setMap(null));
    };
  }, []);

  // center prop이 바뀌면 지도 중심만 이동 (initialCenter와 달리 마운트 이후에도 반영됨)
  // ㅡ 이미 path/markers가 있으면 위 effect의 bounds 조정과 충돌하니 비어있을 때만 적용
  useEffect(() => {
    if (status !== 'ready' || !center) return;
    if (path.length > 0 || markers.length > 0) return;
    mapRef.current.setCenter(new kakaoRef.current.maps.LatLng(center.lat, center.lng));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, center?.lat, center?.lng]);

  // 편집 모드 클릭 핸들러 (지도 클릭시 좌표를 부모에게 넘김)
  useEffect(() => {
    if (status !== 'ready' || !editable || !onMapClick) return;
    const kakao = kakaoRef.current;
    const map = mapRef.current;

    const listener = (mouseEvent) => {
      const latlng = mouseEvent.latLng;
      onMapClick({ lat: latlng.getLat(), lng: latlng.getLng() });
    };
    kakao.maps.event.addListener(map, 'click', listener);

    return () => {
      kakao.maps.event.removeListener(map, 'click', listener);
    };
  }, [status, editable, onMapClick]);

  return (
    <div className="kakao-map-wrapper">
      <div ref={containerRef} className="kakao-map" style={{ height }} />
      {status === 'loading' && <p className="kakao-map-status">지도를 불러오는 중...</p>}
      {status === 'error' && (
        <p className="kakao-map-status error">
          지도를 불러오지 못했어요.{' '}
          <button type="button" onClick={() => setRetryToken((n) => n + 1)}>
            다시 시도
          </button>
        </p>
      )}
      {status === 'ready' && editable && path.length === 0 && emptyHint && (
        <p className="kakao-map-hint">{emptyHint}</p>  // 경유지 없으면 emptyHint
      )}
    </div>
  );
};

export default KakaoMap;
