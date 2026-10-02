import logging
import os
import uuid

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny
from sentence_transformers import SentenceTransformer

load_dotenv()

logger = logging.getLogger("askmyrepo.vector_db")

base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
model_cache_dir = os.path.join(base_dir, 'src', 'models')
fastembed_cache_dir = os.path.join(model_cache_dir, 'fastembed')


def _ensure_model_env() -> None:
    """Create the model cache and point HuggingFace/fastembed at it.

    Deferred to first use. This used to run at import time — two os.makedirs
    calls plus three os.environ mutations — which meant merely importing the
    module required a writable filesystem and changed process-wide env for
    every importer.
    """
    os.makedirs(fastembed_cache_dir, exist_ok=True)
    os.environ['HF_HOME'] = model_cache_dir
    os.environ['FASTEMBED_CACHE_PATH'] = fastembed_cache_dir
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'

# ONNX fastembed pads every item in a batch to the longest sequence — one huge
# chunk can blow memory (50GB+). Keep chunks small and batches tiny.
MAX_CHUNK_LINES = 100
MAX_CHUNK_CHARS = 12_000
EMBED_BATCH_SIZE = 4

# Reserved point id, written only once a push finishes. Its presence is what
# distinguishes a complete index from one whose push was interrupted.
# Qdrant point ids must be an unsigned integer or a UUID, so this is a fixed
# deterministic UUID rather than a readable string.
INDEX_MARKER_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "ask-my-repo:index-complete"))


