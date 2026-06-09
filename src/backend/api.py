# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from dotenv import load_dotenv
import threading
import os

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
session_histories: Dict[str, List[Dict[str, str]]] = {}
MAX_HISTORY_TURNS = 16


def _engine_key(repo_id: str, session_id: str) -> str:
    return f"{repo_id}:{session_id}"


@app.on_event("startup")
def startup_event():
    """Start the background cleanup task on API startup."""
    activity_tracker.start_cleanup_task(engine_cache=active_engines, history_store=session_histories)


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
    session_id: str = Field(..., min_length=1, description="Client-generated UUID persisted in localStorage")


def _get_or_create_engine(repo_id: str, session_id: str, repo_url: str) -> ChatWorkflow:
    key = _engine_key(repo_id, session_id)
    if key in active_engines:
        return active_engines[key]

    files = get_files(repo_url)
    llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=1000, max_retries=5, timeout=30.0)
    engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
    active_engines[key] = engine
    return engine


def _run_parse_job(job_id: str, repo_url: str) -> None:
    try:
        def on_progress(stage: str, progress: int, message: str) -> None:
            update_job(job_id, stage=stage, progress=progress, message=message)

        mapped = map_repository(repo_url, on_progress=on_progress)
        repo_id = mapped["repo_id"]

        activity_tracker.record_activity(repo_id)

        update_job(
            job_id,
            stage="done",
            progress=100,
            message="All set! You can start asking questions.",
            status="done",
            result={
                "repo_id": repo_id,
                "files_count": len(mapped["files"]),
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
    if not request.session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required.")

    session_id = request.session_id.strip()
    engine_key = _engine_key(repo_id, session_id)

    activity_tracker.record_activity(repo_id)

    try:
        engine = _get_or_create_engine(repo_id, session_id, repo_url)
    except Exception:
        raise HTTPException(status_code=400, detail="Repo not indexed. Please connect and index first.")

    history = session_histories.get(engine_key, [])

    initial_state = {
        "repo_id": repo_id,
        "session_id": session_id,
        "current_agent": "router",
        "router_decision": "hybrid",
        "reason": "",
        "context": "",
        "plan": [],
        "user_query": request.query,
        "rewritten_query": "",
        "user_history": history,
        "cypher_query": "",
        "graph_result": None,
        "vector_result": [],
        "architect_subtype": "",
        "final_answer": "",
    }

    try:
        result = engine.app.invoke(initial_state)
        answer = result.get("final_answer", "")

        history.append({"role": "user", "content": request.query})
        history.append({"role": "assistant", "content": answer})
        session_histories[engine_key] = history[-MAX_HISTORY_TURNS:]

        return {
            "status": "success",
            "decision": result.get("router_decision", "unknown"),
            "reason": result.get("reason", ""),
            "answer": answer,
            "rewritten_query": result.get("rewritten_query", ""),
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
        "active_sessions_count": len(session_histories),
    }


@app.post("/api/cleanup/manual/{repo_id}")
def manual_cleanup(repo_id: str):
    """Manually trigger cleanup for a specific repo."""
    removed_engine_keys = [
        k for k in list(active_engines.keys()) if k.startswith(f"{repo_id}:")
    ]
    for k in removed_engine_keys:
        del active_engines[k]

    removed_history_keys = [
        k for k in list(session_histories.keys()) if k.startswith(f"{repo_id}:")
    ]
    for k in removed_history_keys:
        del session_histories[k]

    success = activity_tracker.cleanup_repo(
        repo_id,
        engine_cache=active_engines,
        history_store=session_histories,
    )
    # Attempt to remove cached graph HTML for the repo and report whether it was cleared
    notebook_dir = os.path.join(os.getcwd(), "notebook")
    per_repo_path = os.path.join(notebook_dir, f"{repo_id}_graph.html")
    generic_path = os.path.join(notebook_dir, "graph.html")
    graph_cache_cleared = {"per_repo": False, "generic": False}
    try:
        if os.path.exists(per_repo_path):
            os.remove(per_repo_path)
            graph_cache_cleared["per_repo"] = True
        if os.path.exists(generic_path):
            os.remove(generic_path)
            graph_cache_cleared["generic"] = True
    except Exception:
        # leave boolean flags as-is if deletion failed for either
        pass

    return {
        "status": "success" if success else "partial_failure",
        "message": f"Cleanup triggered for repo '{repo_id}'.",
        "removed_engine_sessions": len(removed_engine_keys),
        "removed_history_sessions": len(removed_history_keys),
        "graph_cache_cleared": graph_cache_cleared,
    }


@app.get("/api/graph/{repo_id}")
def get_graph(repo_id: str):
    """Return the previously saved pyvis HTML for a repo, if present."""
    # Prefer per-repo filename, fall back to generic 'graph.html' for older saves
    notebook_dir = os.path.join(os.getcwd(), "notebook")
    per_repo = os.path.join(notebook_dir, f"{repo_id}_graph.html")
    generic = os.path.join(notebook_dir, "graph.html")

    chosen = None
    if os.path.exists(per_repo):
        chosen = per_repo
    elif os.path.exists(generic):
        chosen = generic

    if not chosen:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        with open(chosen, "r", encoding="utf-8") as fh:
            content = fh.read()
        return HTMLResponse(content=content, media_type="text/html")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
