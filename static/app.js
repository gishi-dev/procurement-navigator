const $ = id => document.getElementById(id);
const money = n => '¥' + n.toLocaleString('ja-JP');
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const suppliers = ['北星パーツ','メトロ部材','光洋サプライ'];
let running = false, lastFrame = '', lastOffers = '', lastEvents = '', previousId = '';

async function request(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '操作を続けられませんでした');
  return data;
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
  $('count').textContent = `${state.offers.length} / 3 社`;
  if (state.frame && state.frame !== lastFrame) {
    $('frame').src = 'data:image/jpeg;base64,' + state.frame;
    $('frame').hidden = false; $('browser-empty').hidden = true; lastFrame = state.frame;
  }
  const current = state.events.at(-1);
  if (current) {$('activity-title').textContent = current.message; $('activity-detail').textContent = current.detail;}
  const offerKey = JSON.stringify([state.offers,state.best,state.status,state.supplier]);
  if (offerKey !== lastOffers) {
    $('offers').innerHTML = suppliers.map((name,i) => {
      const o = state.offers.find(x => x.supplier === name);
      if (!o) return `<div class="offer-placeholder"><span>0${i+1}</span><div><b>${name}</b><p>${running && state.supplier === name ? '調査中…' : running ? '調査待ち' : '未確認'}</p></div><i>—</i></div>`;
      return `<article class="offer-card ${state.best === o.supplier_id ? 'best' : ''}"><div class="offer-top"><b>${esc(name)}</b><span class="badge ${o.eligible ? '' : 'fail'}">${state.best === o.supplier_id ? 'おすすめ' : esc(o.reason)}</span></div><div class="metrics"><div><span>送料込み総額・税別</span><strong>${money(o.total)}</strong></div><div><span>在庫</span><strong>${o.stock}<small> 個</small></strong></div><div><span>お届け目安</span><strong>${o.arrival.slice(5).replace('-','/')}</strong></div></div><div class="source"><span>単価 ${money(o.price)} · 送料 ${money(o.shipping)}</span><a href="/supplier/${encodeURIComponent(o.supplier_id)}?product=${encodeURIComponent(o.sku)}" target="_blank" rel="noopener">元のページ ↗</a></div></article>`;
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
    $('recommendation').innerHTML = best ? `<div class="recommendation-top"><span class="eyebrow">${state.status === 'partial' ? '確認できた範囲の候補' : '条件に合う、おすすめの候補'}</span><span>↗</span></div><h3>${esc(best.supplier)}</h3><p class="total">${money(best.total)}<small>送料込み・税別</small></p><p>必要数 ${state.goal.quantity}個と希望日を満たし、${state.status === 'partial' ? '確認できた候補で' : '3社で'}総額が最も低い仕入先です。<br>注文前に、担当者が条件を確認してください。</p>` : `<div class="recommendation-top"><span class="eyebrow">YOUR NEXT DECISION</span><span>↗</span></div><h3>${state.status === 'complete' ? '条件に合う候補はありません。' : '追加の確認が必要です。'}</h3><p>数量や希望日の調整、未確認の仕入先の確認を検討してください。</p>`;
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
    $('sku').innerHTML = cfg.products.map(p => `<option value="${esc(p.sku)}">${esc(p.name)}</option>`).join('');
    $('deadline').value = cfg.deadline;
    document.querySelector('[value=jev]').disabled = !cfg.jev_available;
    $('jev-label').title = cfg.jev_available ? '' : 'APIキー設定後に利用できます';
    poll();
  } catch {$('error').textContent = '起動したサーバーへの接続を確認してください'; $('error').hidden = false;}
}
init();
