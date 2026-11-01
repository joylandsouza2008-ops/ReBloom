# ReBloom

**Motherhood isn't a gap.** ReBloom helps mothers in India return to work after a career break. It turns the years spent caring for a family into skills employers understand, builds a return plan around her child's age, and finds real jobs and internships.

Built by **Team Phoenix Queens** for the Sahyadri Science Talent Hunt 2026.

## What it does

| Page | What it does | Powered by |
|------|--------------|------------|
| **Sign in** | Google sign-in, or **guest mode** to try it without an account. Each mother's story, plan and searches are saved to her private profile. Guests can save their work to Google later. | Firebase Auth (Google + Anonymous) + Firestore |
| **Skills** | She writes about her break; the AI turns it into a skills profile, a CV summary and roles to search for. | Groq API (Llama) |
| **Plan** | A phased return-to-work plan built from her child's age, childcare and goal. | Groq API |
| **Jobs** | Real, current jobs and internships in India, filtered by what listings mention (work from home, part-time, flexible hours, returnship, women encouraged) and ranked against her skills. | Jooble API + Groq API |
| **Ask** | A chat assistant in English or Kannada that can also translate her story, build her plan and search jobs for her. | Groq API (tool calling) |
| **Privacy and Terms** | Privacy Notice (with a Kannada summary), Terms of Use, consent at sign-in, download and delete your data. | DPDP Act, 2023 |

No sample or made-up data: every job is a live listing from Jooble with a link to apply. Filters only check what each listing says, so details like a creche must be confirmed with the employer.

## How it works

```
Browser (static/index.html)
  ├─ Google sign-in, profile saved in Firestore  ─────────────▶ Firebase
  └─ POST /api/translate | /api/plan | /api/jobs | /api/chat
       with the user's Firebase ID token
            ▼
Flask server (app.py) on Render
  ├─ checks the token (only signed-in users can spend the API keys)
  ├─ limits AI calls per user per hour
  ├─ Groq API    → skills, plan, job ranking, chat agent (free, no data kept)
  └─ Jooble API  → live job and internship listings for India
```

All API keys live in Render's environment variables. They are never in the page or in this repository.

## Cost: everything is free

| Service | Plan | How to make sure you're never billed |
|---------|------|--------------------------------------|
| Groq (AI) | Free tier, no card | Don't add a card or upgrade to a paid plan. When the free limit is reached, requests simply wait or fail with a "try again" message. |
| Jooble (jobs) | Free API key | Free; no card needed. Searches are cached for 15 minutes to save calls. (Adzuna is not used: its API now needs a paid plan after 25 test calls.) |
| Firebase (sign-in, database) | Spark plan, no card | Stay on **Spark**. Never upgrade to **Blaze**, which needs a card. On Spark, Firebase stops at its free limits instead of charging. |
| Render (hosting) | Free web service | Choose the **Free** instance type and don't add a card. The free site sleeps when idle, so the first visit can take up to a minute to load. |

If a service ever asks for a card to continue, don't add one; tell the team instead.

## Setup

You need four accounts. Create them yourself and paste the keys only into Render (or a local `.env` file), never into the code or a chat.

