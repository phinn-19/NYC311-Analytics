import requests

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

params = {
    "$limit": 5
}

headers = {
    "User-Agent": "nyc311-analysis/1.0",
    "Accept": "application/json"
}

response = requests.get(
    URL,
    params=params,
    headers=headers,
    timeout=30
)

print("=" * 50)
print("NYC 311 API TEST")
print("=" * 50)

print("Status code:", response.status_code)
print("Request URL:", response.url)

print("\nResponse:")
print(response.text[:2000])