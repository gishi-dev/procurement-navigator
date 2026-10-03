const $ = id => document.getElementById(id);
const money = n => '¥' + n.toLocaleString('ja-JP');
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let suppliers = [];
let running = false, lastFrame = '', lastOffers = '', lastEvents = '', previousId = '';

async function request(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '操作を続けられませんでした');
  return data;
}

function placeholder(name, i, label) {
  return `<div class="offer-placeholder"><span>${String(i+1).padStart(2,'0')}</span><div><b>${esc(name)}</b><p>${label}</p></div><i>—</i></div>`;
}

function render(state) {
  if (state.status === 'idle') return;
  if (previousId !== state.id) {
    $('sku').value = state.goal.sku; $('quantity').value = state.goal.quantity; $('deadline').value = state.goal.deadline;
    document.querySelector(`input[name=mode][value=${state.mode}]`).checked = true;
    previousId = state.id; lastFrame = ''; lastOffers = ''; lastEvents = '';
    $('recommendation').innerHTML = '<div class="recommendation-top"><span class="eyebrow">YOUR NEXT DECISION</span><span>↗</span></div><h3>候補を集めています。</h3><p>必要数と希望日を満たす仕入先を比較します。</p>';
    $('frame').hidden = true; $('browser-empty').hidden = false;
    $('activity-title').textContent = '調査を始めています'; $('activity-detail').textContent = '仕入先の画面を開きます。';
  }
  running = state.status === 'running';
  $('start').disabled = running;
  document.querySelectorAll('#goal input, #goal select, .mode-bar input').forEach(el => {
    el.disabled = running || (el.value === 'jev' && !window.jevAvailable);
  });
  $('stop').hidden = !running;
  $('start').innerHTML = running ? '仕入先を調査中…' : 'もう一度調べる <span>↗</span>';
  $('status').textContent = {running:'調査中', complete:'調査完了', partial:'一部要確認', stopped:'停止', error:'要確認'}[state.status];
  $('status').className = 'status' + (running ? ' running' : '');
  $('step').textContent = String(state.step).padStart(2,'0') + ' STEPS';
  $('address').textContent = state.url || 'demo://supplier/';
  $('count').textContent = `${state.offers.length} / ${suppliers.length} 社`;
  if (state.frame && state.frame !== lastFrame) {
    $('frame').src = 'data:image/jpeg;base64,' + state.frame;
    $('frame').hidden = false; $('browser-empty').hidden = true; lastFrame = state.frame;
  }
  const current = state.events.at(-1);
  if (current) {$('activity-title').textContent = current.message; $('activity-detail').textContent = current.detail;}
  const offerKey = JSON.stringify([state.offers,state.best,state.status,state.supplier]);
  if (offerKey !== lastOffers) {
    const best = state.offers.find(o => o.supplier_id === state.best);
    $('offers').innerHTML = suppliers.map(({name},i) => {
      const o = state.offers.find(x => x.supplier === name);
      if (!o) return placeholder(name, i, running && state.supplier === name ? '調査中…' : running ? '調査待ち' : '未確認');
      const reason = !o.eligible ? (o.stock < state.goal.quantity ? `必要数 ${state.goal.quantity}個に対し、在庫 ${o.stock}個` : `希望日 ${state.goal.deadline.slice(5).replace('-','/')}までに届かない見込み`) : !best ? '在庫・納期の条件を満たす候補' : o.supplier_id === best.supplier_id ? '条件を満たす候補の中で、送料込み総額が最安' : o.total === best.total ? '最安候補と同額' : `最安候補より ${money(o.total-best.total)} 高い`;
      return `<article class="offer-card ${state.best === o.supplier_id ? 'best' : ''}"><div class="offer-top"><b>${esc(name)}</b><span class="badge ${o.eligible ? '' : 'fail'}">${state.best === o.supplier_id ? 'おすすめ' : esc(o.reason)}</span></div><div class="metrics"><div><span>送料込み総額・税別</span><strong>${money(o.total)}</strong></div><div><span>在庫</span><strong>${o.stock}<small> 個</small></strong></div><div><span>お届け目安</span><strong>${o.arrival.slice(5).replace('-','/')}</strong></div></div><p class="offer-reason">${esc(reason)}</p><div class="source"><span>単価 ${money(o.price)} · 送料 ${money(o.shipping)}</span><a href="/supplier/${encodeURIComponent(o.supplier_id)}?product=${encodeURIComponent(o.sku)}" target="_blank" rel="noopener">元のページ ↗</a></div></article>`;
    }).join(''); lastOffers = offerKey;
  }
  const eventKey = JSON.stringify(state.events);
  if (eventKey !== lastEvents) {
    $('event-count').textContent = state.events.length;
    $('events').innerHTML = state.events.map(e => `<li>${esc(e.message)}${e.detail ? ' · '+esc(e.detail) : ''}</li>`).join('');
    lastEvents = eventKey;
  }
  if (!running) {
    const best = state.offers.find(o => o.supplier_id === state.best);
    $('recommendation').innerHTML = best ? `<div class="recommendation-top"><span class="eyebrow">${state.status === 'partial' ? '確認できた範囲の候補' : '条件に合う、おすすめの候補'}</span><span>↗</span></div><h3>${esc(best.supplier)}</h3><p class="total">${money(best.total)}<small>送料込み・税別</small></p><p>必要数 ${state.goal.quantity}個と希望日を満たし、${state.status === 'partial' ? '確認できた候補で' : `${suppliers.length}社のうち、条件を満たす候補で`}総額が最も低い仕入先です。<br>注文前に、担当者が条件を確認してください。</p>` : `<div class="recommendation-top"><span class="eyebrow">YOUR NEXT DECISION</span><span>↗</span></div><h3>${state.status === 'complete' ? '条件に合う候補はありません。' : '追加の確認が必要です。'}</h3><p>数量や希望日の調整、未確認の仕入先の確認を検討してください。</p>`;
  }
  $('mode-note').textContent = state.mode === 'jev' ? `Jevが操作を判断${state.model ? ' · '+state.model : ''}。計算と比較はコードが担当します。` : 'ルールで操作する実演です。実際のブラウザが動きます。';
}

