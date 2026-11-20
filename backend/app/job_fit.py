import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date

from backend.app.db.database import SCHEMA_PATH


@dataclass(frozen=True)
class JobCriteria:
    required_skills: tuple[str, ...] = ()
    required_certifications: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    preferred_certifications: tuple[str, ...] = ()
    min_career_months: int | None = None
    project_keywords: tuple[str, ...] = ()
    other_required: tuple[str, ...] = ()
    requirements_checked: bool = False


def _norm(text: str) -> str:
    return "".join(text.casefold().split())


def _fraction(flags: list[bool], weight: int) -> int | None:
    return round(weight * sum(flags) / len(flags)) if flags else None


def _compare(profile, job, criteria, skills, certifications, projects, today):
    norm_skills = {_norm(skill) for skill in skills}
    norm_certs = {_norm(cert) for cert in certifications}
    mandatory = {
        **{f"기술: {skill}": _norm(skill) in norm_skills for skill in criteria.required_skills},
        **{f"자격: {cert}": _norm(cert) in norm_certs for cert in criteria.required_certifications},
    }
    preferred = [
        *(_norm(skill) in norm_skills for skill in criteria.preferred_skills),
        *(_norm(cert) in norm_certs for cert in criteria.preferred_certifications),
    ]

    career_ok = None
    if criteria.min_career_months is not None and profile["career_months"] is not None:
        career_ok = profile["career_months"] >= criteria.min_career_months

    deadline = date.fromisoformat(job["deadline"]) if job["deadline"] else None
    application_open = None
    not_started = bool(job.get("recruitment_start") and date.fromisoformat(job["recruitment_start"]) > today)
    if not_started or job["is_open"] == 0 or (deadline is not None and deadline < today):
        application_open = False
    elif job["is_open"] == 1 and deadline is not None:
        application_open = True

    weights = {
        "직무": 15,
        "필수 기술·자격": 20,
        "경력": 15,
        "프로젝트": 10,
        "지역": 10,
        "고용형태": 10,
        "우대사항": 10,
        "지원 일정": 10,
    }

    location_matches = []
    if profile["locations"] and job["locations"]:
        location_matches = [any(
            _norm(preference) == _norm(value)
            for preference in profile["locations"]
            for value in job["locations"]
        )]

    employment_matches = []
    if profile["employment_types"] and job["employment_types"]:
        employment_matches = [any(
            _norm(preference) == _norm(value)
            for preference in profile["employment_types"]
            for value in job["employment_types"]
        )]

    project_matches = [
        any(_norm(keyword) in _norm(project) for project in projects)
        for keyword in criteria.project_keywords
    ]

    by_criterion = {
        "직무": _fraction([all(_norm(term) in _norm(job["title"])
                            for term in profile["desired_role"].split())], weights["직무"]),
        "필수 기술·자격": _fraction(list(mandatory.values()), weights["필수 기술·자격"]),
        "경력": _fraction([career_ok] if career_ok is not None else [], weights["경력"]),
        "프로젝트": _fraction(project_matches, weights["프로젝트"]),
        "지역": _fraction(location_matches, weights["지역"]),
        "고용형태": _fraction(employment_matches, weights["고용형태"]),
        "우대사항": _fraction(preferred, weights["우대사항"]),
        "지원 일정": _fraction(
            [application_open] if application_open is not None else [],
            weights["지원 일정"],
        ),
    }

    gaps = [name for name, present in mandatory.items() if not present]
    unknown = list(criteria.other_required)
    if not criteria.requirements_checked:
        unknown.append("공고의 필수조건 확인 필요")
    if criteria.min_career_months is not None and career_ok is None:
        unknown.append("등록된 경력 개월 수 확인 필요")
    if application_open is None:
        unknown.append("채용 진행 상태 또는 마감일 확인 필요")

    blockers = []
    if application_open is False:
        blockers.append("모집 시작 전" if not_started else "공고 마감 또는 모집 종료")
    if career_ok is False:
        blockers.append(f"요구 경력 {criteria.min_career_months}개월 미충족")

    if blockers:
        eligibility = "unmet"
    elif gaps or unknown:
        eligibility = "unknown"
    else:
        eligibility = "met"

    return {
        "score": sum(value for value in by_criterion.values() if value is not None),
        "assessed_weight": sum(
            weights[key] for key, value in by_criterion.items() if value is not None
        ),
        "by_criterion": by_criterion,
        "eligibility": eligibility,
        "gaps": gaps,
        "unknown": unknown,
        "blockers": blockers,
    }


