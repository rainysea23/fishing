#!/usr/bin/env python3
"""지도호 낚시 예약 현황 크롤러 & 캘린더 생성기 & 텔레그램 알림"""

import requests
import urllib3
from bs4 import BeautifulSoup
import json
import re
import os
import sys
import calendar
from datetime import datetime, date, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "http://www.newjidoho.com"
RESERVATION_URL = f"{BASE_URL}/index.php?mid=bk"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
KST = timezone(timedelta(hours=9))

# 내 예약 이름 (파란색 ★ 내예약 표시)
MY_OWN_NAMES = ["류*익", "류*읻"]
# 동행자 이름 (이름 그대로 표시)
COMPANION_NAMES = ["박*교", "이*병"]
# 전체 (하위 호환용)
MY_NAMES = MY_OWN_NAMES + COMPANION_NAMES


def get_korean_holidays():
    """한국 공휴일 반환 (holidays 라이브러리 또는 내장 목록)"""
    try:
        import holidays as holidays_lib
        today = date.today()
        years = [today.year, today.year + 1]
        try:
            kr = holidays_lib.country_holidays("KR", years=years, language="ko")
        except (AttributeError, TypeError):
            try:
                kr = holidays_lib.country_holidays("KR", years=years)
            except AttributeError:
                kr = holidays_lib.KR(years=years)
        return dict(kr)
    except ImportError:
        pass

    # 내장 2025-2026 공휴일
    h = {}
    entries = [
        (2025, 1, 1, "신정"),
        (2025, 1, 28, "설날 전날"), (2025, 1, 29, "설날"), (2025, 1, 30, "설날 다음날"),
        (2025, 3, 1, "삼일절"),
        (2025, 5, 5, "어린이날"), (2025, 5, 6, "어린이날 대체휴일"),
        (2025, 5, 13, "부처님오신날"),
        (2025, 6, 6, "현충일"),
        (2025, 8, 15, "광복절"),
        (2025, 10, 3, "개천절"),
        (2025, 10, 5, "추석 전날"), (2025, 10, 6, "추석"), (2025, 10, 7, "추석 다음날"),
        (2025, 10, 9, "한글날"),
        (2025, 12, 25, "성탄절"),
        (2026, 1, 1, "신정"),
        (2026, 1, 27, "설날 전날"), (2026, 1, 28, "설날"), (2026, 1, 29, "설날 다음날"),
        (2026, 3, 1, "삼일절"),
        (2026, 5, 5, "어린이날"),
        (2026, 5, 24, "부처님오신날"),
        (2026, 6, 6, "현충일"),
        (2026, 8, 15, "광복절"),
        (2026, 9, 24, "추석 전날"), (2026, 9, 25, "추석"), (2026, 9, 26, "추석 다음날"),
        (2026, 10, 3, "개천절"),
        (2026, 10, 9, "한글날"),
        (2026, 12, 25, "성탄절"),
    ]
    for y, m, d, name in entries:
        h[date(y, m, d)] = name
    return h


def is_holiday(d, korean_holidays):
    return d.weekday() >= 5 or d in korean_holidays


def _parse_divs(day_divs):
    """BeautifulSoup div 목록에서 날짜별 데이터 추출"""
    results = {}
    for div in day_divs:
        date_str = div["id"].replace("new-div-", "")
        img = div.find("img", alt=re.compile(r"남은자리|예약완료"))
        remaining = None
        status = "no_data"
        if img:
            alt = img.get("alt", "")
            if "예약완료" in alt:
                remaining = 0
                status = "full"
            else:
                mm = re.search(r"남은자리 (\d+)명", alt)
                if mm:
                    remaining = int(mm.group(1))
                    status = "available" if remaining > 0 else "full"
        elif div.find("a", class_="btn_re"):
            status = "available"
        header_td = div.find("td", {"colspan": "2"})
        header_text = header_td.get_text(strip=True) if header_td else ""
        tide = ""
        if header_text:
            parts = header_text.split(",")
            if len(parts) >= 3:
                tide = parts[2].strip()
        # 예약자 이름 추출 (예약·입금대기 등 전체 텍스트에서)
        all_names = re.findall(r'[\w*]+님', div.get_text())
        my_booking = any(any(name in r for r in all_names) for name in MY_OWN_NAMES)
        companions = [n for n in COMPANION_NAMES if any(n in r for r in all_names)]
        results[date_str] = {"date": date_str, "remaining": remaining, "status": status, "tide": tide, "my_booking": my_booking, "companions": companions}
    return results


