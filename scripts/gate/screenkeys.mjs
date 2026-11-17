// What the office app and the station tablet send to each write, from oxc's syntax tree (rolldown's parseAst).
//
//   node scripts/gate/screenkeys.mjs <tree>
//
// Prints one JSON line a write: {file, line, shape, endpoint, action, url, keys, open}. `keys` are the body's
// keys; `open` says what could not be read, and screenkeys.py asks for each such write to have been read by
// hand. A body is read through an object literal, a conditional spread, an action's `fields` and `body`, a
// variable set from ask() or useState() or assigned an object in the same function, and `values`.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const TREE = path.resolve(process.argv[2] || ".");
const FRONTEND = path.join(TREE, "frontend");
const require = createRequire(path.join(FRONTEND, "package.json"));
const { parseAst } = await import(pathToFileURL(require.resolve("rolldown/parseAst")).href);

const files = [];
(function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) walk(full);
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.|__tests__|setupTests|test-utils/.test(full)) files.push(full);
  }
})(path.join(FRONTEND, "src"));

const lineOf = (text, offset) => text.slice(0, offset).split("\n").length;

function walkNode(node, visit, parents = []) {
  if (!node || typeof node.type !== "string") return;
  visit(node, parents);
  for (const key of Object.keys(node)) {
    const value = node[key];
    if (Array.isArray(value)) {
      for (const v of value) if (v && typeof v.type === "string") walkNode(v, visit, [...parents, node]);
    } else if (value && typeof value.type === "string") walkNode(value, visit, [...parents, node]);
  }
}

function keyName(prop) {
  if (prop.computed) return null;
  if (prop.key.type === "Identifier") return prop.key.name;
  if (prop.key.type === "Literal") return String(prop.key.value);
  return null;
}

const props = (node) => Object.fromEntries(node.properties.filter((p) => p.type === "Property").map((p) => [keyName(p), p.value]));

// The `key`s of the field definitions under a node: {key: "memo", label: ...}.
function fieldKeys(node) {
  const keys = [];
  walkNode(node, (n) => {
    if (n.type !== "ObjectExpression") return;
    const own = props(n);
    if (own.key && own.key.type === "Literal" && own.label) keys.push(own.key.value);
  });
  return keys;
}

const FUNCTION = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"]);

// What a variable named `name` holds as a body, read where it is set in the functions around `parents`.
function variableKeys(name, parents, keys, open) {
  const scopes = parents.filter((p) => FUNCTION.has(p.type)).reverse();
  for (const scope of scopes) {
    let found = false;
    walkNode(scope.body, (n) => {
      if (n.type === "VariableDeclarator") {
        if (n.id.type === "Identifier" && n.id.name === name && n.init) { found = true; heldKeys(n.init, keys, open); }
        if (n.id.type === "ArrayPattern" && n.id.elements[0] && n.id.elements[0].name === name && n.init
            && n.init.type === "CallExpression" && n.init.callee.name === "useState") {
          found = true;
          heldKeys(n.init.arguments[0], keys, open);
          const setter = n.id.elements[1] && n.id.elements[1].name;
          if (setter) walkNode(scope.body, (c) => {
            if (c.type === "CallExpression" && c.callee.type === "Identifier" && c.callee.name === setter && c.arguments[0])
              heldKeys(c.arguments[0], keys, [], true);
          });
        }
      }
      if (n.type === "AssignmentExpression" && n.left.type === "Identifier" && n.left.name === name) {
        found = true; heldKeys(n.right, keys, open);
      }
      if (n.type === "AssignmentExpression" && n.left.type === "MemberExpression" && n.left.object.type === "Identifier"
          && n.left.object.name === name && !n.left.computed) {
        found = true; keys.add(n.left.property.name);
      }
    });
    if (found) return;
  }
  for (const scope of scopes) {
    const position = scope.params.findIndex((p) => p.type === "Identifier" && p.name === name
      || p.type === "AssignmentPattern" && p.left.name === name);
    if (position < 0) continue;
    const named = scope.id ? scope.id.name : null;
    const holder = parents[parents.indexOf(scope) - 1];
    const fname = named || (holder && holder.type === "VariableDeclarator" && holder.id.type === "Identifier" ? holder.id.name : null);
    if (!fname) break;
    let calls = 0;
    walkNode(CURRENT.ast, (c, cparents) => {
      if (c.type === "CallExpression" && c.callee.type === "Identifier" && c.callee.name === fname) {
        calls += 1;
        if (c.arguments[position]) objectKeys(c.arguments[position], keys, open, null, cparents);
      }
    });
    if (calls) return;
    break;
  }
  open.push(`variable ${name}`);
}

