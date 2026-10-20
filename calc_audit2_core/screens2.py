import os, re, pathlib
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django; django.setup()
from django.urls import resolve, Resolver404
src = pathlib.Path("frontend/src")
reg = (src/"app/registry.ts").read_text()
def need_of(v):
    rp=getattr(v,"required_permission",None)
    if rp: return rp
    qs=getattr(v,"queryset",None)
    if qs is not None:
        mt=qs.model._meta; return f"{mt.app_label}.view_{mt.model_name}"
    return "?"+",".join(c.__name__ for c in getattr(v,"permission_classes",[]))
for m in re.finditer(r'path: "([^"]+)", label: "([^"]+)", permission: "([^"]+)"(.*?)load: \(\) => import\("\.\./([^"]+)"\)', reg, re.S):
    path,label,perm,mid,mod = m.groups()
    f = src/(mod+".tsx")
    text = f.read_text() if f.exists() else ""
    res=set()
    for u in re.findall(r'["`](/api/[a-z0-9_/-]+?/)(?:\$\{|["`])', text):
        try:
            r=resolve(u if u.endswith("/") else u+"/")
            v=getattr(r.func,"cls",None)
            res.add((u,need_of(v) if v else "fn"))
        except Resolver404: res.add((u,"404"))
    needs={n for _,n in res}
    flag="" if (perm in needs or not res) else "  <<<"
    print(f"{path:26} {perm:36} {sorted(res)}{flag}")