$('goal').addEventListener('submit', async e => {
  e.preventDefault(); $('error').hidden = true; $('start').disabled = true;
  try {
    await request('/api/run', {sku:$('sku').value, quantity:Number($('quantity').value), deadline:$('deadline').value, mode:document.querySelector('input[name=mode]:checked').value});
    render(await (await fetch('/api/run')).json());
  } catch (err) {$('error').textContent = err.message; $('error').hidden = false; $('start').disabled = false;}
});
$('stop').addEventListener('click', async () => {
  try {await request('/api/stop', {});} catch(err) {$('error').textContent=err.message; $('error').hidden=false;}
});
document.querySelectorAll('[name=mode]').forEach(el => el.addEventListener('change', () => {
  $('mode-note').textContent = el.value === 'jev' ? 'Jevが画面の操作を判断します。架空の商品情報をTypeSafe APIへ送ります。' : 'ルールで操作する実演です。実際のブラウザが動きます。';
}));

async function poll() {
  try {render(await (await fetch('/api/run')).json());} catch {
    if (running) {$('status').textContent = '接続を確認中';}
  } finally {setTimeout(poll, running ? 400 : 1500);}
}
async function init() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    window.jevAvailable = cfg.jev_available;
    suppliers = cfg.suppliers;
    $('comparison-guide').textContent = `在庫・納期を満たす候補から、送料込み総額で比較します。一覧をスクロールすると全${suppliers.length}社を確認できます。`;
    $('supplier-total').textContent = `${suppliers.length}の仕入先`;
    $('empty-title').textContent = `${suppliers.length}社を巡る、ひとつの依頼。`;
    $('count').textContent = `0 / ${suppliers.length} 社`;
    $('supplier-pills').innerHTML = suppliers.map(s => `<span>${esc(s.name)}</span>`).join('');
    $('offers').innerHTML = suppliers.map((s,i) => placeholder(s.name,i,'調査待ち')).join('');
    $('sku').innerHTML = cfg.products.map(p => `<option value="${esc(p.sku)}">${esc(p.name)}</option>`).join('');
    $('deadline').value = cfg.deadline;
    document.querySelector('[value=jev]').disabled = !cfg.jev_available;
    $('jev-label').title = cfg.jev_available ? '' : 'APIキー設定後に利用できます';
    poll();
  } catch {$('error').textContent = '起動したサーバーへの接続を確認してください'; $('error').hidden = false;}
}
init();