const CURRENT = { ast: null };
const SUBST = new Map();  // a helper's parameter -> {node, parents}: the argument one call of it passed

function heldKeys(node, keys, open, quiet = false) {
  if (!node) return;
  if (node.type === "AwaitExpression") node = node.argument;
  if (node.type === "CallExpression" && node.callee.type === "Identifier" && node.callee.name === "ask" && node.arguments[0]) {
    fieldKeys(node.arguments[0]).forEach((k) => keys.add(k));
    return;
  }
  if (node.type === "ArrowFunctionExpression") {  // setValue((current) => ({...current, key: v}))
    return heldKeys(node.body.type === "BlockStatement" ? null : node.body, keys, open, quiet);
  }
  const lost = [];
  objectKeys(node, keys, lost, null, []);
  if (!quiet) open.push(...lost.filter((l) => !/^variable (current|value|values|prev|previous|v)$/.test(l)));
}

// The keys an expression's object carries; anything that cannot be read goes to `open`.
function objectKeys(node, keys, open, fieldsKeys, parents) {
  if (!node) return;
  switch (node.type) {
    case "ParenthesizedExpression": case "TSAsExpression": case "TSSatisfiesExpression": case "TSNonNullExpression":
      return objectKeys(node.expression, keys, open, fieldsKeys, parents);
    case "ObjectExpression":
      for (const prop of node.properties) {
        if (prop.type === "SpreadElement") objectKeys(prop.argument, keys, open, fieldsKeys, parents);
        else { const name = keyName(prop); if (name) keys.add(name); else open.push("computed key"); }
      }
      return;
    case "ConditionalExpression": {
      const decided = decide(node.test);  // verb === "reject" ? {reason} : {}, verb given by the call
      if (decided !== true) objectKeys(node.alternate, keys, open, fieldsKeys, parents);
      if (decided !== false) objectKeys(node.consequent, keys, open, fieldsKeys, parents);
      return;
    }
    case "LogicalExpression":  // a && {x}: the object; a || {}, a ?? {}: either side
      if (node.operator !== "&&") objectKeys(node.left, keys, open, fieldsKeys, parents);
      return objectKeys(node.right, keys, open, fieldsKeys, parents);
    case "ArrowFunctionExpression":
      if (node.body.type !== "BlockStatement") return objectKeys(node.body, keys, open, fieldsKeys, [...parents, node]);
      walkNode(node.body, (n) => { if (n.type === "ReturnStatement") objectKeys(n.argument, keys, open, fieldsKeys, [...parents, node]); });
      return;
    case "Identifier":
      if (node.name === "undefined") return;
      if (SUBST.has(node.name)) {
        const given = SUBST.get(node.name);
        SUBST.delete(node.name);  // an argument is read where it was written, once
        try { return objectKeys(given.node, keys, open, fieldsKeys, given.parents); }
        finally { SUBST.set(node.name, given); }
      }
      if ((node.name === "values" || node.name === "_values") && fieldsKeys) { fieldsKeys.forEach((k) => keys.add(k)); return; }
      return variableKeys(node.name, parents, keys, open);
    default:
      open.push(node.type);
  }
}

// x === "lit" or x !== "lit", x a parameter one call gave a literal: true or false; otherwise undefined.
function decide(test) {
  if (test.type !== "BinaryExpression" || !["===", "!==", "==", "!="].includes(test.operator)) return undefined;
  const [named, literal] = test.left.type === "Identifier" ? [test.left, test.right] : [test.right, test.left];
  if (named.type !== "Identifier" || literal.type !== "Literal" || !SUBST.has(named.name)) return undefined;
  const given = SUBST.get(named.name).node;
  if (given.type !== "Literal") return undefined;
  const same = given.value === literal.value;
  return test.operator.startsWith("=") ? same : !same;
}

