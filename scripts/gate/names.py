"""Crude missing-name check: every Name loaded is bound somewhere in the module or is a builtin."""
import ast, builtins, sys
bad = 0
for path in sys.argv[1:]:
    tree = ast.parse(open(path).read())
    bound = set(dir(builtins)) | {"__file__", "__name__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
            print(f"{path}:{node.lineno}: {node.id}"); bad += 1
print("names:", "clean" if not bad else f"{bad} unbound")
