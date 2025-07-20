from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid
import os
import json
from agent import generer_questions  # ton IA

app = Flask(__name__)
CORS(app, origins=["https://ismns-frontend.vercel.app"])

QCM_DIR = "qcm_data"
os.makedirs(QCM_DIR, exist_ok=True)

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
    with open(f"{QCM_DIR}/qcm_{qcm_id}.json", "w") as f:
        json.dump(questions, f)

    return jsonify({"qcm_id": qcm_id})


@app.route('/get_qcm/<qcm_id>', methods=['GET'])
def get_qcm(qcm_id):
    path = f"{QCM_DIR}/qcm_{qcm_id}.json"
    if not os.path.exists(path):
        return jsonify({"error": "QCM introuvable"}), 404

    with open(path) as f:
        questions = json.load(f)

    return jsonify({"questions": questions})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
