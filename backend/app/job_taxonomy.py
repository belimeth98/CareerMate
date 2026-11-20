"""Shared, explainable job matching. Provider classifications remain evidence only."""

import re

CATEGORY_LABELS = {
    "software": "소프트웨어 개발",
    "data_ai": "데이터·AI",
    "infrastructure": "시스템·인프라",
    "security": "정보보안",
    "communications": "통신",
    "it_general": "IT·정보통신 분야",
}


def english(word):
    # ASCII boundaries allow 'IT운영' but exclude 'Recruitment', 'Audit', 'Railway'.
    return rf"(?<![a-z0-9])(?:{word})(?![a-z0-9])"


BACKEND = r"백\s*엔드|" + english(r"back[\s-]?end") + r"|서버\s*(?:개발|프로그래)"
FRONTEND = r"프[론런]트\s*엔드|" + english(r"front[\s-]?end")
PATTERNS = {
    "software": (
        BACKEND, FRONTEND, r"소프트웨어|프로그래머|프로그래밍|응용\s*프로그램",
        r"(?:웹|앱|어플리케이션|애플리케이션|모바일|게임|풀스택|펌웨어)\s*(?:개발|프로그래)",
        english(r"software|programmer|programming|full[\s-]?stack|firmware"),
        english(r"(?:game|web|mobile|application|client)[\s-]+(?:software[\s-]+)?(?:engineer|developer)\w*|gameplay[\s-]?(?:engineer|programmer)|unreal|unity"),
        english(r"python|java|javascript|typescript|react|vue(?:\.js)?|node\.js|spring|fastapi|django|c\+\+|c#"),
    ),
    "data_ai": (
        r"인공지능|머신\s*러닝|딥\s*러닝|빅\s*데이터|데이터\s*(?:분석|엔지니어|사이언|아키텍|플랫폼|파이프라인)",
        english(r"ai|machine[\s-]?learning|deep[\s-]?learning|data[\s-]?(?:engineer\w*|scientist\w*|analyst\w*)|mlops"),
    ),
    "infrastructure": (
        r"전산|정보\s*시스템|클라우드|데이터\s*센터|네트워크|시스템\s*(?:운영|관리|엔지니어)|서버\s*(?:운영|관리|엔지니어)",
        r"(?:어플리케이션|애플리케이션|응용\s*프로그램)\s*(?:운영|관리)|(?<![a-z0-9])(?:erp|mes|sap)\s*(?:운영|개발|컨설|구축|유지)",
        english(r"devops|sre|dba|cloud|network|infrastructure|it\s*(?:운영|지원|인프라)"),
    ),
    "security": (
        r"정보\s*보안|정보\s*보호|사이버\s*보안|시스템\s*보안|네트워크\s*보안|보안\s*(?:개발|엔지니어|관제)",
        english(r"cyber[\s-]?security|information[\s-]?security|soc"),
    ),
    "communications": (r"정보\s*통신|통신\s*(?:설비|망|기사|기술|엔지니어|운영|분야)|유무선\s*통신", english(r"telecom\w*")),
}
IT_WORD = english("it") + r"|정보\s*기술|아이티"
NON_IT_DEVELOPMENT = re.compile(r"(?:의약품|신약|제약|제품|의료\s*기기|부동산|도시|지역|인재|역량|능력|교육\s*과정|메뉴|상품|자동차|전기차|기계|소재|재료|화학|공정|기구|회로|배터리)\s*(?:연구\s*)?개발", re.I)

# Exact aliases: focused queries do not silently widen to the entire IT category.
ALIASES = {
    "it": (set(CATEGORY_LABELS), None), "정보통신": (set(CATEGORY_LABELS), None),
    "정보기술": (set(CATEGORY_LABELS), None), "아이티": (set(CATEGORY_LABELS), None),
    "소프트웨어": ({"software"}, None), "software": ({"software"}, None),
    "소프트웨어개발자": ({"software"}, None), "소프트웨어개발": ({"software"}, None),
    "개발": ({"software"}, None), "개발자": ({"software"}, None), "프로그래머": ({"software"}, None),
    "백엔드": ({"software"}, BACKEND), "백엔드개발자": ({"software"}, BACKEND), "backend": ({"software"}, BACKEND),
    "프론트엔드": ({"software"}, FRONTEND), "프런트엔드": ({"software"}, FRONTEND), "frontend": ({"software"}, FRONTEND),
    "데이터": ({"data_ai"}, None), "ai": ({"data_ai"}, None), "인공지능": ({"data_ai"}, None),
    "데이터분석": ({"data_ai"}, r"데이터\s*분석|"+english(r"data[\s-]?analyst\w*")),
    "인프라": ({"infrastructure"}, None), "전산": ({"infrastructure"}, None),
    "클라우드": ({"infrastructure"}, r"클라우드|"+english("cloud")),
    "네트워크": ({"infrastructure"}, r"네트워크|"+english("network")),
    "서버": ({"infrastructure", "software"}, r"서버|"+english("server")),
    "보안": ({"security"}, None), "정보보안": ({"security"}, None), "정보보호": ({"security"}, None),
    "통신": ({"communications"}, None),
}


