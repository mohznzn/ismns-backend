from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from agent import generer_chapitres, generer_questions_chapitre  # Nouvelles fonctions IA

app = Flask(__name__)
CORS(app, origins=["https://ismns-frontend.vercel.app"])

# --- Endpoint pour obtenir les chapitres ---
@app.route('/get_chapters', methods=['POST'])
def get_chapters():
    data = request.get_json()
    theme = data.get('theme')

    if not theme:
        return jsonify({"error": "Le thème est requis."}), 400

    try:
        chapitres = generer_chapitres(theme)
    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

    return jsonify({"chapitres": chapitres})


# --- Endpoint pour générer 30 questions sur un chapitre ---
@app.route('/generate_qcm_chapter', methods=['POST'])
def generate_qcm_chapter():
    data = request.get_json()
    chapitre = data.get('chapitre')

    if not chapitre:
        return jsonify({"error": "Le chapitre est requis."}), 400

    try:
        questions = generer_questions_chapitre(chapitre)
    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

    return jsonify({"questions": questions})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
