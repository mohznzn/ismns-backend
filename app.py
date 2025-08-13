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

# =============== IA (OpenAI) ===============
# pip: openai==1.40.0  (ajoute-le à requirements.txt)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # si la lib n'est pas installée, on tombera sur le fallback

def _openai_client():
    if OpenAI is None:
        return None
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key)

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
- No code fences in the JSON. Return compact JSON only.

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

def generate_qcm_from_jd_openai(job_description: str, language: str, num_questions: int = 12):
    """
    Génère un QCM réaliste via OpenAI. Lève une exception si OPENAI_API_KEY n'est pas set
    ou si l'appel échoue -> le code appelant fera un fallback vers le mock.
    """
    client = _openai_client()
    if not client:
        raise RuntimeError("OPENAI_API_KEY not set or openai lib missing")

    prompt = PROMPT_TEMPLATE.format(num_questions=num_questions, language=language)
    messages = [
        {"role": "system", "content": "You write recruiting MCQs. Output strict JSON only."},
        {"role": "user", "content": f"JOB DESCRIPTION:\n{job_description}\n\n{prompt}"}
    ]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    raw = resp.choices[0].message.content
    data = json.loads(raw)

    skills = data.get("skills") or []
    out_questions = []
    for i, q in enumerate(data.get("questions", [])):
        qid = str(uuid.uuid4())
        options = []
        correct_idx = int(q.get("correct_index", 0))
        for k, opt_text in enumerate(q.get("options", [])[:4]):
            options.append({
                "id": str(uuid.uuid4()),
                "text": opt_text,
                "is_correct": (k == correct_idx)
            })
        out_questions.append({
            "id": qid,
            "skill_tag": q.get("skill", "General"),
            "text": q.get("question", "").strip(),
            "options": options,
            "explanation": q.get("explanation", "").strip(),
        })
    return {"skills": skills, "questions": out_questions}

# =============== Mock (fallback) ===============
def mock_generate_qcm_from_jd(job_description: str, language: str, num_questions: int = 12):
    skills = ["Fundamentals", "Problem Solving", "Tools/Tech", "Domain", "Communication"]
    num_questions = max(1, min(int(num_questions or 12), 50))
    base = []
    for i in range(num_questions):
        skill = skills[i % len(skills)]
        qid = str(uuid.uuid4())
        correct_idx = i % 4
        options = []
        for k, label in enumerate(["Option A", "Option B", "Option C", "Option D"]):
            options.append({
                "id": str(uuid.uuid4()),
                "text": f"{label} for {skill}",
                "is_correct": (k == correct_idx),
            })
        base.append({
            "id": qid,
            "skill_tag": skill,
            "text": f"[{language}] MCQ {i+1} about {skill} derived from the JD.",
            "options": options,
            "explanation": f"Why {['A','B','C','D'][correct_idx]} is correct (admin-only)."
        })
    return {"skills": skills, "questions": base}

# =============== Routes ===============
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

@app.post("/qcm/create_draft_from_jd")
def create_draft_from_jd():
    data = request.get_json(silent=True) or {}
    jd = (data.get("job_description") or "").strip()
    language = (data.get("language") or "en").strip()
    # on accepte "num_questions" ET "nb_questions" (selon ce que le front envoie)
    num_questions = data.get("num_questions", data.get("nb_questions", 12))
    try:
        num_questions = int(num_questions)
    except Exception:
        num_questions = 12

    if not jd:
        return jsonify({"error": "job_description is required"}), 400

    # IA → si échec → mock
    try:
        gen = generate_qcm_from_jd_openai(jd, language, num_questions=num_questions)
    except Exception:
        gen = mock_generate_qcm_from_jd(jd, language, num_questions=num_questions)

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
        "questions": gen["questions"]  # admin-only: includes is_correct + explanation
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

    # Construit un prompt pour régénérer UNE question sur le même skill
    client = _openai_client()
    if client:
        prompt = f"""Regenerate exactly ONE MCQ for skill "{target['skill_tag']}".
Follow the same JSON schema with fields: question, options (4), correct_index, explanation.
Language: {qcm['language']}.
Job description:
{qcm['job_description']}
"""
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You write recruiting MCQs. Output strict JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.6,
            )
            data = json.loads(resp.choices[0].message.content)
            # support single object or {"questions":[...]}
            q_raw = data.get("questions", [data])[0]
            new_qid = str(uuid.uuid4())
            options = []
            correct_idx = int(q_raw.get("correct_index", 0))
            for k, txt in enumerate(q_raw.get("options", [])[:4]):
                options.append({
                    "id": str(uuid.uuid4()),
                    "text": txt,
                    "is_correct": (k == correct_idx)
                })
            new_q = {
                "id": new_qid,
                "skill_tag": q_raw.get("skill", target["skill_tag"]),
                "text": q_raw.get("question", "").strip(),
                "options": options,
                "explanation": q_raw.get("explanation", "").strip(),
            }
        except Exception:
            new_q = None
    else:
        new_q = None

    # Fallback si IA KO
    if not new_q:
        correct = [
            {"id": str(uuid.uuid4()), "text": "Option A", "is_correct": True},
            {"id": str(uuid.uuid4()), "text": "Option B", "is_correct": False},
            {"id": str(uuid.uuid4()), "text": "Option C", "is_correct": False},
            {"id": str(uuid.uuid4()), "text": "Option D", "is_correct": False},
        ]
        new_q = {
            "id": str(uuid.uuid4()),
            "skill_tag": target["skill_tag"],
            "text": f"Alternative MCQ about {target['skill_tag']}",
            "options": correct,
            "explanation": "Admin-only explanation.",
        }

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
