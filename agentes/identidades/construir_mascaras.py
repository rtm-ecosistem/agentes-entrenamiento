"""Construye las máscaras de materiales de las láminas aprobadas: qué píxeles son pelo, prenda superior o prenda
inferior de cada figura. Con ellas agentes/apariencias.py recolorea una base conservando sombras, luces, contornos y
texturas. Solo hace falta volver a ejecutarlo si cambian las láminas (necesita numpy y Pillow):

    python agentes/identidades/construir_mascaras.py [carpeta para las imágenes de comprobación]

Carmen y Diego llevan conjunto (arriba y abajo del mismo color y con las mismas sombras), así que sus dos prendas
son un solo material. A Diego y a Tomás no se les cambia el pelo: su tono se confunde con el de la piel y las
sombras de la cara."""
import json, colorsys, numpy as np
from PIL import Image
from pathlib import Path
AQUI=Path(__file__).resolve().parent
W=str(AQUI.parent/'recursos')+'/'
B=json.load(open(AQUI/'medidas.json'))
NOM=['tomas','marta','iker','lucia','elena','carmen','bernardo','pablo','diego','sara']
PELO,SUP,INF=1,2,3
# (material, h0,h1, smin,smax, vmin,vmax, ymin,ymax) h en grados, y relativa a la figura
R={
 'tomas':[(SUP,225,280,.2,1,.12,1,.25,.85),(INF,190,230,.35,1,.12,1,.55,1)],
 'marta':[(SUP,345,360,.6,1,.25,1,.28,.8),(SUP,0,12,.6,1,.25,1,.28,.8),(INF,0,360,0,.45,.1,.22,.6,.9),(PELO,270,340,.15,1,.08,.3,0,.75)],
 'iker':[(SUP,37,60,.6,1,.3,1,.25,.85),(INF,195,225,.4,1,.12,1,.55,1),(PELO,245,290,.2,1,.1,1,0,.4)],
 'lucia':[(SUP,0,19,.58,1,.35,1,.25,.8),(INF,0,360,0,.2,.1,.3,.6,1),(PELO,10,35,.45,1,.08,.58,0,.42)],
 'elena':[(SUP,160,185,.5,1,.12,1,.25,.85),(INF,195,225,.35,1,.1,1,.55,1),(PELO,10,35,.4,1,.1,.58,0,.42)],
 'carmen':[(SUP,320,355,.3,1,.3,1,.25,.95),(PELO,0,360,0,.12,.25,.65,0,.65)],
 'bernardo':[(SUP,120,165,.3,1,.12,1,.25,.85),(INF,180,230,.1,.5,.1,.25,.6,1),(PELO,15,35,.5,1,.15,.5,0,.4)],
 'pablo':[(SUP,170,190,.6,1,.15,1,.25,.85),(INF,195,225,.4,1,.12,1,.6,1),(PELO,10,35,.8,1,.35,1,0,.38)],
 'diego':[(SUP,195,225,.5,1,.1,1,.25,1)],
 'sara':[(SUP,160,185,.5,1,.12,1,.25,.85),(INF,195,235,.18,1,.08,1,.55,1),(PELO,0,15,.6,1,.2,1,0,.45),(PELO,345,360,.6,1,.2,1,0,.45)],
}
def clasifica(a,n):
    h,w=a.shape[:2];rgb=a[...,:3]/255.0
    mx=rgb.max(2);mn=rgb.min(2);v=mx;s=np.where(mx>0,(mx-mn)/np.maximum(mx,1e-6),0)
    r,g,b=rgb[...,0],rgb[...,1],rgb[...,2];d=np.maximum(mx-mn,1e-6)
    hh=np.where(mx==r,((g-b)/d)%6,np.where(mx==g,(b-r)/d+2,(r-g)/d+4))*60
    yy=np.repeat(np.arange(h)[:,None]/h,w,1)
    m=np.zeros((h,w),np.uint8);op=a[...,3]>128
    for (mat,h0,h1,s0,s1,v0,v1,y0,y1) in R[n]:
        sel=op&(m==0)&(hh>=h0)&(hh<=h1)&(s>=s0)&(s<=s1)&(v>=v0)&(v<=v1)&(yy>=y0)&(yy<=y1)
        m[sel]=mat
    return m
if __name__=='__main__':
    import sys
    out={}
    for key,f in (('personajes','personajes.png'),('cargando','cargando.png')):
        img=np.array(Image.open(W+f).convert('RGBA'));M=np.zeros(img.shape[:2],np.uint8)
        for r,n in enumerate(NOM):
            for (x,y,w,h) in B[key][r]:
                M[y:y+h,x:x+w]=np.maximum(M[y:y+h,x:x+w],clasifica(img[y:y+h,x:x+w],n))
        out[key]=M
        Image.fromarray(M).save(AQUI/f'mascara-{key}.png',optimize=True)
    if len(sys.argv)<2: sys.exit()
    # comprobación: figura y máscara coloreada
    col=np.array([[0,0,0,0],[255,0,255,255],[0,255,0,255],[0,128,255,255]],np.uint8)
    for key,f in (('personajes','personajes.png'),('cargando','cargando.png')):
        img=np.array(Image.open(W+f).convert('RGBA'));M=out[key]
        dbg=img.copy();sel=M>0;dbg[sel]=(img[sel]*.25+col[M[sel]]*.75).astype(np.uint8)
        Image.fromarray(np.concatenate([img,dbg],1)).save(sys.argv[1]+f'/comprobar-{key}.png')