def crawl_reservations():
    """6월~10월 전체 예약 현황 크롤링 (순차 + 재시도)"""
    session = requests.Session()
    session.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": BASE_URL,
    }

    today = date.today()
    end_date = date(today.year, 10, 31)

    # 청크 목록 생성 (월별 1·9·17·25일 시작)
    chunks = []
    cur = date(today.year, today.month, 1)
    while cur <= end_date:
        for start_day in [1, 9, 17, 25]:
            if date(cur.year, cur.month, start_day) <= end_date:
                chunks.append((cur.year, cur.month, start_day))
        m = cur.month + 1
        y = cur.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        cur = date(y, m, 1)

    print(f"  총 {len(chunks)}개 청크 순차 크롤링...")
    all_data = {}
    consecutive_failures = 0

    for year, month, start_day in chunks:
        if consecutive_failures >= 3:
            print("  연속 3회 실패 — 사이트 다운으로 판단, 나머지 스킵", file=sys.stderr)
            break
        url = f"{RESERVATION_URL}&year={year}&month={month:02d}&day={start_day:02d}"
        success = False
        for attempt in range(2):  # 실패 시 1회 재시도
            try:
                resp = session.get(url, headers=headers, timeout=(5, 10))
                resp.encoding = "utf-8"
                soup = BeautifulSoup(resp.text, "lxml")
                day_divs = soup.find_all("div", id=re.compile(r"^new-div-\d{8}$"))
                for date_str, info in _parse_divs(day_divs).items():
                    if date_str not in all_data:
                        all_data[date_str] = info
                print(f"  {year}-{month:02d}-{start_day:02d}: {len(day_divs)}일")
                success = True
                break
            except Exception as e:
                print(f"  오류 {year}-{month:02d}-{start_day:02d} (시도{attempt+1}): {e}", file=sys.stderr)
        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            print(f"  {year}-{month:02d}-{start_day:02d}: 스킵")

    print(f"  수집 완료: {len(all_data)}일")
    return all_data


# ─── HTML 생성 ───────────────────────────────────────────────

