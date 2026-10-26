import json, urllib.request, time, os
from pathlib import Path
from datetime import datetime
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import JOBS_FILE, CV_FILE, OUTPUT_DIR, OPENROUTER_URL, MODEL, PROFILE, load_api_key

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MIN_SCORE = PROFILE["min_score"]

API_KEY = load_api_key()

def write_application(job, cv_text):
    lang = job.get("lang", "EN")
    desc = job.get("description", "")
    desc_section = f"\nJob description:\n{desc[:4000]}" if desc else ""

    if lang == "FI":
        prompt = f"""Kirjoita suomenkielinen tyohakemus.

CV:
{cv_text}

Tyopaikka: {job['title']} @ {job['company']} ({job['location']}){desc_section}

Kirjoita ammattimainen 3-4 kappaleen hakemus. Mainitse yritys nimelta. Paaty toimintakutsuun.
Kirjoita VAIN hakemuskirjeen teksti."""
    else:
        prompt = f"""Write a job application in English.

CV:
{cv_text}

Job: {job['title']} @ {job['company']} ({job['location']}){desc_section}

Write a professional 3-4 paragraph application. Mention the company by name. End with a call to action.
Write ONLY the application letter text."""

    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
        "temperature": 0.7
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            OPENROUTER_URL, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
                "X-Title": "job-search-bot"
            }
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        content = data["choices"][0]["message"].get("content", "").strip()
        # Korjaa mahdolliset merkistoviat
        content = content.encode("utf-8", errors="ignore").decode("utf-8")
        return content
        #return data["choices"][0]["message"].get("content", "").strip()
    except Exception as e:
        return f"Virhe: {e}"

def main():
    if not API_KEY:
        print("OPENROUTER_API_KEY puuttuu!")
        return
    if not JOBS_FILE.exists():
        print("jobs.json puuttuu")
        return

    cv_text = CV_FILE.read_text(encoding="utf-8") if CV_FILE.exists() else ""
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))

    candidates = [j for j in jobs
                  if j.get("score", 0) >= MIN_SCORE
                  and not j.get("expired")
                  and j.get("status") not in ["applied", "written", "rejected"]]
    candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

    print(f"Kirjoitetaan hakemukset {len(candidates)} tyopaikalle (score >= {MIN_SCORE})...\n")

    for i, job in enumerate(candidates):
        lang = job.get("lang", "EN")
        print(f"[{i+1}/{len(candidates)}] [{lang}] {job['title']} @ {job['company']} ({job['score']}/100)")
        application = write_application(job, cv_text)

        safe_name = f"{job['score']}_{job['company'].replace(' ', '_')}_{job['title'].replace(' ', '_')[:30]}"
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "_-")
        filename = OUTPUT_DIR / f"{safe_name}.md"

        content = f"""# {job['title']} @ {job['company']}

Pisteet: {job['score']}/100
Kieli: {lang}
Sijainti: {job['location']}
URL: {job['url']}
Paivamaara: {datetime.now().strftime('%Y-%m-%d')}

---

{application}
"""
        filename.write_text(content, encoding="utf-8")
        job["status"] = "written"
        print(f"  -> {filename.name}\n")
        time.sleep(2)

    JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Valmis! Hakemukset kansiossa: the applications/ folder in the data directory")

if __name__ == "__main__":
    main()