class VectorStore:
    def __init__(self, files: dict, collection_name: str):
        self.files = files
        self.collection_name = collection_name
        self.chunks = []
        self._embed_model = None
        
        url = os.getenv("QDRANT_END_POINT")
        api_key = os.getenv("QDRANT_API_KEY")
        
        if not url or not api_key:
            raise ValueError("Please set QDRANT_END_POINT and QDRANT_API_KEY in your .env file!")
            
        self.client = QdrantClient(url=url, api_key=api_key, timeout=60)
        
        self.client.set_model(
            "jinaai/jina-embeddings-v2-base-code",
            cache_dir=fastembed_cache_dir,
        )
        self.client.set_sparse_model(
            "Qdrant/bm25",
            cache_dir=fastembed_cache_dir,
        )
        
        if not self.client.collection_exists(self.collection_name):
            self._create_collection()

    @classmethod
    def collection_exists(cls, collection_name: str) -> bool:
        url = os.getenv("QDRANT_END_POINT")
        api_key = os.getenv("QDRANT_API_KEY")
        if not url or not api_key:
            raise ValueError("Please set QDRANT_END_POINT and QDRANT_API_KEY in your .env file!")
        client = QdrantClient(url=url, api_key=api_key, timeout=60)
        return client.collection_exists(collection_name)

    @classmethod
    def point_count(cls, collection_name: str) -> int:
        """0 if the collection is missing or unreachable — callers only use
        this for diagnostics."""
        try:
            url = os.getenv("QDRANT_END_POINT")
            api_key = os.getenv("QDRANT_API_KEY")
            client = QdrantClient(url=url, api_key=api_key, timeout=60)
            return client.get_collection(collection_name).points_count or 0
        except Exception as e:
            logger.warning(f"[qdrant] point_count failed for {collection_name}: {e}")
            return 0

    @classmethod
    def is_index_complete(cls, collection_name: str) -> bool:
        """True only if a previous push() ran to completion.

        A push that dies partway still leaves a collection that
        collection_exists() reports as True, so map_repository() would skip
        re-indexing forever and every answer would come back with missing
        context. The marker point is written only after the last batch.

        Falls back to False for collections created before the marker existed,
        which forces one clean re-index rather than silently serving a partial
        one.
        """
        try:
            url = os.getenv("QDRANT_END_POINT")
            api_key = os.getenv("QDRANT_API_KEY")
            client = QdrantClient(url=url, api_key=api_key, timeout=60)
            if not client.collection_exists(collection_name):
                return False
            hits = client.retrieve(
                collection_name=collection_name,
                ids=[INDEX_MARKER_ID],
                with_payload=False,
                with_vectors=False,
            )
            return bool(hits)
        except Exception as e:
            logger.warning(f"[qdrant] completeness check failed for {collection_name}: {e}")
            return False

    def _create_collection(self):
        _ensure_model_env()
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.client.get_fastembed_vector_params(),
            sparse_vectors_config=self.client.get_fastembed_sparse_vector_params(),
        )
        self.client.create_payload_index(
            collection_name=self.collection_name,
            field_name="path",
            field_schema="keyword",
        )

    def reset_collection(self):
        """Drop and recreate the collection for a clean re-index."""
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self._create_collection()

    @property
    def embed_model(self):
        if self._embed_model is None:
            _ensure_model_env()
            logger.info(f"Loading SentenceTransformer model (cache: {model_cache_dir})")
            self._embed_model = SentenceTransformer(
                "jinaai/jina-embeddings-v2-base-code",
                cache_folder=model_cache_dir,
                trust_remote_code=True,
            )
        return self._embed_model

    @staticmethod
    def _iter_line_ranges(start_line: int, end_line: int, max_lines: int, overlap: int):
        """Yield [start, end) line ranges, splitting long spans with overlap."""
        pos = start_line
        while pos < end_line:
            chunk_end = min(pos + max_lines, end_line)
            yield pos, chunk_end
            if chunk_end >= end_line:
                break
            pos = max(pos + 1, chunk_end - overlap)

    @staticmethod
    def _cap_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n# ... truncated for embedding ..."

    def _append_line_chunks(
        self,
        lines: list,
        start_line: int,
        end_line: int,
        base_metadata: dict,
        max_lines: int,
        overlap: int,
        name_prefix: str,
    ):
        ranges = list(self._iter_line_ranges(start_line, end_line, max_lines, overlap))
        for part_idx, (seg_start, seg_end) in enumerate(ranges):
            chunk = self._cap_text("\n".join(lines[seg_start:seg_end]))
            metadata = {
                **base_metadata,
                "function_name": (
                    name_prefix if len(ranges) == 1 else f"{name_prefix}_part_{part_idx}"
                ),
                "line_start": seg_start + 1,
                "line_end": seg_end,
            }
            self.chunks.append([chunk, metadata])

    def build(self, max_module_lines=MAX_CHUNK_LINES, overlap=5):
        self.chunks = []
        
        for file in self.files.keys():
            content = self.files[file]['content']
            lines = content.split('\n')
            classes = self.files[file]['classes']
            functions = self.files[file]['functions']
            path = file.replace("\\", "/")
            filename = file.split("/")[-1]
            
            for cls in classes:
                start_line = cls['line_start'] - 1
                end_line = cls['line_end']
                self._append_line_chunks(
                    lines,
                    start_line,
                    end_line,
                    {
                        "path": path,
                        "filename": filename,
                        "class_name": cls['name'],
                        "chunk_type": "class",
                        "methods": [
                            f['name'] for f in functions
                            if f.get('class_name') == cls['name']
                        ],
                    },
                    max_lines=max_module_lines,
                    overlap=overlap,
                    name_prefix=cls['name'],
                )
            
            for func in functions:
                if func.get('class_name'):
                    continue
                
                start_line = func['line_start'] - 1
                while start_line > 0 and lines[start_line - 1].strip().startswith('@'):
                    start_line -= 1
                end_line = func['line_end']

                self._append_line_chunks(
                    lines,
                    start_line,
                    end_line,
                    {
                        "path": path,
                        "filename": filename,
                        "class_name": "module_level",
                        "chunk_type": "function",
                    },
                    max_lines=max_module_lines,
                    overlap=overlap,
                    name_prefix=func['name'],
                )

            function_ranges = [(f['line_start'] - 1, f['line_end']) for f in functions]
            class_ranges = [(c['line_start'] - 1, c['line_end']) for c in classes]
            all_ranges = function_ranges + class_ranges
            
            # Single sweep with a bytearray instead of `any(...)` over every
            # range for every line, which was O(lines * ranges) — about half a
            # second for a single 10k-line file.
            covered = bytearray(len(lines))
            for start, end in all_ranges:
                start = max(0, start)
                end = min(len(lines), end)
                if end > start:
                    covered[start:end] = b"\x01" * (end - start)

            module_lines = [
                i for i, line in enumerate(lines)
                if not covered[i] and line.strip()
            ]

            if module_lines:
                step = max_module_lines
                for chunk_start_idx in range(0, len(module_lines), step):
                    chunk_indices = module_lines[chunk_start_idx:chunk_start_idx + step]
                    # Join only the selected lines. The previous version joined
                    # lines[start_line:end_line] — the whole span between the
                    # first and last selected line — which re-included every
                    # class and function body sitting between them. Those
                    # bodies were then embedded a second time inside a
                    # "module level" chunk, inflating the index and skewing
                    # retrieval toward duplicated text.
                    chunk = self._cap_text("\n".join(lines[i] for i in chunk_indices))
                    metadata = {
                        "path": path,
                        "filename": filename,
                        "function_name": f"module_segment_{chunk_start_idx // step}",
                        "class_name": "module_level",
                        "chunk_type": "module",
                        "line_start": chunk_indices[0] + 1,
                        "line_end": chunk_indices[-1] + 1,
                    }
                    self.chunks.append([chunk, metadata])

    def push(self, batch_size: int = EMBED_BATCH_SIZE, on_progress=None):
        if not self.chunks:
            logger.warning("No chunks to push. Run build() first.")
            return

        documents = [self._cap_text(c[0]) for c in self.chunks]
        metadatas = [c[1] for c in self.chunks]
        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{m['path']}:{m['function_name']}:{m['line_start']}:{i}"))
            for i, m in enumerate(metadatas)
        ]

        total = len(documents)

        # ONNX pads every item in a batch to the longest sequence in it, so one
        # huge chunk sets the memory cost for the whole batch. Sorting by length
        # first groups similar-sized chunks together, which is what makes a
        # batch size above 4 safe. The previous code left batches unsorted, which
        # is the only reason the cap was 4.
        order = sorted(range(total), key=lambda i: len(documents[i]))
        sorted_docs = [documents[i] for i in order]
        sorted_meta = [metadatas[i] for i in order]
        sorted_ids = [ids[i] for i in order]

        batch_size = max(1, batch_size)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            logger.debug(f"Pushing batch {start}-{end} / {total}")

            self.client.add(
                collection_name=self.collection_name,
                documents=sorted_docs[start:end],
                metadata=sorted_meta[start:end],
                ids=sorted_ids[start:end],
            )

            if on_progress and total:
                on_progress(end / total, f"{end}/{total}")

        self._write_index_marker(total)
        logger.info(f"Successfully pushed {total} chunks to Qdrant")

    def _write_index_marker(self, chunk_count: int) -> None:
        """Record that this collection is fully indexed.

        A zero vector of the collection's own dimension is used. When the
        collection uses named vectors (which is what set_model() creates) the
        point must supply the vector under that name, so both layouts are
        handled. The marker carries no `path`, so path-filtered searches — the
        only kind the chat path issues — never see it.
        """
        try:
            params = self.client.get_collection(self.collection_name).config.params.vectors
            if isinstance(params, dict):
                name, vp = next(iter(params.items()))
                vector = {name: [0.0] * vp.size}
            else:
                vector = [0.0] * params.size
            self.client.upsert(
                collection_name=self.collection_name,
                points=[{
                    "id": INDEX_MARKER_ID,
                    "vector": vector,
                    "payload": {"marker": True, "chunks": chunk_count},
                }],
            )
        except Exception as e:
            # Non-fatal: the index is still usable, it just cannot be verified.
            # Logged loudly because an unverifiable index is rebuilt next time.
            logger.warning(f"[qdrant] Could not write index marker: {e}")

    def search(self, query: str, query_filter=None, top_k: int = 5):
        results = self.client.query(
            collection_name=self.collection_name,
            query_text=query,
            query_filter=query_filter,
            limit=top_k
        )

        return {
            "documents": [[hit.document for hit in results]],
            "metadatas": [[hit.metadata for hit in results]],
            "ids": [[str(hit.id) for hit in results]],
            "scores": [[hit.score for hit in results]]
        }

    def vector_search(self, query: str, filenames: list, top_k: int = 5):
        qdrant_filter = Filter(
            must=[
                FieldCondition(
                    key="path",
                    match=MatchAny(any=filenames)
                )
            ]
        )
        return self.search(
            query=query, 
            query_filter=qdrant_filter, 
            top_k=top_k
        )
    
    def rerank(self, context: dict, query: str, top_k: int):
        documents = context.get('documents', [[]])[0]
        metadatas = context.get('metadatas', [[]])[0]
        scores = context.get('scores', [[]])[0]
        
        if not documents:
            return []
            
        if scores and len(scores) == len(documents):
            # Qdrant already computed the scores and returned them in relevance
            # order, so just use them.
            ranked = sorted(
                [(metadatas[i], scores[i], documents[i]) for i in range(len(documents))],
                key=lambda x: x[1],
                reverse=True
            )[:top_k]
            return ranked

        if scores and len(scores) != len(documents):
            # A partial/mismatched score list is not something to re-rank on, but
            # Qdrant's existing order is still the best ranking available.
            # Falling through to a local re-encode here would silently load a
            # second copy of the embedding model.
            logger.warning(
                f"[rerank] {len(documents)} docs but {len(scores)} scores; "
                "preserving Qdrant order."
            )
            ranked = list(zip(metadatas, documents))[:top_k]
            return [(m, 0.0, d) for m, d in ranked]

        # Fallback to local SentenceTransformer if scores are not pre-computed
        logger.debug("[rerank] No scores returned; re-encoding locally")
        query_embedding = self.embed_model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
        doc_embeddings = self.embed_model.encode(documents, convert_to_numpy=True, normalize_embeddings=True)
        
        computed_scores = np.dot(doc_embeddings, query_embedding)
        
        ranked = sorted(
            [(metadatas[i], computed_scores[i], documents[i]) for i in range(len(documents))],
            key=lambda x: x[1],
            reverse=True
        )[:top_k]
        
        return ranked