def gen_month(year, month, today, reservation_data, korean_holidays):
    mn = "1월 2월 3월 4월 5월 6월 7월 8월 9월 10월 11월 12월".split()[month - 1]
    rows = []
    for week in calendar.monthcalendar(year, month):
        cells = []
        for wd, day in enumerate(week):
            if day == 0:
                cells.append("<td></td>")
                continue
            d = date(year, month, day)
            ds = d.strftime("%Y%m%d")
            is_sat = wd == 5
            is_sun_flag = wd == 6
            is_pub = d in korean_holidays
            hname = korean_holidays.get(d, "")

            if d < today:
                cls = "past"
            elif ds in reservation_data:
                st = reservation_data[ds]["status"]
                cls = "avail" if st == "available" else ("full" if st == "full" else "empty")
            else:
                cls = "empty"

            if d == today:
                cls += " today"
            if is_sat:
                cls += " sat"
            if is_sun_flag:
                cls += " sun"
            if is_pub and not is_sun_flag:
                cls += " hday"

            rem = tide = ""
            my_booking = False
            companions = []
            if ds in reservation_data:
                info = reservation_data[ds]
                my_booking = info.get("my_booking", False)
                companions = info.get("companions", [])
                if d >= today:
                    if info["status"] == "full":
                        rem = "마감"
                    elif info["status"] == "available":
                        rem = f"{info['remaining']}명" if info["remaining"] is not None else "가능"
                    tide = info.get("tide", "")

            if my_booking:
                cls += " mine"

            hname_html = f'<span class="hname">{hname}</span>' if hname else ""
            rem_html = f'<span class="rem">{rem}</span>' if rem else ""
            tide_html = f'<span class="tide">{tide}</span>' if tide else ""
            mine_html = '<span class="mybadge">★ 내예약</span>' if my_booking else ""
            companion_html = "".join(f'<span class="companion">{c}</span>' for c in companions)
            link = f"{RESERVATION_URL}&year={year}&month={month:02d}&day={day:02d}&mode=list#list"

            cells.append(
                f'<td><a class="cell {cls}" href="{link}" target="_blank">'
                f'<span class="num">{day}</span>{hname_html}{mine_html}{companion_html}{rem_html}{tide_html}</a></td>'
            )
        rows.append(f'<tr>{"".join(cells)}</tr>')

    return (
        f'<div class="month"><div class="month-title">{year}년 {mn}</div>'
        f'<table><thead><tr>'
        f'<th>월</th><th>화</th><th>수</th><th>목</th><th>금</th>'
        f'<th class="sat">토</th><th class="sun">일</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def generate_html(reservation_data, korean_holidays, last_run_at=None, last_changed_at=None):
    today = date.today()
    now_kst = datetime.now(KST)
    if last_run_at is None:
        last_run_at = now_kst
    if last_changed_at is None:
        last_changed_at = now_kst

    available_holidays = [
        (date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])), info)
        for ds, info in sorted(reservation_data.items())
        if info["status"] == "available"
        and is_holiday(date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])), korean_holidays)
        and date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])) >= today
    ]

    alert_html = ""
    if available_holidays:
        items = []
        for d, info in available_holidays:
            hname = korean_holidays.get(d, "")
            wd = "월화수목금토일"[d.weekday()]
            dtype = "토요일" if d.weekday() == 5 else ("일요일" if d.weekday() == 6 else f"공휴일({hname})")
            rem = f"{info['remaining']}명" if info["remaining"] is not None else "빈자리"
            link = f"{RESERVATION_URL}&year={d.year}&month={d.month:02d}&day={d.day:02d}&mode=list#list"
            items.append(f'<li><a href="{link}" target="_blank">📅 {d.year}년 {d.month}월 {d.day}일 ({wd}) [{dtype}] — 남은자리 {rem}</a></li>')
        alert_html = f'<div class="alert"><h3>🎣 휴일·주말 빈자리 현황</h3><ul>{"".join(items)}</ul></div>'

    months_html = []
    for offset in range(5):
        m = today.month + offset
        y = today.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        months_html.append(gen_month(y, m, today, reservation_data, korean_holidays))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1800">