def search_group(keyword):
    return ALIASES.get(re.sub(r"[\s_-]+", "", keyword.casefold().strip()))


def role_title(job):
    title = job.get("title") or ""
    prefix = re.match(r"^\s*\[([^\]]+)\]\s*", title)
    normalize = lambda s: re.sub(r"[\s()]", "", re.sub(r"\(주\)|㈜|주식회사", "", s.casefold()))
    if prefix and normalize(prefix[1]) == normalize(job.get("company") or ""):
        title = title[prefix.end():]
    company = (job.get("company") or "").strip()
    if len(company) >= 3:
        title = re.sub(re.escape(company), " ", title, flags=re.I)
    # A combined grade name is not evidence of the field actually being recruited.
    title = title.replace("전기기계통신기사", "기술직")
    title = re.sub(r"AI에\s*대체되지\s*않을", "", title, flags=re.I)
    return title


def classify(job):
    title = role_title(job)
    categories = []
    for category, patterns in PATTERNS.items():
        if any(re.search(p, title, re.I) for p in patterns):
            categories.append(category)
    if re.search(IT_WORD, title, re.I):
        categories.append("it_general")
    if "software" not in categories and re.search(r"(?<![가-힣])개발자(?![가-힣])", title) and not NON_IT_DEVELOPMENT.search(title):
        categories.append("software")
    return categories


def match_job(job, keyword):
    """Return safe public evidence or None. No profile, LLM, or detail crawling."""
    group = search_group(keyword)
    title = role_title(job)
    categories = classify(job)
    if group:
        wanted, focused = group
        direct = bool(set(categories) & wanted) and (not focused or bool(re.search(focused, title, re.I)))
        if direct:
            return {"level": "direct", "rank": 2, "categories": categories,
                    "category_labels": [CATEGORY_LABELS[c] for c in categories],
                    "reasons": ["제목의 직무 표현이 공통 검색 분류와 일치합니다."]}
        # Narrow requests such as backend must not return unrelated frontend jobs.
        # NCS/RSS broad classifications alone never prove that a specific role exists.
        broad = wanted == set(CATEGORY_LABELS) and focused is None
        if not broad or NON_IT_DEVELOPMENT.search(title):
            return None
        reasons = []
        if any("정보통신" in re.sub(r"[.·\s]", "", str(c)) for c in (job.get("ncs_categories") or [])):
            reasons.append("NCS 정보통신 분류 포함 · 여러 직무를 함께 모집할 수 있어 확인이 필요합니다.")
        if job.get("source") == "incruit" and "150" in (job.get("rss_categories") or []):
            reasons.append("인크루트 IT 분야 RSS에 포함 · 구체적인 모집 직무는 원문 확인이 필요합니다.")
        if reasons:
            return {"level": "related", "rank": 1, "categories": ["it_general"],
                    "category_labels": [CATEGORY_LABELS["it_general"]], "reasons": reasons}
        return None
    # Preserve ordinary employer/job word searches; ASCII terms use token boundaries.
    text = f"{job.get('title') or ''} {job.get('company') or ''}"
    terms = keyword.split()
    if not all(re.search(english(re.escape(term)), text, re.I) if re.fullmatch(r"[a-zA-Z0-9+#.]+", term)
               else term.casefold() in text.casefold() for term in terms):
        return None
    return {"level": "direct", "rank": 2 if terms else 0, "categories": categories,
            "category_labels": [CATEGORY_LABELS[c] for c in categories],
            "reasons": ["요청한 검색어가 제목 또는 기관명과 일치합니다."] if terms else []}


def search_priority(job):
    """Stable sorting keeps date order and provider interleaving within a match tier."""
    return -(job.get("search_match") or {}).get("rank", 0)
