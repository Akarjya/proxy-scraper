import asyncio
import os
import random
import string
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus
from urllib.parse import urlparse
import time
from typing import Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from config import FINAL_URL
except ImportError:
    FINAL_URL = "https://www.whatismyip.com/"

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(SessionMiddleware, secret_key="your_secure_secret_key_change_me")

contexts: Dict[str, 'BrowserContext'] = {}
cached_content: Dict[str, str] = {}

SPOOFED_LANGUAGE = 'en-US,en;q=0.9'
SPOOFED_TIMEZONE = 'America/New_York'
SPOOFED_OFFSET = 240

PROXY_HOST = os.getenv('PROXY_HOST', 'pg.proxi.es')
PROXY_PORT = int(os.getenv('PROXY_PORT', 20002))
BASE_USERNAME = os.getenv('BASE_USERNAME', 'KMwYgm4pR4upF6yX-s-')
USERNAME_SUFFIX = os.getenv('USERNAME_SUFFIX', '-co-USA-st-NY-ci-NewYorkCity')
PROXY_PASSWORD = os.getenv('PROXY_PASSWORD', 'pMBwu34BjjGr5urD')

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'
]

def generate_proxy_username():
    random_string = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    username = f"{BASE_USERNAME}{random_string}{USERNAME_SUFFIX}"
    logger.info(f"Generated proxy username: {username}")
    return username

TIMEZONE_SPOOF_JS = f"""
  (function() {{
    console.log('Timezone spoof loaded');
    const originalDateTimeFormat = Intl.DateTimeFormat;
    Intl.DateTimeFormat = function(...args) {{
      const dtf = new originalDateTimeFormat(...args);
      const originalResolvedOptions = dtf.resolvedOptions;
      dtf.resolvedOptions = function() {{
        const options = originalResolvedOptions.call(dtf);
        options.timeZone = '{SPOOFED_TIMEZONE}';
        options.locale = 'en-US';
        options.calendar = 'gregory';
        options.numberingSystem = 'latn';
        return options;
      }};
      return dtf;
    }};
    Date.prototype.getTimezoneOffset = function() {{
      return {SPOOFED_OFFSET};
    }};
    const originalNumberFormat = Intl.NumberFormat;
    Intl.NumberFormat = function(...args) {{
      const nf = new originalNumberFormat(...args);
      const originalResolvedOptions = nf.resolvedOptions;
      nf.resolvedOptions = function() {{
        const options = originalResolvedOptions.call(nf);
        options.locale = 'en-US';
        return options;
      }};
      return nf;
    }};
    const originalPluralRules = Intl.PluralRules;
    Intl.PluralRules = function(...args) {{
      const pr = new originalPluralRules(...args);
      const originalResolvedOptions = pr.resolvedOptions;
      pr.resolvedOptions = function() {{
        const options = originalResolvedOptions.call(pr);
        options.locale = 'en-US';
        return options;
      }};
      return pr;
    }};
  }})();
"""

