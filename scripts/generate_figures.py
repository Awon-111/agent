"""Redraw explanatory figures. Optional dependency: Pillow; set CJK_FONT if needed."""
from pathlib import Path
import json
import math
import os
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/assets'
FONT_CANDIDATES=[os.environ.get('CJK_FONT',''),str(Path(os.environ.get('WINDIR','/'))/'Fonts/simhei.ttf'),
                 '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
FONT=next((p for p in FONT_CANDIDATES if p and Path(p).is_file()),None)
if not FONT:
    raise SystemExit('Set CJK_FONT to an installed Chinese TrueType/OpenType font.')
NAVY='#173E5C'; BLUE='#377DA8'; TEAL='#368C83'; PALE='#EEF5F9'; GRAY='#577080'; LINE='#CCD9E3'

def canvas(w=1680,h=980):
    img=Image.new('RGB',(w,h),'white')
    return img,ImageDraw.Draw(img)

def text(d,xy,s,size=36,color=NAVY,anchor=None):
    d.text(xy,s,font=ImageFont.truetype(FONT,size),fill=color,anchor=anchor)

def box(d,rect,lines):
    d.rounded_rectangle(rect,radius=16,fill=PALE,outline=BLUE,width=3)
    x0,y0,x1,y1=rect
    for i,line in enumerate(lines):
        text(d,((x0+x1)/2,(y0+y1)/2+(i-(len(lines)-1)/2)*53),line,38,anchor='mm')

def arrow(d,points,color=BLUE):
    d.line(points,fill=color,width=5)
    (x0,y0),(x1,y1)=points[-2:]
    a=math.atan2(y1-y0,x1-x0)
    d.polygon([(x1,y1),(x1-20*math.cos(a-.45),y1-20*math.sin(a-.45)),
              (x1-20*math.cos(a+.45),y1-20*math.sin(a+.45))],fill=color)

def save(img,name):
    img.save(OUT/name,dpi=(280,280))

def evidence_flow():
    im,d=canvas()
    text(d,(370,65),'数据构建',52,anchor='mm');text(d,(1300,65),'按当前病例检索',52,anchor='mm')
    ys=[135,345,555,765]
    left=[['固定版本的公开数据'],['大小与SHA256校验'],['训练集隔离与文章去重'],['1362篇病例参考','保留原文与来源许可']]
    right=[['当前病例与问题'],['场景及部位过滤'],['BM25排序','E1等引用与库快照'],['说明相似点、差异','及信息缺口']]
    for i,y in enumerate(ys):
        box(d,(55,y,690,y+135),left[i]);box(d,(985,y,1620,y+135),right[i])
        if i<3:
            arrow(d,[(372,y+135),(372,ys[i+1])]);arrow(d,[(1302,y+135),(1302,ys[i+1])])
    arrow(d,[(690,832),(835,832),(835,412),(985,412)])
    text(d,(768,906),'检索语料',30,anchor='mm')
    save(im,'evidence-pipeline.png')

def recovery():
    im,d=canvas()
    box(d,(120,70,875,190),['已取得真实工具结果'])
    box(d,(120,290,875,440),['独立JSON报告整理','请求不携带tools'])
    arrow(d,[(497,190),(497,290)])
    d.polygon([(497,505),(900,625),(497,745),(95,625)],fill=PALE,outline=BLUE,width=4)
    text(d,(497,625),'字段、引用、输入校验',37,anchor='mm')
    arrow(d,[(497,440),(497,505)])
    box(d,(160,825,840,945),['保存完整待复核方案'])
    arrow(d,[(497,745),(497,825)]);text(d,(552,781),'通过',32,anchor='mm')
    box(d,(1040,550,1620,705),['反馈具体错误','最多纠正2次'])
    arrow(d,[(900,625),(1040,625)]);text(d,(967,585),'失败',31,anchor='mm')
    arrow(d,[(1330,550),(1330,365),(875,365)])
    box(d,(1000,825,1640,945),['仍失败：保留执行记录','不保存部分报告'])
    arrow(d,[(1330,705),(1330,825)])
    save(im,'report-recovery.png')

def repo_map():
    im,d=canvas(1680,820)
    text(d,(840,65),'Awon-111/agent · main',54,anchor='mm')
    columns=[('启动与界面',['app.py','start.ps1','.env.example','static/']),
             ('核心逻辑',['agent.py','planner.py','evidence.py','settings.py']),
             ('资料与复现',['data/evidence/','docs/','examples/','scripts/'])]
    for i,(title,items) in enumerate(columns):
        x=50+i*555
        d.rounded_rectangle((x,155,x+510,715),radius=15,fill=PALE,outline=LINE,width=3)
        text(d,(x+255,220),title,43,anchor='mm')
        for j,item in enumerate(items):text(d,(x+40,310+j*90),item,38)
    text(d,(840,775),'所有文件名均相对仓库根目录；私有配置与运行数据不发布。',31,GRAY,anchor='mm')
    save(im,'repository-map.png')

def corpus():
    meta=json.loads((ROOT/'data/evidence/manifest.json').read_text(encoding='utf-8'))
    im,d=canvas(1680,760)
    text(d,(70,35),'公开病例参考库的专科分布',52)
    text(d,(70,110),f"按文章去重 · 合计 {meta['count']} 篇",33,GRAY)
    x0,x1=310,1270
    for tick in range(0,601,200):
        x=x0+tick/600*(x1-x0);d.line((x,195,x,640),fill=LINE,width=2)
        text(d,(x,666),str(tick),30,GRAY,anchor='mm')
    for y,key,label,color in [(225,'spine','脊柱侧弯',BLUE),(365,'joint','关节置换',TEAL),(505,'trauma','骨折创伤','#789AB7')]:
        count=meta['scenarios'][key];end=x0+count/600*(x1-x0)
        text(d,(70,y+22),label,40);d.rounded_rectangle((x0,y,end,y+85),radius=7,fill=color)
        text(d,(end+25,y+22),f'{count} 篇',36)
    text(d,(70,714),'依据：data/evidence/manifest.json；文章数量不代表疾病发病比例。',29,GRAY)
    save(im,'case-distribution.png')

def cobb():
    im,d=canvas(1680,820)
    text(d,(420,65),'角度输入的几何含义',47,anchor='mm');text(d,(1250,65),'规则计算过程',47,anchor='mm')
    # An abstract two-line angle, not an anatomical X-ray or automated measurement.
    start=(180,410)
    for sign,color in [(-1,BLUE),(1,TEAL)]:
        angle=math.radians(sign*24)
        end=(start[0]+570*math.cos(angle),start[1]+570*math.sin(angle))
        d.line((start,end),fill=color,width=7)
    d.arc((90,320,270,500),start=-24,end=24,fill=NAVY,width=4)
    text(d,(290,380),'48°',50)
    text(d,(260,165),'上端参考线',31,BLUE);text(d,(260,660),'下端参考线',31,TEAL)
    for y,s in [(170,'输入Cobb角：48°'),(290,'输入柔韧性：55%'),
                (410,'矫正比例：0.28 + 0.004 × 55 = 0.50'),(530,'示意目标：48 × (1 - 0.50) = 24.0°')]:
        text(d,(860,y),s,34)
    text(d,(840,760),'仅说明人工输入与规则计算；不是病例影像或自动测角结果。',32,GRAY,anchor='mm')
    save(im,'cobb-calculation.png')

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    evidence_flow();recovery();repo_map();corpus();cobb()
    print('Wrote 5 figures to docs/assets/')
