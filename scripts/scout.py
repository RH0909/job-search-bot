import json, hashlib, re, os, urllib.request, urllib.parse, urllib.error, time
import html as _html
from datetime import datetime
from pathlib import Path
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import JOBS_FILE, REPORT_FILE, PROFILE
JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def make_id(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]

def is_relevant_location(job):
    location = job.get("location", "").lower()
    title = job.get("title", "").lower()
    if any(kw in location for kw in ["remote", "etä"] + [l.lower() for l in PROFILE["locations"]]):
        return True
    if any(kw in title for kw in ["remote", "etä", "freelance"]):
        return True
    if location in ["", "finland", "suomi"]:
        return True
    return False

TODAY = datetime.now().strftime("%Y-%m-%d")
JOB_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
CLOSED_MARKER = "closed-job__flavor--closed"   # "Hakemuksia ei enää hyväksytä" -ilmoitus
SKIP_STATUSES = ("rejected", "applied")        # näitä ei tarkisteta
MAX_EXPIRY_CHECKS = 150                        # max tarkistusta / ajo
CHECK_DELAY = 1.0                              # sekuntia pyyntöjen välillä
KEYWORDS = PROFILE["keywords"]


def linkedin_job_id(job):
    if job.get("li_id"):
        return str(job["li_id"])
    m = re.search(r"(\d{6,})/?$", job.get("url", ""))
    return m.group(1) if m else ""


