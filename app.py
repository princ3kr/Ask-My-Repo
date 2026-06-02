import os
import sys
from langchain_openai import ChatOpenAI
from src.backend.chat_engine.engine import ChatWorkflow
from src.backend.chunking.repo_parser import get_files, get_filename

def run_chat_cli(repo_url: str):
    repo_id = get_filename(repo_url)
    print(f"[*] Preparing local files for {repo_id}...")
    files = get_files(repo_url)
    
    print("[*] Initializing ChatEngine workflow...")
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
    
    print("\n" + "="*50)
    print(f"Chat Engine ready for Repository: {repo_id}")
    print("Type your questions below. Type 'exit' to quit.")
    print("="*50 + "\n")
    
    while True:
        try:
            query = input("Ask a question: ").strip()
            if not query: continue
            if query.lower() in ("exit", "quit"): break
            
            initial_state = {
                "repo_id": repo_id,
                "current_agent": "router",
                "router_decision": "hybrid",
                "reason": "",
                "plan": [],
                "user_query": query,
                "user_history": [],
                "cypher_query": "",
                "graph_result": None,
                "vector_result": [],
                "final_answer": ""
            }
            
            print("[*] Executing LangGraph workflow...")
            result = engine.app.invoke(initial_state)
            
            print(f"\n[Agent Logic] Decision: {result['router_decision'].upper()} | Reason: {result['reason']}")
            print(f"[Synthesized Response]\n{result['final_answer']}")
            print("-" * 50 + "\n")
            
        except KeyboardInterrupt:
            break
            
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python app.py <repo_url>")
        sys.exit(1)
    run_chat_cli(sys.argv[1])