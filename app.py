"""
ReBloom server (Team Phoenix Queens).

Serves the website and four API endpoints:
  POST /api/translate  career-break story  -> skills profile        (Groq API, free tier, open models)
  POST /api/plan       child age, support  -> phased return plan    (Groq)
  POST /api/jobs       role, city, filters -> real jobs/internships (Adzuna API, ranked by Groq)
  POST /api/chat       chat messages       -> reply + app actions   (Groq with tool calling)

Groq's API does not keep or train on requests by default, so stories can be sent there.

Every API call needs a Firebase ID token (Google sign-in), so only signed-in
people can spend the API keys. Keys live in environment variables only.
"""
import os
import re
import json
import time
import html
import threading
from functools import wraps

import requests
from flask import Flask, request, jsonify, send_from_directory, g
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

app = Flask(__name__, static_folder="static", static_url_path="")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GRIEVANCE_EMAIL = os.environ.get("GRIEVANCE_EMAIL", "")
GRIEVANCE_NAME = os.environ.get("GRIEVANCE_NAME", "Team Phoenix Queens")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
AI_CALLS_PER_HOUR = int(os.environ.get("AI_CALLS_PER_HOUR", "40"))

_google_req = google_requests.Request()


# --------------------------------------------------------------------------- errors
class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(error=e.code, message=e.message), e.status


@app.errorhandler(requests.RequestException)
def _network_error(e):
    app.logger.warning("Upstream network error: %s", e)
    return jsonify(error="ai_unavailable", message="The AI service didn't answer. Try again in a moment."), 502


# --------------------------------------------------------------------------- pages
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def config():
    """Public settings for the browser. Firebase web config values are not secrets."""
    return jsonify(
        firebase={
            "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
            "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": FIREBASE_PROJECT_ID,
            "appId": os.environ.get("FIREBASE_APP_ID", ""),
        },
        ai=bool(GROQ_API_KEY),
        grievance={"name": GRIEVANCE_NAME, "email": GRIEVANCE_EMAIL},
        jobs=bool(ADZUNA_APP_ID and ADZUNA_APP_KEY),
    )


@app.get("/healthz")
def healthz():
    return "ok"


# --------------------------------------------------------------------------- sign-in check + rate limit
_calls = {}
_calls_lock = threading.Lock()


def signed_in(ai=True):
    def wrap(f):
        @wraps(f)
        def inner(*a, **kw):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or not FIREBASE_PROJECT_ID:
                raise ApiError(401, "signed_out", "Please sign in again.")
            try:
                claims = google_id_token.verify_firebase_token(auth[7:], _google_req, audience=FIREBASE_PROJECT_ID)
            except Exception:
                raise ApiError(401, "signed_out", "Your sign-in expired. Please sign in again.")
            g.uid = claims["sub"]
            if ai:
                if not GROQ_API_KEY:
                    raise ApiError(503, "not_configured", "The AI isn't set up on the server yet (GROQ_API_KEY is missing).")
                now = time.time()
                with _calls_lock:
                    recent = [t for t in _calls.get(g.uid, []) if now - t < 3600]
                    if len(recent) >= AI_CALLS_PER_HOUR:
                        raise ApiError(429, "rate_limited", "You've used ReBloom a lot this hour. Please try again later.")
                    recent.append(now)
                    _calls[g.uid] = recent
            return f(*a, **kw)
        return inner
    return wrap


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "Send JSON.")
    return data


def text_field(data, key, limit, required=False):
    v = data.get(key, "")
    v = v.strip()[:limit] if isinstance(v, str) else ""
    if required and not v:
        raise ApiError(400, "bad_request", f"{key} is required.")
    return v


def lang_of(data):
    return "Kannada" if data.get("lang") == "kn" else "English"


