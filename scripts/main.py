#!/usr/bin/env python3
"""지도호·라온호 낚시 예약 현황 크롤러 & 캘린더 생성기 & 텔레그램 알림"""

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

JIDO_BASE_URL = "http://www.newjidoho.com"
JIDO_URL      = f"{JIDO_BASE_URL}/index.php?mid=bk"
RAON_BASE_URL = "http://www.raonfishing.com"
RAON_URL      = f"{RAON_BASE_URL}/index.php?mid=bk"
# 하위 호환
BASE_URL = JIDO_BASE_URL
RESERVATION_URL = JIDO_URL

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
KST = timezone(timedelta(hours=9))

# 내 예약 이름 (파란색 ★ 내예약 표시)
MY_OWN_NAMES = ["류*익", "류*읻"]
# 동행자 이름 (이름 그대로 표시)
COMPANION_NAMES = ["박*교", "이*병"]
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
    """BeautifulSoup div 목록에서 날짜별 데이터 추출 (지도호·라온호 공통)"""
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
        # 예약·입금대기 행에서만 이름 추출 (취소 포함 행 제외)
        active_rows = [
            row for row in div.find_all("tr")
            if "취소" not in row.get_text()
            and ("예약" in row.get_text() or "입금대기" in row.get_text())
        ]
        active_text = " ".join(row.get_text() for row in active_rows) if active_rows else ""
        all_names = re.findall(r'[\w*]+님', active_text)
        my_booking = any(any(name in r for r in all_names) for name in MY_OWN_NAMES)
        companions = [n for n in COMPANION_NAMES if any(n in r for r in all_names)]
        results[date_str] = {
            "date": date_str, "remaining": remaining, "status": status,
            "tide": tide, "my_booking": my_booking, "companions": companions,
        }
    return results


def _crawl_site(base_url, reservation_url, label=""):
    """범용 크롤러 — 월별 1·9·17·25일 청킹, 실패 시 1회 재시도"""
    session = requests.Session()
    session.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": base_url,
    }

    today = date.today()
    end_date = date(today.year, 11, 15)

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

    tag = f"[{label}] " if label else ""
    print(f"  {tag}총 {len(chunks)}개 청크 크롤링...")
    all_data = {}
    consecutive_failures = 0

    for year, month, start_day in chunks:
        if consecutive_failures >= 3:
            print(f"  {tag}연속 3회 실패 — 사이트 다운으로 판단, 나머지 스킵", file=sys.stderr)
            break
        url = f"{reservation_url}&year={year}&month={month:02d}&day={start_day:02d}"
        success = False
        for attempt in range(2):
            try:
                resp = session.get(url, headers=headers, timeout=(5, 10))
                resp.encoding = "utf-8"
                soup = BeautifulSoup(resp.text, "lxml")
                day_divs = soup.find_all("div", id=re.compile(r"^new-div-\d{8}$"))
                for date_str, info in _parse_divs(day_divs).items():
                    if date_str not in all_data:
                        all_data[date_str] = info
                print(f"  {tag}{year}-{month:02d}-{start_day:02d}: {len(day_divs)}일")
                success = True
                break
            except Exception as e:
                print(f"  {tag}오류 {year}-{month:02d}-{start_day:02d} (시도{attempt+1}): {e}", file=sys.stderr)
        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            print(f"  {tag}{year}-{month:02d}-{start_day:02d}: 스킵")

    print(f"  {tag}수집 완료: {len(all_data)}일")
    return all_data


def crawl_reservations():
    """지도호 예약 현황 크롤링"""
    return _crawl_site(JIDO_BASE_URL, JIDO_URL, "지도호")


def crawl_raon():
    """라온호 예약 현황 크롤링"""
    return _crawl_site(RAON_BASE_URL, RAON_URL, "라온호")


# ─── HTML 생성 ───────────────────────────────────────────────

def _boat_row(label, cls_boat, status_cls, rem, tide, link, my_booking=False):
    """배 한 줄 HTML 생성"""
    tide_html = f'<span class="tide">{tide}</span>' if tide else ""
    mine_cls  = " mine" if my_booking else ""
    star_html = '<span class="bstar">★</span>' if my_booking else ""
    return (
        f'<a class="boat {cls_boat} {status_cls}{mine_cls}" href="{link}" target="_blank">'
        f'{star_html}'
        f'<span class="brem">{rem}</span>'
        f'{tide_html}</a>'
    )


