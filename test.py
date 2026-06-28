import requests
import json
import websocket
import threading

resp = requests.get('http://localhost:9222/json/list', timeout=5)
targets = resp.json()
ws_url = None
for t in targets:
    if 'ticket.ady.az' in t.get('url', ''):
        ws_url = t.get('webSocketDebuggerUrl')
        break

if ws_url:
    done = threading.Event()
    result_data = None
    cookies_dict = {}
    
    def on_message(ws, message):
        global result_data
        data = json.loads(message)
        if 'result' in data and 'cookies' in data['result']:
            for c in data['result']['cookies']:
                cookies_dict[c['name']] = c['value']
        elif 'result' in data:
            result_data = data
            done.set()
            
    def on_open(ws):
        ws.send(json.dumps({'id': 1, 'method': 'Network.getCookies', 'params': {'urls': ['https://ticket.ady.az']}}))
        js = '''
        (() => {
            return new Promise((resolve) => {
                try {
                    grecaptcha.ready(() => {
                        grecaptcha.execute('6LecJSYtAAAAAMSGKGKhA72oiCfAWr8EoAUzEMgj', {action: 'submit'}).then(function(token) {
                            resolve(token);
                        });
                    });
                } catch (e) {
                    resolve(e.toString());
                }
            });
        })()
        '''
        ws.send(json.dumps({'id': 2, 'method': 'Runtime.evaluate', 'params': {'expression': js, 'awaitPromise': True}}))
        
    ws_client = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open)
    threading.Thread(target=ws_client.run_forever, daemon=True).start()
    done.wait(10)
    
    token = result_data['result']['result']['value'] if result_data else ''
    
    s = requests.Session()
    s.headers.update({
        'sec-ch-ua-platform': '"Windows"',
        'Referer': 'https://ticket.ady.az/',
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not A;Brand";v="24"',
        'Content-Type': 'application/json',
        'sec-ch-ua-mobile': '?0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin': 'https://ticket.ady.az',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    })
    for k, v in cookies_dict.items():
        s.cookies.set(k, v, domain='ticket.ady.az')
        
    print('Sending token:', token[:20], '...')
    r = s.post('https://ticket.ady.az/ticket-api/get_trip_dates', json={'from_station': 232, 'to_station': 170, 'way': 1, 'is_exclusive': 0, 'g_token': token})
    print('Requests status:', r.status_code)
    print('Requests text:', r.text[:200])