function lastSegment(text) {
  const parts = String(text).split("?")[0].split("/").filter(Boolean);
  const last = parts[parts.length - 1];
  return last && /^[a-z_-]+$/.test(last) ? last : null;
}

// The endpoint and action a URL names; $name when the action is a variable (a component's `path` prop).
// The /api/ literal `name` is set to in the functions around a node, else in the module.
function apiConst(name, parents, consts) {
  for (const scope of parents.filter((p) => FUNCTION.has(p.type)).reverse()) {
    let found = null;
    walkNode(scope.body, (d) => {
      if (!found && d.type === "VariableDeclarator" && d.id.type === "Identifier" && d.id.name === name && d.init
          && d.init.type === "Literal" && typeof d.init.value === "string" && d.init.value.startsWith("/api/")) found = d.init.value;
    });
    if (found) return found;
  }
  return consts[name] || null;
}

// A URL ending in a variable that is not an id names an action this cannot read: "?".
function urlParts(node, consts, text, parents = []) {
  if (node.type === "Literal") return { endpoint: node.value, action: null, url: node.value };
  if (node.type === "Identifier" && apiConst(node.name, parents, consts)) {
    const value = apiConst(node.name, parents, consts);
    return { endpoint: value, action: null, url: value };
  }
  if (node.type !== "TemplateLiteral") return { endpoint: null, action: "?" };
  const quasis = node.quasis.map((q) => q.value.cooked);
  let action = lastSegment(quasis[quasis.length - 1]);
  if (!action && node.expressions.length && /^\/?$/.test(quasis[quasis.length - 1])) {
    const expr = node.expressions[node.expressions.length - 1];
    const said = text.slice(expr.start, expr.end);
    if (/(^|[.!]|\b)(id|pk)\b|Id\b/.test(said)) action = null;  // ${ENDPOINT}${row.id}/: a record, not an action
    else action = expr.type === "Identifier" ? "$" + expr.name : "?";
  }
  let endpoint = quasis[0] || null;
  if (!endpoint && node.expressions[0] && node.expressions[0].type === "Identifier") {
    endpoint = apiConst(node.expressions[0].name, parents, consts) || "$" + node.expressions[0].name;
  }
  return { endpoint, action };
}

