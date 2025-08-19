import logging
import random
import string
import time
import asyncio
import os
import hashlib  # For deterministic random based on session_id
import requests  # For proxy test and resource proxy
from urllib.parse import quote, urljoin

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from playwright.async_api import async_playwright, Error as PlaywrightError
from itsdangerous import TimestampSigner
from bs4 import BeautifulSoup

from config import FINAL_URL

app = FastAPI()

contexts = {}
cached_content = {}
signer = TimestampSigner("your_secret_key")  # Replace with a secure secret key, perhaps from env
playwright = None  # Global playwright instance

logging.basicConfig(level=logging.INFO)

@app.on_event("startup")
async def startup_event():
    global playwright
    playwright = await async_playwright().start()
    asyncio.create_task(clear_old_cache())

def generate_random_string_from_session(session_id, length=10):
    # Deterministic random string based on session_id for sticky proxy per session
    seed = int(hashlib.sha256(session_id.encode()).hexdigest(), 16)
    random.seed(seed)
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def get_or_create_context(session_id: str):
    if session_id in contexts:
        return contexts[session_id]

    # Generate deterministic random for sticky
    random_str = generate_random_string_from_session(session_id)
    username = f"KMwYgm4pR4upF6yX-s-{random_str}-co-USA-st-NY-ci-NewYorkCity"
    password = "pMBwu34BjjGr5urD"
    proxy = {
        "server": "http://pg.proxi.es:20000",
        "username": username,
        "password": password
    }
    logging.info(f"Launching browser context with proxy: http://{username}:*****@pg.proxi.es:20000")

    # Test proxy with requests first
    try:
        proxies = {
            "http": f"http://{username}:{password}@pg.proxi.es:20000",
            "https": f"http://{username}:{password}@pg.proxi.es:20000"
        }
        response = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=30)
        logging.info(f"Proxy test success: IP {response.text.strip()}")
    except Exception as e:
        logging.error(f"Proxy test failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Proxy connection test failed: {str(e)}")

    # Ensure sessions dir exists
    os.makedirs(f"./sessions/{session_id}", exist_ok=True)

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=f"./sessions/{session_id}",
        headless=True,
        proxy=proxy,
        args=[
            '--disable-web-security',
            '--no-sandbox',
            '--disable-setuid-sandbox',
        ],
        ignore_default_args=['--enable-automation'],
        viewport={'width': 1920, 'height': 1080},
        timezone_id="America/New_York",
        locale="en-US",
        java_script_enabled=True,
        ignore_https_errors=True,  # Ignore SSL errors
        service_workers='block',  # Block service workers if interfering
    )

    # JS overrides for WebRTC disable, timezone, language
    await context.add_init_script("""
        // Disable WebRTC
        Object.defineProperty(navigator, 'mediaDevices', { get: () => undefined });
        Object.defineProperty(navigator, 'getUserMedia', { get: () => undefined });
        // Already set timezone and locale via context options
    """)

    contexts[session_id] = context
    return context

