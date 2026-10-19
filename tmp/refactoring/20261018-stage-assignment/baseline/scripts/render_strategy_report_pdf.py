"""Render the consolidated Markdown report with embedded Korean fonts and outline."""
import argparse,hashlib,json,re
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate,PageTemplate,Frame,Paragraph,Spacer,PageBreak,Table,TableStyle,Image,KeepTogether
from reportlab.platypus.tableofcontents import TableOfContents
from pypdf import PdfReader
from PIL import Image as PILImage

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/all_strategies_20261012'
PDF=ROOT/'output/pdf/crypto_strategy_complete_report_20261012.pdf'
INK=colors.HexColor('#173247');TEAL=colors.HexColor('#0C776D');GRAY=colors.HexColor('#637483')
PAGE_W,PAGE_H=A4;LEFT=18*mm;RIGHT=17*mm;BOTTOM=17*mm;TOP=20*mm;WIDTH=PAGE_W-LEFT-RIGHT


def inline(text):
    placeholders=[]
    def link(m):
        label,url=m.group(1),m.group(2)
        n=len(placeholders);placeholders.append('<link href="'+escape(url,{'"':'&quot;'})+'" color="#0C776D">'+escape(label)+'</link>')
        return '@@LINK'+str(n)+'@@'
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,text)
    text=escape(text)
    text=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',text)
    text=re.sub(r'`([^`]+)`',r'<font color="#0C776D">\1</font>',text)
    for n,v in enumerate(placeholders):text=text.replace('@@LINK'+str(n)+'@@',v)
    return text


class ReportDoc(BaseDocTemplate):
    def __init__(self,path):
        super().__init__(str(path),pagesize=A4,leftMargin=LEFT,rightMargin=RIGHT,topMargin=TOP,bottomMargin=BOTTOM,
                         title='코인 MM 전략·후보 전체 통합 보고서',author='VibeTrading 연구 기록',pageCompression=1)
        frame=Frame(LEFT,BOTTOM,WIDTH,PAGE_H-TOP-BOTTOM,id='body',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='normal',frames=[frame],onPage=self.paint))
        self.bookmarks=0

    def beforeDocument(self):
        # multiBuild must emit the same bookmark keys on every TOC pass.
        self.bookmarks=0

    def paint(self,canvas,doc):
        canvas.saveState()
        canvas.setStrokeColor(TEAL);canvas.setLineWidth(.8);canvas.line(LEFT,PAGE_H-13*mm,PAGE_W-RIGHT,PAGE_H-13*mm)
        canvas.setFont('KR',8);canvas.setFillColor(GRAY)
        canvas.drawString(LEFT,PAGE_H-10*mm,'VibeTrading  |  전략 연구·운용 기록')
        canvas.drawRightString(PAGE_W-RIGHT,PAGE_H-10*mm,'2026-10-12 KST')
        canvas.line(LEFT,12*mm,PAGE_W-RIGHT,12*mm)
        canvas.drawString(LEFT,8*mm,'과거 실측·제안·미검증을 구분한 통합 보고서')
        canvas.drawRightString(PAGE_W-RIGHT,8*mm,str(doc.page))
        canvas.restoreState()

    def afterFlowable(self,flowable):
        if isinstance(flowable,Paragraph) and flowable.style.name in ('H1','Card'):
            text=flowable.getPlainText();level=0 if flowable.style.name=='H1' else 1
            self.bookmarks+=1;key='section'+str(self.bookmarks)
            self.canv.bookmarkPage(key);self.canv.addOutlineEntry(text,key,level,False)
            if level==0:self.notify('TOCEntry',(0,text,self.page,key))


