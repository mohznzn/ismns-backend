# app.py
import os, uuid, secrets, json
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

# === DB imports (depuis ton db.py) ===
from db import (
    SessionLocal, create_all_tables,
    Qcm, Question, Option, Invite, Attempt, Answer, invite_is_valid
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

# Création auto des tables en dev (désactive en prod si Alembic)
if os.getenv("DB_AUTO_CREATE", "false").lower() == "true":
    create_all_tables()

def _ensure_scheme(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://{url}"

def make_share_token(qcm_id: str) -> str:
    return secrets.token_urlsafe(24) + "_" + qcm_id


# =============== IA via LangChain (OpenAI) ===============
# requirements (compatibles Python 3.13):
# langchain==0.2.12, langchain-openai==0.1.23, openai==1.51.0
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
            "is_correct": (k == correct_idx)
        })
    return {
        "id": new_qid,
        "skill_tag": q.get("skill", skill),
        "text": str(q.get("question", "")).strip(),
        "options": options,
        "explanation": str(q.get("explanation", "")).strip(),
    }


# =============== Routes: santé & diag ===============
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

@app.get("/diag")
def diag():
    has_pkg = bool(_LC_READY)
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
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


# =============== Routes: QCM (admin) ===============
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

        for q in gen["questions"]:
            qq = Question(
                id=q["id"],
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

        try:
            gen_q = regenerate_one_question_langchain(
                job_description=qcm.job_description,
                language=qcm.language,
                skill=target.skill,
            )
        except Exception as e:
            return jsonify({"error": "LANGCHAIN_REGENERATE_FAILED", "message": str(e)}), 502

        target.skill = gen_q["skill_tag"]
        target.text = gen_q["text"]
        target.explanation = gen_q.get("explanation") or ""

        session.query(Option).filter(Option.question_id == target.id).delete(synchronize_session=False)
        for opt in gen_q["options"]:
            session.add(Option(
                id=opt["id"],
                question_id=target.id,
                text=opt["text"],
                is_correct=bool(opt["is_correct"])
            ))

        session.commit()

        updated_opts = session.query(Option).filter(Option.question_id == target.id).all()
        out_q = {
            "id": target.id,
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
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),  # <-- aware
            max_uses=0,
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


# =============== Routes: Public (candidat) ===============
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
                "options": [{"id": o.id, "text": o.text} for o in q.options]  # pas de is_correct/explanation
            })

        return jsonify({
            "qcm": {"id": qcm.id, "language": qcm.language},
            "questions": public_questions
        }), 200
    finally:
        session.close()


# =============== Routes: Attempts/Answers (candidat) ===============
@app.post("/attempts/start")
def start_attempt():
    """
    Body: { "token": "...", "candidate_email": "optional@email" }
    Crée une Attempt et renvoie l’épreuve (questions sans solutions).
    """
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    email = (data.get("candidate_email") or "").strip() or None
    if not token:
        return jsonify({"error": "token required"}), 400

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

        at = Attempt(
            id=str(uuid.uuid4()),
            qcm_id=qcm.id,
            invite_id=inv.id,
            candidate_email=email,
            started_at=datetime.now(timezone.utc),  # <-- aware
            seed=None
        )
        session.add(at)

        if inv.max_uses and inv.max_uses > 0:
            inv.used_count = (inv.used_count or 0) + 1

        session.commit()

        questions = []
        for qu in qcm.questions:
            questions.append({
                "id": qu.id,
                "skill_tag": qu.skill,
                "text": qu.text,
                "options": [{"id": o.id, "text": o.text} for o in qu.options]
            })

        return jsonify({
            "attempt_id": at.id,
            "qcm": {"id": qcm.id, "language": qcm.language},
            "questions": questions
        }), 201
    except Exception as e:
        session.rollback()
        return jsonify({"error": "ATTEMPT_START_FAILED", "message": str(e)}), 500
    finally:
        session.close()

@app.post("/attempts/<attempt_id>/answer")
def save_answer(attempt_id):
    """
    Body: { "question_id": "...", "option_id": "..." }
    Upsert de la réponse. Pas de correction renvoyée.
    """
    data = request.get_json(silent=True) or {}
    qid = (data.get("question_id") or "").strip()
    oid = (data.get("option_id") or "").strip()
    if not qid or not oid:
        return jsonify({"error": "question_id and option_id are required"}), 400

    session = SessionLocal()
    try:
        at = session.get(Attempt, attempt_id)
        if not at:
            return jsonify({"error": "attempt not found"}), 404
        if at.finished_at:
            return jsonify({"error": "attempt already finished"}), 400

        q = session.get(Question, qid, options=[selectinload(Question.options)])
        if not q or q.qcm_id != at.qcm_id:
            return jsonify({"error": "question invalid for this attempt"}), 400

        opt = session.get(Option, oid)
        if not opt or opt.question_id != q.id:
            return jsonify({"error": "option invalid for this question"}), 400

        is_corr = bool(opt.is_correct)

        # upsert
        ans = session.query(Answer).filter(
            Answer.attempt_id == at.id, Answer.question_id == q.id
        ).first()
        if ans:
            ans.option_id = opt.id
            ans.correct = is_corr
        else:
            ans = Answer(
                id=str(uuid.uuid4()),
                attempt_id=at.id,
                question_id=q.id,
                option_id=opt.id,
                correct=is_corr
            )
            session.add(ans)

        session.commit()
        return jsonify({"saved": True}), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": "ANSWER_SAVE_FAILED", "message": str(e)}), 500
    finally:
        session.close()