def gen_month(year, month, today, jido_data, raon_data, korean_holidays):
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
            is_sat    = wd == 5
            is_sun    = wd == 6
            is_pub    = d in korean_holidays
            hname     = korean_holidays.get(d, "")

            cls = "past" if d < today else "base"
            if d == today:          cls += " today"
            if is_sat:              cls += " sat"
            if is_sun:              cls += " sun"
            if is_pub and not is_sun: cls += " hday"

            def boat_info(data):
                rem = status_cls = tide = ""
                my_booking = False
                companions = []
                if ds in data:
                    info = data[ds]
                    my_booking = info.get("my_booking", False)
                    companions = info.get("companions", [])
                    if d >= today:
                        st = info["status"]
                        if st == "full":
                            rem, status_cls = "마감", "full"
                        elif st == "available":
                            rem = f"{info['remaining']}명" if info["remaining"] is not None else "가능"
                            status_cls = "avail"
                        else:
                            status_cls = "empty"
                        tide = info.get("tide", "")
                    else:
                        status_cls = "empty"
                else:
                    status_cls = "empty"
                return rem, status_cls, tide, my_booking, companions

            jido_rem, jido_cls, jido_tide, jido_mine, jido_comp = boat_info(jido_data)
            raon_rem, raon_cls, raon_tide, raon_mine, raon_comp = boat_info(raon_data)

            my_booking = jido_mine or raon_mine
            companions = list(dict.fromkeys(jido_comp + raon_comp))  # 중복 제거, 순서 유지
            if my_booking: cls += " mine"

            hname_html     = f'<span class="hname">{hname}</span>' if hname else ""
            companion_html = "".join(f'<span class="companion">{c}</span>' for c in companions)

            jido_link = f"{JIDO_URL}&year={year}&month={month:02d}&day={day:02d}&mode=list#list"
            raon_link = f"{RAON_URL}&year={year}&month={month:02d}&day={day:02d}&mode=list#list"

            if d < today:
                boats_html = ""
            else:
                boats_html = (
                    '<div class="boats">'
                    + _boat_row("지도", "jido", jido_cls, jido_rem, jido_tide, jido_link, jido_mine)
                    + _boat_row("라온", "raon", raon_cls, raon_rem, raon_tide, raon_link, raon_mine)
                    + '</div>'
                )

            cells.append(
                f'<td><div class="cell {cls}" data-date="{ds}">'
                f'<span class="num">{day}</span>{hname_html}{companion_html}'
                f'{boats_html}</div></td>'
            )
        rows.append(f'<tr>{"".join(cells)}</tr>')

    return (
        f'<div class="month"><div class="month-title">{year}년 {mn}</div>'
        f'<table><thead><tr>'
        f'<th>월</th><th>화</th><th>수</th><th>목</th><th>금</th>'
        f'<th class="sat">토</th><th class="sun">일</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def generate_html(jido_data, raon_data, korean_holidays, last_run_at=None, last_changed_at=None):
    today = date.today()
    now_kst = datetime.now(KST)
    if last_run_at is None:
        last_run_at = now_kst
    if last_changed_at is None:
        last_changed_at = now_kst

    def holiday_available(data, label, res_url):
        result = []
        for ds, info in sorted(data.items()):
            d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
            if info["status"] == "available" and is_holiday(d, korean_holidays) and d >= today:
                result.append((d, info, label, res_url))
        return result

    jido_avail = holiday_available(jido_data, "지도호", JIDO_URL)
    raon_avail = holiday_available(raon_data, "라온호", RAON_URL)

    def make_alert_items(avail_list, res_url):
        items = []
        for d, info, _, _ in avail_list:
            hname = korean_holidays.get(d, "")
            wd    = "월화수목금토일"[d.weekday()]
            dtype = "토요일" if d.weekday() == 5 else ("일요일" if d.weekday() == 6 else f"공휴일({hname})")
            rem   = f"{info['remaining']}명" if info["remaining"] is not None else "빈자리"
            link  = f"{res_url}&year={d.year}&month={d.month:02d}&day={d.day:02d}&mode=list#list"
            items.append(
                f'<li><a href="{link}" target="_blank">'
                f'📅 {d.year}년 {d.month}월 {d.day}일 ({wd}) [{dtype}] — 남은자리 {rem}</a></li>'
            )
        return "".join(items)

    alert_parts = []
    if jido_avail:
        alert_parts.append(
            f'<div class="alert alert-jido">'
            f'<h3>🟢 지도호 휴일·주말 빈자리</h3>'
            f'<ul>{make_alert_items(jido_avail, JIDO_URL)}</ul></div>'
        )
    if raon_avail:
        alert_parts.append(
            f'<div class="alert alert-raon">'
            f'<h3>🟠 라온호 휴일·주말 빈자리</h3>'
            f'<ul>{make_alert_items(raon_avail, RAON_URL)}</ul></div>'
        )
    alert_html = "".join(alert_parts)

    months_html = []
    for offset in range(6):
        m = today.month + offset
        y = today.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        months_html.append(gen_month(y, m, today, jido_data, raon_data, korean_holidays))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1800">
<title>지도호·라온호 낚시 예약 현황</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Malgun Gothic',AppleGothic,sans-serif;background:#eef4eb;padding:12px;color:#333}}
h1{{text-align:center;color:#1a5e0e;font-size:1.5em;margin:10px 0 4px}}
.subtitle{{text-align:center;font-size:.85em;color:#555;margin-bottom:12px}}
.subtitle a{{color:#1a5e0e;text-decoration:none;margin:0 4px}}
.alert{{border-radius:8px;padding:12px 16px;margin:0 auto 10px;max-width:1200px}}
.alert-jido{{background:#f1f8e9;border:2px solid #558b2f}}
.alert-raon{{background:#fff3e0;border:2px solid #e65100}}
.alert h3{{margin-bottom:8px;font-size:1em}}
.alert-jido h3{{color:#33691e}}
.alert-raon h3{{color:#bf360c}}
.alert ul{{list-style:none}}
.alert li{{padding:3px 0;font-size:.9em}}
.alert a{{color:#bf360c;font-weight:bold;text-decoration:none}}
.alert a:hover{{text-decoration:underline}}
.months{{display:flex;flex-wrap:wrap;gap:14px;justify-content:center;max-width:1200px;margin:0 auto}}
.month{{background:white;border-radius:10px;padding:14px;box-shadow:0 2px 8px rgba(0,0,0,.1);flex:1;min-width:400px;max-width:520px}}
.month-title{{text-align:center;font-weight:bold;color:#1a5e0e;font-size:1.05em;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th{{padding:5px 2px;text-align:center;font-size:.78em;color:#666;font-weight:normal}}
th.sat{{color:#1565c0}} th.sun{{color:#b71c1c}}
td{{padding:2px;height:auto;min-height:68px;vertical-align:top}}
.cell{{min-height:66px;border-radius:5px;padding:3px 2px;display:flex;flex-direction:column;align-items:center;cursor:default;transition:filter .15s}}
.cell:hover{{filter:brightness(.95)}}
.num{{font-weight:bold;font-size:.88em;line-height:1.3}}
.hname{{font-size:.58em;color:#c62828;line-height:1.1;margin-top:1px;text-align:center}}
.past{{background:#f5f5f5}} .past .num{{color:#bbb}}
.base{{background:#fafafa}} .base .num{{color:#555}}
.today{{background:#fff9c4!important;border:2px solid #f9a825!important}}
.today .num{{color:#e65100!important;font-size:1em}}
.mine{{background:#e8eaf6!important;border:2px solid #3949ab!important}}
.mine .num{{color:#1a237e!important}}
.mybadge{{font-size:.6em;background:#3949ab;color:#fff;border-radius:3px;padding:1px 3px;margin-top:2px;font-weight:bold;line-height:1.4}}
.boat.mine{{border:2px solid #3949ab!important;font-weight:bold}}
.companion{{font-size:.6em;background:#f57c00;color:#fff;border-radius:3px;padding:1px 3px;margin-top:1px;line-height:1.4}}
.sat .num{{color:#1565c0}}
.sun .num{{color:#b71c1c!important}}
.hday .num{{color:#b71c1c!important}}
.boats{{width:100%;display:flex;flex-direction:column;gap:2px;margin-top:3px}}
.boat{{display:flex;align-items:center;gap:3px;padding:2px 4px;border-radius:3px;font-size:.68em;text-decoration:none;cursor:pointer;transition:filter .15s}}
.boat:hover{{filter:brightness(.88)}}
.bstar{{font-weight:bold;color:#3949ab;margin-right:1px}}
.brem{{font-weight:bold}}
.tide{{font-size:.6em;color:#888;margin-left:auto}}
.jido.avail{{background:#c8f0c0;color:#1b5e20}}
.jido.full{{background:#ffcdd2;color:#b71c1c}}
.jido.empty{{background:#f0f0f0;color:#aaa}}
.raon.avail{{background:#ffe0b2;color:#e65100}}
.raon.full{{background:#ffcdd2;color:#b71c1c}}
.raon.empty{{background:#f0f0f0;color:#aaa}}
.legend{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:0 auto 14px;max-width:900px}}
.legend-item{{display:flex;align-items:center;gap:4px;font-size:.78em}}
.dot{{width:12px;height:12px;border-radius:3px;display:inline-block}}
.foot{{text-align:center;color:#999;font-size:.78em;margin-top:14px;padding-bottom:8px}}
.note{{font-size:.6em;background:#e3f2fd;color:#0d47a1;border-radius:3px;padding:1px 3px;margin-top:1px;line-height:1.4;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.note-overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:1000;display:flex;align-items:center;justify-content:center}}
.note-modal{{background:white;border-radius:10px;padding:20px;width:90%;max-width:400px;box-shadow:0 4px 20px rgba(0,0,0,.3)}}
.note-modal h3{{color:#1a5e0e;margin-bottom:12px;font-size:1em}}
.note-modal textarea{{width:100%;height:100px;border:1px solid #ccc;border-radius:5px;padding:8px;font-size:.9em;font-family:inherit;resize:vertical;box-sizing:border-box}}
.note-buttons{{display:flex;gap:8px;margin-top:12px;justify-content:flex-end}}
.note-buttons button{{padding:6px 16px;border:none;border-radius:5px;cursor:pointer;font-size:.9em}}
.btn-save{{background:#1a5e0e;color:white}}.btn-delete{{background:#c62828;color:white}}.btn-cancel{{background:#eee;color:#333}}
@media(max-width:480px){{.month{{min-width:100%;max-width:100%}}}}
</style>
</head>
<body>
<h1>🎣 지도호·라온호 낚시 예약 현황</h1>
<p class="subtitle">
  <a href="{JIDO_URL}" target="_blank">지도호 예약 페이지 ↗</a>
  &nbsp;|&nbsp;
  <a href="{RAON_URL}" target="_blank">라온호 예약 페이지 ↗</a>
</p>
{alert_html}
<div class="legend">
  <div class="legend-item"><span class="dot" style="background:#c8f0c0;border:1px solid #81c784"></span>지도호 예약가능</div>
  <div class="legend-item"><span class="dot" style="background:#ffe0b2;border:1px solid #ffb74d"></span>라온호 예약가능</div>
  <div class="legend-item"><span class="dot" style="background:#ffcdd2;border:1px solid #ef9a9a"></span>마감</div>
  <div class="legend-item"><span class="dot" style="background:#fff9c4;border:2px solid #f9a825"></span>오늘</div>
  <div class="legend-item"><span class="dot" style="background:#e8eaf6;border:2px solid #3949ab"></span>내 예약</div>
  <div class="legend-item">📝 더블클릭 = 메모</div>
</div>
<div class="months">{"".join(months_html)}</div>
<p class="foot">
  🤖 마지막 자동실행: {last_run_at.strftime("%Y년 %m월 %d일 %H:%M")} KST
  &nbsp;|&nbsp;
  📊 데이터 변경: {last_changed_at.strftime("%Y년 %m월 %d일 %H:%M")} KST
  &nbsp;|&nbsp; 1시간마다 자동 갱신 · 30분마다 페이지 새로고침
  <br><a href="https://github.com/rainysea23/fishing/actions" target="_blank" style="color:#aaa;font-size:.9em">GitHub Actions 실행 이력 보기 ↗</a>
</p>
<script>
(function(){{
  var P='jido_note_';
  function label(ds){{return ds.slice(0,4)+'년 '+parseInt(ds.slice(4,6))+'월 '+parseInt(ds.slice(6,8))+'일';}}
  function updateNote(cell,note){{
    var el=cell.querySelector('.note');
    if(note){{if(!el){{el=document.createElement('span');el.className='note';cell.appendChild(el);}}el.textContent=note;}}
    else{{if(el)el.remove();}}
  }}
  function showModal(ds,cell){{
    var key=P+ds,current=localStorage.getItem(key)||'';
    var ov=document.createElement('div');ov.className='note-overlay';
    var mo=document.createElement('div');mo.className='note-modal';
    mo.innerHTML='<h3>📝 메모 — '+label(ds)+'</h3>'
      +'<textarea id="ni" placeholder="메모를 입력하세요..."></textarea>'
      +'<div class="note-buttons">'
      +'<button class="btn-cancel">취소</button>'
      +'<button class="btn-delete">삭제</button>'
      +'<button class="btn-save">저장</button>'
      +'</div>';
    ov.appendChild(mo);document.body.appendChild(ov);
    var ta=mo.querySelector('#ni');ta.value=current;ta.focus();
    var close=function(){{document.body.removeChild(ov);}};
    mo.querySelector('.btn-save').onclick=function(){{
      var v=ta.value.trim();
      if(v)localStorage.setItem(key,v);else localStorage.removeItem(key);
      updateNote(cell,v);close();
    }};
    mo.querySelector('.btn-delete').onclick=function(){{localStorage.removeItem(key);updateNote(cell,'');close();}};
    mo.querySelector('.btn-cancel').onclick=close;
    ov.addEventListener('click',function(e){{if(e.target===ov)close();}});
  }}
  document.querySelectorAll('div.cell[data-date]').forEach(function(cell){{
    var ds=cell.dataset.date;
    var note=localStorage.getItem(P+ds);
    if(note)updateNote(cell,note);
    cell.addEventListener('dblclick',function(e){{
      e.preventDefault();
      showModal(ds,cell);
    }});
  }});
}})();
</script>
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


def notify_changes(new_data, old_data, korean_holidays, label="지도호", res_url=None):
    """이전 데이터와 비교해 변경된 휴일 날짜만 알림"""
    if res_url is None:
        res_url = JIDO_URL
    today = date.today()
    alerts = []

    for ds, new_info in sorted(new_data.items()):
        d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        if d < today or not is_holiday(d, korean_holidays):
            continue

        old_info      = old_data.get(ds, {})
        old_status    = old_info.get("status", "no_data")
        old_remaining = old_info.get("remaining")
        new_status    = new_info["status"]
        new_remaining = new_info.get("remaining")

        hname   = korean_holidays.get(d, "")
        wd      = "월화수목금토일"[d.weekday()]
        dtype   = "토요일" if d.weekday() == 5 else ("일요일" if d.weekday() == 6 else f"공휴일({hname})")
        rem_str = f"{new_remaining}명" if new_remaining is not None else "빈자리"

        if old_status == "full" and new_status == "available":
            alerts.append(f"🆕 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] 빈자리 생겼습니다! {rem_str}")

        elif old_status == "available" and new_status == "available":
            if old_remaining is not None and new_remaining is not None:
                if new_remaining > old_remaining:
                    alerts.append(f"📈 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] {old_remaining}명→{new_remaining}명으로 증가")
                elif new_remaining < old_remaining:
                    alerts.append(f"📉 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] {old_remaining}명→{new_remaining}명으로 감소")

        elif old_status == "no_data" and new_status == "available":
            alerts.append(f"📅 {d.year}/{d.month}/{d.day}({wd}) [{dtype}] 예약 오픈! {rem_str}")

    if alerts:
        msg = (
            f"🎣 <b>{label} 빈자리 변경 알림</b>\n\n"
            + "\n".join(alerts)
            + f"\n\n<a href='{res_url}'>👉 예약하러 가기</a>"
        )
        send_telegram(msg)
        print(f"[{label}] 변경 알림 {len(alerts)}건 전송")
    else:
        print(f"[{label}] 변경 없음 - 알림 생략")


# ─── 메인 ────────────────────────────────────────────────────

def main():
    # 이전 데이터 로드
    old_data_json      = {}
    old_jido           = {}
    old_raon           = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", encoding="utf-8") as f:
                old_data_json = json.load(f)
                old_jido = old_data_json.get("reservations", {})
                old_raon = old_data_json.get("raon_reservations", {})
        except Exception:
            pass

    print("=== 지도호 크롤링 ===")
    jido_data = crawl_reservations()
    print("=== 라온호 크롤링 ===")
    raon_data = crawl_raon()

    kr_holidays = get_korean_holidays()
    now_kst = datetime.now(KST)

    changed = jido_data != old_jido or raon_data != old_raon
    if changed or not old_data_json.get("last_changed_at"):
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
                "last_run_at":       now_kst.isoformat(),
                "last_changed_at":   last_changed_at.isoformat(),
                "updated_at":        now_kst.isoformat(),
                "reservations":      jido_data,
                "raon_reservations": raon_data,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    html = generate_html(jido_data, raon_data, kr_holidays,
                         last_run_at=now_kst, last_changed_at=last_changed_at)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")

    notify_changes(jido_data, old_jido, kr_holidays, label="지도호", res_url=JIDO_URL)
    notify_changes(raon_data, old_raon, kr_holidays, label="라온호", res_url=RAON_URL)


if __name__ == "__main__":
    main()
