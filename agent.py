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

# === NOUVELLES FONCTIONS POUR LE BACKEND ===

def lister_chapitres(theme):
    """
    Utilise l'IA pour lister les chapitres importants d'un thème donné.
    """
    agent_chapitres = Agent(
        role="Expert en planification d'études",
        goal="Lister les chapitres clés pour bien maîtriser un thème",
        backstory="Spécialiste en pédagogie et en certifications",
        verbose=False,
        allow_delegation=False,
        llm=llm
    )

    task_chapitres = Task(
        description=(
            f"Liste 5 à 7 chapitres importants pour bien préparer le thème '{theme}'. "
            "Donne uniquement la liste des chapitres sous forme de phrases courtes."
        ),
        expected_output="Une liste simple des chapitres, un par ligne.",
        agent=agent_chapitres
    )

    crew_temp = Crew(
        agents=[agent_chapitres],
        tasks=[task_chapitres],
        verbose=False
    )

    chapitres_text = str(crew_temp.kickoff())
    chapitres = [c.strip("-• ").strip() for c in chapitres_text.split("\n") if c.strip()]

    return chapitres


def generer_questions(chapitre, nb_questions=30):
    """
    Génère un QCM pour un chapitre donné avec un nombre de questions paramétrable.
    """
    agent_generateur = Agent(
        role="Générateur de QCM",
        goal=f"Créer un QCM de {nb_questions} questions avec 4 choix et la bonne réponse",
        backstory="Expert pédagogique spécialisé dans la création d'examens",
        verbose=False,
        allow_delegation=False,
        llm=llm
    )

    task_qcm = Task(
        description=(
            f"Génère un QCM de {nb_questions} questions sur le chapitre : {chapitre}. "
            "Chaque question doit avoir 4 choix (A, B, C, D) et une seule bonne réponse. "
            "Format :\nQuestion 1: ...\nA) ...\nB) ...\nC) ...\nD) ...\nRéponse : X"
        ),
        expected_output=f"{nb_questions} questions formatées avec réponses. Une seule bonne réponse par question.",
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


# === MODE TERMINAL (optionnel) ===
if __name__ == "__main__":
    theme = input("🎯 Thème du QCM (ex : Python, Scrum, SQL) : ")
    chapitres = lister_chapitres(theme)
    print("\n📚 Chapitres proposés :")
    for idx, c in enumerate(chapitres, 1):
        print(f"{idx}. {c}")

    choix = int(input("\n👉 Choisissez un chapitre (numéro) : "))
    chapitre = chapitres[choix - 1]

    questions = generer_questions(chapitre, nb_questions=5)
    print("\n📋 Exemple de questions :")
    for q in questions:
        print(f"- {q['question']} ({q['answer']})")