@app.post("/attempts/<attempt_id>/finish")
def finish_attempt(attempt_id):
    """
    Calcule le score et clôture la tentative.
    Response: { score, correct_count, answered_count, total_questions, duration_s }
    """
    session = SessionLocal()
    try:
        at = session.get(Attempt, attempt_id)
        if not at:
            return jsonify({"error": "attempt not found"}), 404
        if at.finished_at:
            return jsonify({"error": "attempt already finished"}), 400

        qcm = session.get(Qcm, at.qcm_id, options=[selectinload(Qcm.questions)])
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404

        answers = session.query(Answer).filter(Answer.attempt_id == at.id).all()
        correct_count = sum(1 for a in answers if a.correct)
        answered_count = len(answers)
        total_questions = len(qcm.questions) if qcm.questions else 0

        score = 0
        if total_questions > 0:
            score = round(100 * correct_count / total_questions)

        # Datetime *aware* pour éviter "can't subtract offset-naive and offset-aware datetimes"
        now = datetime.now(timezone.utc)

        start = at.started_at
        if start is not None and getattr(start, "tzinfo", None) is None:
            # au cas où la colonne serait naïve : on force UTC
            start = start.replace(tzinfo=timezone.utc)

        at.finished_at = now
        if start:
            at.duration_s = int((now - start).total_seconds())
        at.score = score

        session.commit()

        return jsonify({
            "score": score,
            "correct_count": correct_count,
            "answered_count": answered_count,
            "total_questions": total_questions,
            "duration_s": at.duration_s or 0
        }), 200
    except Exception as e:
        session.rollback()
        return jsonify({"error": "ATTEMPT_FINISH_FAILED", "message": str(e)}), 500
    finally:
        session.close()


# =============== Routes: Admin résultats ===============
@app.get("/admin/qcm/<qcm_id>/results")
def qcm_results(qcm_id):
    """
    Liste des tentatives pour un QCM (tableau admin).
    """
    session = SessionLocal()
    try:
        qcm = session.get(Qcm, qcm_id)
        if not qcm:
            return jsonify({"error": "qcm not found"}), 404

        attempts = session.query(Attempt).filter(Attempt.qcm_id == qcm.id).all()
        out = []
        for a in attempts:
            ans_count = session.query(Answer).filter(Answer.attempt_id == a.id).count()
            out.append({
                "attempt_id": a.id,
                "candidate_email": a.candidate_email,
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                "duration_s": a.duration_s,
                "score": a.score,
                "answered_count": ans_count
            })
        return jsonify({"qcm_id": qcm.id, "results": out})
    finally:
        session.close()

@app.get("/admin/attempts/<attempt_id>")
def attempt_detail(attempt_id):
    """
    Détail d'une tentative (admin) avec correction et explications.
    """
    session = SessionLocal()
    try:
        at = session.get(Attempt, attempt_id)
        if not at:
            return jsonify({"error": "attempt not found"}), 404

        qcm = session.get(
            Qcm, at.qcm_id,
            options=[selectinload(Qcm.questions).selectinload(Question.options)]
        )

        answers = session.query(Answer).filter(Answer.attempt_id == at.id).all()
        by_qid = {a.question_id: a for a in answers}

        details = []
        for qu in qcm.questions:
            ans = by_qid.get(qu.id)
            selected_opt = None
            if ans:
                selected_opt = next((o for o in qu.options if o.id == ans.option_id), None)
            correct_opt = next((o for o in qu.options if o.is_correct), None)
            details.append({
                "question_id": qu.id,
                "text": qu.text,
                "selected_option_id": ans.option_id if ans else None,
                "selected_option_text": selected_opt.text if selected_opt else None,
                "correct_option_id": correct_opt.id if correct_opt else None,
                "correct_option_text": correct_opt.text if correct_opt else None,
                "correct": bool(ans.correct) if ans else False,
                "explanation": qu.explanation or ""
            })

        return jsonify({
            "attempt": {
                "id": at.id,
                "qcm_id": at.qcm_id,
                "candidate_email": at.candidate_email,
                "started_at": at.started_at.isoformat() if at.started_at else None,
                "finished_at": at.finished_at.isoformat() if at.finished_at else None,
                "duration_s": at.duration_s,
                "score": at.score
            },
            "questions": details
        })
    finally:
        session.close()


# =============== Main ===============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
