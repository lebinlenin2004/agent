import json
import logging

from pydantic import BaseModel, ValidationError

from app.ai.extractor import skills_in_text
from app.ai.llm_client import LLMClient, LLMError
from app.ai.prompts import MATCH_SYSTEM
from app.ai.sanitize import clean_field, neutralize
from app.ai.schemas import ExtractedJob, MatchResult
from app.ai.vocab import EXPERIENCE_RANGES, ROLE_SYNONYMS
from app.core.utils import normalize_text
from app.schemas.search import Criteria

log = logging.getLogger(__name__)


class _LLMMatch(BaseModel):
    relevance_score: int
    matched_technologies: list[str] = []
    rationale: str = ""


def _role_variants(role: str, include_related: bool) -> list[str]:
    r = normalize_text(role)
    out = [r]
    if include_related:
        out += [normalize_text(x) for x in ROLE_SYNONYMS.get(r, [])]
    return out


def rule_score(job: ExtractedJob, c: Criteria) -> MatchResult:
    title = normalize_text(job.title)
    haystack = normalize_text(" ".join([job.title, " ".join(job.skills), job.description or ""]))
    have = {normalize_text(s) for s in job.skills} | {normalize_text(s) for s in skills_in_text(job.description or "")}
    notes, parts = [], []

    # Only criteria the advisor actually specified count; unspecified ones add nothing (no free points).
    weights: dict[str, float] = {}
    # technologies (45)
    if c.technology_stacks:
        matched = [s for s in c.technology_stacks if normalize_text(s) in have or f" {normalize_text(s)} " in f" {haystack} "]
        stack = len(matched) / len(c.technology_stacks)
        weights['stack'] = 45
        parts.append(f"matches {len(matched)} of {len(c.technology_stacks)} requested technologies"
                     + (f" ({', '.join(matched)})" if matched else ""))
    else:
        matched, stack = [], 1.0
    # role (30)
    role, role_hit = 1.0, None
    if c.target_roles:
        weights['role'] = 30
        role = 0.0
        for r in c.target_roles:
            for i, v in enumerate(_role_variants(r, c.include_related_roles)):
                if v and v in title:
                    role, role_hit = (1.0 if i == 0 else 0.8), r
                    break
                words = set(v.split())
                overlap = len(words & set(title.split())) / len(words) if words else 0
                if overlap > role:
                    role, role_hit = overlap * 0.7, r
            if role >= 1.0:
                break
        parts.append(f"title aligns with '{role_hit}'" if role >= 0.5 and role_hit else "title does not clearly match the requested roles")
    # location (15)
    if not c.locations:
        loc = 1.0
    elif not job.location:
        weights['loc'] = 15
        loc, _ = 0.5, notes.append("location not stated")
    else:
        weights['loc'] = 15
        jl = normalize_text(job.location)
        loc = 1.0 if any(normalize_text(x) in jl for x in c.locations) or "remote" in jl else 0.0
        parts.append(f"location {job.location} {'fits' if loc else 'is outside'} the requested locations")
    # experience (10)
    if not c.experience_levels:
        exp = 1.0
    elif job.experience_min_years is None:
        weights['exp'] = 10
        exp, _ = 0.5, notes.append("experience not stated")
    else:
        weights['exp'] = 10
        lo = job.experience_min_years
        hi = job.experience_max_years if job.experience_max_years is not None else lo + 1
        exp = 1.0 if any(lo <= EXPERIENCE_RANGES[l][1] and hi >= EXPERIENCE_RANGES[l][0] for l in c.experience_levels) else 0.0
        parts.append("experience requirement " + ("fits" if exp else "is outside") + " the requested levels")
    got = {'stack': stack, 'role': role, 'loc': loc, 'exp': exp}
    total_w = sum(weights.values()) or 1
    score = round(100 * sum(w * got[k] for k, w in weights.items()) / total_w, 1)
    return MatchResult(score=score, matched_skills=matched, method="rules", notes=notes,
                       rationale=(parts[0][0].upper() + parts[0][1:] + "; " + "; ".join(parts[1:]) + ".") if parts else "No criteria to compare.")


def assess_match(job: ExtractedJob, c: Criteria, llm: LLMClient | None) -> MatchResult:
    base = rule_score(job, c)
    if llm is None:
        return base
    summary = (f"Title: {job.title}\nLocation: {job.location}\nSkills: {', '.join(job.skills)}\n"
               f"Experience: {job.experience_text}\nDescription: {(job.description or '')[:1500]}")
    request = (f"Roles: {c.target_roles}\nTechnologies: {c.technology_stacks}\nLocations: {c.locations}\n"
               f"Experience levels: {c.experience_levels}\nInclude related roles: {c.include_related_roles}")
    try:
        raw = llm.complete(MATCH_SYSTEM, f"Search request:\n{request}\n<untrusted_job>\n{neutralize(summary)}\n</untrusted_job>", 600)
        data = _LLMMatch(**json.loads(raw[raw.find("{"):raw.rfind("}") + 1]))
        llm_score = max(0, min(100, data.relevance_score))
        # Blend with the deterministic score so a manipulated page can't push the score to an extreme alone.
        blended = 0.6 * base.score + 0.4 * llm_score
        final = round(min(max(blended, base.score - 20), base.score + 20), 1)  # LLM can nudge by at most 20 points
        return MatchResult(score=final, matched_skills=[clean_field(m, 60) or "" for m in data.matched_technologies][:20] or base.matched_skills,
                           rationale=clean_field(data.rationale, 600) or base.rationale, method="rules+llm",
                           notes=base.notes + [f"rules={base.score}", f"llm={llm_score}"])
    except (LLMError, ValueError, ValidationError, json.JSONDecodeError):
        log.info("LLM match assessment failed; using rule-based score")
        base.notes.append("LLM assessment unavailable; rule-based score used")
        return base
