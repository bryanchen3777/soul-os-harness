import urllib.request, urllib.parse, json

url = 'http://localhost:8000/inject/tick'
data = urllib.parse.urlencode({'elapsed_mins': 35, 'time_period': 'morning'}).encode()
req = urllib.request.Request(url, data=data, method='POST')
req.add_header('Content-Type', 'application/x-www-form-urlencoded')
resp = urllib.request.urlopen(req)
print(resp.read().decode())