# --------------------------------------------------------------------------- AI helpers (Groq, free tier)
def groq(payload, retry=True):
    r = requests.post(GROQ_URL, json={"model": GROQ_MODEL, **payload}, timeout=60,
                      headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"})
    if r.status_code == 429:
        raise ApiError(429, "rate_limited", "The free AI limit is used up for the moment. Please try again in a minute.")
    if r.status_code == 400 and retry and "tool_use_failed" in r.text:
        return groq(payload, retry=False)      # the model wrote a malformed tool call; one more try
    if r.status_code != 200:
        app.logger.warning("Groq %s: %s", r.status_code, r.text[:400])
        raise ApiError(502, "ai_unavailable", "The AI couldn't answer right now. Try again in a moment.")
    return r.json()["choices"][0]["message"]


def structured(system, prompt, name, schema, max_tokens=2048):
    """Ask the AI for data in a fixed shape (a forced tool call)."""
    msg = groq({
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "tools": [{"type": "function", "function": {"name": name, "description": "Return the result in this exact shape.", "parameters": schema}}],
        "tool_choice": {"type": "function", "function": {"name": name}},
        "temperature": 0.4, "max_tokens": max_tokens,
    })
    for call in msg.get("tool_calls") or []:
        try:
            return json.loads(call["function"]["arguments"])
        except (ValueError, KeyError):
            break
    raise ApiError(502, "ai_unavailable", "The AI didn't return a result. Try again.")


SKILL_AREAS = [
    "Finance & budgeting", "Operations & coordination", "Project & event management", "Negotiation",
    "Teaching & training", "Business & sales", "Communication & community", "Planning & organisation",
    "Care & wellbeing", "Leadership",
]

GUIDE = (
    "You work inside ReBloom, an app by Team Phoenix Queens that helps mothers in India return to work "
    "after a career break for childbirth or caregiving. Treat caregiving and household management as real "
    "work experience. Be specific, honest and kind. Never invent facts about her, companies, salaries or statistics."
)


# --------------------------------------------------------------------------- 1. skills
SKILLS_SCHEMA = {
    "type": "object",
    "properties": {
        "previous_role": {"type": ["string", "null"], "description": "Her job before the break, if she said it"},
        "break_title": {"type": "string", "description": "A professional title for the break period, e.g. 'Family Operations & Care Management'"},
        "break_years": {"type": ["string", "null"], "description": "e.g. '2024–2026' if she gave a length or dates, else null"},
        "summary": {"type": "string", "description": "2 sentences she can paste into her CV, first person"},
        "skills": {
            "type": "array", "maxItems": 8,
            "items": {"type": "object", "properties": {
                "area": {"type": "string", "enum": SKILL_AREAS},
                "skill": {"type": "string", "description": "Professional skill name, 2-6 words"},
                "evidence": {"type": "string", "description": "One line from her story that shows it, rewritten professionally"},
            }, "required": ["area", "skill", "evidence"]},
        },
        "suggested_roles": {"type": "array", "maxItems": 5, "items": {"type": "string"},
                            "description": "Short job-search keywords for roles that fit (in English), e.g. 'QA analyst'"},
    },
    "required": ["break_title", "summary", "skills", "suggested_roles"],
}


def do_translate(story, lang):
    return structured(
        GUIDE + " You turn what a mother did during her career break into a professional skills profile. "
        "Only use what her story supports. If the story has nothing work-like, return an empty skills list.",
        f"Write break_title, summary, skill names and evidence in {lang}. Keep suggested_roles in English.\n\nHer story:\n{story}",
        "skills_profile", SKILLS_SCHEMA,
    )


@app.post("/api/translate")
@signed_in()
def api_translate():
    data = body()
    story = text_field(data, "story", 2000, required=True)
    return jsonify(do_translate(story, lang_of(data)))


# --------------------------------------------------------------------------- 2. plan
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "1-2 sentences on the overall approach"},
        "phases": {
            "type": "array", "minItems": 3, "maxItems": 6,
            "items": {"type": "object", "properties": {
                "title": {"type": "string", "description": "2-4 words"},
                "start_month": {"type": "integer", "minimum": 0, "description": "Child's age in months when the phase starts"},
                "end_month": {"type": ["integer", "null"], "description": "Child's age in months when it ends; null for the last, open-ended phase"},
                "actions": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string"}, "description": "Concrete things to do, one line each"},
                "tip": {"type": "string", "description": "One practical tip for this phase"},
            }, "required": ["title", "start_month", "end_month", "actions", "tip"]},
        },
    },
    "required": ["summary", "phases"],
}
CARE = {"family": "family helps at home", "creche": "a creche or daycare is available nearby", "none": "no regular childcare help yet"}
GOAL = {"full": "a full-time role", "part": "a part-time or flexible role", "remote": "a fully remote role"}


