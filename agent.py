from crewai import Agent, Task, Crew
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import os
import re

load_dotenv()

llm = ChatOpenAI(
    temperature=0,
    model="gpt-3.5-turbo"
)

# --- Générer les chapitres d'un thème ---
def generer_chapitres(theme):
    agent = Agent(
        role="Expert en formation",
        goal="Lister les chapitres essentiels pour préparer une certification sur un thème donné",
        backstory="Expert pédagogique spécialisé dans les certifications professionnelles",
        verbose=False,
        allow_delegation=False,
        llm=llm
    )

    task = Task(
        description=f"Donne-moi une liste de chapitres à maîtriser pour préparer une certification sur le thème : {theme}. Renvoie uniquement la liste numérotée.",
        expected_output="Une liste de chapitres au format JSON ou liste Python",
        agent=agent
    )

    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    response = str(crew.kickoff())

    # Extraire les chapitres ligne par ligne
    chapitres = [line.strip(" -0123456789.") for line in response.split("\n") if line.strip()]
    return chapitres


# --- Générer 30 questions pour un chapitre ---
def generer_questions_chapitre(chapitre):
    agent = Agent(
        role="Générateur de QCM",
        goal="Créer un QCM de 30 questions avec 4 choix et la bonne réponse",
        backstory="Expert pédagogique spécialisé dans la création d'examens",
        verbose=False,
        allow_delegation=False,
        llm=llm
    )

    task = Task(
        description=(
            f"Génère un QCM de 30 questions sur le chapitre : {chapitre}. "
            "Chaque question doit avoir 4 choix (A, B, C, D) et une seule bonne réponse. "
            "Format :\nQuestion 1: ...\nA) ...\nB) ...\nC) ...\nD) ...\nRéponse : X"
        ),
        expected_output="30 questions formatées avec réponses. Une seule bonne réponse par question.",
        agent=agent
    )

    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    qcm_text = str(crew.kickoff())

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
