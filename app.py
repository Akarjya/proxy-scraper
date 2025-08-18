import logging
import random
import string
import time
import asyncio
import os

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright, Error as PlaywrightError
from itsdangerous import TimestampSigner

from config import FINAL_URL

app = FastAPI()

contexts = {}
cached_content = {}
signer = TimestampSigner("your_secret_key")  # Replace with a secure secret key, perhaps from env

logging.basicConfig(level=logging.INFO)

def generate_random_string(length=10):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def get_or_create_context(session_id: str):
    if session_id in contexts:
        return contexts[session_id]
    # No is_connected check here; we'll handle closed contexts in the caller with try-except

    async with async_playwright() as playwright:
        # Use fixed test username for now
        username = f"KMwYgm4pR4upF6yX-s-test12345678-co-USA-st-NY-ci-NewYorkCity"
        password = "pMBwu34BjjGr5urD"
        proxy = {
            "server": "socks5://pg.proxi.es:20002",  # Changed to socks5:// for compatibility
            "username": username,
            "password": password
        }
        logging.info(f"Launching browser context with proxy: socks5://{username}:*****@pg.proxi.es:20002")

        # Ensure sessions dir exists
        os.makedirs(f"./sessions/{session_id}", exist_ok=True)

        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=f"./sessions/{session_id}",
            headless=True,
            proxy=proxy,
            args=[
                '--disable-web-security',
                '--host-resolver-rules=MAP * 0.0.0.0 , EXCLUDE pg.proxi.es',  # For DNS over SOCKS
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ],
            ignore_default_args=['--enable-automation'],
            viewport={'width': 1920, 'height': 1080},
            timezone_id="America/New_York",
            locale="en-US",
            java_script_enabled=True,
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

@app.post("/pre-fetch")
async def pre_fetch(request: Request):
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
            await page.set_extra_http_headers({'User-Agent': user_agent})
            await context.add_cookies(cookies)  # Add cookies to context
            await page.goto(FINAL_URL, timeout=60000)  # Increased timeout
            content = await page.content()
            await page.close()
            cached_content[session_id] = {'content': content, 'timestamp': time.time()}
            return {"status": "success"}
        except PlaywrightError as e:
            if 'TargetClosedError' in str(e) or 'closed' in str(e).lower():
                logging.error(f"Context closed during pre-fetch, recreating for session {session_id}")
                del contexts[session_id]
                return await pre_fetch(request)  # Retry once
            else:
                raise
    except Exception as e:
        logging.error(f"Pre-fetch critical error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/scrape")
async def scrape(request: Request):
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
        await page.set_extra_http_headers({'User-Agent': user_agent})
        await context.add_cookies(cookies)
        await page.goto(FINAL_URL, timeout=60000)
        content = await page.content()
        await page.close()

        cached_content[session_id] = {'content': content, 'timestamp': time.time()}
        return HTMLResponse(content=content)
    except PlaywrightError as e:
        if 'TargetClosedError' in str(e) or 'closed' in str(e).lower():
            logging.error(f"Context closed during scrape, recreating for session {session_id}")
            del contexts[session_id]
            return await scrape(request)  # Retry once
        else:
            logging.error(f"Critical scrape error for {FINAL_URL}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logging.error(f"Critical scrape error for {FINAL_URL}: {str(e)}")
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
    for session_id in list(contexts.keys()):
        try:
            await contexts[session_id].close()
        except Exception as e:
            logging.error(f"Shutdown: Error closing context for session {session_id}: {str(e)}")
    contexts.clear()

# Optional: Background task to clear old cache
async def clear_old_cache():
    while True:
        current_time = time.time()
        keys_to_delete = [k for k, v in cached_content.items() if current_time - v['timestamp'] > 30]
        for k in keys_to_delete:
            del cached_content[k]
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup():
    asyncio.create_task(clear_old_cache())