def main():
    pdfmetrics.registerFont(TTFont('KR','C:/Windows/Fonts/malgun.ttf'))
    pdfmetrics.registerFont(TTFont('KR-Bold','C:/Windows/Fonts/malgunbd.ttf'))
    pdfmetrics.registerFontFamily('KR',normal='KR',bold='KR-Bold',italic='KR',boldItalic='KR-Bold')
    styles={
      'body':ParagraphStyle('Body',fontName='KR',fontSize=9.2,leading=14.5,spaceAfter=6,wordWrap='CJK',splitLongWords=1),
      'h1':ParagraphStyle('H1',fontName='KR-Bold',fontSize=17,leading=24,spaceBefore=15,spaceAfter=10,textColor=INK,keepWithNext=True,wordWrap='CJK'),
      'h2':ParagraphStyle('Card',fontName='KR-Bold',fontSize=12.1,leading=18,spaceBefore=14,spaceAfter=7,textColor=TEAL,keepWithNext=True,wordWrap='CJK'),
      'cell':ParagraphStyle('Cell',fontName='KR',fontSize=7.15,leading=10.2,wordWrap='CJK',splitLongWords=1),
      'head':ParagraphStyle('Head',fontName='KR-Bold',fontSize=7.3,leading=10.5,wordWrap='CJK',textColor=colors.white),
      'caption':ParagraphStyle('Caption',fontName='KR',fontSize=8,leading=12,textColor=GRAY,wordWrap='CJK',spaceAfter=10),
      'title':ParagraphStyle('Cover',fontName='KR-Bold',fontSize=27,leading=39,textColor=INK,wordWrap='CJK',spaceAfter=16),
      'subtitle':ParagraphStyle('Sub',fontName='KR',fontSize=12,leading=20,textColor=TEAL,wordWrap='CJK',spaceAfter=15),
      'toc':ParagraphStyle('TOC',fontName='KR',fontSize=10.5,leading=18,textColor=INK,wordWrap='CJK',spaceAfter=5)}
    count=len(json.loads((OUT/'catalogue.json').read_text(encoding='utf-8'))['entries'])
    story=[Spacer(1,37*mm),Paragraph('코인 MM 전략·후보<br/>전체 통합 보고서',styles['title']),
           Paragraph('논리 · 신호 · 진입과 청산 · EDA · 실측 · 해설',styles['subtitle']),
           Paragraph(f'{count}개 등록 항목 / 원형·개정·하위분기·대조군·제안 포함',styles['body']),
           Paragraph('작성 2026-10-12 KST<br/>측정 기록 2026-10-03~07<br/>새로운 주문·실험·생산 서비스 변경 없음',styles['body']),
           Spacer(1,15*mm),Paragraph('이 문서는 성공 전략의 소개서가 아니라, 무엇을 가정했고 무엇을 실제로 확인했는지 남기는 연구 기록이다. 거래되지 않은 후보와 비용 후 손실을 구분하며, 유사한 모델과 개정판도 별도 항목으로 보존한다.',styles['body']),
           PageBreak(),Paragraph('목차',styles['title'])]
    toc=TableOfContents();toc.levelStyles=[styles['toc']];story.extend([toc,Spacer(1,12*mm),Paragraph('개별 전략 ID는 3장 목록과 PDF 북마크에서 바로 찾을 수 있다. 동일 본문의 Markdown에는 원본 근거와 전체 결과 파일 링크가 있다.',styles['caption']),PageBreak()])
    lines=(OUT/'REPORT_KO.md').read_text(encoding='utf-8').splitlines();i=1
    while i<len(lines):
        line=lines[i].strip()
        if not line:i+=1;continue
        if line.startswith('## '):
            if not line.startswith('## 1.') and not isinstance(story[-1],PageBreak):story.append(PageBreak())
            story.append(Paragraph(inline(line[3:]),styles['h1']));i+=1;continue
        if line.startswith('### '):story.append(Paragraph(inline(line[4:]),styles['h2']));i+=1;continue
        if line.startswith('|'):
            data=[]
            while i<len(lines) and lines[i].lstrip().startswith('|'):
                row=[x.strip() for x in lines[i].strip().strip('|').split('|')];i+=1
                if all(re.fullmatch(r':?-{2,}:?',x) for x in row):continue
                data.append(row)
            if not data:continue
            cols=max(map(len,data));data=[r+['']*(cols-len(r)) for r in data]
            # Wide experiment tables get numeric columns; text columns retain room.
            if cols>=7:weights=[2.5]+[1.]*(cols-1)
            elif cols==3 and data[0][0]=='ID':weights=[.8,3.5,2.5]
            elif cols==3:weights=[1.2,3.,3.]
            elif cols==5:weights=[1.5,2.5,1.2,2.,3.]
            else:weights=[max(1,min(3.5,max(len(str(r[c])) for r in data[:20])/10)) for c in range(cols)]
            widths=[WIDTH*w/sum(weights) for w in weights]
            paras=[[Paragraph(inline(x),styles['head'] if ri==0 else styles['cell']) for x in row] for ri,row in enumerate(data)]
            table=Table(paras,colWidths=widths,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),
                ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
                ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F3F7F8'),colors.white]),
                ('LINEBELOW',(0,0),(-1,0),.7,TEAL),('LINEBELOW',(0,1),(-1,-1),.2,colors.HexColor('#D8E1E6'))]))
            story.extend([table,Spacer(1,9)]);continue
        img=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if img:
            p=Path(img.group(2));im=PILImage.open(p);w,h=im.size
            scale=min(WIDTH/w,145*mm/h)
            story.extend([Spacer(1,8),Image(str(p),width=w*scale,height=h*scale),Paragraph(inline(img.group(1).strip()),styles['caption'])]);i+=1;continue
        text=[line];i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(('#','|','![')):
            text.append(lines[i].strip());i+=1
        story.append(Paragraph(inline(' '.join(text)),styles['body']))
    PDF.parent.mkdir(parents=True,exist_ok=True)
    doc=ReportDoc(PDF);doc.multiBuild(story)
    reader=PdfReader(PDF);full='\n'.join(p.extract_text() or '' for p in reader.pages)
    entries=json.loads((OUT/'catalogue.json').read_text(encoding='utf-8'))['entries']
    missing=[s['id'] for s in entries if s['id'] not in full]
    if missing:raise ValueError('Missing PDF catalogue entries '+repr(missing))
    if not any('잔여' in (p.extract_text() or '') for p in reader.pages):raise ValueError('Korean text extraction failed')
    checks={'pdf':str(PDF),'pages':len(reader.pages),'strategy_entries':count,'missing_entry_ids':missing,
            'embedded_fonts':['malgun.ttf','malgunbd.ttf'],'pdf_sha256':hashlib.sha256(PDF.read_bytes()).hexdigest(),
            'markdown_sha256':hashlib.sha256((OUT/'REPORT_KO.md').read_bytes()).hexdigest(),'render_validation':'pending'}
    (OUT/'pdf_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=True))


if __name__=='__main__':main()
