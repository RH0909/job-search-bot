"""Set jobs with status 'written' back to 'analyzed' so writer.py drafts them again."""
import json
from config import JOBS_FILE

jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8-sig"))
for j in jobs:
    if j.get("status") == "written":
        j["status"] = "analyzed"
JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Reset done, analyzed jobs with score >= 75: {sum(1 for j in jobs if j.get('score', 0) >= 75)}")
