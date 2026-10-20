import os, re, pathlib
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django; django.setup()
from django.urls import resolve
src = pathlib.Path("frontend/src")
reg = (src/"app/registry.ts").read_text()
# split screens by path:
for m in re.finditer(r'path: "([^"]+)", label: "([^"]+)", permission: "([^"]+)"(.*?)load: \(\) => import\("\.\./([^"]+)"\)', reg, re.S):
    path,label,perm,mid,mod = m.groups()
    f = src/(mod+".tsx")
    if not f.exists(): f = src/(mod+".ts")
    text = f.read_text() if f.exists() else ""
    urls = re.findall(r'"(/api/[a-z0-9-]+/[a-z0-9-]+/)"', text)
    got=None
    for u in urls[:1]:
        try:
            r = resolve(u); v = r.func.cls if hasattr(r.func,"cls") else None
            need=None
            if v is not None:
                if getattr(v,"required_permission",None): need=v.required_permission
                else:
                    qs=getattr(v,"queryset",None)
                    if qs is not None:
                        mt=qs.model._meta; need=f"{mt.app_label}.view_{mt.model_name}"
                    else: need="?"+",".join(getattr(c,"__name__","") for c in v.permission_classes)
            got=(u,need)
        except Exception as e:
            got=(u,"ERR "+str(e)[:40])
    flag = "" if (got and got[1]==perm) else "  <<<"
    print(f"{path:28} {perm:34} {got}{flag}")