const rows = [];
const WRITES = ["POST", "PUT", "PATCH"];
for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const rel = path.relative(TREE, file);
  let ast;
  try { ast = parseAst(text, { lang: file.endsWith(".tsx") ? "tsx" : "ts" }); }
  catch (error) { rows.push({ file: rel, line: 0, shape: "unparsed", open: [String(error.message).slice(0, 200)] }); continue; }
  CURRENT.ast = ast;
  const consts = {};
  walkNode(ast, (n) => {
    if (n.type === "VariableDeclarator" && n.id.type === "Identifier" && n.init && n.init.type === "Literal"
        && typeof n.init.value === "string" && n.init.value.startsWith("/api/")) consts[n.id.name] = n.init.value;
  });
  const attributed = [];
  walkNode(ast, (n) => {
    if (n.type === "JSXAttribute" && n.name.name === "endpoint" && n.value && n.value.type === "Literal"
        && String(n.value.value).startsWith("/api/")) attributed.push(n.value.value);
  });
  const fileEndpoint = attributed[0] || Object.values(consts)[0] || null;
  walkNode(ast, (n, parents) => {
    // act.run("POST", url, body) and send("POST", url, body)
    if (n.type === "CallExpression") {
      const name = n.callee.type === "MemberExpression" ? n.callee.property.name : n.callee.type === "Identifier" ? n.callee.name : null;
      const method = n.arguments[0];
      if ((name === "run" || name === "send") && method && method.type === "Literal" && WRITES.includes(method.value) && n.arguments[1]) {
        const { endpoint, action, url } = urlParts(n.arguments[1], consts, text, parents);
        const helper = action && action.startsWith("$") ? helperOf(action.slice(1), parents) : null;
        if (helper) {
          for (const call of helper.calls) {
            helper.params.forEach((param, i) => SUBST.set(param, {
              node: call.node.arguments[i] || { type: "Identifier", name: "undefined" }, parents: call.parents }));
            const named = call.node.arguments[helper.position];
            const keys = new Set(), open = [];
            if (n.arguments[2]) objectKeys(n.arguments[2], keys, open, null, parents);
            SUBST.clear();
            rows.push({ file: rel, line: lineOf(text, call.node.start), shape: name + " via " + helper.name, endpoint,
                        action: named && named.type === "Literal" ? named.value : "?", keys: [...keys].sort(), open });
          }
          return;
        }
        const keys = new Set(), open = [];
        if (n.arguments[2]) objectKeys(n.arguments[2], keys, open, null, parents);
        rows.push({ file: rel, line: lineOf(text, n.start), shape: name, endpoint, action, url, keys: [...keys].sort(), open });
      }
    }
    // A request held to send later: {method?, path, body} (the offline scan queue, an undo's request)
    if (n.type === "ObjectExpression") {
      const own = props(n);
      if (own.path && own.body && own.path.type !== "Literal" && !own.url) {
        if (own.method && own.method.type === "Literal" && !WRITES.includes(own.method.value)) return;
        let where = own.path;
        if (where.type === "Identifier") {
          walkNode(ast, (d) => {
            if (d.type === "VariableDeclarator" && d.id.type === "Identifier" && d.id.name === own.path.name && d.init) where = d.init;
          });
        }
        const { endpoint, action, url } = urlParts(where, consts, text, parents);
        const keys = new Set(), open = [];
        objectKeys(own.body, keys, open, null, parents);
        rows.push({ file: rel, line: lineOf(text, n.start), shape: "held", endpoint, action, url, keys: [...keys].sort(), open });
        return;
      }
    }
    // An action a screen declares: {label, path: "x" | url: (row) => `...`, fields?, body?}
    if (n.type === "ObjectExpression") {
      const own = props(n);
      if (!own.label || !(own.path || own.url)) return;
      if (own.method && own.method.type === "Literal" && !WRITES.includes(own.method.value)) return;
      let action = null, endpoint = fileEndpoint;
      if (own.path && own.path.type === "Literal") action = own.path.value;
      else if (own.url && own.url.type === "ArrowFunctionExpression") {
        const parts = urlParts(own.url.body, consts, text, parents);
        action = parts.action; endpoint = parts.endpoint || endpoint;
      }
      if (!action || action.startsWith("/")) return;
      const fieldsKeys = own.fields ? fieldKeys(own.fields) : [];
      const keys = new Set(), open = [];
      if (own.fields && own.fields.type === "Identifier") variableFields(own.fields.name, ast, fieldsKeys, open);
      if (own.body) objectKeys(own.body, keys, open, fieldsKeys, [...parents, n]);
      else fieldsKeys.forEach((k) => keys.add(k));
      rows.push({ file: rel, line: lineOf(text, n.start), shape: "declared", endpoint, action, keys: [...keys].sort(), open });
    }
    // <Component path="x">: the component posts its own body to `${endpoint}${id}/${path}/`
    if (n.type === "JSXOpeningElement") {
      const attrs = Object.fromEntries(n.attributes.filter((a) => a.type === "JSXAttribute").map((a) => [a.name.name, a.value]));
      if (attrs.path && attrs.path.type === "Literal") {
        let endpoint = null;
        if (attrs.endpoint && attrs.endpoint.type === "Literal") endpoint = attrs.endpoint.value;
        if (attrs.endpoint && attrs.endpoint.type === "JSXExpressionContainer" && attrs.endpoint.expression.type === "Identifier") {
          endpoint = consts[attrs.endpoint.expression.name] || null;
        }
        rows.push({ file: rel, line: lineOf(text, n.start), shape: "component " + (n.name.name || "?"), endpoint,
                    action: attrs.path.value, keys: null, component: n.name.name || null, open: [] });
      }
    }
  });
}

