import requests, json, websocket, threading, re

resp = requests.get('http://localhost:9222/json/list', timeout=5)
targets = resp.json()
ws_url = next((t.get('webSocketDebuggerUrl') for t in targets if 'ticket.ady.az' in t.get('url', '')), None)

if not ws_url:
    print('Not found')
    exit()

done = threading.Event()
html = ''
def on_message(ws, message):
    global html
    data = json.loads(message)
    if 'result' in data and 'result' in data['result']:
        html = data['result']['result'].get('value', '')
        done.set()

def on_open(ws):
    ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': 'document.documentElement.outerHTML'}}))

ws_client = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open)
threading.Thread(target=ws_client.run_forever, daemon=True).start()
done.wait(5)

match = re.search(r'recaptcha/api\.js\?render=([^&"]+)', html)
if match:
    print('SITE KEY:', match.group(1))
else:
    print('NOT FOUND IN HTML')

