# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException, Request
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from dotenv import load_dotenv
import threading
import os
import time
import uuid
import logging
import traceback

load_dotenv()

from src.backend.chat_engine.engine import ChatWorkflow
from src.backend.chunking.repo_parser import get_files, get_filename, normalize_repo_url
from src.backend.map.mapper import map_repository
from src.backend.job_status import create_job, update_job, get_job, job_to_dict
from src.backend.services.repo_activity import activity_tracker
from src.backend.services.llm_fallback import FallbackChatModel

# ═══════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════
LOG_FORMAT = (
    "[%(asctime)s] %(levelname)-8s | %(name)-25s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.DEBUG,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
    ],
)

# Set noisy third-party loggers to WARNING
for noisy_logger in [
    "langchain",
    "langgraph",
    "httpx",
    "httpcore",
    "urllib3",
    "neo4j",
    "openai",
    "qdrant_client",
]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

logger = logging.getLogger("askmyrepo.api")
llm_logger = logging.getLogger("askmyrepo.llm")
engine_logger = logging.getLogger("askmyrepo.engine")
mapper_logger = logging.getLogger("askmyrepo.mapper")

# ═══════════════════════════════════════════════════════════

app = FastAPI(title="Ask My repo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Logging Middleware ──
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    method = request.method
    path = request.url.path
    query = str(request.url.query) if request.url.query else ""

    logger.info(f"[{request_id}] → {method} {path}{'?' + query if query else ''}")

    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        logger.info(
            f"[{request_id}] ← {response.status_code} {method} {path} "
            f"({elapsed*1000:.0f}ms)"
        )
        return response
    except Exception as e:
        elapsed = time.perf_counter() - start
        logger.error(f"[{request_id}] ✗ EXCEPTION {method} {path} ({elapsed*1000:.0f}ms)")
        logger.error(f"[{request_id}]   Type: {type(e).__name__}")
        logger.error(f"[{request_id}]   Message: {e}")
        for line in traceback.format_exc().splitlines():
            logger.error(f"[{request_id}]   {line}")
        return HTMLResponse(
            status_code=500,
            content=f'<html><body><h1>500 Internal Server Error</h1><pre>{traceback.format_exc()}</pre></body></html>',
        )


active_engines: Dict[str, ChatWorkflow] = {}
session_histories: Dict[str, List[Dict[str, str]]] = {}
MAX_HISTORY_TURNS = 16


def _engine_key(repo_id: str, session_id: str) -> str:
    return f"{repo_id}:{session_id}"


@app.on_event("startup")
def startup_event():
    """Start the background cleanup task on API startup."""
    logger.info("Server starting up — initializing activity tracker...")
    activity_tracker.start_cleanup_task(
        engine_cache=active_engines, history_store=session_histories
    )
    logger.info("Server ready on port 8000")


def friendly_error(message: str, details: str = "") -> str:
    lower = message.lower()
    if "allocate memory" in lower or "onnxruntime" in lower:
        return "This repository is very large and ran out of memory while indexing. Try a smaller repo."
    if "git" in lower and ("clone" in lower or "failed" in lower or "not found" in lower):
        return "We couldn't download that repository. Please double-check the URL."
    if "neo4j" in lower:
        return "Couldn't save the code map. Check that your database connection is set up."
    if "qdrant" in lower:
        return "Couldn't save the search index. Check that your search storage is set up."
    if "openai" in lower or "api key" in lower or "authentication" in lower:
        return "The AI assistant isn't configured yet. Check your API key."
    if "groq" in lower:
        return "The AI assistant (fallback) encountered an error. Check your Groq API key."
    return "Something went wrong while setting up your repository. Please try again."


class ParseRequest(BaseModel):
    repo_url: str


class ChatRequest(BaseModel):
    repo_url: str
    query: str
    session_id: str = Field(
        ..., min_length=1, description="Client-generated UUID persisted in localStorage"
    )


def _get_or_create_engine(repo_id: str, session_id: str, repo_url: str) -> ChatWorkflow:
    key = _engine_key(repo_id, session_id)
    if key in active_engines:
        engine_logger.debug(f"Reusing existing engine: {key}")
        return active_engines[key]

    engine_logger.info(f"Creating new engine: {key}")
    files = get_files(repo_url)
    llm = FallbackChatModel()
    engine = ChatWorkflow(repo_id=repo_id, files=files, llm=llm)
    active_engines[key] = engine
    engine_logger.info(f"Engine created: {key} (model: {llm.model_name})")
    return engine


def _run_parse_job(job_id: str, repo_url: str) -> None:
    logger.info(f"[{job_id}] Starting parse job for: {repo_url}")
    try:

        def on_progress(stage: str, progress: int, message: str) -> None:
            update_job(job_id, stage=stage, progress=progress, message=message)
            logger.debug(f"[{job_id}] Progress: {stage} {progress}% - {message}")

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
        logger.info(
            f"[{job_id}] Parse complete: {len(mapped['files'])} files, "
            f"{mapped['nodes_count']} nodes, {mapped['edges_count']} edges"
        )
    except Exception as e:
        logger.error(f"[{job_id}] Parse job failed:")
        logger.error(f"  Type: {type(e).__name__}")
        logger.error(f"  Message: {e}")
        for line in traceback.format_exc().splitlines():
            logger.error(f"  {line}")
        msg = friendly_error(str(e))
        update_job(
            job_id, stage="error", status="error", message=msg, error=msg
        )


@app.post("/api/parse")
def parse_repo(request: ParseRequest):
    repo_url = normalize_repo_url(request.repo_url)
    if not repo_url:
        logger.warning("Parse request with empty URL")
        raise HTTPException(status_code=400, detail="Repository URL is required.")

    job_id = create_job()
    logger.info(f"Parse initiated: job_id={job_id}, repo={repo_url}")
    thread = threading.Thread(
        target=_run_parse_job, args=(job_id, repo_url), daemon=True
    )
    thread.start()
    return {"status": "started", "job_id": job_id}


@app.get("/api/parse/status/{job_id}")
def parse_status(job_id: str):
    job = get_job(job_id)
    if not job:
        logger.warning(f"Job status requested for unknown job: {job_id}")
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_to_dict(job)


@app.post("/api/chat")
def chat(request: ChatRequest):
    repo_url = normalize_repo_url(request.repo_url)
    repo_id = get_filename(repo_url)
    chat_logger = logging.getLogger("askmyrepo.chat")

    if not repo_id:
        logger.warning(f"Chat request with invalid repo URL: {repo_url}")
        raise HTTPException(status_code=400, detail="Invalid repository URL.")
    if not request.session_id.strip():
        logger.warning("Chat request with empty session_id")
        raise HTTPException(status_code=400, detail="session_id is required.")

    session_id = request.session_id.strip()
    engine_key = _engine_key(repo_id, session_id)

    chat_logger.info(
        f"Chat: repo={repo_id} session={session_id[:8]} "
        f'query="{request.query[:80]}{"..." if len(request.query) > 80 else ""}"'
    )

    activity_tracker.record_activity(repo_id)

    try:
        engine = _get_or_create_engine(repo_id, session_id, repo_url)
    except Exception as e:
        logger.error(f"Failed to get engine for {engine_key}: {e}")
        raise HTTPException(
            status_code=400,
            detail="Repo not indexed. Please connect and index first.",
        )

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
        chat_logger.debug("Invoking LangGraph workflow...")
        start = time.perf_counter()
        result = engine.app.invoke(initial_state)
        elapsed = time.perf_counter() - start
        answer = result.get("final_answer", "")

        history.append({"role": "user", "content": request.query})
        history.append({"role": "assistant", "content": answer})
        session_histories[engine_key] = history[-MAX_HISTORY_TURNS:]

        fallback_flag = ""
        if hasattr(engine.llm, '_fallback_used') and engine.llm._fallback_used:
            fallback_flag = " (via Groq fallback)"

        chat_logger.info(
            f"Response: decision={result.get('router_decision', '?')} "
            f"len={len(answer)} elapsed={elapsed:.1f}s{fallback_flag}"
        )

        return {
            "status": "success",
            "decision": result.get("router_decision", "unknown"),
            "reason": result.get("reason", ""),
            "answer": answer,
            "rewritten_query": result.get("rewritten_query", ""),
        }
    except Exception as e:
        chat_logger.error(f"Chat workflow failed:")
        chat_logger.error(f"  Type: {type(e).__name__}")
        chat_logger.error(f"  Message: {e}")
        for line in traceback.format_exc().splitlines():
            chat_logger.error(f"  {line}")
        raise HTTPException(status_code=500, detail=friendly_error(str(e)))


@app.get("/api/activity")
def get_activity_status():
    """Get current activity log and cleanup configuration."""
    from src.backend.services.repo_activity import (
        INACTIVITY_TIMEOUT_HOURS,
        CLEANUP_CHECK_INTERVAL_MINUTES,
    )

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
    logger.info(f"Manual cleanup triggered for repo: {repo_id}")

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
    except Exception as exc:
        logger.warning(f"Failed to clear graph cache: {exc}")

    logger.info(
        f"Cleanup done: {len(removed_engine_keys)} engines, "
        f"{len(removed_history_keys)} sessions removed"
    )

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


@app.get("/api/tree/{repo_id}")
def get_tree(repo_id: str):
    from src.backend.chunking.chunk_builder import URI, USER, PASSWORD
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        with driver.session() as session:
            res = session.run(
                "MATCH (f:File {repo_id: $repo_id}) RETURN f.path AS path",
                repo_id=repo_id,
            )
            paths = [record["path"] for record in res]
            logger.debug(f"Tree for {repo_id}: {len(paths)} files")
            return {"paths": paths}
    except Exception as e:
        logger.error(f"Tree query failed for {repo_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@app.get("/api/graph_data/{repo_id}")
def get_graph_data(repo_id: str):
    from src.backend.chunking.chunk_builder import URI, USER, PASSWORD
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        with driver.session() as session:
            nodes_query = """
            MATCH (n {repo_id: $repo_id})
            WHERE ANY(label IN labels(n) WHERE label IN ['File', 'Class', 'Function', 'ExternalSymbol'])
            RETURN id(n) as id, labels(n)[0] as type, n
            """
            nodes_res = session.run(nodes_query, repo_id=repo_id)
            nodes = []

            for record in nodes_res:
                n_id = str(record["id"])
                n_type = record["type"]
                props = dict(record["n"])
                label = props.get("name", props.get("path", n_id))

                nodes.append(
                    {
                        "id": n_id,
                        "type": "customNode",
                        "data": {"label": label, "nodeType": n_type, **props},
                        "position": {"x": 0, "y": 0},
                    }
                )

            edges_query = """
            MATCH (a {repo_id: $repo_id})-[r]->(b {repo_id: $repo_id})
            RETURN id(r) as id, id(a) as source, id(b) as target, type(r) as type
            """
            edges_res = session.run(edges_query, repo_id=repo_id)
            edges = []
            for record in edges_res:
                edges.append(
                    {
                        "id": f"e{record['id']}",
                        "source": str(record["source"]),
                        "target": str(record["target"]),
                        "label": record["type"],
                        "data": {"type": record["type"]},
                    }
                )

            logger.debug(
                f"Graph data for {repo_id}: {len(nodes)} nodes, {len(edges)} edges"
            )
            return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"Graph data query failed for {repo_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn

    logger.info("Starting Ask My Repo API server...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