### 1. Groq API key (free AI)
1. Go to [console.groq.com](https://console.groq.com) and sign up (no card needed).
2. **API Keys → Create API Key.** Copy it into `GROQ_API_KEY`.
3. The default model is `openai/gpt-oss-120b` (Groq retired `llama-3.3-70b-versatile` in August 2026). If Groq retires this one too, pick a current model from Groq's model list that supports tool use and set `GROQ_MODEL`.

Why Groq and not Gemini's free tier: Google's free-tier terms let Google use what you send to improve its products, allow human review, and ask you not to send personal information. Mothers' stories are personal information, so ReBloom uses Groq, which says it doesn't keep or train on API requests by default.

### 2. Jooble (jobs and internships)
1. Go to [jooble.org/api/about](https://jooble.org/api/about) and fill in the API request form (project name ReBloom, a short description, your email).
2. Jooble emails you an API key. Copy it into `JOOBLE_API_KEY`.
3. If your key was issued for the India site and searches return nothing, set `JOOBLE_HOST` to `in.jooble.org`.

### 3. Firebase (Google sign-in and saving)
1. Go to [console.firebase.google.com](https://console.firebase.google.com) and create a project.
2. **Project settings → Your apps → Add app → Web.** Copy `apiKey`, `authDomain`, `projectId` and `appId` into `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID` and `FIREBASE_APP_ID`.
3. **Authentication → Sign-in method → Google → Enable.** For guest mode, also enable **Anonymous**.
4. **Firestore Database → Create database** (production mode). Open **Rules**, paste the contents of `firestore.rules` and publish.
5. After deploying, add your Render address (for example `rebloom.onrender.com`) under **Authentication → Settings → Authorized domains**.

### 4. Deploy on Render
1. Push this repository to GitHub.
2. In [Render](https://render.com): **New → Blueprint**, pick the repository (it reads `render.yaml`), or **New → Web Service** with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 120`
3. Add the environment variables from steps 1–3, plus `GRIEVANCE_EMAIL` (the team email shown on the privacy page for questions and complaints).
4. Open the site, sign in with Google and try each page.

### Run it on your computer
```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
export $(grep -v '^#' .env | xargs)
python app.py               # http://localhost:5000
```
Add `localhost` to Firebase's authorized domains to sign in locally.

## Project files

| File | Purpose |
|------|---------|
| `app.py` | Flask server and the four API endpoints |
| `static/index.html` | The whole website (HTML, CSS, JavaScript) |
| `firestore.rules` | Database rules: each user reads and writes only her own profile |
| `render.yaml` | Render deployment settings |
| `requirements.txt` | Python packages |
| `.env.example` | List of the environment variables |

## Tech

Python (Flask, gunicorn), Groq API (Llama models), Jooble API, Firebase Authentication and Firestore, GSAP for animation, Google Fonts (Manrope, Noto Sans, Noto Sans Kannada).

## Privacy and the DPDP Act, 2023

What ReBloom does:
- **Notice:** a plain-language Privacy Notice listing every piece of personal data, why it's used and who processes it, separate from the Terms of Use, with a Kannada summary.
- **Consent:** an unticked box at sign-in ("I am 18 or older…"). Google sign-in stays locked until it's ticked. The consent and its date are saved with her profile, and she's asked again if the notice changes.
- **Withdraw consent as easily as giving it:** More → Delete my account and data (one confirmation step).
- **Rights:** download all her data (More → Download my data), edit her inputs any time, delete everything, and a grievance contact that replies within 30 days (the law allows up to 90).
- **Children:** 18+ only, as the Act requires verifiable parental consent for anyone younger.
- **Data minimisation:** her name and email are never sent to the AI; only search words and city go to Jooble; chat messages aren't stored.
- **Security:** HTTPS, sign-in required for every API call, per-user limits, keys only on the server, and database rules so each profile is private.

What the team must do:
- Set `GRIEVANCE_EMAIL` to a team email that someone checks, and answer requests within 30 days.
- If personal data is ever exposed, tell affected users and the Data Protection Board of India promptly.
- Most DPDP obligations apply from May 2027, but ReBloom follows them already.
- This was written in good faith and is not legal advice; ask a teacher or a lawyer to review the Privacy Notice and Terms before a public launch.

## Safety and privacy

- Only signed-in users who have given consent can use the AI and job endpoints, with a per-user hourly limit. Guests get a smaller limit (`GUEST_CALLS_PER_HOUR`, default 15) that is also capped per network address (`GUEST_CALLS_PER_IP_HOUR`, default 30), so nobody can drain the free AI quota by opening new guest sessions.
- Ending a guest session deletes the guest profile and account.
- Each profile is readable only by its owner (Firestore rules).
- The assistant gives general information only on legal questions, never diagnoses, and points to India's free Tele-MANAS helpline (14416) if someone sounds in distress.
- Jobs by [Jooble](https://in.jooble.org).