async def pre_fetch_internal(request: Request, retry_count: int = 0):
    max_retries = 3
    if retry_count >= max_retries:
        raise HTTPException(status_code=500, detail="Max retries exceeded for pre-fetch")

    try:
        data = await request.json()
        user_agent = data.get('userAgent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        cookies = data.get('cookies', [])  # Expect list of dicts {name, value, domain, etc.}
        session_id = data.get('sessionId')  # Assume sent from JS; if not, generate
        if not session_id:
            session_id = signer.sign(str(time.time())).decode('utf-8')

        context = await get_or_create_context(session_id)

        try:
            page = await context.new_page()
            # Log browser errors
            page.on("pageerror", lambda err: logging.error(f"Page error: {err}"))
            page.on("console", lambda msg: logging.info(f"Browser console: {msg.text}"))
            await page.set_extra_http_headers({'User-Agent': user_agent})
            await context.add_cookies(cookies)  # Add cookies to context
            await page.goto(FINAL_URL, timeout=120000)
            await page.wait_for_load_state('networkidle')
            # Wait for IP to load
            await page.wait_for_function('() => document.querySelector("#ipv4") && document.querySelector("#ipv4").innerText !== "Not Detected"', timeout=60000)
            content = await page.content()
            # Rewrite URLs
            content = rewrite_urls(content, session_id, FINAL_URL)
            await page.close()
            cached_content[session_id] = {'content': content, 'timestamp': time.time()}
            return {"status": "success"}
        except PlaywrightError as e:
            logging.error(f"PlaywrightError during pre-fetch: {str(e)}")
            if 'TargetClosedError' in str(e) or 'closed' in str(e).lower():
                logging.error(f"Context closed during pre-fetch, recreating for session {session_id} (retry {retry_count + 1}/{max_retries})")
                del contexts[session_id]
                return await pre_fetch_internal(request, retry_count + 1)
            else:
                raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logging.error(f"Pre-fetch critical error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/pre-fetch")
async def pre_fetch(request: Request):
    return await pre_fetch_internal(request)

async def scrape_internal(request: Request, retry_count: int = 0):
    max_retries = 3
    if retry_count >= max_retries:
        raise HTTPException(status_code=500, detail="Max retries exceeded for scrape")

    try:
        data = await request.json()
        session_id = data.get('sessionId')
        if not session_id:
            raise HTTPException(status_code=400, detail="Missing sessionId")

        if session_id in cached_content and time.time() - cached_content[session_id]['timestamp'] < 30:
            return HTMLResponse(content=cached_content[session_id]['content'])

        context = await get_or_create_context(session_id)

        user_agent = data.get('userAgent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        cookies = data.get('cookies', [])

        page = await context.new_page()
        # Log browser errors
        page.on("pageerror", lambda err: logging.error(f"Page error: {err}"))
        page.on("console", lambda msg: logging.info(f"Browser console: {msg.text}"))
        await page.set_extra_http_headers({'User-Agent': user_agent})
        await context.add_cookies(cookies)
        await page.goto(FINAL_URL, timeout=120000)
        await page.wait_for_load_state('networkidle')
        # Wait for IP to load
        await page.wait_for_function('() => document.querySelector("#ipv4") && document.querySelector("#ipv4").innerText !== "Not Detected"', timeout=60000)
        content = await page.content()
        # Rewrite URLs
        content = rewrite_urls(content, session_id, FINAL_URL)
        await page.close()

        cached_content[session_id] = {'content': content, 'timestamp': time.time()}
        return HTMLResponse(content=content)
    except PlaywrightError as e:
        logging.error(f"PlaywrightError during scrape: {str(e)}")
        if 'TargetClosedError' in str(e) or 'closed' in str(e).lower():
            logging.error(f"Context closed during scrape, recreating for session {session_id} (retry {retry_count + 1}/{max_retries})")
            del contexts[session_id]
            return await scrape_internal(request, retry_count + 1)
        else:
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logging.error(f"Critical scrape error for {FINAL_URL}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape")
async def scrape(request: Request):
    return await scrape_internal(request)

def rewrite_urls(content: str, session_id: str, base_url: str) -> str:
    soup = BeautifulSoup(content, 'lxml')
    base = base_url.rsplit('/', 1)[0] + '/'  # For relative to absolute

    for tag in soup.find_all(True):
        for attr in ['src', 'href', 'action', 'data-src', 'poster', 'data-background', 'srcset']:
            if attr in tag.attrs:
                original = tag[attr]
                if original and not original.startswith('data:') and not original.startswith('#'):
                    # Make absolute
                    if not original.startswith('http'):
                        original = urljoin(base, original)
                    tag[attr] = f"/resource?session_id={session_id}&url={quote(original)}"

    # For style urls
    for tag in soup.find_all('style'):
        if tag.string:
            tag.string = tag.string.replace('url(', 'url(/resource?session_id=' + session_id + '&url=')

    # For inline scripts with fetch/API calls
    for tag in soup.find_all('script'):
        if tag.string:
            tag.string = tag.string.replace('https://api.iplocation.io/', '/resource?session_id=' + session_id + '&url=https%3A%2F%2Fapi.iplocation.io%2F')
            tag.string = tag.string.replace('https://ex.ingage.tech/', '/resource?session_id=' + session_id + '&url=https%3A%2F%2Fex.ingage.tech%2F')

    return str(soup)

@app.get("/resource")
async def proxy_resource(session_id: str, url: str, request: Request):
    if not session_id or not url:
        raise HTTPException(status_code=400, detail="Missing parameters")

    # Get username from session_id
    random_str = generate_random_string_from_session(session_id)
    username = f"KMwYgm4pR4upF6yX-s-{random_str}-co-USA-st-NY-ci-NewYorkCity"
    password = "pMBwu34BjjGr5urD"
    proxy_url = f"http://{username}:{password}@pg.proxi.es:20000"

    proxies = {"http": proxy_url, "https": proxy_url}

    headers = dict(request.headers)  # Forward user headers
    headers.pop('host', None)
    headers.pop('content-length', None)

    try:
        resp = requests.get(url, proxies=proxies, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = {name: value for name, value in resp.headers.items() if name.lower() not in excluded_headers}
        return Response(content=resp.content, status_code=resp.status_code, headers=response_headers)
    except Exception as e:
        logging.error(f"Resource proxy error for {url}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/middle", response_class=HTMLResponse)
async def middle():
    # Hardcoded middle.html content to avoid FileNotFoundError
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Middle Page</title>
        <script>
            window.onload = function() {
                let sessionId = localStorage.getItem('sessionId');
                if (!sessionId) {
                    sessionId = Date.now().toString();
                    localStorage.setItem('sessionId', sessionId);
                }
                const userAgent = navigator.userAgent;
                const cookies = document.cookie.split(';').map(c => {
                    const [name, ...valueParts] = c.trim().split('=');
                    const value = valueParts.join('=');
                    return {name, value, domain: location.hostname, path: '/'};
                });
                fetch('/pre-fetch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({userAgent, cookies, sessionId})
                }).then(response => {
                    if (!response.ok) {
                        throw new Error('Pre-fetch failed');
                    }
                }).catch(error => {
                    document.getElementById('error').innerText = 'Failed to load content. Click Proceed to try again.';
                    document.getElementById('error').style.display = 'block';
                });
            };
        </script>
    </head>
    <body>
        <h1>Welcome to the Middle Page</h1>
        <p>Click Proceed to load the target site via proxy.</p>
        <button id="proceedButton">Proceed</button>
        <div id="error" style="color: red; display: none;"></div>
        <script>
            document.getElementById('proceedButton').addEventListener('click', function() {
                const sessionId = localStorage.getItem('sessionId');
                const userAgent = navigator.userAgent;
                const cookies = document.cookie.split(';').map(c => {
                    const [name, ...valueParts] = c.trim().split('=');
                    const value = valueParts.join('=');
                    return {name, value, domain: location.hostname, path: '/'};
                });
                fetch('/scrape', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sessionId, userAgent, cookies})
                }).then(response => {
                    if (response.ok) {
                        return response.text();
                    }
                    throw new Error('Scrape failed');
                }).then(content => {
                    document.open();
                    document.write(content);
                    document.close();
                }).catch(error => {
                    document.getElementById('error').innerText = 'Error loading content: ' + error.message;
                    document.getElementById('error').style.display = 'block';
                });
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.on_event("shutdown")
async def cleanup():
    global playwright
    for session_id in list(contexts.keys()):
        try:
            await contexts[session_id].close()
        except Exception as e:
            logging.error(f"Shutdown: Error closing context for session {session_id}: {str(e)}")
    contexts.clear()
    if playwright:
        await playwright.stop()

# Optional: Background task to clear old cache
async def clear_old_cache():
    while True:
        current_time = time.time()
        keys_to_delete = [k for k, v in cached_content.items() if current_time - v['timestamp'] > 30]
        for k in keys_to_delete:
            del cached_content[k]
        await asyncio.sleep(10)
