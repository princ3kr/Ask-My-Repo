from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from neo4j import GraphDatabase
from typing import Optional
from dotenv import load_dotenv
from fuzzywuzzy import process, fuzz
import re
import time
import os

load_dotenv()

class CypherQuery(BaseModel):
    cypher: str = Field(..., description="Cypher query for retrieving relationships within the nodes")

VALID_FILE_PROPERTIES = {
    'name', 'path', 'functions', 'classes', 'imports', 'repo_id', 
    'qualified_name', 'class_name', 'bases', 'module', 'alias', 'line', 
    'line_start', 'line_end'
}

TEMPLATES = {
    "multi_hop_imports": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH (source:File {{name: '{source}'}})-[:IMPORTS]->(via:File {{name: '{via}'}})
        MATCH (via:File)-[:IMPORTS]->(target:File)
        RETURN DISTINCT target.path as indirect_dependency
        ORDER BY target.path
    """,
    
    "leaf_nodes": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
        WHERE NOT (f)-[:IMPORTS]->()
        RETURN f.path as leaf_file
        ORDER BY f.path
    """,
    
    "most_dependencies": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
        MATCH (f)-[:IMPORTS]->(deps:File)
        WITH f, count(DISTINCT deps) as dep_count
        ORDER BY dep_count DESC
        LIMIT 5
        RETURN f.path as file, dep_count as dependency_count
    """,
    
    "direct_imports": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH (f:File {{name: '{filename}'}})-[:IMPORTS]->(imported:File)
        RETURN imported.path as imported_file
        ORDER BY imported.path
    """,
    
    "reverse_lookup": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH (importer:File)-[:IMPORTS]->(target:File {{name: '{filename}'}})
        RETURN importer.path as file_that_imports
        ORDER BY importer.path
    """,
    
    "transitive_dependencies": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH path=(source:File {{name: '{source}'}})-[:IMPORTS*1..3]->(target:File)
        WHERE source <> target
        RETURN DISTINCT target.path as transitive_dep
        ORDER BY target.path
    """,
    
    "file_structure": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File {{name: '{filename}'}})
        MATCH (f)-[:DEFINES_CLASS]->(c:Class)
        MATCH (f)-[:DEFINES_FUNCTION]->(fn:Function)
        RETURN f.path as file_path, 
            collect(DISTINCT c.name) as classes, 
            collect(DISTINCT fn.name) as functions
    """,
    
    "call_graph": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH (caller:Function {{name: '{function_name}'}})-[:CALLS]->(callee:Function)
        RETURN callee.qualified_name as called_function
        ORDER BY called_function
    """,
    
    "class_hierarchy": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})
        MATCH (c:Class {{name: '{class_name}'}})-[:INHERITS_FROM]->(parent:Class)
        RETURN parent.qualified_name as parent_class
        ORDER BY parent_class
    """,
    
    "all_files": """
        MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
        RETURN f.path as file_path
        ORDER BY f.path
    """
}

URI = os.getenv("NEO4j_URI")
USER = os.getenv("NEO4j_USER")
PASSWORD = os.getenv("NEO4j_PASS")


class GraphAgent:
    def __init__(self, llm: ChatOpenAI, repo_id: str):
        self.repo_id = repo_id
        self.llm = llm
        self.driver = self.initilize_driver(URI, USER, PASSWORD)
        self._file_index = None
        self._graph_cache: dict[str, dict | None] = {}
        self.min_result_count = 1
        self.max_result_variance = 0.5      

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initilize_driver(self, uri, user, password):
        driver = GraphDatabase.driver(uri, auth=(user, password))
        print("Driver initialization successful.")
        return driver

    def close(self):
        self.driver.close()
        print("Driver closed.")

    def _load_file_index(self) -> list[dict]:
        if self._file_index is not None:
            return self._file_index

        with self.driver.session() as session:
            result = session.run(f"""
                MATCH (r:Repo {{repo_id: '{self.repo_id}'}})-[:CONTAINS]->(f:File)
                OPTIONAL MATCH (f)-[:DEFINES_CLASS]->(c:Class)
                OPTIONAL MATCH (f)-[:DEFINES_FUNCTION]->(fn:Function)
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Function)
                RETURN f.name as name, f.path as path,
                       collect(DISTINCT c.name) as classes,
                       collect(DISTINCT fn.name) + collect(DISTINCT m.name) as functions
            """)
            self._file_index = [dict(r) for r in result]

        return self._file_index

    def _match_template(self, query: str) -> Optional[tuple[str, dict]]:
        """
        Pattern match query against known templates.
        Returns: (template_name, extracted_params) or None
        """
        # Multi-hop
        match = re.search(
            r'(?:which\s+)?files?\s+(?:does?\s+)?(\w+\.py)\s+(?:indirectly\s+)?depend(?:s|ency)?\s+on\s+through\s+(\w+\.py)',
            query.lower()
        )
        if match:
            return ("multi_hop_imports", {
                "repo_id": self.repo_id,
                "source": match.group(1),
                "via": match.group(2)
            })
        
        # Leaf nodes
        if re.search(r'leaf\s+node|no\s+import|do\s+not\s+import\s+any', query.lower()):
            return ("leaf_nodes", {"repo_id": self.repo_id})
        
        # Most dependencies
        if re.search(r'most\s+dependen|highest\s+dependen|most\s+import', query.lower()):
            return ("most_dependencies", {"repo_id": self.repo_id})
        
        # Direct imports
        match = re.search(r'(?:what\s+(?:does|do)\s+)?(\w+\.py)\s+import', query.lower())
        if match:
            return ("direct_imports", {
                "repo_id": self.repo_id,
                "filename": match.group(1)
            })
        
        # Reverse lookup
        match = re.search(r'(?:which|what)\s+files?\s+import\s+(\w+\.py)', query.lower())
        if match:
            return ("reverse_lookup", {
                "repo_id": self.repo_id,
                "filename": match.group(1)
            })
        
        # Transitive
        match = re.search(r'transitive\s+dependenc(?:y|ies).*?(\w+\.py)', query.lower())
        if match:
            return ("transitive_dependencies", {
                "repo_id": self.repo_id,
                "source": match.group(1)
            })
        
        # File structure
        match = re.search(r'(?:structure|content|what\s+is\s+in)\s+(\w+\.py)', query.lower())
        if match:
            return ("file_structure", {
                "repo_id": self.repo_id,
                "filename": match.group(1)
            })
        
        # Call graph
        match = re.search(r'(?:what\s+(?:does|do)\s+)?(?:function\s+)?(\w+)\s+call', query.lower())
        if match:
            return ("call_graph", {
                "repo_id": self.repo_id,
                "function_name": match.group(1)
            })
        
        # Class hierarchy
        match = re.search(r'(?:what\s+(?:does|do)\s+)?(?:class\s+)?(\w+)\s+inherit', query.lower())
        if match:
            return ("class_hierarchy", {
                "repo_id": self.repo_id,
                "class_name": match.group(1)
            })
        
        return None
    
    def resolve_names_in_cypher(self, cypher: str) -> str:
        def replacer(match):
            raw = match.group(1)
            resolved = self.resolve_filename(raw)
            if resolved and resolved != raw:
                print(f"[resolve] '{raw}' → '{resolved}'")
                return match.group(0).replace(raw, resolved)
            return match.group(0)

        pattern = r"\{(?:name|source|via):\s*['\"]([^'\"]+)['\"]\}"
        return re.sub(pattern, replacer, cypher)
    
    def sanitize_cypher(self, cypher: str) -> str:
        # Remove string literals to avoid false matches inside quotes
        cypher_clean = re.sub(r"'[^']*'|\"[^\"]*\"", "''", cypher)

        # Match property accesses: word.property (not ::, ->, etc.)
        used_props = set(re.findall(r'\b\w+\.(\w+)\b', cypher_clean))
        
        # These are valid Cypher keywords that look like properties — exclude them
        cypher_keywords = {'path', 'type', 'keys', 'labels', 'id', 'properties'}
        
        invalid = used_props - VALID_FILE_PROPERTIES - cypher_keywords
        if invalid:
            print(f"[Sanitize] Blocked invalid aproperties: {invalid}")
            return None
        return cypher
    
    def extract_entities_from_query(self, query: str) -> list[str]:
        index = self._load_file_index()
        entity_map: dict[str, str] = {}
        
        for f in index:
            entity_map[f["name"]] = f["path"]
            for cls in (f.get("classes") or []):
                entity_map[cls] = f["path"]
            for fn in (f.get("functions") or []):
                entity_map[fn] = f["path"]

        if not entity_map:
            return []

        words = query.replace("'s", "").split()
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        candidates = words + bigrams

        matched_paths = []
        for candidate in candidates:
            match = process.extractOne(
                candidate,
                list(entity_map.keys()),
                scorer=fuzz.token_sort_ratio
            )
            if match and match[1] >= 75:
                matched_paths.append(entity_map[match[0]])

        return list(set(matched_paths))

    def _execute_template(self, template_name: str, params: dict) -> dict | None:
        """Execute a template query safely"""
        try:
            cypher = TEMPLATES[template_name].format(**params)
            
            with self.driver.session() as session:
                result = session.run(cypher)
                data = [record.data() for record in result]
            
            return {
                "is_fallback": False,
                "data": data,
                "response": cypher,
                "method": "template",
                "template_name": template_name,
                "timestamp": time.time()
            }
        except Exception as e:
            print(f"[Template Error] {template_name}: {str(e)}")
            return None
        

    def graph_search(self, query: str) -> dict | None:
        """
        Enhanced graph search with better Cypher generation prompt
        """
        if query in self._graph_cache:
            return self._graph_cache[query]
        
        # Try template first
        template_match = self._match_template(query)
        if template_match:
            template_name, params = template_match
            result = self._execute_template(template_name, params)
            if result:
                self._graph_cache[query] = result
                return result
        
        structured_llm = self.llm.with_structured_output(CypherQuery)
        
        index = self._load_file_index()
        sample = index[:15]
        file_list = "\n".join(f"- {f['name']} ({f['path']})" for f in sample)
        
        system_content = f"""You are a Cypher expert for Neo4j. Generate ONLY valid, working queries.
            CRITICAL RULES:
            1. ALWAYS start with: MATCH (r:Repo {{repo_id: '{self.repo_id}'}})
            2. NEVER use properties not in VALID_PROPERTIES list
            3. For multi-hop queries (A -> B -> C), use proper MATCH chains with intermediate variables
            4. ALWAYS use DISTINCT if returning multiple instances
            5. ALWAYS ORDER BY results for consistency
            6. For "files that depend on X", use reverse relationship direction
            7. For "transitive" queries, use variable-length patterns: -[:IMPORTS*1..3]->
            8. Return DISTINCT paths to avoid duplicates
            9. Always filter by repo_id to scope queries
            10. If unsure about relationship direction, return sentinel query

            SCHEMA:
            Nodes:
            - Repo(repo_id)
            - File(name, path, functions, classes, imports, repo_id)
            - Class(name, qualified_name, path, bases, line_start, line_end, repo_id)
            - Function(name, qualified_name, path, class_name, line_start, line_end, repo_id)
            - Import(name, module, alias, path, line, repo_id)
            - ExternalSymbol(name, repo_id)

            Edges (all have repo_id):
            - (Repo)-[:CONTAINS]->(File)
            - (File)-[:IMPORTS]->(File)
            - (File)-[:IMPORTS_SYMBOL]->(Import)
            - (File)-[:DEFINES_CLASS]->(Class)
            - (File)-[:DEFINES_FUNCTION]->(Function)
            - (Class)-[:HAS_METHOD]->(Function)
            - (Class)-[:INHERITS_FROM]->(Class)
            - (Class)-[:INHERITS_EXTERNAL]->(ExternalSymbol)
            - (Function)-[:CALLS]->(Function)
            - (Function)-[:INSTANTIATES]->(Class)
            - (Function)-[:CALLS_EXTERNAL]->(ExternalSymbol)

            VALID PROPERTIES: {', '.join(sorted(VALID_FILE_PROPERTIES))}

            SAMPLE FILES IN REPO:
            {file_list}

            EXAMPLES OF CORRECT QUERIES:
            Example 1: Multi-hop (A -> B -> C)
            Query: "What does X import transitively?"
            Example 2: Leaf nodes (no outgoing edges)
            Query: "Which files have no imports?"
            Example 3: Reverse lookup (who imports X)
            Query: "Which files import X?"
            Example 4: Variable-length (transitive)
            Query: "Transitive dependencies of X?"

            IMPORTANT:
            - For behavioral questions (what does code do), return sentinel immediately
            - Never hardcode file names except when explicitly mentioned in question
            - Use DISTINCT to avoid duplicates
            - Filter by repo_id in first MATCH
            - Return file paths, not File nodes themselves
            """
        
        response = structured_llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=f"Generate Cypher for: {query}")
        ])
        
        try:
            safe_cypher = self.sanitize_cypher(response.cypher)
            if not safe_cypher:
                print(f"[Warning] Sanitization failed for query: {query}")
                return None
            
            safe_cypher = self.resolve_names_in_cypher(safe_cypher)
            
            with self.driver.session() as session:
                result = session.run(safe_cypher)
                data = [record.data() for record in result]
            
            is_fallback = (
                len(data) == 1 and 
                len(data[0]) == 1 and 
                "f.path" in data[0]
            )
            
            output = {
                "is_fallback": is_fallback,
                "data": data,
                "response": response.cypher,
                "method": "llm",
                "timestamp": time.time()
            }
            
            self._graph_cache[query] = output
            return output
            
        except Exception as e:
            print(f"[Error] Graph query failed:")
            print(f"  Query: {query}")
            print(f"  Generated Cypher: {response.cypher}")
            print(f"  Exception: {type(e).__name__}: {str(e)}")
            return None
        

    def _validate_graph_result(self, result: dict | None, query: str) -> dict | None:
        """
        Validate graph results for completeness and accuracy.
        Flag incomplete or suspicious results.
        """
        if result is None:
            return None
        
        data = result.get("data", [])
        
        # Empty result
        if not data:
            print(f"[Validation] Empty result for query: {query}")
            return result
        
        # Single result for multi-hop query
        if len(data) == 1 and any(
            kw in query.lower() 
            for kw in ["indirectly", "transitive", "through"]
        ):
            print(f"[Validation Warning] Single result for multi-hop query: {query}")
            print(f"  Result: {data}")
            result["confidence"] = 0.3  # Low confidence
            return result
        
        # Result variance (check if results look complete)
        if len(data) > 1:
            # For imports/dependencies, expect multiple results
            result["confidence"] = min(1.0, len(data) / 5.0)  # Assume 5+ is good
        else:
            result["confidence"] = 0.5
        
        # Extract file count
        file_count = 0
        for record in data:
            for k, v in record.items():
                if isinstance(v, str) and v.endswith(".py"):
                    file_count += 1
                elif isinstance(v, list):
                    file_count += len([x for x in v if isinstance(x, str) and x.endswith(".py")])
        
        result["extracted_file_count"] = file_count
        
        # Flag if result seems incomplete
        if file_count < self.min_result_count:
            print(f"[Validation Warning] Low file count: {file_count}")
            result["is_incomplete"] = True
        else:
            result["is_incomplete"] = False
        
        return result
    

    def _extract_filenames_safe(self, graph_result: dict | None, query: str) -> list[str]:
        """
        Safely extract filenames with validation
        """
        if not graph_result:
            return self.extract_entities_from_query(query)
        
        # Validate first
        graph_result = self._validate_graph_result(graph_result, query)
        
        if not self._is_meaningful(graph_result):
            print(f"[Fallback] Graph result not meaningful, using entity extraction")
            return self.extract_entities_from_query(query)
        
        filenames = self._extract_filenames(graph_result["data"])
        
        # If validation flagged as incomplete, augment with entity extraction
        if graph_result.get("is_incomplete"):
            print(f"[Augmentation] Graph result incomplete, augmenting with entity extraction")
            entity_files = self.extract_entities_from_query(query)
            filenames = list(set(filenames) | set(entity_files))
        
        if not filenames:
            print(f"[Fallback] No filenames extracted, using entity extraction")
            return self.extract_entities_from_query(query)
        
        print(f"[Validation] Extracted {len(filenames)} files from graph: {filenames[:3]}...")
        return filenames
    
    def _extract_filenames(self, graph_data: list[dict]) -> list[str]:
        paths = []
        for record in graph_data:
            for v in record.values():
                if isinstance(v, str) and v.endswith(".py"):
                    paths.append(v)
                elif isinstance(v, list):
                    paths.extend(x for x in v if isinstance(x, str) and x.endswith(".py"))
        return list(set(paths))

    def _resolve_filenames(self, graph_result: dict | None, query: str) -> list[str]:
        """Updated to use validation"""
        if graph_result and self._is_meaningful(graph_result):
            filenames = self._extract_filenames(graph_result["data"])
            if filenames:
                return filenames

        print("[fallback] Graph returned no meaningful data, using entity extraction")
        return self.extract_entities_from_query(query)
    
    def resolve_filename(self, raw_name: str) -> str | None:
        index = self._load_file_index()
        names = [f["name"] for f in index]

        if raw_name in names:
            return raw_name

        match = process.extractOne(raw_name, names, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= 70:
            return match[0]

        return None
    
    def _is_meaningful(self, graph_result: dict) -> bool:
        if graph_result is None:
            return False
        return not graph_result.get("is_fallback", False) and bool(graph_result.get("data"))