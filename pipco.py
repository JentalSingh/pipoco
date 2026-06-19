import os, time, logging, sys, io, random, re
from datetime import datetime
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("pdf_uploader")

load_dotenv()

PDF_FILE_NAME = "newexpediapdf4.pdf"
TARGET_URL = "https://pipoca.esalq.usp.br/webOS/form/solicitacao-auxilio-financeiro-svg-03"

def find_chrome_path():
    paths = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        os.path.expanduser("~") + "\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None

def load_proxies_from_file(filename="Webshare proxies.txt"):
    proxies = []
    if os.path.exists(filename):
        with open(filename, "r", encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(':')
                    if len(parts) == 4:
                        ip, port, username, password = parts
                        proxy = f"http://{username}:{password}@{ip}:{port}"
                        proxies.append(proxy)
                    else:
                        proxies.append(line)
        logger.info(f"✅ Loaded {len(proxies)} proxies from {filename}")
    else:
        logger.warning(f"⚠️ Proxy file not found: {filename}")
    return proxies

PROXY_LIST = load_proxies_from_file()

def get_pdf_path():
    return os.path.abspath(PDF_FILE_NAME) if os.path.exists(PDF_FILE_NAME) else None

def get_random_proxy():
    if PROXY_LIST:
        proxy = random.choice(PROXY_LIST)
        logger.info(f"🔄 Using proxy: {proxy[:40]}...")
        return proxy
    logger.warning("⚠️ No proxies available, running without proxy")
    return None

def upload_pdf_and_get_url(pdf_path):
    pdf_url = None
    proxy_str = get_random_proxy()
    chrome_path = find_chrome_path()
    
    proxy_config = None
    if proxy_str:
        try:
            if proxy_str.startswith('http'):
                parts = proxy_str.replace('http://', '').split('@')
                if len(parts) == 2:
                    auth, server = parts
                    user_pass = auth.split(':')
                    server_parts = server.split(':')
                    proxy_config = {
                        "server": f"http://{server}",
                        "username": user_pass[0],
                        "password": user_pass[1] if len(user_pass) > 1 else ''
                    }
                else:
                    proxy_config = {"server": proxy_str}
            elif proxy_str.startswith('socks5'):
                proxy_config = {"server": proxy_str}
        except Exception as e:
            logger.warning(f"Proxy parse error: {e}")
    
    with sync_playwright() as p:
        context_kwargs = {
            "user_data_dir": os.path.join(os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "USP_Profile"),
            "executable_path": chrome_path,
            "headless": False,
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars"
            ],
            "ignore_default_args": ["--enable-automation", "--disable-blink-features"],
            "viewport": {'width': 1366, 'height': 768}
        }
        
        if proxy_config:
            context_kwargs["proxy"] = proxy_config
            logger.info(f"🌐 Proxy configured: {proxy_config.get('server', 'unknown')}")
        
        context = p.chromium.launch_persistent_context(**context_kwargs)
        page = context.new_page()
        
        # ============================================================
        # NETWORK INTERCEPT - CAPTURE PDF URL
        # ============================================================
        def capture_pdf_url(response):
            nonlocal pdf_url
            try:
                if 'file/ajax' in response.url and response.status == 200:
                    try:
                        text = response.text()
                        logger.info(f"📄 Raw response: {text[:500]}")
                        
                        # Try to find actual file URL
                        # Pattern: sites/default/files/webform/.../filename.pdf
                        match = re.search(r'sites/default/files/webform/[^"\']+\.pdf', text)
                        if match:
                            pdf_url = f"https://pipoca.esalq.usp.br/{match.group(0)}"
                            logger.info(f"📄 PDF URL from response: {pdf_url}")
                    except Exception as e:
                        pass
            except Exception as e:
                pass
        
        page.on("response", capture_pdf_url)
        
        logger.info("Navigating to form...")
        try:
            page.goto(TARGET_URL, timeout=30000)
            page.wait_for_selector('input[type="file"]', timeout=15000)
            logger.info("✅ Page loaded!")
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            context.close()
            return None
        
        time.sleep(3)
        
        # ============================================================
        # UPLOAD PDF
        # ============================================================
        logger.info(f"Uploading PDF: {os.path.basename(pdf_path)}...")
        
        try:
            file_input = page.locator('input[type="file"]').first
            if file_input:
                file_input.set_input_files(pdf_path)
                logger.info("✅ File selected!")
            
            time.sleep(2)
            
            upload_btn = page.locator('#edit-submitted-folder-af-upload-button--3, input[value="Upload"]').first
            if upload_btn:
                upload_btn.click()
                logger.info("✅ Upload button clicked!")
            
            time.sleep(5)
            logger.info("✅ PDF uploaded successfully!")
            
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            context.close()
            return None
        
        # ============================================================
        # GET URL FROM PAGE
        # ============================================================
        if not pdf_url:
            logger.info("🔍 Searching for URL in page...")
            
            # Check page source
            pdf_url = page.evaluate("""
                () => {
                    let html = document.documentElement.outerHTML;
                    let match = html.match(/sites\\/default\\/files\\/webform\\/[^"']+\\.pdf/);
                    if (match) {
                        return match[0];
                    }
                    match = html.match(/\\/webOS\\/sites\\/default\\/files\\/[^"']+\\.pdf/);
                    if (match) {
                        return match[0];
                    }
                    return null;
                }
            """)
            
            if pdf_url:
                if not pdf_url.startswith('http'):
                    pdf_url = f"https://pipoca.esalq.usp.br/{pdf_url}"
                logger.info(f"📄 URL from page source: {pdf_url}")
        
        # ============================================================
        # MANUAL URL CONSTRUCTION
        # ============================================================
        if not pdf_url:
            # Try to construct URL
            base_url = "https://pipoca.esalq.usp.br/webOS/sites/default/files/webform/svg/"
            pdf_url = base_url + PDF_FILE_NAME
            logger.info(f"📄 Constructed URL: {pdf_url}")
        
        # ============================================================
        # FINAL OUTPUT
        # ============================================================
        print("\n" + "="*70)
        print("PDF UPLOAD RESULT - USP PIRACICABA")
        print("="*70)
        print(f"PDF File: {os.path.basename(pdf_path)}")
        print(f"Chrome: {chrome_path}")
        print(f"Proxy Used: {proxy_str[:40] + '...' if proxy_str else 'None'}")
        print(f"Dynamic PDF URL: {pdf_url if pdf_url else '❌ NOT FOUND'}")
        print("="*70 + "\n")
        
        if pdf_url:
            with open("pdf_url_usp.txt", "w") as f:
                f.write(pdf_url)
            logger.info(f"✅ URL saved to pdf_url_usp.txt")
        else:
            with open("page_source.html", "w", encoding='utf-8') as f:
                f.write(page.content())
            logger.info("📄 Page source saved to page_source.html")
        
        logger.info("⏳ Waiting 15 seconds...")
        time.sleep(15)
        
        context.close()
        return pdf_url

def run():
    print("="*70)
    print("PDF UPLOAD - USP PIRACICABA (FINAL)")
    print("="*70 + "\n")
    
    chrome_path = find_chrome_path()
    if chrome_path:
        logger.info(f"✅ Chrome found: {chrome_path}")
    else:
        logger.error("❌ Chrome not found!")
        return
    
    pdf_path = get_pdf_path()
    if not pdf_path:
        logger.error(f"❌ PDF not found: {PDF_FILE_NAME}")
        return
    
    logger.info(f"PDF: {pdf_path}")
    logger.info(f"Available proxies: {len(PROXY_LIST)}")
    
    url = upload_pdf_and_get_url(pdf_path)
    
    if url:
        print(f"\n✅ Success! PDF URL: {url}")
    else:
        print("\n❌ Failed to capture PDF URL")

if __name__ == "__main__":
    run()