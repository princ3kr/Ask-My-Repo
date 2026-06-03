import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ragas import evaluate
from langchain_openai import ChatOpenAI
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    answer_correctness,
    context_precision,
    context_recall
)
from ragas.llms import LangchainLLMWrapper
from datasets import Dataset
import json
from src.backend.chat_engine.engine import ChatWorkflow
from src.backend.chunking.repo_parser import get_files, get_filename

with open('src/evaluation/questions.json') as f:
    eval_questions = json.load(f)

results = []
for q in eval_questions:
    results.append(q)

repo_id = get_filename("https://github.com/princ3kr/Notebook-LM-Mini")
print(f"[*] Preparing local files for {repo_id}...")
files = get_files("https://github.com/princ3kr/Notebook-LM-Mini")
llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000)
engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)

for sample in eval_questions:
    initial_state = {
        "repo_id": repo_id,
        "current_agent": "router",
        "router_decision": "hybrid",
        "reason": "",
        "plan": [],
        "user_query": sample['question'],
        "user_history": [],
        "cypher_query": "",
        "graph_result": None,
        "vector_result": [],
        "final_answer": ""
    }
    response = engine.app.invoke(initial_state)
   
    results.append({
        "question": sample["question"],
        "answer": response['final_answer'],
        "sources": response['context'],
        "ground_truth": sample["ground_truth"]
    })

ragas_llm = LangchainLLMWrapper(ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=600
))

dataset = Dataset.from_list(results)

scores = evaluate(
    dataset,
    metrics=[
        faithfulness,
        answer_relevancy,
        answer_correctness,
        context_precision,
        context_recall
    ],
    llm=ragas_llm
)

print(scores)