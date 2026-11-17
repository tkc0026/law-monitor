#!/usr/bin/env python3
"""안전·환경 법령 이슈 수집기

국가법령정보 공동활용 Open API(법령 개정, 신구법 비교, 중처법 판례)와
직접 등록한 RSS/Atom 피드(입법예고, 정책방향, 우수사례)를 모아
docs/index.html 대시보드와 블로그 초안(docs/drafts/날짜.md)을 만듭니다.

사용법
  LAW_OC=발급받은ID python collector.py          # 수집 + 대시보드 생성
  LAW_OC=발급받은ID python collector.py --check  # 소스별 연결 상태만 점검
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
DATA_FILE = DOCS / "data.json"
BASE = "https://www.law.go.kr/DRF"
OC = os.environ.get("LAW_OC", "").strip()
KEEP_DAYS = 400


# ---------------------------------------------------------------- 공통 도구
def kst_today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


def mask(text):
    return text.replace(OC, "***") if OC else text


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "law-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def nd(value):
    """20260105, 2026.01.05, 2026-01-05 -> 2026-01-05"""
    d = re.sub(r"\D", "", str(value or ""))
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else ""


def pick(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def squash(s):
    return re.sub(r"\s+", "", s or "")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def find_rows(obj):
    """API 응답 JSON에서 가장 긴 'dict 리스트'를 결과 목록으로 간주합니다."""
    best = []

    def walk(x):
        nonlocal best
        if isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x) and len(x) > len(best):
                best = x
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)

    walk(obj)
    return best


def law_api(target, **params):
    if not OC:
        raise RuntimeError("LAW_OC(법제처 Open API 인증키)가 설정되지 않았습니다")
    q = {"OC": OC, "target": target, "type": "JSON", **params}
    url = f"{BASE}/lawSearch.do?" + urllib.parse.urlencode(q)
    text = http_get(url)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        snippet = mask(text[:160].replace("\n", " "))
        raise RuntimeError(f"JSON이 아닌 응답(인증키 또는 서버 IP 등록 문제 가능): {snippet}")


def law_page(title):
    return "https://www.law.go.kr/법령/" + urllib.parse.quote(title)


def make_item(law, kind, title, date, source, url, note=""):
    key = hashlib.sha1(f"{law}|{kind}|{url}|{title}".encode()).hexdigest()[:12]
    return {"id": key, "law": law, "kind": kind, "title": title, "date": date,
            "source": source, "url": url, "note": note}


# ---------------------------------------------------------------- 수집기
def collect_laws(law, cfg, today):
    """법령 개정, 시행 예정 (본 법, 시행령, 시행규칙 포함)"""
    out, since = [], (today - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    for nm in cfg["laws"][law]["names"]:
        for r in find_rows(law_api("law", query=nm, display=100)):
            title = pick(r, "법령명한글", "법령명")
            if not title or not squash(title).startswith(squash(nm)):
                continue
            pub, eff = nd(pick(r, "공포일자")), nd(pick(r, "시행일자"))
            upcoming = eff > today.isoformat()
            if not (pub >= since or upcoming):
                continue
            change = pick(r, "제개정구분명")
            note = ", ".join(x for x in [change, f"공포 {pub}" if pub else "", f"시행 {eff}" if eff else ""] if x)
            out.append(make_item(law, "시행예정" if upcoming else "개정", title, pub or eff,
                                 "국가법령정보센터", law_page(title), note))
    return out


def collect_oldnew(law, cfg, today):
    """신구법 전/후 비교"""
    out, since = [], (today - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    for nm in cfg["laws"][law]["names"]:
        for r in find_rows(law_api("oldAndNew", query=nm, display=50)):
            title = pick(r, "신구법명한글", "신구법명", "법령명한글")
            if not title or not squash(title).startswith(squash(nm)):
                continue
            pub = nd(pick(r, "공포일자"))
            if pub < since:
                continue
            note = ", ".join(x for x in [pick(r, "제개정구분명"), f"시행 {nd(pick(r, '시행일자'))}"] if x and not x.endswith(" "))
            out.append(make_item(law, "신구법비교", title + " 신구법 비교", pub,
                                 "국가법령정보센터", law_page(title), note))
    return out


def collect_precedents(law, cfg, today):
    """판례 (본문에 해당 법이 언급된 판결)"""
    out, seen = [], set()
    since = (today - dt.timedelta(days=cfg["precedent_lookback_days"])).isoformat()
    for q in cfg["laws"][law]["precedent_queries"]:
        rows = find_rows(law_api("prec", query=q, search=2, display=100, sort="ddes"))
        for r in rows:
            seq = pick(r, "판례일련번호")
            date = nd(pick(r, "선고일자"))
            if not seq or seq in seen or date < since:
                continue
            seen.add(seq)
            court, no = pick(r, "법원명"), pick(r, "사건번호")
            title = pick(r, "사건명") or no
            out.append(make_item(law, "판례", f"{title} ({court} {no})", date, "국가법령정보센터 판례",
                                 f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={seq}"))
    return out


def parse_date(s):
    if not s:
        return ""
    try:
        return email.utils.parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return nd(s)


def parse_feed(text):
    root = ET.fromstring(text)
    rows = []
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        d = {c.tag.split("}")[-1]: (c.text or "") for c in it}
        link = d.get("link", "").strip()
        if not link:
            for c in it:
                if c.tag.split("}")[-1] == "link" and c.get("href"):
                    link = c.get("href")
        date = d.get("pubDate") or d.get("date") or d.get("published") or d.get("updated") or ""
        summary = d.get("description") or d.get("summary") or d.get("content") or ""
        rows.append((d.get("title", "").strip(), link, parse_date(date), strip_tags(summary)))
    return rows


def collect_feed(feed, cfg, today):
    """RSS/Atom -> 키워드로 법 분류"""
    out, since = [], (today - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    for title, link, date, summary in parse_feed(http_get(feed["url"])):
        if not title or not link or (date and date < since):
            continue
        text = title + " " + summary
        for law, lc in cfg["laws"].items():
            if any(k in text for k in lc["keywords"]):
                out.append(make_item(law, feed["type"], title, date, feed["name"], link))
    return out


def run_collectors(cfg, today):
    """(항목 목록, 오류 목록, 소스별 건수) 반환"""
    items, errors, counts = [], [], []

    def run(label, fn, *args):
        try:
            got = fn(*args)
            items.extend(got)
            counts.append((label, len(got)))
        except Exception as e:  # 한 소스의 실패가 전체를 막지 않게 함
            errors.append(f"{label}: {mask(str(e))}")
            counts.append((label, None))

    for law, lc in cfg["laws"].items():
        run(f"{law} 개정·시행예정", collect_laws, law, cfg, today)
        run(f"{law} 신구법비교", collect_oldnew, law, cfg, today)
        if lc.get("precedent_queries"):
            run(f"{law} 판례", collect_precedents, law, cfg, today)
    for feed in cfg.get("feeds", []):
        run(f"피드 {feed['name']}", collect_feed, feed, cfg, today)
    return items, errors, counts


# ---------------------------------------------------------------- 저장
def merge(new_items, today):
    first_run = not DATA_FILE.exists()
    old = {} if first_run else {i["id"]: i for i in json.loads(DATA_FILE.read_text("utf-8"))["items"]}
    stamp = "" if first_run else today.isoformat()  # 첫 실행분은 '신규'로 표시하지 않음
    for it in new_items:
        if it["id"] in old:
            it["first_seen"] = old[it["id"]]["first_seen"]
        else:
            it["first_seen"] = stamp
    merged = {i["id"]: i for i in old.values()}
    merged.update({i["id"]: i for i in new_items})
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    kept = [i for i in merged.values() if (i["date"] or i["first_seen"] or "9") >= cutoff or not i["date"]]
    return sorted(kept, key=lambda i: (i["date"], i["title"]), reverse=True)


# ---------------------------------------------------------------- 대시보드
GROUPS = [
    ("입법예고·행정예고", ["입법예고"], "feed"),
    ("개정·시행 예정", ["시행예정", "개정"], "api"),
    ("신구법 전/후 비교", ["신구법비교"], "api"),
    ("판례", ["판례"], "prec"),
    ("정책방향", ["정책방향"], "feed"),
    ("우수사례", ["우수사례"], "feed"),
]
PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>안전·환경 법령 이슈 모니터</title>
<style>
:root{--bg:#eef1f4;--panel:#fff;--ink:#17212b;--sub:#5c6b7a;--line:#d6dde4;--acc:#0f5f73;--new-bg:#fdecc8;--new-ink:#8a4b08}
@media (prefers-color-scheme:dark){:root{--bg:#10161c;--panel:#18212a;--ink:#e6ecf1;--sub:#9aa9b8;--line:#2b3946;--acc:#6cc3d5;--new-bg:#4a3510;--new-ink:#f6c877}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Pretendard","Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
.wrap{display:grid;grid-template-columns:220px minmax(0,1fr);gap:32px;max-width:1080px;margin:0 auto;padding:32px 20px 64px}
nav{position:sticky;top:24px;align-self:start}
nav h1{font-size:17px;margin:0 0 4px}
nav p{margin:0 0 20px;color:var(--sub);font-size:13px}
nav a{display:flex;justify-content:space-between;padding:8px 10px;margin:0 -10px;border-radius:6px;color:var(--ink);text-decoration:none}
nav a:hover,nav a:focus-visible{background:var(--panel);outline:2px solid var(--acc);outline-offset:-2px}
nav b{color:var(--new-ink);background:var(--new-bg);border-radius:10px;padding:0 8px;font-size:12px;font-weight:600}
section.law{margin-bottom:48px}
section.law>h2{font-size:22px;margin:0 0 16px;padding-bottom:8px;border-bottom:2px solid var(--acc)}
h3{font-size:15px;margin:24px 0 8px;color:var(--acc)}
ul{list-style:none;margin:0;padding:0;background:var(--panel);border:1px solid var(--line);border-radius:8px}
li{display:grid;grid-template-columns:92px 1fr;gap:12px;padding:10px 14px;border-top:1px solid var(--line)}
li:first-child{border-top:0}
time{color:var(--sub);font-variant-numeric:tabular-nums}
li a{color:var(--ink);font-weight:500}
li a:hover{color:var(--acc)}
.meta{display:block;color:var(--sub);font-size:13px}
.new{background:var(--new-bg);color:var(--new-ink);font-size:12px;font-weight:600;border-radius:4px;padding:0 6px;margin-right:6px}
.empty{padding:12px 14px;color:var(--sub);background:var(--panel);border:1px dashed var(--line);border-radius:8px}
details{margin-top:6px}summary{cursor:pointer;color:var(--sub);padding:4px 0}
footer{color:var(--sub);font-size:13px;border-top:1px solid var(--line);padding-top:16px}
footer li{display:list-item;border:0;padding:0}footer ul{background:none;border:0;list-style:disc;padding-left:18px}
@media (max-width:760px){.wrap{grid-template-columns:1fr}nav{position:static}li{grid-template-columns:1fr;gap:0}}
</style></head><body><div class="wrap">
<nav><h1>법령 이슈 모니터</h1><p>__UPDATED__ 기준</p>__NAV__</nav>
<main>__BODY__
<footer><p>출처는 국가법령정보센터 Open API와 직접 등록한 공공기관 피드입니다. 게시 전에 원문 링크에서 내용을 다시 확인하세요.</p>__ERRORS__</footer>
</main></div></body></html>"""


