import os, uuid, secrets, json
from flask import Flask, request, jsonify
from flask_cors import CORS

# =============== Config Flask + CORS ===============
app = Flask(__name__)

allowed_env = os.getenv("ALLOWED_ORIGINS", "").strip()
if allowed_env:
    ORIGINS = [o.strip() for o in allowed_env.split(",") if o.strip()]
else:
    ORIGINS = [
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        r"https://.*\.vercel\.app$",
    ]

CORS(
    app,
    resources={r"/*": {"origins": ORIGINS}},
    methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=False,
)

SHARE_SECRET = os.getenv("SHARE_SECRET", "dev_share_secret")

# =============== Stockage mémoire (MVP) ===============
QCMS = {}        # qcm_id -> qcm dict
QUESTIONS = {}   # qcm_id -> list[question dict]
PUBLIC = {}      # share_token -> qcm_id

def make_share_token(qcm_id: str) -> str:
    return secrets.token_urlsafe(16) + "_" + qcm_id

def _ensure_scheme(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://{url}"

# =============== IA via LangChain (OpenAI) ===============
# requirements.txt: langchain==0.2.11, langchain-openai==0.1.22
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import JsonOutputParser
    _LC_READY = True
except Exception:
    _LC_READY = False

def _lc_llm(temp: float = 0.5):
    """Crée un LLM LangChain; renvoie None si package/clé absents."""
    if not _LC_READY:
        return None
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    # ChatOpenAI lit OPENAI_API_KEY depuis l'env automatiquement
    return ChatOpenAI(model="gpt-4o-mini", temperature=temp)

PROMPT_TEMPLATE = """You are an expert test author. Given a job description and a target language, do:
1) Extract 4–6 core skills (short labels).
2) Generate {num_questions} high-quality multiple-choice questions (MCQ) in the language: {language}.
3) Each MCQ must have:
   - "skill": one of the extracted skills
   - "question": concise, unambiguous
   - "options": exactly 4 distinct plausible options (strings)
   - "correct_index": integer 0..3
   - "explanation": 1–3 sentences why the correct option is right (admin only; never shown to candidate)

Constraints:
- Tailor content strictly to the job description.
- Avoid trivia; test practical knowledge and reasoning.
- Return STRICT JSON ONLY (no markdown, no code fences).

Return JSON object:
{{
  "skills": ["..."],
  "questions": [
    {{
      "skill": "...",
      "question": "...",
      "options": ["...","...","...","..."],
      "correct_index": 0,
      "explanation": "..."
    }}
  ]
}}"""


def generate_qcm_from_jd_langchain(job_description: str, language: str, num_questions: int = 12):
    llm = _lc_llm(0.5)
    if not llm:
        raise RuntimeError("LangChain/OpenAI not available or OPENAI_API_KEY missing")

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You write recruiting MCQs. Output strict JSON only."),
        ("user", "JOB DESCRIPTION:\n{job_description}\n\n" + PROMPT_TEMPLATE),
    ])
    parser = JsonOutputParser()
    chain = prompt | llm | parser

    data = chain.invoke({
        "job_description": job_description,
        "language": language,
        "num_questions": int(num_questions)
    })

    skills = data.get("skills") or []
    out_questions = []
    for q in data.get("questions", []):
        qid = str(uuid.uuid4())
        correct_idx = int(q.get("correct_index", 0))
        options = []
        for k, opt_text in enumerate((q.get("options") or [])[:4]):
            options.append({
                "id": str(uuid.uuid4()),
                "text": str(opt_text),
                "is_correct": (k == correct_idx)
            })
        out_questions.append({
            "id": qid,
            "skill_tag": q.get("skill", "General"),
            "text": str(q.get("question", "")).strip(),
            "options": options,
            "explanation": str(q.get("explanation", "")).strip(),
        })
    if not out_questions:
        raise RuntimeError("Model returned no questions")
    return {"skills": skills, "questions": out_questions}

def regenerate_one_question_langchain(job_description: str, language: str, skill: str):
    llm = _lc_llm(0.6)
    if not llm:
        raise RuntimeError("LangChain/OpenAI not available or OPENAI_API_KEY missing")

