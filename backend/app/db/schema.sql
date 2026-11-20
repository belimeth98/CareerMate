BEGIN;

-- Internal RSS snapshot metadata, including a successful empty snapshot.
CREATE TABLE IF NOT EXISTS job_source_snapshot (
    source TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK(item_count >= 0),
    job_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY,
    profile_name TEXT,
    desired_role TEXT NOT NULL,
    preferred_locations TEXT NOT NULL DEFAULT '[]',
    preferred_employment_types TEXT NOT NULL DEFAULT '[]',
    education_level TEXT,
    education_status TEXT,
    major TEXT,
    career_months INTEGER CHECK (career_months >= 0),
    skills TEXT NOT NULL DEFAULT '[]',
    certifications TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS job_posting (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    board_id TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    employment_type TEXT,
    career_type TEXT,
    ncs_categories TEXT,
    rss_categories TEXT NOT NULL DEFAULT '[]',
    deadline TEXT,
    is_open INTEGER CHECK (is_open IN (0, 1)),
    job_url TEXT,
    description_html TEXT,
    required_qualifications TEXT,
    preferred_qualifications TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE (source, board_id, source_job_id)
);

CREATE TABLE IF NOT EXISTS experience (
    id INTEGER PRIMARY KEY,
    user_profile_id INTEGER NOT NULL REFERENCES user_profile(id),
    experience_type TEXT NOT NULL
        CHECK (experience_type IN ('work', 'project', 'other')),
    title TEXT NOT NULL,
    organization TEXT,
    role TEXT,
    start_date TEXT,
    end_date TEXT,
    description TEXT,
    situation TEXT,
    task TEXT,
    action TEXT,
    result TEXT,
    skills TEXT NOT NULL DEFAULT '[]',
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS growth_plan (
    id INTEGER PRIMARY KEY,
    user_profile_id INTEGER NOT NULL REFERENCES user_profile(id),
    plan_type TEXT NOT NULL
        CHECK (plan_type IN ('skill', 'certification', 'project')),
    target_name TEXT NOT NULL,
    expected_skills TEXT NOT NULL DEFAULT '[]',
    target_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'cancelled')),
    UNIQUE (user_profile_id, id)
);

CREATE TABLE IF NOT EXISTS application_plan (
    id INTEGER PRIMARY KEY,
    user_profile_id INTEGER NOT NULL REFERENCES user_profile(id),
    job_posting_id INTEGER REFERENCES job_posting(id),
    growth_plan_id INTEGER,
    plan_type TEXT NOT NULL
        CHECK (plan_type IN ('cert_registration', 'portfolio', 'coding_test', 'application', 'other')),
    title TEXT NOT NULL,
    due_date TEXT,
    start_at TEXT,
    end_at TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'cancelled')),
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_profile_id, growth_plan_id)
        REFERENCES growth_plan(user_profile_id, id),
    CHECK (due_date IS NOT NULL OR start_at IS NOT NULL),
    CHECK (
        (start_at IS NULL AND end_at IS NULL)
        OR (start_at IS NOT NULL AND end_at IS NOT NULL AND end_at > start_at)
    )
);

CREATE TABLE IF NOT EXISTS match_result (
    id INTEGER PRIMARY KEY,
    user_profile_id INTEGER NOT NULL REFERENCES user_profile(id),
    job_posting_id INTEGER NOT NULL REFERENCES job_posting(id),
    current_score INTEGER NOT NULL
        CHECK (typeof(current_score) = 'integer' AND current_score BETWEEN 0 AND 100),
    future_score INTEGER NOT NULL
        CHECK (typeof(future_score) = 'integer' AND future_score BETWEEN 0 AND 100),
    current_eligibility TEXT NOT NULL
        CHECK (current_eligibility IN ('met', 'unmet', 'unknown')),
    future_eligibility TEXT NOT NULL
        CHECK (future_eligibility IN ('met', 'unmet', 'unknown')),
    eligibility_reason TEXT,
    recommendation_reason TEXT NOT NULL,
    current_gaps TEXT NOT NULL DEFAULT '[]',
    future_gaps TEXT NOT NULL DEFAULT '[]',
    evaluated_at TEXT NOT NULL,
    UNIQUE (user_profile_id, job_posting_id)
);

CREATE TABLE IF NOT EXISTS execution_event (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    duration_ms INTEGER CHECK (duration_ms >= 0),
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_execution_event_session_created_at
    ON execution_event (session_id, created_at);

COMMIT;
