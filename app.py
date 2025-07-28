from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from agent import generer_questions, lister_chapitres  # ton IA

app = Flask(__name__)
CORS(app, origins=["https://ismns-frontend.vercel.app"])

# --- Route 1 : Récupérer la liste des chapitres pour un thème ---
@app.route('/get_chapitres', methods=['POST'])
def get_chapitres():
    """
    Récupère les chapitres à maîtriser pour un thème donné.
    """
    data = request.get_json()
    theme = data.get('theme')

    if not theme:
        return jsonify({"error": "Le thème est requis."}), 400

    try:
        chapitres = lister_chapitres(theme)
    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

    return jsonify({"chapitres": chapitres})


# --- Route 2 : Générer les questions pour un chapitre ---
@app.route('/generate_questions', methods=['POST'])
def generate_questions():
    """
    Génère 30 questions sur un chapitre choisi.
    """
    data = request.get_json()
    chapitre = data.get('chapitre')

    if not chapitre:
        return jsonify({"error": "Le chapitre est requis."}), 400

    try:
        questions = generer_questions(chapitre, nb_questions=30)
    except Exception as e:
        return jsonify({"error": f"Erreur IA : {str(e)}"}), 500

    return jsonify({"questions": questions})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)