"""Match attendees to QR PNGs by name and maintain badges.csv, the single master file for badge generation.

  python3 match_qr.py                 build badges.csv from b.xlsx + the QR PNG folder. Run once; refuses to overwrite
                                      an existing badges.csv because humans edit it afterwards.
  python3 match_qr.py --set DIR --xlsx FILE
                                      same, for a new set: DIR/qr/ holds the QR PNGs, DIR/badges.csv is written. FILE needs
                                      the headers Name, Company, Country, Category (No column is numbered 1..n in order).
  python3 match_qr.py --fill-missing  for rows of the existing badges.csv still marked 'missing', look for QR PNGs
                                      added to the folder since, and fill QR file / QR path / Match status. Other
                                      columns, including human edits, are left alone. Then run make_badge.py --rows
                                      with the row numbers printed here.
"""
import argparse,os,re,sys,unicodedata,collections,csv,openpyxl
OUT='badges.csv'
D='badges_Fusion_Georgia_2026_2026-10-02'
XLSX='b.xlsx'
HEADER_ALIASES={'Custom States':'Category'}  # the category column of some exports
USE_MANUAL=True  # MANUAL is keyed by the first set's row numbers; off for any --set
HEADER=['№','Name','Company','Country','Category','Source status','Attendance note','QR file','QR path','Match status','QR files found','Review note',
        'Template colour','PDF path','TIFF path','Badge status']
# xlsx row number -> file-name keys (same person, different spelling), verified by reading both lists
MANUAL={159:['giorgi koroghishvili','giorgi koroghlishvili'],207:['kamran iravanli'],
        325:['zhanara ahmedzhanova'],363:['grzegorz greg trepka'],372:['denis kohaev'],
        381:['abduvahid vokhidjon ataugilov']}
def norm(s):
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',s).split())

def scan_qr_folder():
    files=collections.defaultdict(list)   # normalised name -> [(mtime, qr id, file name)]
    spell_by_id=collections.defaultdict(set)
    for f in os.listdir(D):
        m=re.match(r'(\d{3})_(.+)_(\d+)\.png$',f)
        if not m: sys.exit(f'{D}: file name does not match NNN_Name_ID.png: {f!r}')
        files[norm(m.group(2))].append((os.path.getmtime(f'{D}/{f}'),int(m.group(3)),f))
        spell_by_id[int(m.group(3))].add(m.group(2).replace('_',' '))
    return files,spell_by_id

def candidates(n,name,files):
    keys=MANUAL.get(n,[norm(name or '')]) if USE_MANUAL else [norm(name or '')]
    return keys,[c for k in keys for c in files.get(k,[])]

def qr_notes(best,cands,name,spell_by_id):
    notes=[]
    found_ids=sorted({c[1] for c in cands})
    if len(found_ids)>1:
        notes.append('DIFFERENT QR CODES for this name: ids '+', '.join(map(str,found_ids))+f'; used newest id {best[1]}')
    variants=sorted(spell_by_id[best[1]]-{name})
    if variants: notes.append('QR file spelling differs from list: '+' / '.join(variants))
    return notes

