# app.py
import os, uuid, secrets, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

# === DB imports (depuis ton db.py) ===
from db import (
    SessionLocal, create_all_tables,
    Qcm, Question, Option, Invite, invite_is_valid
)
from sqlalchemy.orm import selectinload

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

# Création auto des tables en dev (désactive en prod si tu utilises Alembic)
if os.getenv("DB_AUTO_CREATE", "false").lower() == "true":
    create_all_tables()

def _ensure_scheme(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://{url}"

def make_share_token(qcm_id: str) -> str:
    return secrets.token_urlsafe(24) + "_" + qcm_id


# =============== IA via LangChain (OpenAI) ===============
# requirements (compatibles Python 3.13) :
# langchain==0.2.12
# langchain-openai==0.1.23
# openai==1.51.0
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
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None
    # ChatOpenAI lit OPENAI_API_KEY depuis l'env automatiquement
    return ChatOpenAI(model="gpt-4o-mini", temperature=temp)


# ⚠️ Doubler les accolades pour échapper le JSON dans le prompt formatable
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
        "num_questions": int(num_questions),
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
                "is_correct": (k == correct_idx),
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
        "job_description": job_description,
    })

    # Support {"questions":[{...}]} ou objet direct
    if isinstance(q, dict) and "questions" in q and isinstance(q["questions"], list) and q["questions"]:
        q = q["questions"][0]

    new_qid = str(uuid.uuid4())
    correct_idx = int(q.get("correct_index", 0))
    options = []
    for k, txt in enumerate((q.get("options") or [])[:4]):
        options.append({
            "id": str(uuid.uuid4()),
            "text": str(txt),
            "is_correct": (k == correct_idx),
        })

    return {
        "id": new_qid,  # on génère un nouvel id si on veut remplacer
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
    # petit check DB
    db_ok = True
    try:
        s = SessionLocal(); s.execute("SELECT 1;"); s.close()
    except Exception:
        db_ok = False
    return jsonify({
        "langchain_ready": has_pkg,
        "openai_key_present": has_key,
        "db_ok": db_ok
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

    # --- Persistance DB ---
    session = SessionLocal()
    try:
        qcm = Qcm(
            id=str(uuid.uuid4()),
            language=language,
            job_description=jd,
            status="draft",
            skills_json=json.dumps(gen["skills"]),
            share_token=None,
        )
        session.add(qcm)
        session.flush()

        # Insérer questions + options
        for q in gen["questions"]:
            qq = Question(
                id=q["id"],  # on réutilise l'id généré en mémoire pour cohérence avec le front
                qcm_id=qcm.id,
                skill=q["skill_tag"],
                text=q["text"],
                explanation=q.get("explanation") or "",
                locked=False,
            )
            session.add(qq); session.flush()
            for opt in q["options"]:
                session.add(Option(
                    id=opt["id"],
                    question_id=qq.id,
                    text=opt["text"],
                    is_correct=bool(opt["is_correct"])
                ))

        session.commit()

        # Réponse (reprend la structure admin)
        return jsonify({
            "qcm_id": qcm.id,
            "skills": gen["skills"],
            "questions": gen["questions"],
            "engine": "langchain"
        }), 201
    except Exception as e:
        session.rollback()
        return jsonify({"error": "DB_WRITE_FAILED", "message": str(e)}), 500
    finally:
        session.close()


@app.get("/qcm/<qcm_id>/admin")
def get_qcm_admin(qcm_id):
    session = SessionLocal()
    try:
        qcm = session.get(
            Qcm, qcm_id,
            options=[selectinload(Qcm.questions).selectinload(Question.options)]
        )
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404

        questions = []
        for qu in qcm.questions:
            questions.append({
                "id": qu.id,
                "skill_tag": qu.skill,
                "text": qu.text,
                "options": [{"id": o.id, "text": o.text, "is_correct": o.is_correct} for o in qu.options],
                "explanation": qu.explanation or ""
            })

        return jsonify({
            "qcm": {
                "id": qcm.id,
                "language": qcm.language,
                "status": qcm.status,
                "skills": json.loads(qcm.skills_json or "[]"),
                "share_token": qcm.share_token
            },
            "questions": questions
        })
    finally:
        session.close()


@app.post("/qcm/<qcm_id>/question/<qid>/regenerate")
def regenerate_question(qcm_id, qid):
    session = SessionLocal()
    try:
        qcm = session.get(Qcm, qcm_id)
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404

        target = session.get(
            Question, qid,
            options=[selectinload(Question.options)]
        )
        if not target or target.qcm_id != qcm.id:
            return jsonify({"error": "question not found"}), 404

        # Génère une nouvelle question (on met à jour EN PLACE: même id)
        try:
            gen_q = regenerate_one_question_langchain(
                job_description=qcm.job_description,
                language=qcm.language,
                skill=target.skill,
            )
        except Exception as e:
            return jsonify({"error": "LANGCHAIN_REGENERATE_FAILED", "message": str(e)}), 502

        # Update question
        target.skill = gen_q["skill_tag"]
        target.text = gen_q["text"]
        target.explanation = gen_q.get("explanation") or ""

        # Remplace les options
        session.query(Option).filter(Option.question_id == target.id).delete(synchronize_session=False)
        for opt in gen_q["options"]:
            session.add(Option(
                id=opt["id"],
                question_id=target.id,
                text=opt["text"],
                is_correct=bool(opt["is_correct"])
            ))

        session.commit()

        # Relecture options pour réponse
        updated_opts = session.query(Option).filter(Option.question_id == target.id).all()
        out_q = {
            "id": target.id,  # on garde le même id pour le front
            "skill_tag": target.skill,
            "text": target.text,
            "options": [{"id": o.id, "text": o.text, "is_correct": o.is_correct} for o in updated_opts],
            "explanation": target.explanation or ""
        }
        return jsonify({"question": out_q}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": "DB_UPDATE_FAILED", "message": str(e)}), 500
    finally:
        session.close()


@app.post("/qcm/<qcm_id>/publish")
def publish_qcm(qcm_id):
    session = SessionLocal()
    try:
        qcm = session.get(Qcm, qcm_id)
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404
        if qcm.status != "draft":
            return jsonify({"error": "qcm not in draft"}), 400

        token = make_share_token(qcm.id)
        qcm.status = "published"
        qcm.share_token = token

        invite = Invite(
            qcm_id=qcm.id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(days=30),  # 30 jours par défaut
            max_uses=0,  # 0 = illimité
            used_count=0
        )
        session.add(invite)
        session.commit()

        frontend = _ensure_scheme(os.getenv('FRONTEND_URL','http://localhost:3000'))
        share_url = f"{frontend}/invite?token={token}"
        return jsonify({"share_url": share_url, "token": token}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": "DB_UPDATE_FAILED", "message": str(e)}), 500
    finally:
        session.close()


@app.get("/public/qcm/<token>")
def get_public_qcm(token):
    session = SessionLocal()
    try:
        inv = session.query(Invite).filter(Invite.token == token).first()
        if not inv or not invite_is_valid(inv):
            return jsonify({"error": "invalid token"}), 404

        qcm = session.get(
            Qcm, inv.qcm_id,
            options=[selectinload(Qcm.questions).selectinload(Question.options)]
        )
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404

        public_questions = []
        for q in qcm.questions:
            public_questions.append({
                "id": q.id,
                "skill_tag": q.skill,
                "text": q.text,
                "options": [{"id": o.id, "text": o.text} for o in q.options]  # pas de is_correct/explanation publiquement
            })

        return jsonify({
            "qcm": {"id": qcm.id, "language": qcm.language},
            "questions": public_questions
        }), 200
    finally:
        session.close()


# =============== Main ===============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

