# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
import os

load_dotenv()

# pyrefly: ignore [missing-import]
from src.backend.chat_engine.engine import ChatWorkflow
# pyrefly: ignore [missing-import]
from src.backend.chunking.repo_parser import get_files, get_filename
# pyrefly: ignore [missing-import]
from langchain_openai import ChatOpenAI

app = FastAPI(title="R2G Mapper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global engine cache
active_engines: Dict[str, ChatWorkflow] = {}

class ParseRequest(BaseModel):
    repo_url: str

class ChatRequest(BaseModel):
    repo_url: str
    query: str
    history: Optional[List[Dict[str, str]]] = []

@app.post("/api/parse")
def parse_repo(request: ParseRequest):
    try:
        repo_url = request.repo_url
        repo_id = get_filename(repo_url)
        files = get_files(repo_url)
        
        llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000, max_retries=5, timeout=30.0)
        engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
        
        # Store engine in cache
        active_engines[repo_id] = engine
        
        return {
            "status": "success",
            "repo_id": repo_id,
            "message": "Repository parsed successfully",
            "files_count": len(files)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(request: ChatRequest):
    repo_id = get_filename(request.repo_url)
    if repo_id not in active_engines:
        # Re-initialize if not in memory (for simplicity, we assume files are cached)
        try:
            files = get_files(request.repo_url)
            llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000, max_retries=5, timeout=30.0)
            engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
            active_engines[repo_id] = engine
        except Exception as e:
            raise HTTPException(status_code=400, detail="Repo not parsed. Please parse first.")

    engine = active_engines[repo_id]
    
    initial_state = {
        "repo_id": repo_id,
        "current_agent": "router",
        "router_decision": "hybrid",
        "reason": "",
        "plan": [],
        "user_query": request.query,
        "user_history": request.history or [],
        "cypher_query": "",
        "graph_result": None,
        "vector_result": [],
        "final_answer": ""
    }
    
    try:
        result = engine.app.invoke(initial_state)
        return {
            "status": "success",
            "decision": result.get("router_decision", "unknown"),
            "reason": result.get("reason", ""),
            "answer": result.get("final_answer", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn  
    uvicorn.run(app, host="0.0.0.0", port=8000)
