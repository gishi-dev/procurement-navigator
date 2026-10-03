from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
PORT = int(os.environ.get("PORT", "8871"))
BASE = f"http://127.0.0.1:{PORT}"
KEY = os.environ.get("TYPESAFE_API_KEY", "")
MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")
SUPPLIERS = [
    {
        "id": "north",
        "name": "北星パーツ",
        "tag": "産業用部品の専門店",
        "color": "#1a4e77",
        "prices": [2480, 1680],
        "stock": [48, 60],
        "days": [3, 2],
        "shipping": 600,
    },
    {
        "id": "metro",
        "name": "メトロ部材",
        "tag": "工場の調達をもっと身近に",
        "color": "#bd632e",
        "prices": [2190, 1490],
        "stock": [80, 0],
        "days": [12, 4],
        "shipping": 800,
    },
    {
        "id": "koyo",
        "name": "光洋サプライ",
        "tag": "必要な部品を、必要なときに",
        "color": "#297461",
        "prices": [2320, 1590],
        "stock": [32, 40],
        "days": [5, 5],
        "shipping": 500,
    },
]
PRODUCTS = [
    {"sku": "GS-FAN120", "name": "冷却ファン 120mm", "kind": "COOLING / 冷却部品"},
    {"sku": "GS-SENSOR24", "name": "温度センサー 24V", "kind": "SENSING / 検知部品"},
]
app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
run: dict = {}
run_task: asyncio.Task | None = None
lock = asyncio.Lock()