def extract_description(page):
    match = re.search(r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
                      page, re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = _html.unescape(re.sub(r"\s+", " ", text).strip())
    return text[:8000]


def check_job(job):
    """Tarkista ilmoituksen tila LinkedInin vieras-API:sta ja hae työnkuvaus.

    Palauttaa (tila, kuvaus). Tila:
      open    - ilmoitus on auki
      closed  - "Hakemuksia ei enää hyväksytä"
      gone    - ilmoitus on poistettu (404/410)
      unknown - ei saatu selville (esto, verkkovirhe, tuntematon sivu)
    """
    job_id = linkedin_job_id(job)
    if not job_id:
        return "unknown", ""
    try:
        req = urllib.request.Request(JOB_API.format(job_id), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return ("gone" if e.code in (404, 410) else "unknown"), ""
    except Exception:
        return "unknown", ""
    desc = extract_description(page)
    if CLOSED_MARKER in page:
        return "closed", desc
    if desc or "top-card-layout" in page:
        return "open", desc
    return "unknown", ""


def mark_expired(job, reason):
    job["expired"] = True
    job["expired_reason"] = reason
    job["expired_at"] = TODAY


def load_jobs():
    if JOBS_FILE.exists():
        return {j["id"]: j for j in json.loads(JOBS_FILE.read_text(encoding="utf-8-sig"))}
    return {}


def save_jobs(jobs):
    # Säilytä serve.py:llä ajon aikana tehdyt hylkäykset
    try:
        for j in json.loads(JOBS_FILE.read_text(encoding="utf-8-sig")):
            cur = jobs.get(j["id"])
            if cur and j.get("status") == "rejected" and cur.get("status") != "rejected":
                cur["prev_status"] = j.get("prev_status", cur.get("status"))
                cur["status"] = "rejected"
    except Exception:
        pass
    tmp = JOBS_FILE.with_name(JOBS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(list(jobs.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, JOBS_FILE)


def apply_rejected_file(jobs):
    """Käsittele raportista ladattu rejected.json (tiedostotila)."""
    rejected_file = JOBS_FILE.parent / "rejected.json"
    if not rejected_file.exists():
        return
    try:
        ids = set(json.loads(rejected_file.read_text(encoding="utf-8-sig")))
    except Exception as e:
        print(f"rejected.json virhe: {e}")
        return
    n = 0
    for jid in ids:
        if jid in jobs and jobs[jid].get("status") != "rejected":
            jobs[jid]["prev_status"] = jobs[jid].get("status", "new")
            jobs[jid]["status"] = "rejected"
            n += 1
    print(f"Hylätty raportin perusteella: {n}")


def check_expiry(jobs):
    """Merkitse suljetut ja poistuneet ilmoitukset vanhentuneiksi (expired)."""
    active = [j for j in jobs.values()
              if not j.get("expired")
              and j.get("status") not in SKIP_STATUSES
              and j.get("source") == "linkedin"
              and j.get("last_checked") != TODAY]
    active.sort(key=lambda j: j.get("last_checked", ""))
    batch = active[:MAX_EXPIRY_CHECKS]

    counts = {"open": 0, "closed": 0, "gone": 0, "unknown": 0}
    expired = []
    unknown_streak = 0
    for job in batch:
        state, desc = check_job(job)
        counts[state] += 1
        if desc and not job.get("description"):
            job["description"] = desc
        if state == "unknown":
            unknown_streak += 1
            if unknown_streak >= 5:
                print("  LinkedIn ei vastaa tai rajoittaa pyyntöjä, keskeytetään tarkistus")
                break
        else:
            unknown_streak = 0
            job["last_checked"] = TODAY
            if state in ("closed", "gone"):
                mark_expired(job, state)
                expired.append(job)
        time.sleep(CHECK_DELAY)

    print(f"Vanhentumistarkistus: auki {counts['open']} | suljettu {counts['closed']} | "
          f"poistunut {counts['gone']} | ei tiedossa {counts['unknown']}")
    for j in expired:
        print(f"  - {j['title']} @ {j['company']} ({j['expired_reason']})")


def run_analyst():
    """Pisteytä uudet työpaikat analyst.py:llä."""
    try:
        import analyst
    except Exception as e:
        print(f"Analystia ei voitu ladata: {e}")
        return
    try:
        analyst.main()
    except Exception as e:
        print(f"Analyst epäonnistui: {e}")


def fetch_linkedin(keyword, location=PROFILE["search_location"]):
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={urllib.parse.quote(keyword)}&location={urllib.parse.quote(location)}&start=0"
    )
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")

        jobs = []
        cards = re.findall(
            r'data-entity-urn="urn:li:jobPosting:(\d+)".*?'
            r'href="(https://[^"]+linkedin\.com/jobs/view/[^"]+)".*?'
            r'class="[^"]*base-search-card__title[^"]*"[^>]*>\s*(.*?)\s*</h3>.*?'
            r'class="[^"]*base-search-card__subtitle[^"]*".*?<a[^>]*>\s*(.*?)\s*</a>.*?'
            r'class="[^"]*job-search-card__location[^"]*"[^>]*>\s*(.*?)\s*<',
            html, re.DOTALL
        )
        for job_id, href, title, company, location_str in cards:
            title = re.sub(r'&amp;', '&', re.sub(r'<[^>]+>', '', title)).strip()
            company = re.sub(r'&amp;', '&', re.sub(r'<[^>]+>', '', company)).strip()
            location_str = re.sub(r'<[^>]+>', '', location_str).strip()
            clean_url = href.split('?')[0]
            jobs.append({
                "id": make_id(clean_url),
                "li_id": job_id,
                "title": title,
                "company": company,
                "location": location_str,
                "url": clean_url,
                "source": "linkedin",
                "published": datetime.now().strftime("%Y-%m-%d"),
                "status": "new",
                "description": ""
            })
        return jobs
    except Exception as e:
        print(f"LinkedIn virhe ({keyword}): {e}")
        return []

from datetime import date
import html as _html

REPORT_STYLE = """
<style>
  body { font-family: Arial, sans-serif; margin: 30px; }
  h1, h2 { color: #333; }
  table { border-collapse: collapse; width: 100%; margin-top: 10px; }
  th { background: #333; color: white; padding: 8px 12px; text-align: left; }
  td { padding: 7px 12px; border-bottom: 1px solid #ddd; }
  a { color: #1a73e8; }
  .toolbar { margin: 10px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .toolbar button { padding: 6px 12px; cursor: pointer; }
  td.rej { text-align: center; }
  tr.rejected { display: none; }
  body.show-rejected tr.rejected { display: table-row; opacity: 0.45; text-decoration: line-through; }
  #msg.err { color: #b00020; font-weight: bold; }
</style>
"""

REPORT_SCRIPT = """
<script>
(function () {
  var KEY = 'tyonhaku_rejected';
  var SERVER = location.protocol.indexOf('http') === 0;
  var rejected = new Set();
  var msg = document.getElementById('msg');

  document.querySelectorAll('tr[data-rej]').forEach(function (tr) { rejected.add(tr.dataset.id); });
  if (!SERVER) {
    try { JSON.parse(localStorage.getItem(KEY) || '[]').forEach(function (id) { rejected.add(id); }); } catch (e) {}
  }

  function persistLocal() {
    try { localStorage.setItem(KEY, JSON.stringify(Array.from(rejected))); } catch (e) {}
  }
  function render() {
    document.querySelectorAll('tr[data-id]').forEach(function (tr) {
      var r = rejected.has(tr.dataset.id);
      tr.classList.toggle('rejected', r);
      tr.querySelector('input').checked = r;
    });
    document.getElementById('rej-count').textContent = rejected.size;
  }
  function setMsg(text, isErr) { msg.textContent = text; msg.className = isErr ? 'err' : ''; }

  document.addEventListener('change', function (e) {
    if (e.target.type !== 'checkbox') return;
    var tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    var id = tr.dataset.id, flag = e.target.checked;
    if (flag) rejected.add(id); else rejected.delete(id);
    render();
    if (!SERVER) { persistLocal(); return; }
    fetch('/api/reject', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: id, rejected: flag})
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      setMsg('Tallennettu jobs.json:iin', false);
    }).catch(function (err) {
      if (flag) rejected.delete(id); else rejected.add(id);
      render();
      setMsg('Tallennus epäonnistui: ' + err.message + ' (palvelin käynnissä?)', true);
    });
  });

  document.getElementById('toggle-rej').addEventListener('click', function () {
    document.body.classList.toggle('show-rejected');
  });

  var exp = document.getElementById('export-rej');
  if (SERVER) {
    exp.style.display = 'none';
    setMsg('Palvelintila: ruksit tallentuvat suoraan jobs.json:iin', false);
    fetch('/api/rejected').then(function (r) { return r.json(); }).then(function (ids) {
      rejected = new Set(ids); render();
    }).catch(function () { setMsg('Palvelimeen ei saatu yhteyttä', true); });
  } else {
    setMsg('Tiedostotila: lataa rejected.json ja siirrä se data-kansioon (tai käynnistä scripts/serve.py)', false);
    exp.addEventListener('click', function () {
      var blob = new Blob([JSON.stringify(Array.from(rejected), null, 2)], {type: 'application/json'});
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'rejected.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
    });
  }
  render();
})();
</script>
"""

def generate_report(jobs):
    n_expired = sum(1 for j in jobs if j.get("expired"))
    jobs = [j for j in jobs if not j.get("expired")]
    esc = lambda s: _html.escape(str(s), quote=True)
    today = date.today().strftime("%d.%m.%Y")
    today_iso = date.today().strftime("%Y-%m-%d")
    new = sorted([j for j in jobs if j.get("published") == today_iso],
                 key=lambda x: x.get("score", 0), reverse=True)
    all_analyzed = sorted([j for j in jobs if j.get("score", 0) > 0],
                          key=lambda x: x["score"], reverse=True)

    def attrs(j, extra=""):
        rej = " data-rej='1'" if j.get("status") == "rejected" else ""
        return f"data-id='{esc(j['id'])}'{rej}{extra}"

    checkbox = "<td class='rej'><input type='checkbox' title='Hylkää'></td>"

    rows_new = ""
    for j in new:
        rows_new += (f"<tr {attrs(j)}>{checkbox}<td>{esc(j.get('score', '-'))}</td>"
                     f"<td><a href='{esc(j['url'])}' target='_blank'>{esc(j['title'])}</a></td>"
                     f"<td>{esc(j['company'])}</td><td>{esc(j['location'])}</td><td>{esc(j.get('reason', ''))}</td></tr>")

    rows_all = ""
    for j in all_analyzed:
        score = j.get("score", 0)
        color = "#c8f7c5" if score >= 80 else "#fff9c4" if score >= 70 else "#f5f5f5"
        style = f" style='background:{color}'"
        rows_all += (f"<tr {attrs(j, style)}>{checkbox}<td>{score}</td>"
                     f"<td><a href='{esc(j['url'])}' target='_blank'>{esc(j['title'])}</a></td>"
                     f"<td>{esc(j['company'])}</td><td>{esc(j['location'])}</td><td>{esc(j.get('reason',''))}</td></tr>")

    n_new = len([j for j in new if j.get("status") != "rejected"])

    html = f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<title>Job search report {today}</title>
{REPORT_STYLE}
</head>
<body>
<h1>Työnhakuraportti {today}</h1>
<div class="toolbar">
  <span>Hylätty: <b id="rej-count">0</b></span>
  <button id="toggle-rej" type="button">Näytä/piilota hylätyt</button>
  <button id="export-rej" type="button">Lataa rejected.json</button>
  <span id="msg"></span>
  <span style="color:#777">Vanhentuneita piilotettu: {n_expired}</span>
</div>
<h2>Tänään loydetyt ({n_new} kpl)</h2>
<table><tr><th>Hylkää</th><th>Pisteet</th><th>Nimike</th><th>Yritys</th><th>Sijainti</th><th>Arvio</th></tr>
{rows_new if rows_new else "<tr><td colspan='6'>Ei uusia tanaan</td></tr>"}
</table>
<h2>Kaikki analysoidut pistejärjestyksessa</h2>
<table><tr><th>Hylkää</th><th>Pisteet</th><th>Nimike</th><th>Yritys</th><th>Sijainti</th><th>Arvio</th></tr>
{rows_all if rows_all else "<tr><td colspan='6'>Ei analysoituja</td></tr>"}
</table>
{REPORT_SCRIPT}
</body>
</html>"""

    report_path = REPORT_FILE
    report_path.write_text(html, encoding="utf-8-sig")
    print(f"Raportti: {report_path}")

def main():
    existing = load_jobs()
    apply_rejected_file(existing)

    # Hae
    all_jobs = []
    for kw in KEYWORDS:
        found = fetch_linkedin(kw)
        print(f"  '{kw}': {len(found)} tulosta")
        all_jobs += found

    # Suodatus ja dedup
    relevant = [j for j in all_jobs if is_relevant_location(j)]
    new_jobs = list({j["id"]: j for j in relevant if j["id"] not in existing}.values())

    # Hae työnkuvaus ja tila uusille; suljetut ilmoitukset merkitään heti vanhentuneiksi
    for j in new_jobs:
        state, desc = check_job(j)
        j["description"] = desc
        if state != "unknown":
            j["last_checked"] = TODAY
        if state in ("closed", "gone"):
            mark_expired(j, state)
        existing[j["id"]] = j
        time.sleep(CHECK_DELAY)
    save_jobs(existing)

    # Poista vanhentuneet ilmoitukset
    check_expiry(existing)
    save_jobs(existing)

    # Pisteytä uudet
    run_analyst()

    # Raportti
    jobs_now = load_jobs()
    generate_report(list(jobs_now.values()))

    fresh = [jobs_now[j["id"]] for j in new_jobs
             if j["id"] in jobs_now and not jobs_now[j["id"]].get("expired")]
    n_exp = sum(1 for j in jobs_now.values() if j.get("expired"))
    print(f"Haettu: {len(all_jobs)} | Relevantti: {len(relevant)} | Uusia: {len(fresh)} | "
          f"Yhteensä: {len(jobs_now)} | Vanhentuneita: {n_exp}")

    if fresh:
        print("\n🆕 Uudet työpaikat:")
        for j in sorted(fresh, key=lambda x: x.get("score", 0), reverse=True):
            lang = j.get("lang", "EN")
            print(f"\n[{lang}] {j['title']} @ {j['company']}")
            print(f"📍 {j['location']}")
            if j.get("score"):
                print(f"📊 {j['score']}/100 {j.get('reason', '')}")
            print(f"🔗 {j['url']}")
    else:
        print("Ei uusia työpaikkoja tänään.")


if __name__ == "__main__":
    main()
