import os
import threading
import time
import heapq
from datetime import datetime, timedelta
from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

load_dotenv()

# Configuration
INACTIVITY_TIMEOUT_HOURS = int(os.getenv("REPO_INACTIVITY_TIMEOUT_HOURS", "3"))
CLEANUP_CHECK_INTERVAL_MINUTES = int(os.getenv("CLEANUP_CHECK_INTERVAL_MINUTES", "5"))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASS = os.getenv("NEO4J_PASS")

QDRANT_END_POINT = os.getenv("QDRANT_END_POINT")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")


class RepoActivityTracker:
    """Track last activity time for each repo and auto-cleanup after inactivity."""

    def __init__(self):
        self.activity_log: dict[str, datetime] = {}
        self.lock = threading.Lock()
        self.cleanup_thread = None
        self.running = False
        self.failed_cleanups: dict[str, int] = {}  # Track retry count for failed repos
        self.neo4j_driver = None
        self.qdrant_client = None

    def _init_connections(self) -> tuple:
        """Initialize and cache database connections."""
        if not self.neo4j_driver and NEO4J_URI and NEO4J_USER and NEO4J_PASS:
            self.neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        
        if not self.qdrant_client and QDRANT_END_POINT and QDRANT_API_KEY:
            self.qdrant_client = QdrantClient(url=QDRANT_END_POINT, api_key=QDRANT_API_KEY, timeout=60)
        
        return self.neo4j_driver, self.qdrant_client

    def record_activity(self, repo_id: str) -> None:
        """Update the last activity timestamp for a repo."""
        with self.lock:
            self.activity_log[repo_id] = datetime.now()
            # Reset failure count on successful activity
            if repo_id in self.failed_cleanups:
                del self.failed_cleanups[repo_id]
            print(f"[Activity] Repo '{repo_id}' activity recorded at {datetime.now().isoformat()}")

    def get_next_cleanup_repo(self, timeout_hours: int = INACTIVITY_TIMEOUT_HOURS) -> str | None:
        """Get the next repo to clean up using priority queue logic."""
        with self.lock:
            if not self.activity_log:
                return None
            
            now = datetime.now()
            timeout = timedelta(hours=timeout_hours)
            
            # Find the repo that has been inactive the longest
            oldest_repo = None
            oldest_time = None
            
            for repo_id, last_activity in self.activity_log.items():
                time_since = now - last_activity
                if time_since > timeout:
                    if oldest_time is None or time_since > oldest_time:
                        oldest_repo = repo_id
                        oldest_time = time_since
            
            return oldest_repo

    def cleanup_neo4j_repo(self, repo_id: str) -> bool:
        """Delete repo graph from Neo4j (optimized with connection reuse)."""
        if not self.neo4j_driver:
            print(f"[Cleanup] Neo4j driver not initialized for {repo_id}.")
            return False

        try:
            with self.neo4j_driver.session() as session:
                # Single transaction for all deletions
                result = session.run(
                    """
                    MATCH (n {repo_id: $repo_id})
                    WITH COUNT(n) as count
                    MATCH (n {repo_id: $repo_id})
                    DETACH DELETE n
                    RETURN count
                    """,
                    repo_id=repo_id,
                )
                record = result.single()
                deleted_count = record[0] if record else 0
                print(f"[Cleanup] Deleted {deleted_count} Neo4j nodes for repo '{repo_id}'.")
                return True
        except Exception as e:
            print(f"[Cleanup Error] Failed to delete Neo4j graph for {repo_id}: {e}")
            return False

    def cleanup_qdrant_collection(self, repo_id: str) -> bool:
        """Delete repo collection from Qdrant (optimized with connection reuse)."""
        if not self.qdrant_client:
            print(f"[Cleanup] Qdrant client not initialized for {repo_id}.")
            return False

        try:
            collection_name = f"repo_{repo_id}"
            if self.qdrant_client.collection_exists(collection_name):
                self.qdrant_client.delete_collection(collection_name)
                print(f"[Cleanup] Deleted Qdrant collection '{collection_name}' for repo '{repo_id}'.")
                return True
            else:
                print(f"[Cleanup] Qdrant collection '{collection_name}' not found for repo '{repo_id}'.")
                return False
        except Exception as e:
            print(f"[Cleanup Error] Failed to delete Qdrant collection for {repo_id}: {e}")
            return False

    def _wipe_session_stores(self, repo_id: str, engine_cache: dict | None, history_store: dict | None) -> None:
        """Remove all session-scoped cache entries for a repo."""
        prefix = f"{repo_id}:"
        if engine_cache is not None:
            for key in [k for k in engine_cache if k.startswith(prefix)]:
                del engine_cache[key]
        if history_store is not None:
            for key in [k for k in history_store if k.startswith(prefix)]:
                del history_store[key]

    def cleanup_repo(
        self,
        repo_id: str,
        engine_cache: dict | None = None,
        history_store: dict | None = None,
    ) -> bool:
        """Clean up Neo4j, Qdrant, and optional session caches for an inactive repo."""
        print(f"[Cleanup] Starting cleanup for inactive repo '{repo_id}'...")

        self._wipe_session_stores(repo_id, engine_cache, history_store)

        failure_count = self.failed_cleanups.get(repo_id, 0)

        neo4j_ok = self.cleanup_neo4j_repo(repo_id)
        qdrant_ok = self.cleanup_qdrant_collection(repo_id)
        
        success = neo4j_ok and qdrant_ok
        
        # Remove from activity log on success
        with self.lock:
            if success and repo_id in self.activity_log:
                del self.activity_log[repo_id]
                # Clear failure count
                if repo_id in self.failed_cleanups:
                    del self.failed_cleanups[repo_id]
                print(f"[Cleanup] Successfully cleaned up repo '{repo_id}'.")
            elif not success:
                # Track failure and apply exponential backoff
                self.failed_cleanups[repo_id] = failure_count + 1
                print(f"[Cleanup] Failed to cleanup '{repo_id}' (attempt {failure_count + 1}). Will retry later.")
        
        return success

    def _cleanup_loop(self) -> None:
        """Background thread loop that periodically checks and cleans up inactive repos."""
        print(f"[Cleanup] Initialized connections to Neo4j and Qdrant.")
        self._init_connections()
        
        while self.running:
            try:
                # Only check the next repo that needs cleanup (optimization)
                repo_to_cleanup = self.get_next_cleanup_repo(INACTIVITY_TIMEOUT_HOURS)
                
                if repo_to_cleanup:
                    print(f"[Cleanup] Found inactive repo: {repo_to_cleanup}")
                    self.cleanup_repo(
                        repo_to_cleanup,
                        engine_cache=getattr(self, "_engine_cache", None),
                        history_store=getattr(self, "_history_store", None),
                    )
                else:
                    with self.lock:
                        active_count = len(self.activity_log)
                    if active_count > 0:
                        print(f"[Cleanup] No repos to clean yet. Monitoring {active_count} active repo(s).")

                # Sleep for the configured interval
                time.sleep(CLEANUP_CHECK_INTERVAL_MINUTES * 60)
            except Exception as e:
                print(f"[Cleanup Thread Error] {e}")
                time.sleep(CLEANUP_CHECK_INTERVAL_MINUTES * 60)

    def start_cleanup_task(
        self,
        engine_cache: dict | None = None,
        history_store: dict | None = None,
    ) -> None:
        """Start the background cleanup thread."""
        if self.running:
            print("[Cleanup] Cleanup task already running.")
            return

        self._engine_cache = engine_cache
        self._history_store = history_store
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        print(f"[Cleanup] Cleanup task started (timeout: {INACTIVITY_TIMEOUT_HOURS}h, check interval: {CLEANUP_CHECK_INTERVAL_MINUTES}min).")

    def stop_cleanup_task(self) -> None:
        """Stop the background cleanup thread and close connections."""
        self.running = False
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        # Close database connections
        if self.neo4j_driver:
            self.neo4j_driver.close()
            self.neo4j_driver = None
        
        print("[Cleanup] Cleanup task stopped.")


# Global singleton instance
activity_tracker = RepoActivityTracker()

