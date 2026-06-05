import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
import time
import math
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

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
from src.backend.chunking.repo_parser import get_filename

data_path = Path(__file__).resolve().parent / "questions.json"
with open(data_path) as f:
    eval_questions = json.load(f)

repo_url = "https://github.com/princ3kr/Notebook-LM-Mini"
repo_id = get_filename(repo_url)
print(f"[*] Reusing existing graph/vector indexes for {repo_id}...")
files = {}
llm = ChatOpenAI(model="gpt-4o", temperature=0, max_retries=5, timeout=30.0)
engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)

def process_sample(sample):
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
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"[*] Starting query: {sample['question']}")
            response = engine.app.invoke(initial_state)
            return {
                "question": sample["question"],
                "answer": response.get('final_answer', ''),
                "sources": response.get('context', ''),
                "contexts": [response.get('context', '')],
                "ground_truth": sample["ground_truth"]
            }
        except Exception as e:
            print(f"[Error] Failed query '{sample['question']}': {str(e)}")
            if attempt < max_retries - 1:
                print(f"[*] Retrying query in 2s ({attempt + 2}/{max_retries})...")
                time.sleep(2)
            else:
                return {
                    "question": sample["question"],
                    "answer": "Failed to retrieve answer.",
                    "sources": "",
                    "contexts": [""],
                    "ground_truth": sample["ground_truth"]
                }

BATCH_SIZE = 3
BATCH_COOLDOWN_SECS = 12

batches = [
    eval_questions[i : i + BATCH_SIZE]
    for i in range(0, len(eval_questions), BATCH_SIZE)
]
print(
    f"[*] Starting batched execution: {len(eval_questions)} queries "
    f"in {len(batches)} batches of {BATCH_SIZE} (cooldown={BATCH_COOLDOWN_SECS}s)..."
)
start_time = time.time()
results = []
for batch_idx, batch in enumerate(batches):
    print(f"[*] Batch {batch_idx + 1}/{len(batches)} ({len(batch)} queries)...")
    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
        batch_results = list(executor.map(process_sample, batch))
    results.extend(batch_results)
    if batch_idx < len(batches) - 1:
        print(f"[*] Cooldown {BATCH_COOLDOWN_SECS}s before next batch...")
        time.sleep(BATCH_COOLDOWN_SECS)
print(f"[*] Finished all queries in {time.time() - start_time:.2f} seconds.")

ragas_llm = LangchainLLMWrapper(ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_retries=5,
    timeout=30.0
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
print("===========================================================================================")
print()
print()
print()
print(results)
print()
print()
print()
print("===========================================================================================")