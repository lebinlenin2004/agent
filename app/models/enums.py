import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    ADVISOR = "advisor"
    VIEWER = "viewer"


class VacancyStatus(str, enum.Enum):
    ACTIVE_VERIFIED = "active_verified"
    FOUND_UNVERIFIED = "found_unverified"
    CLOSED_VERIFIED = "closed_verified"
    NO_MATCH_FOUND = "no_matching_vacancy_found"
    SEARCH_INCOMPLETE = "search_incomplete"


STATUS_LABELS = {
    VacancyStatus.ACTIVE_VERIFIED: "Active — Verified",
    VacancyStatus.FOUND_UNVERIFIED: "Found — Unverified",
    VacancyStatus.CLOSED_VERIFIED: "Closed — Verified",
    VacancyStatus.NO_MATCH_FOUND: "No Matching Vacancy Found",
    VacancyStatus.SEARCH_INCOMPLETE: "Search Incomplete",
}


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceType(str, enum.Enum):
    COMPANY_CAREERS = "company_careers"
    JOB_PORTAL = "job_portal"
    SEARCH_API = "search_api"