// The named function around a call whose parameter `name` is the action, and every call of it in the file.
function helperOf(name, parents) {
  for (let i = parents.length - 1; i >= 0; i--) {
    const scope = parents[i];
    if (!FUNCTION.has(scope.type)) continue;
    const params = scope.params.map((p) => (p.type === "Identifier" ? p.name : p.type === "AssignmentPattern" ? p.left.name : null));
    const position = params.indexOf(name);
    if (position < 0) continue;
    const holder = parents[i - 1];
    const fname = scope.id ? scope.id.name
      : holder && holder.type === "VariableDeclarator" && holder.id.type === "Identifier" ? holder.id.name : null;
    if (!fname) return null;
    const calls = [];
    walkNode(CURRENT.ast, (c, cparents) => {
      if (c.type === "CallExpression" && c.callee.type === "Identifier" && c.callee.name === fname) calls.push({ node: c, parents: cparents });
    });
    return calls.length ? { name: fname, params, position, calls } : null;
  }
  return null;
}

// fields: SOME_FIELDS, a constant declared in this file or imported: its field keys.
function variableFields(name, ast, fieldsKeys, open) {
  let found = false;
  walkNode(ast, (n) => {
    if (n.type === "VariableDeclarator" && n.id.type === "Identifier" && n.id.name === name && n.init) {
      found = true; fieldKeys(n.init).forEach((k) => fieldsKeys.push(k));
    }
  });
  if (!found) {
    for (const file of files) {
      const text = fs.readFileSync(file, "utf8");
      if (!new RegExp(`export const ${name}\\b`).test(text)) continue;
      const other = parseAst(text, { lang: file.endsWith(".tsx") ? "tsx" : "ts" });
      walkNode(other, (n) => {
        if (n.type === "VariableDeclarator" && n.id.type === "Identifier" && n.id.name === name && n.init) {
          found = true; fieldKeys(n.init).forEach((k) => fieldsKeys.push(k));
        }
      });
    }
  }
  if (!found) open.push(`fields ${name}`);
}

// A component given a `path` posts through a URL ending in ${path}: its own call's body is the write's.
const byComponent = {};
for (const row of rows) {
  if (row.action && row.action.startsWith("$") && (row.shape === "run" || row.shape === "send")) {
    const component = path.basename(row.file).replace(/\.tsx?$/, "");
    (byComponent[component] ||= []).push(row);
  }
}
for (const row of rows) {
  if (!row.component) continue;
  const own = byComponent[row.component];
  if (!own || own.length !== 1) { row.open.push(`component ${row.component}: its post not found`); row.keys = []; continue; }
  row.keys = own[0].keys; row.open.push(...own[0].open);
  own[0].through = row.component;
}

// The station tablet: api("rolls/", {body: {...}}) against `const API = "/api/manufacturing/stations/<code>/"`.
const templates = [];
(function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) { if (name !== "node_modules" && name !== "static") walk(full); }
    else if (name.endsWith(".html") && full.includes(`${path.sep}templates${path.sep}`)) templates.push(full);
  }
})(path.join(TREE, "apps"));
for (const file of templates) {
  const html = fs.readFileSync(file, "utf8");
  const rel = path.relative(TREE, file);
  const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)];
  for (const match of scripts) {
    const offset = html.slice(0, match.index).split("\n").length - 1;
    const code = match[1].replace(/\{\{[\s\S]*?\}\}/g, "S1").replace(/\{%[\s\S]*?%\}/g, "");
    let ast;
    try { ast = parseAst(code, { lang: "js" }); }
    catch (error) { rows.push({ file: rel, line: offset, shape: "unparsed", open: [String(error.message).slice(0, 200)] }); continue; }
    CURRENT.ast = ast;
    let base = null;
    walkNode(ast, (n) => {
      if (n.type === "VariableDeclarator" && n.id.name === "API" && n.init && n.init.type === "Literal") base = n.init.value;
    });
    if (!base) continue;
    walkNode(ast, (n, parents) => {
      if (n.type !== "CallExpression" || n.callee.type !== "Identifier" || n.callee.name !== "api") return;
      const [where, options] = n.arguments;
      if (!where || where.type !== "Literal" || !options || options.type !== "ObjectExpression") return;
      const body = props(options).body;
      if (!body) return;
      const keys = new Set(), open = [];
      objectKeys(body, keys, open, null, parents);
      rows.push({ file: rel, line: offset + lineOf(code, n.start), shape: "api", url: base + where.value,
                  keys: [...keys].sort(), open });
    });
  }
}
for (const row of rows) console.log(JSON.stringify(row));
