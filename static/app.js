import {initStudio, stopCamera} from './studio.js';

const $ = (q,root=document)=>root.querySelector(q);
const $$ = (q,root=document)=>[...root.querySelectorAll(q)];
const esc = value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeLink = value=>/^https:\/\//i.test(value||'')?esc(value):'#';
const money = value=>value==null?'Check brand for price':new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:value%100?2:0}).format(value/100);
const date = value=>value?new Date(value*1000).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}):'Date unavailable';
const state={csrf:'',user:null,meta:null,route:'home',page:1,filters:{q:'',brand:'',category:'',max_price:'',finish:'',region:'',available:'',sort:'featured'},products:new Map(),wishlist:[],compare:new Map(),request:0,register:false,pending:null,studioRegion:'face',jobTimer:null};
let toastTimer;

async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf,...options.headers},credentials:'same-origin'});
 const data=await response.json().catch(()=>({error:'The server returned an unreadable response.'}));
 if(!response.ok)throw new Error(data.error||'Request failed. Please try again.');
 return data;
}
function toast(message){const el=$('#toast');el.textContent=message;el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,5000);}
function showDialog(id){const d=$(id);if(!d.open)d.showModal();}
function empty(title,description,link=true){return `<div class="empty-state"><span>✧</span><h3>${esc(title)}</h3><p>${esc(description)}</p>${link?'<a class="button primary" href="#catalogue">Explore the collection ↗</a>':''}</div>`;}
async function busy(button,task){const label=button.textContent;button.disabled=true;try{return await task();}catch(error){toast(error.message);}finally{button.disabled=false;button.textContent=label;}}
function userNeeded(action){if(state.user){action();return;}state.pending=action;openAuth();}
function openAuth(){state.register=false;renderAuth();showDialog('#auth-dialog');}
function renderAuth(){
 $('#auth-title').textContent=state.register?'Make yourself at home.':'Welcome back.';
 $('#auth-submit').textContent=state.register?'Create account →':'Sign in →';
 $('#name-field').hidden=!state.register;$('#auth-name').required=state.register;
 $('#auth-password').minLength=state.register?10:1;$('#auth-password').autocomplete=state.register?'new-password':'current-password';
 $('#auth-description').textContent=state.register?'Save your favourites with a password of 10 or more characters.':'Save the shades and products you love.';
 $('#toggle-auth').textContent=state.register?'Already here? Sign in':'New here? Create an account';$('#auth-error').textContent='';
}
function authUI(){
 $('#account-button').textContent=state.user?state.user.name.split(' ')[0]+' ↗':'Sign in ↗';
 $('#manager-controls').hidden=!state.user?.is_admin;
 $('#manager-help').textContent=state.user?.is_admin?'Refresh official catalogues or add product listings from a supplier file. A failed refresh keeps your previous catalogue.':'The local owner account can refresh connected brands and import supplier catalogues. The first account created is the owner.';
 if(state.user?.preferences)for(const [key,value] of Object.entries(state.user.preferences)){const input=$(`[name="${key}"]`,$('#studio-form'));if(input&&[...input.options].some(o=>o.value===value))input.value=value;}
}
async function loadMeta(){state.meta=await api('/api/meta');buildFilters();renderBrands();}
function buildFilters(){
 const f=state.filters;
 $('#brand-filter').innerHTML='<option value="">All brands</option>'+state.meta.brands.filter(b=>b.count).map(b=>`<option value="${esc(b.id)}">${esc(b.name)} (${b.count})</option>`).join('');
 const allowed=f.region?state.meta.regions[f.region]:null;
 $('#category-filter').innerHTML='<option value="">All categories</option>'+state.meta.categories.filter(c=>c.count&&(!allowed||allowed.includes(c.id))).map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
 syncFilterInputs();
}
function syncFilterInputs(){for(const [id,key] of Object.entries({'search':'q','brand-filter':'brand','category-filter':'category','budget-filter':'max_price','finish-filter':'finish','sort':'sort'}))$('#'+id).value=state.filters[key]||'';$('#stock-filter').checked=state.filters.available==='1';}
function resetFilters(){state.filters={q:'',brand:'',category:'',max_price:'',finish:'',region:'',available:'',sort:'featured'};state.page=1;buildFilters();}
function routeLink(filters={}){const q=new URLSearchParams(Object.entries(filters).filter(([,v])=>v));return '#catalogue'+(q.size?'?'+q:'');}
async function route(){
 const [path,query]=location.hash.slice(1).split('?');
 const next=['home','catalogue','studio','saved','brands'].includes(path)?path:'home';
 if(state.route==='studio'&&next!=='studio')stopCamera();
 state.route=next;state.page=1;
 if(query&&next==='catalogue'){resetFilters();const params=new URLSearchParams(query);for(const k in state.filters)if(params.has(k))state.filters[k]=params.get(k);buildFilters();}
 $$('[data-pages]').forEach(el=>el.hidden=!el.dataset.pages.split(' ').includes(next));
 $$('nav a').forEach(el=>el.classList.toggle('active',el.hash.slice(1)===next));
 document.title=`${{home:'Beauty, in sync with you',catalogue:'Discover your favourites',studio:'Your shade studio',saved:'Saved with love',brands:'The brands'}[next]} · GlowSync`;
 window.scrollTo({top:0,behavior:'instant'});
 if(next==='home'||next==='catalogue')await loadProducts();
 if(next==='saved')await renderSaved();
 if(next==='brands')renderBrands();
}
async function loadProducts(){
 const seq=++state.request;
 const home=state.route==='home';
 const params=new URLSearchParams({...(!home?state.filters:{}),page:String(state.page),limit:home?'12':'24'});
 $('#product-grid').innerHTML='<div class="loading">Finding your next favourite…</div>';
 $('#catalogue-title').textContent=home?'Your next beauty favourite.':'Find your kind of beauty.';
 $('#catalogue-eyebrow').textContent=home?'THE GLOWSYNC EDIT':'THE BEAUTY COLLECTION';
 $('#filter-summary').hidden=home||!state.filters.region;
 if(!$('#filter-summary').hidden)$('#filter-summary').innerHTML=`Exploring ${esc(state.filters.region==='brows'?'eyebrows':state.filters.region)} <button class="plain-button" id="clear-region">Browse every feature ×</button>`;
 try{
  const data=await api('/api/products?'+params);if(seq!==state.request)return;
  $('#catalogue-count').textContent=`${data.total.toLocaleString('en-IN')} product listings`;
  $('#results-note').textContent=data.unknown_price_excluded?`${data.unknown_price_excluded} listings with unknown prices are excluded from your budget filter.`:'';
  $('#product-grid').innerHTML=data.products.length?data.products.map(p=>card(p)).join(''):empty('A little room to explore.','No products match this combination. Try another brand, category or budget.',false);
  state.page=data.page;
  $('#pagination').innerHTML=home?'<a class="button secondary" href="#catalogue">Explore the full collection ↗</a>':`<button data-page="${data.page-1}" ${data.page<=1?'disabled':''}>← Previous</button><span>Page ${data.page} of ${data.pages}</span><button data-page="${data.page+1}" ${data.page>=data.pages?'disabled':''}>Next →</button>`;
  bindImageFallbacks($('#product-grid'));
 }catch(error){if(seq===state.request){$('#product-grid').innerHTML=empty('The collection needs a moment.',error.message,false);$('#pagination').innerHTML='';}}
}
function preferredVariant(p){return p.variants.find(v=>v.id===p.display_variant_id)||p.variants[0];}
function isSaved(pid,vid){return state.wishlist.some(w=>w.product_id===pid&&w.variant_id===vid);}
function imageMarkup(p,extra=''){
 const src=p.local_image||p.image;
 if(!src)return '<div class="photo-fallback"><span>✳</span>Photo unavailable</div>';
 return `<img src="${esc(src)}" alt="${esc(p.name)} — range photo" loading="lazy" referrerpolicy="no-referrer" ${extra}>`;
}
function bindImageFallbacks(root){$$('img',root).forEach(img=>{const fallback=()=>{img.outerHTML='<div class="photo-fallback"><span>✳</span>Photo unavailable</div>';};img.addEventListener('error',fallback,{once:true});if(img.complete&&!img.naturalWidth)fallback();});}
function card(p){
 state.products.set(p.id,p);const v=preferredVariant(p),saved=isSaved(p.id,v.id);
 return `<article class="product-card" data-product="${esc(p.id)}"><button class="product-image-link" data-detail="${esc(p.id)}" aria-label="View ${esc(p.name)}">${imageMarkup(p)}</button><button class="save-button ${saved?'saved':''}" data-save="${esc(p.id)}" aria-label="${saved?'Remove saved variant':'Save '+esc(p.name)}" aria-pressed="${saved}">${saved?'♥':'♡'}</button><p class="product-brand">${esc(p.brand_name)}</p><button class="product-name" data-detail="${esc(p.id)}">${esc(p.name)}</button><select class="variant-select" data-variant="${esc(p.id)}" aria-label="Variant for ${esc(p.name)}">${p.variants.filter(v=>!p.matching_variant_ids||p.matching_variant_ids.includes(v.id)).map(x=>`<option value="${esc(x.id)}" ${v.id===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select><p class="product-price">${money(v.price)} ${v.mrp?`<del>${money(v.mrp)}</del>`:''}</p><p class="product-date">${quoteLabel(p,v)}</p><div class="product-bottom"><a href="${safeLink(v.url||p.url)}" target="_blank" rel="noopener noreferrer">Visit brand ↗</a><label class="compare-checkbox"><input type="checkbox" data-compare="${esc(p.id)}" ${state.compare.has(p.id)?'checked':''}> Compare</label></div></article>`;
}
function quoteLabel(p,v){if(v.price==null)return 'Price & stock: check the brand';return `Source quote · ${esc(date(p.observed_at))}${v.available===false?' · Sold out at check':''}`;}
async function toggleSave(pid,vid){
 const saved=state.wishlist.find(w=>w.product_id===pid&&w.variant_id===vid);
 if(saved)await api('/api/wishlist/'+saved.id,{method:'DELETE'});
 else await api('/api/wishlist',{method:'POST',body:JSON.stringify({product_id:pid,variant_id:vid})});
 await loadWishlist();toast(saved?'Removed from your saved collection.':'Saved to your beauty collection.');
 if(state.route==='saved')await renderSaved(false);
 updateSaveButtons();
}
function updateSaveButtons(){
 $$('[data-save]').forEach(btn=>{const pid=btn.dataset.save,container=btn.closest('[data-product]');const vid=container?.querySelector('[data-variant]')?.value||state.products.get(pid)?.variants[0]?.id;
 const saved=isSaved(pid,vid);btn.classList.toggle('saved',saved);btn.setAttribute('aria-pressed',String(saved));
 if(btn.classList.contains('save-button')){btn.textContent=saved?'♥':'♡';btn.setAttribute('aria-label',saved?'Remove saved variant':'Save variant');}else btn.textContent=saved?'Remove from saved ♡':'Save this variant ♡';});
}
async function loadWishlist(){
 state.wishlist=state.user?(await api('/api/wishlist')).items:[];
 $('#saved-count').hidden=!state.wishlist.length;$('#saved-count').textContent=String(state.wishlist.length);
}
async function openProduct(pid){
 try{
 const data=await api('/api/products/'+encodeURIComponent(pid));const p=data.product;state.products.set(pid,p);
 const visible=$(`[data-product="${CSS.escape(pid)}"] [data-variant]`),vid=visible?.value||p.variants[0].id;
 const v=p.variants.find(x=>x.id===vid)||p.variants[0];
 $('#product-detail').innerHTML=`<div class="product-detail-layout" data-product="${esc(pid)}"><div>${imageMarkup(p,'class="detail-image"')}<p class="small-copy muted">Range photo. Selected shade and packaging may differ.</p></div><div><p class="product-brand">${esc(p.brand_name)} · ${esc(p.category_name)}</p><h2>${esc(p.name)}</h2><label>Choose a variant<select data-variant="${esc(pid)}">${p.variants.map(x=>`<option value="${esc(x.id)}" ${x.id===v.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label><p class="product-price detail-price">${money(v.price)} ${v.mrp?`<del>${money(v.mrp)}</del>`:''}</p><p class="detail-stock">${stockLabel(p,v)}</p><p class="product-date detail-meta">${quoteLabel(p,v)}<br>Prices may change. Confirm availability on the brand site.</p><div class="detail-buttons"><a class="button primary detail-visit" href="${safeLink(v.url||p.url)}" target="_blank" rel="noopener noreferrer">Visit brand ↗</a><button class="button secondary" data-save="${esc(pid)}">Save this variant ♡</button></div><p class="small-copy muted" style="margin-top:18px">${p.watch_supported?'Exact-variant price watches are available after saving.':'No connected price feed. Prices and additional shades are available on the source site.'}</p><a class="text-link" href="${safeLink(p.source_url||p.url)}" target="_blank" rel="noopener noreferrer">Product data source ↗</a></div></div>`;
 bindImageFallbacks($('#product-detail'));updateSaveButtons();showDialog('#product-dialog');
 }catch(error){toast(error.message);}
}
function stockLabel(p,v){if(v.available==null)return 'Availability not verified';return `${v.available?'Reported in stock':'Reported sold out'} · ${date(p.observed_at)}`;}
function changeVariant(select){
 const pid=select.dataset.variant,p=state.products.get(pid),v=p.variants.find(v=>v.id===select.value),box=select.closest('[data-product]');if(!v||!box)return;
 $('.product-price',box).innerHTML=`${money(v.price)} ${v.mrp?`<del>${money(v.mrp)}</del>`:''}`;
 $('.product-date',box).innerHTML=quoteLabel(p,v);
 const visit=$('.product-bottom a,.detail-visit',box);if(visit)visit.href=v.url||p.url;
 const stock=$('.detail-stock',box);if(stock)stock.textContent=stockLabel(p,v);
 if(state.compare.has(pid)){state.compare.set(pid,{p,vid:v.id});renderCompareBar();}
 updateSaveButtons();
}
function renderCompareBar(){
 $('#compare-bar').hidden=!state.compare.size;$('#compare-count').textContent=`${state.compare.size} of 4 products selected`;
 $$('[data-compare]').forEach(c=>c.checked=state.compare.has(c.dataset.compare));
}
function openCompare(){
 if(!state.compare.size)return;
 const rows=[...state.compare.values()].map(({p,vid})=>({p,v:p.variants.find(x=>x.id===vid)||p.variants[0]}));
 const cells=(label,fn)=>`<tr><th>${label}</th>${rows.map(r=>`<td>${fn(r)}</td>`).join('')}</tr>`;
 $('#compare-content').innerHTML='<div class="comparison-scroll"><table class="comparison-table"><tbody>'+cells('Product',({p})=>`<strong>${esc(p.name)}</strong>`)+cells('Brand',({p})=>esc(p.brand_name))+cells('Variant',({v})=>esc(v.name))+cells('Category',({p})=>esc(p.category_name))+cells('Price',({v})=>money(v.price))+cells('Source date',({p})=>esc(date(p.observed_at)))+cells('Stock at check',({v})=>v.available==null?'Unknown':v.available?'In stock':'Sold out')+cells('Explore',({p,v})=>`<a class="text-link" href="${safeLink(v.url||p.url)}" target="_blank" rel="noopener noreferrer">Visit brand ↗</a>`)+ '</tbody></table></div><p class="small-copy muted">These are dated quotes for the selected variants, not a live retailer price comparison.</p>';
 showDialog('#compare-dialog');
}
async function renderSaved(reload=true){
 $('#check-prices').hidden=!state.user;
 if(!state.user){$('#saved-grid').innerHTML=empty('A space for your favourites.','Sign in to keep the shades, products and price watches you love.',false)+'<button class="button primary" id="saved-signin">Sign in to save →</button>';return;}
 if(reload)await loadWishlist();
 $('#saved-grid').innerHTML=state.wishlist.length?state.wishlist.map(w=>{
 const p=w.product,v=w.variant;if(!p||!v)return `<article class="saved-card"><h3>Previously saved product</h3><p class="muted small-copy">This variant is not in the current catalogue. Your saved record was preserved.</p><button data-remove-wish="${esc(w.id)}" class="plain-button">Remove saved item</button></article>`;
 state.products.set(p.id,p);
 return `<article class="saved-card"><div class="saved-top">${imageMarkup(p)}<div><p class="product-brand">${esc(p.brand_name)}</p><button class="product-name" data-detail="${esc(p.id)}">${esc(p.name)}</button><p class="saved-variant">${esc(v.name)}</p><p class="product-price">${money(v.price)}</p></div></div><p class="product-date">${quoteLabel(p,v)}${!p.active?' · No longer in the active feed':''}</p><div class="saved-actions">${p.watch_supported?`<label><input type="checkbox" data-watch="${esc(w.id)}" ${w.watching?'checked':''}> Watch this price</label>`:'<span class="muted small-copy">Price feed not connected</span>'}<button class="plain-button" data-remove-wish="${esc(w.id)}">Remove</button></div>${w.watching?`<p class="small-copy muted">${w.baseline==null?'Awaiting your first live check.':'Lowest watched price: '+money(w.baseline)}</p>`:''}</article>`;
 }).join(''):empty('Your collection starts here.','Save a product variant with the heart button. It will be waiting here.');
 bindImageFallbacks($('#saved-grid'));
}
function renderBrands(){
 if(!state.meta)return;
 const m=state.meta,connected=m.brands.filter(b=>b.count).length;
 $('#catalogue-totals').innerHTML=`<div class="total-stat"><b>${m.total.toLocaleString('en-IN')}</b><span>product listings</span></div><div class="total-stat"><b>${m.variants.toLocaleString('en-IN')}</b><span>published variants & range entries</span></div><div class="total-stat"><b>${connected}</b><span>brands with products</span></div>`;
 $('#brand-grid').innerHTML=m.brands.map(b=>{
 const coverage=b.feed_base?(b.status.complete?'Public feed imported':'Public feed connected'):b.count?'Selected product ranges':'Supplier catalogue needed';
 return `<article class="brand-card"><span class="coverage-tag ${!b.feed_base?'limited':''}">${coverage}</span><h3>${esc(b.name)}</h3><p class="brand-count">${b.count?b.count.toLocaleString('en-IN')+' product listings':'No products imported yet'}</p><p>${b.status.observed_at?'Imported '+esc(date(b.status.observed_at)):b.count?'Verified source links; price feed not connected.':'A verified supplier file is needed to list products here.'}</p><div class="brand-links">${b.count?`<a class="text-link" href="${routeLink({brand:b.id})}">Explore ↗</a>`:''}<a href="${safeLink(b.website)}" target="_blank" rel="noopener noreferrer">Brand site ↗</a>${b.feed_base&&state.user?.is_admin?`<button data-sync-brand="${esc(b.id)}" class="plain-button">Refresh</button>`:''}</div></article>`;
 }).join('');
}
async function loadNotifications(open=false){
 if(!state.user){if(open)openAuth();return;}
 const {items}=await api('/api/notifications');const unread=items.filter(n=>!n.seen).length;
 $('#alert-count').hidden=!unread;$('#alert-count').textContent=String(unread);
 $('#notifications-list').innerHTML=items.length?items.map(n=>`<div class="notification ${!n.seen?'unread':''}"><p>${esc(n.title)}</p><small>${esc(date(n.created))}</small></div>`).join(''):'<p>No price drops yet. Save a connected product, enable its watch and check prices to set a starting point.</p>';
 if(open)showDialog('#notifications-dialog');
}
async function startJob(path,payload={}){
 await api(path,{method:'POST',body:JSON.stringify(payload)});toast('Refresh started. You can keep browsing.');pollJob();
}
async function pollJob(){
 clearTimeout(state.jobTimer);
 try{
 const job=await api('/api/jobs');const html=`<strong>${esc(job.message)}</strong>${job.results.length?'<ul>'+job.results.map(r=>`<li class="${r.ok?'status-success':'status-error'}">${r.ok?'✓':'!'} ${r.brand?esc(r.brand)+': ':''}${esc(r.message)}</li>`).join('')+'</ul>':''}`;
 for(const id of ['catalogue-job','saved-job']){$('#'+id).innerHTML=html;$('#'+id).hidden=false;}
 if(job.running)state.jobTimer=setTimeout(pollJob,1500);
 else{await Promise.all([loadMeta(),loadNotifications(),loadWishlist()]);if(state.route==='saved')await renderSaved(false);if(['home','catalogue'].includes(state.route))await loadProducts();}
 }catch(error){toast(error.message);}
}
async function regionProducts(region){
 state.studioRegion=region;
 $$('.region-buttons button').forEach(b=>{b.classList.toggle('active',b.dataset.region===region);b.setAttribute('aria-pressed',String(b.dataset.region===region));});
 $('#studio-region-title').textContent=`A little love for your ${region==='brows'?'brows':region}.`;
 let grid=$('#studio-product-grid');if(!grid){grid=document.createElement('div');grid.id='studio-product-grid';grid.className='product-grid';grid.style.marginTop='26px';$('#studio-page').append(grid);}
 grid.innerHTML='<div class="loading">Exploring your feature…</div>';
 try{
  if(region==='face'){
   const result=await api('/api/recommend',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData($('#studio-form'))))});
   if(region!==state.studioRegion)return;
   grid.innerHTML=`<p class="small-copy muted" style="grid-column:1/-1;margin:0">${esc(result.note)}</p>`+(result.shade_suggestions.length?result.shade_suggestions.slice(0,8).map(card).join(''):empty('No named-shade suggestions for this combination.','Try another finish, or use Explore products to browse every face product and choose a shade yourself.',false));
  }else{
   const data=await api('/api/products?'+new URLSearchParams({region,limit:'8',finish:$('#studio-finish').value==='any'?'':$('#studio-finish').value}));
   if(region!==state.studioRegion)return;
   grid.innerHTML=data.products.length?data.products.map(card).join(''):empty('Try a different finish.','No connected products match this feature and finish.',false);
  }
  bindImageFallbacks(grid);
 }catch(e){grid.innerHTML=empty('Products could not load.',e.message,false);}
}

