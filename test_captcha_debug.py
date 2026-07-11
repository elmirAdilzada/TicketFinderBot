# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
from playwright.sync_api import sync_playwright

user_data_dir = os.path.join(os.path.dirname(__file__), "playwright_profile")

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--window-size=1280,800"],
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    pages = ctx.pages
    page = pages[0] if pages else ctx.new_page()
    page.goto("https://ticket.ady.az", wait_until="networkidle", timeout=30000)
    print(f"[+] URL: {page.url}")

    # ___grecaptcha_cfg icini goster
    cfg = page.evaluate("""
        (() => {
            try {
                const cfg = window.___grecaptcha_cfg;
                if (!cfg) return {error: '___grecaptcha_cfg tapilmadi'};
                
                const result = {
                    clients_keys: [],
                    raw: JSON.stringify(cfg).substring(0, 2000)
                };
                
                // clients icindeki sitekey-leri tap
                if (cfg.clients) {
                    for (const [id, client] of Object.entries(cfg.clients)) {
                        for (const [k, v] of Object.entries(client)) {
                            if (typeof v === 'string' && v.startsWith('6') && v.length > 30) {
                                result.clients_keys.push({id, field: k, key: v});
                            }
                            if (typeof v === 'object' && v !== null) {
                                for (const [k2, v2] of Object.entries(v)) {
                                    if (typeof v2 === 'string' && v2.startsWith('6') && v2.length > 30) {
                                        result.clients_keys.push({id, field: k+'.'+k2, key: v2});
                                    }
                                }
                            }
                        }
                    }
                }
                return result;
            } catch(e) {
                return {error: e.toString()};
            }
        })()
    """)
    
    print(f"\n[+] ___grecaptcha_cfg clients site keys:")
    for item in cfg.get('clients_keys', []):
        print(f"    ID={item['id']} | field={item['field']} | key={item['key']}")
    
    if not cfg.get('clients_keys'):
        print("[!] Hec bir site key tapilmadi")
        print(f"\n[+] Raw cfg (ilk 500 char): {cfg.get('raw', '')[:500]}")

    # Butun site key-leri HTML-den axtar
    print("\n[+] HTML-den 6xxx... key-leri axtarilir...")
    keys_in_html = page.evaluate("""
        (() => {
            const html = document.documentElement.innerHTML;
            const matches = [...html.matchAll(/6[A-Za-z0-9_-]{38}/g)];
            return [...new Set(matches.map(m => m[0]))];
        })()
    """)
    print(f"    Tapilan keyler: {keys_in_html}")

    # Network sorgularindan gelen sitekey-i axtar (XHR loglarindan)
    print("\n[+] grecaptcha.execute ile mevcut clientlerdeki keyler sinaqdan kecirilir...")
    for key in keys_in_html:
        print(f"\n    Sinaq edilir: {key}")
        result = page.evaluate(f"""
            new Promise((resolve) => {{
                const timer = setTimeout(() => resolve({{status: 'TIMEOUT'}}), 5000);
                try {{
                    grecaptcha.ready(() => {{
                        grecaptcha.execute('{key}', {{action: 'submit'}})
                            .then(token => {{ clearTimeout(timer); resolve({{status: 'OK', len: token.length, prefix: token.substring(0,30)}}); }})
                            .catch(err => {{ clearTimeout(timer); resolve({{status: 'ERROR', reason: err.toString()}}); }});
                    }});
                }} catch(e) {{
                    clearTimeout(timer);
                    resolve({{status: 'EXCEPTION', reason: e.toString()}});
                }}
            }})
        """)
        print(f"    Netice: {result}")
        if result.get('status') == 'OK':
            print(f"\n    *** DOGRU KEY TAPILDI: {key} ***")
            break

    input("\n[Enter] bagla...")
    ctx.close()
