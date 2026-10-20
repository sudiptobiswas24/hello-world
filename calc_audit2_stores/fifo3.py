from decimal import Decimal as D, ROUND_HALF_UP
p=lambda x:x.quantize(D("0.01"),rounding=ROUND_HALF_UP)
L=[[D(3),D("10.01")],[D(7),D("33.33")],[D(4),p(D("95.835051")*4)]]
def take(q):
    t=D(0)
    while q>0:
        l=L[0]; d=min(l[0],q)
        m=l[1] if d==l[0] else p(d*l[1]/l[0])
        l[0]-=d;l[1]-=m;t+=m;q-=d
        if l[0]==0:L.pop(0)
    return t
print(take(D(5)), take(D(6)), L)
