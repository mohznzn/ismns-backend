import os, uuid, secrets
from flask import Flask, request, jsonify
from flask_cors import CORS

# ---------------- Config ----------------
app = Flask(__name__)

# Autoriser plusieurs origines (prod + local). Liste via env ALLOWED_ORIGINS, séparées par virgules.
# Exemple à mettre sur Render :
# ALLOWED_ORIGINS = https://ismns-frontend-5qiq.vercel.app, http://localhost:3000
allowed_env = os.getenv("ALLOWED_ORIGINS", "").strip()
if allowed_env:
    ORIGINS = [o.strip() for o in allowed_env.split(",") if o.strip()]
else:
    # Valeurs par défaut raisonnables
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

# ---------------- Stockage en mémoire (MVP) ----------------
QCMS = {}       # qcm_id -> qcm dict
QUESTIONS = {}  # qcm_id -> list[question dict]
PUBLIC = {}     # share_token -> qcm_id

def make_share_token(qcm_id: str) -> str:
    return secrets.token_urlsafe(16) + "_" + qcm_id

# ---------------- Mock IA : génère un QCM à partir du JD ----------------
def mock_generate_qcm_from_jd(job_description: str, language: str):
    skills = ["Fundamentals", "Problem Solving", "Tools/Tech"]
    base = []
    for i in range(4):
        skill = skills[i % len(skills)]
        qid = str(uuid.uuid4())
        base.append({
            "id": qid,
            "skill_tag": skill,
            "text": f"[{language}] MCQ {i+1} about {skill} derived from the JD.",
            "options": [
                {"id": str(uuid.uuid4()), "text": "Option A", "is_correct": (i % 4 == 0)},
                {"id": str(uuid.uuid4()), "text": "Option B", "is_correct": (i % 4 == 1)},
                {"id": str(uuid.uuid4()), "text": "Option C", "is_correct": (i % 4 == 2)},
                {"id": str(uuid.uuid4()), "text": "Option D", "is_correct": (i % 4 == 3)},
            ],
            "explanation": "Admin-only explanation for the correct answer."
        })
    return {"skills": skills, "questions": base}

# ---------------- Routes ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

@app.post("/qcm/create_draft_from_jd")
def create_draft_from_jd():
    data = request.get_json(silent=True) or {}
    jd = data.get("job_description", "").strip()
    language = data.get("language", "en").strip()
    if not jd:
        return jsonify({"error": "job_description is required"}), 400

    qcm_id = str(uuid.uuid4())
    gen = mock_generate_qcm_from_jd(jd, language)
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
        "questions": gen["questions"]  # is_correct + explanation (admin only)
    }), 201

@app.get("/qcm/<qcm_id>/admin")
def get_qcm_admin(qcm_id):
    qcm = QCMS.get(qcm_id)
    if not qcm:
        return jsonify({"error": "qcm not found"}), 404
    return jsonify({"qcm": qcm, "questions": QUESTIONS.get(qcm_id, [])})

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
    share_url = f"{os.getenv('FRONTEND_URL','http://localhost:3000')}/invite?token={token}"
    return jsonify({"share_url": share_url, "token": token}), 200

@app.get("/public/qcm/<token>")
def get_public_qcm(token):
    qcm_id = PUBLIC.get(token)
    if not qcm_id:
        return jsonify({"error": "invalid token"}), 404
    qcm = QCMS[qcm_id]
    # Public : PAS d'is_correct, PAS d'explications
    public_questions = []
    for q in QUESTIONS.get(qcm_id, []):
        public_questions.append({
            "id": q["id"],
            "skill_tag": q["skill_tag"],
            "text": q["text"],
            "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]],
        })
    return jsonify({
        "qcm": {"id": qcm["id"], "language": qcm["language"]},
        "questions": public_questions
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