document.addEventListener('click',async event=>{
 const close=event.target.closest('[data-close]');if(close){close.closest('dialog').close();return;}
 const detail=event.target.closest('[data-detail]');if(detail){await openProduct(detail.dataset.detail);return;}
 const save=event.target.closest('[data-save]');if(save){const pid=save.dataset.save,box=save.closest('[data-product]');const vid=box?.querySelector('[data-variant]')?.value||state.products.get(pid)?.variants[0].id;userNeeded(()=>busy(save,()=>toggleSave(pid,vid)));return;}
 const cat=event.target.closest('[data-category]');if(cat){location.hash=routeLink({category:cat.dataset.category});return;}
 const region=event.target.closest('[data-region]');if(region){await regionProducts(region.dataset.region);return;}
 const pager=event.target.closest('[data-page]');if(pager&&!pager.disabled){state.page=Number(pager.dataset.page);await loadProducts();$('#catalogue-page').scrollIntoView({behavior:'smooth'});return;}
 const remove=event.target.closest('[data-remove-wish]');if(remove){await busy(remove,async()=>{await api('/api/wishlist/'+remove.dataset.removeWish,{method:'DELETE'});await loadWishlist();await renderSaved(false);});return;}
 const sync=event.target.closest('[data-sync-brand]');if(sync){await busy(sync,()=>startJob('/api/catalogue/sync',{brands:[sync.dataset.syncBrand]}));return;}
 if(event.target.id==='clear-region'){state.filters.region='';state.filters.category='';buildFilters();await loadProducts();}
 if(event.target.id==='saved-signin')openAuth();
});
document.addEventListener('change',async event=>{
 const select=event.target.closest('[data-variant]');if(select){changeVariant(select);return;}
 const comp=event.target.closest('[data-compare]');if(comp){const p=state.products.get(comp.dataset.compare);if(comp.checked&&state.compare.size>=4){comp.checked=false;toast('Compare up to four products at a time.');return;}if(comp.checked)state.compare.set(p.id,{p,vid:comp.closest('[data-product]').querySelector('[data-variant]').value});else state.compare.delete(p.id);renderCompareBar();return;}
 const watch=event.target.closest('[data-watch]');if(watch){try{await api('/api/wishlist/'+watch.dataset.watch+'/watch',{method:'PUT',body:JSON.stringify({enabled:watch.checked})});await loadWishlist();await renderSaved(false);toast(watch.checked?'Watch enabled. Check prices to set its starting point.':'Price watch disabled.');}catch(e){watch.checked=!watch.checked;toast(e.message);}}
});
for(const [id,key] of Object.entries({'brand-filter':'brand','category-filter':'category','budget-filter':'max_price','finish-filter':'finish','sort':'sort','stock-filter':'available'}))$('#'+id).addEventListener('change',()=>{state.filters[key]=id==='stock-filter'?($('#'+id).checked?'1':''):$('#'+id).value;state.page=1;if(state.route==='home'){state.route='catalogue';location.hash='catalogue';}else loadProducts();});
let searchTimer;$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);state.filters.q=$('#search').value;state.page=1;searchTimer=setTimeout(()=>{if(state.route==='home')location.hash='catalogue';else loadProducts();},280);});
$('#clear-filters').addEventListener('click',()=>{resetFilters();loadProducts();});
$('#account-button').addEventListener('click',()=>{if(!state.user){openAuth();return;}$('#profile-name').value=state.user.name;$('#profile-email').textContent=state.user.email+(state.user.is_admin?' · Local owner':'');showDialog('#account-dialog');});
$('#toggle-auth').addEventListener('click',()=>{state.register=!state.register;renderAuth();});
$('#auth-form').addEventListener('submit',async event=>{event.preventDefault();const button=$('#auth-submit');button.disabled=true;$('#auth-error').textContent='';try{const data=await api('/api/auth/'+(state.register?'register':'login'),{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});state.user=data.user;state.csrf=data.csrf;$('#auth-password').value='';$('#auth-dialog').close();authUI();renderBrands();await Promise.all([loadWishlist(),loadNotifications()]);updateSaveButtons();if(state.route==='saved')await renderSaved(false);toast('Welcome, '+state.user.name+'.');if(state.pending){const action=state.pending;state.pending=null;await action();}}catch(e){$('#auth-error').textContent=e.message;}finally{button.disabled=false;}});
$('#profile-form').addEventListener('submit',event=>{event.preventDefault();busy($('button',event.target),async()=>{const result=await api('/api/profile',{method:'PUT',body:JSON.stringify({name:$('#profile-name').value,preferences:state.user.preferences})});state.user=result.user;authUI();toast('Your name has been updated.');$('#account-dialog').close();});});
$('#logout').addEventListener('click',()=>busy($('#logout'),async()=>{const result=await api('/api/auth/logout',{method:'POST',body:'{}'});state.csrf=result.csrf;state.user=null;state.pending=null;clearTimeout(state.jobTimer);$('#account-dialog').close();$('#alert-count').hidden=true;authUI();await loadWishlist();updateSaveButtons();renderBrands();if(state.route==='saved')renderSaved(false);toast('You have signed out.');}));
$('#alerts-button').addEventListener('click',()=>loadNotifications(true).catch(e=>toast(e.message)));
$('#mark-read').addEventListener('click',()=>busy($('#mark-read'),async()=>{await api('/api/notifications/read',{method:'POST',body:'{}'});await loadNotifications();}));
$('#privacy-button').addEventListener('click',()=>showDialog('#privacy-dialog'));
$('#open-compare').addEventListener('click',openCompare);$('#clear-compare').addEventListener('click',()=>{state.compare.clear();renderCompareBar();});
$('#check-prices').addEventListener('click',()=>busy($('#check-prices'),()=>startJob('/api/prices/check')));
$('#sync-all').addEventListener('click',()=>busy($('#sync-all'),()=>startJob('/api/catalogue/sync')));
$('#import-button').addEventListener('click',()=>$('#import-file').click());
$('#import-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;await busy($('#import-button'),async()=>{if(file.size>24*1024*1024)throw new Error('Choose a JSON file smaller than 24 MB.');const text=await file.text();let data;try{data=JSON.parse(text);}catch{throw new Error('This file is not valid JSON.');}const result=await api('/api/catalogue/import',{method:'POST',body:JSON.stringify(data)});await loadMeta();toast(`${result.count} product listings imported.`);});event.target.value='';});
$('#studio-form').addEventListener('submit',event=>{event.preventDefault();busy($('button[type="submit"]',event.target),async()=>{const settings=Object.fromEntries(new FormData(event.target));const result=await api('/api/recommend',{method:'POST',body:JSON.stringify(settings)});const p=result.palette;$('#palette-result').innerHTML=`<p class="eyebrow">YOUR COLOUR DIRECTION</p><h3>${esc(p.direction)}</h3><div class="palette-swatches">${p.colours.map(c=>`<div><div class="palette-swatch" style="background:${/^#[0-9a-f]{6}$/i.test(c.hex)?c.hex:'#ddd'}"></div><small>${esc(c.label)}</small></div>`).join('')}</div><p>${esc(p.guidance)}</p><p>These colours are inspiration, not product swatches.</p>`;await regionProducts(state.studioRegion);});});
$('#save-preferences').addEventListener('click',()=>userNeeded(()=>busy($('#save-preferences'),async()=>{const result=await api('/api/profile',{method:'PUT',body:JSON.stringify({preferences:Object.fromEntries(new FormData($('#studio-form')))})});state.user=result.user;toast('Your studio preferences are saved.');})));
$('#browse-region').addEventListener('click',()=>{location.hash=routeLink({region:state.studioRegion,finish:$('#studio-finish').value==='any'?'':$('#studio-finish').value});});
let aiPollTimer=null;
function stopAiPoll(){clearTimeout(aiPollTimer);aiPollTimer=null;}
async function renderAiSetup(){
 const el=$('#ai-setup');let st;
 try{st=await api('/api/ai/status');}catch(e){el.innerHTML='';return;}
 if(st.job.running){
  el.innerHTML=`<div class="ai-setup-card"><p>Downloading ${esc(st.job.model||'model')}… ${esc(st.job.message||'')}</p></div>`;
  stopAiPoll();aiPollTimer=setTimeout(renderAiSetup,900);return;
 }
 if(st.active_model_ready){
  el.innerHTML=state.user?.is_admin
   ?`<div class="ai-setup-card ready"><p>✨ AI suggestions are on (${esc(st.active_model)}). <button type="button" class="plain-button" id="ai-change">Change model</button></p></div>`
   :'';
  $('#ai-change')?.addEventListener('click',()=>renderAiModelPicker(st,el));
  return;
 }
 if(!st.cli_installed){
  el.innerHTML=state.user?.is_admin
   ?`<div class="ai-setup-card"><p>Want AI-written suggestions instead of plain search results? Install <a href="https://ollama.com" target="_blank" rel="noopener noreferrer">Ollama</a> (free, runs locally, no account needed), then reopen this chat.</p></div>`
   :'';
  return;
 }
 if(!st.server_running){
  el.innerHTML=state.user?.is_admin
   ?'<div class="ai-setup-card"><p>Ollama is installed but doesn\'t seem to be running. Start it, then reopen this chat.</p></div>'
   :'';
  return;
 }
 if(state.user?.is_admin) renderAiModelPicker(st,el); else el.innerHTML='';
}
function renderAiModelPicker(st,el){
 el.innerHTML=`<div class="ai-setup-card"><p class="small-copy muted">No cosmetics-specific AI model exists publicly — these are small general-purpose models chosen to run on an ordinary laptop. Pick one to enable AI-written suggestions:</p><div class="ai-model-list">${
  st.curated_models.map(m=>`<button type="button" class="ai-model-option" data-pull="${esc(m.id)}"><strong>${esc(m.label)}</strong><span>${esc(m.approx_size)} — ${esc(m.note)}</span></button>`).join('')
 }</div></div>`;
 $$('[data-pull]',el).forEach(btn=>btn.addEventListener('click',async()=>{
  try{await api('/api/ai/pull',{method:'POST',body:JSON.stringify({model:btn.dataset.pull})});renderAiSetup();}
  catch(e){toast(e.message);}
 }));
}
$('#chat-button').addEventListener('click',()=>{showDialog('#chat-dialog');$('#chat-input').focus();renderAiSetup();});
$('#chat-dialog').addEventListener('close',stopAiPoll);
function chatBubble(role,html){return `<div class="chat-message chat-${role}">${html}</div>`;}
$('#chat-form').addEventListener('submit',event=>{
 event.preventDefault();const input=$('#chat-input'),message=input.value.trim();if(!message)return;
 const log=$('#chat-log');log.insertAdjacentHTML('beforeend',chatBubble('user',esc(message)));
 input.value='';input.disabled=true;log.scrollTop=log.scrollHeight;
 api('/api/chat',{method:'POST',body:JSON.stringify({message})}).then(data=>{
  const products=data.products.length?`<div class="chat-results">${data.products.map(p=>card(p)).join('')}</div>`:'';
  const badge=data.ai?'<span class="chat-ai-badge" title="Generated by a local AI model, based only on the matched products below">✨ AI</span> ':'';
  log.insertAdjacentHTML('beforeend',chatBubble('bot',badge+esc(data.reply)+products));
  bindImageFallbacks(log);updateSaveButtons();log.scrollTop=log.scrollHeight;
 }).catch(e=>{log.insertAdjacentHTML('beforeend',chatBubble('bot',esc(e.message)));log.scrollTop=log.scrollHeight;
 }).finally(()=>{input.disabled=false;input.focus();});
});
window.addEventListener('hashchange',()=>route().catch(e=>toast(e.message)));
setInterval(()=>{if(state.user&&!document.hidden&&state.wishlist.some(w=>w.watching))startJob('/api/prices/check').catch(()=>{});},300000);

async function init(){
 try{const sessionData=await api('/api/session');state.csrf=sessionData.csrf;state.user=sessionData.user;await loadMeta();authUI();await Promise.all([loadWishlist(),loadNotifications()]);initStudio({onRegion:regionProducts,onTone:tone=>{$('#tone').value=tone;},onError:toast});await route();if(state.user)api('/api/jobs').then(j=>{if(j.running)pollJob();}).catch(()=>{});}catch(e){$('#product-grid').innerHTML=empty('GlowSync could not connect.',e.message+' Keep the terminal running and reload this page.',false);toast(e.message);}
}
init();