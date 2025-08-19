import logging
import random
import json
import time
import asyncio
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from starlette.responses import Response
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # Updated import for v2.0.0+
from bs4 import BeautifulSoup
from config import FINAL_URL

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()

cached_content = {}
playwright = None

PROXY_USERNAME = "KMwYgm4pR4upF6yX-s-OHIAjmD24A-co-USA-st-NY-ci-NewYorkCity"  # From your logs; includes geo for sticky NY proxies
PROXY_PASSWORD = "pMBwu34BjjGr5urD"
PROXY_SERVER = "pg.proxi.es:20000"  # From your logs; update if needed

@app.on_event("startup")
async def startup():
    global playwright
    playwright = await async_playwright().start()

@app.on_event("shutdown")
async def shutdown():
    await playwright.stop()

MIDDLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Middle Page</title>
</head>
<body>
    <h1>Welcome to the Middle Page</h1>
    <p>Click the button to proceed to the target site.</p>
    <form id="proceedForm" method="POST" action="/scrape">
        <button type="submit">Proceed</button>
    </form>
    <div id="error" style="color: red;"></div>
    <iframe id="contentFrame" sandbox="allow-scripts" style="width:100%; height:100vh; border:none;"></iframe>
    <script>
        const userData = {
            user_agent: navigator.userAgent,
            cookies: document.cookie
        };
        // Pre-fetch on load
        window.addEventListener('load', () => {
            fetch('/pre-fetch', {
                method: 'POST',
                body: JSON.stringify(userData),
                headers: {'Content-Type': 'application/json'}
            }).then(response => {
                if (!response.ok) {
                    document.getElementById('error').textContent = 'Pre-fetch failed.';
                }
            }).catch(error => {
                document.getElementById('error').textContent = 'Error: ' + error;
            });
        });
        // Proceed button
        document.getElementById('proceedForm').addEventListener('submit', (e) => {
            e.preventDefault();
            fetch('/scrape', {
                method: 'POST',
                body: JSON.stringify(userData),
                headers: {'Content-Type': 'application/json'}
            }).then(response => response.json())
            .then(data => {
                if (data.content) {
                    document.getElementById('contentFrame').srcdoc = data.content;
                } else {
                    document.getElementById('error').textContent = 'Failed to load content.';
                }
            }).catch(error => {
                document.getElementById('error').textContent = 'Error: ' + error;
            });
        });
    </script>
</body>
</html>
"""

@app.get("/middle", response_class=HTMLResponse)
async def middle():
    return MIDDLE_HTML

async def test_proxy(proxy):
    try:
        response = requests.get("http://jsonip.com", proxies={"http": f"http://{proxy['username']}:{proxy['password']}@{PROXY_SERVER}"})
        response.raise_for_status()
        logger.info(f"Proxy test success: IP {response.json()}")
        return True
    except Exception as e:
        logger.error(f"Proxy test failed: {str(e)}")
        return False

async def rewrite_content(content, base_url):
    soup = BeautifulSoup(content, 'lxml')
    for tag in soup.find_all(['img', 'script', 'link', 'a', 'source']):
        if tag.has_attr('src'):
            tag['src'] = f"/resource?original_url={tag['src']}"
        if tag.has_attr('href'):
            tag['href'] = f"/resource?original_url={tag['href']}"
        if tag.has_attr('style'):
            # Rewrite background urls in style
            style = tag['style']
            if 'url(' in style:
                # Simple replace; improve if needed
                style = style.replace('url(', 'url(/resource?original_url=')
                tag['style'] = style
    # Handle inline scripts if needed (e.g., replace API calls)
    return str(soup)

async def scrape_target(user_data, is_pre_fetch=False):
    retries = 3
    for attempt in range(retries):
        try:
            username = f"{PROXY_USERNAME}-{random.randint(1000, 9999)}"  # Sticky session ID
            proxy = {
                "server": f"http://{PROXY_SERVER}",
                "username": username,
                "password": PROXY_PASSWORD
            }
            logger.info(f"Launching browser context with proxy: {proxy['server']}")
            if not await test_proxy(proxy):
                raise Exception("Proxy test failed")
            stealth = Stealth()  # New: Create Stealth instance for v2.0.0+
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=[
                    '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE myproxyhost',
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-web-security'
                ])
                context = await browser.new_context(
                    proxy=proxy,
                    user_agent=user_data['user_agent'],
                    ignore_https_errors=True,
                    bypass_csp=True,
                    service_workers="block"
                )
                await stealth.apply_stealth_async(context)  # New: Apply stealth to context (covers all pages)
                # Forward cookies
                cookies = user_data.get('cookies', '')
                cookie_list = [{'name': c.split('=')[0], 'value': '='.join(c.split('=')[1:]), 'domain': FINAL_URL.split('//')[1], 'path': '/'} for c in cookies.split('; ') if c]
                await context.add_cookies(cookie_list)
                # JS overrides for timezone/WebRTC (Stealth handles most, but keep custom if needed)
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    const originalDateTimeFormat = Intl.DateTimeFormat;
                    Intl.DateTimeFormat = function(...args) {
                        const dtf = new originalDateTimeFormat(...args);
                        dtf.resolvedOptions = function() {
                            return { ...originalDateTimeFormat.prototype.resolvedOptions.apply(this), timeZone: 'America/New_York' };
                        };
                        return dtf;
                    };
                    navigator.mediaDevices.getUserMedia = () => Promise.reject(new Error('WebRTC disabled'));
                """)
                page = await context.new_page()
                await page.on("console", lambda msg: logger.info(f"Browser console: {msg.text}"))
                # Block ads/trackers
                await page.route("**/*{ads,track,analytics,google,facebook,cdn.pubmatic,openrtb,doubleclick}*", lambda route: route.abort())
                await page.goto(FINAL_URL, timeout=300000)
                await page.wait_for_function('() => document.querySelector("#ipv4") && !document.querySelector("#ipv4").textContent.includes("Detecting")', timeout=300000)
                content = await page.content()
                logger.info(f"Scraped content length: {len(content)}")
                rewritten_content = await rewrite_content(content, FINAL_URL)
                await context.close()
                await browser.close()
            return rewritten_content
        except Exception as e:
            logger.error(f"Scrape error attempt {attempt+1}: {str(e)}")
            if attempt == retries - 1:
                raise
            await asyncio.sleep(1)

@app.post("/pre-fetch")
async def pre_fetch(user_data: dict, background_tasks: BackgroundTasks):
    key = hash(json.dumps(user_data, sort_keys=True))  # Deterministic key
    if key not in cached_content:
        content = await scrape_target(user_data, is_pre_fetch=True)
        cached_content[key] = {'content': content, 'timestamp': time.time()}
        background_tasks.add_task(clear_cache, key)
    return {"status": "pre-fetched"}

async def clear_cache(key):
    await asyncio.sleep(30)
    if key in cached_content and time.time() - cached_content[key]['timestamp'] > 30:
        del cached_content[key]

@app.post("/scrape")
async def scrape(user_data: dict):
    key = hash(json.dumps(user_data, sort_keys=True))
    if key in cached_content:
        return {"content": cached_content[key]['content']}
    else:
        content = await scrape_target(user_data)
        return {"content": content}

@app.get("/resource")
async def resource(original_url: str):
    try:
        response = requests.get(original_url, headers={'User-Agent': user_data['user_agent'] if 'user_data' in globals() else 'Mozilla/5.0'})
        return Response(content=response.content, media_type=response.headers.get('Content-Type'))
    except Exception as e:
        logger.error(f"Resource proxy error: {str(e)}")
        return {"error": "Failed to load resource"}
