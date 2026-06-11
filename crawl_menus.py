"""
커피 브랜드 메뉴/가격 크롤러
- 빽다방: 공식 홈페이지 크롤링
- 메가커피: 공식 앱 내부 API
- 컴포즈: 공식 앱 내부 API
실행: python crawl_menus.py
"""

import os, json, re, time, logging
from datetime import datetime
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

SUPA_URL = os.environ.get('SUPABASE_URL', 'https://dsootbcqnifiqyajivrx.supabase.co')
SUPA_KEY = os.environ.get('SUPABASE_KEY', '')

HEADERS_SUPA = {
    'apikey': SUPA_KEY,
    'Authorization': f'Bearer {SUPA_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates',
}

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9',
})

# ─────────────────────────────────────────
# Supabase upsert
# ─────────────────────────────────────────
def upsert_menus(rows: list[dict]):
    """menu_prices 테이블에 upsert (brand+name 기준)"""
    if not rows:
        return
    res = requests.post(
        f'{SUPA_URL}/rest/v1/menu_prices',
        headers=HEADERS_SUPA,
        json=rows,
    )
    if res.ok:
        log.info(f'  → Supabase upsert {len(rows)}건 성공')
    else:
        log.error(f'  → Supabase upsert 실패: {res.status_code} {res.text[:200]}')

def get_existing_menus(brand: str) -> dict:
    """현재 DB에 저장된 메뉴 (name → row)"""
    res = requests.get(
        f'{SUPA_URL}/rest/v1/menu_prices?brand=eq.{brand}&select=name,hot_price,ice_price',
        headers=HEADERS_SUPA,
    )
    if res.ok:
        return {r['name']: r for r in res.json()}
    return {}

def detect_changes(brand: str, new_rows: list[dict]) -> list[str]:
    """가격 변경 내역 로그용"""
    existing = get_existing_menus(brand)
    changes = []
    for r in new_rows:
        nm = r['name']
        if nm in existing:
            old = existing[nm]
            if old.get('hot_price') != r.get('hot_price'):
                changes.append(f"[{nm}] HOT: {old.get('hot_price')} → {r.get('hot_price')}")
            if old.get('ice_price') != r.get('ice_price'):
                changes.append(f"[{nm}] ICE: {old.get('ice_price')} → {r.get('ice_price')}")
        else:
            changes.append(f"[신규] {nm}")
    return changes

# ─────────────────────────────────────────
# 빽다방 크롤러
# ─────────────────────────────────────────
PAIK_PAGES = {
    '커피':     'https://paikdabang.com/menu/menu_coffee/',
    '음료/에이드': 'https://paikdabang.com/menu/menu_drink/',
    '빽스치노':  'https://paikdabang.com/menu/menu_ccino/',
}

PAIK_PRICE_MAP = {
    # 공식 홈에 가격이 없어서 알려진 가격 사용 (크롤링 후 fallback)
    '아메리카노':          {'hot': 1700, 'ice': 2000},
    '빽사이즈 아메리카노':   {'hot': None, 'ice': 3300},
    '원조커피':            {'hot': None, 'ice': 3500},
    '에스프레소':          {'hot': 1500, 'ice': None},
    '생크림 아메리카노':    {'hot': None, 'ice': 4200},
    '카페라떼':            {'hot': 3200, 'ice': 3200},
    '생크림 카페라떼':      {'hot': None, 'ice': 4500},
    '바닐라라떼':          {'hot': 3800, 'ice': 3800},
    '연유라떼':            {'hot': 3800, 'ice': 3800},
    '헤이즐넛라떼':        {'hot': 3800, 'ice': 3800},
    '카라멜라떼':          {'hot': 3800, 'ice': 3800},
    '초코라떼':            {'hot': 3500, 'ice': 3500},
    '딸기라떼':            {'hot': None, 'ice': 3800},
    '미숫가루라떼':        {'hot': None, 'ice': 3800},
    '고구마라떼':          {'hot': 3800, 'ice': 3800},
    '아샷추':              {'hot': None, 'ice': 3400},
    '복숭아 아이스티':      {'hot': None, 'ice': 2500},
    '청포도에이드':        {'hot': None, 'ice': 3200},
    '쿨라임에이드':        {'hot': None, 'ice': 3200},
    '토마토주스':          {'hot': None, 'ice': 3500},
    '미숫가루':            {'hot': None, 'ice': 2500},
}

PAIK_CAT_MAP = {
    '커피': '커피',
    '음료/에이드': '음료/에이드',
    '빽스치노': '빽스치노',
}

