"""Shared configuration: paths, profile and API key.

All personal data (CV, profile, jobs, applications, .env) lives in the data
directory, which is git-ignored. Override its location with JOBBOT_DATA.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBBOT_DATA", ROOT / "data"))

JOBS_FILE = DATA_DIR / "jobs.json"
CV_FILE = DATA_DIR / "cv.md"
REPORT_FILE = DATA_DIR / "report.html"
OUTPUT_DIR = DATA_DIR / "applications"
PROFILE_FILE = DATA_DIR / "profile.json"

MODEL = os.environ.get("JOBBOT_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_PROFILE = {
    "skills": "Python, SQL, data analysis, reporting, 10 years of experience",
    "keywords": ["data engineer", "data scientist", "machine learning engineer", "MLOps", "AI engineer"],
    "search_location": "Finland",
    "locations": [],          # extra location substrings that count as relevant, e.g. ["helsinki"]
    "min_score": 75,          # writer.py only drafts applications at or above this score
}


def load_profile():
    profile = dict(DEFAULT_PROFILE)
    if PROFILE_FILE.exists():
        profile.update(json.loads(PROFILE_FILE.read_text(encoding="utf-8-sig")))
    return profile


PROFILE = load_profile()


def load_api_key():
    """OPENROUTER_API_KEY from the environment, then from .env in the repo root or data dir."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    for env_file in (ROOT / ".env", DATA_DIR / ".env"):
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""
