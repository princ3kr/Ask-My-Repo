import os
import numpy as np
import uuid
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny
from langchain_openai import ChatOpenAI
from sentence_transformers import SentenceTransformer

load_dotenv()
base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['HF_HOME'] = os.path.join(base_dir, 'models')

# ONNX fastembed pads every item in a batch to the longest sequence — one huge
# chunk can blow memory (50GB+). Keep chunks small and batches tiny.
MAX_CHUNK_LINES = 100
MAX_CHUNK_CHARS = 12_000
EMBED_BATCH_SIZE = 4

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
        
        self.client.set_model("jinaai/jina-embeddings-v2-base-code")
        self.client.set_sparse_model("Qdrant/bm25")
        
        if not self.client.collection_exists(self.collection_name):
            self._create_collection()

    def _create_collection(self):
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
            print("Loading SentenceTransformer model...")
            self._embed_model = SentenceTransformer("jinaai/jina-embeddings-v2-base-code", trust_remote_code=True)
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
            
            module_lines = []
            for i, line in enumerate(lines):
                in_func_or_class = any(start <= i < end for start, end in all_ranges)
                if not in_func_or_class and line.strip():
                    module_lines.append(i)

            if module_lines:
                step = max_module_lines
                for chunk_start_idx in range(0, len(module_lines), step):
                    chunk_indices = module_lines[chunk_start_idx:chunk_start_idx + step]
                    start_line = chunk_indices[0]
                    end_line = chunk_indices[-1] + 1
                    
                    chunk = self._cap_text("\n".join(lines[start_line:end_line]))
                    metadata = {
                        "path": path,
                        "filename": filename,
                        "function_name": f"module_segment_{chunk_start_idx // step}",
                        "class_name": "module_level",
                        "chunk_type": "module",
                        "line_start": start_line + 1,
                        "line_end": end_line,
                    }
                    self.chunks.append([chunk, metadata])

    def push(self, batch_size: int = EMBED_BATCH_SIZE, on_progress=None):
        if not self.chunks:
            print("No chunks to push. Run build() first.")
            return

        documents = [self._cap_text(c[0]) for c in self.chunks]
        metadatas = [c[1] for c in self.chunks]
        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{m['path']}:{m['function_name']}:{m['line_start']}:{i}"))
            for i, m in enumerate(metadatas)
        ]

        total = len(documents)
        batch_size = max(1, min(batch_size, EMBED_BATCH_SIZE))
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            print(f"Pushing batch {start}-{end} / {total}...")

            self.client.add(
                collection_name=self.collection_name,
                documents=documents[start:end],
                metadata=metadatas[start:end],
                ids=ids[start:end],
            )

            if on_progress and total:
                on_progress(end / total, f"{end}/{total}")
        
        print(f"Successfully pushed {total} chunks to Qdrant!")

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
            # Qdrant already computed the scores, just use them!
            ranked = sorted(
                [(metadatas[i], scores[i], documents[i]) for i in range(len(documents))],
                key=lambda x: x[1],
                reverse=True
            )[:top_k]
            return ranked
        
        # Fallback to local SentenceTransformer if scores are not pre-computed
        query_embedding = self.embed_model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
        doc_embeddings = self.embed_model.encode(documents, convert_to_numpy=True, normalize_embeddings=True)
        
        computed_scores = np.dot(doc_embeddings, query_embedding)
        
        ranked = sorted(
            [(metadatas[i], computed_scores[i], documents[i]) for i in range(len(documents))],
            key=lambda x: x[1],
            reverse=True
        )[:top_k]
        
        return ranked