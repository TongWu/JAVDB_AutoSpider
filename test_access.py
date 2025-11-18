import requests

test_sites = [
    ("Google", "https://www.google.com"),
    ("JavDB 主站", "https://javdb.com"),
    ("JavDB VFT=2", "https://javdb.com/?vft=2"),
]

proxy = "http://tedwu:No.25_Aminor@150.230.4.50:12300"
proxies = {'http': proxy, 'https': proxy}

print("测试代理可访问性:")
print("=" * 70)

for name, url in test_sites:
    try:
        response = requests.get(url, proxies=proxies, timeout=10)
        print(f"✓ {name:20} -> {response.status_code} ({len(response.text)} 字符)")
    except Exception as e:
        print(f"✗ {name:20} -> 错误: {str(e)[:50]}")

print("=" * 70)
