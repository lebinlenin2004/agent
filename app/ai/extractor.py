import json
import logging
import re

from pydantic import ValidationError

from app.ai.llm_client import LLMClient, LLMError
from app.ai.prompts import EXTRACT_SYSTEM
from app.ai.sanitize import clean_field, neutralize
from app.ai.schemas import ExtractedJob
from app.ai.vocab import TECH_VOCAB
from app.research.parsing import ParsedPage

log = logging.getLogger(__name__)
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", re.I)
_PLUS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", re.I)


def skills_in_text(text: str) -> list[str]:
    low = " " + text.lower() + " "
    found = []
    for t in TECH_VOCAB:
        if re.search(r"(?<![a-z0-9+#.])" + re.escape(t) + r"(?![a-z0-9+#])", low):
            found.append(t)
    return found


def parse_years(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    m = _RANGE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _PLUS.search(text)
    return (float(m.group(1)), None) if m else (None, None)


def _from_json_ld(node: dict) -> ExtractedJob | None:
    desc = clean_field(node.get("description"), 8000) or ""
    loc = node.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    place = None
    if isinstance(loc, dict):
        a = loc.get("address", {})
        place = a.get("addressLocality") if isinstance(a, dict) else str(a)
    if node.get("jobLocationType") == "TELECOMMUTE":
        place = place or "Remote"
    skills = node.get("skills") or []
    skills = [s.strip() for s in skills.split(",")] if isinstance(skills, str) else list(skills)
    skills = skills or skills_in_text(desc)
    exp = node.get("experienceRequirements")
    exp = exp.get("description") if isinstance(exp, dict) else exp
    mn, mx = parse_years(str(exp) if exp else desc)
    sal = node.get("baseSalary")
    salary = None
    if isinstance(sal, dict):
        v = sal.get("value", {})
        salary = f"{sal.get('currency', '')} {v.get('minValue', '')}-{v.get('maxValue', '')}".strip() if isinstance(v, dict) else None
    try:
        return ExtractedJob(title=node.get("title", ""), location=place, skills=skills, experience_text=exp if isinstance(exp, str) else None,
                            experience_min_years=mn, experience_max_years=mx, salary_text=salary, description=desc,
                            application_url=node.get("url"), posted_date=node.get("datePosted"))
    except ValidationError:
        return None


def _heuristic(page: ParsedPage, url: str) -> ExtractedJob | None:
    title = page.h1 or page.title.split(" - ")[0]
    text = page.text
    if not title or not re.search(r"(responsibilit|requirement|qualification|skills|apply|experience)", text, re.I):
        return None
    skills = skills_in_text(text)
    if not skills and not re.search(r"engineer|developer|analyst|designer|manager|scientist", title, re.I):
        return None
    loc = re.search(r"Location:\s*([A-Za-z ,/-]{2,60}?)(?=\s+(?:Required|Experience|Salary|Apply|Skills)|$|\.)", text)
    exp = re.search(r"Experience:\s*([^.]{3,40})", text)
    mn, mx = parse_years(exp.group(1) if exp else text)
    try:
        return ExtractedJob(title=title, location=loc.group(1) if loc else None, skills=skills,
                            experience_text=exp.group(1) if exp else None, experience_min_years=mn,
                            experience_max_years=mx, description=text[:3000])
    except ValidationError:
        return None


def _llm_extract(page: ParsedPage, url: str, llm: LLMClient) -> ExtractedJob | None:
    user = f"Page URL: {url}\n<untrusted_page>\n{neutralize(page.text[:12000])}\n</untrusted_page>"
    raw = llm.complete(EXTRACT_SYSTEM, user)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON in model output")
    data = json.loads(raw[start:end + 1])
    if not data.get("is_job_posting", False):
        return None
    allowed = ExtractedJob.model_fields.keys()
    return ExtractedJob(**{k: v for k, v in data.items() if k in allowed})  # schema validates & sanitises


def extract_job(page: ParsedPage, url: str, llm: LLMClient | None) -> ExtractedJob | None:
    """Structured data in the page (JSON-LD) wins; the LLM (or heuristics offline) handles everything else."""
    for node in page.json_ld_jobs:
        job = _from_json_ld(node)
        if job and job.title:
            return job
    if llm is not None:
        try:
            return _llm_extract(page, url, llm)
        except (LLMError, ValueError, ValidationError, json.JSONDecodeError):
            log.info("LLM extraction failed for %s; using heuristics", url.split("/")[2] if "//" in url else url)
    return _heuristic(page, url)
