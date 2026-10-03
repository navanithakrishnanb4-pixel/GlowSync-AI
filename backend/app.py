"""Local Flask API: authentication, catalogue, shade studio, wishlists and imports."""
from __future__ import annotations
import csv
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import HTTPException

from .catalogue import ROOT, BRANDS, BY_BRAND, CATEGORIES, REGIONS, norm, fetch_brand, get_json, upsert_products, validate_import, shade_tags
from .db import connect, initialize

def create_app(database=None, testing=False):
 app=Flask(__name__,static_folder=str(ROOT/'static'),template_folder=str(ROOT/'templates'))
 db_path=str(database or os.environ.get('GLOWSYNC_DATABASE',ROOT/'local.db'))
 key_path=ROOT/'.session-key'
 if testing: secret='glowsync-test-secret-not-for-production'
 elif os.environ.get('GLOWSYNC_SECRET'): secret=os.environ['GLOWSYNC_SECRET']
 else:
  if not key_path.exists():
   try:
    with key_path.open('x',encoding='utf-8') as f: f.write(secrets.token_hex(48))
    try: key_path.chmod(0o600)
    except OSError: pass
   except FileExistsError: pass
  secret=key_path.read_text(encoding='utf-8').strip()
 app.config.update(SECRET_KEY=secret,DATABASE=db_path,TESTING=testing,MAX_CONTENT_LENGTH=24*1024*1024,
  SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=False,PERMANENT_SESSION_LIFETIME=60*60*24*14)
 initialize(db_path)
 lock=threading.Lock()
 job={'running':False,'message':'No refresh running.','results':[],'finished_at':None,'owner':None}
 ai_lock=threading.Lock()
 ai_job={'running':False,'message':'','ok':None,'model':None,'finished_at':None}

 def db():
  if 'db' not in g: g.db=connect(app.config['DATABASE'])
  return g.db

 @app.teardown_appcontext
 def close_db(_):
  if 'db' in g: g.db.close()

 def error(message,status=400): return jsonify(error=message),status
 def body():
  data=request.get_json(silent=True)
  if not isinstance(data,dict): raise ValueError('A JSON object is required')
  return data
 def current_user():
  if session.get('uid'):
   return db().execute('SELECT id,email,name,is_admin,preferences FROM users WHERE id=?',(session['uid'],)).fetchone()
  return None
 def user_json(u):
  return {k:u[k] for k in ['id','email','name','is_admin']}|{'preferences':json.loads(u['preferences'])}
 def login_required(fn):
  @wraps(fn)
  def wrapped(*a,**kw):
   if not current_user(): return error('Sign in to save your choices.',401)
   return fn(*a,**kw)
  return wrapped
 def admin_required(fn):
  @wraps(fn)
  @login_required
  def wrapped(*a,**kw):
   if not current_user()['is_admin']: return error('Catalogue management is available to the local owner account.',403)
   return fn(*a,**kw)
  return wrapped

 @app.before_request
 def protect():
  # The launcher is loopback-only; reject DNS rebinding and foreign origins as well.
  hostname=request.host.split(':')[0].lower()
  if hostname not in ('127.0.0.1','localhost','[::1]'): return error('Use the local GlowSync address.',403)
  if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
  if request.method in ('POST','PUT','PATCH','DELETE'):
   origin=request.headers.get('Origin')
   if origin and origin!=request.host_url.rstrip('/'): return error('Cross-site request rejected.',403)
   token=request.headers.get('X-CSRF-Token','')
   if not secrets.compare_digest(token,session['csrf']): return error('Your session changed. Refresh the page and try again.',403)

 @app.after_request
 def headers(response):
  response.headers['X-Content-Type-Options']='nosniff'
  response.headers['X-Frame-Options']='DENY'
  response.headers['Referrer-Policy']='no-referrer'
  response.headers['Permissions-Policy']='camera=(self), microphone=(), geolocation=()'
  response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
  if request.path.startswith('/api/'): response.headers['Cache-Control']='no-store'
  return response

 @app.errorhandler(Exception)
 def handle_exception(exc):
  if isinstance(exc,HTTPException): return error(exc.description,exc.code)
  if isinstance(exc,ValueError): return error(str(exc))
  app.logger.exception('Request failed')
  return error('Something went wrong. Please try again; details are in the local terminal.',500)

 @app.get('/')
 @app.get('/studio')
 @app.get('/catalogue')
 def home(): return render_template('index.html')

 @app.get('/api/health')
 def health():
  shade='unavailable'
  try:
   with urlopen(f'http://127.0.0.1:{os.environ.get("GLOWSYNC_JAVA_PORT","8081")}/health',timeout=.6) as r:
    if r.status==200: shade='ready'
  except OSError: pass
  return jsonify(status='ok',java=shade,products=db().execute('SELECT count(*) FROM catalog_products WHERE active=1').fetchone()[0])

 @app.get('/api/session')
 def get_session():
  u=current_user()
  return jsonify(csrf=session['csrf'],user=user_json(u) if u else None)

 def rate_limit(email):
  now=int(time.time())
  buckets=['ip:'+str(request.remote_addr),'email:'+email]
  for bucket in buckets:
   row=db().execute('SELECT * FROM attempts WHERE bucket=?',(bucket,)).fetchone()
   if row and row['expires']>now and row['count']>=15: return False
  for bucket in buckets:
   db().execute('''INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET
    count=CASE WHEN expires<=? THEN 1 ELSE count+1 END,expires=CASE WHEN expires<=? THEN excluded.expires ELSE expires END''',(bucket,now+900,now,now))
  db().execute('DELETE FROM attempts WHERE expires<?',(now-3600,))
  db().commit()
  return True

 @app.post('/api/auth/register')
 @app.post('/api/auth/login')
 def auth():
  data=body(); email=str(data.get('email','')).strip().lower(); password=data.get('password','')
  if not isinstance(password,str) or len(password)>256 or len(email)>254: return error('Invalid credentials.')
  if not rate_limit(email): return error('Too many attempts. Please try again in 15 minutes.',429)
  if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): return error('Enter a valid email address.')
  register=request.path.endswith('register')
  if register:
   name=str(data.get('name','')).strip()
   if not 1<=len(name)<=80 or not 10<=len(password)<=256: return error('Use a name and a password of at least 10 characters.')
   uid=str(uuid.uuid4())
   # Serialize first-user assignment so exactly one owner is created.
   db().execute('BEGIN IMMEDIATE')
   owner=not db().execute('SELECT 1 FROM users LIMIT 1').fetchone()
   try:
    db().execute('INSERT INTO users(id,email,name,password,is_admin) VALUES(?,?,?,?,?)',(uid,email,name,generate_password_hash(password),int(owner)))
    db().commit()
   except sqlite3.IntegrityError:
    db().rollback(); return error('An account with this email already exists. Try signing in.',409)
  else:
   row=db().execute('SELECT * FROM users WHERE lower(email)=?',(email,)).fetchone()
   if not row or not check_password_hash(row['password'],password): return error('Email or password is incorrect.',401)
   uid=row['id']
  session.clear(); session['uid']=uid; session['csrf']=secrets.token_urlsafe(32); session.permanent=True
  return jsonify(user=user_json(current_user()),csrf=session['csrf'])

 @app.post('/api/auth/logout')
 def logout():
  session.clear(); session['csrf']=secrets.token_urlsafe(32)
  return jsonify(ok=True,csrf=session['csrf'])

 @app.put('/api/profile')
 @login_required
 def profile():
  data=body(); name=str(data.get('name',current_user()['name'])).strip()
  if not 1<=len(name)<=80: return error('Name must contain 1–80 characters.')
  preferences=data.get('preferences',{})
  if not isinstance(preferences,dict) or len(json.dumps(preferences))>3000: return error('Invalid preferences.')
  db().execute('UPDATE users SET name=?,preferences=? WHERE id=?',(name,json.dumps(preferences),session['uid']));db().commit()
  return jsonify(user=user_json(current_user()))

 def product(pid, active=False):
  r=db().execute('SELECT data,active FROM catalog_products WHERE id=?'+(' AND active=1' if active else ''),(pid,)).fetchone()
  if not r: return None
  p=json.loads(r['data']);p['active']=bool(r['active']);return p

 def public_product(p):
  p=dict(p);p['brand_name']=BY_BRAND[p['brand']]['name'];p['category_name']=CATEGORIES[p['category']]
  cached='/static/products/'+__import__('hashlib').sha256(str(p.get('image','')).encode()).hexdigest()[:24]+'.jpg'
  if (ROOT/cached.lstrip('/')).exists(): p['local_image']=cached
  else: p['local_image']=None
  p['watch_supported']=bool(BY_BRAND[p['brand']].get('feed_base') and p.get('handle'))
  return p

 @app.get('/api/meta')
 def meta():
  counts={r['brand']:r['n'] for r in db().execute('SELECT brand,count(*) n FROM catalog_products WHERE active=1 GROUP BY brand')}
  cats={r['category']:r['n'] for r in db().execute('SELECT category,count(*) n FROM catalog_products WHERE active=1 GROUP BY category')}
  sources={r['brand']:json.loads(r['data']) for r in db().execute('SELECT * FROM catalog_sources')}
  return jsonify(brands=[dict(b,count=counts.get(b['id'],0),status=sources.get(b['id'],{})) for b in BRANDS],
   categories=[{'id':c,'name':n,'count':cats.get(c,0)} for c,n in CATEGORIES.items()],regions=REGIONS,total=sum(counts.values()),
   variants=sum(len(json.loads(r['data'])['variants']) for r in db().execute('SELECT data FROM catalog_products WHERE active=1')))

 def filtered_products():
  q=norm(request.args.get('q','').strip())[:120];brand=request.args.get('brand','');category=request.args.get('category','');region=request.args.get('region','')
  finish=request.args.get('finish','');sort=request.args.get('sort','featured');maximum=request.args.get('max_price','')
  try: page=max(1,int(request.args.get('page','1')));limit=min(60,max(1,int(request.args.get('limit','24'))))
  except ValueError: raise ValueError('Invalid page number')
  if brand and brand not in BY_BRAND: raise ValueError('Unknown brand')
  if category and category not in CATEGORIES: raise ValueError('Unknown category')
  if region and region not in REGIONS: raise ValueError('Unknown face region')
  clauses=['active=1'];params=[]
  if brand: clauses.append('brand=?');params.append(brand)
  if category: clauses.append('category=?');params.append(category)
  if region:
   cs=REGIONS[region];clauses.append('category IN ('+','.join('?' for _ in cs)+')');params.extend(cs)
  if finish:
   if finish not in ['matte','radiant','satin','unspecified']: raise ValueError('Unknown finish')
   clauses.append('finish=?');params.append(finish)
  if q:
   for word in q.split():
    clauses.append("search LIKE ? ESCAPE '\\'");params.append('%'+word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
  max_p=None
  if maximum:
   try: max_p=float(maximum)*100
   except ValueError: raise ValueError('Invalid budget')
   if not math.isfinite(max_p) or max_p<0: raise ValueError('Invalid budget')
  rows=db().execute('SELECT data FROM catalog_products WHERE '+' AND '.join(clauses),params).fetchall()
  products=[];unknown=0
  for row in rows:
   p=json.loads(row['data']);vs=p['variants']
   if q:
    vq=[v for v in vs if all(w in norm(p['name']+' '+BY_BRAND[p['brand']]['name']+' '+v['name']+' '+p['category']) for w in q.split())]
    vs=vq
   if max_p is not None:
    if all(v.get('price') is None for v in vs): unknown+=1
    vs=[v for v in vs if v.get('price') is not None and v['price']<=max_p]
   if request.args.get('available')=='1':
    if p.get('observed_at',0)<time.time()-86400: vs=[]
    else: vs=[v for v in vs if v.get('available') is True]
   if not vs: continue
   p['matching_variant_ids']=[v['id'] for v in vs]
   p['display_variant_id']=next((v['id'] for v in vs if v.get('available') is True),vs[0]['id'])
   products.append(p)
  if sort in ('price-asc','price-desc'):
   def pricekey(p):
    prices=[v['price'] for v in p['variants'] if v['id'] in p['matching_variant_ids'] and v.get('price') is not None]
    return (not prices,(min(prices) if sort=='price-asc' else -min(prices)) if prices else 0,norm(p['name']))
   products.sort(key=pricekey)
  elif sort=='name': products.sort(key=lambda p:norm(p['name']))
  else:
   # Mix brands, with individual makeup ranges ahead of bundles/accessories.
   by_brand={b['id']:[] for b in BRANDS}
   for p in products: by_brand[p['brand']].append(p)
   for rows in by_brand.values(): rows.sort(key=lambda p:(p['category'] in ['sets','tools','other'],p['category'] not in ['foundation','lipstick','lip-gloss','mascara'],norm(p['name'])))
   products=[rows[i] for i in range(max((len(v) for v in by_brand.values()),default=0)) for rows in by_brand.values() if len(rows)>i]
  total=len(products);pages=max(1,math.ceil(total/limit));page=min(page,pages)
  return products[(page-1)*limit:page*limit],total,page,pages,unknown

 @app.get('/api/products')
 def products():
  ps,total,page,pages,unknown=filtered_products()
  return jsonify(products=[public_product(p) for p in ps],total=total,page=page,pages=pages,unknown_price_excluded=unknown)

 @app.get('/api/products/<path:pid>')
 def product_detail(pid):
  p=product(pid)
  if not p: return error('Product not found.',404)
  return jsonify(product=public_product(p))

 @app.get('/api/wishlist')
 @login_required
 def wishlist():
  items=[]
  for row in db().execute('SELECT * FROM wishlist WHERE user_id=? ORDER BY rowid DESC',(session['uid'],)):
   p=product(row['product_id']);v=next((v for v in p['variants'] if v['id']==row['variant_id']),None) if p else None
   items.append(dict(row)|{'product':public_product(p) if p else None,'variant':v})
  return jsonify(items=items)

 @app.post('/api/wishlist')
 @login_required
 def add_wish():
  data=body();p=product(str(data.get('product_id','')),True)
  if not p or str(data.get('variant_id')) not in [v['id'] for v in p['variants']]: return error('Choose an existing product and variant.')
  if db().execute('SELECT count(*) FROM wishlist WHERE user_id=?',(session['uid'],)).fetchone()[0]>=500: return error('Your wishlist can hold up to 500 variants.')
  db().execute('INSERT OR IGNORE INTO wishlist(id,user_id,product_id,variant_id) VALUES(?,?,?,?)',(str(uuid.uuid4()),session['uid'],p['id'],str(data['variant_id'])));db().commit()
  return jsonify(ok=True)

 @app.delete('/api/wishlist/<wid>')
 @login_required
 def remove_wish(wid):
  cursor=db().execute('DELETE FROM wishlist WHERE id=? AND user_id=?',(wid,session['uid']));db().commit()
  if not cursor.rowcount: return error('Saved item not found.',404)
  return jsonify(ok=True)

 @app.put('/api/wishlist/<wid>/watch')
 @login_required
 def watch(wid):
  row=db().execute('SELECT * FROM wishlist WHERE id=? AND user_id=?',(wid,session['uid'])).fetchone()
  if not row: return error('Saved item not found.',404)
  data=body();enabled=data.get('enabled')
  if not isinstance(enabled,bool): return error('enabled must be true or false')
  p=product(row['product_id']);v=next((v for v in p['variants'] if v['id']==row['variant_id']),None) if p else None
  if enabled and (not p or not v or not public_product(p)['watch_supported']): return error('This variant has no connected price feed.')
  if enabled and not row['watching'] and db().execute('SELECT count(*) FROM wishlist WHERE user_id=? AND watching=1',(session['uid'],)).fetchone()[0]>=30:
   return error('You can watch up to 30 variants at once. Turn off another watch to add this one.')
  # A saved snapshot never establishes an alert baseline. The first successful live check does.
  db().execute('UPDATE wishlist SET watching=?,baseline=NULL WHERE id=?',(int(enabled),wid));db().commit()
  return jsonify(ok=True)

 @app.get('/api/notifications')
 @login_required
 def notifications():
  return jsonify(items=[dict(r) for r in db().execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created DESC LIMIT 100',(session['uid'],))])

 @app.post('/api/notifications/read')
 @login_required
 def read_notifications():
  db().execute('UPDATE notifications SET seen=1 WHERE user_id=?',(session['uid'],));db().commit();return jsonify(ok=True)

 def update_watch_quotes(user_id):
  results=[]
  with connect(app.config['DATABASE']) as connection:
   rows=connection.execute('SELECT DISTINCT product_id FROM wishlist WHERE watching=1 AND user_id=? LIMIT 30',(user_id,)).fetchall()
  currencies={}
  for row in rows:
   with connect(app.config['DATABASE']) as connection:
    pr=connection.execute('SELECT data,active FROM catalog_products WHERE id=?',(row['product_id'],)).fetchone()
   if not pr or not pr['active']: continue
   p=json.loads(pr['data']);brand=BY_BRAND[p['brand']]
   if not brand.get('feed_base') or not p.get('handle'): continue
   try:
    if p['brand'] not in currencies: currencies[p['brand']]=get_json(brand,'/meta.json').get('currency')
    if currencies[p['brand']]!='INR': raise ValueError('Currency was not confirmed as INR')
    from urllib.parse import quote
    raw=get_json(brand,'/products/'+quote(p['handle'],safe='-')+'.js')
    remote={str(v['id']):v for v in raw.get('variants',[])}
    if str(raw.get('id'))!=p['id'].split(':',1)[1]: raise ValueError('Source returned a different product')
    now=int(time.time());valid={}
    for v in p['variants']:
     fresh=remote.get(v['id'])
     if not fresh: continue
     price=fresh.get('price')
     if not isinstance(price,int) or isinstance(price,bool) or not 0<=price<=100000000: continue
     v['price']=price;v['available']=fresh.get('available') if isinstance(fresh.get('available'),bool) else None
     mrp=fresh.get('compare_at_price');v['mrp']=mrp if isinstance(mrp,int) and mrp>price else None
     valid[v['id']]=v
    # Do not mark missing variants as fresh or use their old prices for alerts.
    if not valid: raise ValueError('No verified variant prices were returned')
    for v in p['variants']:
     if v['id'] not in valid: v['price']=None;v['mrp']=None;v['available']=None
    p['observed_at']=now;p['source_kind']='feed';p['currency']='INR'
    with connect(app.config['DATABASE']) as connection:
     upsert_products(connection,[p]);apply_price_alerts(connection,p,now)
    results.append({'brand':brand['name'],'ok':True,'message':p['name']+': prices checked'})
   except Exception as exc:
    results.append({'brand':brand['name'],'ok':False,'message':p['name']+': '+source_error(exc)})
  if not rows: results.append({'ok':True,'message':'No price watches are enabled.'})
  return results

 def run_job(kind,uid,brands):
  results=[]
  try:
   if kind=='prices': results=update_watch_quotes(uid)
   else:
    for brand in brands:
     try:
      ps,status=fetch_brand(brand,lambda m:job.update(message=m))
      with connect(app.config['DATABASE']) as connection:
       upsert_products(connection,ps,brand,True,status)
       for p in ps: apply_price_alerts(connection,p,status['observed_at'])
      results.append({'brand':BY_BRAND[brand]['name'],'ok':True,'message':f'{len(ps):,} products refreshed'})
     except Exception as exc: results.append({'brand':BY_BRAND[brand]['name'],'ok':False,'message':source_error(exc)})
     job['results']=list(results)
  finally:
   job.update(running=False,message='Refresh finished.',results=results,finished_at=int(time.time()))
   lock.release()

 def start_job(kind,brands=None):
  if not lock.acquire(blocking=False): return error('A refresh is already running. Check its progress below.',409)
  uid=session['uid'];job.update(running=True,message='Contacting official sources…',results=[],finished_at=None,owner=uid)
  threading.Thread(target=run_job,args=(kind,uid,brands),daemon=True).start()
  return jsonify(ok=True),202

 @app.post('/api/prices/check')
 @login_required
 def check_prices(): return start_job('prices')

 @app.post('/api/catalogue/sync')
 @admin_required
 def sync():
  brands=body().get('brands')
  if brands is None: brands=[b['id'] for b in BRANDS if b.get('feed_base')]
  if not isinstance(brands,list) or not brands or any(b not in BY_BRAND or not BY_BRAND[b].get('feed_base') for b in brands): return error('Choose brands with connected feeds.')
  return start_job('catalogue',list(dict.fromkeys(brands)))

 @app.get('/api/jobs')
 @login_required
 def jobs():
  if job['owner'] not in (None,session['uid']) and not current_user()['is_admin']:
   return jsonify(running=job['running'],message='Another account is refreshing data.',results=[])
  return jsonify(**{k:v for k,v in job.items() if k!='owner'})

 @app.post('/api/catalogue/import')
 @admin_required
 def import_catalogue():
  ps=validate_import(body())
  with db(): upsert_products(db(),ps)
  return jsonify(ok=True,count=len(ps))

 @app.get('/api/catalogue/export')
 @admin_required
 def export_catalogue():
  ps=[]
  for row in db().execute('SELECT data FROM catalog_products WHERE active=1'):
   p=json.loads(row['data']);variants=[]
   for v in p['variants']:
    variants.append({'id':v['id'],'name':v['name'],'price_inr':v['price']/100 if v.get('price') is not None else None,'mrp_inr':v['mrp']/100 if v.get('mrp') is not None else None,'available':v.get('available'),'url':v.get('url',p['url'])})
   ps.append({k:p[k] for k in ['id','brand','name','category','url','image','source_url','observed_at']}|{'variants':variants})
  return Response(json.dumps({'schema_version':1,'products':ps},ensure_ascii=False),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=glowsync-catalogue.json'})

 @app.post('/api/recommend')
 def recommend():
  data=body()
  allowed={'tone':['fair','light','medium','tan','deep'],'undertone':['warm','cool','neutral'],'occasion':['everyday','work','evening','festive'],'clothing':['neutral','red','blue','green','pink','black','white'],'finish':['any','matte','radiant','satin']}
  settings={key:data.get(key,values[0]) for key,values in allowed.items()}
  if any(settings[k] not in v for k,v in allowed.items()): return error('Invalid studio preference.')
  port=os.environ.get('GLOWSYNC_JAVA_PORT','8081')
  try:
   with urlopen(f'http://127.0.0.1:{port}/palette?'+urlencode(settings),timeout=3) as r: palette=json.load(r)
  except (OSError,ValueError): return error('Shade service is unavailable. Start the app with run.py to enable the studio.',503)
  suggestions=[]
  for row in db().execute("SELECT data FROM catalog_products WHERE active=1 AND category='foundation'"):
   p=json.loads(row['data'])
   if settings['finish']!='any' and p.get('finish')!=settings['finish']: continue
   vs=[]
   for v in p['variants']:
    tone,undertone=shade_tags(v['name'])
    if tone==settings['tone'] and (undertone is None or settings['undertone']=='neutral' or undertone==settings['undertone']): vs.append(v)
   if vs:
    p['matching_variant_ids']=[v['id'] for v in vs];p['display_variant_id']=vs[0]['id'];suggestions.append(public_product(p))
  suggestions.sort(key=lambda p:(p['brand'],norm(p['name'])))
  return jsonify(palette=palette,settings=settings,shade_suggestions=suggestions[:12],shade_suggestion_count=len(suggestions),
   note='Foundation suggestions use broad, editorial interpretations of published shade names. They are not measured or calibrated matches. Numeric-only shades are not inferred; swatch before purchasing.')

 @app.get('/api/ai/status')
 def ai_status():
  installed=ollama_installed_models()
  return jsonify(
   cli_installed=ollama_cli_installed(),
   server_running=installed is not None,
   installed_models=installed or [],
   active_model=ACTIVE_MODEL['id'],
   active_model_ready=bool(installed and any(ACTIVE_MODEL['id']==name or name.startswith(ACTIVE_MODEL['id']+':') or ACTIVE_MODEL['id'].startswith(name) for name in installed)),
   curated_models=CURATED_MODELS,
   job={k:v for k,v in ai_job.items()},
  )

 @app.post('/api/ai/pull')
 @admin_required
 def ai_pull():
  model=str(body().get('model','')).strip()
  if model not in CURATED_MODEL_IDS: return error('Choose one of the listed models.')
  if not ollama_cli_installed(): return error('Ollama is not installed on this machine. Install it from ollama.com first.')
  if not ai_lock.acquire(blocking=False): return error('A model download is already running.',409)
  ai_job.update(running=True,message=f'Starting download of {model}…',ok=None,model=model,finished_at=None)

  def run_pull():
   try:
    proc=subprocess.Popen(['ollama','pull',model],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    buf=''
    while True:
     ch=proc.stdout.read(1)
     if ch=='' and proc.poll() is not None: break
     if ch in ('\r','\n'):
      if buf.strip(): ai_job['message']=buf.strip()
      buf=''
     else: buf+=ch
    if buf.strip(): ai_job['message']=buf.strip()
    code=proc.wait()
    if code==0:
     save_active_model(model)
     ai_job.update(ok=True,message=f'{model} is ready.')
    else:
     ai_job.update(ok=False,message=f'ollama pull exited with code {code}.')
   except Exception as exc:
    ai_job.update(ok=False,message=f'Download failed: {exc}')
   finally:
    ai_job.update(running=False,finished_at=int(time.time()))
    ai_lock.release()

  threading.Thread(target=run_pull,daemon=True).start()
  return jsonify(ok=True),202

 @app.post('/api/chat')
 def chat():
  data=body();message=str(data.get('message','')).strip()
  if not message: return error('Say something to search the catalogue.')
  if len(message)>300: return error('Keep messages under 300 characters.')
  text=norm(message);words=set(re.findall(r'[a-z0-9]+',text))

  brand=next((b['id'] for b in BRANDS if norm(b['name']) in text or b['id'] in words),None)
  category=next((cid for cid,name in CATEGORIES.items() if norm(name) in text or cid in words or any(w in words for w in norm(name).split())),None)
  matched_concern=None
  if not category:
   concern_words={'dry':'skincare','oily':'skincare','acne':'skincare','pimple':'skincare','sensitive':'skincare',
    'moisturizer':'skincare','moisturiser':'skincare','moisturizing':'skincare','serum':'skincare','sunscreen':'skincare',
    'spf':'skincare','cleanser':'skincare','toner':'skincare','skin':'skincare','hair':'haircare','shampoo':'haircare',
    'conditioner':'haircare','scalp':'haircare','perfume':'fragrance','scent':'fragrance','deodorant':'fragrance',
    'body':'bodycare','lotion':'bodycare','soap':'bodycare'}
   matched_concern=next((k for k in concern_words if k in words),None)
   if matched_concern: category=concern_words[matched_concern]
  region_words={'face':'face','lips':'lips','lip':'lips','eyes':'eyes','eye':'eyes','brows':'brows','brow':'brows'}
  region=None if category else next((v for k,v in region_words.items() if k in words and v in REGIONS),None)
  finish=next((f for f in ('matte','radiant','satin') if f in words),None)
  m=re.search(r'(?:under|below|less than|upto|up to|within)\s*(?:inr|rs\.?|₹)?\s*(\d+)',text)
  max_p=float(m.group(1))*100 if m else None
  want_available=bool({'stock','available','instock'}&words)

  # Whatever's left after stripping recognized entities becomes a free-text search,
  # the same way /api/products' `q` filter works, so words like "compact" or a
  # product name still narrow the results even without an exact category match.
  stop={'under','below','less','than','upto','up','to','within','stock','available','instock',
   'rs','inr','a','an','the','for','with','in','of','and','me','show','find','looking','want',
   'need','please','some','any','products','product','have','has','get','give','can','you','i',
   'hi','hey','hello','yo','sup','thanks','thank','ok','okay','what','do','does','is','are',
   'my','your','something','looking','like','how','are','there'}
  consumed=set(stop)
  if brand: consumed|=set(norm(BY_BRAND[brand]['name']).split())|{brand}
  if category: consumed|=set(norm(CATEGORIES[category]).split())|{category}
  if matched_concern: consumed.add(matched_concern)
  if finish: consumed.add(finish)
  if m: consumed|=set(m.group(0).split())
  leftover=[w for w in re.findall(r'[a-z0-9]+',text) if w not in consumed and not w.isdigit() and len(w)>=3]

  clauses=['active=1'];params=[]
  if brand: clauses.append('brand=?');params.append(brand)
  if category: clauses.append('category=?');params.append(category)
  elif region:
   cs=REGIONS[region];clauses.append('category IN ('+','.join('?' for _ in cs)+')');params.extend(cs)
  if finish: clauses.append('finish=?');params.append(finish)
  for word in leftover:
   clauses.append("search LIKE ? ESCAPE '\\'");params.append('%'+word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')

  understood=any([brand,category,region,finish,max_p,want_available,leftover])
  rows=db().execute('SELECT data FROM catalog_products WHERE '+' AND '.join(clauses),params).fetchall() if understood else []
  products=[]
  for row in rows:
   p=json.loads(row['data']);vs=p['variants']
   if max_p is not None: vs=[v for v in vs if v.get('price') is not None and v['price']<=max_p]
   if want_available: vs=[v for v in vs if v.get('available') is True]
   if not vs: continue
   p['matching_variant_ids']=[v['id'] for v in vs]
   p['display_variant_id']=next((v['id'] for v in vs if v.get('available') is True),vs[0]['id'])
   products.append(p)
  products.sort(key=lambda p:norm(p['name']))
  results=[public_product(p) for p in products[:8]]

  if not understood:
   reply='I can help you search the catalogue — try "matte lipsticks under 500" or "Lakmé foundations in stock".'
  elif not results:
   reply="I couldn't find anything matching that. Try a different brand, category or a higher budget."
  else:
   bits=[]
   if brand: bits.append(BY_BRAND[brand]['name'])
   if category: bits.append(CATEGORIES[category].lower())
   elif region: bits.append(region)
   if finish: bits.append(finish+' finish')
   if max_p is not None: bits.append(f'under ₹{max_p/100:,.0f}')
   if leftover and not (brand or category or region): bits.append('"'+' '.join(leftover)+'"')
   reply=f"Found {len(results)} match{'es' if len(results)!=1 else ''} for {' · '.join(bits) or message}."
  ai_reply=ollama_reply(message,results)
  used_ai=ai_reply is not None
  if used_ai: reply=ai_reply
  return jsonify(reply=reply,products=results,understood=understood,ai=used_ai)

 return app

OLLAMA_URL=os.environ.get('GLOWSYNC_OLLAMA_URL','http://127.0.0.1:11434/api/generate')
OLLAMA_BASE=OLLAMA_URL.rsplit('/api/',1)[0]
AI_SETTINGS_PATH=ROOT/'data'/'ai-settings.json'
# There is no cosmetics-specific local language model publicly available.
# These are general-purpose instruction models picked only for being small
# enough to run on an ordinary laptop; sizes are approximate download sizes.
CURATED_MODELS=[
 {'id':'llama3.2','label':'Llama 3.2 (3B)','approx_size':'~2.0 GB','note':'Good general-purpose default; fast on most laptops.'},
 {'id':'phi3:mini','label':'Phi-3 Mini (3.8B)','approx_size':'~2.2 GB','note':'Strong at following instructions; very fast.'},
 {'id':'qwen2.5:3b','label':'Qwen 2.5 (3B)','approx_size':'~1.9 GB','note':'Solid all-rounder with good multilingual support.'},
 {'id':'gemma2:2b','label':'Gemma 2 (2B)','approx_size':'~1.6 GB','note':'Smallest and fastest; somewhat lower answer quality.'},
]
CURATED_MODEL_IDS={m['id'] for m in CURATED_MODELS}

def load_active_model():
 try: return json.loads(AI_SETTINGS_PATH.read_text(encoding='utf-8')).get('model') or os.environ.get('GLOWSYNC_OLLAMA_MODEL','llama3.2')
 except (FileNotFoundError,ValueError): return os.environ.get('GLOWSYNC_OLLAMA_MODEL','llama3.2')

def save_active_model(model_id):
 AI_SETTINGS_PATH.write_text(json.dumps({'model':model_id}),encoding='utf-8')
 ACTIVE_MODEL['id']=model_id

ACTIVE_MODEL={'id':load_active_model()}

def ollama_cli_installed():
 return shutil.which('ollama') is not None

def ollama_installed_models(timeout=2):
 """Returns the list of model names Ollama already has pulled, or None if
 the Ollama server isn't reachable at all (not installed, not running)."""
 try:
  with urlopen(OLLAMA_BASE+'/api/tags',timeout=timeout) as resp:
   data=json.loads(resp.read().decode('utf-8'))
  return [m.get('name') for m in data.get('models',[])]
 except Exception:
  return None

def ollama_reply(message,products,timeout=8):
 """Ask a locally-running Ollama model to write a short, natural reply about
 the already-selected real products. Returns None on any failure (Ollama not
 installed, not running, wrong model, slow, bad response) so the caller can
 fall back to the deterministic templated reply. The model is never allowed
 to introduce products of its own: it only receives names/brand/price/
 availability already pulled from the real catalogue, and is instructed to
 stick to that list, so this cannot hallucinate new products into the UI
 (the product cards shown to the user always come from the real query, not
 from the model's text)."""
 if not products: return None
 lines=[]
 for p in products[:8]:
  v=next((x for x in p['variants'] if x['id']==p.get('display_variant_id')),p['variants'][0] if p['variants'] else {})
  price=f"₹{v['price']/100:,.0f}" if v.get('price') is not None else 'price unavailable'
  stock='in stock' if v.get('available') else ('out of stock' if v.get('available') is False else 'stock unknown')
  lines.append(f"- {p['name']} ({p['brand']}, {CATEGORIES.get(p['category'],p['category'])}, {price}, {stock})")
 prompt=('You are the in-app assistant for GlowSync, a cosmetics catalogue app. '
  'A user asked: "'+message+'"\n\n'
  'Here are the ONLY products you may mention, already matched from the real catalogue:\n'
  +'\n'.join(lines)+
  '\n\nWrite a short, warm, 2-3 sentence reply recommending from this list only. '
  'Never mention a product, brand, price or shade that is not in the list above. '
  'Do not invent availability, ratings or claims not shown above. '
  'Do not use markdown.')
 try:
  payload=json.dumps({'model':ACTIVE_MODEL['id'],'prompt':prompt,'stream':False}).encode('utf-8')
  req=Request(OLLAMA_URL,data=payload,headers={'Content-Type':'application/json'},method='POST')
  with urlopen(req,timeout=timeout) as resp:
   out=json.loads(resp.read().decode('utf-8'))
  text=str(out.get('response','')).strip()
  return text or None
 except Exception:
  return None

def source_error(exc):
 from urllib.error import HTTPError,URLError
 if isinstance(exc,HTTPError): return f'Source returned HTTP {exc.code}; saved data retained.'
 if isinstance(exc,(URLError,TimeoutError,OSError)): return 'Source could not be reached; saved data retained.'
 if isinstance(exc,ValueError): return str(exc)
 return 'Source format could not be verified; saved data retained.'

def apply_price_alerts(db,p,now):
 """Only freshly obtained, exact, in-stock INR variants may create alerts."""
 if p.get('source_kind')!='feed' or p.get('currency')!='INR' or abs(now-p.get('observed_at',0))>300: return
 for v in p['variants']:
  if v.get('available') is not True or v.get('price') is None: continue
  price=v['price']
  db.execute('INSERT OR IGNORE INTO price_history VALUES(?,?,?,?)',(p['id'],v['id'],price,now))
  for watch in db.execute('SELECT * FROM wishlist WHERE product_id=? AND variant_id=? AND watching=1',(p['id'],v['id'])).fetchall():
   baseline=watch['baseline']
   if baseline is not None and price<baseline:
    title=f'{p["name"]} · {v["name"]}: ₹{price/100:,.2f} (was ₹{baseline/100:,.2f} since your watch began)'
    db.execute('INSERT INTO notifications VALUES(?,?,?,?,0)',(str(uuid.uuid4()),watch['user_id'],title,now))
   if baseline is None or price<baseline:
    db.execute('UPDATE wishlist SET baseline=? WHERE id=?',(price,watch['id']))