def do_plan(age, care, goal, skills, roles, story, lang):
    return structured(
        GUIDE + " You build realistic, phased return-to-work plans for mothers in India. Phases are contiguous, "
        "start at month 0 (birth), and the last phase is open-ended. Pace the plan to the childcare she has.",
        f"Write all text in {lang}.\n"
        f"Child's age now: {age} months. Childcare: {CARE.get(care, care)}. Goal: {GOAL.get(goal, goal)}.\n"
        f"Her skills: {', '.join(skills) or 'not translated yet'}.\nRoles that fit her: {', '.join(roles) or 'unknown'}.\n"
        f"Her story (may be empty): {story[:800]}\n"
        "Make the phase that contains her child's current age especially concrete.",
        "return_plan", PLAN_SCHEMA,
    )


def plan_inputs(data):
    try:
        age = max(0, min(60, int(data.get("child_age_months", 0))))
    except (TypeError, ValueError):
        age = 0
    care = data.get("childcare") if data.get("childcare") in CARE else "family"
    goal = data.get("goal") if data.get("goal") in GOAL else "full"
    skills = [str(s)[:80] for s in (data.get("skills") or [])][:10]
    roles = [str(s)[:60] for s in (data.get("roles") or [])][:5]
    return age, care, goal, skills, roles, text_field(data, "story", 2000)


@app.post("/api/plan")
@signed_in()
def api_plan():
    data = body()
    return jsonify(do_plan(*plan_inputs(data), lang_of(data)))


# --------------------------------------------------------------------------- 3. jobs (Adzuna, India)
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"
FLAG_PATTERNS = {
    "remote": r"work[\s-]?from[\s-]?home|\bwfh\b|\bremote\b|\bhybrid\b",
    "parttime": r"part[\s-]?time",
    "flexible": r"flexib|flexi[\s-]?(hours|timing|time)",
    "returnship": r"returnship|return[\s-]?to[\s-]?work|career[\s-]?break|second[\s-]?career|women[\s-]?returners?|restart|relaunch",
    "women": r"\bwomen\b|female candidates|diversity hiring",
    "childcare": r"cr[eè]che|day[\s-]?care|child[\s-]?care",
}
_job_cache = {}


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def adzuna_search(what, where, kind):
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        raise ApiError(503, "not_configured", "Job search isn't set up on the server yet (Adzuna keys are missing).")
    query = f"{what} intern" if kind == "internship" else what
    key = (query.lower(), where.lower())
    hit = _job_cache.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    params = {"app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY, "results_per_page": 50,
              "what": query, "max_days_old": 45, "sort_by": "relevance", "content-type": "application/json"}
    if where:
        params["where"] = where
    r = requests.get(ADZUNA_URL, params=params, timeout=15)
    if r.status_code != 200:
        app.logger.warning("Adzuna %s: %s", r.status_code, r.text[:300])
        raise ApiError(502, "jobs_unavailable", "The job search service didn't answer. Try again in a moment.")
    jobs = []
    for j in r.json().get("results", []):
        desc = clean(j.get("description"))
        title = clean(j.get("title"))
        text = f"{title} {desc}".lower()
        flags = [f for f, pat in FLAG_PATTERNS.items() if re.search(pat, text)]
        if j.get("contract_time") == "part_time" and "parttime" not in flags:
            flags.append("parttime")
        jobs.append({
            "id": str(j.get("id")), "title": title,
            "company": clean((j.get("company") or {}).get("display_name")) or None,
            "location": clean((j.get("location") or {}).get("display_name")) or None,
            "created": j.get("created"), "url": j.get("redirect_url"),
            "salary_min": j.get("salary_min"), "salary_max": j.get("salary_max"),
            "snippet": desc[:240], "flags": flags,
        })
    _job_cache[key] = (time.time(), jobs)
    return jobs


