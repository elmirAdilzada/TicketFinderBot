import json
import websocket
import threading

def _cdp_execute_fetch(from_station: int, to_station: int, way: int, trip_date: str = None) -> dict:
    import requests
    resp = requests.get('http://localhost:9222/json/list', timeout=5)
    targets = resp.json()
    ws_url = None
    for t in targets:
        if 'ticket.ady.az' in t.get('url', ''):
            ws_url = t.get('webSocketDebuggerUrl')
            break
    if not ws_url:
        for t in targets:
            if t.get('type') == 'page':
                ws_url = t.get('webSocketDebuggerUrl')
                break
    if not ws_url:
        return None

    done = threading.Event()
    result_data = None
    
    def on_message(ws, message):
        nonlocal result_data
        data = json.loads(message)
        if 'result' in data:
            result_data = data
            done.set()
            
    def on_open(ws):
        if trip_date:
            body = json.dumps({'from_station': from_station, 'to_station': to_station, 'trip_date': trip_date, 'is_exclusive': 0, 'check': False, 'g_token': ''})
            url = 'https://ticket.ady.az/ticket-api/get_traintrip'
        else:
            body = json.dumps({'from_station': from_station, 'to_station': to_station, 'way': way, 'is_exclusive': 0, 'g_token': ''})
            url = 'https://ticket.ady.az/ticket-api/get_trip_dates'
            
        js = f'''
        (() => {{
            return new Promise((resolve) => {{
                try {{
                    grecaptcha.ready(() => {{
                        grecaptcha.execute('6LecJSYtAAAAAMSGKGKhA72oiCfAWr8EoAUzEMgj', {{action: 'submit'}}).then(function(token) {{
                            let payload = {body};
                            payload.g_token = token;
                            fetch('{url}', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json',
                                    'X-Requested-With': 'XMLHttpRequest'
                                }},
                                body: JSON.stringify(payload)
                            }}).then(r => {{
                                r.json().then(j => resolve(JSON.stringify({{status: r.status, data: j}})));
                            }});
                        }});
                    }});
                }} catch (e) {{
                    resolve(JSON.stringify({{status: 500, error: e.toString()}}));
                }}
            }});
        }})()
        '''
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': js, 'awaitPromise': True}}))
        
    ws_client = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open)
    threading.Thread(target=ws_client.run_forever, daemon=True).start()
    done.wait(10)
    if result_data and 'result' in result_data and 'result' in result_data['result']:
        try:
            return json.loads(result_data['result']['result']['value'])
        except Exception:
            return None
    return None

if __name__ == '__main__':
    print('Testing direct CDP fetch with ReCaptcha token...')
    res = _cdp_execute_fetch(232, 170, 1)
    print(res)
