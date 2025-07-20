from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid
import os
from agent import generer_questions  # ⬅️ Ton IA

app = Flask(__name__)
CORS(app, origins=["https://ismns-frontend.vercel.app"])

qcm_storage = {}

@app.route('/generate_qcm', methods=['POST'])
def generate_qcm():
    data = request.get_json()
    theme = data.get('theme')
    niveau = data.get('niveau')

    if not theme or not niveau:
        return jsonify({"error": "Le thème et le niveau sont requis."}), 400

    try:
        questions = generer_questions(theme, niveau)
    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

    qcm_id = str(uuid.uuid4())
    qcm_storage[qcm_id] = questions

    return jsonify({"qcm_id": qcm_id})


@app.route('/get_qcm/<qcm_id>', methods=['GET'])
def get_qcm(qcm_id):
    questions = qcm_storage.get(qcm_id)
    if questions:
        return jsonify({"questions": questions})
    else:
        return jsonify({"error": "QCM introuvable"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # ⚠️ utilise le port dynamique de Render
    app.run(host="0.0.0.0", port=port)