PROXY_JS_OVERRIDE = f"""
  console.log('Proxy JS override loaded');
  (function() {{
    const proxyBase = window.location.origin + '/scrape?url=';
    const spoofedLang = 'en-US,en;q=0.9';
    const finalUrl = '{FINAL_URL}';
    const originalFetch = window.fetch;
    window.fetch = function(url, options = {{}}) {{
      console.log('Intercepted fetch to:', url);
      if (typeof url === 'string' && !url.startsWith(proxyBase)) {{
        url = proxyBase + encodeURIComponent(url);
      }} else if (url instanceof Request && !url.url.startsWith(proxyBase)) {{
        url = new Request(proxyBase + encodeURIComponent(url.url), url);
      }}
      if (!options.headers) options.headers = {{}};
      options.headers['Accept-Language'] = spoofedLang;
      return originalFetch.call(this, url, options);
    }};
    const originalXHR = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {{
      console.log('Intercepted XHR to:', url);
      if (!url.startsWith(proxyBase)) {{
        url = proxyBase + encodeURIComponent(url);
      }}
      const originalSetHeader = this.setRequestHeader;
      this.setRequestHeader = function(name, value) {{
        if (name === 'Accept-Language') value = spoofedLang;
        return originalSetHeader.call(this, name, value);
      }};
      return originalXHR.call(this, method, url);
    }};
    const originalSendBeacon = navigator.sendBeacon;
    navigator.sendBeacon = function(url, data) {{
      console.log('Intercepted sendBeacon to:', url);
      if (!url.startsWith(proxyBase)) {{
        url = proxyBase + encodeURIComponent(url);
      }}
      return originalSendBeacon.call(navigator, url, data);
    }};
    window.location.replace = function(url) {{
      console.log('Intercepted location.replace to:', url);
      if (!url.startsWith(proxyBase)) {{
        url = proxyBase + encodeURIComponent(url);
      }}
      return this.href = url;
    }};
    window.location.assign = function(url) {{
      console.log('Intercepted location.assign to:', url);
      if (!url.startsWith(proxyBase)) {{
        url = proxyBase + encodeURIComponent(url);
      }}
      return this.href = url;
    }};
    window.location.reload = function() {{
      console.log('Reload blocked by proxy');
      return;
    }};
    Object.defineProperty(navigator, 'language', {{
      get: function() {{
        return 'en-US';
      }}
    }});
    Object.defineProperty(navigator, 'languages', {{
      get: function() {{
        return ['en-US', 'en'];
      }}
    }});
    Object.defineProperty(navigator, 'userLanguage', {{
      get: function() {{
        return 'en-US';
      }}
    }});
    const originalCreateElement = document.createElement.bind(document);
    document.createElement = function(tagName) {{
      const elem = originalCreateElement(tagName);
      const tagLower = tagName.toLowerCase();
      if (tagLower === 'script' || tagLower === 'iframe') {{
        Object.defineProperty(elem, 'src', {{
          get: function() {{
            return this.getAttribute('src');
          }},
          set: function(value) {{
            if (value && typeof value === 'string' && !value.startsWith(proxyBase)) {{
              console.log('Intercepted ' + tagLower + ' src set:', value);
              value = proxyBase + encodeURIComponent(value);
            }}
            this.setAttribute('src', value);
          }},
          enumerable: true,
          configurable: true
        }});
      }}
      return elem;
    }};
    document.addEventListener('DOMContentLoaded', function() {{
      const metas = document.querySelectorAll('meta[http-equiv="refresh"]');
      metas.forEach(meta => meta.remove());
      setInterval(() => {{
        fetch(proxyBase + encodeURIComponent(finalUrl), {{ method: 'HEAD' }}).catch(() => {{}});
      }}, 30000);
    }});
  }})();
"""

GEO_COOKIES = ['country', 'geo', 'location', 'lat', 'lon', 'region']

@app.get("/")
async def home():
    return {"message": "Scraper app is live. Use /middle to access the entry page."}

