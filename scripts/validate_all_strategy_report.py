"""Validate report coverage and numerical provenance without modifying source evidence."""
import hashlib,json,re
from collections import Counter
from pathlib import Path
from pypdf import PdfReader
from PIL import Image,ImageOps,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/all_strategies_20261012'
PDF=ROOT/'output/pdf/crypto_strategy_complete_report_20261012.pdf'
PNG=ROOT/'tmp/pdfs/all-strategies-20261012'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    catalogue=json.loads((OUT/'catalogue.json').read_text(encoding='utf-8'))
    entries=catalogue['entries'];text=(OUT/'REPORT_KO.md').read_text(encoding='utf-8')
    ids=re.findall(r'^### ([A-Z]\d\d) - ',text,re.M)
    assert len(ids)==len(set(ids))==89
    assert set(ids)=={x['id'] for x in entries}
    expected={'A':14,'B':6,'C':6,'D':3,'E':12,'F':8,'O':8,'G':14,'H':7,'I':3,'J':6,'Z':2}
    assert dict(Counter(x[0] for x in ids))==expected
    cards=re.split(r'^### ([A-Z]\d\d) - ',text,flags=re.M)
    labels=['전략 논리','신호·계산','진입·주문','청산·재고','EDA·실측 결과','가능한 해설','한계·판정']
    for i in range(1,len(cards),2):
        chunk=cards[i+1]
        assert all('**'+label+':**' in chunk for label in labels),cards[i]
        assert '근거: [' in chunk,cards[i]
    links=re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text)
    broken=[p for p in links if not p.startswith('https://') and not Path(p).exists()]
    assert not broken,broken
    assert not re.search(r'[\ue000-\uf8ff]',text)
    assert all(x==x.strip() for x in re.findall(r'\*\*(.*?)\*\*',text)), 'Malformed Markdown strong delimiters'
    integrity=json.loads((OUT/'integrity.json').read_text(encoding='utf-8'))
    altered=[p for p,v in integrity['source_sha256'].items() if sha(ROOT/p)!=v]
    assert not altered,altered
    reader=PdfReader(PDF);pages=[p.extract_text() or '' for p in reader.pages]
    pdf_text='\n'.join(pages)
    pdf_ids=re.findall(r'\b([A-Z]\d\d)\s*-\s*',pdf_text)
    assert set(ids)<=set(pdf_ids)
    critical=['0.113898','957','2.464','0.541994','2.71','4.12','31.8','67.6','100/100','180','ADF','KPSS']
    assert all(x in pdf_text for x in critical),[x for x in critical if x not in pdf_text]
    assert all(len(x)>50 for x in pages)
    t=json.loads((ROOT/'docs/evidence/fv_mm_v4_20261004/final_30min/results.json').read_text(encoding='utf-8'))
    p=json.loads((ROOT/'docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json').read_text(encoding='utf-8'))
    m=json.loads((ROOT/'docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json').read_text(encoding='utf-8'))
    assert len(t['performance'])==8 and sum(x['fills'] for x in t['performance'].values())==0
    assert len(p['performance'])==16 and len(p['stress_performance'])==16
    assert len(m['results'])==69 and sum(x['fill_events'] for x in m['results'])==957
    assert len(re.findall(r'^!\[',text,re.M))==5
    images=sorted(PNG.glob('page-*.png'))
    assert len(images)==len(pages),(len(images),len(pages))
    contacts=[]
    for start in range(0,len(images),12):
        sheet=Image.new('RGB',(1050,1560),'#dce4e8');draw=ImageDraw.Draw(sheet)
        for j,png in enumerate(images[start:start+12]):
            im=Image.open(png).convert('RGB');im.thumbnail((330,350))
            x=10+(j%3)*350;y=20+(j//3)*390
            sheet.paste(im,(x+(330-im.width)//2,y))
            draw.text((x+4,y+354),'PAGE '+str(start+j+1),fill='#173247')
        contact=PNG/f'contact-{start//12+1:02d}.png';sheet.save(contact);contacts.append(str(contact))
    page_map={x:next((i+1 for i,p in enumerate(pages) if re.search(r'\b'+x+r'\s*-',p)),None) for x in ids}
    checks={'entries':len(ids),'families':expected,'sources':len(catalogue['sources']),'linked_targets':len(links),'broken_links':broken,
            'missing_card_fields':[],'changed_sources':altered,'pdf_pages':len(pages),'rendered_pages':len(images),
            'all_ids_in_pdf':True,'critical_numbers_in_pdf':True,'figures':5,'case_counts':{'testnet':8,'public_pilot':16,'state_replay':69},
            'contacts':contacts,'strategy_first_occurrence_pages':page_map,'pdf_sha256':sha(PDF),'report_sha256':sha(OUT/'REPORT_KO.md')}
    (OUT/'validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in checks.items() if k not in ('strategy_first_occurrence_pages','contacts')},ensure_ascii=True))


if __name__=='__main__':main()