def li(it, today):
    new = bool(it.get("first_seen")) and it["first_seen"] >= (today - dt.timedelta(days=3)).isoformat()
    badge = '<span class="new">신규</span>' if new else ""
    meta = " / ".join(x for x in [it["source"], it.get("note", "")] if x)
    return (f'<li><time>{html.escape(it["date"] or "-")}</time><div>{badge}'
            f'<a href="{html.escape(it["url"], quote=True)}" target="_blank" rel="noopener">{html.escape(it["title"])}</a>'
            f'<span class="meta">{html.escape(meta)}</span></div></li>')


def render(items, errors, cfg, today):
    nav, body = [], []
    has_feed = {t: any(f["type"] == t for f in cfg.get("feeds", [])) for t in ("입법예고", "정책방향", "우수사례")}
    for n, (law, lc) in enumerate(cfg["laws"].items()):
        mine = [i for i in items if i["law"] == law]
        new_cnt = sum(1 for i in mine if i.get("first_seen") and i["first_seen"] >= (today - dt.timedelta(days=3)).isoformat())
        nav.append(f'<a href="#law{n}">{html.escape(law)}{f"<b>{new_cnt}</b>" if new_cnt else ""}</a>')
        parts = [f'<section class="law" id="law{n}"><h2>{html.escape(law)}</h2>']
        for label, kinds, mode in GROUPS:
            if mode == "prec" and not lc.get("precedent_queries"):
                continue
            rows = [i for i in mine if i["kind"] in kinds]
            parts.append(f"<h3>{label}</h3>")
            if not rows:
                if mode == "feed" and not has_feed[kinds[0]]:
                    msg = f"수집 소스가 아직 없습니다. config.json의 feeds에 '{kinds[0]}' 유형 RSS를 추가하세요."
                else:
                    msg = "수집 기간 안에 해당 항목이 없습니다."
                parts.append(f'<div class="empty">{msg}</div>')
                continue
            shown, rest = rows[:12], rows[12:]
            parts.append("<ul>" + "".join(li(i, today) for i in shown) + "</ul>")
            if rest:
                parts.append(f'<details><summary>{len(rest)}건 더 보기</summary><ul>'
                             + "".join(li(i, today) for i in rest) + "</ul></details>")
        parts.append("</section>")
        body.append("".join(parts))
    err_html = ("<p>수집 중 문제가 있었던 소스</p><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in errors) + "</ul>") if errors else ""
    return (PAGE.replace("__UPDATED__", today.isoformat()).replace("__NAV__", "".join(nav))
            .replace("__BODY__", "".join(body)).replace("__ERRORS__", err_html))


def write_draft(items, today):
    fresh = [i for i in items if i.get("first_seen") == today.isoformat()]
    if not fresh:
        return None
    lines = [f"# 안전·환경 법령 이슈 ({today.isoformat()})", ""]
    for law in dict.fromkeys(i["law"] for i in fresh):
        lines += [f"## {law}", ""]
        for it in (i for i in fresh if i["law"] == law):
            lines += [f"### [{it['kind']}] {it['title']}",
                      f"- 일자: {it['date'] or '-'}", f"- 출처: {it['source']}", f"- 원문: {it['url']}"]
            if it.get("note"):
                lines.append(f"- 비고: {it['note']}")
            lines += ["- 내 코멘트: ", ""]
    path = DOCS / "drafts" / f"{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), "utf-8")
    return path


# ---------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="소스별 연결 상태만 점검하고 저장하지 않음")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text("utf-8"))
    today = kst_today()
    items, errors, counts = run_collectors(cfg, today)

    if args.check:
        for label, n in counts:
            print(f"{'실패' if n is None else '정상'}  {label}" + ("" if n is None else f"  ({n}건)"))
        for e in errors:
            print("  -", e)
        sys.exit(1 if errors else 0)

    DOCS.mkdir(exist_ok=True)
    merged = merge(items, today)
    DATA_FILE.write_text(json.dumps({"updated": today.isoformat(), "items": merged}, ensure_ascii=False, indent=1), "utf-8")
    (DOCS / "index.html").write_text(render(merged, errors, cfg, today), "utf-8")
    draft = write_draft(merged, today)
    print(f"수집 {len(items)}건, 누적 {len(merged)}건, 오류 {len(errors)}건" + (f", 초안 {draft.name}" if draft else ""))
    for e in errors:
        print("  -", e)
    # 모든 소스가 실패한 경우에만 실패 처리 (일부 실패는 대시보드 하단에 표시)
    if errors and not items:
        sys.exit(1)


if __name__ == "__main__":
    main()