def today():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.hostname not in {"localhost", "127.0.0.1"}:
        return HTMLResponse("Local access only", status_code=403)
    if request.method == "POST" and request.headers.get("origin") not in {BASE, f"http://localhost:{PORT}"}:
        return HTMLResponse("Invalid origin", status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class Goal(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=1000)
    deadline: date
    mode: str = "demo"


@app.get("/", response_class=HTMLResponse)
async def index():
    return (ROOT / "static" / "index.html").read_text()


@app.get("/api/config")
async def config():
    return {
        "jev_available": bool(KEY),
        "products": PRODUCTS,
        "deadline": (today() + timedelta(days=7)).isoformat(),
        "model": MODEL,
    }


@app.get("/api/run")
async def status():
    return run or {"status": "idle"}


@app.post("/api/run")
async def start(goal: Goal):
    global run, run_task
    if goal.sku not in {p["sku"] for p in PRODUCTS} or goal.mode not in {"demo", "jev"}:
        raise HTTPException(400, "商品またはモードを確認してください")
    if not today() <= goal.deadline <= today() + timedelta(days=365):
        raise HTTPException(400, "希望日は今日から1年以内で指定してください")
    if goal.mode == "jev" and not KEY:
        raise HTTPException(400, "JevのAPIキーが未設定です")
    async with lock:
        # One browser run at a time; repeated clicks must not start duplicate paid calls.
        if run_task and not run_task.done():
            raise HTTPException(409, "調査が進行中です")
        run = {
            "id": uuid.uuid4().hex,
            "status": "running",
            "mode": goal.mode,
            "goal": goal.model_dump(mode="json"),
            "offers": [],
            "events": [],
            "frame": None,
            "supplier": "",
            "url": "",
            "step": 0,
            "model": None,
            "input_tokens": 0,
        }
        run_task = asyncio.create_task(investigate(goal, run))
        return {"id": run["id"]}


@app.post("/api/stop")
async def stop():
    if run_task and not run_task.done():
        run_task.cancel()
        run["status"] = "stopped"
        event(run, "調査を停止", "取得できた結果を残しました")
    return {"ok": True}


def event(state, message, detail=""):
    state["events"].append({"message": message, "detail": detail})
    state["events"] = state["events"][-30:]


async def capture(page, state):
    state["frame"] = base64.b64encode(await page.screenshot(type="jpeg", quality=78)).decode()
    state["url"] = page.url.replace(BASE + "/", "demo://")


async def decide(options, visible, goal, state):
    if goal.mode == "demo":
        for prefix in ["fill:", "click:search", "click:detail", "collect:"]:
            found = next((o for o in options if o.startswith(prefix)), None)
            if found:
                return found
        return "blocked"
    # Only fictional page excerpts and the declared goal leave this local demo.
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": f"Bearer {KEY}"},
            json={
                "model": MODEL,
                "state": {
                    "goal": f"Find the price, stock and lead time for {goal.sku}. Do not purchase.",
                    "page": visible[:14000],
                },
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": "Choose the next available browser action to find this exact product. "
                        "Fill the search before clicking search. Open product details before collecting.",
                        "criteria": {
                            **options,
                            "blocked": "Cannot find the requested product; stop this supplier.",
                        },
                    }
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        choice = data["answers"]["action"]["choice"]
        if choice != "blocked" and choice not in options:
            raise ValueError("Invalid action")
        state["model"] = data["model"]
        state["input_tokens"] += data.get("usage", {}).get("input_tokens", 0) or 0
        return choice


async def investigate(goal, state):
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                context = await browser.new_context(viewport={"width": 1080, "height": 760})
                page = await context.new_page()

                # Browser navigation is confined to this application's fictional supplier pages.
                async def allow_local(route):
                    if route.request.url.startswith(BASE + "/supplier/"):
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", allow_local)
                for supplier in SUPPLIERS:
                    state["supplier"] = supplier["name"]
                    event(state, f"{supplier['name']}を調査", "商品ページで在庫・価格・納期を確認します")
                    await page.goto(f"{BASE}/supplier/{supplier['id']}")
                    collected = False
                    for _ in range(7):
                        await capture(page, state)
                        await asyncio.sleep(0.7)
                        controls = await page.locator("[data-action]").evaluate_all(
                            "els => els.map(e => ({id:e.dataset.action, text:e.getAttribute('aria-label') "
                            "|| e.textContent, value:e.value || ''}))"
                        )
                        options = {}
                        for c in controls:
                            if c["id"].startswith("fill:") and c["value"] == goal.sku:
                                continue
                            if c["id"] == "click:search" and parse_qs(urlparse(page.url).query).get("q") == [
                                goal.sku
                            ]:
                                continue
                            options[c["id"]] = c["text"].strip()
                        visible = await page.locator("body").inner_text()
                        action = await decide(options, visible, goal, state)
                        state["step"] += 1
                        if action == "blocked":
                            break
                        target = page.locator(f'[data-action="{action}"]')
                        await target.evaluate("el => el.style.outline='4px solid #d2a357'")
                        await capture(page, state)
                        await asyncio.sleep(0.65)
                        if action.startswith("fill:"):
                            event(state, "型番を入力", goal.sku)
                            await target.fill(goal.sku)
                        elif action.startswith("click:"):
                            event(state, "商品を検索" if action == "click:search" else "商品詳細を確認")
                            await target.click()
                            await page.wait_for_load_state("domcontentloaded")
                        elif action.startswith("collect:"):
                            offer = json.loads(await page.locator("#offer-data").text_content())
                            # Prices and dates are read from the page; all arithmetic stays in code.
                            if offer["sku"] != goal.sku or offer["supplier_id"] != supplier["id"]:
                                raise ValueError("Product mismatch")
                            offer["arrival"] = (today() + timedelta(days=offer["days"])).isoformat()
                            offer["total"] = offer["price"] * goal.quantity + offer["shipping"]
                            offer["eligible"] = offer["stock"] >= goal.quantity and (
                                date.fromisoformat(offer["arrival"]) <= goal.deadline
                            )
                            offer["reason"] = (
                                "在庫不足"
                                if offer["stock"] < goal.quantity
                                else "希望日を超過"
                                if not offer["eligible"]
                                else "条件を満たす"
                            )
                            state["offers"].append(offer)
                            event(state, "比較表に追加", supplier["name"])
                            collected = True
                            await capture(page, state)
                            break
                    if not collected:
                        event(
                            state, f"{supplier['name']}は確認が必要", "条件を読み取れず、候補に含めていません"
                        )
                eligible = [o for o in state["offers"] if o["eligible"]]
                state["best"] = min(eligible, key=lambda o: o["total"])["supplier_id"] if eligible else None
                state["status"] = "complete" if len(state["offers"]) == len(SUPPLIERS) else "partial"
                event(state, "調査を終了", "条件を満たす候補から総額が最も低い仕入先を表示します")
            finally:
                await browser.close()
    except asyncio.CancelledError:
        if state["status"] != "stopped":
            state["status"] = "stopped"
            event(state, "調査を停止", "取得できた結果を残しました")
    except (httpx.HTTPError, BrowserError, ValueError, KeyError, TypeError, OSError):
        # Never expose provider exception text: it may include request headers or page data.
        state["status"] = "error"
        event(state, "調査を続けられませんでした", "ブラウザの準備、APIキー、API接続を確認してください")


@app.get("/supplier/{supplier_id}", response_class=HTMLResponse)
async def supplier_page(supplier_id: str, q: str = "", product: str = ""):
    s = next((s for s in SUPPLIERS if s["id"] == supplier_id), None)
    if not s:
        raise HTTPException(404)
    item = next((p for p in PRODUCTS if p["sku"] == product), None)
    content = ""
    if item:
        i = PRODUCTS.index(item)
        offer = {
            "supplier_id": s["id"],
            "supplier": s["name"],
            "sku": item["sku"],
            "price": s["prices"][i],
            "stock": s["stock"][i],
            "days": s["days"][i],
            "shipping": s["shipping"],
            "source": f"demo://supplier/{s['id']}?product={item['sku']}",
        }
        content = f"""<div class="detail"><div class="part">{part_svg(item["sku"])}</div><div><small>{item["kind"]}</small>
        <h1>{item["name"]}</h1><p class="sku">{item["sku"]}</p><p class="price">¥{offer["price"]:,}<small> / 個・税別</small></p>
        <div class="spec"><p>在庫 <b>{offer["stock"]} 個</b></p><p>お届け目安 <b>{offer["days"]} 日後</b></p>
        <p>送料 <b>¥{offer["shipping"]:,} / 注文</b></p></div>
        <button data-action="collect:offer">この商品の条件を記録</button><p class="note">展示用の商品です。注文機能はありません。</p></div></div>
        <script id="offer-data" type="application/json">{json.dumps(offer, ensure_ascii=False)}</script>"""
    elif q:
        found = [p for p in PRODUCTS if q.strip().lower() in (p["sku"] + p["name"]).lower()]
        content = f"<small>CATALOG</small><h1>「{html.escape(q)}」の検索結果</h1><p>{len(found)}件の商品</p>"
        for p in found:
            content += f"""<div class="result"><div class="mini">{part_svg(p["sku"])}</div><div><small>{p["kind"]}</small>
            <h2>{p["name"]}</h2><p>{p["sku"]}</p></div><a data-action="click:detail" href="?product={p["sku"]}">商品詳細を見る →</a></div>"""
    else:
        content = f"""<div class="hero"><small>PARTS FOR YOUR NEXT IDEA</small><h1>ものづくりを支える、<br>ひとつの部品から。</h1><p>冷却・検知・制御。現場で使える部品を取り揃えています。</p>
        <div class="part">{fan_svg()}</div></div><div class="categories"><div>01　冷却部品</div><div>02　センサー</div><div>03　制御部品</div></div>"""
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f7f8f7;color:#24313b;font-family:Arial,"Hiragino Sans",sans-serif}}
    header{{background:{s["color"]};color:white;padding:24px 48px;display:flex;justify-content:space-between;align-items:center}}
    .brand{{font-size:25px;font-weight:bold}}header small{{font-size:12px;opacity:.8}}nav{{background:white;padding:18px 48px;display:flex;justify-content:space-between;font-size:13px;color:#68727b}}
    form{{display:flex;margin:26px 48px;gap:10px}}input{{border:1px solid #c7cfce;border-radius:6px;flex:1;padding:15px;font-size:15px}}
    button,a{{background:{s["color"]};color:white;border:0;border-radius:5px;padding:15px 24px;text-decoration:none;font-size:14px;cursor:pointer}}
    main{{padding:10px 48px}}small{{letter-spacing:1px;color:#708080}}h1{{font-size:34px;line-height:1.6;margin:15px 0}}h2{{font-size:21px}}p{{color:#66737b;line-height:1.8}}
    .hero{{position:relative;background:#edf1ef;padding:36px;border-radius:10px;min-height:310px}}.hero .part{{position:absolute;right:30px;top:35px;width:240px}}
    .part svg,.mini svg{{width:100%}}.categories{{display:flex;gap:16px;margin-top:24px}}.categories div{{background:white;padding:24px;flex:1;border:1px solid #e1e7e4}}
    .detail{{display:grid;grid-template-columns:1fr 1fr;gap:36px}}.detail .part{{background:#edf1ef;padding:50px;border-radius:10px}}
    .price{{font-size:33px;font-weight:bold;color:{s["color"]}}}.price small{{font-size:12px}}.spec{{background:white;border:1px solid #e0e5e3;border-radius:8px;padding:10px 20px;margin-bottom:20px}}
    .spec p{{display:flex;justify-content:space-between;margin:12px 0}}.note{{font-size:11px}}.result{{display:flex;align-items:center;gap:28px;background:white;padding:28px;border:1px solid #dce3df;border-radius:10px}}
    .result a{{margin-left:auto}}.mini{{width:125px}}footer{{margin:35px 48px;border-top:1px solid #dde3df;padding-top:18px;font-size:11px;color:#809089}}
    </style><header><div class="brand">{s["name"]} <small> / PARTS STORE</small></div><span>{s["tag"]}</span></header>
    <nav><span>商品を探す　 /　 はじめての方へ　 /　 お届けについて</span><span>架空の仕入先サイト</span></nav>
    {"" if item else f'<form><input data-action="fill:search" name="q" aria-label="検索欄に型番を入力" placeholder="型番・商品名から探す" value="{html.escape(q, quote=True)}"><button data-action="click:search">検索</button></form>'}
    <main>{content}</main><footer>個人開発デモ · 会社・商品・取引条件はすべて架空です</footer></html>"""


def part_svg(sku):
    if sku == "GS-SENSOR24":
        return '<svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg"><path d="M120 90V45Q120 20 180 20" fill="none" stroke="#334f5c" stroke-width="10"/><rect x="90" y="80" width="60" height="68" rx="8" fill="#23333f"/><rect x="106" y="135" width="28" height="80" rx="8" fill="#a1b2ba"/><path d="M95 102H145M95 114H145" stroke="#97abb4" stroke-width="3"/></svg>'
    return fan_svg()


def fan_svg():
    return """<svg viewBox="0 0 240 240" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="20" y="20" width="200" height="200" rx="22" fill="#23333f"/><rect x="30" y="30" width="180" height="180" rx="14" stroke="#516471" stroke-width="2"/><circle cx="120" cy="120" r="76" fill="#17252e" stroke="#6b7b82" stroke-width="3"/><g fill="#536b77"><path d="M120 120C60 120 45 63 80 53C114 43 137 88 120 120Z"/><path d="M120 120C120 60 177 45 187 80C197 114 152 137 120 120Z"/><path d="M120 120C180 120 195 177 160 187C126 197 103 152 120 120Z"/><path d="M120 120C120 180 63 195 53 160C43 126 88 103 120 120Z"/></g><circle cx="120" cy="120" r="24" fill="#97abb4"/><circle cx="120" cy="120" r="12" fill="#334f5c"/><g fill="#97abb4"><circle cx="35" cy="35" r="5"/><circle cx="205" cy="35" r="5"/><circle cx="35" cy="205" r="5"/><circle cx="205" cy="205" r="5"/></g></svg>"""