RANK_SCHEMA = {
    "type": "object",
    "properties": {"ranked": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"},
        "match": {"type": "integer", "minimum": 0, "maximum": 100},
        "why": {"type": "string", "description": "Why it fits or doesn't, max 14 words"},
    }, "required": ["id", "match", "why"]}}},
    "required": ["ranked"],
}


def do_jobs(what, where, kind, flags, skills, lang):
    jobs = adzuna_search(what, where, kind)
    found = len(jobs)
    if flags:
        jobs = [j for j in jobs if all(f in j["flags"] for f in flags)]
    jobs = jobs[:15]
    if jobs and skills:
        listing = "\n".join(f'{j["id"]} | {j["title"]} | {j["company"] or ""} | {j["snippet"][:200]}' for j in jobs)
        ranked = structured(
            GUIDE + " You score how well real job listings fit a mother returning to work, from her skills. "
            "Judge only from the listing text given.",
            f"Write 'why' in {lang}.\nHer skills: {', '.join(skills)}\n\nListings (id | title | company | excerpt):\n{listing}",
            "rank_jobs", RANK_SCHEMA, max_tokens=1500,
        ).get("ranked", [])
        score = {str(r.get("id")): r for r in ranked}
        for j in jobs:
            r = score.get(j["id"])
            j["match"] = max(0, min(100, int(r["match"]))) if r and str(r.get("match", "")).isdigit() else None
            j["why"] = r.get("why") if r else None
        jobs.sort(key=lambda j: j["match"] or 0, reverse=True)
    return {"jobs": jobs, "total_found": found, "query": {"what": what, "where": where, "kind": kind, "flags": flags}}


def job_inputs(data):
    what = text_field(data, "what", 80, required=True)
    where = text_field(data, "where", 60)
    kind = "internship" if data.get("kind") == "internship" else "job"
    flags = [f for f in (data.get("flags") or []) if f in FLAG_PATTERNS][:4]
    skills = [str(s)[:80] for s in (data.get("skills") or [])][:10]
    return what, where, kind, flags, skills


@app.post("/api/jobs")
@signed_in()
def api_jobs():
    data = body()
    return jsonify(do_jobs(*job_inputs(data), lang_of(data)))


# --------------------------------------------------------------------------- 4. chat agent
CHAT_TOOLS = [
    {"name": "translate_story",
     "description": "Turn her career-break story into a skills profile and show it on her Skills page. Use when she describes what she did during her break.",
     "input_schema": {"type": "object", "properties": {"story": {"type": "string", "description": "Her story in her own words"}}, "required": ["story"]}},
    {"name": "build_plan",
     "description": "Build or rebuild her phased return-to-work plan and show it on her Plan page. Pass only what she told you; anything left out keeps her current setting.",
     "input_schema": {"type": "object", "properties": {
         "child_age_months": {"type": "integer", "minimum": 0, "maximum": 60},
         "childcare": {"type": "string", "enum": list(CARE)},
         "goal": {"type": "string", "enum": list(GOAL)}}}},
    {"name": "search_jobs",
     "description": "Search real, current job or internship listings in India (Adzuna) and show them on her Jobs page. Returns the top matches.",
     "input_schema": {"type": "object", "properties": {
         "what": {"type": "string", "description": "Role or keywords in English"},
         "where": {"type": "string", "description": "City in India, or empty for all India"},
         "kind": {"type": "string", "enum": ["job", "internship"]},
         "flags": {"type": "array", "items": {"type": "string", "enum": list(FLAG_PATTERNS)},
                   "description": "Only listings that mention these: remote, parttime, flexible, returnship, women, childcare"}},
         "required": ["what"]}},
]