<title>지도호 낚시 예약 현황</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Malgun Gothic',AppleGothic,sans-serif;background:#eef4eb;padding:12px;color:#333}}
h1{{text-align:center;color:#1a5e0e;font-size:1.5em;margin:10px 0 4px}}
.subtitle{{text-align:center;font-size:.85em;color:#555;margin-bottom:12px}}
.subtitle a{{color:#1a5e0e;text-decoration:none}}
.alert{{background:#fffde7;border:2px solid #f9a825;border-radius:8px;padding:12px 16px;margin:0 auto 16px;max-width:1200px}}
.alert h3{{color:#e65100;margin-bottom:8px;font-size:1em}}
.alert ul{{list-style:none}}
.alert li{{padding:3px 0;font-size:.9em}}
.alert a{{color:#bf360c;font-weight:bold;text-decoration:none}}
.alert a:hover{{text-decoration:underline}}
.months{{display:flex;flex-wrap:wrap;gap:14px;justify-content:center;max-width:1200px;margin:0 auto}}
.month{{background:white;border-radius:10px;padding:14px;box-shadow:0 2px 8px rgba(0,0,0,.1);flex:1;min-width:270px;max-width:360px}}
.month-title{{text-align:center;font-weight:bold;color:#1a5e0e;font-size:1.05em;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse}}
th{{padding:5px 2px;text-align:center;font-size:.78em;color:#666;font-weight:normal}}
th.sat{{color:#1565c0}} th.sun{{color:#b71c1c}}
td{{padding:2px;height:54px;vertical-align:top}}
.cell{{height:100%;border-radius:5px;padding:3px 2px;display:flex;flex-direction:column;align-items:center;cursor:pointer;text-decoration:none;transition:filter .15s}}
.cell:hover{{filter:brightness(.9)}}
.num{{font-weight:bold;font-size:.88em;line-height:1.3}}
.rem{{font-size:.7em;font-weight:bold;margin-top:1px}}
.tide{{font-size:.6em;color:#888;margin-top:1px}}
.hname{{font-size:.58em;color:#c62828;line-height:1.1;margin-top:1px;text-align:center}}
.full{{background:#ffebee}} .full .num{{color:#c62828}} .full .rem{{color:#c62828}}
.avail{{background:#e8f5e9}} .avail .num{{color:#1b5e20}} .avail .rem{{color:#2e7d32}}
.past{{background:#f5f5f5}} .past .num{{color:#bbb}}
.empty{{background:#fafafa}} .empty .num{{color:#888}}
.today{{background:#fff9c4!important;border:2px solid #f9a825!important}}
.today .num{{color:#e65100!important;font-size:1em}}
.mine{{background:#e8eaf6!important;border:2px solid #3949ab!important}}
.mine .num{{color:#1a237e!important}}
.mybadge{{font-size:.6em;background:#3949ab;color:#fff;border-radius:3px;padding:1px 3px;margin-top:2px;font-weight:bold;line-height:1.4}}
.companion{{font-size:.6em;background:#f57c00;color:#fff;border-radius:3px;padding:1px 3px;margin-top:1px;line-height:1.4}}
.sat .num{{color:#1565c0}}
.sun .num{{color:#b71c1c!important}}
.hday .num{{color:#b71c1c!important}}
.legend{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:0 auto 14px;max-width:700px}}
.legend-item{{display:flex;align-items:center;gap:4px;font-size:.78em}}
.dot{{width:12px;height:12px;border-radius:3px;display:inline-block}}
.foot{{text-align:center;color:#999;font-size:.78em;margin-top:14px;padding-bottom:8px}}
@media(max-width:600px){{.month{{min-width:100%}}}}
</style>
</head>
<body>
<h1>🎣 지도호 낚시 예약 현황</h1>
<p class="subtitle"><a href="{RESERVATION_URL}" target="_blank">원본 예약 페이지 바로가기 ↗</a></p>
{alert_html}
<div class="legend">
  <div class="legend-item"><span class="dot" style="background:#e8f5e9;border:1px solid #81c784"></span>예약가능</div>
  <div class="legend-item"><span class="dot" style="background:#ffebee;border:1px solid #ef9a9a"></span>마감</div>
  <div class="legend-item"><span class="dot" style="background:#fff9c4;border:2px solid #f9a825"></span>오늘</div>
  <div class="legend-item"><span class="dot" style="background:#fafafa;border:1px solid #ddd"></span>미정</div>
  <div class="legend-item"><span class="dot" style="background:#e8eaf6;border:2px solid #3949ab"></span>내 예약</div>
</div>
<div class="months">{"".join(months_html)}</div>
<p class="foot">
  🤖 마지막 자동실행: {last_run_at.strftime("%Y년 %m월 %d일 %H:%M")} KST
  &nbsp;|&nbsp;
  📊 데이터 변경: {last_changed_at.strftime("%Y년 %m월 %d일 %H:%M")} KST
  &nbsp;|&nbsp; 1시간마다 자동 갱신 · 30분마다 페이지 새로고침
  <br><a href="https://github.com/rainysea23/fishing/actions" target="_blank" style="color:#aaa;font-size:.9em">GitHub Actions 실행 이력 보기 ↗</a>
</p>
</body>
</html>"""


# ─── 텔레그램 ────────────────────────────────────────────────

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 미설정 — 알림 생략")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        print("Telegram 전송 완료!")
        return True
    except Exception as e:
        print(f"Telegram 오류: {e}", file=sys.stderr)
        return False


def notify_changes(new_data, old_data, korean_holidays):
    """이전 데이터와 비교해 변경된 휴일 날짜만 알림"""
    today = date.today()
    alerts = []

    for ds, new_info in sorted(new_data.items()):
        d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        if d < today or not is_holiday(d, korean_holidays):
            continue

        old_info = old_data.get(ds, {})
        old_status = old_info.get("status", "no_data")
        old_remaining = old_info.get("remaining")
        new_status = new_info["status"]
        new_remaining = new_info.get("remaining")

        hname = korean_holidays.get(d, "")
        wd = "월화수목금토일"[d.weekday()]
        dtype = "토요일" if d.weekday() == 5 else ("일요일" if d.weekday() == 6 else f"공휴일({hname})")
        rem_str = f"{new_remaining}명" if new_remaining is not None else "빈자리"

        # 마감→빈자리: 취소로 자리 생긴 경우
        if old_status == "full" and new_status == "available":
            alerts.append(f"🆕 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] 빈자리 생겼습니다! {rem_str}")

        # 빈자리 증가: 추가 취소
        elif old_status == "available" and new_status == "available":
            if old_remaining is not None and new_remaining is not None and new_remaining > old_remaining:
                alerts.append(f"📈 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] {old_remaining}명→{new_remaining}명으로 증가")

        # 처음 등장한 날짜에 빈자리
        elif old_status == "no_data" and new_status == "available":
            alerts.append(f"📅 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] 예약 오픈! {rem_str}")

    if alerts:
        msg = (
            "🎣 <b>지도호 낚시 빈자리 변경 알림</b>\n\n"
            + "\n".join(alerts)
            + f"\n\n<a href='{RESERVATION_URL}'>👉 예약하러 가기</a>"
        )
        send_telegram(msg)
        print(f"변경 알림 {len(alerts)}건 전송")
    else:
        print("변경 없음 - 알림 생략")


# ─── 메인 ────────────────────────────────────────────────────

def main():
    do_notify = "--notify" in sys.argv or os.environ.get("TELEGRAM_NOTIFY") == "1"

    # 이전 데이터 로드 (변경 감지용)
    old_data_json = {}
    old_reservations = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", encoding="utf-8") as f:
                old_data_json = json.load(f)
                old_reservations = old_data_json.get("reservations", {})
        except Exception:
            pass

    print("크롤링 시작...")
    data = crawl_reservations()
    print(f"수집 완료: {len(data)}일")

    kr_holidays = get_korean_holidays()

    now_kst = datetime.now(KST)
    # 예약 데이터 실제 변경 여부 판단
    reservations_changed = data != old_reservations
    # last_changed_at: 데이터가 바뀌면 지금, 아니면 이전 값 유지
    if reservations_changed or not old_data_json.get("last_changed_at"):
        last_changed_at = now_kst
    else:
        try:
            last_changed_at = datetime.fromisoformat(old_data_json["last_changed_at"])
            if last_changed_at.tzinfo is None:
                last_changed_at = last_changed_at.replace(tzinfo=KST)
        except Exception:
            last_changed_at = now_kst

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_run_at": now_kst.isoformat(),
                "last_changed_at": last_changed_at.isoformat(),
                "updated_at": now_kst.isoformat(),
                "reservations": data,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    html = generate_html(data, kr_holidays, last_run_at=now_kst, last_changed_at=last_changed_at)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")

    # 변경사항 있으면 항상 알림 (수동실행 포함)
    notify_changes(data, old_reservations, kr_holidays)


if __name__ == "__main__":
    main()