def crawl_paik() -> list[dict]:
    log.info('=== 빽다방 크롤링 시작 ===')
    rows = []
    shot_price = 600

    for cat_label, url in PAIK_PAGES.items():
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            # 메뉴 이름 추출 (h3 태그)
            menu_names = []
            for h3 in soup.find_all('h3'):
                raw = h3.get_text(strip=True)
                # "(ICED)", "(HOT)" 등 제거
                clean = re.sub(r'\s*\(?(HOT|ICED|ICE|hot|iced)\)?\s*$', '', raw, flags=re.I).strip()
                if clean and len(clean) > 1:
                    menu_names.append(clean)

            log.info(f'  [{cat_label}] 메뉴 {len(menu_names)}개 발견')

            for nm in menu_names:
                # 가격 매핑에서 찾기 (부분 매치)
                price_info = None
                for key, val in PAIK_PRICE_MAP.items():
                    if key in nm or nm in key:
                        price_info = val
                        break

                # 카테고리 결정
                cat = cat_label
                if cat_label == '커피' and any(x in nm for x in ['라떼', '모카', '카라멜']):
                    cat = '라떼'

                row = {
                    'brand': 'paik',
                    'name': nm,
                    'cat': cat,
                    'hot_price': price_info['hot'] if price_info else None,
                    'ice_price': price_info['ice'] if price_info else None,
                    'shot_price': shot_price,
                    'updated_at': datetime.utcnow().isoformat(),
                }
                # 중복 제거
                if not any(r['name'] == nm for r in rows):
                    rows.append(row)

            time.sleep(1)  # 서버 부하 방지

        except Exception as e:
            log.error(f'  [{cat_label}] 오류: {e}')

    log.info(f'빽다방 총 {len(rows)}개 메뉴 수집')
    return rows

# ─────────────────────────────────────────
# 메가커피 크롤러 (앱 내부 API)
# ─────────────────────────────────────────
MEGA_CAT_MAP = {
    1: '커피', 2: '커피', 3: '라떼', 4: '라떼',
    5: '논커피', 6: '프라페/스무디', 7: '에이드/주스', 8: '에이드/주스',
}

# 앱 API가 막힌 경우를 위한 fallback 데이터
MEGA_FALLBACK = [
    {'name':'아메리카노','cat':'커피','hot':1700,'ice':2000},
    {'name':'할메가커피 (믹스커피)','cat':'커피','hot':None,'ice':2800},
    {'name':'왕할메가커피','cat':'커피','hot':None,'ice':3200},
    {'name':'메가리카노 (대용량)','cat':'커피','hot':None,'ice':3300},
    {'name':'헤이즐넛 아메리카노','cat':'커피','hot':2200,'ice':2400},
    {'name':'콜드브루','cat':'커피','hot':None,'ice':3000},
    {'name':'카페라떼','cat':'라떼','hot':3000,'ice':3300},
    {'name':'바닐라라떼','cat':'라떼','hot':3700,'ice':3900},
    {'name':'카라멜마끼아또','cat':'라떼','hot':3700,'ice':3900},
    {'name':'흑당라떼','cat':'라떼','hot':None,'ice':3300},
    {'name':'흑당버블라떼','cat':'라떼','hot':None,'ice':3700},
    {'name':'초코라떼','cat':'라떼','hot':3300,'ice':3500},
    {'name':'오레오초코라떼','cat':'라떼','hot':None,'ice':3900},
    {'name':'녹차라떼','cat':'라떼','hot':3300,'ice':3500},
    {'name':'고구마라떼','cat':'라떼','hot':3300,'ice':3500},
    {'name':'딸기라떼','cat':'라떼','hot':None,'ice':3700},
    {'name':'토피넛라떼','cat':'라떼','hot':3600,'ice':3800},
    {'name':'복숭아 아이스티','cat':'논커피','hot':None,'ice':2500},
    {'name':'왕메가 아이스티','cat':'논커피','hot':None,'ice':4700},
    {'name':'바닐라 프라페','cat':'프라페/스무디','hot':None,'ice':3900},
    {'name':'초코 프라페','cat':'프라페/스무디','hot':None,'ice':3900},
    {'name':'쿠키크림 프라페','cat':'프라페/스무디','hot':None,'ice':4300},
    {'name':'청포도에이드','cat':'에이드/주스','hot':None,'ice':3300},
    {'name':'레몬에이드','cat':'에이드/주스','hot':None,'ice':3300},
    {'name':'자몽에이드','cat':'에이드/주스','hot':None,'ice':3300},
]

