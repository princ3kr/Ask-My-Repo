import networkx as nx
from pyvis.network import Network
from neo4j import GraphDatabase
import json
import os
from dotenv import load_dotenv

load_dotenv()

URI = os.getenv("NEO4J_URI")
USER = os.getenv("NEO4J_USER")
PASSWORD = os.getenv("NEO4J_PASS")


class ChunkBuilder:
    def __init__(self, files: dict, repo_id: str):
        self.repo_id = repo_id
        self.G = nx.DiGraph()
        self.files = files
        self.name_index = self.build_name_index()
        self.module_index = self.build_module_index()

    @staticmethod
    def repo_exists(repo_id: str) -> bool:
        driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
        try:
            with driver.session() as session:
                result = session.run(
                    "MATCH (r:Repo {repo_id: $repo_id}) RETURN count(r) AS count",
                    repo_id=repo_id,
                )
                record = result.single()
                return bool(record and record["count"] > 0)
        finally:
            driver.close()

    @staticmethod
    def get_repo_graph_counts(repo_id: str) -> tuple[int, int]:
        driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
        try:
            with driver.session() as session:
                nodes_result = session.run(
                    "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS count",
                    repo_id=repo_id,
                )
                edges_result = session.run(
                    "MATCH (n {repo_id: $repo_id})-[r]-() RETURN count(r) AS count",
                    repo_id=repo_id,
                )
                nodes = nodes_result.single()
                edges = edges_result.single()
                return (
                    int(nodes["count"]) if nodes else 0,
                    int(edges["count"]) if edges else 0,
                )
        finally:
            driver.close()

    def get_node_style(self, filepath: str) -> dict:
        """Dynamically assigns colours based on top-level directory — matches BuildGraph.get_node_style()"""
        parts = filepath.split("/")
        top_level = parts[0] if len(parts) > 1 else "root"

        colours = [
            {"background": "#3b82f6", "border": "#60a5fa", "highlight": "#93c5fd"},
            {"background": "#10b981", "border": "#34d399", "highlight": "#6ee7b7"},
            {"background": "#f59e0b", "border": "#fbbf24", "highlight": "#fcd34d"},
            {"background": "#8b5cf6", "border": "#a78bfa", "highlight": "#c4b5fd"},
            {"background": "#ec4899", "border": "#f472b6", "highlight": "#fbcfe8"},
            {"background": "#ef4444", "border": "#f87171", "highlight": "#fca5a5"},
        ]

        colour_idx = hash(top_level) % len(colours)
        return colours[colour_idx]

    def build(self):
        """Populate the NetworkX dependency graph — must be called before push_to_neo4j() or show()."""
        cleared_path = self.get_path()
        for source, targets in cleared_path.items():
            style = self.get_node_style(source)
            self.G.add_node(
                source,
                label=source.split("/")[-1],
                title=source,
                color=style,
                shadow=True,
                shape="dot",
                size=25,
                font={"color": "white", "size": 14, "face": "Segoe UI, Tahoma, Geneva, Verdana, sans-serif"}
            )
            for target in targets:
                self.G.add_edge(source, target)

    def build_name_index(self) -> dict:
        name_index = {}
        for filepath in self.files:
            for classname in self.files.get(filepath, {}).get("classes", []):
                name_index[classname['name']] = filepath
            for funcname in self.files.get(filepath, {}).get("functions", []):
                name_index[funcname['name']] = filepath
        return name_index

    def build_module_index(self) -> dict:
        """Creates an index mapping python module paths (e.g. 'utils.helpers') to file paths"""
        module_index = {}
        for filepath in self.files:
            if filepath.endswith(".py"):
                # Convert 'src/utils/helpers.py' -> 'src.utils.helpers' and 'utils.helpers'
                parts = filepath[:-3].split("/")
                for i in range(len(parts)):
                    module_name = ".".join(parts[i:])
                    module_index[module_name] = filepath
        return module_index

    def get_path(self) -> dict:
        cleared_files = {}
        for filepath in self.files:
            imports = []
            modules = self.files[filepath].get("import_modules", [])
            names = self.files[filepath].get("import_names", [])

            for imp, imp_names in zip(modules, names):
                if not imp:
                    continue

                if imp in self.module_index:
                    imports.append(self.module_index[imp])
                else:
                    for name in imp_names:
                        if name in self.name_index:
                            imports.append(self.name_index[name])
                            
            cleared_files[filepath] = list(set(imports))
            
        return cleared_files

    def _code_indexes(self):
        class_index, function_index, method_index, caller_class = {}, {}, {}, {}
        for path, data in self.files.items():
            for cls in data.get("classes", []):
                class_index.setdefault(cls["name"], cls["qualified_name"])
            for fn in data.get("functions", []):
                function_index.setdefault(fn["name"], fn["qualified_name"])
                function_index[fn["qualified_name"]] = fn["qualified_name"]
                if fn.get("class_name"):
                    method_index[(fn["class_name"], fn["name"])] = fn["qualified_name"]
                    caller_class[fn["qualified_name"]] = fn["class_name"]
        return class_index, function_index, method_index, caller_class

    def _resolve_call(self, caller, callee, class_index, function_index, method_index, caller_class):
        # Return BOTH type and full qualified name
        # Check direct qualified name match
        if callee in function_index:
            return "function", function_index[callee]  # Already qualified
        if callee in class_index:
            return "class", class_index[callee]  # Already qualified
        
        # Check self.method() calls
        current_class = caller_class.get(caller)
        if current_class and callee.startswith("self."):
            method_name = callee.split(".")[-1]
            target = method_index.get((current_class, method_name))
            if target:
                return "function", target  # Qualified from method_index
        
        # Check short name (just the function name, not qualified)
        short_name = callee.split(".")[-1]
        if short_name in function_index:
            # Return the QUALIFIED name, not short name
            return "function", function_index[short_name]
        if short_name in class_index:
            return "class", class_index[short_name]
        
        # External call
        return "external", callee

    def push_to_neo4j(self):
        driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
        try:
            class_index, function_index, method_index, caller_class = self._code_indexes()
            repo_id = self.repo_id
            with driver.session() as session:
                with session.begin_transaction() as tx:
                    for path, data in self.files.items():
                        imports = [imp.get("name") for imp in data.get("imports", []) if imp.get("name")]
                        tx.run('''
                            MERGE (r:Repo {repo_id: $repo_id})
                            MERGE (f:File {path: $path, repo_id: $repo_id})
                            MERGE (r)-[:CONTAINS]->(f)
                            SET f.name = $name,
                                f.classes = $classes,
                                f.imports = $imports,
                                f.functions = $functions
                        ''', repo_id=repo_id, path=path, name=path.split('/')[-1],
                             classes=[c['name'] for c in data.get("classes", [])],
                             imports=imports,
                             functions=[f['name'] for f in data.get("functions", [])])

                        for cls in data.get("classes", []):
                            tx.run('''
                                MATCH (f:File {path: $path, repo_id: $repo_id})
                                MERGE (c:Class {qualified_name: $qualified_name, repo_id: $repo_id})
                                SET c.name = $name, c.path = $path, c.bases = $bases,
                                    c.line_start = $line_start, c.line_end = $line_end
                                MERGE (f)-[:DEFINES_CLASS]->(c)
                            ''', repo_id=repo_id, path=path, **cls)

                        for fn in data.get("functions", []):
                            tx.run('''
                                MATCH (f:File {path: $path, repo_id: $repo_id})
                                MERGE (fn:Function {qualified_name: $qualified_name, repo_id: $repo_id})
                                SET fn.name = $name, fn.path = $path, fn.class_name = $class_name,
                                    fn.line_start = $line_start, fn.line_end = $line_end
                                WITH f, fn
                                OPTIONAL MATCH (c:Class {name: $class_name, path: $path, repo_id: $repo_id})
                                FOREACH (_ IN CASE WHEN c IS NULL THEN [1] ELSE [] END | MERGE (f)-[:DEFINES_FUNCTION]->(fn))
                                FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (c)-[:HAS_METHOD]->(fn))
                            ''', repo_id=repo_id, path=path, **fn)

                        for imp in data.get("imports", []):
                            tx.run('''
                                MATCH (f:File {path: $path, repo_id: $repo_id})
                                MERGE (i:Import {path: $path, name: $name, module: $module, repo_id: $repo_id})
                                SET i.alias = $alias, i.line = $line
                                MERGE (f)-[:IMPORTS_SYMBOL]->(i)
                            ''', repo_id=repo_id, path=path, **imp)

                    for source, target in self.G.edges():
                        tx.run('''
                            MATCH (a:File {path: $source, repo_id: $repo_id})
                            MATCH (b:File {path: $target, repo_id: $repo_id})
                            MERGE (a)-[:IMPORTS]->(b)
                        ''', source=source, target=target, repo_id=repo_id)

                    for path, data in self.files.items():
                        for rel in data.get("inheritance", []):
                            base_type, target = ("class", class_index[rel["base"]]) if rel["base"] in class_index else ("external", rel["base"])
                            if base_type == "class":
                                tx.run('''
                                    MATCH (c:Class {qualified_name: $class_qname, repo_id: $repo_id})
                                    MATCH (base:Class {qualified_name: $base_qname, repo_id: $repo_id})
                                    MERGE (c)-[:INHERITS_FROM {line: $line}]->(base)
                                ''', repo_id=repo_id, class_qname=rel["qualified_name"], base_qname=target, line=rel["line"])
                            else:
                                tx.run('''
                                    MATCH (c:Class {qualified_name: $class_qname, repo_id: $repo_id})
                                    MERGE (ext:ExternalSymbol {name: $base_name, repo_id: $repo_id})
                                    MERGE (c)-[:INHERITS_EXTERNAL {line: $line}]->(ext)
                                ''', repo_id=repo_id, class_qname=rel["qualified_name"], base_name=target, line=rel["line"])

                        for call in data.get("calls", []):
                            target_type, target = self._resolve_call(call["caller"], call["callee"], class_index, function_index, method_index, caller_class)
                            if target_type == "function":
                                tx.run('''
                                    MATCH (caller:Function {qualified_name: $caller, repo_id: $repo_id})
                                    MATCH (callee:Function {qualified_name: $callee, repo_id: $repo_id})
                                    MERGE (caller)-[:CALLS {line: $line}]->(callee)
                                ''', repo_id=repo_id, caller=call["caller"], callee=target, line=call["line"])
                            elif target_type == "class":
                                tx.run('''
                                    MATCH (caller:Function {qualified_name: $caller, repo_id: $repo_id})
                                    MATCH (callee:Class {qualified_name: $callee, repo_id: $repo_id})
                                    MERGE (caller)-[:INSTANTIATES {line: $line}]->(callee)
                                ''', repo_id=repo_id, caller=call["caller"], callee=target, line=call["line"])
                            else:
                                tx.run('''
                                    MATCH (caller:Function {qualified_name: $caller, repo_id: $repo_id})
                                    MERGE (ext:ExternalSymbol {name: $callee, repo_id: $repo_id})
                                    MERGE (caller)-[:CALLS_EXTERNAL {line: $line}]->(ext)
                                ''', repo_id=repo_id, caller=call["caller"], callee=target, line=call["line"])
        finally:
            driver.close()
                
    def show(self, filename="graph.html"):
        net = Network(
            directed=True, notebook=True, cdn_resources='remote', 
            height="750px", width="100%", bgcolor="#111827", font_color="white"
        )
        net.from_nx(self.G)
        options = {
            "edges": {"color": {"inherit": "from", "opacity": 0.5}, "smooth": {"type": "continuous", "roundness": 0.5}, "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}}, "width": 1.5},
            "physics": {"forceAtlas2Based": {"gravitationalConstant": -60, "centralGravity": 0.005, "springLength": 150, "springStrength": 0.08, "damping": 0.4, "avoidOverlap": 0.5}, "maxVelocity": 50, "minVelocity": 0.1, "solver": "forceAtlas2Based", "stabilization": {"enabled": True, "iterations": 1000}},
            "interaction": {"hover": True, "navigationButtons": True, "multiselect": True, "tooltipDelay": 200}
        }
        net.set_options(json.dumps(options))
        return net.write_html(filename)
