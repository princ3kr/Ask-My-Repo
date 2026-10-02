"""RAGAS evaluation harness.

Usage:
    uv run python -m src.evaluation.eval <repo_url>

The repository must already be indexed (run ``POST /api/parse`` for it first);
this reuses the existing Neo4j and Qdrant data.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.backend.chat_engine.engine import ChatWorkflow  # noqa: E402
from src.backend.chunking.repo_parser import get_filename  # noqa: E402
from src.backend.services.llm_fallback import FallbackChatModel  # noqa: E402

BATCH_SIZE = 3
BATCH_COOLDOWN_SECS = 12
MAX_RETRIES = 3


def load_questions() -> list[dict]:
    path = Path(__file__).resolve().parent / "questions.json"
    with open(path) as f:
        return json.load(f)


def main() -> None:
    # The repo was hardcoded here, so evaluating meant editing the file. It is
    # also an argument now so that importing this module does nothing: the
    # previous module-level code started spending LLM credits and hitting both
    # databases on `import src.evaluation.eval`.
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    repo_url = sys.argv[1]

    repo_id = get_filename(repo_url)
    if not repo_id:
        print(f"Not a valid repository URL: {repo_url}")
        sys.exit(1)
    questions = load_questions()
    print(f"[*] Reusing existing graph/vector indexes for {repo_id}...")

    # FallbackChatModel, like the API, so an OpenAI outage degrades to Groq
    # rather than aborting the whole evaluation run.
    engine = ChatWorkflow(repo_id=repo_id, files={}, llm=FallbackChatModel())

    def process_sample(sample: dict) -> dict:
        initial_state = {
            "repo_id": repo_id,
            "session_id": "eval",
            "current_agent": "router",
            "router_decision": "hybrid",
            "reason": "",
            "context": "",
            "plan": [],
            "user_query": sample['question'],
            "rewritten_query": "",
            "user_history": [],
            "cypher_query": "",
            "graph_result": None,
            "vector_result": [],
            "architect_subtype": "",
            "final_answer": "",
        }

        for attempt in range(MAX_RETRIES):
            try:
                print(f"[*] {sample['question']}")
                response = engine.app.invoke(initial_state)
                return {
                    "question": sample["question"],
                    "answer": response.get('final_answer', ''),
                    "sources": response.get('context', ''),
                    "contexts": [response.get('context', '')],
                    "ground_truth": sample["ground_truth"],
                }
            except Exception as e:
                print(f"[Error] {sample['question']}: {e!s}")
                if attempt < MAX_RETRIES - 1:
                    print(f"[*] Retrying in 2s (attempt {attempt + 2}/{MAX_RETRIES})...")
                    time.sleep(2)
                else:
                    return {
                        "question": sample["question"],
                        "answer": "Failed to retrieve answer.",
                        "sources": "",
                        "contexts": [""],
                        "ground_truth": sample["ground_truth"],
                    }

    batches = [
        questions[i : i + BATCH_SIZE]
        for i in range(0, len(questions), BATCH_SIZE)
    ]
    print(
        f"[*] {len(questions)} queries in {len(batches)} batches of "
        f"{BATCH_SIZE} (cooldown={BATCH_COOLDOWN_SECS}s)"
    )

    start = time.time()
    results: list[dict] = []
    for idx, batch in enumerate(batches, 1):
        print(f"[*] Batch {idx}/{len(batches)} ({len(batch)} queries)")
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            results.extend(executor.map(process_sample, batch))
        if idx < len(batches):
            print(f"[*] Cooldown {BATCH_COOLDOWN_SECS}s before next batch")
            time.sleep(BATCH_COOLDOWN_SECS)
    print(f"[*] Finished all queries in {time.time() - start:.2f}s")

    # Imported here, not at module scope: the installed `ragas` release does
    # `from langchain_community.chat_models.vertexai import ChatVertexAI`, a
    # module path that no longer exists in current langchain-community, so
    # importing ragas at all raises ModuleNotFoundError. Keeping it lazy lets
    # the rest of this module (arg validation, question loading) work, and
    # confines the incompatibility to the scoring step. See the note in
    # pyproject about pinning ragas/langchain-community.
    from datasets import Dataset
    from langchain_openai import ChatOpenAI
    from ragas import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_correctness,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    scores = evaluate(
        Dataset.from_list(results),
        metrics=[
            faithfulness,
            answer_relevancy,
            answer_correctness,
            context_precision,
            context_recall,
        ],
        llm=LangchainLLMWrapper(
            ChatOpenAI(model="gpt-4o-mini", temperature=0, max_retries=5, timeout=30.0)
        ),
    )

    print("\n" + "=" * 60)
    print(json.dumps({k: float(v) for k, v in scores.items()}, indent=2))
    print("=" * 60)
    print("\nPer-question results:")
    for r in results:
        print(f"\n  Q: {r['question']}")
        print(f"  A: {r['answer'][:400]}")


if __name__ == "__main__":
    main()