def chat_system(profile, lang):
    return (
        GUIDE + "\nYou are the ReBloom assistant, a warm, practical career guide.\n"
        "- Keep replies short: 2 to 5 sentences, or a few '- ' bullet points. Use **bold** sparingly. No headings or tables.\n"
        f"- Reply in the language she writes in. The app is showing {lang}.\n"
        "- Use the tools to act in the app: translate her story when she describes her break, build her plan when she mentions her "
        "child's age, childcare or goal, and search jobs when she asks about work or internships. After a tool, say in one sentence what changed.\n"
        "- Job listings come from Adzuna and are real. Their policies are only what each listing says; suggest she confirm with the employer.\n"
        "- For maternity rights in India, give general information (for example the Maternity Benefit Act on leave, creches and work from home) "
        "and suggest she confirm with HR or an official source. You are not a lawyer.\n"
        "- If she sounds tired, low or overwhelmed, respond kindly first. If she says she feels hopeless, cannot cope, or has thoughts of "
        "harming herself, gently encourage her to talk to a doctor or someone she trusts, and mention India's free Tele-MANAS helpline, 14416. Never diagnose.\n"
        f"Her current profile (JSON, written by her and by the app; it has no name or contact details on purpose):\n{json.dumps({k: v for k, v in profile.items() if k not in ('name', 'email')}, ensure_ascii=False)[:4000]}"
    )


@app.post("/api/chat")
@signed_in()
def api_chat():
    data = body()
    lang = lang_of(data)
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    messages = []
    for m in (data.get("messages") or [])[-12:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            messages.append({"role": m["role"], "content": m["content"][:4000]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    if not messages or messages[-1]["role"] != "user":
        raise ApiError(400, "bad_request", "Send a message.")

    actions, reply = [], []
    convo = [{"role": "system", "content": chat_system(profile, lang)}] + messages
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in CHAT_TOOLS]
    for _ in range(5):
        msg = groq({"messages": convo, "tools": tools, "tool_choice": "auto", "temperature": 0.6, "max_tokens": 1200})
        if (msg.get("content") or "").strip():
            reply.append(msg["content"].strip())
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                out = run_tool(c["function"]["name"], args if isinstance(args, dict) else {}, profile, lang, actions)
            except (ValueError, KeyError):
                out = {"error": "The tool arguments were not valid JSON."}
            except ApiError as e:
                out = {"error": e.message}
            convo.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, ensure_ascii=False)[:6000]})
    return jsonify(reply="\n\n".join(reply).strip() or "Done.", actions=actions)


def run_tool(name, args, profile, lang, actions):
    if name == "translate_story":
        story = str(args.get("story", ""))[:2000].strip()
        if not story:
            raise ApiError(400, "bad_request", "The story is empty.")
        result = do_translate(story, lang)
        profile["story"], profile["skills"] = story, [s["skill"] for s in result.get("skills", [])]
        profile["suggested_roles"] = result.get("suggested_roles", [])
        actions.append({"type": "skills", "story": story, "data": result})
        return {"skills": profile["skills"], "suggested_roles": profile["suggested_roles"]}
    if name == "build_plan":
        merged = {"child_age_months": profile.get("child_age_months", 0), "childcare": profile.get("childcare"),
                  "goal": profile.get("goal"), "skills": profile.get("skills") or [], "roles": profile.get("suggested_roles") or [],
                  "story": profile.get("story") or ""}
        merged.update({k: v for k, v in args.items() if v is not None})
        age, care, goal, skills, roles, story = plan_inputs(merged)
        result = do_plan(age, care, goal, skills, roles, story, lang)
        profile.update(child_age_months=age, childcare=care, goal=goal)
        actions.append({"type": "plan", "inputs": {"age": age, "care": care, "goal": goal}, "data": result})
        return {"summary": result.get("summary"), "phases": [p.get("title") for p in result.get("phases", [])]}
    if name == "search_jobs":
        what, where, kind, flags, _ = job_inputs({**args, "what": args.get("what") or ""})
        result = do_jobs(what, where, kind, flags, profile.get("skills") or [], lang)
        actions.append({"type": "jobs", "data": result})
        return {"total_found": result["total_found"],
                "top": [{k: j.get(k) for k in ("title", "company", "location", "match", "flags")} for j in result["jobs"][:5]]}
    raise ApiError(400, "bad_request", f"Unknown tool {name}.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=bool(os.environ.get("FLASK_DEBUG")))
