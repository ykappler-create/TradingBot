from langchain import OpenAI, LLMChain, PromptTemplate
from langchain.agents import initialize_agent, Tool

# Initialisiere LLM
llm = OpenAI(temperature=0.2)

# Tool: Dateiansicht (liest history.csv)
def view_history(path: str) -> str:
    import pandas as pd
    df = pd.read_csv(path)
    return df.tail(5).to_string()

tools = [
    Tool(name="ViewHistory", func=view_history, description="Zeigt die letzten Zeilen von history.csv")
]

agent = initialize_agent(tools, llm, agent="zero-shot-react-description")

# Beispiel-Abfrage
result = agent.run("Zeige mir die letzten 5 Einträge in history.csv")
print(result)
