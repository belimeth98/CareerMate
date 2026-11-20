import json
from contextlib import closing
from datetime import datetime, timezone

from backend.app.db.database import connect_db
from backend.app.db.profile_migrations import ensure_profile_extensions
from backend.app.workspace_schemas import ExperienceFields, GrowthInput, PlanInput, PlanPatch, ProfileInput


class AppProblem(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        self.status, self.code, self.message, self.details = status, code, message, details


def missing(entity="항목"):
    return AppProblem(404, "NOT_FOUND", f"해당 {entity}을 찾을 수 없습니다.")


def _profile(row):
    data = dict(row)
    for field in ("preferred_locations", "preferred_employment_types", "skills", "certifications"):
        data[field] = json.loads(data[field])
    return data


def _growth(row):
    data = dict(row)
    data["expected_skills"] = json.loads(data["expected_skills"])
    return data


class WorkspaceService:
    def __init__(self, connection_factory=connect_db):
        self.connection_factory = connection_factory

    def _connect(self):
        db = self.connection_factory()
        try:
            ensure_profile_extensions(db)
        except Exception:
            db.close()
            raise
        return db

    def profiles(self):
        with closing(self._connect()) as db:
            return {"profiles": [_profile(r) for r in db.execute("SELECT * FROM user_profile ORDER BY id")]}

    def profile(self, profile_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM user_profile WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise missing("프로필")
            return _profile(row)

    def save_profile(self, data: ProfileInput, profile_id=None):
        values = data.model_dump()
        initial = values.pop("initial_experiences")
        if profile_id is not None and initial:
            raise AppProblem(422, "INITIAL_EXPERIENCES_CREATE_ONLY", "기존 프로필에는 경험 추가 기능을 사용해 주세요.")
        for key in ("preferred_locations", "preferred_employment_types", "skills", "certifications"):
            values[key] = json.dumps(values[key], ensure_ascii=False)
        columns = tuple(values)
        with closing(self._connect()) as db, db:
            if profile_id is None:
                profile_id = db.execute(
                    f"INSERT INTO user_profile ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(values.values()),
                ).lastrowid
            else:
                changed = db.execute(
                    f"UPDATE user_profile SET {','.join(key+'=?' for key in columns)} WHERE id=?",
                    (*values.values(), profile_id),
                ).rowcount
                if not changed:
                    raise missing("프로필")
            for experience in initial:
                self._insert_experience(db, profile_id, ExperienceFields.model_validate(experience))
            row = db.execute("SELECT * FROM user_profile WHERE id=?", (profile_id,)).fetchone()
            return _profile(row)

    def growth_plans(self, profile_id):
        self.profile(profile_id)
        with closing(self._connect()) as db:
            return {"growth_plans": [_growth(r) for r in db.execute(
                "SELECT * FROM growth_plan WHERE user_profile_id=? ORDER BY target_date,id", (profile_id,),
            )]}

    def save_growth(self, data: GrowthInput):
        self.profile(data.user_profile_id)
        with closing(self._connect()) as db, db:
            growth_id = db.execute(
                """INSERT INTO growth_plan (user_profile_id,plan_type,target_name,expected_skills,target_date,status)
                   VALUES (?,?,?,?,?,?)""",
                (data.user_profile_id, data.plan_type, data.target_name,
                 json.dumps(data.expected_skills, ensure_ascii=False), data.target_date.isoformat(), data.status),
            ).lastrowid
            return _growth(db.execute("SELECT * FROM growth_plan WHERE id=?", (growth_id,)).fetchone())

    def delete_growth(self, growth_id, profile_id):
        with closing(self._connect()) as db, db:
            if db.execute("SELECT 1 FROM application_plan WHERE growth_plan_id=?", (growth_id,)).fetchone():
                raise AppProblem(409, "GROWTH_PLAN_IN_USE", "연결된 지원 일정을 먼저 수정하거나 삭제해 주세요.")
            if not db.execute("DELETE FROM growth_plan WHERE id=? AND user_profile_id=?", (growth_id, profile_id)).rowcount:
                raise missing("성장 계획")
        return {"id": growth_id, "deleted": True}

    def plans(self, profile_id):
        self.profile(profile_id)
        with closing(self._connect()) as db:
            return {"plans": [dict(row) for row in db.execute(
                "SELECT * FROM application_plan WHERE user_profile_id=? ORDER BY coalesce(due_date,substr(start_at,1,10)),id",
                (profile_id,),
            )]}

    def plan(self, plan_id, profile_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM application_plan WHERE id=? AND user_profile_id=?", (plan_id, profile_id)).fetchone()
            if row is None:
                raise missing("지원 계획")
            return dict(row)

    def save_plan(self, data: PlanInput, plan_id=None):
        values = data.model_dump()
        for field in ("start_at", "end_at"):
            if values[field] is not None:
                values[field] = values[field].astimezone(timezone.utc).isoformat(timespec="microseconds")
        if values["due_date"] is not None:
            values["due_date"] = values["due_date"].isoformat()
        with closing(self._connect()) as db:
            # Serialize conflict detection and the write so simultaneous saves see each other.
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute("SELECT 1 FROM user_profile WHERE id=?", (data.user_profile_id,)).fetchone():
                    raise missing("프로필")
                if data.job_posting_id is not None and not db.execute("SELECT 1 FROM job_posting WHERE id=?", (data.job_posting_id,)).fetchone():
                    raise missing("공고")
                if data.growth_plan_id is not None and not db.execute(
                    "SELECT 1 FROM growth_plan WHERE id=? AND user_profile_id=?", (data.growth_plan_id, data.user_profile_id),
                ).fetchone():
                    raise AppProblem(422, "INVALID_GROWTH_PLAN", "선택한 프로필의 성장 계획을 지정해 주세요.")
                conflicts = []
                if data.start_at is not None and data.status != "cancelled":
                    conflicts = [dict(row) for row in db.execute(
                        """SELECT * FROM application_plan WHERE user_profile_id=? AND status!='cancelled'
                           AND start_at IS NOT NULL AND start_at < ? AND ? < end_at
                           AND (? IS NULL OR id!=?) ORDER BY start_at,id""",
                        (data.user_profile_id, values["end_at"], values["start_at"], plan_id, plan_id),
                    )]
                if plan_id is None:
                    values["created_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                    plan_id = db.execute(
                        f"INSERT INTO application_plan ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values()),
                    ).lastrowid
                elif not db.execute(
                    f"UPDATE application_plan SET {','.join(key+'=?' for key in values)} WHERE id=? AND user_profile_id=?",
                    (*values.values(), plan_id, data.user_profile_id),
                ).rowcount:
                    raise missing("지원 계획")
                saved = dict(db.execute("SELECT * FROM application_plan WHERE id=?", (plan_id,)).fetchone())
                db.commit()
            except Exception:
                db.rollback()
                raise
        warning = f"시간이 겹치는 일정 {len(conflicts)}건이 있습니다. 계획은 저장했습니다." if conflicts else None
        return {"plan": saved, "conflicts": conflicts, "warning": warning}

    def patch_plan(self, plan_id, profile_id, patch: PlanPatch):
        current = self.plan(plan_id, profile_id)
        current.pop("id")
        current.pop("created_at")
        current.update(patch.model_dump(exclude_unset=True))
        try:
            data = PlanInput.model_validate(current)
        except ValueError:
            raise AppProblem(422, "INVALID_SCHEDULE", "날짜 또는 시작·종료 시간을 확인해 주세요. 종료는 시작보다 늦어야 합니다.") from None
        return self.save_plan(data, plan_id)

    def delete_plan(self, plan_id, profile_id):
        with closing(self._connect()) as db, db:
            if not db.execute("DELETE FROM application_plan WHERE id=? AND user_profile_id=?", (plan_id, profile_id)).rowcount:
                raise missing("지원 계획")
        return {"id": plan_id, "deleted": True}

    @staticmethod
    def _experience_values(data):
        values = data.model_dump(mode="json")
        values["skills"] = json.dumps(list(dict.fromkeys(s.strip() for s in values["skills"] if s.strip())), ensure_ascii=False)
        return values

    @staticmethod
    def _experience(row):
        result = dict(row)
        result["skills"] = json.loads(result.get("skills") or "[]")
        return result

    def _insert_experience(self, db, profile_id, data):
        values = {"user_profile_id": profile_id, **self._experience_values(data)}
        return db.execute(f"INSERT INTO experience ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values())).lastrowid

    def experiences(self, profile_id):
        self.profile(profile_id)
        with closing(self._connect()) as db:
            return {"experiences": [self._experience(row) for row in db.execute(
                "SELECT * FROM experience WHERE user_profile_id=? ORDER BY id", (profile_id,))]}

    def save_experience(self, profile_id, data: ExperienceFields, experience_id=None):
        self.profile(profile_id)
        with closing(self._connect()) as db, db:
            if experience_id is None:
                experience_id = self._insert_experience(db, profile_id, data)
            else:
                values = self._experience_values(data)
                if not db.execute(f"UPDATE experience SET {','.join(k+'=?' for k in values)} WHERE id=? AND user_profile_id=?",
                                  (*values.values(), experience_id, profile_id)).rowcount:
                    raise missing("경험")
            return self._experience(db.execute("SELECT * FROM experience WHERE id=? AND user_profile_id=?", (experience_id, profile_id)).fetchone())

    def delete_experience(self, profile_id, experience_id):
        with closing(self._connect()) as db, db:
            if not db.execute("DELETE FROM experience WHERE id=? AND user_profile_id=?", (experience_id, profile_id)).rowcount:
                raise missing("경험")
        return {"id": experience_id, "deleted": True}