def evaluate_match(
    db: sqlite3.Connection,
    profile_id: int,
    job_id: int,
    criteria: JobCriteria,
    today: date,
    *, job_overrides: dict | None = None,
) -> dict:
    profile_row = db.execute(
        "SELECT * FROM user_profile WHERE id = ?", (profile_id,)
    ).fetchone()
    job_row = db.execute(
        "SELECT * FROM job_posting WHERE id = ?", (job_id,)
    ).fetchone()

    if profile_row is None or job_row is None:
        raise ValueError("프로필 또는 공고를 찾을 수 없습니다.")

    profile = dict(profile_row)
    job = dict(job_row)
    if job_overrides:
        job.update({key: value for key, value in job_overrides.items()
                    if key in {"deadline", "recruitment_start", "is_open"}})
    profile["locations"] = json.loads(profile["preferred_locations"])
    profile["employment_types"] = json.loads(profile["preferred_employment_types"])
    job["locations"] = json.loads(job["location"]) if job["location"] else []
    job["employment_types"] = json.loads(job["employment_type"]) if job["employment_type"] else []

    skills = json.loads(profile["skills"])
    certifications = json.loads(profile["certifications"])
    projects = []
    for row in db.execute("SELECT * FROM experience WHERE user_profile_id=?", (profile_id,)):
        experience = dict(row)
        skills.extend(json.loads(experience.get("skills") or "[]"))
        projects.append(" ".join(experience.get(key) or "" for key in
                                 ("title", "role", "description", "situation", "task", "action", "result")))

    current = _compare(profile, job, criteria, skills, certifications, projects, today)
    applied_plans = []

    if job["deadline"] and job["is_open"] != 0:
        plans = db.execute(
            """
            SELECT plan_type, target_name, expected_skills FROM growth_plan
            WHERE user_profile_id = ?
              AND status IN ('planned', 'in_progress')
              AND target_date BETWEEN ? AND ?
            ORDER BY target_date, id
            """,
            (profile_id, today.isoformat(), job["deadline"]),
        )

        for plan_type, name, expected_skills in plans:
            skills.extend(json.loads(expected_skills))
            if plan_type == "skill":
                skills.append(name)
            elif plan_type == "certification":
                certifications.append(name)
            elif plan_type == "project":
                projects.append(name)
            applied_plans.append(name)

    future = _compare(profile, job, criteria, skills, certifications, projects, today)
    return {"current": current, "future": future, "applied_plans": applied_plans}


def main() -> None:
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

        profile_id = db.execute(
            """
            INSERT INTO user_profile
                (desired_role, preferred_locations, preferred_employment_types,
                 career_months, skills)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("데이터 분석", '["부산"]', '["정규직"]', 18, '["SQL"]'),
        ).lastrowid

        job_id = db.execute(
            """
            INSERT INTO job_posting
                (source, board_id, source_job_id, company, title,
                 location, employment_type, deadline, is_open, fetched_at)
            VALUES ('demo', 'demo', '1', '예시기관', '데이터 분석 담당자 채용',
                    '["부산"]', '["정규직"]', '2026-11-30', 1, '2026-11-19T00:00:00Z')
            """
        ).lastrowid

        db.executemany(
            """
            INSERT INTO growth_plan
                (user_profile_id, plan_type, target_name, target_date)
            VALUES (?, 'skill', ?, ?)
            """,
            [
                (profile_id, "Python", "2026-11-25"),
                (profile_id, "Tableau", "2026-12-02"),
            ],
        )

        criteria = JobCriteria(
            required_skills=("SQL", "Python"),
            min_career_months=12,
            preferred_skills=("Tableau",),
            requirements_checked=True,
        )

        result = evaluate_match(db, profile_id, job_id, criteria, date(2026, 11, 19))

    for label, key in (("현재", "current"), ("미래 예상", "future")):
        fit = result[key]
        print(f"{label} 적합도 {fit['score']}점 | 확인된 배점 {fit['assessed_weight']}점")
        print(f"필수조건 판정 {fit['eligibility']} | 확인 필요 역량 {fit['gaps']}")

    print("반영한 성장 계획", result["applied_plans"])


if __name__ == "__main__":
    main()