one_q_prompt = ChatPromptTemplate.from_messages([
    ("system", "You write recruiting MCQs. Output strict JSON only."),
    ("user",
     """Regenerate exactly ONE MCQ for the skill "{skill}" in language: {language}.
Use STRICT JSON (no markdown). Schema:
{{
  "skill": "...",
  "question": "...",
  "options": ["...","...","...","..."],
  "correct_index": 0,
  "explanation": "..."
}}
Job description:
{job_description}
"""),
])

    parser = JsonOutputParser()
    chain = one_q_prompt | llm | parser

    q = chain.invoke({
        "skill": skill,
        "language": language,
        "job_description": job_description
    })

    # Supporte un objet direct ou un format {"questions":[...]}
    if isinstance(q, dict) and "questions" in q and isinstance(q["questions"], list) and q["questions"]:
        q = q["questions"][0]

    new_qid = str(uuid.uuid4())
    correct_idx = int(q.get("correct_index", 0))
    options = []
    for k, txt in enumerate((q.get("options") or [])[:4]):
        options.append({
            "id": str(uuid.uuid4()),
            "text": str(txt),
            "is_correct": (k == correct_idx)
        })
    return {
        "id": new_qid,
        "skill_tag": q.get("skill", skill),
        "text": str(q.get("question", "")).strip(),
        "options": options,
        "explanation": str(q.get("explanation", "")).strip(),
    }

# =============== Routes ===============
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

@app.get("/diag")
def diag():
    has_pkg = bool(_LC_READY)
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return jsonify({
        "langchain_ready": has_pkg,
        "openai_key_present": has_key
    })

@app.post("/qcm/create_draft_from_jd")
def create_draft_from_jd():
    data = request.get_json(silent=True) or {}
    jd = (data.get("job_description") or "").strip()
    language = (data.get("language") or "en").strip()
    num_questions = data.get("num_questions", data.get("nb_questions", 12))
    try:
        num_questions = int(num_questions)
    except Exception:
        num_questions = 12

    if not jd:
        return jsonify({"error": "job_description is required"}), 400

    try:
        gen = generate_qcm_from_jd_langchain(jd, language, num_questions=num_questions)
    except Exception as e:
        return jsonify({
            "error": "LANGCHAIN_GENERATION_FAILED",
            "message": str(e)
        }), 502

    qcm_id = str(uuid.uuid4())
    QCMS[qcm_id] = {
        "id": qcm_id,
        "job_description": jd,
        "language": language,
        "status": "draft",
        "skills": gen["skills"],
        "share_token": None,
    }
    QUESTIONS[qcm_id] = gen["questions"]

    return jsonify({
        "qcm_id": qcm_id,
        "skills": gen["skills"],
        "questions": gen["questions"],
        "engine": "langchain"
    }), 201

@app.get("/qcm/<qcm_id>/admin")
def get_qcm_admin(qcm_id):
    qcm = QCMS.get(qcm_id)
    if not qcm:
        return jsonify({"error": "qcm not found"}), 404
    return jsonify({"qcm": qcm, "questions": QUESTIONS.get(qcm_id, [])})

@app.post("/qcm/<qcm_id>/question/<qid>/regenerate")
def regenerate_question(qcm_id, qid):
    qcm = QCMS.get(qcm_id)
    if not qcm:
        return jsonify({"error": "qcm not found"}), 404
    all_qs = QUESTIONS.get(qcm_id, [])
    target = next((q for q in all_qs if q["id"] == qid), None)
    if not target:
        return jsonify({"error": "question not found"}), 404

    try:
        new_q = regenerate_one_question_langchain(
            job_description=qcm["job_description"],
            language=qcm["language"],
            skill=target["skill_tag"],
        )
    except Exception as e:
        return jsonify({
            "error": "LANGCHAIN_REGENERATE_FAILED",
            "message": str(e)
        }), 502

    idx = all_qs.index(target)
    all_qs[idx] = new_q
    return jsonify({"question": new_q}), 200

@app.post("/qcm/<qcm_id>/publish")
def publish_qcm(qcm_id):
    qcm = QCMS.get(qcm_id)
    if not qcm:
        return jsonify({"error": "qcm not found"}), 404
    if qcm["status"] != "draft":
        return jsonify({"error": "qcm not in draft"}), 400

    token = make_share_token(qcm_id)
    qcm["status"] = "published"
    qcm["share_token"] = token
    PUBLIC[token] = qcm_id

    frontend = _ensure_scheme(os.getenv('FRONTEND_URL','http://localhost:3000'))
    share_url = f"{frontend}/invite?token={token}"
    return jsonify({"share_url": share_url, "token": token}), 200

@app.get("/public/qcm/<token>")
def get_public_qcm(token):
    qcm_id = PUBLIC.get(token)
    if not qcm_id:
        return jsonify({"error": "invalid token"}), 404

    qcm = QCMS[qcm_id]
    public_questions = [
        {
            "id": q["id"],
            "skill_tag": q["skill_tag"],
            "text": q["text"],
            "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]],
        }
        for q in QUESTIONS.get(qcm_id, [])
    ]

    return jsonify({
        "qcm": {"id": qcm["id"], "language": qcm["language"]},
        "questions": public_questions
    }), 200

# =============== Main ===============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
