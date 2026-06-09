# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
import threading

load_dotenv()

# pyrefly: ignore [missing-import]
from src.backend.chat_engine.engine import ChatWorkflow
# pyrefly: ignore [missing-import]
from src.backend.chunking.repo_parser import get_files, get_filename, normalize_repo_url
# pyrefly: ignore [missing-import]
from src.backend.map.mapper import map_repository
# pyrefly: ignore [missing-import]
from src.backend.job_status import create_job, update_job, get_job, job_to_dict
# pyrefly: ignore [missing-import]
from src.backend.services.repo_activity import activity_tracker
# pyrefly: ignore [missing-import]
from langchain_openai import ChatOpenAI

app = FastAPI(title="Ask My repo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_engines: Dict[str, ChatWorkflow] = {}


@app.on_event("startup")
def startup_event():
    """Start the background cleanup task on API startup."""
    activity_tracker.start_cleanup_task()


def friendly_error(message: str) -> str:
    lower = message.lower()
    if "allocate memory" in lower or "onnxruntime" in lower:
        return "This repository is very large and ran out of memory while indexing. Try a smaller repo."
    if "git" in lower and ("clone" in lower or "failed" in lower or "not found" in lower):
        return "We couldn't download that repository. Please double-check the URL."
    if "neo4j" in lower:
        return "Couldn't save the code map. Check that your database connection is set up."
    if "qdrant" in lower:
        return "Couldn't save the search index. Check that your search storage is set up."
    if "openai" in lower or "api key" in lower:
        return "The AI assistant isn't configured yet. Check your API key."
    return "Something went wrong while setting up your repository. Please try again."


class ParseRequest(BaseModel):
    repo_url: str


class ChatRequest(BaseModel):
    repo_url: str
    query: str
    history: Optional[List[Dict[str, str]]] = []


def _run_parse_job(job_id: str, repo_url: str) -> None:
    try:
        def on_progress(stage: str, progress: int, message: str) -> None:
            update_job(job_id, stage=stage, progress=progress, message=message)

        mapped = map_repository(repo_url, on_progress=on_progress)
        repo_id = mapped["repo_id"]
        files = mapped["files"]

        # Record activity for this repo
        activity_tracker.record_activity(repo_id)

        llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000, max_retries=5, timeout=30.0)
        engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
        active_engines[repo_id] = engine

        update_job(
            job_id,
            stage="done",
            progress=100,
            message="All set! You can start asking questions.",
            status="done",
            result={
                "repo_id": repo_id,
                "files_count": len(files),
                "nodes_count": mapped["nodes_count"],
                "edges_count": mapped["edges_count"],
            },
        )
    except Exception as e:
        msg = friendly_error(str(e))
        update_job(job_id, stage="error", status="error", message=msg, error=msg)


@app.post("/api/parse")
def parse_repo(request: ParseRequest):
    repo_url = normalize_repo_url(request.repo_url)
    if not repo_url:
        raise HTTPException(status_code=400, detail="Repository URL is required.")

    job_id = create_job()
    thread = threading.Thread(target=_run_parse_job, args=(job_id, repo_url), daemon=True)
    thread.start()
    return {"status": "started", "job_id": job_id}


@app.get("/api/parse/status/{job_id}")
def parse_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_to_dict(job)


@app.post("/api/chat")
def chat(request: ChatRequest):
    repo_url = normalize_repo_url(request.repo_url)
    repo_id = get_filename(repo_url)
    if not repo_id:
        raise HTTPException(status_code=400, detail="Invalid repository URL.")

    # Record activity for this repo
    activity_tracker.record_activity(repo_id)

    if repo_id not in active_engines:
        try:
            files = get_files(repo_url)
            llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000, max_retries=5, timeout=30.0)
            engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
            active_engines[repo_id] = engine
        except Exception:
            raise HTTPException(status_code=400, detail="Repo not indexed. Please connect and index first.")

    engine = active_engines[repo_id]

    initial_state = {
        "repo_id": repo_id,
        "current_agent": "router",
        "router_decision": "hybrid",
        "reason": "",
        "context": "",
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
        raise HTTPException(status_code=500, detail=friendly_error(str(e)))


@app.get("/api/activity")
def get_activity_status():
    """Get current activity log and cleanup configuration."""
    from src.backend.services.repo_activity import INACTIVITY_TIMEOUT_HOURS, CLEANUP_CHECK_INTERVAL_MINUTES
    
    activity_log = {}
    for repo_id, last_activity in activity_tracker.activity_log.items():
        activity_log[repo_id] = last_activity.isoformat()
    
    return {
        "status": "ok",
        "active_repos": len(activity_tracker.activity_log),
        "activity_log": activity_log,
        "inactivity_timeout_hours": INACTIVITY_TIMEOUT_HOURS,
        "cleanup_check_interval_minutes": CLEANUP_CHECK_INTERVAL_MINUTES,
        "active_engines_count": len(active_engines),
    }


@app.post("/api/cleanup/manual/{repo_id}")
def manual_cleanup(repo_id: str):
    """Manually trigger cleanup for a specific repo."""
    if repo_id in active_engines:
        del active_engines[repo_id]
    
    success = activity_tracker.cleanup_repo(repo_id)
    return {
        "status": "success" if success else "partial_failure",
        "message": f"Cleanup triggered for repo '{repo_id}'.",
        "removed_from_cache": repo_id in active_engines,
    }


if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