def build():
    if os.path.exists(OUT): sys.exit(f'{OUT} exists; delete it first if you really want to rebuild it (human edits are lost)')
    files,spell_by_id=scan_qr_folder()
    sheet=list(openpyxl.load_workbook(XLSX).active.iter_rows(values_only=True))
    col={HEADER_ALIASES.get(h,h):i for i,h in enumerate(sheet[0]) if h}
    missing=[h for h in ('Name','Company','Country') if h not in col]
    if missing: sys.exit(f'{XLSX}: missing header(s) {missing}; found {list(col)}')
    col.update({h:i for h,i in (('Category',4),('Source status',5),('Attendance note',6)) if h not in col})  # b.xlsx has no headers on E-G
    get=lambda r,h:r[col[h]] if h in col and col[h]<len(r) else None
    kept=[(i,r) for i,r in enumerate(sheet[1:],2) if get(r,'Name')]
    blank=[i for i,r in enumerate(sheet[1:],2) if not get(r,'Name') and any(v not in (None,'') for v in r)]
    if blank: sys.exit(f'{XLSX}: rows with data but no Name (sheet rows {blank}); fix the sheet')
    if '№' in col:
        nums=[get(r,'№') for _,r in kept]
        if not all(isinstance(x,int) for x in nums) or len(set(nums))!=len(nums): sys.exit(f'{XLSX}: № values must be unique integers')
    else: nums=list(range(1,len(kept)+1))
    all_rows=[(n,get(r,'Name'),get(r,'Company'),get(r,'Country'),get(r,'Category'),get(r,'Source status'),get(r,'Attendance note'))
              for n,(_,r) in zip(nums,kept)]
    clean=lambda v: (v or '').strip() if isinstance(v,str) or v is None else str(v)
    out=[];used=set()
    name_count=collections.Counter(norm(r[1] or '') for r in all_rows)
    for r in all_rows:
        n,name,company,country=r[:4]
        keys,cands=candidates(n,name,files)
        used.update(k for k in keys if k in files)
        notes=[]
        if cands:
            best=max(cands); status='matched'; f=best[2]
            notes+=qr_notes(best,cands,name,spell_by_id)
        else: status='missing'; f=''
        if name_count[norm(name or '')]>1: notes.append('NAME APPEARS TWICE in list (rows '+', '.join(str(x[0]) for x in all_rows if norm(x[1] or '')==norm(name or ''))+')')
        out.append([n,clean(name),clean(company),clean(country),clean(r[4]),clean(r[5]),clean(r[6]),f,
                    os.path.abspath(f'{D}/{f}') if f else '',status,len(cands),'; '.join(notes),'','','',''])
    with open(OUT,'w',newline='',encoding='utf-8-sig') as fh:
        w=csv.writer(fh); w.writerow(HEADER); w.writerows(out)
    print('rows',len(out),'matched',sum(o[9]=='matched' for o in out),'missing',sum(o[9]=='missing' for o in out))
    print('QR name keys with no xlsx row:',len(set(files)-used))

def fill_missing():
    files,spell_by_id=scan_qr_folder()
    with open(OUT,encoding='utf-8-sig',newline='') as fh: rows=list(csv.DictReader(fh))
    filled=[]
    for r in rows:
        if r['Match status']!='missing': continue
        _,cands=candidates(int(r['№']),r['Name'],files)
        if not cands: continue
        best=max(cands)
        notes=[x for x in [r['Review note']] if x]+qr_notes(best,cands,r['Name'],spell_by_id)+['QR added after the first match']
        r.update({'QR file':best[2],'QR path':os.path.abspath(f'{D}/{best[2]}'),'Match status':'matched',
                  'QR files found':str(len(cands)),'Review note':'; '.join(notes)})
        filled.append(r['№'])
    with open(OUT+'.tmp','w',newline='',encoding='utf-8-sig') as fh:
        w=csv.DictWriter(fh,fieldnames=HEADER); w.writeheader(); w.writerows(rows)
    os.replace(OUT+'.tmp',OUT)
    print('filled',len(filled),'rows; still missing',sum(r['Match status']=='missing' for r in rows))
    if filled: print('make_badge.py --rows '+','.join(filled))

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--set',help='new badge set directory: DIR/qr holds the PNGs, DIR/badges.csv is read/written')
    ap.add_argument('--xlsx',help='attendee list for a new set (needs --set)')
    ap.add_argument('--fill-missing',action='store_true')
    args=ap.parse_args()
    if bool(args.set)!=bool(args.xlsx) and not args.fill_missing: ap.error('--set and --xlsx go together')
    if args.xlsx and not args.set: ap.error('--xlsx needs --set')
    if args.set:
        OUT,D,USE_MANUAL=os.path.join(args.set,'badges.csv'),os.path.join(args.set,'qr'),False
    if args.xlsx: XLSX=args.xlsx
    fill_missing() if args.fill_missing else build()
