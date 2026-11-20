"""Read the official, bounded RSS lists; never fetch job detail pages."""

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests

FEEDS = (
    "https://www.incruit.com/rss/job.asp",
    "https://www.incruit.com/rss/job.asp?occ1=150",
    "https://www.incruit.com/rss/job.asp?occ1=190",
)
CACHE_MINUTES = 15
NOTICE = (
    "인크루트 전체·IT·의료 RSS에 노출된 일부 공고입니다. 전체 채용공고 검색이 아닙니다. "
    "진행 상태·고용형태·NCS 분류와 상세 자격은 확인되지 않았습니다. "
    "원문을 확인하고 자격요건을 입력하면 입력한 내용에 근거해 적합도를 평가합니다."
)


class RssError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def plain_text(value):
    return unescape(re.sub(r"<[^>]*>", " ", value or "")).strip()


def parse_feed(content: bytes, fetched_at: str) -> list[dict]:
    if len(content) > 2_000_000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise RssError("UPSTREAM_INVALID_RESPONSE", "RSS 문서 형식을 확인하지 못했습니다.")
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        raise RssError("UPSTREAM_INVALID_RESPONSE", "RSS XML을 읽지 못했습니다.") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise RssError("UPSTREAM_INVALID_RESPONSE", "RSS 채널이 없습니다.")
    result = []
    for item in root.findall("./channel/item")[:500]:
        link = (item.findtext("link") or "").strip()
        parsed = urlparse(link)
        source_id = parse_qs(parsed.query).get("job", [""])[0]
        title = plain_text(item.findtext("title"))
        company = plain_text(item.findtext("author"))
        if (parsed.scheme != "https" or parsed.netloc.lower() != "job.incruit.com"
                or parsed.path.lower() != "/jobdb_info/jobpost.asp"
                or not re.fullmatch(r"\d{5,30}", source_id) or not title or not company):
            raise RssError("UPSTREAM_INVALID_RESPONSE", "RSS 공고 식별자나 필수 항목을 확인하지 못했습니다.")
        description = item.findtext("description") or ""
        fields = {}
        for part in re.split(r"<br\s*/?>", description, flags=re.I):
            text = plain_text(part).lstrip("▨ ")
            if ":" in text:
                key, value = text.split(":", 1)
                fields[key.strip()] = value.strip()
        region = fields.get("지역", "")
        locations = list(dict.fromkeys(x.strip() for x in re.split(r"[|>/,]", region) if x.strip())) or None
        career = fields.get("경력", "")
        career_types = (["경력무관"] if "무관" in career else
                        [x for x in ("신입", "경력") if x in career]) or None
        # RSS MM/DD and '채용시' are intentionally not converted to invented dates.
        deadline = fields.get("마감일", "")
        try:
            deadline = date.fromisoformat(deadline).isoformat()
        except ValueError:
            deadline = None
        result.append({
            "source": "incruit", "board_id": "rss", "source_job_id": source_id,
            "company": company, "title": title, "location": locations,
            "career_type": career_types, "employment_type": None, "ncs_categories": None,
            "rss_categories": [],
            "deadline": deadline, "is_open": None,
            "job_url": f"https://job.incruit.com/jobdb_info/jobpost.asp?job={source_id}",
            "description_html": plain_text(description),
            "required_qualifications": None, "preferred_qualifications": None,
            "fetched_at": fetched_at,
        })
    return result


def collect_jobs() -> list[dict]:
    stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    jobs = {}
    for url in FEEDS:
        try:
            with requests.get(url, timeout=(5, 10), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RssError("UPSTREAM_ERROR", "인크루트 RSS가 요청을 처리하지 못했습니다.")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise RssError("UPSTREAM_INVALID_RESPONSE", "RSS 응답 크기를 초과했습니다.")
                    chunks.append(chunk)
                batch = parse_feed(b"".join(chunks), stamp)
        except requests.Timeout:
            raise RssError("UPSTREAM_TIMEOUT", "인크루트 RSS 응답 시간이 초과됐습니다.") from None
        except requests.RequestException:
            raise RssError("UPSTREAM_ERROR", "인크루트 RSS에 연결하지 못했습니다.") from None
        for job in batch:
            category = parse_qs(urlparse(url).query).get("occ1", [])
            previous = jobs.get(job["source_job_id"], {})
            job["rss_categories"] = sorted(set(previous.get("rss_categories", [])) | set(category))
            jobs[job["source_job_id"]] = job
    # All feeds must succeed before the caller writes a new snapshot.
    return list(jobs.values())


def snapshot_is_fresh(db) -> bool:
    row = db.execute("SELECT fetched_at FROM job_source_snapshot WHERE source='incruit'").fetchone()
    if row is None:
        return False
    stamp = row[0]
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
        return timedelta(0) <= age < timedelta(minutes=CACHE_MINUTES)
    except (ValueError, TypeError):
        return False


def ensure_snapshot_table(db):
    migrated = False
    db.execute("""CREATE TABLE IF NOT EXISTS job_source_snapshot (
        source TEXT PRIMARY KEY, fetched_at TEXT NOT NULL,
        item_count INTEGER NOT NULL CHECK(item_count >= 0),
        job_ids TEXT NOT NULL DEFAULT '[]')""")
    if "job_ids" not in {row[1] for row in db.execute("PRAGMA table_info(job_source_snapshot)")}:
        db.execute("ALTER TABLE job_source_snapshot ADD COLUMN job_ids TEXT NOT NULL DEFAULT '[]'")
        migrated = True
    if "rss_categories" not in {row[1] for row in db.execute("PRAGMA table_info(job_posting)")}:
        db.execute("ALTER TABLE job_posting ADD COLUMN rss_categories TEXT NOT NULL DEFAULT '[]'")
        migrated = True
    if migrated:
        # Old cached rows did not preserve the feed category. Recollect on next refresh.
        db.execute("UPDATE job_source_snapshot SET fetched_at='' WHERE source='incruit'")
        db.commit()
