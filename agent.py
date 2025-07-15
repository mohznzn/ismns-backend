from crewai import Agent, Task, Crew
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import os
import re

# 🔐 Charger la clé API OpenAI
load_dotenv()

# 🧠 Initialiser le modèle
llm = ChatOpenAI(
    temperature=0,
    model="gpt-3.5-turbo"
)

# === MODE TERMINAL (optionnel si tu veux tester en local) ===
if __name__ == "__main__":
    theme = input("🎯 Thème du QCM (ex : Python, Scrum, SQL) : ")
    niveau = input("📈 Difficulté (débutant / intermédiaire / avancé) : ").lower()

    questions = []

    # Appel à la fonction de génération
    questions = []

    agent_generateur = Agent(
        role="Générateur de QCM",
        goal="Créer un QCM de 5 questions avec 4 choix et la bonne réponse",
        backstory="Expert pédagogique spécialisé dans la création d'examens",
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    task_qcm = Task(
        description=(
            f"Génère un QCM de 5 questions sur le thème : {theme}, pour un niveau {niveau}. "
            "Chaque question doit avoir 4 choix (A, B, C, D) et une seule bonne réponse. "
            "Format :\nQuestion 1: ...\nA) ...\nB) ...\nC) ...\nD) ...\nRéponse : X"
        ),
        expected_output="5 questions formatées avec réponses. Une seule bonne réponse par question.",
        agent=agent_generateur
    )

    crew = Crew(
        agents=[agent_generateur],
        tasks=[task_qcm],
        verbose=True
    )

    qcm_text = str(crew.kickoff())

    # ✅ Extraction correcte des questions
    pattern = r"(Question\s*\d+\s*:[^\n]+\n(?:[A-D]\)[^\n]*\n){4}Réponse\s*:\s*[A-D])"
    blocs = re.findall(pattern, qcm_text, re.DOTALL)

    score = 0
    user_answers = []

    print("\n📋 Début du test :")
    for bloc in blocs:
        match = re.search(r"Réponse\s*:\s*([A-D])", bloc)
        bonne_reponse = match.group(1).strip() if match else None
        question_sans_reponse = re.sub(r"Réponse\s*:\s*[A-D]", "", bloc).strip()

        print(f"\n{question_sans_reponse}")
        reponse_user = input("👉 Votre réponse (A, B, C ou D) : ").strip().upper()
        user_answers.append((question_sans_reponse, bonne_reponse, reponse_user))

        if reponse_user == bonne_reponse:
            print("✅ Bonne réponse !")
            score += 1
        else:
            print(f"❌ Mauvaise réponse. La bonne réponse était : {bonne_reponse}")

    print("\n📊 Résultat final :")
    print(f"Score : {score} / {len(blocs)}")

    if score == len(blocs):
        print("🎉 Excellent travail, vous avez tout juste !")
    elif score >= len(blocs) // 2:
        print("👍 Bon début, continuez comme ça pour progresser.")
    else:
        print("📘 Vous pouvez vous améliorer. N’hésitez pas à revoir le cours et refaire le test.")

# === 🎯 FONCTION POUR USAGE DANS FLASK / BACKEND ===

def generer_questions(theme, niveau):
    agent_generateur = Agent(
        role="Générateur de QCM",
        goal="Créer un QCM de 5 questions avec 4 choix et la bonne réponse",
        backstory="Expert pédagogique spécialisé dans la création d'examens",
        verbose=False,
        allow_delegation=False,
        llm=llm
    )

    task_qcm = Task(
        description=(
            f"Génère un QCM de 5 questions sur le thème : {theme}, pour un niveau {niveau}. "
            "Chaque question doit avoir 4 choix (A, B, C, D) et une seule bonne réponse. "
            "Format :\nQuestion 1: ...\nA) ...\nB) ...\nC) ...\nD) ...\nRéponse : X"
        ),
        expected_output="5 questions formatées avec réponses. Une seule bonne réponse par question.",
        agent=agent_generateur
    )

    crew_temp = Crew(
        agents=[agent_generateur],
        tasks=[task_qcm],
        verbose=False
    )

    qcm_text = str(crew_temp.kickoff())
    pattern = r"(Question\s*\d+\s*:[^\n]+\n(?:[A-D]\)[^\n]*\n){4}Réponse\s*:\s*[A-D])"
    blocs = re.findall(pattern, qcm_text, re.DOTALL)

    questions_list = []
    for bloc in blocs:
        match = re.search(r"Réponse\s*:\s*([A-D])", bloc)
        bonne_reponse = match.group(1).strip() if match else None
        lignes = bloc.strip().split("\n")
        question = lignes[0].replace("Question", "").strip(": ").strip()
        options = [l[3:].strip() for l in lignes[1:5]]

        questions_list.append({
            "question": question,
            "options": options,
            "answer": bonne_reponse
        })

    return questions_list