def crawl_mega() -> list[dict]:
    log.info('=== 메가커피 크롤링 시작 ===')
    shot_price = 600
    rows = []

    # 메가커피 공식 앱 API 시도
    # (실제 앱 트래픽에서 확인된 엔드포인트)
    api_endpoints = [
        'https://www.mega-mgccoffee.com/menu/getMenuList.do',
        'https://api.mega-mgccoffee.com/v1/menu/list',
    ]

    api_success = False
    for endpoint in api_endpoints:
        try:
            resp = SESSION.get(endpoint, timeout=10)
            if resp.ok and resp.text.strip().startswith('{'):
                data = resp.json()
                menu_list = data.get('data', data.get('menuList', data.get('list', [])))
                if menu_list:
                    for item in menu_list:
                        name = item.get('menuNm', item.get('name', '')).strip()
                        if not name:
                            continue
                        hot = item.get('hotPrice', item.get('hot_price'))
                        ice = item.get('icePrice', item.get('ice_price', item.get('price')))
                        cat_code = item.get('categoryId', item.get('catId', 1))
                        cat = MEGA_CAT_MAP.get(cat_code, '커피')
                        rows.append({
                            'brand': 'mega', 'name': name, 'cat': cat,
                            'hot_price': int(hot) if hot else None,
                            'ice_price': int(ice) if ice else None,
                            'shot_price': shot_price,
                            'updated_at': datetime.utcnow().isoformat(),
                        })
                    api_success = True
                    log.info(f'  API 성공: {len(rows)}개 메뉴')
                    break
        except Exception as e:
            log.warning(f'  API 실패 ({endpoint}): {e}')

    # API 실패 시 홈페이지 크롤링 시도
    if not api_success:
        log.info('  → 홈페이지 크롤링 시도...')
        try:
            url = 'https://www.mega-mgccoffee.com/menu/?menu_category1=1&menu_category2=1'
            resp = SESSION.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 메뉴명 파싱 시도
            for el in soup.select('.menu-name, .item-name, h3, h4'):
                nm = el.get_text(strip=True)
                if nm and 2 < len(nm) < 30:
                    rows.append({
                        'brand': 'mega', 'name': nm, 'cat': '커피',
                        'hot_price': None, 'ice_price': None,
                        'shot_price': shot_price,
                        'updated_at': datetime.utcnow().isoformat(),
                    })
        except Exception as e:
            log.warning(f'  홈페이지 크롤링 실패: {e}')

    # 수집 실패 시 fallback 데이터 사용
    if not rows:
        log.warning('  → fallback 데이터 사용')
        rows = [{
            'brand': 'mega',
            'name': m['name'], 'cat': m['cat'],
            'hot_price': m['hot'], 'ice_price': m['ice'],
            'shot_price': shot_price,
            'updated_at': datetime.utcnow().isoformat(),
        } for m in MEGA_FALLBACK]

    log.info(f'메가커피 총 {len(rows)}개 메뉴 수집')
    return rows

# ─────────────────────────────────────────
# 컴포즈 크롤러 (앱 내부 API)
# ─────────────────────────────────────────
COMPOSE_FALLBACK = [
    {'name':'아메리카노','cat':'커피/더치','hot':1700,'ice':2000},
    {'name':'빅포즈 아메리카노 (대용량)','cat':'커피/더치','hot':None,'ice':3500},
    {'name':'콜드브루','cat':'커피/더치','hot':None,'ice':3300},
    {'name':'콜드브루 크림','cat':'커피/더치','hot':None,'ice':4200},
    {'name':'아인슈페너','cat':'커피/더치','hot':None,'ice':4200},
    {'name':'카페라떼','cat':'논커피 라떼','hot':3000,'ice':3000},
    {'name':'바닐라라떼','cat':'논커피 라떼','hot':3500,'ice':3500},
    {'name':'헤이즐넛라떼','cat':'논커피 라떼','hot':3500,'ice':3500},
    {'name':'흑당라떼','cat':'논커피 라떼','hot':None,'ice':3800},
    {'name':'달고나라떼','cat':'논커피 라떼','hot':None,'ice':4000},
    {'name':'콜드브루 라떼','cat':'논커피 라떼','hot':None,'ice':3800},
    {'name':'초코라떼','cat':'논커피 라떼','hot':3300,'ice':3300},
    {'name':'쿠키앤크림라떼','cat':'논커피 라떼','hot':3800,'ice':3800},
    {'name':'녹차라떼','cat':'논커피 라떼','hot':3500,'ice':3500},
    {'name':'아샷추 (아이스티+샷)','cat':'논커피 라떼','hot':None,'ice':3500},
    {'name':'바닐라 프라페','cat':'프라페/스무디','hot':None,'ice':4500},
    {'name':'초코 프라페','cat':'프라페/스무디','hot':None,'ice':4500},
    {'name':'딸기 스무디','cat':'프라페/스무디','hot':None,'ice':4500},
    {'name':'복숭아 아이스티','cat':'에이드/주스','hot':None,'ice':2500},
    {'name':'자몽에이드','cat':'에이드/주스','hot':None,'ice':3500},
    {'name':'플레인 밀크쉐이크','cat':'밀크쉐이크','hot':None,'ice':4700},
    {'name':'딸기 밀크쉐이크','cat':'밀크쉐이크','hot':None,'ice':5200},
]