@app.get("/middle", response_class=HTMLResponse)
async def middle_page():
    with open("static/middle.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.post("/pre-fetch")
async def pre_fetch(request: Request):
    session_id = request.session.get('session_id')
    if not session_id:
        request.session['session_id'] = ''.join(random.choices(string.digits, k=8))
        session_id = request.session['session_id']
    
    body = await request.json()
    user_agent = body.get('userAgent', random.choice(USER_AGENTS))
    
    asyncio.create_task(_scrape_and_cache(session_id, FINAL_URL, user_agent))
    return JSONResponse({"status": "pre-fetch started"})

async def _scrape_and_cache(session_id: str, url: str, user_agent: str):
    try:
        async with async_playwright() as p:
            if session_id not in contexts:
                user_data_dir = os.path.join(os.getcwd(), f'temp_profile_{session_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                # Test username for debugging
                proxy_username = 'KMwYgm4pR4upF6yX-s-test12345678-co-USA-st-NY-ci-NewYorkCity'
                logger.info(f"Launching browser context with proxy: socks5h://{proxy_username}:*****@{PROXY_HOST}:{PROXY_PORT}")
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=True,
                    proxy={"server": f"socks5h://{proxy_username}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"},
                    user_agent=user_agent,
                    viewport={'width': 390, 'height': 844} if 'mobile' in user_agent.lower() or 'iphone' in user_agent.lower() or 'android' in user_agent.lower() else {'width': 1920, 'height': 1080},
                    locale='en-US',
                    timezone_id=SPOOFED_TIMEZONE,
                    extra_http_headers={'Accept-Language': SPOOFED_LANGUAGE}
                )
                await context.add_init_script(script=f"""
                    Object.defineProperty(navigator, 'language', {{ get: () => 'en-US' }});
                    Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
                    Date.prototype.getTimezoneOffset = () => {SPOOFED_OFFSET};
                    const originalGetContext = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type, attributes) {{
                        if (type === 'webgl' || type === 'webgl2') {{
                            attributes = {{ ...attributes, failIfMajorPerformanceCaveat: false }};
                        }}
                        return originalGetContext.call(this, type, attributes);
                    }};
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => 8 }});
                    window.RTCPeerConnection = null;
                    window.mozRTCPeerConnection = null;
                    window.webkitRTCPeerConnection = null;
                    window.RTCSessionDescription = null;
                    window.RTCIceCandidate = null;
                    navigator.getUserMedia = null;
                    navigator.mediaDevices = {{ getUserMedia: null }};
                    navigator.mozGetUserMedia = null;
                    navigator.webkitGetUserMedia = null;
                """)
                contexts[session_id] = context
            else:
                context = contexts[session_id]

            if not context.is_connected():
                logger.error(f"Context for session {session_id} is closed. Creating new.")
                del contexts[session_id]
                user_data_dir = os.path.join(os.getcwd(), f'temp_profile_{session_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                proxy_username = 'KMwYgm4pR4upF6yX-s-test12345678-co-USA-st-NY-ci-NewYorkCity'
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=True,
                    proxy={"server": f"socks5h://{proxy_username}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"},
                    user_agent=user_agent,
                    viewport={'width': 390, 'height': 844} if 'mobile' in user_agent.lower() or 'iphone' in user_agent.lower() or 'android' in user_agent.lower() else {'width': 1920, 'height': 1080},
                    locale='en-US',
                    timezone_id=SPOOFED_TIMEZONE,
                    extra_http_headers={'Accept-Language': SPOOFED_LANGUAGE}
                )
                await context.add_init_script(script=f"""
                    Object.defineProperty(navigator, 'language', {{ get: () => 'en-US' }});
                    Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
                    Date.prototype.getTimezoneOffset = () => {SPOOFED_OFFSET};
                    const originalGetContext = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type, attributes) {{
                        if (type === 'webgl' || type === 'webgl2') {{
                            attributes = {{ ...attributes, failIfMajorPerformanceCaveat: false }};
                        }}
                        return originalGetContext.call(this, type, attributes);
                    }};
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => 8 }});
                    window.RTCPeerConnection = null;
                    window.mozRTCPeerConnection = null;
                    window.webkitRTCPeerConnection = null;
                    window.RTCSessionDescription = null;
                    window.RTCIceCandidate = null;
                    navigator.getUserMedia = null;
                    navigator.mediaDevices = {{ getUserMedia: null }};
                    navigator.mozGetUserMedia = null;
                    navigator.webkitGetUserMedia = null;
                """)
                contexts[session_id] = context

            page = await context.new_page()

            async def handle_response(resp):
                headers = await resp.all_headers()
                set_cookie = headers.get('set-cookie', '')
                if set_cookie:
                    filtered = [c for c in set_cookie.split(';') if not any(geo in c.lower() for geo in GEO_COOKIES)]
                    logger.info(f"Filtered cookies for {resp.url}: {filtered}")

            page.on('response', handle_response)

            try:
                logger.info(f"Navigating to {url} with session {session_id}")
                await page.goto(url, wait_until='networkidle', timeout=60000)
                await asyncio.sleep(random.uniform(2, 5))
                content = await page.content()

                soup = BeautifulSoup(content, 'lxml')
                for tag in soup.find_all(['a', 'form'], href=True):
                    original_url = tag['href']
                    full_url = urljoin(url, original_url)
                    tag['href'] = f'/scrape?url={quote_plus(full_url)}&session_id={session_id}'
                if soup.html:
                    soup.html['lang'] = 'en-US'
                if soup.head:
                    timezone_script = soup.new_tag('script')
                    timezone_script.string = TIMEZONE_SPOOF_JS
                    soup.head.append(timezone_script)
                    proxy_script = soup.new_tag('script')
                    proxy_script.string = PROXY_JS_OVERRIDE
                    soup.head.append(proxy_script)

                rewritten_content = str(soup)
                cached_content[session_id] = rewritten_content
                logger.info(f"Cached content for session {session_id}")
                await asyncio.sleep(30)
                if session_id in cached_content:
                    del cached_content[session_id]
                    logger.info(f"Cleared cache for session {session_id}")
            except Exception as e:
                cached_content[session_id] = f"Pre-fetch failed: {str(e)}. Please try again."
                logger.error(f"Pre-fetch error: {str(e)}")
    except Exception as e:
        cached_content[session_id] = f"Pre-fetch failed: {str(e)}. Please try again."
        logger.error(f"Pre-fetch critical error: {str(e)}")

@app.get("/scrape")
async def scrape_get(request: Request, response: Response, url: str):
    session_id = request.session.get('session_id')
    if not session_id:
        return HTMLResponse("Error: No session. Please start from /middle.")

    if session_id in cached_content:
        content = cached_content[session_id]
        del cached_content[session_id]
        if content.startswith("Pre-fetch failed"):
            pass
        else:
            return content

    return await scrape(request, response, url)

@app.post("/scrape")
async def scrape_post(request: Request, response: Response):
    session_id = request.session.get('session_id')
    if not session_id:
        return HTMLResponse("Error: No session. Please start from /middle.")

    try:
        body = await request.json()
        url = body.get('url', FINAL_URL)
    except Exception:
        return HTMLResponse("Error: Invalid request body. Please try again.")

    return await scrape(request, response, url)

async def scrape(request: Request, response: Response, url: str):
    session_id = request.session.get('session_id')
    user_agent = request.headers.get('User-Agent', random.choice(USER_AGENTS))
    cookies = request.cookies
    is_mobile = 'mobile' in user_agent.lower() or 'iphone' in user_agent.lower() or 'android' in user_agent.lower()
    viewport = {'width': 390, 'height': 844} if is_mobile else {'width': 1920, 'height': 1080}

    try:
        async with async_playwright() as p:
            if session_id not in contexts:
                user_data_dir = os.path.join(os.getcwd(), f'temp_profile_{session_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                proxy_username = 'KMwYgm4pR4upF6yX-s-test12345678-co-USA-st-NY-ci-NewYorkCity'
                logger.info(f"Launching browser context for scrape with proxy: socks5h://{proxy_username}:*****@{PROXY_HOST}:{PROXY_PORT}")
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=True,
                    proxy={"server": f"socks5h://{proxy_username}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"},
                    user_agent=user_agent,
                    viewport=viewport,
                    locale='en-US',
                    timezone_id=SPOOFED_TIMEZONE,
                    extra_http_headers={'Accept-Language': SPOOFED_LANGUAGE}
                )
                await context.add_init_script(script=f"""
                    Object.defineProperty(navigator, 'language', {{ get: () => 'en-US' }});
                    Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
                    Date.prototype.getTimezoneOffset = () => {SPOOFED_OFFSET};
                    const originalGetContext = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type, attributes) {{
                        if (type === 'webgl' || type === 'webgl2') {{
                            attributes = {{ ...attributes, failIfMajorPerformanceCaveat: false }};
                        }}
                        return originalGetContext.call(this, type, attributes);
                    }};
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => 8 }});
                    window.RTCPeerConnection = null;
                    window.mozRTCPeerConnection = null;
                    window.webkitRTCPeerConnection = null;
                    window.RTCSessionDescription = null;
                    window.RTCIceCandidate = null;
                    navigator.getUserMedia = null;
                    navigator.mediaDevices = {{ getUserMedia: null }};
                    navigator.mozGetUserMedia = null;
                    navigator.webkitGetUserMedia = null;
                """)
                contexts[session_id] = context
            else:
                context = contexts[session_id]

            if not context.is_connected():
                logger.error(f"Context for session {session_id} is closed. Creating new.")
                del contexts[session_id]
                user_data_dir = os.path.join(os.getcwd(), f'temp_profile_{session_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                proxy_username = 'KMwYgm4pR4upF6yX-s-test12345678-co-USA-st-NY-ci-NewYorkCity'
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=True,
                    proxy={"server": f"socks5h://{proxy_username}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"},
                    user_agent=user_agent,
                    viewport=viewport,
                    locale='en-US',
                    timezone_id=SPOOFED_TIMEZONE,
                    extra_http_headers={'Accept-Language': SPOOFED_LANGUAGE}
                )
                await context.add_init_script(script=f"""
                    Object.defineProperty(navigator, 'language', {{ get: () => 'en-US' }});
                    Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
                    Date.prototype.getTimezoneOffset = () => {SPOOFED_OFFSET};
                    const originalGetContext = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type, attributes) {{
                        if (type === 'webgl' || type === 'webgl2') {{
                            attributes = {{ ...attributes, failIfMajorPerformanceCaveat: false }};
                        }}
                        return originalGetContext.call(this, type, attributes);
                    }};
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => 8 }});
                    window.RTCPeerConnection = null;
                    window.mozRTCPeerConnection = null;
                    window.webkitRTCPeerConnection = null;
                    window.RTCSessionDescription = null;
                    window.RTCIceCandidate = null;
                    navigator.getUserMedia = null;
                    navigator.mediaDevices = {{ getUserMedia: null }};
                    navigator.mozGetUserMedia = null;
                    navigator.webkitGetUserMedia = null;
                """)
                contexts[session_id] = context

            page = await context.new_page()

            async def handle_response(resp):
                headers = await resp.all_headers()
                set_cookie = headers.get('set-cookie', '')
                if set_cookie:
                    filtered = [c for c in set_cookie.split(';') if not any(geo in c.lower() for geo in GEO_COOKIES)]
                    logger.info(f"Filtered cookies for {resp.url}: {filtered}")

            page.on('response', handle_response)

            try:
                await context.add_cookies([{'name': k, 'value': v, 'domain': urlparse(url).hostname, 'path': '/'} for k, v in cookies.items()])
                logger.info(f"Navigating to {url} for session {session_id}")
                await page.goto(url, wait_until='networkidle', timeout=60000)
                await asyncio.sleep(random.uniform(2, 5))
                content = await page.content()

                soup = BeautifulSoup(content, 'lxml')
                for tag in soup.find_all(['a', 'form'], href=True):
                    original_url = tag['href']
                    full_url = urljoin(url, original_url)
                    tag['href'] = f'/scrape?url={quote_plus(full_url)}&session_id={session_id}'
                if soup.html:
                    soup.html['lang'] = 'en-US'
                if soup.head:
                    timezone_script = soup.new_tag('script')
                    timezone_script.string = TIMEZONE_SPOOF_JS
                    soup.head.append(timezone_script)
                    proxy_script = soup.new_tag('script')
                    proxy_script.string = PROXY_JS_OVERRIDE
                    soup.head.append(proxy_script)

                rewritten_content = str(soup)
                return rewritten_content
            except Exception as e:
                logger.error(f"Scrape error for {url}: {str(e)}")
                if 'proxy' in str(e).lower() or 'expire' in str(e).lower():
                    return HTMLResponse("Proxy error: Connection failed. Please check credentials or try again later.")
                return HTMLResponse(f"Error scraping: {str(e)}. Please try again.")
    except Exception as e:
        logger.error(f"Critical scrape error for {url}: {str(e)}")
        return HTMLResponse(f"Critical error: {str(e)}. Please try again.")

@app.get("/close_session")
async def close_session(request: Request):
    session_id = request.session.get('session_id')
    if session_id in contexts:
        try:
            await contexts[session_id].close()
            logger.info(f"Closed context for session {session_id}")
        except Exception as e:
            logger.error(f"Error closing context for session {session_id}: {str(e)}")
        del contexts[session_id]
    if session_id in cached_content:
        del cached_content[session_id]
    request.session.pop('session_id', None)
    return {"message": "Session closed"}

@app.on_event("shutdown")
async def shutdown_event():
    for session_id, context in list(contexts.items()):
        try:
            await context.close()
            logger.info(f"Shutdown: Closed context for session {session_id}")
        except Exception as e:
            logger.error(f"Shutdown: Error closing context for session {session_id}: {str(e)}")
    contexts.clear()
    cached_content.clear()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