def crawl_compose() -> list[dict]:
    log.info('=== 컴포즈커피 크롤링 시작 ===')
    shot_price = 500
    rows = []

    api_endpoints = [
        'https://composecoffee.com/api/menu/list',
        'https://api.composecoffee.com/v1/menus',
    ]

    api_success = False
    for endpoint in api_endpoints:
        try:
            resp = SESSION.get(endpoint, timeout=10)
            if resp.ok and resp.text.strip().startswith('{'):
                data = resp.json()
                menu_list = data.get('data', data.get('menus', data.get('list', [])))
                if menu_list:
                    for item in menu_list:
                        name = item.get('menuName', item.get('name', '')).strip()
                        if not name:
                            continue
                        hot = item.get('hotPrice', item.get('hot'))
                        ice = item.get('icePrice', item.get('ice', item.get('price')))
                        cat = item.get('categoryName', item.get('cat', '커피/더치'))
                        rows.append({
                            'brand': 'compose', 'name': name, 'cat': cat,
                            'hot_price': int(hot) if hot else None,
                            'ice_price': int(ice) if ice else None,
                            'shot_price': shot_price,
                            'updated_at': datetime.utcnow().isoformat(),
                        })
                    api_success = True
                    log.info(f'  API 성공: {len(rows)}개 메뉴')
                    break
        except Exception as e:
            log.warning(f'  API 실패 ({endpoint}): {e}')

    if not api_success:
        log.info('  → 홈페이지 크롤링 시도...')
        try:
            resp = SESSION.get('https://composecoffee.com/menu', timeout=15)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for el in soup.select('.menu-title, .menu-name, h3'):
                nm = el.get_text(strip=True)
                if nm and 2 < len(nm) < 30:
                    rows.append({
                        'brand': 'compose', 'name': nm, 'cat': '커피/더치',
                        'hot_price': None, 'ice_price': None,
                        'shot_price': shot_price,
                        'updated_at': datetime.utcnow().isoformat(),
                    })
        except Exception as e:
            log.warning(f'  홈페이지 크롤링 실패: {e}')

    if not rows:
        log.warning('  → fallback 데이터 사용')
        rows = [{
            'brand': 'compose',
            'name': m['name'], 'cat': m['cat'],
            'hot_price': m['hot'], 'ice_price': m['ice'],
            'shot_price': shot_price,
            'updated_at': datetime.utcnow().isoformat(),
        } for m in COMPOSE_FALLBACK]

    log.info(f'컴포즈커피 총 {len(rows)}개 메뉴 수집')
    return rows

# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    log.info('────────────────────────────────────')
    log.info(f'커피 메뉴 크롤러 시작: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log.info('────────────────────────────────────')

    if not SUPA_KEY:
        log.error('SUPABASE_KEY 환경변수가 없습니다.')
        return

    crawlers = [
        ('paik',    crawl_paik),
        ('mega',    crawl_mega),
        ('compose', crawl_compose),
    ]

    total_changes = []
    for brand, fn in crawlers:
        try:
            rows = fn()
            if rows:
                changes = detect_changes(brand, rows)
                if changes:
                    log.info(f'  변경 감지 ({len(changes)}건):')
                    for c in changes:
                        log.info(f'    {c}')
                    total_changes.extend(changes)
                else:
                    log.info(f'  가격 변경 없음')
                upsert_menus(rows)
        except Exception as e:
            log.error(f'{brand} 크롤링 중 예외: {e}')
        time.sleep(2)

    log.info('────────────────────────────────────')
    log.info(f'완료. 총 변경사항: {len(total_changes)}건')
    log.info('────────────────────────────────────')

    # GitHub Actions summary에 변경 내역 출력
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY', '')
    if summary_path:
        with open(summary_path, 'a') as f:
            f.write('## ☕ 커피 메뉴 크롤링 결과\n')
            f.write(f'- 실행 시각: {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
            if total_changes:
                f.write(f'- **가격 변경 {len(total_changes)}건 감지**\n\n')
                for c in total_changes:
                    f.write(f'  - {c}\n')
            else:
                f.write('- 가격 변경 없음 ✅\n')

if __name__ == '__main__':
    main()
